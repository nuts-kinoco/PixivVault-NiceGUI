"""WU-8: bookmark run (engine kind='bookmarks'), the completed progress band, the bookmark pane, ZIP toast."""
import asyncio
from types import SimpleNamespace

import pytest
from nicegui import Client, ui

from pixivvault_nicegui import unified_download_state as U
from pixivvault_nicegui.author_download import Difference
from pixivvault_nicegui.batch_engine import BatchRunEngine, ServicesPort
from pixivvault_nicegui.batch_runs import BatchRunLedger
from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
from pixivvault_nicegui.bookmark_run import BookmarkCandidate, ROW_VISIBILITY, VISIBILITY_ROWS, classify
from pixivvault_nicegui.following_sync import FollowingSyncStore
from pixivvault_nicegui.ui.unified_download_view import UnifiedDownloadView

from test_w_dl_screen_20261118 import FakeServices, FakePort, by_testid, finish, texts

ACCOUNT = '55'


def remote(wid, visibility='show', author='31', kind='illustration', typ=0, title=None):
    return RemoteBookmark(str(wid), kind, visibility, title or f'作品{wid}', author, '作者', (), 'https://i.pximg.net/a.jpg',
                          '', 1, typ, 0)


def candidate(rec, difference=Difference.NEW):
    return BookmarkCandidate(rec.remote_id, rec.author_id, rec.title, rec.visibility, difference, record=rec)


class BookmarkPort(FakePort):
    def __init__(self, records, saved=(), hold=False):
        super().__init__(hold)
        self.records = list(records)
        self.saved = set(saved)
        self.listed = []

    def list_bookmarks(self, visibility):
        self.listed.append(visibility)
        recs = [r for r in self.records if r.visibility == visibility]
        return tuple(candidate(r, Difference.SKIPPED if r.remote_id in self.saved else Difference.NEW) for r in recs)


def engine_for(tmp_path, port):
    ledger = BatchRunLedger(tmp_path / 'runs')
    following = FollowingSyncStore(tmp_path / 'follow')

    class Reader:
        def require_identity(self):
            return ACCOUNT

        def get_following_users_detailed(self, uid, rest):
            return () if rest == 'hide' else (dict(user_id='31', name='n', account='', profile_img=''),)
    following.sync(Reader())
    events = []
    engine = BatchRunEngine(port, ledger, following=following, account_id=ACCOUNT, poll_seconds=0.001,
                            wait=lambda s: None, on_progress=events.append)
    return engine, ledger, following, events


# ---------------------------------------------------------------- engine: kind='bookmarks'
def test_bookmark_run_lists_each_chosen_visibility_queues_new_works_and_never_stamps(tmp_path):
    port = BookmarkPort([remote(11), remote(12), remote(13, 'hide')], saved={'12'})
    engine, ledger, following, events = engine_for(tmp_path, port)
    try:
        run_id = engine.run(('show', 'hide'), kind='bookmarks')
        run = ledger.run(run_id)
        assert run.kind == 'bookmarks' and run.state == 'done' and run.authors_total == 2
        rows = ledger.authors(run_id)
        assert [r.author_id for r in rows] == [VISIBILITY_ROWS['show'], VISIBILITY_ROWS['hide']]
        assert [(r.state, r.n_new, r.n_skipped) for r in rows] == [('done', 1, 1), ('done', 1, 0)]
        assert port.listed == ['show', 'hide'] and port.enqueued == ['11', '13']
        assert all(r.stamp_written == 0 and r.last_downloaded_utc is None for r in rows)
        assert following.snapshot(ACCOUNT).authors[0].last_downloaded is None       # bookmarks never stamp
        assert [e['event'] for e in events if e['event'] == 'run_end'] == ['run_end']
    finally:
        ledger.close()
        following.close()


def test_a_single_visibility_is_all_that_is_listed(tmp_path):
    port = BookmarkPort([remote(11), remote(13, 'hide')])
    engine, ledger, following, _ = engine_for(tmp_path, port)
    try:
        run_id = engine.run(('hide',), kind='bookmarks')
        assert port.listed == ['hide'] and port.enqueued == ['13'] and ledger.run(run_id).authors_total == 1
        with pytest.raises(Exception):
            engine.run(('everything',), kind='bookmarks')
    finally:
        ledger.close()
        following.close()


