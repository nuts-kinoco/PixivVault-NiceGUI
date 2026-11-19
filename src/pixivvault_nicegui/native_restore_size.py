"""Resize only the owned WinForms window during a real restore transition."""
import logging
import sys
from threading import Lock


def bind_form_restore_size(form, normal, minimized, maximized, make_size):
    """Run on the form UI thread; never show or activate a window."""
    previous = [form.WindowState]

    def resized(*_):
        if form.IsDisposed:
            return
        state = form.WindowState
        old = previous[0]
        # Set first: changing Size synchronously fires another Resize event.
        previous[0] = state
        if old in (minimized, maximized) and state == normal and form.Visible:
            scale = form._scale
            form.Size = make_size(int(1280 * scale), int(720 * scale))

    def closed(*_):
        form.Resize -= resized
        form.FormClosed -= closed

    form.Resize += resized
    form.FormClosed += closed
    return resized


def install_window_restore_size(window):
    """Bind exactly this master form, before-show or via its UI-thread Invoke."""
    if (sys.platform != 'win32' or getattr(window, 'uid', None) != 'master'
            or getattr(getattr(window, 'gui', None), 'renderer', None) != 'edgechromium'
            or hasattr(window, '_pixivvault_restore_size_installed')):
        return
    before_show = getattr(window.events, 'before_show', None)
    if before_show is None:
        return
    window._pixivvault_restore_size_installed = False
    lock = Lock()

    def bind():
        # before_show is synchronous on the UI thread in pywebview WinForms.
        with lock:
            if window._pixivvault_restore_size_installed:
                return
            form = window.native
            if form is None or form.IsDisposed:
                return
            from System.Drawing import Size
            from System.Windows.Forms import FormWindowState
            bind_form_restore_size(form, FormWindowState.Normal, FormWindowState.Minimized,
                                   FormWindowState.Maximized, Size)
            window._pixivvault_restore_size_installed = True

    def safe_bind():
        try:
            bind()
        except Exception:
            logging.getLogger(__name__).warning('Native restore size hook unavailable')

    window.events.before_show += safe_bind
    form = getattr(window, 'native', None)
    if form is not None and not form.IsDisposed and form.IsHandleCreated:
        try:
            from System import Action
            form.Invoke(Action(safe_bind))
        except Exception:
            logging.getLogger(__name__).warning('Native restore size hook unavailable')


def install_owned_restore_size():
    """Standalone child startup hook when the optional tray is unavailable."""
    import webview
    windows = tuple(webview.windows)
    if len(windows) == 1 and getattr(windows[0], 'uid', None) == 'master':
        from .native_webview_visibility import install_owned_visibility
        install_owned_visibility(windows[0])
        install_window_restore_size(windows[0])


def configure_owned_restore_size(native):
    args = getattr(native, 'start_args', None)
    if not isinstance(args, dict):
        return False
    if args.get('func') is install_owned_restore_size and args.get('args') == ():
        return True
    if 'func' in args or 'args' in args:
        return False
    args.update(func=install_owned_restore_size, args=())
    return True
