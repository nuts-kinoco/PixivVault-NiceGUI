"""Focused X09/V06 additions to the existing generated X07 product checker.

Imported only by --focus x09-v06. No credentials, real transport, original
images, desktop adapter, registry operation, or external navigation is used.
The bounded read gate delays the actual product snapshot, never invents one.
"""
from __future__ import annotations

import hashlib
import json
import re
from threading import Event, Lock
import time
from types import SimpleNamespace


class FocusedFixture:
    def __init__(self, services, reader, flags):
        from pixivvault_nicegui.recommendation_state import (
            RecommendationController, RecommendationGroup,
            RecommendationSnapshot, RecommendationWork,
        )

        self.services = services
        self.writer = services.managed_downloads
        self.reader = reader
        self.flags = flags
        self.flags.update(trash_pause=False, trash_fail=False)
        self._lock = Lock()
        self._release = Event()
        self._release.set()
        self._started = self._completed = self._waiting = self._failed = 0
        source = services.duplicate_inspection
        actual_snapshot = source.quarantine_snapshot

        def observed_snapshot():
            with self._lock:
                self._started += 1
                paused = self.flags['trash_pause']
                fail = self.flags['trash_fail']
            try:
                result = actual_snapshot()
                if paused:
                    with self._lock:
                        self._waiting += 1
                    try:
                        if not self._release.wait(12):
                            raise OSError('generated trash read gate expired')
                    finally:
                        with self._lock:
                            self._waiting -= 1
                if fail:
                    raise OSError('generated trash overview read failure')
                return result
            except Exception:
                with self._lock:
                    self._failed += 1
                raise
            finally:
                with self._lock:
                    self._completed += 1

        # Fixture-only completion ordering; the source method still computes
        # every returned QuarantineSnapshot from the actual managed files.
        source.quarantine_snapshot = observed_snapshot
        snapshot = RecommendationSnapshot(sections=(
            ('superlike', (RecommendationGroup(
                'superlike', 'Generated saved works',
                (RecommendationWork('123', 'Generated 123', '24', 'Generated author'),
                 RecommendationWork('223', 'Generated 223', '25', 'Generated author')),
            ),)),
        ), unavailable=())
        previous = services.recommendations
        services.recommendations = RecommendationController(
            SimpleNamespace(load=lambda: snapshot))
        if previous is not None:
            previous.close()
        if services.desktop is not None:
            raise AssertionError('focused browser fixture must not have an OS launcher')

    def on_inject(self, values):
        if 'trash_pause' in values:
            if values['trash_pause']:
                self._release.clear()
            else:
                self._release.set()

    def release(self):
        self.flags['trash_pause'] = False
        self._release.set()

    def state(self):
        from pixivvault_nicegui.managed_downloads import _safe_path

        writer = self.writer
        tree = {}
        copies = []
        # This independent observer uses the existing fixture's actual files,
        # not the product overview count/format method being tested.
        with writer.quarantine._lock:
            generation = writer._generations.state_snapshot()
            root = writer.quarantine.root
            _safe_path(root)
            exists = root.exists()
            if exists:
                for path in sorted(root.rglob('*')):
                    if not path.is_file():
                        continue
                    _safe_path(path, file=True)
                    raw = path.read_bytes()
                    relative = path.relative_to(root).as_posix()
                    digest = hashlib.sha256(raw).hexdigest()
                    tree[relative] = digest
                    if (path.parent.parent == writer.quarantine.operations
                            and path.suffix == '.png'):
                        copies.append({'file': relative, 'bytes': len(raw), 'sha256': digest})
            index = writer._generations.index_path
            _safe_path(index, file=True)
            index_sha = hashlib.sha256(index.read_bytes()).hexdigest() if index.exists() else None
        with self._lock:
            read_gate = dict(started=self._started, completed=self._completed,
                             waiting=self._waiting, failed=self._failed)
        calls = list(self.reader.calls)
        return {
            'revision': generation['revision'],
            'trash_directory_exists': exists,
            'quarantine_tree': tree,
            'copy_files': copies,
            'copy_bytes': sum(item['bytes'] for item in copies),
            'index_sha256': index_sha,
            'read_gate': read_gate,
            'reader_calls': calls,
            # Ranking/thumbnail warmup is recorded above, separately from the
            # original downloads and recommendation acquisition under review.
            'acquisition_calls': [call for call in calls
                                  if call[0] not in ('ranking', 'image')],
            'desktop_connected': self.services.desktop is not None,
        }


