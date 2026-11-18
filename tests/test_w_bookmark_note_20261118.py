import pytest

from test_w_dl_screen_20261118 import finish, make, texts


@pytest.mark.asyncio
async def test_bookmark_pane_explains_excluded_types(tmp_path):
    services, client, view = make(tmp_path)
    try:
        view.set_mode('bookmark')
        assert '小説・うごイラのブックマークは対象外です' in texts(client)
    finally:
        finish(services, client)
