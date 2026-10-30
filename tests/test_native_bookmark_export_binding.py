"""S10 native profile -> explicit verified bookmark read -> real owned layout.

Only generated cookies, generated HTTP payloads and newly owned temp roots.
No server, WebView, OS launcher, real network, old DB or real image is used.
"""
from __future__ import annotations

import asyncio
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from threading import Event, Thread
import traceback
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from nicegui import Client, ui
from PIL import Image

from pixivvault_nicegui import bootstrap, media_routes, pixiv_read_client
from pixivvault_nicegui.fs_paths import native_io_path
from pixivvault_nicegui.native_profile import (
    NativeProfileStore, native_kwargs, provision_storage,
)
from pixivvault_nicegui.native_startup import native_entry_arguments
from pixivvault_nicegui.save_exports import SaveExports
from pixivvault_nicegui.services import get_services
from pixivvault_nicegui.ui.preferences_view import PreferencesView
from test_pixiv_read_client import cookie_file, home_page, packet


class GeneratedHTTP:
    original_url = 'https://i.pximg.net/img-original/img/2026/10/29/00/00/00/701_p0.png'

    def __init__(self):
        self.calls = []
        self.fail = None
        self.after_last = None
        image = Image.new('RGB', (48, 32), (23, 81, 140))
        stream = BytesIO()
        image.save(stream, 'PNG')
        self.png = stream.getvalue()

    def __call__(self, request, timeout, limit, deadline):
        assert request.get_method() == 'GET'
        parsed = urlsplit(request.full_url)
        query = parse_qs(parsed.query)
        self.calls.append((parsed.path, query))
        if request.full_url == 'https://www.pixiv.net/':
            return home_page(None if self.fail == 'identity' else '55')
        if parsed.path == '/ajax/user/extra':
            return packet({'userId': '55'})
        if parsed.path in (
                '/ajax/user/55/illusts/bookmarks',
                '/ajax/user/55/novels/bookmarks'):
            rest = query['rest'][0]
            novel = '/novels/' in parsed.path
            if novel and rest == 'hide':
                if self.after_last is not None:
                    callback, self.after_last = self.after_last, None
                    callback()
                if self.fail == 'route':
                    return packet({}, error=True)
            works = []
            if not novel and rest == 'show' and query['offset'][0] == '0':
                works = [dict(id='701', title='Generated 701',
                    userId='91', userName='Generated author',
                    illustType=0, pageCount=1, tags=[], xRestrict=0,
                    createDate='2026-10-29T00:00:00Z',
                    url='https://i.pximg.net/c/360x360_70/img-master/701_p0_master1200.jpg')]
            return packet({'works': works})
        if parsed.path == '/ajax/illust/701/pages':
            return packet([{'urls': {'original': self.original_url}}])
        if request.full_url == self.original_url:
            return self.png
        raise AssertionError('unexpected generated-only endpoint')