def inspect(page, url, headers, output):
    from playwright.sync_api import expect

    expect.set_options(timeout=20000)
    checks = []
    links = []
    phase = 'focused-open'
    conflicts = 0

    def mark(value):
        nonlocal phase
        phase = value
        with (output / 'progress.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'phase': phase, 'time': time.time()},
                                    ensure_ascii=False) + '\n')

    def state():
        nonlocal conflicts
        for _ in range(5):
            response = page.request.get(url + '/_x07/state', headers=headers)
            if response.status == 409:
                conflicts += 1
                page.wait_for_timeout(50)
                continue
            assert response.ok, f'{phase}: state HTTP {response.status}'
            return response.json()
        raise AssertionError(f'{phase}: observation revisions did not settle')

    def inject(**values):
        response = page.request.post(url + '/_x07/inject', headers=headers, data=values)
        assert response.ok, f'{phase}: fixture control HTTP {response.status}'

    def eventually(predicate, label, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = state()
            if predicate(value):
                return value
            page.wait_for_timeout(75)
        raise AssertionError(label)

    def tab(key):
        button = page.get_by_test_id('tab-' + key)
        button.click()
        expect(button).to_have_attribute('aria-current', 'page')

    def selection():
        values = []
        for node in page.locator('[data-testid^="duplicate-select-"]').all():
            identity = node.get_attribute('data-testid') or ''
            if re.fullmatch(r'duplicate-select-[1-9][0-9]*-[1-9][0-9]*', identity):
                values.append((identity, node.is_checked()))
        return sorted(values)

    def protected(reference, value):
        for key in ('source_pages', 'operation_count', 'excluded_count',
                    'complete_receipts', 'original_proofs'):
            assert value[key] == reference[key], f'{phase}: protected {key} changed'
        for key in ('quarantine_tree', 'index_sha256', 'copy_files'):
            assert value['focus'][key] == reference['focus'][key], (
                f'{phase}: read-only storage {key} changed')
        assert value['original_unchanged']
        assert value['original_gets'] == initial['original_gets']
        assert value['following_calls'] == []
        assert value['external_server_attempts'] == []
        assert value['focus']['acquisition_calls'] == []
        assert not value['focus']['desktop_connected']

    def complete_read_after(previous):
        return eventually(
            lambda value: value['focus']['read_gate']['completed'] > previous
                          and value['focus']['read_gate']['started']
                          == value['focus']['read_gate']['completed'],
            f'{phase}: overview read did not complete')

    trash = page.get_by_test_id('duplicate-trash-dialog')
    status = page.get_by_test_id('duplicate-trash-status')
    count = page.get_by_test_id('duplicate-trash-copy-count')
    capacity = page.get_by_test_id('duplicate-trash-copy-bytes')
    excluded = page.get_by_test_id('duplicate-trash-excluded')
    open_location = page.get_by_test_id('duplicate-trash-open')

    mark('focused-open')
    page.goto(url, wait_until='domcontentloaded')
    expect(page.get_by_test_id('tab-dedup')).to_be_enabled()
    expect(page.get_by_test_id('tab-recommend')).to_be_enabled()
    tab('dedup')
    page.get_by_test_id('duplicate-scan').click()
    expect(page.get_by_test_id('duplicate-mode-near')).to_be_enabled()
    page.get_by_test_id('duplicate-mode-near').click()
    expect(page.get_by_test_id('duplicate-thumbnail')).to_have_count(6)
    page.get_by_test_id('duplicate-select-123-2').click()
    expect(page.get_by_test_id('duplicate-select-123-2')).to_be_checked()
    initial = state()
    assert initial['complete_receipts'] == 4
    assert initial['operation_count'] == initial['excluded_count'] == 0
    assert initial['focus']['trash_directory_exists'] is False
    assert initial['focus']['copy_files'] == []
    assert initial['focus']['read_gate']['started'] == 0
    protected(initial, initial)
    selected = selection()
    assert any(checked for _, checked in selected)

    mark('trash-uncreated')
    page.get_by_test_id('duplicate-trash').click()
    expect(trash).to_be_visible()
    expect(status).to_contain_text('まだ作成されていません')
    expect(count).to_have_text('公開済みの隔離コピー: 0件')
    expect(capacity).to_have_text('隔離コピーの容量: 0 B')
    expect(excluded).to_have_text('通常の一覧から除外中: 0ページ')
    expect(open_location).to_be_disabled()
    before_read = state()['focus']['read_gate']['completed']
    page.get_by_test_id('duplicate-trash-refresh').click()
    uncreated = complete_read_after(before_read)
    expect(status).to_contain_text('まだ作成されていません')
    protected(initial, uncreated)
    assert not uncreated['focus']['trash_directory_exists']
    page.screenshot(path=str(output / 'trash-uncreated.png'), animations='disabled')
    page.get_by_test_id('duplicate-trash-close').click()
    expect(trash).not_to_be_visible()
    assert selection() == selected
    checks.append('uncreated overview and explicit refresh keep selection, storage and original GET count')

    mark('single-published-fixture')
    page.get_by_test_id('duplicate-group-move').first.click()
    confirm = page.get_by_test_id('duplicate-confirm-dialog')
    expect(confirm).to_be_visible()
    expect(page.get_by_test_id('duplicate-confirm-title')).to_contain_text('1枚')
    expect(confirm).to_contain_text('元の2ページ')
    page.get_by_test_id('duplicate-confirm-execute').click()
    published = eventually(
        lambda value: value['operation_count'] == 1
                      and value['source_pages']['123'] == [0, 2]
                      and len(value['focus']['copy_files']) == 1,
        'one generated quarantine publication did not complete')
    expect(page.get_by_test_id('duplicate-status')).to_contain_text('1枚を隔離しました・失敗0枚')
    assert published['excluded_count'] == 1 and published['original_unchanged']
    assert published['original_proofs'] == initial['original_proofs']
    assert published['original_gets'] == initial['original_gets']
    copied = published['focus']['copy_files'][0]
    assert copied['file'].endswith('/123-p2.png')
    assert copied['sha256'] in initial['original_proofs'].values()
    expected_bytes = copied['bytes']
    # A tiny generated solid PNG deliberately stays below a rounding boundary.
    assert 0 < expected_bytes < 1024
    page.get_by_test_id('duplicate-select-123-1').click()
    expect(page.get_by_test_id('duplicate-select-123-1')).to_be_checked()
    selected = selection()
    baseline = state()

    def ready_panel():
        expect(trash).to_be_visible()
        expect(status).to_have_text('ごみ箱の内容を確認しました。')
        expect(count).to_have_text('公開済みの隔離コピー: 1件')
        expect(capacity).to_have_text(f'隔離コピーの容量: {expected_bytes} B')
        expect(excluded).to_have_text('通常の一覧から除外中: 1ページ')
        # The web-only fixture intentionally has no DesktopIntegration.
        expect(open_location).to_be_disabled()

    mark('trash-published-totals')
    page.get_by_test_id('duplicate-trash').click()
    ready_panel()
    before_read = state()['focus']['read_gate']['completed']
    page.get_by_test_id('duplicate-trash-refresh').click()
    complete_read_after(before_read)
    ready_panel()
    protected(baseline, state())
    assert selection() == selected
    page.screenshot(path=str(output / 'trash-published.png'), animations='disabled')
    checks.append('published count and unrounded capacity match actual copied file; active exclusion is separate')

    mark('trash-read-failure')
    inject(trash_fail=True)
    before_read = state()['focus']['read_gate']['completed']
    page.get_by_test_id('duplicate-trash-refresh').click()
    complete_read_after(before_read)
    expect(status).to_contain_text('読み取れませんでした')
    expect(count).to_have_text('公開済みの隔離コピー: 未確認')
    expect(capacity).to_have_text('隔離コピーの容量: 未確認')
    expect(excluded).to_have_text('通常の一覧から除外中: 未確認')
    expect(open_location).to_be_disabled()
    protected(baseline, state())
    assert selection() == selected
    page.screenshot(path=str(output / 'trash-read-failure.png'), animations='disabled')
    inject(trash_fail=False)
    page.get_by_test_id('duplicate-trash-refresh').click()
    ready_panel()
    checks.append('read failure remains unknown; an explicit reread restores the real overview')

    mark('trash-late-read-close-and-leave')
    inject(trash_pause=True)
    page.get_by_test_id('duplicate-trash-refresh').click()
    eventually(lambda value: value['focus']['read_gate']['waiting'] == 1,
               'actual snapshot was not held by the finite fixture gate')
    expect(status).to_have_text('ごみ箱を確認しています…')
    page.get_by_test_id('duplicate-trash-close').click()
    expect(trash).not_to_be_visible()
    assert selection() == selected
    tab('recommend')
    inject(trash_pause=False)
    eventually(lambda value: value['focus']['read_gate']['waiting'] == 0
               and value['focus']['read_gate']['started']
               == value['focus']['read_gate']['completed'],
               'released late snapshot did not finish')
    expect(trash).not_to_be_visible()
    expect(page.get_by_test_id('tab-recommend')).to_have_attribute('aria-current', 'page')
    protected(baseline, state())
    checks.append('closing during a delayed actual read and leaving the tab rejects its late result')

    mark('recommend-right-click-fixed-url')
    assert len(page.context.pages) == 1
    for wid in ('123', '223'):
        row = page.get_by_test_id('recommend-work-superlike-' + wid)
        expect(row).to_be_visible()
        row.get_by_text('Generated ' + wid, exact=True).click(button='right')
        menu = page.get_by_test_id('recommend-pixiv-context-superlike-' + wid)
        link = page.get_by_test_id('recommend-pixiv-link-superlike-' + wid)
        expect(menu).to_be_visible()
        expect(link).to_be_visible()
        expected_url = 'https://www.pixiv.net/artworks/' + wid
        expect(link).to_have_attribute('href', expected_url)
        expect(link).to_have_attribute('target', '_blank')
        relation = set((link.get_attribute('rel') or '').split())
        assert {'noopener', 'noreferrer'} <= relation
        expect(row.get_by_role('button', name='保存済みを開く', exact=True)).to_be_visible()
        links.append({'work_id': wid, 'href': link.get_attribute('href'),
                      'target': link.get_attribute('target'), 'rel': sorted(relation),
                      'navigation_attempted': False})
        page.screenshot(path=str(output / ('recommend-context-' + wid + '.png')),
                        animations='disabled')
        page.keyboard.press('Escape')
        expect(menu).not_to_be_visible()
        assert page.url.rstrip('/') == url
        assert len(page.context.pages) == 1
    protected(baseline, state())
    checks.append('real right-click menus expose each fixed Pixiv URL and safe new-tab attributes without navigation')

    mark('trash-tab-return')
    tab('dedup')
    expect(page.get_by_test_id('duplicate-select-123-1')).to_be_checked()
    assert selection() == selected
    expect(trash).not_to_be_visible()
    page.get_by_test_id('duplicate-trash').click()
    ready_panel()
    page.screenshot(path=str(output / 'trash-return.png'), animations='disabled')
    page.get_by_test_id('duplicate-trash-close').click()
    expect(trash).not_to_be_visible()
    final = state()
    protected(baseline, final)
    assert selection() == selected
    assert final['focus']['read_gate']['started'] == final['focus']['read_gate']['completed']
    assert final['focus']['read_gate']['waiting'] == 0
    assert final['focus']['read_gate']['failed'] == 1
    checks.append('returning to the original tab preserves selection and reopens a fresh correct overview')

    return {'scope': 'X09/V06 focused synthetic product UI', 'checks': checks,
            'initial': initial, 'final': final, 'link_observations': links,
            'original_get_delta': final['original_gets'] - initial['original_gets'],
            'copy_bytes_observed_independently': expected_bytes,
            'observation_conflicts': conflicts,
            'viewport': page.evaluate('({width:innerWidth,height:innerHeight,dpr:devicePixelRatio})')}