def test_work_events_carry_the_title_of_the_work_in_progress(tmp_path):
    port = BookmarkPort([remote(11, title='青い空')])
    engine, ledger, following, events = engine_for(tmp_path, port)
    try:
        engine.run(('show',), kind='bookmarks')
        info = [e for e in events if e['event'] == 'work_info']
        assert info and info[0]['title'] == '青い空' and info[0]['author_id'] == VISIBILITY_ROWS['show']
        assert [e['title'] for e in events if e['event'] == 'work_progress'] == ['青い空']
    finally:
        ledger.close()
        following.close()


def test_author_runs_also_report_titles(tmp_path):
    from test_w_dl_screen_20261118 import cand
    port = FakePort()
    engine, ledger, following, events = engine_for(tmp_path, port)
    try:
        engine.run(('31',))
        assert {e['title'] for e in events if e['event'] == 'work_info'} == {'t311', 't312'}
    finally:
        ledger.close()
        following.close()


def test_a_stopped_bookmark_run_can_be_continued_over_the_unfinished_visibility(tmp_path):
    port = BookmarkPort([remote(11), remote(13, 'hide')], hold=True)
    engine, ledger, following, _ = engine_for(tmp_path, port)
    try:
        engine.start(('show', 'hide'), kind='bookmarks')
        for _ in range(500):
            if port.enqueued:
                break
            import time
            time.sleep(0.01)
        engine.stop()
        assert engine.join(10)
        assert engine.resumable_run().kind == 'bookmarks'
        port.hold = False
        engine.start_unfinished()
        assert engine.join(10)
        assert sorted(set(port.listed)) == ['hide', 'show'] and ledger.runs()[0].state == 'done'
    finally:
        ledger.close()
        following.close()


def test_classify_marks_saved_and_unsupported_bookmarks():
    recs = [remote(1), remote(2), remote(3, typ=2), remote(4, kind='novel')]
    got = classify(recs, is_saved=lambda wid: wid == '2')
    assert [(c.work_id, c.difference) for c in got] == [('1', Difference.NEW), ('2', Difference.SKIPPED)]


# ---------------------------------------------------------------- services port
def test_services_port_lists_cached_bookmarks_and_enqueues_through_the_managed_path():
    recs = [remote(11), remote(12, 'hide'), remote(13)]
    accepted = []
    services = SimpleNamespace(
        closing=False, bookmark_download_candidates=lambda: tuple(recs),
        managed_downloads=SimpleNamespace(verified_saved_receipt=lambda wid, verify_pages=True:
                                          ('x', {}, 'd', None, None) if wid == '13' else None),
        enqueue_managed_download=lambda rec, op, return_created=False:
            (accepted.append((rec.remote_id, op)), (SimpleNamespace(job_id='j' + rec.remote_id), True))[1])
    port = ServicesPort(services)
    rows = port.list_bookmarks('show')
    assert [(c.work_id, c.difference) for c in rows] == [('11', Difference.NEW), ('13', Difference.SKIPPED)]
    assert [c.work_id for c in port.list_bookmarks('hide')] == ['12']
    assert port.enqueue(rows[0], 'op1') == ('j11', True) and accepted == [('11', 'op1')]


# ---------------------------------------------------------------- view: bookmark pane and run
class BmServices(FakeServices):
    def __init__(self, tmp_path, rows, records=(), **kw):
        super().__init__(tmp_path, rows, **kw)
        self.port = BookmarkPort(records, hold=kw.get('hold', False))
        self.batch_runs.port = self.port
        self.refreshed = 0
        self.settings = {'use_work_folder': '0', 'bookmark_download_mode': 'direct'}

    async def start_bookmark_run(self, visibilities, *, origin='manual', target_type=None):
        self.refreshed += 1
        return self.batch_runs.start(tuple(visibilities), kind='bookmarks', origin=origin, target_type=target_type)

    def bookmark_layout(self):
        return dict(hierarchical=self.settings['bookmark_download_mode'] == 'link',
                    work_folder=self.settings['use_work_folder'] == '1')

    def set_bookmark_layout(self, *, hierarchical=None, work_folder=None):
        if hierarchical is not None:
            self.settings['bookmark_download_mode'] = 'link' if hierarchical else 'direct'
        if work_folder is not None:
            self.settings['use_work_folder'] = '1' if work_folder else '0'


def make_bm(tmp_path, rows=(), records=(), **kw):
    services = BmServices(tmp_path, rows, records, **kw)
    client = Client(ui.page(f'/_w8_{id(services)}'))
    with client:
        view = UnifiedDownloadView(services)
    return services, client, view


