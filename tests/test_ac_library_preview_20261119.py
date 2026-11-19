from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from io import BytesIO
from pathlib import Path

import pytest
from nicegui import Client, ui
from PIL import Image

from pixivvault_nicegui.composite_library import CompositeLocalCatalog, compose_library
from pixivvault_nicegui.library_models import LibrarySnapshot
from pixivvault_nicegui.local_media_adapter import ReadonlyLocalMediaCatalog, MediaMissing, MediaUnavailable
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.library_view import LibraryView
from test_ac_library_cover_20261119 import Remote, png, rgb, snap, source


def walk(node):
    yield node
    for child in node.default_slot.children:
        yield from walk(child)


def card_src(card):
    return next(node.props['src'] for node in walk(card) if node.props.get('src'))


@pytest.mark.asyncio
async def test_legacy_thumbnail_setting_off_does_not_block_existing_bookmark_image(tmp_path, monkeypatch):
    import asyncio
    from nicegui import core
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    import sqlite3
    import json
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from pixivvault_nicegui.media_routes import register_media_routes
    from test_managed_download_services import FakeSource
    legacy_url='https://i.pximg.net/synthetic/legacy-101.png'
    bookmark_url='https://i.pximg.net/synthetic/bookmark-101.png'
    class Reader(FakeSource):
        def get_image_bytes(self,url):
            self.calls.append(url)
            return png('blue' if url==bookmark_url else 'red')
        def get_bookmarked_works(self, identity, rest_type):
            return ()
        def get_bookmarked_novels(self, identity, rest_type):
            if rest_type == 'show':
                return ({'id': '101', 'title': 'Bookmark', 'user_id': '12',
                         'user_name': 'Author', 'tags': [], 'thumb_url': bookmark_url},)
            return ()
    img=tmp_path/'images'
    source(img)
    db_dir=tmp_path/'readonly'
    db_dir.mkdir()
    db=db_dir/'old.sqlite'
    with sqlite3.connect(db) as handle:
        handle.executescript('CREATE TABLE works (work_id TEXT,title TEXT,page_count INTEGER,tags TEXT,user_id TEXT,is_deleted INTEGER,content_type TEXT,work_meta TEXT);'
            'CREATE TABLE following_users (user_id TEXT,name TEXT);'
            'CREATE TABLE bookmark_remote_cache(work_id TEXT,is_novel INTEGER,title TEXT,user_id TEXT,'
            'user_name TEXT,tags TEXT,create_date TEXT,thumb_url TEXT,rest_type TEXT,x_restrict INTEGER);')
        handle.execute('INSERT INTO works VALUES (?,?,1,?,?,0,?,?)',
            ('101','Novel','[]','12','novel',json.dumps({'url':legacy_url})))
        handle.execute('INSERT INTO bookmark_remote_cache VALUES (?,?,?,?,?,?,?,?,?,?)',
            ('101',1,'Bookmark','12','Author','[]','',bookmark_url,'show',0))
    reader=Reader()
    svc=ApplicationServices(live_client=reader,library_image_root=img,library_readonly_db=db,
        preferences_root=tmp_path/'prefs',legacy_copy_root=_legacy_copy_root(tmp_path))
    client=Client(ui.page('/_sol_cover_bookmark'))
    app=FastAPI()
    register_media_routes(app,svc.images)
    view=None
    try:
        assert svc.preferences.get_setting('legacy_novel_thumbnail_auto')=='1'
        assert 'legacy_novel_thumbnail_auto' in svc.preference_consumers()
        await svc.refresh_bookmark_cache()
        assert svc.bookmark_remote_snapshot()[0].work_id=='novel:101'
        reader.calls.clear()
        svc.preferences.set_setting('legacy_novel_thumbnail_auto','0')
        svc.preferences.set_setting('bookmark_auto_refresh','0')
        with client, TestClient(app) as http:
            view=LibraryView(svc)
            author_url=card_src(view.author_cards['12'])
            author=http.get(author_url)
            assert author.status_code==503 and reader.calls==[]
            view.show_subview('bookmarks')
            bookmark=http.get(card_src(view.bookmarks.cards['novel:101']))
            print('private setting author/bookmark',author.status_code,bookmark.status_code)
            assert bookmark.status_code==200
            assert reader.calls==[bookmark_url]
    finally:
        if view is not None:view.close()
        client.delete()
        svc.close()


