import asyncio
from threading import Event
from types import SimpleNamespace

import pytest
from nicegui import Client, core, ui

from pixivvault_nicegui.external_vault import VaultError
from pixivvault_nicegui.local_tagger import LocalTagger, TaggerManager
from pixivvault_nicegui.pixiv_read_client import HTTPStatusRejected, PublicImageReadClient
from pixivvault_nicegui.preferences import PreferencesStore, RequestPreferences
from pixivvault_nicegui.request_policy import RequestRetryPolicy
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.library_view import LibraryView
from pixivvault_nicegui.ui.preferences_view import PreferencesView
from pixivvault_nicegui.ui.request_status_view import RequestStatusView
from pixivvault_nicegui.ui.scheduler_view import SchedulerView
from test_ac_library_search_20261119 import FakeVault, until
from test_ak_tagger_20261119 import make_model
from test_managed_download_services import FakeSource
from test_personal_ranking import raw
from test_w_account_separation_20261119 import A, B, X, Y, resources, switch
from test_y_ranking_personal_20261118 import build, open_view, settle
from z_learning_support_20261118 import open_discovery_view


@pytest.mark.asyncio
async def test_learning_missing_image_is_local_and_is_not_retried(tmp_path, monkeypatch):
    learning, _, _, cleanup = await open_discovery_view(tmp_path, monkeypatch)
    calls = []

    def absent(*_):
        calls.append(1)
        raise HTTPStatusRejected(404)

    reader = PublicImageReadClient(transport=absent)
    policy = RequestRetryPolicy(lambda: RequestPreferences(.6, 3, 5.0))
    reader.set_retry_policy(policy)
    try:
        with learning.client:
            global_status = RequestStatusView(policy)
            with pytest.raises(HTTPStatusRejected) as failure:
                reader.get_image_bytes('https://i.pximg.net/generated/failure_p0.jpg')
            assert failure.value.status_code == 404
            assert calls == [1]
            learning._on_failed(SimpleNamespace(args=learning._image_url))
            assert '画像を読み込めませんでした' in learning.status.text
            global_status.refresh()
            assert not global_status.label.visible
            learning.set_active(False)
            global_status.refresh()
            assert not global_status.label.visible
            global_status.close()
    finally:
        reader.close()
        await cleanup()


def test_scheduler_label_follows_verified_form_and_binding(tmp_path):
    with resources(tmp_path) as (_, store, ledger, reader, services):
        store.sync(reader)
        store.set_favorite(A, X, True)
        client = Client(ui.page('/_r3_schedule_account'))
        try:
            with client:
                view = SchedulerView(services)
                view._load(ledger)
                assert view.account_label.text == f'対象アカウント ID {A}（未紐づけ）'
                services.scheduler_favourite_authors(ledger=ledger)
                view._load(ledger)
                assert view.account_label.text == f'紐づけアカウント ID {A}'
                assert view.authors.value == X
                view.interval.set_value('6')
                switch(store, reader)
                store.set_favorite(B, Y, True)
                view._load(ledger)
                assert view.account_label.text == '紐づけアカウントを確認できません'
                assert view._settings_account == A and ledger._bound_account() == A
                view.discard_changes()
                assert view.account_label.text == '紐づけアカウントを確認できません'
                assert view._settings_account == B and ledger._bound_account() == A
        finally:
            client.delete()


def test_download_star_projection_uses_the_displayed_account(tmp_path):
    with resources(tmp_path) as (_, store, _, reader, services):
        store.sync(reader)
        store.set_favorite(A, X, True)

        def projected_stars():
            return {row['user_id'] for row in ApplicationServices.unified_download_authors(services)
                    if row['is_favorite']}

        assert projected_stars() == {X}
        switch(store, reader)
        store.set_favorite(B, Y, True)
        assert projected_stars() == {Y}
        reader._verified_identity = A
        store.sync(reader)
        assert projected_stars() == {X}


