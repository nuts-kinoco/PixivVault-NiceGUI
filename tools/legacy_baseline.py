"""Prepare an isolated copy of the original Flet ranking UI and optionally launch it.

No original application's Python file is imported. --launch requires a separate
Flet environment and must only be used after --prepare-only review.
"""
from __future__ import annotations

import ast
import base64
import hashlib
import importlib
import json
import argparse
import os
from pathlib import Path
import shutil
import socket
import sys
import threading
import types
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'artifacts' / 'legacy-baseline'
SOURCES = ('ranking_tab.py', 'artbook_viewer.py', 'grid_card_size.py',
           'grid_card_size_ui.py', 'filter_panel.py', 'discovery_ranking.py',
           'ui_dispatch.py')
BLOCKED = frozenset({'main', 'gui', 'core', 'pixiv_client', 'database', 'nana7mi_vault', 'server'})
PERMITTED_EXTERNAL = frozenset({'flet', 'requests', 'PIL'})


def safe_output(path: Path) -> Path:
    """Only allow baseline output directories below this worktree's artifacts."""
    base = (ROOT / 'artifacts').resolve()
    target = path.resolve()
    if target == base or base not in target.parents:
        raise ValueError('output must be a child directory of repo/artifacts')
    if any(part in {'.venv', 'source', 'fixture'} for part in target.relative_to(base).parts):
        raise ValueError('output cannot target reserved source/venv directories')
    if (target / 'Scripts' / 'python.exe').exists() or (target / 'pyvenv.cfg').exists():
        raise ValueError('output cannot overwrite a virtual environment')
    return target


def verify_pngs(fixture: list[dict], folder: Path) -> None:
    for row in fixture:
        for entry in (row['thumbnail'], *row['pages']):
            name = entry['file']
            if Path(name).name != name or not name.endswith('.png'):
                raise ValueError('invalid fixture PNG path')
            if sha((folder / name).read_bytes()) != entry['sha256']:
                raise RuntimeError(f'fixture PNG hash mismatch: {name}')


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def imports_in(source: bytes) -> set[str]:
    tree = ast.parse(source)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split('.')[0])
    return names


def prepare(source: Path, output: Path = OUTPUT) -> dict:
    output = safe_output(output)
    source = source.resolve(strict=True)
    if not source.is_dir():
        raise ValueError('source must be a directory')
    existing_fixture = output / 'fixture.json'
    if existing_fixture.is_file():
        previous = json.loads(existing_fixture.read_text(encoding='utf-8'))
        if previous and any('legacy_id' not in row for row in previous):
            raise RuntimeError('existing v1 fixture is immutable; choose --output artifacts/legacy-baseline-v2')
    source_root = output / 'source'
    if source_root.exists():
        for name in SOURCES:
            previous = source_root / name
            if previous.exists() and sha(previous.read_bytes()) != sha((source / name).read_bytes()):
                raise RuntimeError(f'existing baseline differs: {name}; manually inspect before replacing')
    data: dict[str, bytes] = {}
    for name in SOURCES:
        path = source / name
        if not path.is_file() or path.is_symlink():
            raise ValueError(f'missing/unsafe source: {name}')
        data[name] = path.read_bytes()
    local = {Path(n).stem for n in SOURCES}
    builtin = set(sys.stdlib_module_names)
    for name, value in data.items():
        unknown = imports_in(value) - local - builtin - BLOCKED - PERMITTED_EXTERNAL
        if unknown:
            raise ValueError(f'unrecognized imports in {name}: {sorted(unknown)}')
    source_root.mkdir(parents=True, exist_ok=True)
    for name, value in data.items():
        destination = source_root / name
        if destination.exists() and destination.read_bytes() == value:
            continue
        destination.write_bytes(value)
    # Reuse the same generator as the NiceGUI demo (not a substitute image).
    sys.path.insert(0, str(ROOT / 'src'))
    from pixivvault_nicegui.fixtures import generate_demo_artworks
    from pixivvault_nicegui.media import DemoImageCatalog
    artworks = generate_demo_artworks(120)
    catalog = DemoImageCatalog(artworks, max_bytes=8 * 1024 * 1024)
    fixture_dir = output / 'fixture'
    fixture_dir.mkdir(parents=True, exist_ok=True)
    fixture_rows = []
    for index, art in enumerate(artworks, start=1):
        pages = []
        for page in range(art.page_count):
            blob = catalog.get(art.work_id, page, 'view')
            name = f'{art.work_id}-{page}-view.png'
            (fixture_dir / name).write_bytes(blob.data)
            pages.append({'file': name, 'sha256': sha(blob.data)})
        thumbnail = catalog.get(art.work_id, 0, 'thumb')
        thumb_name = f'{art.work_id}-thumb.png'
        (fixture_dir / thumb_name).write_bytes(thumbnail.data)
        fixture_rows.append({'work_id': art.work_id, 'legacy_id': index, 'title': art.title,
                             'author': art.author, 'rank': index,
                             'page_count': art.page_count,
                             'thumbnail': {'file': thumb_name, 'sha256': sha(thumbnail.data)},
                             'pages': pages})
    fixture_bytes = json.dumps(fixture_rows, ensure_ascii=False, sort_keys=True).encode()
    (output / 'fixture.json').write_bytes(fixture_bytes)
    manifest = {'source_files': {name: sha(content) for name, content in data.items()},
                'fixture_sha256': sha(fixture_bytes), 'artworks': len(fixture_rows),
                'source_isolation': True}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return manifest