def test_quarantine_blocks_remote_thumbnail_response_after_inflight_exclusion(tmp_path):
    from threading import Event
    from types import SimpleNamespace
    from pixivvault_nicegui.legacy_quarantine import LegacyQuarantineCatalog
    from pixivvault_nicegui.library_media_registry import LibraryImageRegistry
    from pixivvault_nicegui.remote_media import LegacyNovelPreviewImages
    from test_ac_library_cover_20261119 import Demo
    source(tmp_path)
    snap_url=LibrarySnapshot((replace(snap().works[0],has_thumbnail_url=True),))
    state={'exclusions':{}}
    store=SimpleNamespace(catalog=SimpleNamespace(root=tmp_path),state=lambda **kw:state,
                         require_available=lambda *args:None)
    leaf=LegacyQuarantineCatalog(store,snap_url,snap_url,{'novel:101':(0,)},state)
    comp=compose_library(snap_url,LibrarySnapshot(()),(),(),managed_source_pages={},
                         managed_original_page_counts={})
    entered,release=Event(),Event()
    class Reader:
        def get_image_bytes(self,url):
            entered.set()
            assert release.wait(4)
            return png('blue')
    registry=LibraryImageRegistry(Demo())
    registry.publish(CompositeLocalCatalog(comp,legacy_catalog=leaf),None,
        LegacyNovelPreviewImages(Reader(),{'novel:101':'https://i.pximg.net/synthetic/101.png'},lambda:True))
    pin=registry.acquire()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            job=pool.submit(pin.get,'novel:101',0,'thumb')
            assert entered.wait(4)
            state['exclusions']['novel:101:1']=True
            release.set()
            with pytest.raises(MediaMissing):
                job.result(timeout=4)
    finally:
        release.set()
        pin.close()
        registry.close()


def test_thumbnail_setting_off_is_checked_after_waiting_for_decode_slot(tmp_path):
    from threading import Event,Lock
    import time
    from pixivvault_nicegui.preferences import PreferencesStore
    from pixivvault_nicegui.remote_media import LegacyNovelPreviewImages
    prefs=PreferencesStore(tmp_path/'settings.sqlite')
    url={f'novel:{i}':f'https://i.pximg.net/synthetic/{i}.png' for i in (101,102,103)}
    lock,release,first=Lock(),Event(),Event()
    started=[]
    class Reader:
        def get_image_bytes(self,address):
            with lock:
                started.append(address)
                if len(started)==2:first.set()
            assert release.wait(4)
            return png('blue')
    catalog=LegacyNovelPreviewImages(Reader(),url,lambda:prefs.get_setting('legacy_novel_thumbnail_auto')=='1')
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            try:
                jobs=[pool.submit(catalog.get,f'novel:{i}',0,'thumb') for i in (101,102)]
                assert first.wait(4)
                queued=pool.submit(catalog.get,'novel:103',0,'thumb')
                deadline=time.monotonic()+4
                while len(catalog._flights)!=3 and time.monotonic()<deadline:
                    time.sleep(.005)
                assert len(catalog._flights)==3
                prefs.set_setting('legacy_novel_thumbnail_auto','0')
            finally:release.set()
            for job in jobs:job.result(timeout=4)
            with pytest.raises(RuntimeError):
                queued.result(timeout=4)
            assert len(started)==2
    finally:
        release.set()
        catalog.close()
        prefs.close()


def _legacy_copy_root(tmp_path):
    root = tmp_path.parent / (tmp_path.name + '-legacy-copies')
    root.mkdir(exist_ok=True)
    return root