@pytest.mark.asyncio
async def test_bookmark_pane_has_the_visibility_checks_layout_toggle_and_unfollowed_button(tmp_path):
    services, client, view = make_bm(tmp_path)
    try:
        view.set_mode('bookmark')
        found = by_testid(client)
        for name in ('unified-bm-show', 'unified-bm-hide', 'unified-bm-hier', 'unified-bm-flat',
                     'unified-bm-work-folder', 'unified-bm-unfollowed', 'unified-bm-hint'):
            assert name in found, name
        assert [t for t in ('含めるブックマーク', '公開ブックマーク', '非公開ブックマーク', '保存方式',
                            '階層化（作者別フォルダ）', 'フラット配置', '未フォロー作者を確認') if t not in texts(client)] == []
        assert view.bm_show is True and view.bm_hide is False
        assert view.bookmark_pane.visible and not view.followed_pane.visible
    finally:
        finish(services, client)


@pytest.mark.asyncio
async def test_visibility_checks_decide_whether_run_can_be_pressed(tmp_path):
    services, client, view = make_bm(tmp_path)
    try:
        view.set_mode('bookmark')
        assert view.run_block_reason() == '' and 'disabled' not in view.run_button.props
        view.set_bm('show', False)
        assert view.run_block_reason() == '含めるブックマークを1つ選びます'
        assert view.run_button.props.get('disabled') is True and view.bm_hint.visible
        view.set_bm('hide', True)
        assert view.run_block_reason() == '' and not view.bm_hint.visible
    finally:
        finish(services, client)


@pytest.mark.asyncio
async def test_layout_toggles_write_the_settings_and_show_the_current_values(tmp_path):
    services, client, view = make_bm(tmp_path)
    try:
        view.set_mode('bookmark')
        assert view.bm_hierarchical is False and view.bm_work_folder is False
        view.set_bm_layout(hierarchical=True)
        assert services.settings == {'use_work_folder': '0', 'bookmark_download_mode': 'link'}
        view.set_bm_layout(work_folder=True)
        assert services.settings == {'use_work_folder': '1', 'bookmark_download_mode': 'link'}
        assert view.bm_buttons['hier'].props['data-selected'] == 'true' and view.bm_buttons['flat'].props['data-selected'] == 'false'
        view.set_bm_layout(hierarchical=False)
        assert services.settings == {'use_work_folder': '1', 'bookmark_download_mode': 'direct'}
    finally:
        finish(services, client)


@pytest.mark.asyncio
async def test_run_in_bookmark_mode_starts_a_bookmarks_run_that_never_stamps(tmp_path):
    services, client, view = make_bm(tmp_path, records=[remote(11), remote(12, 'hide')])
    try:
        await view.reload()
        view.set_mode('bookmark')
        view.set_bm('hide', True)
        assert await view.run_bookmarks()
        assert services.batch_runs.join(10)
        view.tick()
        run = services.batch_run_ledger.runs()[0]
        assert run.kind == 'bookmarks' and run.state == 'done' and run.authors_total == 2 and services.refreshed == 1
        assert services.port.enqueued == ['11', '12']
        assert services.following_sync.snapshot(ACCOUNT).authors[0].last_downloaded is None
        assert view.band_status.text == '完了しました。'
    finally:
        finish(services, client)


@pytest.mark.asyncio
async def test_bookmark_progress_reads_as_the_original_count_of_works(tmp_path):
    services, client, view = make_bm(tmp_path, records=[remote(11), remote(12), remote(13)], hold=True)
    try:
        view.set_mode('bookmark')
        assert await view.run_bookmarks()
        for _ in range(300):
            if len(services.port.enqueued) == 3:
                break
            await asyncio.sleep(0.01)
        view.tick()
        assert view.band_status.text.startswith('公開ブックマーク: ') and view.band_status.text.endswith(' / 3 件')
        view.stop()
        services.batch_runs.join(10)
    finally:
        finish(services, client)


@pytest.mark.asyncio
async def test_unfollowed_button_opens_the_existing_dialog_from_the_pane(tmp_path):
    services, client, view = make_bm(tmp_path)
    try:
        view.set_mode('bookmark')
        opened = []
        view.open_unfollowed = lambda: opened.append(1)
        view.on_unfollowed_click()
        assert opened == [1]
    finally:
        finish(services, client)


