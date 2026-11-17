"""FL-F05/E07/H02/L09/I15: 小さな表示・遷移の原本合わせ（元 gui.py:4406, 3734-3739, 5439-5480 ほか）。"""
from types import SimpleNamespace

from nicegui import ui

from pixivvault_nicegui.job_service import JobSnapshot
from pixivvault_nicegui.ui.job_views import QueueView


def _texts(view):
    return [str(getattr(e, 'text', '')) for e in view.root.descendants() if getattr(e, 'text', None) is not None]


def _tooltips(view):
    return [e.text for e in view.root.descendants() if isinstance(e, ui.tooltip)]


def _queue(failed_only, snapshots=()):
    service = SimpleNamespace(snapshot=lambda: tuple(snapshots), faulted=False)
    calls = []
    original = service.snapshot

    def counting():
        calls.append(1)
        return original()
    service.snapshot = counting
    with ui.column():
        view = QueueView(SimpleNamespace(jobs=service, artworks=()), failed_only=failed_only)
    return view, calls


def test_failed_list_has_refresh_button_with_original_tooltip():
    view, calls = _queue(True)
    try:
        button = view.refresh_button
        assert button.props['data-testid'] == 'failed-refresh'
        assert 'リスト更新' in _tooltips(view)
        before = len(calls)
        click = next(e.handler for e in button._event_listeners.values() if e.type == 'click')
        click(SimpleNamespace(args={}))
        assert len(calls) > before          # 押すと一覧を読み直す（通信なし・ローカルのジョブ台帳のみ）
    finally:
        view.close()


def test_queue_heading_and_helper_text_match_original():
    view, _ = _queue(False)
    try:
        texts = _texts(view)
        assert '現在のジョブ（アプリ連携）' in texts
        assert '2秒毎に自動更新' in texts
        assert not any(t == 'キュー' for t in texts)
        assert not hasattr(view, 'refresh_button')    # 元のキューにはリスト更新ボタンはない
    finally:
        view.close()


def test_failed_tab_keeps_its_own_heading():
    view, _ = _queue(True)
    try:
        assert '失敗リスト' in _texts(view)
        assert '現在のジョブ（アプリ連携）' not in _texts(view)
    finally:
        view.close()


import pytest
from nicegui import Client
from pixivvault_nicegui.ui.failed_records_view import FailedRecordsView
from test_q07_novel_retry import create


@pytest.mark.asyncio
async def test_legacy_failed_records_refresh_tooltip_is_original(tmp_path):
    service, reader, db = create(tmp_path, 3)
    client = Client(ui.page('/_h05_failed_refresh'))
    try:
        with client:
            view = FailedRecordsView(service)
            assert view.reload_button.props['aria-label'] == 'リスト更新'
            assert 'リスト更新' in _tooltips(view)
            view.close()
    finally:
        client.delete()
        service.close()


@pytest.mark.asyncio
async def test_duplicate_mode_chips_are_equal_width_190px_like_original():
    # 元 gui.py:5462-5469: 重複整理の2チップは「等幅2択」(190px固定)。件数・未走査の「–」・
    # 切替は既存の DuplicateView 実装（tests/test_duplicate_view.py）で既に原本どおり。
    from test_duplicate_view import StubSource
    from pixivvault_nicegui.ui.duplicate_view import DuplicateView
    client = Client(ui.page('/_h05_dup_chips'))
    try:
        with client:
            view = DuplicateView(StubSource())
            for key in ('undecidable', 'near'):
                assert view.mode_buttons[key]._style.get('width') == '190px'
            view.close()
    finally:
        client.delete()


# ---- FL-L09 おすすめの空状態 ------------------------------------------------

def _recommend_texts(view):
    return [str(getattr(e, 'text', '')) for e in view.body.descendants() if getattr(e, 'text', None) is not None]


@pytest.mark.asyncio
async def test_recommend_empty_state_offers_jump_to_learning_tab():
    from types import SimpleNamespace as NS
    from pixivvault_nicegui.recommendation_state import RecommendationController
    from pixivvault_nicegui.ui.recommend_view import RecommendView
    from test_fail7_batch1 import EmptyRecommendationSource
    jumps = []
    client = Client(ui.page('/_h05_recommend_empty'))
    try:
        with client:
            view = RecommendView(NS(recommendations=RecommendationController(EmptyRecommendationSource())),
                                 on_navigate_to_learning=lambda: jumps.append('learning'))
            view.active = True
            await view.refresh()
            texts = _recommend_texts(view)
            assert 'おすすめの材料がまだありません' in texts                       # 元 recommend_tab.py:2896
            assert '学習タブで好み・嫌いを判定すると、ここにおすすめが表示されます。' in texts
            button = next(e for e in view.body.descendants() if getattr(e, 'text', None) == '学習タブへ')
            click = next(e.handler for e in button._event_listeners.values() if e.type == 'click')
            click(NS(args={}))
            assert jumps == ['learning']
            view.close()
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_recommend_empty_state_has_no_button_without_a_destination():
    from types import SimpleNamespace as NS
    from pixivvault_nicegui.recommendation_state import RecommendationController
    from pixivvault_nicegui.ui.recommend_view import RecommendView
    from test_fail7_batch1 import EmptyRecommendationSource
    client = Client(ui.page('/_h05_recommend_empty2'))
    try:
        with client:
            view = RecommendView(NS(recommendations=RecommendationController(EmptyRecommendationSource())))
            view.active = True
            await view.refresh()
            assert not any(getattr(e, 'text', None) == '学習タブへ' for e in view.body.descendants())
            view.close()
    finally:
        client.delete()
