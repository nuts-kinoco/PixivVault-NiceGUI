"""Keyboard focus shared by dialogs and controls that dismiss a surface."""
import json

from nicegui import core, ui


_STOP_SELECTOR = 'a[href],button,input,select,textarea,[tabindex],[contenteditable="true"]'
_VALID = r'''(element, scope, excluded) =>
    element instanceof HTMLElement && scope.contains(element) && !excluded?.contains(element)
    && !element.matches(':disabled')
    && !element.closest('[hidden],[inert],[aria-hidden="true"],[aria-disabled="true"]')
    && element.checkVisibility({visibilityProperty: true})'''
_RESTORE = rf'''(origin, excluded, scope = null, fallback = null) => {{
    const valid = {_VALID};
    scope ??= excluded?.closest('.q-dialog__inner, .pv-header, .pv-shell') ?? document.body;
    const controls = scope.querySelectorAll({json.dumps(_STOP_SELECTOR)});
    const tryFocus = element => {{
        if (!valid(element, scope, excluded) || element === document.body) return false;
        element.focus({{preventScroll: true}});
        return document.activeElement === element;
    }};
    if (tryFocus(origin) || tryFocus(fallback)) return;
    for (const element of controls) {{
        if (element.tabIndex >= 0 && tryFocus(element)) return;
    }}
    if (scope !== document.body && valid(scope, scope, excluded)) {{
        if (!scope.hasAttribute('tabindex')) scope.tabIndex = -1;
        tryFocus(scope);
    }}
}}'''


def bind_dialog_keys(dialog, *, cancel=None, return_focus=None):
    """Keep the real QDialog's Tab order contained and retain its opener."""
    element_id = json.dumps(dialog.html_id)
    origin = f"document.getElementById({json.dumps(return_focus.html_id)})" if return_focus is not None else 'null'
    dialog.on('before-show', js_handler='''() => {
        document.activeElement?.closest('button, [role="switch"]')?.focus({preventScroll:true});
    }''')
    dialog.on('keydown', js_handler=f'''event => {{
        if (event.key !== 'Tab' || event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return;
        const scope = document.getElementById({element_id})?.querySelector('.q-dialog__inner');
        if (!scope || !scope.contains(event.target)) return;
        const valid = {_VALID};
        const stops = [...scope.querySelectorAll({json.dumps(_STOP_SELECTOR)})]
            .filter(el => el.tabIndex >= 0 && valid(el, scope, null))
            .sort((a, b) => (a.tabIndex || Infinity) - (b.tabIndex || Infinity));
        const first = stops[0], last = stops.at(-1), active = document.activeElement;
        if (!stops.includes(active) || (event.shiftKey ? active === first : active === last)) {{
            event.preventDefault();
            (event.shiftKey ? last ?? scope : first ?? scope).focus();
        }}
    }}''')
    # Quasar restores the opening control before its portal disappears. Supply
    # a fallback when the opener was removed, disabled, or is inside a closed menu.
    dialog.on('hide', js_handler=f'''() => {{
        const closing = document.getElementById({element_id});
        requestAnimationFrame(() => {{
            const active = document.activeElement;
            const valid = {_VALID};
            const visibleModal = [...document.querySelectorAll('.q-dialog--modal')]
                .filter(el => el !== closing && valid(el, document.body, null)).at(-1);
            const scope = visibleModal?.querySelector('.q-dialog__inner') ?? document.body;
            const target = {origin};
            if (valid(target, scope, closing) || active === document.body || !valid(active, scope, closing))
                ({_RESTORE})(target, closing, scope, scope.querySelector('[data-testid="preferences-open"]'));
        }});
    }}''')
    if cancel is not None:
        dialog.on('keydown.escape.stop.prevent', cancel)
    return dialog


def managed_dialog(*, cancel=None, return_focus=None):
    return bind_dialog_keys(ui.dialog(), cancel=cancel, return_focus=return_focus)


def dismiss_focus_js(surface, *, return_focus=None, fallback=None, payload=None, defer=False):
    """Prepare focus return for the click and optionally wait for committed dismissal."""
    origin = (f'document.getElementById({json.dumps(return_focus.html_id)})'
              if return_focus is not None else 'surface?.pvFocusOrigin')
    apply = 'if (surface) surface.pvRestoreAfterDismiss = restore;' if defer else 'restore();'
    return f'''() => {{
        const surface = document.getElementById({json.dumps(surface.html_id)});
        const captured = {origin};
        const restore = () => {{
            const active = document.activeElement;
            if (surface?.contains(active) || active === document.body) {{
                const modal = surface?.closest('.q-dialog__inner');
                const scope = modal ?? surface?.closest('.pv-header, .pv-shell') ?? document.body;
                ({_RESTORE})(captured, surface, scope, scope.querySelector({json.dumps(fallback or '[data-testid="preferences-open"]')}));
            }}
        }};
        {apply}
        emit({payload or ''});
    }}'''


def restore_dismissed_focus(surface):
    if surface.client.has_socket_connection:
        surface.client.run_javascript(f'''requestAnimationFrame(() => {{
            const surface = document.getElementById({json.dumps(surface.html_id)});
            surface?.pvRestoreAfterDismiss?.();
            if (surface) delete surface.pvRestoreAfterDismiss;
        }})''')


def remember_focus(surface):
    if surface.client.has_socket_connection:
        surface.client.run_javascript(f'''(() => {{
            const origin = document.activeElement;
            requestAnimationFrame(() => {{
                const surface = document.getElementById({json.dumps(surface.html_id)});
                if (surface) surface.pvFocusOrigin = origin;
            }});
        }})()''')


def toast_dismiss_action(return_focus=None):
    target = (f'document.getElementById({json.dumps(return_focus.html_id)})'
              if return_focus is not None else 'document.activeElement')
    # This expression runs when NiceGUI mounts the Quasar notification.
    return f'''(() => {{
        const origin = {target};
        return () => {{
            const surface = document.activeElement?.closest('.q-notification');
            if (!surface && document.activeElement !== document.body) return;
            const visibleDialogs = [...document.querySelectorAll('.q-dialog--modal .q-dialog__inner')];
            const scope = visibleDialogs.filter(el => el.checkVisibility({{visibilityProperty:true}})).at(-1)
                ?? document.body;
            ({_RESTORE})(origin, surface, scope, scope.querySelector('[data-testid="preferences-open"]'));
        }};
    }})()'''


def focus_after_dialog(client, *selectors):
    if core.loop is None:
        return
    client.run_javascript(f'''requestAnimationFrame(() => {{
        const valid = {_VALID};
        const modal = [...document.querySelectorAll('.q-dialog--modal .q-dialog__inner')]
            .filter(el => valid(el, document.body, null)).at(-1);
        const scope = modal ?? document.body;
        for (const selector of {json.dumps(selectors)}) {{
            const target = scope.querySelector(selector);
            if (valid(target, scope, null)) {{
                target.focus({{preventScroll:true}});
                if (document.activeElement === target) break;
            }}
        }}
    }})''')
