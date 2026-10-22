"""アプリ単位の資源。画面の再接続では作り直したり閉じたりしない。"""

from __future__ import annotations

from pathlib import Path
from threading import Lock

from .fixtures import generate_demo_artworks
from .media import DemoImageCatalog


class ApplicationServices:
    def __init__(self, count: int = 120, *, learning_sandbox: Path | None = None) -> None:
        self.artworks = generate_demo_artworks(count)
        self.images = DemoImageCatalog(self.artworks)
        self.learning_sandbox = learning_sandbox
        self.learning = None
        if learning_sandbox is not None:
            from .learning_service import LearningService
            try:
                self.learning = LearningService(learning_sandbox, self.artworks)
            except Exception:
                self.images.close()
                raise
        self.closing = False
        self.closed = False
        self._close_lock = Lock()

    def close(self) -> None:
        with self._close_lock:
            if not self.closed:
                self.closing = True
                try:
                    if self.learning is not None:
                        self.learning.close()
                finally:
                    self.images.close()
                    self.closed = True


_services: ApplicationServices | None = None
_lock = Lock()
_APP_KEY = "_pixivvault_application_services"


def get_services(app=None, *, learning_sandbox: Path | None = None) -> ApplicationServices:
    """Return resources scoped to the ASGI app; no-argument access is legacy."""
    global _services
    with _lock:
        if app is None:
            if _services is None:
                _services = ApplicationServices(learning_sandbox=learning_sandbox)
            services = _services
        else:
            services = getattr(app.state, _APP_KEY, None)
            if services is None:
                services = ApplicationServices(learning_sandbox=learning_sandbox)
                setattr(app.state, _APP_KEY, services)
        if services.closing:
            raise RuntimeError("application services are closed")
        if learning_sandbox is not None and services.learning_sandbox != learning_sandbox:
            raise RuntimeError("application services already use a different learning storage")
        return services
