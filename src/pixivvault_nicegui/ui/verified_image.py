"""Verified image load events tied to the exact requested source."""

from nicegui import ui


class VerifiedImage(ui.element, component='verified_image.js'):
    def __init__(self, source: str = '', *, alt: str = '') -> None:
        super().__init__()
        self._request_id = 0
        self._props['src'] = ''
        self._props['request_id'] = self._request_id
        self._props['alt'] = alt
        if source:
            self.set_source(source)

    def set_source(self, source: str) -> 'VerifiedImage':
        if not isinstance(source, str):
            raise TypeError('source must be a string')
        self._request_id += 1
        self._props['src'] = source
        self._props['request_id'] = self._request_id
        self.update()
        return self
