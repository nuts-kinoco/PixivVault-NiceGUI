"""Inventory outbound I/O candidates and fail closed on new or changed sites.

Static discovery identifies sinks and client references. Route status comes
from checked-in review evidence, never from the presence of a gate-related name.
"""

import argparse
import ast
import hashlib
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path


NETWORK_MODULES = {'requests', 'httpx', 'urllib3', 'aiohttp', 'socket', 'socketserver', 'http',
                   'httpcore', 'pixivpy', 'pixivpy3', 'curl_cffi', 'websocket', 'websockets',
                   'ftplib', 'smtplib', 'paramiko', 'pycurl', 'webbrowser'}
NETWORK_NAMES = {'getaddrinfo', 'gethostbyname', 'gethostbyaddr', 'urlopen', 'urlretrieve',
                 'create_connection', 'open_connection', 'do_open', 'fenced_request',
                 '_transport', 'perform', 'sendto', 'sendall', 'connect_ex', 'request', 'connect',
                 '_signed', '_app_read', '_read_response', '_default_write_transport', 'bind_shared_pixiv_stop',
                 'bind_public_cdn_shared_stop', 'create_shared_profile_reader', 'create_exact_managed_read_client',
                 'metadata_transport_for', 'write_admission', '_request', 'submit_author_batch'}
CLIENT_FILES = {'pixiv_read_client.py': {'LivePixivReadClient', 'PublicImageReadClient', 'VaultImageReader'},
                'pixiv_write_client.py': {'PixivWriteClient'},
                'external_vault_transport.py': {'ShaftTransport'}}
CLIENT_WRITES = {'_refresh_oauth', '_request_unfenced', 'fetch_account', 'online_tick',
                 'search_recommendation_illusts', 'search_illusts', 'resolve_novel_author',
                 'verify_identity', 'require_identity', 'reconcile_from_reader'}
JS_SINKS = re.compile(r'\b(fetch|XMLHttpRequest|WebSocket|EventSource|sendBeacon)\s*\(')
HREF = re.compile(r'\bhref\s*=(?!=|>)')
JS_IMAGE = re.compile(r'\bnew\s+Image\s*\(')
JS_SOURCE = re.compile(r'\.\s*src\s*=(?!=|>)')
ASSET_SINK = re.compile(r'''(?ix)\b(?:src|srcset)\s*=\s*["']?https?://|'''
                        r'''\burl\(\s*["']?https?://|@import\s+["']https?://''')
BOUND_ASSET = re.compile(r'\b(?:v-bind:src|:src|srcset)\s*=|\s:src\s*=')
JS_DELEGATORS = {'run_javascript', 'add_head_html', 'add_body_html', 'html'}
BROWSER_DELEGATORS = {'image', 'link', 'set_source', 'to', 'props', 'style'}


def strings(node):
    return [part.value for part in ast.walk(node)
            if isinstance(part, ast.Constant) and isinstance(part.value, str)]


def digest(node):
    return hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Site:
    path: str
    line: int
    scope: str
    target: str
    key: str


def qualified(node, aliases):
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        return f'{qualified(node.value, aliases)}.{node.attr}'
    if isinstance(node, ast.Call):
        return qualified(node.func, aliases)
    return '?'


def network_reference(node, aliases):
    if not isinstance(node, (ast.Name, ast.Attribute)):
        return None
    name = qualified(node, aliases)
    module, _, method = name.rpartition('.')
    if (module.split('.')[0] in NETWORK_MODULES and method in NETWORK_NAMES | {
        'get', 'post', 'head', 'put', 'patch', 'delete', 'send', 'urlopen', 'open'
    } or name in {'urllib.request.urlopen', 'urllib.request.urlretrieve'}):
        return name
    return None


