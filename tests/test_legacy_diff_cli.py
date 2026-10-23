"""New opt-in cannot point managed outputs at the original snapshot or ZIPs."""
from pathlib import Path

import pytest

from pixivvault_nicegui.bootstrap import parse_args


def options(tmp_path):
    cookie = tmp_path / 'credentials' / 'cookie.txt'
    cookie.parent.mkdir()
    cookie.write_text('synthetic-placeholder', encoding='utf-8')
    old = tmp_path / 'frozen' / 'copy.sqlite'
    old.parent.mkdir()
    old.write_bytes(b'placeholder')
    images = tmp_path / 'old-images'
    images.mkdir()
    managed = tmp_path / 'managed' / 'new'
    return cookie, old, images, managed


def test_diff_cli_requires_managed_root_and_frozen_copy(tmp_path):
    cookie, old, images, managed = options(tmp_path)
    with pytest.raises(SystemExit):
        parse_args(['--legacy-diff-image-root', str(images)])
    with pytest.raises(SystemExit):
        parse_args(['--pixiv-cookie-file', str(cookie),
                    '--pixiv-download-root', str(managed),
                    '--legacy-diff-image-root', str(images)])
    cfg = parse_args(['--pixiv-cookie-file', str(cookie),
                      '--pixiv-download-root', str(managed),
                      '--failed-queue-readonly-db', str(old),
                      '--legacy-diff-image-root', str(images)])
    assert cfg.legacy_diff_image_root == images
    assert cfg.failed_queue_readonly_db == old
    assert not managed.exists()


def test_diff_cli_rejects_unsafe_overlap_and_symlink(tmp_path):
    cookie, old, images, managed = options(tmp_path)
    prefix = ['--pixiv-cookie-file', str(cookie),
              '--pixiv-download-root', str(managed),
              '--failed-queue-readonly-db', str(old)]
    for candidate in (managed.parent, cookie.parent, old.parent, tmp_path):
        with pytest.raises(SystemExit):
            parse_args(prefix + ['--legacy-diff-image-root', str(candidate)])
    with pytest.raises(SystemExit):
        parse_args(prefix + ['--legacy-diff-image-root', str(images / 'missing')])
