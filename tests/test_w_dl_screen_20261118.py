"""WU-7: the restored download screen (UnifiedDownloadView). No network, fake engine port."""
import asyncio
from types import SimpleNamespace

import pytest
from nicegui import Client, ui

from pixivvault_nicegui import unified_download_state as U
from pixivvault_nicegui.author_download import Difference
from pixivvault_nicegui.author_media import AuthorMediaCandidate
from pixivvault_nicegui.batch_engine import BatchRunEngine
from pixivvault_nicegui.batch_runs import BatchRunLedger
from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
from pixivvault_nicegui.following_sync import FollowingSyncStore
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.unified_download_view import NEED_TARGET, PAGE_SIZE, UnifiedDownloadView

ACCOUNT = '55'


def cand(author, wid):
    return AuthorMediaCandidate('illustration', str(wid), author, '2026-01-01T00:00:00+00:00', 1, 't' + str(wid),
                                Difference.NEW, work_type=0)


class FakePort:
    closing = False

    def __init__(self, hold=False):
        self.hold = hold
        self.enqueued = []

    def require_allowed(self):
        pass

    def author_target(self, aid):
        return None

    def list_author(self, aid, target):
        return (cand(aid, int(aid) * 10 + 1), cand(aid, int(aid) * 10 + 2))

    def enqueue(self, candidate, operation_id):
        self.enqueued.append(candidate.work_id)
        return '%032x' % len(self.enqueued), True

    def job_states(self, refs):
        return {r.job_id: ('queued' if self.hold else 'done', '') for r in refs}

    def stop_jobs(self, refs):
        pass

    def observe_deletion(self, aid, remote, scan_id):
        return None

    def settle(self, aid):
        pass

    def retire(self, refs):
        pass


def author_rows(n, start=1001):
    return [dict(user_id=str(start + i), name=f'作者{i:03d}', follow_order=i, is_favorite=(i % 4 == 0),
                 is_zipped=False, last_downloaded=None) for i in range(n)]


class FakeServices:
    """Just the surface the screen uses. Every attribute is optional for the screen."""

    def __init__(self, tmp_path, rows, *, hold=False, ids=('41', '42', '43')):
        self.managed_downloads = object()
        self.jobs = object()
        self.closing = False
        self.rows = list(rows)
        self.counts = {}
        self.following_sync = FollowingSyncStore(tmp_path / 'follow')

        class Reader:
            def require_identity(self_inner):
                return ACCOUNT

            def get_following_users_detailed(self_inner, uid, rest):
                return () if rest == 'hide' else tuple(
                    dict(user_id=u, name='n' + u, account='', profile_img='') for u in ids)
        self.following_sync.sync(Reader())
        self.batch_run_ledger = BatchRunLedger(tmp_path / 'runs')
        self.port = FakePort(hold)
        self.batch_runs = self.engine = BatchRunEngine(self.port, self.batch_run_ledger, following=self.following_sync,
                                         account_id=ACCOUNT, poll_seconds=0.001, wait=lambda seconds: None,
                                         on_progress=self._fan_out)
        self._listeners = []
        self.favourites = {}
        self.fail_favourite = False

    def _fan_out(self, event):
        for listener in list(self._listeners):
            listener(event)

    def subscribe_batch_events(self, listener):
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener) if listener in self._listeners else None

    def batch_run_active(self):
        return self.batch_runs.active

    def start_batch_run(self, author_ids, *, origin='manual', target_type=None):
        self.batch_runs.account_id = self.following_sync.last_synced_account()
        return self.batch_runs.start(tuple(author_ids), origin=origin, target_type=target_type)

    def resume_batch_run(self, run_id=None):
        return self.batch_runs.start_unfinished(run_id)

    def unified_download_authors(self):
        if self.rows:
            return tuple(self.rows)
        return ApplicationServices.unified_download_authors(self)

    def saved_author_counts(self):
        return dict(self.counts)

    def set_favourite(self, uid, value):
        if self.fail_favourite:
            raise RuntimeError('no book')
        self.favourites[uid] = value
        return True

    # ManagedDownloadView (the "詳細" dialog) needs these
    def download_candidates(self):
        return ()

    def close(self):
        self.engine.stop()
        self.engine.join(5)
        self.batch_run_ledger.close()
        self.following_sync.close()


def by_testid(client):
    found = {}
    for element in client.elements.values():
        name = element.props.get('data-testid')
        if name:
            found.setdefault(name, []).append(element)
    return found