class Visitor(ast.NodeVisitor):
    def __init__(self, path, tree, methods):
        self.path, self.tree, self.methods = path, tree, methods
        self.scopes = []
        self.aliases = [{}]
        self.sites = []
        self.duplicates = Counter()

    def visit_Import(self, node):
        for name in node.names:
            self.aliases[-1][name.asname or name.name.split('.')[0]] = name.name if name.asname else name.name.split('.')[0]

    def visit_ImportFrom(self, node):
        for name in node.names:
            self.aliases[-1][name.asname or name.name] = f'{node.module}.{name.name}'

    def visit_Assign(self, node):
        self.generic_visit(node)
        value = qualified(node.value, self.aliases[-1])
        for target in node.targets:
            if isinstance(target, ast.Name):
                self.aliases[-1][target.id] = value if value != '?' else target.id
            elif isinstance(target, ast.Attribute) and (reference := network_reference(node.value, self.aliases[-1])):
                self.add(node, f'deferred.{reference}')

    def visit_scope(self, node):
        self.scopes.append(node)
        self.aliases.append(self.aliases[-1].copy())
        if self.path.endswith('/managed_network_stop_gate.py') and [part.name for part in self.scopes] == [
            'ManagedNetworkStopGate', 'request'
        ]:
            self.add(node, 'gate.request-implementation')
        self.generic_visit(node)
        self.aliases.pop()
        self.scopes.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = visit_scope

    def add(self, node, target):
        scope = '.'.join(part.name for part in self.scopes) or '<module>'
        owner = self.scopes[-1] if self.scopes else self.tree
        key = f'{self.path}:{scope}:{target}:{digest(owner)}:{digest(node)}'
        self.duplicates[key] += 1
        self.sites.append(Site(self.path, node.lineno, scope, target, f'{key}:{self.duplicates[key]}'))

    def visit_Call(self, node):
        name = qualified(node.func, self.aliases[-1])
        base = name.split('.', 1)[0]
        method = name.rsplit('.', 1)[-1]
        if (base in NETWORK_MODULES and not name.startswith(('http.cookiejar.', 'http.cookies.'))
                or name.startswith(('urllib.request.', 'asyncio.open_connection'))):
            if method not in {'Request', 'parse_http_list', 'parse_keqv_list'}:
                self.add(node, name)
        elif method in NETWORK_NAMES and not name.startswith(('sqlite3.', 'ui.', 'app.')):
            self.add(node, name)
        elif method == 'open' and name.startswith(('opener.', 'build_opener.', 'urllib.')):
            self.add(node, name)
        elif method in {'transport', 'LivePixivReadClient', 'PublicImageReadClient'}:
            self.add(node, name)
        for arg in [*node.args, *(kw.value for kw in node.keywords)]:
            if reference := network_reference(arg, self.aliases[-1]):
                self.add(node, f'deferred.{reference}')
        if method == 'getattr' and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
            attr = node.args[1].value
            owner = qualified(node.args[0], self.aliases[-1])
            if isinstance(attr, str) and (attr in self.methods | NETWORK_NAMES or
                                         owner.split('.', 1)[0] in NETWORK_MODULES):
                self.add(node, f'getattr.{attr}')
        has_href = any(keyword.arg == 'href' for keyword in node.keywords)
        if method in JS_DELEGATORS | BROWSER_DELEGATORS:
            fragments = [part for arg in [*node.args, *(kw.value for kw in node.keywords)] for part in strings(arg)]
            has_href = has_href or any(HREF.search(part) for part in fragments)
            if method in JS_DELEGATORS:
                for hit in sorted({hit for part in fragments for hit in JS_SINKS.findall(part)}):
                    self.add(node, f'browser.{hit}')
                for pattern, target in ((JS_IMAGE, 'browser.image.construct'), (JS_SOURCE, 'browser.image.src')):
                    for part in fragments:
                        for _ in pattern.finditer(part):
                            self.add(node, target)
            if any(ASSET_SINK.search(part) or part.lower().startswith(('https://', 'http://'))
                   for part in fragments):
                self.add(node, f'browser.{method}.external-url')
            elif ((name.startswith('nicegui.ui.') and method in {'image', 'link'})
                  or method == 'set_source') and node.args:
                self.add(node, f'browser.{method}.source')
        if has_href:
            self.add(node, 'browser.href')
        self.generic_visit(node)

    def visit_Subscript(self, node):
        if (isinstance(node.ctx, ast.Store) and isinstance(node.value, ast.Attribute)
                and node.value.attr in {'props', '_props'} and isinstance(node.slice, ast.Constant)
                and node.slice.value == 'href'):
            self.add(node, 'browser.href')
        self.generic_visit(node)

    def visit_Attribute(self, node):
        if node.attr == 'href' and isinstance(node.ctx, ast.Store):
            self.add(node, 'browser.href')
        explicit_write = node.attr == 'execute' and re.search(
            r'(?:client|writer|write|pixiv)', qualified(node.value, self.aliases[-1]), re.I)
        borrowed = node.attr in {'search', 'ranking'} and re.search(
            r'(?:vault|transport)', qualified(node.value, self.aliases[-1]), re.I)
        if (node.attr in self.methods or explicit_write or borrowed) and isinstance(node.ctx, ast.Load):
            self.add(node, f'client.{node.attr}')
        self.generic_visit(node)