@pytest.fixture
def native_context(tmp_path, monkeypatch):
    import nicegui

    credential_dir = tmp_path / 'synthetic-cookie'
    credential_dir.mkdir()
    cookie = cookie_file(credential_dir)
    cookie_hash = sha256(cookie.read_bytes()).hexdigest()
    parent = tmp_path / 'selected-save-parent'
    parent.mkdir()
    profile = tmp_path / 'profile'
    store = NativeProfileStore(profile)
    try:
        selected = store.update(cookie_file=cookie, save_parent=parent,
                                expected_revision=0)
    finally:
        store.close()
    monkeypatch.setenv('PIXIVVAULT_PROFILE_ROOT', str(profile))
    config = bootstrap.parse_args(['--native', *native_entry_arguments([]), '--check'])
    assert config.bookmark_account_id is None
    assert not config.enable_pixiv_actions
    planned = native_kwargs(selected, profile)
    assert 'bookmark_account_id' not in planned
    assert not list(parent.iterdir())
    assert sha256(cookie.read_bytes()).hexdigest() == cookie_hash
    created = []

    def build(*, fixed_account=None):
        http = GeneratedHTTP()
        readers = []
        actual_type = pixiv_read_client.LivePixivReadClient

        class GeneratedReader(actual_type):
            def __init__(self, source):
                super().__init__(source, transport=http)
                readers.append(self)

        callbacks = []
        shutdown = []
        owned_app = SimpleNamespace(
            state=SimpleNamespace(),
            on_startup=callbacks.append,
            on_shutdown=shutdown.append,
            get=lambda *_args, **_kwargs: (lambda callback: callback),
        )
        snapshot = NativeProfileStore.peek(profile)
        values = native_kwargs(snapshot, profile)
        provision_storage(snapshot, profile)
        # Keep every ordinary profile resource option. Isolate only HTTP route
        # registration and native OS surfaces; all service owners are real.
        with monkeypatch.context() as patch:
            patch.setattr(bootstrap, '_registered', False)
            patch.setattr(nicegui, 'app', owned_app)
            patch.setattr(ui, 'page', lambda *_args, **_kwargs: (lambda builder: builder))
            patch.setattr(media_routes, 'register_media_routes', lambda *_args: None)
            patch.setattr(pixiv_read_client, 'LivePixivReadClient', GeneratedReader)
            bootstrap.register_pages(**values, bookmark_account_id=fixed_account,
                                     pixiv_actions_mode='preferences')
            service = get_services(owned_app)
        created.append(service)
        runner_errors = []
        original_run = service.managed_downloads.run

        def observed_run(*args, **kwargs):
            try:
                return original_run(*args, **kwargs)
            except BaseException as error:
                # This fixture owns every input; retain the failure locally so
                # a generic JobService error cannot conceal a missing contract.
                causes = []
                cause = error.__context__
                seen = {id(error)}
                while cause is not None and id(cause) not in seen:
                    seen.add(id(cause))
                    causes.append({'type': type(cause).__name__,
                                   'errno': getattr(cause, 'errno', None),
                                   'winerror': getattr(cause, 'winerror', None),
                                   'traceback': ''.join(traceback.format_exception(
                                       type(cause), cause, cause.__traceback__))})
                    cause = cause.__context__
                runner_errors.append({'type': type(error).__name__,
                                      'traceback': traceback.format_exc(),
                                      'causes': causes})
                (tmp_path / 'generated-runner-diagnostic.json').write_text(
                    json.dumps({'generated_http_calls': http.calls,
                                'runner_errors': runner_errors}, indent=2),
                    encoding='utf-8')
                raise

        monkeypatch.setattr(service.managed_downloads, 'run', observed_run)
        assert service.live_client is readers[0]
        assert service.save_exports.root == values['save_export_root']
        assert service.save_exports.start in callbacks
        assert service.pixiv_actions is None
        assert http.calls == []
        assert sha256(cookie.read_bytes()).hexdigest() == cookie_hash
        # Keep real request policy; choose its existing shortest user setting.
        service.preferences.update(
            {'download_interval': '0.3', 'api_retry_count': '1'},
            expected_revision=service.preferences.snapshot().revision)
        return SimpleNamespace(service=service, reader=readers[0], http=http,
                               exports=service.save_exports, roots=values,
                               runner_errors=runner_errors)

    yield SimpleNamespace(build=build, cookie=cookie, cookie_hash=cookie_hash,
                          profile=profile, config=config, parent=parent)
    for service in reversed(created):
        # A fence test may set this scalar without running an actual shutdown.
        if not getattr(service, 'closed', False):
            service.closing = False
        service.close()
    assert sha256(cookie.read_bytes()).hexdigest() == cookie_hash


def managed_proofs(service):
    writer = service.managed_downloads
    root_io = native_io_path(writer.root)
    return {path.relative_to(root_io).as_posix(): sha256(path.read_bytes()).hexdigest()
            for folder in (writer.outputs, writer.generation_outputs)
            for path in native_io_path(folder).rglob('*') if path.is_file()}


def wait_job(owner, job_id, diagnostic=None):
    with owner._condition:
        assert owner._condition.wait_for(
            lambda: any(item.job_id == job_id and item.state in ('done', 'error', 'stopped')
                        for item in owner.snapshot()), 20)
    result = next(item for item in owner.snapshot() if item.job_id == job_id)
    assert result.state == 'done', (result.error_code, diagnostic)
    return result