def texts(client):
    return [element.text for element in client.elements.values() if isinstance(getattr(element, 'text', None), str)]


def make(tmp_path, rows=(), **kwargs):
    services = FakeServices(tmp_path, rows, **kwargs)
    client = Client(ui.page(f'/_w_{id(services)}'))
    with client:
        view = UnifiedDownloadView(services)
    return services, client, view


def finish(services, client):
    client.delete()
    services.close()


@pytest.mark.asyncio
async def test_dom_structure_follows_the_original_layout(tmp_path):
    services, client, view = make(tmp_path, author_rows(5))
    try:
        found = by_testid(client)
        for name in ('unified-download-view', 'unified-header', 'unified-left', 'unified-right', 'unified-band',
                     'unified-mode-followed', 'unified-mode-bookmark', 'unified-search', 'unified-select-all',
                     'unified-deselect-all', 'unified-star-only', 'unified-sort', 'unified-sort-asc',
                     'unified-sort-desc', 'unified-count', 'unified-thead', 'unified-author-list',
                     'unified-target-card', 'unified-target-count', 'unified-favorite-card',
                     'unified-favorite-count', 'unified-favorite-add', 'unified-type', 'unified-run',
                     'unified-pause', 'unified-stop', 'unified-band-status', 'unified-details-open'):
            assert name in found, name
        labels = texts(client)
        for text in ('ダウンロード', '作者を選ぶか、IDを入れて実行します', 'フォロー中の作者', 'マイブックマーク',
                     '表示中を全部選ぶ', '選択を全部外す', '☆のみ', 'この回の対象', '自動監視', '☆を対象にする',
                     '待機中', '左の一覧でチェックすると、ここに積まれます'):
            assert text in labels, text
        assert found['unified-search'][0].props['label'] == '絞り込み（数字はID指定）'
        # the columns of the table
        head = [c.text for c in view.thead.default_slot.children if isinstance(getattr(c, 'text', None), str)]
        assert head == ['作者名', 'ID', '保存済み', '最後に取得した日']
        assert found['unified-mode-followed'][0].props['data-selected'] == 'true'
        assert found['unified-mode-bookmark'][0].props['data-selected'] == 'false'
        assert view.mode == 'followed'
    finally:
        finish(services, client)


@pytest.mark.asyncio
async def test_rows_show_name_id_saved_count_and_last_downloaded(tmp_path):
    rows = author_rows(3)
    rows[1]['last_downloaded'] = '2026-11-17T09:30:00+00:00'
    services, client, view = make(tmp_path, rows)
    services.counts = {'1001': 7}
    try:
        assert await view.reload()
        assert list(view._rows) == ['1001', '1002', '1003']
        found = by_testid(client)
        assert [e.text for e in found['unified-row-count']] == ['7', '0', '0']
        assert [e.text for e in found['unified-row-last']] == ['－', '2026-11-17 09:30', '－']
        assert view.count_label.text == '1-3 / 3 人'
    finally:
        finish(services, client)


@pytest.mark.asyncio
async def test_sort_menu_has_the_five_original_choices_and_each_one_orders_the_table(tmp_path):
    rows = [dict(user_id='11', name='ccc', follow_order=0, is_favorite=False, last_downloaded='2026-01-01T00:00:00'),
            dict(user_id='12', name='aaa', follow_order=2, is_favorite=True, last_downloaded=None),
            dict(user_id='13', name='bbb', follow_order=1, is_favorite=False, last_downloaded='2026-05-01T00:00:00')]
    services, client, view = make(tmp_path, rows)
    services.counts = {'11': 5, '12': 1, '13': 9}
    try:
        await view.reload()
        found = by_testid(client)
        menu = [view.sort_items[key] for key in U.SORT_LABELS]
        assert [e.default_slot.children[0].text for e in menu] == list(U.SORT_LABELS.values()) and len(menu) == 5
        assert all(f'unified-sort-{key}' in found for key in U.SORT_LABELS)
        expected = {'follow_order': ['11', '13', '12'], 'name': ['12', '13', '11'], 'work_count': ['12', '11', '13'],
                    'last_downloaded': ['12', '11', '13'], 'favorite': ['12', '11', '13']}
        for key, order in expected.items():
            assert view.set_sort(key)
            assert [a['user_id'] for a in view.shown_authors()] == order, key
            assert list(view._rows) == order, key
            assert view.sort_label.text == U.SORT_LABELS[key]
            view.set_sort_dir(True)
            assert list(view._rows) == ([c for c in order if c != '12'] + ['12'] if key == 'favorite' else order[::-1]), key  # ties keep their order
            assert view.desc_button.props['data-on'] == 'true' and view.asc_button.props['data-on'] == 'false'
            view.set_sort_dir(False)
        assert not view.set_sort('id')       # the old 3-choice "ID順" is gone
    finally:
        finish(services, client)


