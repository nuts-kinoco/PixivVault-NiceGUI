from email.utils import formatdate

from test_ag_z04_20261119 import setup, reader, fetch
from pixivvault_nicegui.pixiv_read_client import _ReadResponse


def test_capacity_skips_later_extensions_without_deleting_credentials(tmp_path, monkeypatch):
    import pixivvault_nicegui.extension_cookie_store as module
    monkeypatch.setattr(module, 'MAX_RETAINED_COOKIE_GENERATIONS', 3, raising=False)
    expiry, cookie, profile, store, coordinator = setup(tmp_path)
    client, sent = reader(cookie, coordinator, [])
    try:
        for index in range(6):
            header = 'PHPSESSID=777_old; Domain=.pixiv.net; Path=/; Secure; Expires=' + formatdate(expiry + 3600 + index, usegmt=True)
            client._transport = lambda *args: _ReadResponse(b'{"error":false,"body":{}}', (header,))
            fetch(client)
        folders = list((store.root / 'generations').iterdir())
        assert len(folders) == 3
        assert all((folder / 'cookies.txt').is_file() for folder in folders)
        assert profile.snapshot().cookie_file.is_file()
    finally:
        client.close(); coordinator.close(); store.close(); profile.close()
