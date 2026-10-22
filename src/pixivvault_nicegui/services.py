"""アプリ単位の資源。画面の再接続では作り直したり閉じたりしない。"""

from __future__ import annotations

from threading import Lock

from .fixtures import generate_demo_artworks
from .media import DemoImageCatalog


class ApplicationServices:
    def __init__(self, count: int = 120) -> None:
        self.artworks = generate_demo_artworks(count)
        self.images = DemoImageCatalog(self.artworks)
        self.closed = False
        self._close_lock = Lock()

    def close(self) -> None:
        with self._close_lock:
            if not self.closed:
                self.images.close()
                self.closed = True


_services: ApplicationServices | None = None
_lock = Lock()
_APP_KEY = "_pixivvault_application_services"


def get_services(app=None) -> ApplicationServices:
    """Return resources scoped to the ASGI app; no-argument access is legacy."""
    global _services
    with _lock:
        if app is None:
            if _services is None:
                _services = ApplicationServices()
            services = _services
        else:
            services = getattr(app.state, _APP_KEY, None)
            if services is None:
                services = ApplicationServices()
                setattr(app.state, _APP_KEY, services)
        if services.closed:
            raise RuntimeError("application services are closed")
        return services