@pytest.mark.asyncio
async def test_star_only_filter_and_toggling_a_star(tmp_path):
    services, client, view = make(tmp_path, author_rows(8))
    try:
        await view.reload()
        assert len(view._rows) == 8 and view.favorite_count.text == '2 人'
        view.toggle_favorite_filter()
        assert set(view._rows) == {'1001', '1005'} and view.star_filter_button.props['data-on'] == 'true'
        assert view.count_label.text.startswith('1-2 / 2 人')
        assert view.toggle_favorite('1005') and services.favourites == {'1005': False}
        assert set(view._rows) == {'1001'} and view.favorite_count.text == '1 人'      # left the ☆-only list
        services.fail_favourite = True
        assert not view.toggle_favorite('1001')
        assert view.authors[0]['is_favorite'] is True                                    # not lied about
        view.toggle_favorite_filter()
        assert len(view._rows) == 8
    finally:
        finish(services, client)


@pytest.mark.asyncio
async def test_select_all_shown_and_clear_touch_only_the_filtered_list(tmp_path):
    services, client, view = make(tmp_path, author_rows(10))
    try:
        await view.reload()
        view.set_query('作者00')           # 作者000-009: all of them
        view.select_all_shown()
        assert view.target_ids() == [str(1001 + i) for i in range(10)]
        assert view.header_check_state() == 'all' and view.target_count.text == '10 人'
        view.set_query('作者003')          # filtering never changes the targets
        assert len(view.targets) == 10 and view.header_check_state() == 'all'
        view.deselect_all_shown()          # only what is shown
        assert len(view.targets) == 9 and '1004' not in view.target_ids()
        view.set_query('')
        assert view.header_check_state() == 'partial'
        view.toggle_header_check()         # partial -> select all shown
        assert len(view.targets) == 10 and view.header_check_state() == 'all'
        view.toggle_header_check()         # all -> clear
        assert view.targets == [] and view.header_check_state() == 'empty'
        # the star card puts every ☆ author on the list
        assert view.add_favorites_to_targets()
        assert view.target_ids() == ['1001', '1005', '1009']
        assert [c.props['data-user-id'] for c in by_testid(client)['unified-chip']] == ['1001', '1005', '1009']
    finally:
        finish(services, client)


@pytest.mark.asyncio
async def test_fifty_authors_per_page(tmp_path):
    services, client, view = make(tmp_path, author_rows(120))
    try:
        await view.reload()
        assert PAGE_SIZE == 50 and len(view._rows) == 50
        assert view.count_label.text == '1-50 / 120 人　1 / 3 ページ'
        assert view.prev_button.props.get('disabled') is True and 'disabled' not in view.next_button.props
        view.go_page(1)
        assert len(view._rows) == 50 and list(view._rows)[0] == '1051'
        view.go_page(1)
        assert len(view._rows) == 20 and view.count_label.text == '101-120 / 120 人　3 / 3 ページ'
        assert view.next_button.props.get('disabled') is True
        view.go_page(5)                                  # out of range is clamped
        assert view.page == 2
        view.set_query('作者11')                         # a filter goes back to page 1 and the pager hides for one page
        assert view.page == 0 and len(view._rows) == 10
        assert view.prev_button.visible is False and view.next_button.visible is False
        view.set_query('')
        view.set_sort('name')
        assert view.page == 0
    finally:
        finish(services, client)


@pytest.mark.asyncio
async def test_a_number_is_an_id_and_can_be_added_even_when_not_followed(tmp_path):
    services, client, view = make(tmp_path, author_rows(3))
    try:
        await view.reload()
        view.set_query('１００１')                       # full-width digits
        assert view.id_row.visible and 'フォロー中の 作者000 です（ID: 1001）' in view.id_row_text.text
        view.set_query('777')
        assert 'ID 777 を対象にする（フォロー中にはいません）' in view.id_row_text.text
        assert view.pick_from_id() and view.targets[0]['origin'] == 'id'
        assert not view.pick_from_id()                   # no duplicates
        view.set_query('0123')                           # leading zero is not an author ID
        assert not view.pick_from_id() and len(view.targets) == 1
        view.set_query('')
        assert not view.id_row.visible
    finally:
        finish(services, client)


