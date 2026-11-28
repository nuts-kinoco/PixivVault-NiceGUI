from io import BytesIO
import hashlib

from PIL import Image
import pytest

from tools import uiux_audit


def test_capture_image_records_actual_dimensions_and_digest(tmp_path):
    image = BytesIO()
    Image.new('RGB', (1280, 800), 'white').save(image, 'PNG')

    class Page:
        def screenshot(self, **kwargs):
            return image.getvalue()

    cell = uiux_audit.Cell('queue', 'normal', 'dark', 1280, 800, 100, None)
    info = uiux_audit.screenshot(Page(), tmp_path / 'screen.png', cell)
    assert info['width'] == 1280 and info['height'] == 800
    assert info['sha256'] == hashlib.sha256((tmp_path / 'screen.png').read_bytes()).hexdigest()


def test_redacted_capture_restores_text_after_screenshot_failure(tmp_path):
    class Page:
        def __init__(self):
            self.evaluations = []
        def evaluate(self, script):
            self.evaluations.append(script)
        def screenshot(self, **kwargs):
            raise ValueError('capture failed')
    page = Page()
    cell = uiux_audit.Cell('queue', 'normal', 'dark', 1280, 800, 100, None)
    with pytest.raises(ValueError):
        uiux_audit.screenshot(page, tmp_path / 'screen.png', cell, redact=True)
    assert len(page.evaluations) == 2


def test_state_requires_current_visible_explicit_evidence():
    assert not uiux_audit.capture_state('normal', False, True, False)
    assert not uiux_audit.capture_state('normal', True, True, True)
    assert not uiux_audit.capture_state('normal', True, False, False)
    assert uiux_audit.capture_state('normal', True, True, False)


def test_normal_requires_screen_specific_nonempty_evidence():
    assert uiux_audit.state_marker('queue', 'normal') == '[data-testid=queue-view] [data-testid=job-row]'
    assert uiux_audit.state_marker('download', 'normal') == '[data-testid=unified-author-row]'
    assert uiux_audit.state_marker('queue', 'empty') == '[data-testid=queue-view] .pv-empty'
    assert uiux_audit.state_marker('bookmarks', 'empty') == '[data-testid=bookmark-status]'
    assert uiux_audit.state_marker('learning', 'empty') is None


def test_loaded_module_manifest_excludes_outside_modules_and_hashes_bytes(tmp_path):
    src = tmp_path / 'src'
    src.mkdir()
    module = src / 'sample.py'
    module.write_bytes(b'print(1)\n')
    result = uiux_audit.loaded_module_manifest(src, [module, tmp_path / 'outside.py'])
    assert list(result) == ['sample.py']
    assert result['sample.py'] == hashlib.sha256(module.read_bytes()).hexdigest()


def test_synthetic_job_receipts_bind_author_to_matching_job():
    from tools.p5_design_evidence import synthetic_job_receipts
    from pixivvault_nicegui.job_service import JobSnapshot
    from pixivvault_nicegui.ui.job_views import _accepted_author_id
    from types import SimpleNamespace

    rows = [dict(jobId='sample', id='401', author_name='synthetic', author_id='2001')]
    job = JobSnapshot('sample', 'pixiv-illust', '401', 'synthetic', state='done')
    services = SimpleNamespace(_managed_job_records=synthetic_job_receipts(rows))
    assert _accepted_author_id(services, job) == '2001'


def test_old_r10_mask_leaves_banner_pixels_below_32():
    from tools.uiux_audit import old_r10_mask
    source = Image.new('RGB', (80, 40), 'red')
    masked = old_r10_mask(source)
    assert masked.getpixel((0, 31)) == (20, 23, 28)
    assert masked.getpixel((0, 32)) == (255, 0, 0)
    assert source.getpixel((0, 0)) == (255, 0, 0)


def test_personal_loading_gate_is_released_explicitly():
    from tools.uiux_fixture import LoadingGate
    gate = LoadingGate()
    gate.hold()
    assert not gate.released.is_set()
    gate.release()
    assert gate.released.is_set()
