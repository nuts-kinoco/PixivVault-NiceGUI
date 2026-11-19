"""Launch the copied Flet app with synthetic candidate and image providers."""

import json
import base64
import os
import runpy
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace


def main():
    from tools.check_library_calendar_slider_owned import deny_external_connections

    network = deny_external_connections()
    base = Path.cwd()
    if base.drive.upper() != 'S:' or os.environ.get('PV_NO_BROWSER_COOKIES') != '1':
        raise RuntimeError('isolated S: working directory and disabled browser Cookie extraction required')
    if (base / 'cookies.txt').exists():
        raise RuntimeError('Cookie file in Flet fixture')
    sys.path.insert(0, str(base))
    import flet
    import webbrowser
    import discovery_engine
    from pixiv_client import PixivClient

    # The checked-in original_run patch selects WEB_BROWSER. Suppress the
    # automatic browser launch and serve the bundled assets without CDN access.
    original_run = flet.run
    flet.run = lambda *args, **kwargs: original_run(*args, **kwargs, no_cdn=True, host='127.0.0.1')
    webbrowser.open = lambda *_args, **_kwargs: False

    records = json.loads((base / 'learning.json').read_text(encoding='utf-8'))
    with sqlite3.connect(os.environ['PIXIVVAULT_DB_PATH']) as db:
        image_root = base.parent / 'library' / 'Images'
        local_paths = {}
        pages = {}
        for wid, title, count, uid, author in db.execute(
            'SELECT w.work_id,w.title,w.page_count,w.user_id,u.name FROM works w '
            'JOIN following_users u ON u.user_id=w.user_id'
        ):
            names = [f'{wid}-{page}.png' for page in range(1, count + 1)]
            local_paths.update((name, image_root / f'{author}({uid})' / f'{title}({wid})_{page}.png')
                               for page, name in enumerate(names, 1))
            pages[wid] = [f'https://synthetic.invalid/{name}' for name in names]
    def get_next_batch(*, n, exclude_ids, **_kwargs):
        return SimpleNamespace(items=[row.copy() for row in records if row['work_id'] not in exclude_ids][:n],
                               nothing_and_failed=False)

    def image_bytes(_self, url, *_args, **_kwargs):
        name = str(url).rsplit('/', 1)[-1]
        return local_paths[name].read_bytes() if name in local_paths else None

    def image_uri(client, url, *_args, **_kwargs):
        data = image_bytes(client, url)
        return 'data:image/png;base64,' + base64.b64encode(data).decode('ascii') if data else None

    local_paths.update((f"{record['work_id']}.png", base / 'learning' / f"{record['work_id']}.png")
                       for record in records)
    discovery_engine.get_next_batch = get_next_batch
    PixivClient.get_discovery_detail = lambda _self, wid, **_kwargs: {
        'image_url': f'https://synthetic.invalid/{wid}.png', 'description': ''}
    # Local bytes replace only the external transport of the selected fixture images.
    PixivClient.download_thumbnail_to_memory = image_bytes
    PixivClient.download_thumbnail_as_data_uri = image_uri
    PixivClient.get_image_urls = lambda _self, wid, **_kwargs: pages.get(str(wid), [])
    (base / 'cache' / 'thumbnails').mkdir(parents=True, exist_ok=True)

    def report_denials():
        (base / 'network-count.json').write_text(json.dumps(network), encoding='utf-8')

    from threading import Thread
    from time import sleep
    def monitor():
        while True:
            report_denials()
            sleep(0.5)

    Thread(target=monitor, daemon=True).start()
    runpy.run_path(str(base / 'main.py'), run_name='__main__')


if __name__ == '__main__':
    main()