@pytest.mark.asyncio
async def test_run_is_blocked_with_zero_targets_and_says_why(tmp_path):
    services, client, view = make(tmp_path, author_rows(3))
    try:
        await view.reload()
        assert view.run_button.props.get('disabled') is True
        assert view.run_wrap.props['title'] == NEED_TARGET == '左の一覧で作者を選ぶと押せます'
        assert view.run_button.props['aria-label'] == NEED_TARGET
        assert not view.run() and services.batch_run_ledger.runs() == ()
        assert view.pause_button.props.get('disabled') is True and view.stop_button.props.get('disabled') is True
        view.toggle_pick('1001')
        assert 'disabled' not in view.run_button.props and view.run_wrap.props['title'] == ''
        view.toggle_pick('1001')
        assert view.run_button.props.get('disabled') is True
        # the type toggle cycles illust -> novel -> both
        assert view.target_type == 'both'
        seen = []
        for _ in range(3):
            view.cycle_type()
            seen.append(view.target_type)
        assert seen == ['illust', 'novel', 'both']
    finally:
        finish(services, client)


@pytest.mark.asyncio
async def test_without_an_engine_the_screen_still_renders_and_run_stays_off(tmp_path):
    services, client, view = make(tmp_path, author_rows(2))
    try:
        services.batch_runs = None
        view.toggle_pick('1001')
        assert view.run_button.props.get('disabled') is True
        assert 'ありません' not in view.run_wrap.props['title'] and '準備' in view.run_wrap.props['title']
        assert not view.run()
    finally:
        finish(services, client)


@pytest.mark.asyncio
async def test_run_creates_a_ledger_run_chips_follow_it_and_last_downloaded_is_stamped(tmp_path):
    services, client, view = make(tmp_path)
    try:
        await view.reload()
        assert [a['user_id'] for a in view.authors] == ['41', '42', '43']
        assert all(a['last_downloaded'] is None for a in view.authors)
        view.toggle_pick('41')
        view.toggle_pick('42')
        assert view.run()
        runs = services.batch_run_ledger.runs()
        assert len(runs) == 1 and runs[0].kind == 'authors' and runs[0].origin == 'manual'
        assert runs[0].target_type == 'both' and runs[0].authors_total == 2
        assert services.batch_runs.join(10)
        view.tick()
        assert services.batch_run_ledger.run(runs[0].run_id).state == 'done'
        assert [a.state for a in services.batch_run_ledger.authors(runs[0].run_id)] == ['done', 'done']
        assert view.run_state == 'idle' and view.band_status.text == '完了しました。'
        assert [c.props['data-status'] for c in by_testid(client)['unified-chip']] == ['done', 'done']
        assert [e.text for e in client.elements.values() if getattr(e, 'text', None) == '完了'] == ['完了', '完了']
        assert 'disabled' not in view.run_button.props
        await view.reload()                              # the table now shows the stamped dates
        stamped = {a['user_id']: a['last_downloaded'] for a in view.authors}
        assert stamped['41'] and stamped['42'] and stamped['43'] is None
        assert services.port.enqueued                    # works were queued through the engine
        assert view.bar.props['data-mode'] == 'determinate'
    finally:
        finish(services, client)


@pytest.mark.asyncio
async def test_pause_resume_and_stop_drive_the_engine(tmp_path):
    services, client, view = make(tmp_path, hold=True)
    try:
        await view.reload()
        view.toggle_pick('41')
        assert view.run()
        for _ in range(200):                             # the run lists, queues, then waits on the held jobs
            if services.port.enqueued:
                break
            await asyncio.sleep(0.01)
        view.tick()
        assert view.running and view.run_button.props.get('disabled') is True and view.run_label.text == '実行中'
        assert 'disabled' not in view.pause_button.props and 'disabled' not in view.stop_button.props
        assert view.mode_buttons['bookmark'].props.get('disabled') is True       # no switching mid-run
        assert not view.set_mode('bookmark')
        view.toggle_pause()
        assert services.batch_runs.state == 'paused' and view.pause_icon.props['name'] == 'play_arrow'
        assert view.band_status.text == U.PAUSE_TEXT['user']
        view.toggle_pause()
        assert services.batch_runs.state == 'running' and view.pause_icon.props['name'] == 'pause'
        view.stop()
        assert view.band_status.text == '停止しています（いまの作者が終わるまで待っています）…'
        assert services.batch_runs.join(10)
        view.tick()
        assert view.run_state == 'idle' and view.band_status.text == '停止しました。'
        run = services.batch_run_ledger.runs()[0]
        assert run.state == 'stopped'
        assert view.resume_button.visible and view.discard_button.visible        # a stopped run can be continued
        view.discard_interrupted()
        assert not view.resume_button.visible
        assert services.following_sync.snapshot(ACCOUNT).authors[0].last_downloaded is None   # a stop stamps nothing
    finally:
        finish(services, client)


