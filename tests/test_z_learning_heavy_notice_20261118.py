"""FL-Y10: 保存・仕入れなどの重い失敗を、自動で消えず「閉じる」まで残す2行の通知枠（元 discovery_tab.py:853-896）。"""
from types import SimpleNamespace

import pytest

from z_learning_support_20261118 import open_discovery_view, settled


@pytest.fixture
async def dview(tmp_path, monkeypatch):
    view, services, service, cleanup = await open_discovery_view(tmp_path, monkeypatch, count=5)
    try:
        yield view, service
    finally:
        await cleanup()


def band_texts(view):
    return [e.text for e in view.toast.band.descendants() if getattr(e, 'text', None)]


def band_button(view, testid):
    found = [e for e in view.toast.band.descendants() if getattr(e, 'props', {}).get('data-testid') == testid]
    return found[0] if found else None


def click(button):
    for listener in button._event_listeners.values():
        if listener.type == 'click':
            return listener.handler(SimpleNamespace(args={}))
    raise AssertionError('no click handler')


async def judge(view, choice, operation):
    view._on_loaded(SimpleNamespace(args=view._image_url))
    work_id = view.state.current_artwork.work_id
    assert view.judge(choice, dict(work_id=work_id, generation=view.state.generation, operation_id=operation))
    await settled(view)
    return work_id


async def test_save_failure_shows_two_line_notice_that_stays_until_closed(dview, monkeypatch):
    view, service = dview
    view._on_loaded(SimpleNamespace(args=view._image_url))
    real = service.submit_record

    def boom(*args, **kwargs):
        raise RuntimeError('disk full')
    monkeypatch.setattr(service, 'submit_record', boom)
    work_id = view.state.current_artwork.work_id
    view.judge('like', dict(work_id=work_id, generation=view.state.generation, operation_id='f1'))
    assert view.toast.band.visible and view.toast.heavy
    texts = band_texts(view)
    assert '判定を保存できませんでした' in texts
    assert any('保存を再試行' in t for t in texts)              # 嘘にならない復帰の案内（元の「もう一度この作品が出たとき」は移植版では偽）
    assert band_button(view, 'learning-toast-close').text == '閉じる'
    # 時間が経っても、別の判定の帯でも、取り消しでも消えない
    view.clock.now += 3600
    view.toast.tick()
    assert view.toast.band.visible
    assert view.toast.show_undo('', lambda e: None, '') is False
    assert view.toast.hide() is False
    monkeypatch.setattr(service, 'submit_record', real)
    # 「閉じる」だけが消せる
    click(band_button(view, 'learning-toast-close'))
    assert view.toast.band.visible is False and view.toast.heavy is False


async def test_retry_success_keeps_save_failure_notice_until_closed(dview, monkeypatch):
    view, service = dview
    view._on_loaded(SimpleNamespace(args=view._image_url))
    real = service.submit_record
    monkeypatch.setattr(service, 'submit_record', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('x')))
    work_id = view.state.current_artwork.work_id
    view.judge('like', dict(work_id=work_id, generation=view.state.generation, operation_id='f1'))
    assert view.toast.heavy
    monkeypatch.setattr(service, 'submit_record', real)
    view.retry_save()
    await settled(view)
    assert view.toast.heavy is True                               # 直ったのに「保存できません」を残さない
    assert view.state.index == 1


async def test_heavy_notice_blocks_reason_and_judged_bands(dview, monkeypatch):
    view, service = dview
    view.toast.show_gave_up('候補を取りにいけませんでした', '再開できます', heavy=True, key='refill')
    await judge(view, 'hate', 'h1')
    assert view.toast.kind == 'heavy'                             # 「合わない」の理由帯も譲る（元 1566-1568）
    assert '候補を取りにいけませんでした' in band_texts(view)


async def test_undo_failure_shows_heavy_notice(dview, monkeypatch):
    view, service = dview
    await judge(view, 'like', 'l1')
    monkeypatch.setattr(service, 'submit_revert', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('x')))
    view._on_undo(SimpleNamespace(args=dict(work_id=view.state.current_artwork.work_id,
                                            generation=view.state.generation, operation_id='u1')))
    assert view.toast.heavy
    assert any('取り消し' in t or '取消' in t for t in band_texts(view))


async def test_refill_gave_up_shows_heavy_notice_once_and_clears_when_resumed(dview):
    view, service = dview
    view._refill.arm(view._refill_clock())
    view._refill.gave_up = True
    view._refill.failed_rounds = 3
    view._render_refill_status()
    assert view.toast.heavy and view.toast.heavy_key == 'refill'
    assert '候補を取りにいけませんでした' in band_texts(view)
    assert any('候補を取得' in t for t in band_texts(view))
    click(band_button(view, 'learning-toast-close'))
    view._render_refill_status()                                  # 同じ停止状態の間は出し直さない
    assert view.toast.band.visible is False
    view._refill.gave_up = False
    view._render_refill_status()
    view._refill.gave_up = True
    view._render_refill_status()
    assert view.toast.heavy                                       # 新しい停止なら、また出る


async def test_refill_resume_keeps_the_refill_notice_until_closed(dview):
    view, service = dview
    view._refill.arm(view._refill_clock())
    view._refill.gave_up = True
    view._render_refill_status()
    assert view.toast.heavy
    view._refill.resume(view._refill_clock())
    view._render_refill_status()
    assert view.toast.band.visible is True


async def test_initial_load_failure_shows_heavy_notice(dview, monkeypatch):
    view, service = dview
    view._initial_done = False

    async def boom(*args, **kwargs):
        raise RuntimeError('network')
    monkeypatch.setattr(service, 'initialize_once', boom)
    view._start_initial_load()
    import asyncio
    await asyncio.wait_for(view._initial_task, 5) if view._initial_task else None
    assert view.toast.heavy
    assert '初回の候補を取得できませんでした' in band_texts(view)


async def test_manual_fetch_success_keeps_the_initial_failure_notice(dview, monkeypatch):
    view, service = dview
    view.toast.show_gave_up('初回の候補を取得できませんでした', '再開できます', heavy=True, key='initial')
    monkeypatch.setattr(view, 'reload_storage', lambda **k: None)
    batch = SimpleNamespace(failed=(), artworks=(1,), nothing_and_failed=False)

    async def fetch(**kwargs):
        return batch
    monkeypatch.setattr(service, 'fetch_candidates', fetch)
    await view.fetch_discovery()
    assert view.toast.heavy is True