class FakeDB:
    def __init__(self):
        self.settings = {}
    def get_setting(self, key, default=None):
        return self.settings.get(key, default)
    def set_setting(self, key, value):
        self.settings[key] = value
    def get_hidden_work_ids(self):
        return set()
    def get_hidden_works(self):
        return []
    def get_discovery_preferences(self):
        return {}
    def get_discovery_author_preferences(self):
        return {}
    def get_following_users(self):
        return []
    def get_work(self, _id):
        return None
    def is_following_user(self, _id):
        return False
    def __getattr__(self, name):
        raise RuntimeError(f'FakeDB unexpected access: {name}')


class FakeClient:
    rows = ()
    fixtures = Path()
    id_map = {}
    _image_cache = {}
    _image_cache_lock = threading.RLock()
    def __init__(self, *a, **kw):
        pass
    def get_ranking(self, *, mode, content, page, date=None, lane=None):
        if content != 'illust' or mode != 'daily':
            raise RuntimeError(f'unsupported synthetic ranking: {mode}/{content}')
        return list(self.rows[(page-1)*50:page*50])
    def get_image_urls(self, work_id, size='regular'):
        if size != 'regular':
            raise RuntimeError('unsupported image size')
        try:
            legacy_id = int(work_id)
        except (TypeError, ValueError) as exc:
            raise RuntimeError('legacy numeric work ID required') from exc
        row = next((r for r in self.rows if r['illust_id'] == legacy_id), None)
        if row is None:
            raise RuntimeError('unknown work_id')
        shared_id = self.id_map.get(legacy_id)
        if shared_id is None:
            raise RuntimeError('unknown legacy ID')
        return [f'fixture:{shared_id}:{i}:view' for i in range(row['page_count'])]
    def download_thumbnail_as_data_uri(self, url):
        if not isinstance(url, str) or not url.startswith('fixture:'):
            raise RuntimeError('external image URL prohibited')
        parts = url.split(':')
        if len(parts) != 4:
            raise RuntimeError('invalid fixture image URL')
        _, work_id, page, variant = parts
        allowed = set(self.id_map.values())
        if work_id not in allowed or not page.isascii() or not page.isdecimal() or (len(page) > 1 and page.startswith('0')):
            raise RuntimeError('invalid fixture image URL')
        legacy_id = next((key for key, value in self.id_map.items() if value == work_id), None)
        row = next((r for r in self.rows if r['illust_id'] == legacy_id), None)
        if row is None or variant not in {'thumb', 'view'} or int(page) >= row['page_count'] or (variant == 'thumb' and page != '0'):
            raise RuntimeError('invalid fixture image URL')
        name = f'{work_id}-thumb.png' if variant == 'thumb' else f'{work_id}-{page}-view.png'
        root = self.fixtures.resolve()
        with self._image_cache_lock:
            key = (str(root), url)
            if key not in self._image_cache:
                path = root / name
                if not path.is_file():
                    raise RuntimeError('fixture image missing')
                self._image_cache[key] = 'data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode('ascii')
            return self._image_cache[key]
    def __getattr__(self, name):
        raise RuntimeError(f'FakeClient unexpected API: {name}')