def inventory(root):
    parsed = [(path, ast.parse(path.read_bytes(), filename=str(path))) for path in sorted(root.rglob('*.py'))]
    methods = set(CLIENT_WRITES)
    for path, tree in parsed:
        for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)
                    and n.name in CLIENT_FILES.get(path.name, ())):
            methods.update(n.name for n in cls.body if isinstance(n, ast.FunctionDef)
                           and n.name.startswith(('get_', 'search_', 'resolve_')))
    sites = []
    for path, tree in parsed:
        visitor = Visitor(path.relative_to(root).as_posix(), tree, methods)
        visitor.visit(tree)
        sites.extend(visitor.sites)
    for path in sorted(root.rglob('*')):
        if path.suffix in {'.js', '.ts', '.html', '.vue', '.css'} and path.is_file():
            source = path.read_bytes()
            source_hash = hashlib.sha256(source.replace(b'\r\n', b'\n')).hexdigest()[:16]
            for line, value in enumerate(source.decode('utf-8').splitlines(), 1):
                targets = {f'browser.{target}' for target in JS_SINKS.findall(value)}
                if HREF.search(value):
                    targets.add('browser.href')
                if JS_IMAGE.search(value):
                    targets.add('browser.image.construct')
                if JS_SOURCE.search(value):
                    targets.add('browser.image.src')
                if ASSET_SINK.search(value):
                    targets.add('browser.asset.external-url')
                elif BOUND_ASSET.search(value):
                    targets.add('browser.asset.bound-source')
                for target in sorted(targets):
                    name = path.relative_to(root).as_posix()
                    key = f'{name}:{line}:{target}:{source_hash}'
                    sites.append(Site(name, line, '<asset>', target, key))
    return sites


def check(sites, review):
    errors = []
    for site in sites:
        entry = review.get(site.key, {})
        status = entry.get('status', 'unreviewed')
        if status not in {'gated', 'local', 'manual', 'non-network'} or not entry.get('reason'):
            errors.append(f'{site.path}:{site.line} {site.scope} {site.target}: {status}')
        elif status in {'local', 'manual', 'non-network'} and not entry.get('evidence'):
            errors.append(f'{site.path}:{site.line} {site.scope} {site.target}: missing exclusion evidence')
        elif status == 'manual' and not re.search(r'original/[^\s]+\.py:[0-9]+', entry['evidence']):
            errors.append(f'{site.path}:{site.line} {site.scope} {site.target}: missing original location')
    for key in sorted(set(review) - {site.key for site in sites}):
        errors.append(f'stale review: {key}')
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1]
    parser.add_argument('--src', type=Path, default=root / 'src')
    parser.add_argument('--review', type=Path, default=root / 'tools' / 'p6_network_review.json')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.out.resolve().drive.upper() != 'S:':
        parser.error('output must be on S:')
    sites = inventory(args.src)
    review = json.loads(args.review.read_text(encoding='utf-8')) if args.review.exists() else {}
    errors = check(sites, review)
    rows = [dict(asdict(site), **review.get(site.key, {'status': 'unreviewed'})) for site in sites]
    args.out.write_text(json.dumps({'counts': Counter(row['status'] for row in rows), 'sites': rows,
                                    'issues': errors}, indent=2), encoding='utf-8')
    print(f'network sites={len(sites)}, issues={len(errors)}')
    return bool(errors)


if __name__ == '__main__':
    raise SystemExit(main())
