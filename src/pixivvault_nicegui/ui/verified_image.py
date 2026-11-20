"""Verified image load events tied to the exact requested source."""

from nicegui import ui


class VerifiedImage(ui.element, component='verified_image.js'):
    def __init__(self, source: str = '', *, alt: str = '', retain_previous: bool = False) -> None:
        super().__init__()
        self._request_id = 0
        self._props['src'] = ''
        self._props['request_id'] = self._request_id
        self._props['alt'] = alt
        self._props['retain_previous'] = retain_previous
        if source:
            self.set_source(source)

    def set_source(self, source: str, *, event_source: str | None = None,
                   retain_previous: bool | None = None, use_preload: bool = False) -> 'VerifiedImage':
        if not isinstance(source, str):
            raise TypeError('source must be a string')
        self._request_id += 1
        if retain_previous is not None:
            self._props['retain_previous'] = bool(retain_previous)
        self._props['src'] = source
        self._props['event_source'] = event_source or ''
        self._props['request_id'] = self._request_id
        self._props['preload_src'] = ''
        self._props['use_preload'] = use_preload
        self.update()
        return self

    def set_preload_source(self, source: str) -> None:
        self.props['preload_src'] = source