def warm_fixture_images(client: FakeClient) -> int:
    """Preload all three ranking pages and all image pages for warm calibration."""
    total = 0
    for page in (1, 2, 3):
        for row in client.get_ranking(mode='daily', content='illust', page=page):
            client.download_thumbnail_as_data_uri(row['url'])
            total += 1
            for url in client.get_image_urls(row['illust_id']):
                client.download_thumbnail_as_data_uri(url)
                total += 1
    return total


def marshal_with_dispatcher(dispatcher, async_fn, fallback, *, key=None):
    """Preserve gui.py's async task and only use fallback when dispatcher requires it."""
    if key is not None:
        dispatcher.post(async_fn, fallback=fallback, key=key)
        return
    dispatcher.post(async_fn, fallback=fallback)


def isolate_imports(copy: Path, fixture_dir: Path, fixture: list[dict], original: Path) -> None:
    # An audit hook protects the original tree even from unanticipated deep imports.
    original = original.resolve()
    def audit(event, args):
        if event == 'open' and args and isinstance(args[0], (str, bytes, os.PathLike)):
            requested = Path(args[0]).resolve()
            if requested == original or original in requested.parents:
                raise PermissionError(f'original repository access blocked: {requested.name}')
    sys.addaudithook(audit)
    def blocked_browser(*_args, **_kwargs):
        raise PermissionError('external browser navigation blocked')
    webbrowser.open = blocked_browser
    webbrowser.open_new = blocked_browser
    webbrowser.open_new_tab = blocked_browser
    socket_create = socket.create_connection
    original_resolve = socket.getaddrinfo
    def only_loopback_dns(host, *args, **kwargs):
        if host not in {None, 'localhost', '127.0.0.1', '::1'}:
            raise PermissionError('external DNS resolution blocked')
        return original_resolve(host, *args, **kwargs)
    socket.getaddrinfo = only_loopback_dns
    def only_loopback(address, *args, **kwargs):
        if address[0] not in {'localhost', '127.0.0.1', '::1'}:
            raise PermissionError('external socket connection blocked')
        return socket_create(address, *args, **kwargs)
    socket.create_connection = only_loopback
    original_connect = socket.socket.connect
    def socket_connect(self, address):
        if isinstance(address, tuple) and address[0] not in {'localhost', '127.0.0.1', '::1'}:
            raise PermissionError('external socket connection blocked')
        return original_connect(self, address)
    socket.socket.connect = socket_connect
    FakeClient.fixtures = fixture_dir
    FakeClient.id_map = {r.get('legacy_id', r['rank']): r['work_id'] for r in fixture}
    FakeClient.rows = tuple({
        'illust_id': r.get('legacy_id', r['rank']), 'rank': r['rank'], 'title': r['title'],
        'user_name': r['author'], 'user_id': f"author-{r['rank'] % 12}",
        'url': f"fixture:{r['work_id']}:0:thumb", 'tags': [],
        'is_masked': False, 'page_count': r['page_count'],
    } for r in fixture)
    warm_count = warm_fixture_images(FakeClient())
    print(json.dumps({'warm_cache_images': warm_count, 'cache_root': str(fixture_dir.resolve())}), flush=True)
    safe_modules = {
        'core': {'sanitize_filename': lambda s: str(s), 'illust_save_filename': lambda *a, **kw: 'fixture.png'},
        'database': {'normalize_tag': lambda s: str(s).strip()},
        'pixiv_client': {'PixivClient': FakeClient, 'extract_tag_names': lambda tags: tags},
        'server': {'enqueue_gui_job': lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('queue disabled'))},
        'nana7mi_vault': {'session': types.SimpleNamespace(is_open=lambda: False,
                         get_active_client_mode=lambda: 'none', get=lambda *_: None,
                         record_token_success=lambda: None, record_token_failure=lambda: None)},
    }
    for name, attrs in safe_modules.items():
        if name in sys.modules:
            raise RuntimeError(f'unsafe module already loaded: {name}')
        module = types.ModuleType(name)
        module.__dict__.update(attrs)
        sys.modules[name] = module
    sys.path.insert(0, str(copy))
    if str(original) in sys.path:
        raise RuntimeError('original repository on import path')


