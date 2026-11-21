"""Install measurement taps below the applications' ranking and STOP paths."""

import base64
import json
from urllib.parse import urlsplit

from live_gate import Ledger, RouteDenied, SocketFence, route
from timing import StopRun


class RankingWire:
    def __init__(self, root, side, cookie_path, *, fake=False):
        from pixivvault_nicegui.pixiv_read_client import _read_cookies

        _, self.claimed = _read_cookies(cookie_path)
        if self.claimed is None:
            raise StopRun("authentication")
        self.ledger = Ledger(root)
        self.side = side
        self.fake = fake
        self.fence = SocketFence()
        self.image = None
        self.image_bytes = None
        self.fence.install()

    def kind(self, url, method, cookie):
        value = route(url, method, bool(cookie))
        if value == "image" and url != self.image:
            raise RouteDenied()
        return value

    def inspect(self, kind, status, body):
        from pixivvault_nicegui.pixiv_read_client import _identity_from_home, PixivReadError

        if status != 200:
            return status, False, None
        prefix = body[:262144].lower()
        challenge = any(marker in prefix for marker in (
            b'cf-chl-', b'/cdn-cgi/challenge-platform/', b'id="captcha-container"',
            b'<title>just a moment', b'<title>access denied',
        ))
        if challenge:
            return status, True, None
        if kind == "identity":
            try:
                identity = _identity_from_home(body)
            except PixivReadError:
                return status, False, False
            return status, False, identity == self.claimed
        if kind == "image":
            if prefix.lstrip().startswith((b"<!doctype html", b"<html")):
                raise StopRun("transport")
            if self.side == "original":
                self.image_bytes = base64.b64encode(body).decode("ascii")
            return status, False, None
        try:
            document = json.loads(body)
        except (UnicodeError, ValueError):
            return status, False, False
        if not isinstance(document, dict) or document.get("error"):
            return status, False, False
        if kind == "identity_extra":
            values = document.get("body")
            if document.get("error") is not False or not isinstance(values, dict):
                return status, False, False
            user = values.get("user", {})
            extracts = (values, user) if isinstance(user, dict) else (values,)
            return status, False, all(str(value[k]) == self.claimed
                                       for value in extracts for k in ("id", "userId") if k in value)
        items = document.get("contents")
        if not isinstance(items, list) or not items or not isinstance(items[0], dict):
            raise StopRun("transport")
        thumbnail = items[0].get("url")
        if not isinstance(thumbnail, str) or route(thumbnail, "GET", False) != "image":
            raise StopRun("destination")
        if thumbnail != self.image:
            self.image_bytes = None
            self.image = thumbnail
        return status, False, None

    def install_original(self, product_check):
        import requests

        send = requests.adapters.HTTPAdapter.send
        if self.fake:
            from fake_wire import original_response
            send = original_response
        setup = requests.sessions.Session.__init__

        def session_setup(session, *args, **kwargs):
            setup(session, *args, **kwargs)
            session.trust_env = False

        def measured(adapter, request, *args, **kwargs):
            kind = self.kind(request.url, request.method, request.headers.get("Cookie"))
            response = None

            def check_gate():
                # Check after the ledger's pacing wait. Any inability to
                # snapshot the isolation gate fails closed before socket I/O.
                try:
                    product_check()
                except Exception:
                    raise StopRun("product_stop") from None

            def dispatch():
                nonlocal response
                with self.fence.admitted(None if self.fake else urlsplit(request.url).hostname):
                    response = send(adapter, request, *args, **kwargs)
                    if response.status_code != 200:
                        response.close()
                        return response.status_code, False, None
                    chunks = bytearray()
                    for piece in response.iter_content(chunk_size=65536):
                        chunks.extend(piece)
                        if len(chunks) > 8 * 1024 * 1024:
                            raise StopRun("transport")
                    response._content = bytes(chunks)
                    response._content_consumed = True
                return self.inspect(kind, response.status_code, response.content)

            try:
                self.ledger.attempt(self.side, kind, dispatch, before_dispatch=check_gate)
            except Exception:
                if response is not None:
                    response.close()
                raise
            return response

        requests.sessions.Session.__init__ = session_setup
        requests.adapters.HTTPAdapter.send = measured

    def install_nice(self):
        from pixivvault_nicegui.managed_network_stop_gate import (
            ManagedNetworkGateBusy, ManagedNetworkGateStorageError, ManagedNetworkStopped,
        )
        from pixivvault_nicegui.managed_network_stop_shared_transport import (
            SharedProfileStopTransport, _DraftAuthSharedTransport,
        )
        from pixivvault_nicegui.native_startup import NativeProfileBinding
        from pixivvault_nicegui.pixiv_read_client import (
            DestinationRejected, HTTPStatusRejected, LivePixivReadClient,
            ResponseRejected, _OwnedReadTransport,
        )

        send = _OwnedReadTransport.__call__
        fake = getattr(self, "fake", False)
        if fake:
            from fake_wire import nice_response
            send = nice_response
        read_image = LivePixivReadClient.get_image_bytes
        verify = NativeProfileBinding._verify_auth_result

        def report_product_stop(send_with_gate):
            def observed(transport, *args):
                try:
                    return send_with_gate(transport, *args)
                except ManagedNetworkGateBusy:
                    raise
                except (ManagedNetworkGateStorageError, ManagedNetworkStopped):
                    self.ledger.halt("product_stop")
                    raise
            return observed

        def observed_verify(binding, *args):
            result = verify(binding, *args)
            if result.code in ("uncertain", "stopped", "local_unavailable", "owner_absent"):
                self.ledger.halt("product_stop")
            if result.code in ("identity_unverified", "cookie_unreadable"):
                self.ledger.halt("authentication")
            return result

        def selected_image(client, url):
            if url != self.image:
                # Filter the workload before the product records GET intent.
                raise DestinationRejected()
            return read_image(client, url)

        def measured(transport, request, timeout, limit, deadline):
            try:
                kind = self.kind(request.full_url, request.get_method(), request.get_header("Cookie"))
            except RouteDenied:
                # No wire dispatch occurred. The product recognizes a typed
                # destination refusal as a settled no-result outcome.
                raise DestinationRejected() from None
            result = error = None

            def dispatch():
                nonlocal result, error
                with self.fence.admitted(None if fake else urlsplit(request.full_url).hostname):
                    try:
                        result = send(transport, request, timeout, limit, deadline)
                    except HTTPStatusRejected as exc:
                        error = exc
                        return exc.status_code, False, None
                body = result if isinstance(result, bytes) else result.body
                return self.inspect(kind, 200, body)

            try:
                self.ledger.attempt(self.side, kind, dispatch, deadline=deadline)
            except Exception as failure:
                if error is not None:
                    # The real gate must see its normal typed HTTP error.
                    raise error from None
                if isinstance(failure, StopRun):
                    raise ResponseRejected() from None
                raise
            return result

        LivePixivReadClient.get_image_bytes = selected_image
        _OwnedReadTransport.__call__ = measured
        SharedProfileStopTransport.__call__ = report_product_stop(SharedProfileStopTransport.__call__)
        _DraftAuthSharedTransport.__call__ = report_product_stop(_DraftAuthSharedTransport.__call__)
        NativeProfileBinding._verify_auth_result = observed_verify
