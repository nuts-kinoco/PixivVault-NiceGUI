"""Keep the owned WinForms WebView2 control visible after native restore.

No navigation or DOM reconstruction: WinForms Visible forwards to the
WebView2 controller. All changes run on the form's UI thread.
"""
import sys
import logging


def bind_form_visibility(form, minimized):
    """Bind once on the UI thread, retaining this exact existing WebView."""
    control = form.webview

    def sync(*_):
        if not form.IsDisposed and not control.IsDisposed:
            control.Visible = bool(form.Visible and form.WindowState != minimized)

    def closed(*_):
        form.Resize -= sync
        form.VisibleChanged -= sync
        form.FormClosed -= closed

    form.Resize += sync
    form.VisibleChanged += sync
    form.FormClosed += closed
    sync()
    return sync


def install_owned_visibility(window):
    """Called in the native child startup hook, before or after form creation."""
    if (sys.platform != 'win32' or getattr(window, 'uid', None) != 'master'
            or getattr(getattr(window, 'gui', None), 'renderer', None) != 'edgechromium'
            or hasattr(window, '_pixivvault_visibility_installed')):
        return
    event = getattr(window.events, 'before_show', None)
    if event is None:
        return
    window._pixivvault_visibility_installed = False

    def bind():
        if window._pixivvault_visibility_installed:
            return
        form = window.native
        if form is None or form.IsDisposed:
            return
        from System.Windows.Forms import FormWindowState
        bind_form_visibility(form, FormWindowState.Minimized)
        window._pixivvault_visibility_installed = True

    def before_show():
        try:
            bind()
        except Exception:
            logging.getLogger(__name__).warning('Native WebView visibility hook unavailable')

    window.events.before_show += before_show
    form = getattr(window, 'native', None)
    if form is not None and not form.IsDisposed and form.IsHandleCreated:
        try:
            from System import Action
            form.Invoke(Action(before_show))
        except Exception:
            logging.getLogger(__name__).warning('Native WebView visibility hook unavailable')
