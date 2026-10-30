"""Negative controls for the S07 next-save checker (generated work 501 only).

Never launch the product or use original DB, Cookie, network or saved files.
The old predicate (job done, receipt format, .txt present, no .epub) is
intentionally shown to accept corrupted content before checking the new guard.
"""
from __future__ import annotations

import json
from hashlib import sha256

import pytest

from tools.check_stage5_s07_next_saved_txt import (
    EXPECTED_TXT_501,
    verify_generated_txt_501,
)


def make_generated_export(tmp_path, content=None, **receipt_overrides):
    folder = tmp_path / 'outputs' / 'novel-501-fixture'
    folder.mkdir(parents=True)
    name = 'Generated novel(501).txt'
    payload = EXPECTED_TXT_501 if content is None else content
    (folder / name).write_bytes(payload)
    receipt = {
        'schema': 2, 'kind': 'novel-export', 'work_id': '501', 'author_id': '91',
        'format': 'TXT',
        'metadata': {'title': 'Generated novel', 'author': 'Fixture author',
                     'chapters': ['501'], 'series_id': None},
        'outputs': [{'name': name, 'bytes': len(payload),
                     'sha256': sha256(payload).hexdigest()}],
    }
    receipt.update(receipt_overrides)
    receipt_path = folder / 'receipt.json'
    receipt_path.write_text(json.dumps(receipt), encoding='utf-8')
    return folder / name, receipt_path


def old_only_extension_accepts(path, receipt_path):
    """Exactly the historical blind spot: no TXT bytes or identity inspected."""
    r = json.loads(receipt_path.read_text(encoding='utf-8'))
    return (r.get('format') == 'TXT' and path.exists()
            and path.suffix.lower() == '.txt'
            and not list(path.parent.glob('*.epub')))


def test_known_generated_fixture_501_bytes_and_identity_are_accepted(tmp_path):
    text, receipt = make_generated_export(tmp_path)
    result = verify_generated_txt_501(text, receipt)
    assert result['work_id'] == '501'
    assert result['author_id'] == '91'
    assert result['encoding'] == 'utf-8-sig'
    assert result['body_sha256'] == sha256(EXPECTED_TXT_501).hexdigest()
    assert result['decoded_characters'] > 0


@pytest.mark.parametrize('bad_bytes', [
    b'',  # empty TXT
    b'\xef\xbb\xbfwrong novel text',  # nonempty UTF-8, wrong body
    b'\xef\xbb\xbfGenerated fixture text.\n\n---\n\nforeign work 502',
    b'\xff\xfe\x00',  # invalid UTF-8
    EXPECTED_TXT_501[3:],  # valid UTF-8 body but missing required BOM
])
def test_old_check_misses_but_new_guard_rejects_wrong_or_empty_body(tmp_path, bad_bytes):
    text, receipt = make_generated_export(tmp_path, bad_bytes)
    assert old_only_extension_accepts(text, receipt)
    with pytest.raises(AssertionError, match='TXT'):
        verify_generated_txt_501(text, receipt)


@pytest.mark.parametrize('overrides', [
    {'work_id': '502'},
    {'author_id': '92'},
    {'metadata': {'title': 'Different novel', 'author': 'Fixture author',
                  'chapters': ['501'], 'series_id': None}},
    {'metadata': {'title': 'Generated novel', 'author': 'Fixture author',
                  'chapters': ['502'], 'series_id': None}},
])
def test_old_check_misses_but_new_guard_rejects_wrong_work_receipt(tmp_path, overrides):
    text, receipt = make_generated_export(tmp_path, **overrides)
    assert old_only_extension_accepts(text, receipt)
    with pytest.raises(AssertionError, match='TXT'):
        verify_generated_txt_501(text, receipt)


def test_modified_file_with_unchanged_receipt_digest_is_rejected(tmp_path):
    text, receipt = make_generated_export(tmp_path)
    text.write_bytes(b'\xef\xbb\xbfGenerated fixture text.\n\n---\n\nALTERED')
    assert old_only_extension_accepts(text, receipt)
    with pytest.raises(AssertionError, match='TXT'):
        verify_generated_txt_501(text, receipt)
