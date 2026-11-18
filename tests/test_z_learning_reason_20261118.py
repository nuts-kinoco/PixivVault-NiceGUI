"""FL-I05: 「合わない」後の理由2択「何が合いませんでしたか？ 絵柄／ジャンル」（元 discovery_tab.py:1701-1765）。

学習への反映は元 database.py の `_JUDGMENT_ROUTING`（165-167）どおり:
  genre → タグの hate_count、style → 作者の hate_count、理由なし → タグの hate_count（既定「タグだけ下げる」）。
"""
import asyncio
from types import SimpleNamespace

import pytest
from nicegui import Client, background_tasks, core, ui

from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.learning_toast import REASON_SECONDS
from pixivvault_nicegui.ui.learning_view import LearningView


async def settled(view):
    async with asyncio.timeout(10):
        while view._storage_busy or not view._storage_ready or view.state.saving:
            await asyncio.sleep(.01)
        while any(not t.done() for t in view._reason_tasks):
            await asyncio.sleep(.01)


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
async def learning_view(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    services = ApplicationServices(count=6, learning_sandbox=tmp_path / 'learning')
    client = Client(ui.page('/_z_learning_reason'))
    before = set(background_tasks.running_tasks)
    with client:
        view = LearningView(services)
    view.clock = Clock()
    view.toast._clock = view.clock
    view.set_active(True)
    await settled(view)
    try:
        yield view, services
    finally:
        view.close()
        client.delete()
        owned = set(background_tasks.running_tasks) - before
        for task in owned:
            if not task.done():
                task.cancel()
        await asyncio.gather(*owned, return_exceptions=True)
        await asyncio.to_thread(services.close)


async def judge(view, choice, operation):
    view._on_loaded(SimpleNamespace(args=view._image_url))
    work_id = view.state.current_artwork.work_id
    assert view.judge(choice, dict(work_id=work_id, generation=view.state.generation, operation_id=operation))
    await settled(view)
    return work_id


def chip(view, testid):
    found = [e for e in view.toast.band.descendants() if getattr(e, 'props', {}).get('data-testid') == testid]
    return found[0] if found else None


def click(button, args=None):
    for listener in button._event_listeners.values():
        if listener.type == 'click':
            return listener.handler(SimpleNamespace(args=args or {}))
    raise AssertionError('no click handler')


async def db_read(services, sql, *args):
    learning = services.learning
    return await learning._wait(learning._submit(
        lambda: [dict(r) for r in learning._database().conn.execute(sql, args).fetchall()]))


async def counts(services, work_id):
    history = await db_read(services, 'SELECT verdict, reason, tags, author_id FROM discovery_swipe_history WHERE work_id=?', work_id)
    tags = {r['tag']: r['hate_count'] for r in await db_read(services, 'SELECT tag, hate_count FROM discovery_preference')}
    authors = {r['author_id']: r['hate_count'] for r in await db_read(services, 'SELECT author_id, hate_count FROM discovery_author_preference')}
    return history, sum(tags.values()), sum(authors.values())


async def test_hate_shows_reason_band_without_undo_chip(learning_view):
    view, _ = learning_view
    await judge(view, 'hate', 'h1')
    assert view.toast.band.visible and view.toast.kind == 'reason'
    texts = [e.text for e in view.toast.band.descendants() if getattr(e, 'text', None)]
    assert '何が合いませんでしたか？' in texts
    assert chip(view, 'learning-reason-style').text == '絵柄'
    assert chip(view, 'learning-reason-genre').text == 'ジャンル'
    assert chip(view, 'learning-toast-undo') is None          # 元 1750-1755: 理由帯に「取り消す」は置かない


async def test_reason_band_lasts_six_seconds(learning_view):
    view, _ = learning_view
    await judge(view, 'hate', 'h1')
    view.clock.now += REASON_SECONDS - 0.5
    view.toast.tick()
    assert view.toast.band.visible
    view.clock.now += 0.6
    view.toast.tick()
    assert not view.toast.band.visible


async def test_no_reason_defaults_to_tag_only(learning_view):
    view, services = learning_view
    work = await judge(view, 'hate', 'h1')
    history, tag_hate, author_hate = await counts(services, work)
    assert history[0]['reason'] is None and tag_hate > 0 and author_hate == 0


async def test_style_reason_moves_author_not_tags(learning_view):
    view, services = learning_view
    work = await judge(view, 'hate', 'h1')
    _, before_tags, _ = await counts(services, work)
    assert before_tags > 0
    click(chip(view, 'learning-reason-style'))
    await settled(view)
    history, tag_hate, author_hate = await counts(services, work)
    assert history[0]['reason'] == 'style'
    assert tag_hate == 0 and author_hate == 1
    assert view.toast.band.visible is False                    # 答えたら帯は消える


async def test_genre_reason_keeps_tag_hate_and_not_author(learning_view):
    view, services = learning_view
    work = await judge(view, 'hate', 'h1')
    _, before_tags, _ = await counts(services, work)
    click(chip(view, 'learning-reason-genre'))
    await settled(view)
    history, tag_hate, author_hate = await counts(services, work)
    assert history[0]['reason'] == 'genre' and tag_hate == before_tags and author_hate == 0


async def test_undo_after_reason_zeroes_everything_without_conflict(learning_view):
    view, services = learning_view
    work = await judge(view, 'hate', 'h1')
    click(chip(view, 'learning-reason-style'))
    await settled(view)
    assert view.state.can_undo
    view._on_undo(SimpleNamespace(args=dict(work_id=view.state.current_artwork.work_id,
                                            generation=view.state.generation, operation_id='u1')))
    await settled(view)
    assert view.state.current_artwork.work_id == work
    assert view._failed_command is None
    history, tag_hate, author_hate = await counts(services, work)
    assert history == [] and tag_hate == 0 and author_hate == 0


async def test_reason_after_undo_writes_nothing(learning_view):
    view, services = learning_view
    work = await judge(view, 'hate', 'h1')
    style = next(l.handler for l in chip(view, 'learning-reason-style')._event_listeners.values() if l.type == 'click')
    view._on_undo(SimpleNamespace(args=dict(work_id=view.state.current_artwork.work_id,
                                            generation=view.state.generation, operation_id='u1')))
    await settled(view)
    style(SimpleNamespace(args={}))                            # 取り消しと入れ違いで古いチップが押された
    await settled(view)
    history, tag_hate, author_hate = await counts(services, work)
    assert history == [] and tag_hate == 0 and author_hate == 0


async def test_reason_band_survives_next_judgment_and_targets_its_own_work(learning_view):
    view, services = learning_view
    hated = await judge(view, 'hate', 'h1')
    liked = await judge(view, 'like', 'l1')
    assert view.toast.kind == 'reason'                         # 「判定しました」帯で上書きしない（元 1951）
    click(chip(view, 'learning-reason-style'))
    await settled(view)
    history, *_ = await counts(services, hated)
    assert history[0]['reason'] == 'style'
    liked_history, *_ = await counts(services, liked)
    assert liked_history[0]['verdict'] == 'like' and liked_history[0]['reason'] is None
    # 理由のあとでも、直前の like は Undo できる（バージョン記録が壊れていない）
    view._on_undo(SimpleNamespace(args=dict(work_id=view.state.current_artwork.work_id,
                                            generation=view.state.generation, operation_id='u2')))
    await settled(view)
    assert view._failed_command is None


async def test_rehate_replaces_band_so_first_work_gets_no_reason(learning_view):
    view, services = learning_view
    first = await judge(view, 'hate', 'h1')
    await judge(view, 'hate', 'h2')
    assert view.toast.kind == 'reason'
    click(chip(view, 'learning-reason-genre'))                 # 2枚目への理由
    await settled(view)
    h1, *_ = await counts(services, first)
    assert h1[0]['reason'] is None                             # 1枚目の帯は2枚目に入れ替わっており、理由は付かない


async def test_repository_rejects_unknown_reason(learning_view):
    view, services = learning_view
    work = await judge(view, 'hate', 'h1')
    from pixivvault_nicegui.learning import Judgment
    with pytest.raises(ValueError):
        await services.learning._wait(services.learning.submit_hate_reason(
            view._session_id, Judgment(work, 'hate', 'h1'), 'bogus'))


async def test_reason_failure_shows_light_band_and_keeps_judgment(learning_view, monkeypatch):
    view, services = learning_view
    work = await judge(view, 'hate', 'h1')

    def boom(*args, **kwargs):
        raise RuntimeError('disk')
    monkeypatch.setattr(view.storage, 'submit_hate_reason', boom)
    click(chip(view, 'learning-reason-style'))
    await settled(view)
    texts = [e.text for e in view.toast.band.descendants() if getattr(e, 'text', None)]
    assert '「合わない」の理由を保存できませんでした' in texts
    history, tag_hate, _ = await counts(services, work)
    assert history[0]['verdict'] == 'hate' and history[0]['reason'] is None and tag_hate > 0


def test_reason_update_recomputes_consecutive_skips_after_history_reorders(tmp_path):
    from pixivvault_nicegui.learning import Judgment
    from pixivvault_nicegui.learning_repository import LearningRepository
    from pixivvault_nicegui.legacy.database import Database

    db = Database(tmp_path / 'learning.db', sandbox_root=tmp_path)
    try:
        repo = LearningRepository(db, {wid: {'tags': ['tag'], 'author_id': '42'} for wid in ('1', '2')})
        hated = Judgment('1', 'hate', 'old')
        repo.record(hated)
        repo.record(Judgment('2', 'skip', 'new'))
        def skips():
            return db.conn.execute("SELECT consecutive_skip_count FROM discovery_author_preference WHERE author_id='42'").fetchone()[0]
        assert skips() == 1
        assert repo.set_hate_reason(hated, 'style')
        assert skips() == 0
    finally:
        db.close()


def test_failed_reason_commit_leaves_undo_usable(tmp_path):
    import sqlite3
    from pixivvault_nicegui.learning import Judgment
    from pixivvault_nicegui.learning_repository import LearningRepository
    from pixivvault_nicegui.legacy.database import Database

    db = Database(tmp_path / 'learning.db', sandbox_root=tmp_path)
    try:
        repo = LearningRepository(db, {'1': {'tags': ['tag'], 'author_id': '42'}})
        hated = Judgment('1', 'hate', 'old')
        repo.record(hated)
        db.conn.set_authorizer(lambda op, action, *_: sqlite3.SQLITE_DENY
                               if op == sqlite3.SQLITE_TRANSACTION and action == 'COMMIT' else sqlite3.SQLITE_OK)
        with pytest.raises(sqlite3.DatabaseError):
            repo.set_hate_reason(hated, 'style')
        db.conn.set_authorizer(None)
        assert db.conn.execute("SELECT reason FROM discovery_swipe_history WHERE work_id='1'").fetchone()[0] is None
        repo.revert(hated, 'undo')
        assert db.conn.execute("SELECT COUNT(*) FROM discovery_swipe_history").fetchone()[0] == 0
    finally:
        db.conn.set_authorizer(None)
        db.close()