def wait_projection(exports, mode):
    def found():
        for job in exports.jobs.snapshot():
            if job.kind != 'bookmark-projection' or job.state != 'done':
                continue
            manifest = json.loads(native_io_path(exports.root / job.result).read_bytes())
            if manifest['source']['mode'] == mode:
                return job, manifest
        return None

    with exports.jobs._condition:
        assert exports.jobs._condition.wait_for(lambda: found() is not None, 20)
    return found()


@pytest.mark.asyncio
async def test_native_profile_settings_are_editable_offline_and_explain_account_gate(native_context):
    context = native_context.build()
    service = context.service
    client = Client(ui.page('/_s10_profile_preferences'))
    try:
        with client:
            view = PreferencesView(service.preferences,
                enabled_keys=service.preference_consumers(),
                on_change=service.apply_preferences,
                pixiv_actions_mode='preferences')
            view.open()
            assert view.controls['bookmark_download_mode'].enabled
            assert view.controls['use_work_folder'].enabled
            hint = [node for node in view.dialog.descendants()
                    if node._props.get('data-testid') == 'preference-bookmark-account-gate']
            assert len(hint) == 1 and '再読込' in hint[0].text and '通信' in hint[0].text
            view.controls['bookmark_download_mode'].set_value('link')
            view.controls['use_work_folder'].set_value('1')
            view.save()
        assert service.preferences.get_setting('bookmark_download_mode') == 'link'
        assert service.preferences.get_setting('use_work_folder') == '1'
        assert context.exports.account is None
        assert context.http.calls == []
        context.exports.scan()
        assert not context.exports.jobs.snapshot()
        assert context.http.calls == []
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_native_profile_explicit_read_binds_real_direct_then_link_outputs_and_restart(native_context):
    context = native_context.build()
    service, exports, http = context.service, context.exports, context.http
    exports.start()  # Actual registered scanner; its startup must not identify an account.
    assert http.calls == [] and exports.account is None
    await service.refresh_bookmark_cache()
    assert exports.account == '55' == context.reader._verified_identity
    routes = {(path.rsplit('/', 2)[-2], query['rest'][0])
              for path, query in http.calls if path.endswith('/bookmarks')}
    assert routes == {('illusts', 'show'), ('illusts', 'hide'),
                      ('novels', 'show'), ('novels', 'hide')}
    record, = service.bookmark_download_candidates()
    accepted = service.enqueue_managed_download(record, 's10-owned-download')
    await asyncio.to_thread(wait_job, service.static_jobs, accepted.job_id,
                            {'generated_http_calls': http.calls,
                             'runner_errors': context.runner_errors})
    direct_job, direct = await asyncio.to_thread(wait_projection, exports, 'direct')
    assert direct['source']['account'] == '55'
    first = direct['files'][0]
    direct_target = native_io_path(exports.projection.root / first['path'])
    assert Path(first['path']).parts[0] == '☆ブックマーク(55)'
    assert len(Path(first['path']).parts) == 2
    assert direct_target.read_bytes() == http.png
    assert sha256(direct_target.read_bytes()).hexdigest() == first['sha256']
    proofs = managed_proofs(service)
    calls = tuple(http.calls)
    direct_manifest = native_io_path(exports.root / direct_job.result).read_bytes()

    service.preferences.update(
        {'bookmark_download_mode': 'link', 'use_work_folder': '1'},
        expected_revision=service.preferences.snapshot().revision)
    link_job, linked = await asyncio.to_thread(wait_projection, exports, 'link')
    assert linked['source']['account'] == '55'
    assert linked['source']['use_work_folder'] is True
    path = Path(linked['files'][0]['path'])
    assert path.parts[0] == '☆ブックマーク(55)'
    assert path.parts[1] == 'Generated author(91)'
    assert '(701)' in path.parts[2] and len(path.parts) == 4
    link_target = native_io_path(exports.projection.root / path)
    assert link_target.read_bytes() == http.png == direct_target.read_bytes()
    assert native_io_path(exports.root / direct_job.result).read_bytes() == direct_manifest
    assert tuple(http.calls) == calls and managed_proofs(service) == proofs
    jobs = [(job.job_id, job.kind, job.state, job.target_id) for job in exports.jobs.snapshot()]
    service.close()

    restarted = native_context.build()
    assert restarted.http.calls == []
    assert restarted.exports.account is None  # No account guess from old filenames/manifests.
    assert restarted.service.preferences.get_setting('bookmark_download_mode') == 'link'
    assert [(job.job_id, job.kind, job.state, job.target_id)
            for job in restarted.exports.jobs.snapshot()] == jobs
    restarted.exports.scan()
    assert restarted.http.calls == [] and restarted.exports.account is None
    assert direct_target.read_bytes() == http.png == link_target.read_bytes()
    assert managed_proofs(restarted.service) == proofs


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['identity', 'route'])
async def test_failed_or_partial_explicit_read_cannot_bind_or_publish(native_context, failure):
    context = native_context.build()
    service = context.service
    old_records = service._remote_records
    old_remote = service._cached_remote_bookmarks
    context.http.fail = failure
    with pytest.raises(Exception):
        await service.refresh_bookmark_cache()
    assert context.exports.account is None
    assert service._remote_records == old_records
    assert service._cached_remote_bookmarks == old_remote
    context.exports.scan()
    assert not context.exports.jobs.snapshot()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['invalidated', 'different_reader', 'closing'])
