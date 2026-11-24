"""E-A: 元(Flet版 gui.py:2183,2264-2297)の「学習状況のリセット」の復元。"""
import asyncio
import pytest
from pixivvault_nicegui.fixtures import generate_demo_artworks
from pixivvault_nicegui.learning import Judgment
from pixivvault_nicegui.learning_repository import LearningConflict
from pixivvault_nicegui.learning_service import LearningService


@pytest.fixture
async def service(tmp_path):
    s = LearningService(tmp_path / 'learning', generate_demo_artworks(4))
    try:
        yield s
    finally:
        await asyncio.to_thread(s.close)


async def test_reset_clears_history_and_counts(service):
    session = await service.open_session()
    await service.record(session.session_id, Judgment('demo-0001', 'like', 'a'))
    await service.record(session.session_id, Judgment('demo-0002', 'hate', 'b'))
    assert await service.judged_count() == 2
    result = await service.reset_learning()
    assert result['history'] == 2
    assert await service.judged_count() == 0
    review = await service.review_snapshot(session.session_id)
    assert review['rows'] == [] and review['tags'] == {} and review['authors'] == {}


async def test_reset_discards_review_undo_tombstones_and_undo_stack(service):
    main = await service.open_session()
    review = await service.open_session()
    await service.record(main.session_id, Judgment('demo-0001', 'like', 'a'))
    row = (await service.review_snapshot(review.session_id))['rows'][0]
    await service._wait(service.submit_review_undo(review.session_id, row['work_id'], row['revision'], 'undo'))
    await service.record(main.session_id, Judgment('demo-0002', 'like', 'b'))
    await service.reset_learning()
    # review-undone judgment must not be resurrected by a stale redo
    with pytest.raises(KeyError):
        await service._wait(service.submit_review_restore(review.session_id, 'undo', 'redo'))
    # the pre-reset undo stack must not revert anything
    with pytest.raises((LearningConflict, ValueError)):
        await service.revert(main.session_id, Judgment('demo-0002', 'like', 'b'), 'old-undo')
    assert await service.judged_count() == 0


async def test_reset_is_durable_across_reopen(tmp_path):
    s = LearningService(tmp_path / 'learning', generate_demo_artworks(3))
    try:
        session = await s.open_session()
        await s.record(session.session_id, Judgment('demo-0001', 'like', 'a'))
        await s.reset_learning()
    finally:
        await asyncio.to_thread(s.close)
    s = LearningService(tmp_path / 'learning', generate_demo_artworks(3))
    try:
        assert await s.judged_count() == 0
        assert (await s.open_session()).judged_ids == frozenset()
    finally:
        await asyncio.to_thread(s.close)


async def test_history_export_rows_match_original_columns(service):
    session = await service.open_session()
    await service.record(session.session_id, Judgment('demo-0001', 'superLike', 'a'))
    rows = await service.judgment_export_rows()
    assert [r['work_id'] for r in rows] == ['demo-0001'] and rows[0]['verdict'] == 'superLike'


async def _view(tmp_path, monkeypatch, name):
    from nicegui import Client, ui, core
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    from pixivvault_nicegui.services import ApplicationServices
    from pixivvault_nicegui.ui.learning_view import LearningView
    s = ApplicationServices(3, learning_sandbox=tmp_path / 'learning')
    client = Client(ui.page('/' + name))
    return s, client, LearningView


async def test_reset_dialog_text_gate_and_execution(tmp_path, monkeypatch):
    s, client, LearningView = await _view(tmp_path, monkeypatch, 'e-reset')
    try:
        with client:
            v = LearningView(s)
            from pixivvault_nicegui.ui.learning_reset import LearningResetView
            from pixivvault_nicegui.ui.preferences_view import PreferencesView

            preferences = PreferencesView(s.preferences, build_learning_reset=lambda is_open:
                                          LearningResetView(v, v.storage, is_open=is_open))
            preferences.open()
            r = preferences.learning_reset
            assert not v.active
            assert r.item_label.text == '学習状況のリセット'
            assert r.button.text == '学習状況をリセット'
            await r.refresh()
            assert r.desc.text == 'まだ判定がありません。'
            assert not r.button.enabled
            session = await s.learning.open_session()
            for i, c in enumerate(('like', 'hate', 'skip'), 1):
                await s.learning.record(session.session_id, Judgment(f'demo-000{i}', c, f'o{i}'))
            await r.refresh()
            assert r.desc.text == (
                'これまでの判定（3枚）と、そこから学んだ好みの傾向を消します。'
                'おすすめタブの「あなたがSuperLikeした作品」の一覧と、閲覧履歴に付いている判定バッジ、'
                'ランキングの「合わない を除外」も一緒に空になります。閲覧履歴そのものと、'
                '「出さない」に登録した作品や作者は消えません。')
            assert r.button.enabled
            await r.open()
            assert r.title.text == '学習状況のリセット（判定 3枚）'
            assert r.warning.text == '削除すると元に戻せません。件数を入力すると「学習状況をリセット」が押せます。'
            assert r.confirm.props['label'] == '件数を入力'
            assert not r.execute_button.enabled
            r.confirm.set_value('2')
            assert not r.execute_button.enabled
            r.confirm.set_value('abc')
            assert not r.execute_button.enabled
            await r.execute()  # gate: refuses while locked
            assert await s.learning.judged_count() == 3
            r.confirm.set_value('3')
            assert r.execute_button.enabled
            await r.execute()
            assert await s.learning.judged_count() == 0
            assert r.notice.text == '学習状況: リセットしました'
            assert r.desc.text == 'まだ判定がありません。'
            assert not r.dialog.value
            preferences.close()
            v.close()
    finally:
        s.close()
        client.delete()


async def test_reset_failure_keeps_data_and_shows_no_raw_error(tmp_path, monkeypatch):
    s, client, LearningView = await _view(tmp_path, monkeypatch, 'e-reset-fail')
    try:
        with client:
            v = LearningView(s)
            from pixivvault_nicegui.ui.learning_reset import LearningResetView
            from pixivvault_nicegui.ui.preferences_view import PreferencesView

            preferences = PreferencesView(s.preferences, build_learning_reset=lambda is_open:
                                          LearningResetView(v, v.storage, is_open=is_open))
            preferences.open()
            r = preferences.learning_reset
            assert not v.active
            session = await s.learning.open_session()
            await s.learning.record(session.session_id, Judgment('demo-0001', 'like', 'o1'))
            await r.refresh()
            await r.open()
            r.confirm.set_value('1')

            def boom(*a, **k):
                raise RuntimeError('private-data')
            original = s.learning._reset
            s.learning._reset = boom
            await r.execute()
            s.learning._reset = original
            assert 'private-data' not in r.notice.text
            assert r.notice.text.startswith('学習状況: リセットに失敗しました')
            assert await s.learning.judged_count() == 1
            preferences.close()
            v.close()
    finally:
        s.close()
        client.delete()