@pytest.mark.asyncio
async def test_bookmark_mode_swaps_the_pane_and_keeps_targets(tmp_path):
    services, client, view = make(tmp_path, author_rows(3))
    try:
        await view.reload()
        view.toggle_pick('1002')
        assert view.set_mode('bookmark')
        assert view.followed_pane.visible is False and view.bookmark_pane.visible is True
        assert view.mode_buttons['bookmark'].props['data-selected'] == 'true'
        assert view.target_count.text == '全体'
        assert view.run_button.props.get('disabled') is True        # run in this mode arrives with WU-8
        view.set_mode('followed')
        assert view.followed_pane.visible is True and view.target_ids() == ['1002']
    finally:
        finish(services, client)


@pytest.mark.asyncio
async def test_existing_per_work_screen_and_its_testids_survive_in_the_details_dialog(tmp_path):
    services, client, view = make(tmp_path, author_rows(3))
    try:
        assert view.legacy is None                                   # not built until it is asked for
        await view.reload()
        view.toggle_pick('1002')
        assert view.open_details()
        found = by_testid(client)
        for name in ('managed-download-view', 'managed-download-mode-bookmark', 'managed-download-mode-author',
                     'managed-download-submit', 'managed-download-refresh', 'managed-download-direct-id',
                     'managed-download-direct-lookup', 'managed-download-author-panel', 'managed-download-author-add',
                     'managed-download-author-fetch', 'managed-download-author-scan', 'managed-download-unfollowed',
                     'managed-download-follow-refresh', 'managed-download-follow-list', 'managed-download-follow-sort',
                     'managed-download-follow-star-only', 'download-details', 'download-detail-toggle'):
            assert name in found, name
        managed = view.managed
        assert managed is view.legacy and managed.active is True
        assert [a.user_id for a in managed.author_targets] == ['1002']          # the same authors as this round
        assert [a.user_id for a in managed.follow_authors] == ['1001', '1002', '1003']
        view.close_details()
        assert managed.active is False
        view.set_active(False)
        assert view.root.props['data-active'] == 'false'
    finally:
        finish(services, client)


@pytest.mark.asyncio
async def test_shell_puts_the_screen_in_the_download_tab(tmp_path):
    from pixivvault_nicegui.ui.shell import DemoShell
    from test_managed_download_services import FakeSource
    services = ApplicationServices(live_client=FakeSource(), pixiv_download_root=tmp_path / 'managed',
                                   preferences_root=tmp_path / 'prefs')
    services.configure_save_exports(tmp_path / 'exports', '12')
    client = Client(ui.page('/_w_shell'))
    try:
        with client:
            shell = DemoShell(services)
            assert shell.active_tab == 'download'
            view = shell.job_views['download']
            assert isinstance(view, UnifiedDownloadView)
            assert services.batch_runs is not None and view.engine is services.batch_runs
            assert view.run_block_reason() == NEED_TARGET
            shell.close()
    finally:
        client.delete()
        await asyncio.to_thread(services.close)


def test_services_expose_the_local_author_rows_and_saved_counts(tmp_path):
    from test_managed_download_services import FakeSource
    services = ApplicationServices(live_client=FakeSource(), pixiv_download_root=tmp_path / 'managed',
                                   preferences_root=tmp_path / 'prefs', following_sync_root=tmp_path / 'follow')
    try:
        assert services.unified_download_authors() == () and services.saved_author_counts() == {}
        events = []
        stop = services.subscribe_batch_events(events.append)
        services._fan_out_batch_event({'event': 'run_start'})
        stop()
        services._fan_out_batch_event({'event': 'run_end'})
        assert events == [{'event': 'run_start'}]
    finally:
        services.close()
