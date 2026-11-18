"""FL-Y07: 探索枠から出た候補への「新しい絵柄を試しています」「知らない題材を試しています」バッジ
（元 discovery_tab.py:2819-2828 の文言表, 2918-2926 の表示）。確実枠・枠なし・画像失敗では出さない。"""
from types import SimpleNamespace

import pytest

from z_learning_support_20261118 import open_discovery_view, settled

FRAMES = {'1000': 'style', '1001': 'subject', '1002': 'sure'}


@pytest.fixture
async def dview(tmp_path, monkeypatch):
    view, services, service, cleanup = await open_discovery_view(tmp_path, monkeypatch, count=4, frames=FRAMES)
    try:
        yield view
    finally:
        await cleanup()


async def judge(view, choice, operation):
    view._on_loaded(SimpleNamespace(args=view._image_url))
    work_id = view.state.current_artwork.work_id
    assert view.judge(choice, dict(work_id=work_id, generation=view.state.generation, operation_id=operation))
    await settled(view)


def badge(view):
    # 判定のたびに実際の並べ替えが枠を付け直すので、試験では既知の枠表に固定して表示だけを見る。
    view._discovery_frames.clear()
    view._discovery_frames.update(FRAMES)
    view._update_frame_badge()
    return view.frame_label


async def test_style_frame_candidate_shows_new_style_badge(dview):
    view = dview
    assert view.state.current_artwork.work_id == '1000'
    assert badge(view).visible and badge(view).text == '新しい絵柄を試しています'


async def test_subject_frame_candidate_shows_unknown_subject_badge(dview):
    view = dview
    await judge(view, 'skip', 'a')
    assert view.state.current_artwork.work_id == '1001'
    assert badge(view).visible and badge(view).text == '知らない題材を試しています'


async def test_sure_frame_and_unlisted_candidates_show_no_badge(dview):
    view = dview
    await judge(view, 'skip', 'a')
    await judge(view, 'skip', 'b')
    assert view.state.current_artwork.work_id == '1002'
    assert badge(view).visible is False
    await judge(view, 'skip', 'c')
    assert view.state.current_artwork.work_id == '1003'              # 枠の記録が無い候補
    assert badge(view).visible is False


async def test_old_frame_words_are_not_shown(dview):
    view = dview
    texts = [str(getattr(e, 'text', '')) for e in view.root.descendants()]
    assert not any(t in ('確実枠', '題材探索枠', '絵柄探索枠') for t in texts)


async def test_badge_is_hidden_when_the_image_failed_and_returns_on_reload(dview):
    view = dview
    assert badge(view).visible
    view._on_failed(SimpleNamespace(args=view._image_url))
    assert badge(view).visible is False                              # 元 2918: 画像失敗の画面には添えない
    view.request_image()
    view._on_loaded(SimpleNamespace(args=view._image_url))
    assert badge(view).visible


async def test_badge_is_a_pill_with_muted_small_text(dview):
    style = dview.frame_label._style
    assert style.get('border-radius') == '999px' and style.get('font-size') == '10px'


async def test_undo_restores_badge_of_the_returned_candidate(dview):
    view = dview
    await judge(view, 'skip', 'a')
    assert badge(view).text == '知らない題材を試しています'
    view._on_undo(SimpleNamespace(args=dict(work_id=view.state.current_artwork.work_id,
                                            generation=view.state.generation, operation_id='u1')))
    await settled(view)
    assert view.state.current_artwork.work_id == '1000'
    assert badge(view).text == '新しい絵柄を試しています'