async def test_completed_get_must_still_belong_to_current_open_verified_reader(native_context, failure):
    context = native_context.build()
    service, reader = context.service, context.reader
    old_records = service._remote_records
    old_remote = service._cached_remote_bookmarks

    def late_change():
        if failure == 'invalidated':
            reader._verified_identity = None
        elif failure == 'different_reader':
            service.live_client = SimpleNamespace(_verified_identity='55')
        else:
            service.closing = True

    context.http.after_last = late_change
    try:
        with pytest.raises(RuntimeError):
            await service.refresh_bookmark_cache()
        assert context.exports.account is None
        assert service._remote_records == old_records
        assert service._cached_remote_bookmarks == old_remote
        assert not context.exports.jobs.snapshot()
    finally:
        service.live_client = reader
        service.closing = False


@pytest.mark.asyncio
async def test_fixed_cli_account_and_closed_export_owner_cannot_be_rebound(native_context):
    context = native_context.build(fixed_account='56')
    old = context.service._cached_remote_bookmarks
    with pytest.raises(RuntimeError):
        await context.service.refresh_bookmark_cache()
    assert context.exports.account == '56'
    assert context.service._cached_remote_bookmarks == old
    assert not context.exports.jobs.snapshot()
    context.exports.bind_verified_account('56')
    with pytest.raises(ValueError):
        context.exports.bind_verified_account('55')
    context.exports.close()
    with pytest.raises(RuntimeError):
        context.exports.bind_verified_account('56')
    assert context.exports.account == '56'


def test_first_binding_does_not_relabel_a_scan_batch_that_started_unverified(tmp_path, monkeypatch):
    from test_save_exports import fixture, settled

    remote, managed, initial = fixture(tmp_path, auto='0')
    service, root = initial.s, initial.root
    initial.close()
    exports = SaveExports(service, root)
    entered, release = Event(), Event()
    actual = exports._static_completed
    errors = []

    def delayed(live, current):
        result = actual(live, current)
        entered.set()
        assert release.wait(5), 'owned scan gate was not released'
        return result

    monkeypatch.setattr(exports, '_static_completed', delayed)

    def scan():
        try:
            exports.scan()
        except BaseException as error:
            errors.append(error)

    worker = Thread(target=scan)
    try:
        worker.start()
        assert entered.wait(3)
        exports.bind_verified_account('12')
        release.set()
        worker.join(5)
        assert not worker.is_alive() and not errors
        assert not exports.jobs.snapshot(), 'an unverified batch adopted a later account'
        monkeypatch.setattr(exports, '_static_completed', actual)
        exports.scan()
        jobs = settled(exports)
        assert jobs and all(job.state == 'done' for job in jobs)
        fixed = tuple((job.job_id, job.target_id, job.state) for job in jobs)
        intent_bytes = {path.name: path.read_bytes() for path in root.glob('*.json')}
        exports.bind_verified_account('12')
        with pytest.raises(ValueError):
            exports.bind_verified_account('13')
        assert tuple((job.job_id, job.target_id, job.state)
                     for job in exports.jobs.snapshot()) == fixed
        assert {path.name: path.read_bytes() for path in root.glob('*.json')} == intent_bytes
    finally:
        release.set()
        worker.join(5)
        exports.close()
        managed.close()