# ---------------------------------------------------------------- view: the progress band
@pytest.mark.asyncio
async def test_band_opens_and_closes_between_84_and_140_and_shows_the_record_boxes(tmp_path):
    services, client, view = make_bm(tmp_path, author_rows_for(2))
    try:
        await view.reload()
        assert view.band_open is False and view.band.props['data-open'] == 'false'
        assert view.band_height == 84
        view.toggle_band()
        assert view.band_open is True and view.band_height == 140 and view.band.props['data-open'] == 'true'
        assert [l for l in ('この回の記録', '開始', '進捗', '完了見込', '経過', 'クリックで詳細を閉じる') if l not in texts(client)] == []
        view.toggle_band()
        assert view.band_height == 84 and 'クリックで詳細を開く' in texts(client)
    finally:
        finish(services, client)


@pytest.mark.asyncio
async def test_record_boxes_follow_the_run(tmp_path):
    services, client, view = make_bm(tmp_path, author_rows_for(2))
    try:
        await view.reload()
        view.toggle_band()
        assert view.box_values['start'].text == '' and view.box_values['eta'].text == '算出中…'
        view.toggle_pick('41')
        view.toggle_pick('42')
        assert view.run()
        assert services.batch_runs.join(10)
        view.tick()
        assert view.box_values['start'].text != ''
        assert view.box_values['now'].text.startswith('2/2人')
        assert view.box_values['elapsed'].text.endswith('秒 経過') or '分' in view.box_values['elapsed'].text
    finally:
        finish(services, client)


@pytest.mark.asyncio
async def test_first_author_not_done_shows_work_counts_and_title_in_the_box(tmp_path):
    services, client, view = make_bm(tmp_path, author_rows_for(1), hold=True)
    try:
        await view.reload()
        view.toggle_band()
        view.toggle_pick('41')
        assert view.run()
        for _ in range(300):
            if services.port.enqueued:
                break
            await asyncio.sleep(0.01)
        view.progress.apply({'event': 'work_info', 'author_id': '41', 'title': '桜の下'})
        view.progress.apply({'event': 'work_progress', 'author_id': '41', 'done': 0, 'queued': 4})
        view.render_band_boxes()
        assert view.box_values['now'].text == '作品 1/4: 桜の下'
        view.stop()
        services.batch_runs.join(10)
    finally:
        finish(services, client)


@pytest.mark.asyncio
async def test_band_closes_when_the_body_gets_too_short_and_refuses_to_open_then(tmp_path):
    services, client, view = make_bm(tmp_path)
    try:
        view.on_body_size(700)
        view.toggle_band()
        assert view.band_open
        view.on_body_size(239)                       # below BODY_MIN_H: closes by itself
        assert view.band_open is False
        view.toggle_band()                           # opening would leave the body under 240 (the band grows 56px)
        assert view.band_open is False and 'ウィンドウ' in view.notice_text
        view.on_body_size(400)
        view.toggle_band()
        assert view.band_open
    finally:
        finish(services, client)


@pytest.mark.asyncio
async def test_double_click_and_the_hint_row_both_toggle(tmp_path):
    services, client, view = make_bm(tmp_path)
    try:
        by = by_testid(client)
        assert by['unified-band'][0]._event_listeners  # a listener exists
        types = {l.type for l in by['unified-band'][0]._event_listeners.values()}
        assert 'dblclick' in types
        hint_types = {l.type for l in by['unified-band-hint-row'][0]._event_listeners.values()}
        assert 'click' in hint_types
    finally:
        finish(services, client)


def author_rows_for(n):
    return []


# ---------------------------------------------------------------- ZIP corruption toast (FL-D16)
def test_a_failed_auto_zip_job_raises_the_zip_toast_once():
    from pixivvault_nicegui.save_exports import notify_zip_problem
    sent = []
    services = SimpleNamespace(notifications=SimpleNamespace(
        notify=lambda title, body, event_id=None: sent.append((title, body, event_id))))
    job = SimpleNamespace(kind='auto-zip', target_id='abc', job_id='j1')
    notify_zip_problem(services, job, ValueError('ZIP bytes changed'), author_id='31')
    notify_zip_problem(SimpleNamespace(notifications=None), job, ValueError('x'), author_id='31')   # no dispatcher: no crash
    assert sent == [('PixivVault - ZIP破損検出', '作者 31 のアーカイブに問題があります: ZIP bytes changed', 'zip-problem:j1')]