@pytest.mark.asyncio
@pytest.mark.parametrize('remote_selected', (False, True), ids=('local-viewer', 'remote-viewer'))
async def test_borrow_poll_retires_remote_pin_and_preserves_local_viewer(monkeypatch, remote_selected):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())

    class Vault(FakeVault):
        ready = True

        def status(self):
            return {**super().status(), 'open': self.ready}

    from pixivvault_nicegui.library_models import LibrarySnapshot, LibraryWork
    from pixivvault_nicegui.media import DemoImageCatalog
    from pixivvault_nicegui.models import Artwork

    vault = Vault()
    services = ApplicationServices(live_client=FakeSource())
    services.external_vault = vault
    work = Artwork('203', 'gallery local', 'author', author_id='23')
    services.library = LibrarySnapshot((LibraryWork(work, '23'),))
    services.images.demo.close()
    services.images.demo = DemoImageCatalog((work,))
    client = Client(ui.page('/_r3_viewer_borrow_poll'))
    view = None
    try:
        with client:
            view = LibraryView(services)
            view.set_search_scope('pixiv')
            view.search('gallery')
            await until(lambda: view.pixiv_query == 'gallery')
            view.open_author('23')
            view.open_page('204' if remote_selected else '203', 0)
            assert view._viewer_state.viewer_open
            view._image_loaded(SimpleNamespace(args=view._image_url))
            assert view.viewer.is_open
            state = view._viewer_state
            generation = view.viewer.root.props['data-zoom-generation']
            remote_pin = view._image_provider.remote
            if not remote_selected:
                view._refresh_bookmark_projection()
                assert view._bookmark_projection_pending

                def no_reacquire(_):
                    raise AssertionError('retired remote pin must not be reacquired')

                monkeypatch.setattr(services, 'acquire_recommendation_images', no_reacquire)
            vault.ready = False
            view.refresh_search_scope()
            assert remote_pin.closed and not view.scope_pixiv.enabled
            with pytest.raises(RuntimeError, match='snapshot closed'):
                remote_pin.url('204', 0, 'view')
            if not remote_selected:
                assert view.viewer.is_open and view._viewer_state is state
                assert view.viewer.root.props['data-zoom-generation'] == generation
                vault.ready = True
                view.refresh_search_scope()
                assert view.viewer.is_open
                view.close_viewer()
            assert view.search_scope == 'library' and not view.viewer.is_open
            assert {work.work_id for work in view.state.snapshot.works} == {'203'}
            assert len(vault.calls) == 1
    finally:
        if view is not None:
            view.close()
        client.delete()
        services.external_vault = None
        services.close()


@pytest.mark.asyncio
async def test_tagger_runtime_status_does_not_claim_enabled_without_inference(tmp_path, monkeypatch):
    model = tmp_path / 'model'
    model.mkdir()
    make_model(model)
    store = PreferencesStore(tmp_path / 'preferences.sqlite')
    store.set_setting('local_tagger_path', str(model))
    manager = TaggerManager(store)
    monkeypatch.setattr(LocalTagger, '_runtime_available', lambda _: False)
    client = Client(ui.page('/_r3_model_status'))
    try:
        with client:
            view = PreferencesView(store, tagger=manager)
            view.open()
            await view._verify_tagger(view._epoch)
            assert view.tagger_effective.text == '状態 無効'
            assert view.tagger_status.text == 'onnxruntime なし'
            assert view.tagger_enabled.value and store.get_setting('local_tagger_enabled') == '1'
            assert manager.current_fingerprint() is None
            assert manager.try_current_fingerprint() is None
            view.close()
    finally:
        client.delete()
        manager.close()
        store.close()


