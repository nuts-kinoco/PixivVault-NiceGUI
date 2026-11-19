from test_native_webview_visibility_20261114 import form
from pixivvault_nicegui.native_restore_size import bind_form_restore_size


def test_restore_resizes_only_the_owned_form():
    owned=form(); other=form(); owned._scale=1.0
    bind_form_restore_size(owned,'normal','minimized','maximized',lambda w,h:(w,h))
    owned.WindowState='minimized'; owned.Resize.fire()
    owned.WindowState='normal'; owned.Resize.fire()
    assert owned.Size == (1280,720)
    assert not hasattr(other,'Size')
