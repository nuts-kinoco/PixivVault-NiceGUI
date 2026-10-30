"""CoS-only loopback PV-B fixture. No HTTP Pixiv client, Cookie, or user roots.

Run from this candidate with PYTHONPATH=src and an explicitly reserved --port.
All clicks operate the real GalleryView/ViewerController/AuthorBrowseView.
"""
import argparse
import hashlib
import json
from threading import Lock
import time
from types import MappingProxyType, SimpleNamespace
from nicegui import app, ui
from pixivvault_nicegui.models import Artwork
from pixivvault_nicegui.media import DemoImageCatalog
from pixivvault_nicegui.library_media_registry import LibraryImageRegistry
from pixivvault_nicegui.remote_media import RemoteBookmarkImages
from pixivvault_nicegui.media_routes import register_media_routes
from pixivvault_nicegui.ui.gallery import GalleryView
from pixivvault_nicegui.ui.theme import CSS


def _author_length(aid, author_count):
    # A02 opt-in includes the original small-author terminal/Retry condition.
    # Default PV-B remains 51 + 51; primary author 91 remains 100 in A02 mode.
    return 3 if author_count == 100 and aid == '92' else author_count


class FixtureReader:
    def __init__(self, catalog, *, author_count=51):
        self.catalog = catalog
        self.author_count = author_count
        self._secondary_failed_once = False
        self.calls = []
        self.fail_author = self.fail_details = False
        self._prepared_images = MappingProxyType({})
        self.image_fixture_manifest = None
        self._image_lock = Lock()
        self._image_events = []
        self._image_started = self._image_finished = self._image_failed = 0
        self._image_active = self._image_dropped = 0
        if author_count == 100:
            # Freeze only the fixture's raw source. The real registry, remote
            # provider, resize/decode and HTTP route still run for every request.
            began, began_wall = time.monotonic_ns(), time.time_ns()
            prepared, entries, total = {}, [], 0
            limit = 32 * 1024 * 1024
            for aid, start in (('91', 501), ('92', 601)):
                for number in range(start, start + _author_length(aid, author_count)):
                    wid = str(number)
                    raw = catalog.get(wid, 0, 'view').data
                    total += len(raw)
                    if total > limit:
                        raise ValueError('A02 prepared raw image limit exceeded')
                    prepared[(wid, 0)] = raw
                    entries.append({'work_id': wid, 'page_no': 0, 'variant': 'view',
                                    'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
            self._prepared_images = MappingProxyType(prepared)
            self.image_fixture_manifest = {
                'schema': 'a02-prepared-raw-v1', 'count': len(entries),
                'bytes': total, 'limit_bytes': limit, 'entries': entries,
                'aggregate_sha256': hashlib.sha256(json.dumps(
                    entries, sort_keys=True, separators=(',', ':')).encode('ascii')).hexdigest(),
                'preparation_started_wall_ns': began_wall,
                'preparation_finished_wall_ns': time.time_ns(),
                'preparation_elapsed_ns': time.monotonic_ns() - began,
                'interpretation': 'functional fixture preparation; not cold performance',
            }

    def require_identity(self):
        return '7'

    def get_user_works(self, aid, *, lane):
        if aid not in ('91', '92') or lane != 'interactive':
            raise ValueError('unexpected fixture author request')
        self.calls.append(('author', aid, lane))
        if self.fail_author:
            raise OSError('synthetic author failure')
        if self.author_count == 100 and aid == '92':
            first = not self._secondary_failed_once
            self._secondary_failed_once = True
            # The fixture reader runs off the UI thread. Keep the real loading
            # status observable on initial failure and the explicit Retry.
            time.sleep(0.75)
            if first:
                raise OSError('synthetic first secondary-author failure')
        start = 501 if aid == '91' else 601
        return [dict(id=str(i), user_id=aid, title=f'合成作品 {i}', user_name=f'合成作者 {aid}',
                     tags=['合成タグ'], thumbnail_url=f'https://i.pximg.net/offline/{i}/0.png',
                     page_count=2, illust_type=0, x_restrict=0) for i in range(start, start + _author_length(aid, self.author_count))]

    def get_artwork_metadata_raw(self, wid, aid):
        if aid not in ('91', '92') or not ((501 <= int(wid) < 501 + _author_length(aid, self.author_count) and aid == '91') or (601 <= int(wid) < 601 + _author_length(aid, self.author_count) and aid == '92')):
            raise ValueError('unexpected fixture detail request')
        self.calls.append(('details', wid, aid))
        if self.fail_details:
            raise OSError('synthetic detail failure')
        return dict(id=wid, userId=aid, description=f'<p>合成説明 {wid}</p><br>2行目',
                    tags={'tags': [{'tag': '合成タグ'}, {'tag': '<text-only>'}]},
                    createDate='2026-10-28', bookmarkCount=0, viewCount=12, width=800, height=600)

    def get_artwork_pages(self, wid):
        self.calls.append(('pages', wid))
        return tuple(f'https://i.pximg.net/offline/{wid}/{i}.png' for i in range(2))

    def get_image_bytes(self, url):
        # URL strings are fixture identities only. Never open a URL or file.
        prefix = 'https://i.pximg.net/offline/'
        if not url.startswith(prefix) or not url.endswith('.png'):
            raise ValueError('non-fixture image rejected')
        wid, page = url[len(prefix):-4].split('/')
        number = int(page)
        if self.author_count != 100:
            return self.catalog.get(wid, number, 'view').data
        source = 'prepared' if (wid, number) in self._prepared_images else 'catalog-fallback'
        with self._image_lock:
            self._image_started += 1
            serial = self._image_started
            self._image_active += 1
            self._append_image_event({
                'event': 'begin', 'serial': serial, 'work_id': wid, 'page_no': number,
                'source': source, 'wall_ns': time.time_ns(), 'monotonic_ns': time.monotonic_ns()})
        began, count, digest, error = time.monotonic_ns(), None, None, None
        try:
            raw = (self._prepared_images[(wid, number)] if source == 'prepared'
                   else self.catalog.get(wid, number, 'view').data)
            count, digest = len(raw), hashlib.sha256(raw).hexdigest()
            return raw
        except BaseException as exc:
            error = type(exc).__name__
            raise
        finally:
            with self._image_lock:
                self._image_finished += 1
                self._image_failed += int(error is not None)
                self._image_active -= 1
                self._append_image_event({
                    'event': 'end', 'serial': serial, 'work_id': wid, 'page_no': number,
                    'source': source, 'wall_ns': time.time_ns(), 'monotonic_ns': time.monotonic_ns(),
                    'elapsed_ns': time.monotonic_ns() - began, 'bytes': count,
                    'sha256': digest, 'error_class': error})

    def _append_image_event(self, event):
        # Caller holds only this small observation lock, never a catalog lock.
        if len(self._image_events) < 512:
            self._image_events.append(event)
        else:
            self._image_dropped += 1

    def image_observation(self):
        with self._image_lock:
            return {'event_limit': 512, 'dropped': self._image_dropped,
                    'started': self._image_started, 'finished': self._image_finished,
                    'failed': self._image_failed, 'active': self._image_active,
                    'events': [dict(event) for event in self._image_events]}


def main(*, acceptance_hooks=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--author-count', type=int, choices=(51, 100), default=51)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535 or args.port == 8089:
        parser.error('use a dedicated free unprivileged port other than 8089')
    all_art = tuple(Artwork(str(i), f'合成作品 {i}', f'合成作者 {aid}', author_id=aid, page_count=2)
                    for aid, start in [('91', 501), ('92', 601)] for i in range(start, start + _author_length(aid, args.author_count)))
    catalog = DemoImageCatalog(all_art)
    registry = LibraryImageRegistry(catalog)
    reader = FixtureReader(catalog, author_count=args.author_count)
    services = SimpleNamespace(artworks=(all_art[0], all_art[args.author_count]), images=registry, live_client=reader,
                               managed_downloads=None,
                               acquire_ranking_images=lambda records: registry.acquire_catalog(RemoteBookmarkImages(reader, records)))
    register_media_routes(app, registry)

    @app.get('/_fixture/state')
    def state():
        return {'calls': list(reader.calls), 'fail_author': reader.fail_author, 'fail_details': reader.fail_details}

    @ui.page('/')
    def page():
        ui.add_css(CSS)
        dark = ui.dark_mode(True)
        with ui.column().classes('pv-shell'):
            with ui.row().classes('pv-header'):
                ui.label('合成 PV-B 検証').classes('pv-title')
                ui.button('明暗を切替', on_click=dark.toggle)
                ui.checkbox('合成作者取得失敗', on_change=lambda e: setattr(reader, 'fail_author', e.value))
                ui.checkbox('合成詳細取得失敗', on_change=lambda e: setattr(reader, 'fail_details', e.value))
            with ui.element('main').classes('pv-content'):
                GalleryView(services)

    if acceptance_hooks is None:
        app.on_shutdown(registry.close)
    else:
        acceptance_hooks(app, registry, reader)
    ui.run(host='127.0.0.1', port=args.port, show=False, reload=False, title='PV-B synthetic fixture')


if __name__ == '__main__':
    main()