@pytest.mark.asyncio
async def test_personal_ai_exclusion_and_more_use_server_offset(monkeypatch):
    services, vault, client = await build(monkeypatch, 'r3_ai_and_next')

    def search(word, bookmark_min=0, **kwargs):
        vault.calls.append(kwargs)
        if kwargs['offset']:
            return {'illusts': [raw(125, illust_ai_type=2), raw(126, illust_ai_type=1)]}
        return {'illusts': [raw(123, illust_ai_type=2), raw(124, illust_ai_type=1)], 'next_url': 'next'}

    monkeypatch.setattr(vault, 'search', search)
    try:
        with client:
            view = await open_view(services, vault)
            view.tags.set_value('tag')
            await view.load()
            assert set(view.gallery.cards) == {'123', '124'}
            view.ai.set_value(True)
            await settle(view)
            assert set(view.gallery.cards) == {'124'}
            assert view.page.offset == 2 and view.gallery.more_cell.visible
            await view.load(append=True)
            assert set(view.gallery.cards) == {'124', '126'}
            assert vault.calls[-1]['offset'] == 2 and vault.calls[-1]['exclude_ai'] is True
            assert view.page.exhausted and not view.gallery.more_cell.visible
            view.close()
    finally:
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_personal_search_discards_old_response_before_latest_conditions(monkeypatch):
    services, vault, client = await build(monkeypatch, 'r3_latest_search')
    entered, release = Event(), Event()

    def search(word, bookmark_min=0, **kwargs):
        vault.calls.append((word, bookmark_min, kwargs.get('start_date')))
        if word == 'old':
            entered.set()
            assert release.wait(5)
        return {'illusts': [raw(123 if word == 'old' else 124)]}

    monkeypatch.setattr(vault, 'search', search)
    first = None
    try:
        with client:
            view = await open_view(services, vault)
            view.tags.set_value('old')
            first = asyncio.create_task(view.load())
            await until(entered.is_set)
            view.tags.set_value('new')
            view.start_date.set_value('2026-01-01')
            view.bookmarks.set_value(1000)
            view.bookmarks.set_value(5000)
            assert vault.calls == [('old', 0, None)]
            release.set()
            await first
            assert view.page is None and '123' not in view.gallery.cards
            await settle(view)
            assert vault.calls == [('old', 0, None), ('new', 5000, '2026-01-01')]
            assert set(view.gallery.cards) == {'124'}
            view.exclude.set_value('tag')
            await settle(view)
            assert len(vault.calls) == 2 and not view.gallery.cards
            view.close()
    finally:
        release.set()
        if first is not None:
            await first
        client.delete()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(('code', 'reason'), [
    ('transport-failed', '通信障害'),
    ('pixiv-network-stopped', '通信の停止状態'),
])
@pytest.mark.parametrize('replace_tags', (False, True), ids=('filter-change', 'replace-tags'))
async def test_real_personal_source_failure_keeps_last_cards(monkeypatch, code, reason, replace_tags):
    services, vault, client = await build(monkeypatch, 'r3_failure_' + code)

    def fail(*_, **__):
        raise VaultError(code)

    try:
        with client:
            view = await open_view(services, vault)
            view.tags.set_value('tag')
            await view.load()
            previous, pin = view.page, view.pin
            assert '123' in view.gallery.cards
            monkeypatch.setattr(vault, 'search', fail)
            if replace_tags:
                view.tags.set_value('')
                assert not view.gallery.cards
                view.tags.set_value('new')
                await view.load()
            else:
                view.bookmarks.set_value(1000)
                await settle(view)
            assert view.page is previous and view.pin is pin
            assert '123' in view.gallery.cards
            assert reason in view.notice.text and '直前の検索結果' in view.notice.text
            assert not view.gallery.more_button.enabled
            failure_notice = view.notice.text
            await view._refresh_context()
            assert view.notice.text == failure_notice and '123' in view.gallery.cards
            view.close()
    finally:
        client.delete()
        services.close()



def test_personal_ranking_hides_the_shared_log():
    from pixivvault_nicegui.queue_log import QueueLog
    from pixivvault_nicegui.ui.shared_log_view import SharedLogView

    client = Client(ui.page('/_r3_log_scope'))
    try:
        with client:
            view = SharedLogView(QueueLog())
            view.show_tab('scheduler')
            assert view.root.visible
            view.show_tab('personal')
            assert not view.root.visible
            view.show_tab('metadata')
            assert view.root.visible
            view.close()
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_recommend_missing_saved_section_does_not_claim_connection_is_missing(tmp_path, monkeypatch):
    from pixivvault_nicegui.recommendation_state import RecommendationSnapshot
    from test_x_ctx_recommend_20261118 import make, open_view as open_recommend

    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    services = make(tmp_path)
    services.recommendations.source.load = lambda: RecommendationSnapshot(unavailable=('certain',))
    services.recommendations.acquisition.source = object()
    client = Client(ui.page('/_r3_recommend_cache'))
    try:
        with client:
            view = await open_recommend(services)
            texts = {getattr(element, 'text', '') or '' for element in view.body.descendants()}
            assert view.update_button.enabled
            assert '未接続' not in view.notice.text
            assert '取得元を接続' not in ''.join(texts)
            assert any('保存済み結果がありません' in text for text in texts)
            services.recommendations.acquisition.source = None
            await view.refresh()
            assert not view.update_button.enabled
            assert any('取得元が未接続' in getattr(element, 'text', '')
                       for element in view.body.descendants())
            view.close()
    finally:
        client.delete()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('restore_focus', (False, True))
