"""同一clientの閲覧画面で使う、原版順のカードサイズ操作。"""

from nicegui import ui

from .grid_layout import MAX_COLUMNS


def size_buttons(on_change, *, prefix: str) -> dict[str, ui.button]:
    result = {}
    for label, size, icon in (("大", "large", "view_module"),
                              ("中", "medium", "view_comfy"),
                              ("小", "small", "apps")):
        button = ui.button(label, icon=icon, color=None,
                           on_click=lambda _, value=size: on_change(value))
        button.props(f"flat dense stack no-caps aria-label={label} aria-pressed=false "
                     f"data-testid={prefix}-size-{size}").classes("pv-card-size pv-size-button")
        result[size] = button
    return result


def update_size_buttons(buttons: dict, size: str) -> None:
    if size not in MAX_COLUMNS:
        raise ValueError("invalid card size")
    for value, button in buttons.items():
        selected = value == size
        button.classes(add="pv-size-selected" if selected else "pv-size-button",
                       remove="pv-size-button" if selected else "pv-size-selected")
        button.props(f"aria-pressed={'true' if selected else 'false'}")
