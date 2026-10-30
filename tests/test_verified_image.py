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
    assert image._props['retain_previous'] is False
    image.set_source('')
    assert image._props['src'] == ''


def test_vue3_component_has_versioned_decode_and_unmount_cleanup():
    script = Path(__file__).resolve().parents[1] / 'src/pixivvault_nicegui/ui/verified_image.js'
    code = script.read_text(encoding='utf8')
    assert 'beforeUnmount()' in code
    assert "this.$emit('load', reportedSource)" in code
    assert "this.$emit('error', reportedSource)" in code
    assert 'const reportedSource = this.event_source || source;' in code
    assert 'requestAnimationFrame' in code
    assert 'request_id()' in code
    assert ':key="frame.id"' in code
    assert 'await image.decode()' in code
    assert 'image !== this.imageNodes.get(version)' in code
    assert 'actualResource.href !== expectedResource.href' in code


def test_previous_image_retained_without_acknowledging_old_request():
    image = VerifiedImage('/media/a/0/view.png#request-1', retain_previous=True)
    assert image._props['retain_previous'] is True
    image.set_source('/media/b/0/view.png#request-2')
    assert image._props['src'].endswith('#request-2')
    script = Path(__file__).resolve().parents[1] / 'src/pixivvault_nicegui/ui/verified_image.js'
    code = script.read_text(encoding='utf8')
    # The real browser suite injects mounted decode delay/failure and verifies
    # both the retained front node and the identity of the promoted candidate.
    assert 'frame.id === this.displayVersion' in code
    assert 'this.displayVersion = version;' in code
    assert code.index('await image.decode()') < code.index('this.displayVersion = version;')
    assert 'if (version !== this.version || source !== this.src)' in code
    assert "this.$emit('load', reportedSource)" in code


def test_displayed_gif_uses_short_event_identity_and_normal_sources_restore_default():
    image = VerifiedImage()
    initial = image._props['request_id']
    data_uri = 'data:image/gif;base64,R0lGODlh'
    image.set_source(data_uri, event_source='viewer-gif-7')
    assert image._props['src'] == data_uri
    assert image._props['event_source'] == 'viewer-gif-7'
    assert image._props['request_id'] == initial + 1
    image.set_source('/media/a/0/view.png#request-8')
    assert image._props['event_source'] == ''
    assert image._props['request_id'] == initial + 2
    image.set_source('')
    assert image._props['src'] == '' and image._props['event_source'] == ''
