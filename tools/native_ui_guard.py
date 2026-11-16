"""Guard CDP interactions with the owned native WebView and a shared deadline."""

from __future__ import annotations

import time
from urllib.parse import urlsplit


class NativeUiGuard:
    """Check the original window, local app page and time budget for each action.

    Playwright calls which accept a timeout receive the remaining total
    inspection budget. Calls such as Page.evaluate and Keyboard.press do not
    accept that argument; use with_timeout=False for those.
    """

    def __init__(self, page, app_port: int, deadline: float, check_owner) -> None:
        self.page = page
        self.app_port = app_port
        self.deadline = deadline
        self.check_owner = check_owner
        self.check()

    def check(self) -> None:
        if time.monotonic() >= self.deadline:
            raise TimeoutError("native UI inspection deadline expired")
        self.check_owner()
        if self.page.is_closed():
            raise RuntimeError("native UI page has closed")
        try:
            url = urlsplit(self.page.url)
            matches = (
                url.scheme == "http"
                and url.hostname in {"127.0.0.1", "localhost"}
                and url.port == self.app_port
                and not url.username
                and not url.password
            )
        except ValueError:
            matches = False
        if not matches:
            raise RuntimeError("native UI page left the owned loopback app")

    def run(self, action, *args, with_timeout: bool = True, **kwargs):
        self.check()
        if with_timeout:
            remaining_ms = max(1, int((self.deadline - time.monotonic()) * 1000))
            specified = kwargs.get("timeout")
            kwargs["timeout"] = (
                min(remaining_ms, specified) if specified is not None else remaining_ms
            )
        result = action(*args, **kwargs)
        self.check()
        return result

    def decode(self, image) -> dict:
        """Require the displayed image to finish decoding with actual dimensions."""
        info = self.run(image.evaluate, """async img => {
            await img.decode();
            return {
                complete: img.complete,
                naturalWidth: img.naturalWidth,
                naturalHeight: img.naturalHeight,
                src: img.currentSrc || img.src
            };
        }""")
        if (not info["complete"] or info["naturalWidth"] <= 0
                or info["naturalHeight"] <= 0):
            raise AssertionError(f"native UI image did not decode: {info}")
        try:
            source = urlsplit(info["src"])
            matches = (
                source.scheme == "http"
                and source.hostname in {"127.0.0.1", "localhost"}
                and source.port == self.app_port
            )
        except ValueError:
            matches = False
        if not matches:
            raise AssertionError("native UI image used a non-local source")
        return info