async def test_inflight_library_search_does_not_redraw_open_local_viewer(monkeypatch, restore_focus):
    from pixivvault_nicegui.library_models import LibrarySnapshot, LibraryWork
    from pixivvault_nicegui.media import DemoImageCatalog
    from pixivvault_nicegui.models import Artwork

    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    vault = FakeVault()
    services = ApplicationServices(live_client=FakeSource())
    services.external_vault = vault
    work = Artwork('203', 'alpha local', 'author', author_id='23')
    services.library = LibrarySnapshot((LibraryWork(work, '23'),))
    services.images.demo.close()
    services.images.demo = DemoImageCatalog((work,))
    client = Client(ui.page('/_r3_viewer_search_projection'))
    view = None
    try:
        with client:
            view = LibraryView(services)
            view.set_search_scope('pixiv')
            vault.block = True
            view.search('alpha')
            await until(vault.entered.is_set)
            view.open_author('23')
            view.open_page('203', 0)
            assert view._viewer_state.viewer_open
            view._image_loaded(SimpleNamespace(args=view._image_url))
            assert view.viewer.is_open
            vault.release.set()
            await until(lambda: view.pixiv_query == 'alpha')
            assert view.viewer.is_open
            assert view._viewer_state.selected_artwork.work_id == '203'
            view.close_viewer(restore_focus=restore_focus)
            assert {work.work_id for work in view.state.snapshot.works} == {'203', '204'}
    finally:
        vault.release.set()
        if view is not None:
            view.close()
        client.delete()
        services.external_vault = None
        services.close()



def test_model_load_failure_prevents_usable_tagger(tmp_path):
    make_model(tmp_path)
    calls = []

    def unavailable(_):
        calls.append('session')
        raise RuntimeError('synthetic runtime load failure')

    tagger = LocalTagger(tmp_path, session_factory=unavailable, tensor_factory=lambda image: image)
    try:
        assert tagger.verify() == '読込失敗'
        assert tagger.verify() == '読込失敗'
        assert calls == ['session'] and tagger.session is None
    finally:
        tagger.close()


def test_tagger_lightweight_check_cannot_publish_a_stale_model(tmp_path, monkeypatch):
    import hashlib
    import json
    from threading import Thread, current_thread
    from pixivvault_nicegui import local_tagger

    model = tmp_path / 'model'
    model.mkdir()
    make_model(model)
    store = PreferencesStore(tmp_path / 'preferences.sqlite')
    store.set_setting('local_tagger_path', str(model))
    manager = TaggerManager(store)
    manager.tagger = LocalTagger(model, session_factory=lambda _: object(), tensor_factory=lambda image: image)
    manager._provider = 'auto'
    entered, release = Event(), Event()
    checked = []
    thread = Thread(target=lambda: checked.append(manager.try_current_fingerprint()))
    identity = local_tagger._identity

    def interrupt(path):
        value = identity(path)
        if current_thread() is thread and path.name == 'selected_tags.csv':
            entered.set()
            assert release.wait(5)
        return value

    monkeypatch.setattr(local_tagger, '_identity', interrupt)
    try:
        old = manager.current_fingerprint()
        assert old
        manager.invalidate()
        thread.start()
        assert entered.wait(5)
        data = b'second synthetic model'
        (model / 'model.onnx').write_bytes(data)
        manifest_path = model / 'MODEL_SOURCE.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        manifest['files'][0]['sha256'] = hashlib.sha256(data).hexdigest()
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        new = manager.current_fingerprint()
        assert new and new != old
        release.set()
        thread.join(5)
        assert checked == [None]
        assert manager.try_current_fingerprint() == new and manager.usable
    finally:
        release.set()
        if thread.ident is not None:
            thread.join(5)
        manager.close()
        store.close()
