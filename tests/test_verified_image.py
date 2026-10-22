from pathlib import Path

from nicegui import ui

from pixivvault_nicegui.ui.verified_image import VerifiedImage


def test_source_changes_bump_request_even_for_same_source():
    image = VerifiedImage('/media/demo/0/view.png#learning-7', alt='Artwork')
    assert isinstance(image, ui.element)
    assert image._props['src'].endswith('#learning-7')
    initial = image._props['request_id']
    assert image.set_source('/media/demo/0/view.png#learning-7') is image
    assert image._props['request_id'] == initial + 1
    image.set_source('/media/demo/0/view.png#learning-11')
    assert image._props['request_id'] == initial + 2
    assert image._props['alt'] == 'Artwork'
    image.set_source('')
    assert image._props['src'] == ''


def test_vue3_component_has_versioned_decode_and_unmount_cleanup():
    script = Path(__file__).resolve().parents[1] / 'src/pixivvault_nicegui/ui/verified_image.js'
    code = script.read_text(encoding='utf8')
    assert 'probe.decode()' in code
    assert 'beforeUnmount()' in code
    assert "this.$emit('load', source)" in code
    assert "this.$emit('error', source)" in code
    assert 'requestAnimationFrame' in code
    assert 'request_id()' in code
    assert ':key="version"' in code
    assert 'await image.decode()' in code
    assert 'image !== this.$refs.image' in code
    assert 'actualResource.href !== expectedResource.href' in code
