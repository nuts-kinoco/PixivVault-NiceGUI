"""Manual toolbar shared by views that keep their main actions visible."""
from nicegui import ui


def manual_toolbar(parent, key, controls, *, opened=False, index=-1, title='絞り込み'):
    with parent:
        with ui.element('details').classes('pv-manual-toolbar').props(
                f'data-testid=toolbar-{key} data-pv-toolbar-key={key}') as details:
            with ui.element('summary').classes('pv-hit28 pv-focus-ring').props(
                    f'tabindex=0 data-testid=toolbar-toggle-{key}') as toggle:
                ui.label(f'{title}を開く').classes('pv-expand-label')
                ui.label(f'{title}を畳む').classes('pv-collapse-label')
            panel = ui.row().classes('pv-manual-panel w-full').props(f'data-testid=toolbar-controls-{key}')
            toggle.props['aria-controls'] = panel.html_id
            if opened:
                details.props['open'] = True
    details.move(parent, target_index=index)
    for control in controls:
        control.move(panel)
    return details, toggle, panel
