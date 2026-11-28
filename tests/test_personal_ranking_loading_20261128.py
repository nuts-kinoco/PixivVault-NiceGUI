import asyncio
import threading

import pytest

from test_y_ranking_personal_20261118 import build, open_view


@pytest.mark.asyncio
@pytest.mark.parametrize('existing_page', [False, True])
async def test_loading_notice_survives_context_refresh_while_search_is_pending(monkeypatch, existing_page):
    services, vault, client = await build(monkeypatch, f'pending_{existing_page}')
    entered, release = threading.Event(), threading.Event()
    original_search = vault.search
    task = None
    try:
        with client:
            view = await open_view(services, vault)
            view.open_with_tag('tag')
            if existing_page:
                await view.load()
                assert view.page is not None

            def held_search(*args, **kwargs):
                entered.set()
                if not release.wait(10):
                    raise TimeoutError('synthetic search was not released')
                return original_search(*args, **kwargs)

            vault.search = held_search
            task = asyncio.create_task(view.load())
            assert await asyncio.to_thread(entered.wait, 5)
            assert view.busy and not view.fetch_button.enabled
            assert view.notice.text == '取得中…'
            await view._start_context_refresh()
            assert view.busy and not view.fetch_button.enabled
            assert view.notice.text == '取得中…'
            release.set()
            await task
            assert not view.busy and view.fetch_button.enabled
            assert '件表示' in view.notice.text
            view.close()
    finally:
        release.set()
        if task is not None:
            await task
        client.delete()
        services.close()
