"""One NiceGUI element for a keyed Vue card list and its shared menu."""
from nicegui import ui


class CardGrid(ui.element, component='card_grid.js'):
    def __init__(self, on_action):
        super().__init__()
        self.props['cards'] = []
        self.props['render_epoch'] = 0
        self.props['grid_active'] = True
        self.on('card-action', on_action)

    @property
    def render_epoch(self):
        return self.props['render_epoch']

    def invalidate_actions(self):
        self.props['render_epoch'] += 1

    def retry_failed_images(self):
        self.props['retry_epoch'] = self.props.get('retry_epoch', 0) + 1


class MoreCell:
    """State of the last cell, whose button is rendered inside CardGrid."""
    def __init__(self, grid):
        self.grid = grid
        self.set_text('もっと見る')
        self.set_enabled(True)
        self.set_visibility(False)

    @property
    def visible(self):
        return self.grid.props['more_visible']

    @property
    def enabled(self):
        return self.grid.props['more_enabled']

    @property
    def is_deleted(self):
        return self.grid.is_deleted

    def set_visibility(self, value):
        self.grid.props['more_visible'] = bool(value)

    def set_enabled(self, value):
        self.grid.props['more_enabled'] = bool(value)

    def set_text(self, value):
        self.grid.props['more_text'] = value

    def enable(self):
        self.set_enabled(True)

    def disable(self):
        self.set_enabled(False)
