"""Shared keyboard containment and focus recovery for application overlays."""
import json
from pathlib import Path

from nicegui import core, ui


_RECOVERY = Path(__file__).with_name('focus_recovery.js').read_text(encoding='utf-8')


def install_focus_recovery(client):
    if getattr(client, '_pv_focus_recovery', False):
        return
    client._pv_focus_recovery = True
    if client.has_socket_connection:
        client.run_javascript(_RECOVERY)
    else:
        with client:
            ui.add_head_html(f'<script>{_RECOVERY}</script>')


def focus_region(surface, return_focus=None):
    """Identify a disappearing region and, when known, its explicit opener."""
    install_focus_recovery(surface.client)
    surface.props['data-pv-focus-region'] = 'true'
    if return_focus is not None:
        surface.props['data-pv-focus-origin'] = return_focus.html_id


def bind_dialog_keys(dialog, *, cancel=None, return_focus=None):
    """Keep the real QDialog's Tab order contained and retain its opener."""
    install_focus_recovery(dialog.client)
    element_id = json.dumps(dialog.html_id)
    origin = f"document.getElementById({json.dumps(return_focus.html_id)})" if return_focus is not None else 'null'
    dialog.on('before-show', js_handler='''() => {
        document.activeElement?.closest('button, [role="switch"]')?.focus({preventScroll:true});
    }''')
    dialog.on('keydown', js_handler=f'''event => {{
        if (event.key !== 'Tab' || event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return;
        const scope = document.getElementById({element_id})?.querySelector('.q-dialog__inner');
        if (!scope || !scope.contains(event.target)) return;
        const stops = window.pvFocus.stops(scope).sort((a, b) => (a.tabIndex || Infinity) - (b.tabIndex || Infinity));
        const first = stops[0], last = stops.at(-1), active = document.activeElement;
        if (!stops.includes(active) || (event.shiftKey ? active === first : active === last)) {{
            event.preventDefault();
            (event.shiftKey ? last ?? scope : first ?? scope).focus();
        }}
    }}''')
    # Quasar restores the opener before its portal disappears. Supply a
    # fallback if that opener has gone away, or use the explicit opener.
    dialog.on('hide', js_handler=f'''() => {{
        const closing = document.getElementById({element_id});
        requestAnimationFrame(() => {{
            const origin = {origin};
            if (origin || document.activeElement === document.body)
                window.pvFocus.restore({{origin, excluded: closing, near: null}});
        }});
    }}''')
    if cancel is not None:
        dialog.on('keydown.escape.stop.prevent', cancel)
    return dialog


def managed_dialog(*, cancel=None, return_focus=None):
    return bind_dialog_keys(ui.dialog(), cancel=cancel, return_focus=return_focus)


def dismiss_focus_js(surface, *, return_focus=None, fallback=None):
    """Use the same recovery order for explicit closes and DOM updates."""
    focus_region(surface, return_focus)
    return f'''() => {{
        const surface = document.getElementById({json.dumps(surface.html_id)});
        const active = document.activeElement;
        if (surface?.contains(active) || active === document.body) {{
            const near = surface?.parentElement?.closest('.q-dialog__inner,.pv-content,.pv-header,.pv-shell');
            const fallback = {json.dumps(fallback)};
            window.pvFocus.restore({{origin: window.pvFocus.originOf(surface), excluded: surface, near,
                                    alternatives: fallback ? [document.querySelector(fallback)] : []}});
        }}
        emit();
    }}'''


def remember_focus(surface):
    if surface.client.has_socket_connection:
        surface.client.run_javascript(f'''(() => {{
            const origin = document.activeElement;
            requestAnimationFrame(() => {{
                const surface = document.getElementById({json.dumps(surface.html_id)});
                if (surface && !surface.contains(origin)) surface.pvFocusOrigin = origin;
            }});
        }})()''')


def toast_dismiss_action(return_focus=None):
    target = (f'document.getElementById({json.dumps(return_focus.html_id)})'
              if return_focus is not None else 'document.activeElement')
    return f'''(() => {{
        const origin = {target};
        return () => {{
            const surface = document.activeElement?.closest('.q-notification');
            if (surface || document.activeElement === document.body)
                window.pvFocus.restore({{origin, excluded: surface, near: origin?.parentElement}});
        }};
    }})()'''


def focus_after_dialog(client, *selectors):
    if core.loop is None:
        return
    install_focus_recovery(client)
    client.run_javascript(f'''requestAnimationFrame(() => {{
        const alternatives = {json.dumps(selectors)}.flatMap(selector => [...document.querySelectorAll(selector)]);
        window.pvFocus.restore({{alternatives, near: null}});
    }})''')
