"""Offline deterministic regressions for the Phase9 browser evidence checker.

These tests do not start a web browser, reach Pixiv, or read the user's library.
"""
from __future__ import annotations

import hashlib
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from tools import check_author_download_browser as checker


OLD = '2026-10-24T10:00:00+09:00'
NEW = '2026-10-25T10:00:00+09:00'


def _png(color: tuple[int, int, int]) -> bytes:
    buffer = BytesIO()
    Image.new('RGB', (120, 84), color).save(buffer, format='PNG')
    return buffer.getvalue()


def _fixture(root: Path, *, version_count=3, selected_date=OLD,
             page_color: tuple[int, int, int] | None = None):
    root.mkdir()
    generations = root / 'generations'
    generations.mkdir()
    receipt_path = None
    for num in range(version_count):
        folder = generations / f'work-321-{num + 1:032x}'
        folder.mkdir()
        if num == version_count - 1:
            colors = [page_color or (13, 37, 211), (9, 191, 53)]
            pages = []
            for index, color in enumerate(colors, 1):
                data = _png(color)
                name = f'page-{index}.png'
                (folder / name).write_bytes(data)
                pages.append({'name': name, 'sha256': hashlib.sha256(data).hexdigest()})
            receipt = {'version': 1, 'work_id': '321', 'page_count': 2, 'files': pages}
        else:
            receipt = {'work_id': '321'}
        receipt_path = folder / 'receipt.json'
        receipt_path.write_text(json.dumps(receipt), encoding='utf-8')
    assert receipt_path is not None
    relative = receipt_path.relative_to(root).as_posix()
    selected = {
        'update_date': selected_date,
        'accepted_fingerprint': 'a' * 64,
        'relative_receipt': relative,
        'sha256': hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
    }
    entries = {str(n + 400): {} for n in range(69)}
    entries['321'] = selected
    index_file = root / 'active-generations.json'

    def set_date(value: str):
        entries['321']['update_date'] = value
        temporary = root / 'index-temporary.json'
        temporary.write_text(json.dumps({'version': 2, 'entries': entries}), encoding='utf-8')
        temporary.replace(index_file)

    set_date(selected_date)
    return index_file, set_date


def _clock(monkeypatch, callback=None):
    now = [0.0]
    waits = [0]

    def sleep(seconds: float):
        now[0] += seconds
        waits[0] += 1
        if callback is not None:
            callback(waits[0])

    monkeypatch.setattr(checker, 'time', SimpleNamespace(monotonic=lambda: now[0], sleep=sleep))
    return waits


def test_checker_waits_for_specific_generation_after_70_entries(tmp_path, monkeypatch):
    root = tmp_path / 'managed'
    _index, set_date = _fixture(root)
    waits = _clock(monkeypatch, lambda count: set_date(NEW) if count == 3 else None)
    assert checker.verify_saved(root, expected_update_date=NEW, expected_versions=3,
                                expected_entries=70)['work_id'] == '321'
    assert waits[0] == 3


def test_checker_retries_atomic_index_read_during_replace(tmp_path, monkeypatch):
    root = tmp_path / 'managed'
    index, set_date = _fixture(root)
    index.write_text('{ invalid JSON', encoding='utf-8')
    waits = _clock(monkeypatch, lambda count: set_date(NEW) if count == 2 else None)
    assert checker.verify_saved(root, expected_update_date=NEW, expected_versions=3,
                                expected_entries=70)['work_id'] == '321'
    assert waits[0] == 2


def test_checker_rejects_stale_generation_after_bounded_poll(tmp_path, monkeypatch):
    root = tmp_path / 'managed'
    _fixture(root)
    waits = _clock(monkeypatch)
    with pytest.raises(AssertionError, match='active generation not settled'):
        checker.verify_saved(root, expected_update_date=NEW, expected_versions=3,
                            expected_entries=70)
    assert waits[0] >= 1200  # fake monotonic seconds, not real waiting


def test_checker_rejects_invalid_page_pixels_even_after_settled_index(tmp_path, monkeypatch):
    root = tmp_path / 'managed'
    _fixture(root, selected_date=NEW, page_color=(74, 28, 90))
    _clock(monkeypatch)
    with pytest.raises(AssertionError):
        checker.verify_saved(root, expected_update_date=NEW, expected_versions=3,
                            expected_entries=70)