def launch(source: Path, output: Path, width: int, height: int) -> None:
    output = safe_output(output)
    manifest = json.loads((output / 'manifest.json').read_text(encoding='utf-8'))
    copied = output / 'source'
    for name, digest in manifest['source_files'].items():
        if name not in SOURCES or sha((copied / name).read_bytes()) != digest:
            raise RuntimeError(f'source manifest mismatch: {name}')
    raw = (output / 'fixture.json').read_bytes()
    if sha(raw) != manifest['fixture_sha256']:
        raise RuntimeError('fixture manifest mismatch')
    fixture = json.loads(raw)
    verify_pngs(fixture, output / 'fixture')
    if any('legacy_id' not in row for row in fixture):
        raise RuntimeError('old fixture manifest: run --prepare-only --output ...-v2')
    os.chdir(output)
    print(json.dumps({'baseline_pid': os.getpid(), 'output': str(output),
                      'viewport': [width, height], 'fixture_sha256': manifest['fixture_sha256']}), flush=True)
    isolate_imports(copied, output / 'fixture', fixture, source)
    import flet as ft
    ranking = importlib.import_module('ranking_tab')
    ui_dispatch = importlib.import_module('ui_dispatch')
    class DT:
        BG_VAULT = '#0b0b10'; BG_SURFACE = '#15161c'; BG_CARD = '#1e202d'
        BORDER_VAULT = '#2a2b38'; PIXIV_BLUE = '#0096FA'
        PIXIV_BLUE_HOVER = '#0080d6'
        PIXIV_BLUE_GLOW = ft.Colors.with_opacity(0.25, '#0096FA')
        GLASS_BG = ft.Colors.with_opacity(0.94, '#15161c')
        TEXT_MAIN = '#e8e8ef'; TEXT_MUTED = '#8a8a99'; TEXT_ELSEWHERE = '#b9b9c4'
        SP_XS = 4; SP_SM = 8; SP_MD = 16; SP_LG = 24; SP_GRID = 12
        R_CARD = 12; R_BUTTON = 8; SERIF = 'Yu Mincho'
    def main(page):
        page.title = 'Isolated Flet Ranking Baseline'
        page.theme_mode = ft.ThemeMode.DARK
        page.theme = ft.Theme(color_scheme_seed='#0096FA')
        page.dark_theme = ft.Theme(color_scheme_seed='#0096FA')
        page.window.width = width
        page.window.height = height
        db = FakeDB()
        page.bgcolor = DT.BG_VAULT
        update_lock = threading.RLock()
        shutdown = threading.Event()
        dispatcher = ui_dispatch.UiDispatcher(page, ui_update_lock=update_lock, shutdown_event=shutdown)
        def on_close(event):
            if event.type == ft.WindowEventType.CLOSE:
                shutdown.set()
        page.window.on_event = on_close
        def marshal_to_ui(async_fn, fallback, *, key=None):
            marshal_with_dispatcher(dispatcher, async_fn, fallback, key=key)
        baseline = ranking.build_ranking_tab(
            page, db, DT, lambda color, width=1: ft.Border(
                top=ft.BorderSide(width, color), bottom=ft.BorderSide(width, color),
                left=ft.BorderSide(width, color), right=ft.BorderSide(width, color)),
            update_lock, marshal_to_ui, dispatcher=dispatcher)
        page.add(baseline.tab_ranking_bg)
        baseline.tab_ranking_bg.visible = True
        page.update()
        baseline.on_tab_open()
    ft.run(main, view=ft.AppView.FLET_APP)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=OUTPUT)
    parser.add_argument('--width', type=int, choices=(1280, 1920), default=1280)
    parser.add_argument('--height', type=int, choices=(720, 1080), default=720)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prepare-only', action='store_true')
    mode.add_argument('--launch', action='store_true')
    args = parser.parse_args(argv)
    source = args.source.resolve(strict=True)
    if (args.width, args.height) not in ((1280, 720), (1920, 1080)):
        parser.error('valid viewport pairs are 1280x720 or 1920x1080')
    if args.launch:
        launch(source, args.output, args.width, args.height)
    else:
        print(json.dumps(prepare(source, args.output), indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
