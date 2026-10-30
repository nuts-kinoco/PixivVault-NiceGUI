"""CoS regressions: preserve expansion/acquisition and long internal pointer paths."""
import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
import pytest
from pixivvault_nicegui.ugoira_practical import native_io_path, os_replace


def test_internal_pointer_rename_retains_bytes_beyond_windows_max_path(tmp_path):
    parent=tmp_path
    while len(str(parent))<270:
        parent=parent/'fixture-segment'
    parent=native_io_path(parent)
    parent.mkdir(parents=True)
    source=parent/'pointer.json';target=parent/'source-pointer.json'
    source.write_bytes(b'fixture pointer proof')
    os_replace(source,target)
    assert not source.exists()
    assert target.read_bytes()==b'fixture pointer proof'
    assert native_io_path(target)==target


@pytest.mark.asyncio
async def test_recommendation_ready_is_published_only_after_render_finishes():
    from nicegui import Client,ui
    from pixivvault_nicegui.ui.recommend_view import RecommendView
    client=Client(ui.page('/_p3_gui_fix'))
    gate=asyncio.Event();seen=[]
    try:
        with client:view=RecommendView(SimpleNamespace(recommendations=object()))
        view.active=True
        view.render=lambda:seen.append(view.root.props['data-busy'])
        async def mutate():
            # _change uses a worker callback; use thread events for this boundary.
            pass
        from threading import Event
        entered,release=Event(),Event()
        def callback():
            entered.set();release.wait(5)
            return {'revision':1}
        task=asyncio.create_task(view._change(callback))
        try:
            await asyncio.to_thread(entered.wait,5)
            assert view.root.props['data-busy']=='true'
        finally:release.set()
        await task
        assert seen==['true']
        assert view.root.props['data-busy']=='false'
    finally:client.delete()
