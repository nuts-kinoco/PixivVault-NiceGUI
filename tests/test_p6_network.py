import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import p6_network


def test_network_inventory_requires_review_for_added_and_changed_call(tmp_path):
    module = tmp_path / 'example.py'
    module.write_text('import requests as r\nget = r.get\ndef run():\n    get("https://example.invalid")\n')
    original = p6_network.inventory(tmp_path)
    assert [site.target for site in original] == ['requests.get']
    approved = {original[0].key: {'status': 'gated', 'reason': 'test'}}
    assert p6_network.check(original, approved) == []
    module.write_text(module.read_text().replace('example.invalid', 'other.invalid'))
    assert p6_network.check(p6_network.inventory(tmp_path), approved)


def test_network_inventory_covers_socket_http_instances_and_browser(tmp_path):
    (tmp_path / 'sample.py').write_text('''from socket import socket as S
import httpx as h
from nicegui import ui
def go():
    sock = S()
    sock.connect(('127.0.0.1', 1))
    h.Client().get('https://example.invalid')
    ui.run_javascript("fetch('/api')")
''')
    targets = {site.target for site in p6_network.inventory(tmp_path)}
    assert {'socket.socket.connect', 'httpx.Client.get', 'browser.fetch'} <= targets


def test_network_inventory_finds_external_asset_loads(tmp_path):
    (tmp_path / 'skin.css').write_text('body { background: url(https://example.invalid/x.png); }')
    (tmp_path / 'index.html').write_text('<img src=https://example.invalid/y.png>')
    assert [(site.path, site.target) for site in p6_network.inventory(tmp_path)] == [
        ('index.html', 'browser.asset.external-url'), ('skin.css', 'browser.asset.external-url')]


def test_network_inventory_finds_deferred_urlopen(tmp_path):
    (tmp_path / 'deferred.py').write_text('''from urllib.request import urlopen
import asyncio
async def run(url):
    return await asyncio.to_thread(urlopen, url)
''')
    assert [site.target for site in p6_network.inventory(tmp_path)] == ['deferred.urllib.request.urlopen']


def test_manual_exclusion_requires_original_code_location(tmp_path):
    (tmp_path / 'manual.py').write_text('import webbrowser\nwebbrowser.open("https://example.invalid")\n')
    site, = p6_network.inventory(tmp_path)
    approval = {'status': 'manual', 'reason': 'Click opens the external browser'}
    assert p6_network.check([site], {site.key: approval})
    assert p6_network.check([site], {site.key: dict(approval, evidence='src/manual.py:2')})
    assert not p6_network.check([site], {site.key: dict(approval, evidence='original/gui.py:2825')})


def test_network_inventory_finds_dynamic_href_and_image_sources(tmp_path):
    (tmp_path / 'sample.py').write_text('''from nicegui import ui
def add_link(item, target):
    item.props['href'] = target
    item.props(f'href="{target}" target=_blank')
    MenuEntry(href=target)
    ui.run_javascript('const i = new Image(); i.src = next.url;')
''')
    (tmp_path / 'sample.js').write_text('const image = new Image();\nimage.src = target;\nimage.src === other;\n')
    sites = p6_network.inventory(tmp_path)
    assert [(s.path, s.target) for s in sites].count(('sample.py', 'browser.href')) == 3
    for path in ('sample.py', 'sample.js'):
        assert [(s.path, s.target) for s in sites].count((path, 'browser.image.construct')) == 1
        assert [(s.path, s.target) for s in sites].count((path, 'browser.image.src')) == 1
    assert len(p6_network.check(sites, {})) == 7


def test_candidate_network_routes():
    root = Path(__file__).resolve().parents[1]
    review = json.loads((root / 'tools' / 'p6_network_review.json').read_text(encoding='utf-8'))
    issues = p6_network.check(p6_network.inventory(root / 'src'), review)
    assert not issues, '\n'.join(issues)
