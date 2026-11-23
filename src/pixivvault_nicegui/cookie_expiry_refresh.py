"""Serialized response-cookie expiry renewal for one owned native profile."""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
from email.utils import mktime_tz, parsedate_tz
import os
from http.cookies import CookieError, SimpleCookie
import re
import time
from urllib.parse import urlsplit
from threading import RLock
from urllib.request import Request

from .extension_cookie_store import (
    COOKIE_LIMIT, CookieSyncError, CookieSyncBusy, CookieSyncCapacityExceeded, CookieSyncCommitUnconfirmed,
    _MAX_EXPIRY, _canonical, _cookie_octet_value, _identity, _key, _parse_existing, _read_stable,
)
from .pixiv_read_client import CookieSourceRejected, _SESSION_ID, _read_cookies


class CookieExpiryCoordinator:
    """One proof and lock shared by all live readers of a profile generation."""
    def __init__(self, profile, store_factory):
        self.lock = RLock()
        self._profile = profile
        self._store_factory = store_factory
        self._readers = []
        self._closed = False
        self._adopted = False
        self._identity = None
        try:
            self._snapshot = profile.snapshot()
            self._initial_cookie = self._snapshot.cookie_file
            path = _canonical(self._snapshot.cookie_file, file=True)
            raw, self._proof = _read_stable(path, COOKIE_LIMIT)
            try:
                self._rows = _parse_existing(raw)
            except (CookieSyncError, UnicodeError, ValueError):
                # Preserve the established reader for other Mozilla-compatible
                # syntaxes, but never rewrite a source we cannot fully parse.
                import logging
                logging.getLogger(__name__).warning('Cookie expiry renewal unavailable: unsupported format')
                self._rows = None
            if profile.snapshot() != self._snapshot:
                raise CookieSourceRejected()
        except Exception:
            raise CookieSourceRejected() from None

    def _check(self):
        if self._closed:
            raise CookieSourceRejected()
        try:
            if self._profile.snapshot() != self._snapshot:
                raise CookieSourceRejected()
            path = _canonical(self._snapshot.cookie_file, file=True)
            _raw, proof = _read_stable(path, COOKIE_LIMIT)
            if proof != self._proof:
                raise CookieSourceRejected()
        except Exception:
            self._closed = True
            raise CookieSourceRejected() from None

    def register(self, reader):
        with self.lock:
            self._check()
            if reader._expiry_coordinator is not None or reader._closed:
                raise CookieSourceRejected()
            jar, user_id = _read_cookies(self._snapshot.cookie_file)
            self._check()
            if reader._cookie_user_id is not None and user_id != reader._cookie_user_id:
                raise CookieSourceRejected()
            if reader._verified_identity is not None:
                self.bind_identity(reader._verified_identity)
            reader._cookies, reader._cookie_user_id = jar, user_id
            reader._expiry_coordinator = self
            self._readers.append(reader)

    def bind_identity(self, identity):
        if (type(identity) is not str or re.fullmatch(r'[1-9][0-9]{0,19}', identity) is None
                or self._identity not in (None, identity)):
            self._closed = True
            raise CookieSourceRejected()
        self._identity = identity

    def ensure_current(self, reader):
        self._check()
        if reader not in self._readers or reader._closed:
            raise CookieSourceRejected()

    def ensure_post_owner(self, reader):
        """Recheck ownership after a reentrant dispatch callback."""
        if (self._closed or reader._closed or reader not in self._readers
                or reader._expiry_coordinator is not self):
            raise CookieSourceRejected()

    @contextmanager
    def post_fence(self, reader, generation):
        """Keep the selected profile and Cookie pinned throughout one POST."""
        from .frozen_source_pin import _open_held
        if os.name != 'nt':
            raise CookieSourceRejected()
        if self._closed or reader not in self._readers or reader._closed:
            raise CookieSourceRejected()
        descriptor = None
        with self._profile.pinned_snapshot() as snapshot:
            try:
                if snapshot != self._snapshot or generation != (self._snapshot, self._proof):
                    raise CookieSourceRejected()
                path = _canonical(snapshot.cookie_file, file=True)
                descriptor = _open_held(path)
                if _identity(os.fstat(descriptor)) != self._proof.identity:
                    raise CookieSourceRejected()
                _, proof = _read_stable(path, COOKIE_LIMIT)
                if proof != self._proof or _identity(os.fstat(descriptor)) != proof.identity:
                    raise CookieSourceRejected()
            except Exception:
                if descriptor is not None:
                    os.close(descriptor)
                raise CookieSourceRejected() from None
            try:
                yield
            finally:
                import sys
                from .pixiv_read_client import HTTPStatusRejected
                active = sys.exc_info()[1]
                try:
                    os.close(descriptor)
                except Exception:
                    self._closed = True
                    if isinstance(active, HTTPStatusRejected):
                        active.cleanup_failed = True
                    else:
                        raise

    @contextmanager
    def _commit_admission(self, reader):
        from .managed_network_stop_profile_scope import _ProfileBoundTransport, _require_selected_cookie
        from .managed_network_stop_shared_transport import SharedProfileStopTransport
        from .managed_network_stop_transport import ManagedNetworkStopTransport
        transport = reader._transport
        with ExitStack() as stack:
            if isinstance(transport, _ProfileBoundTransport):
                _require_selected_cookie(transport.profile_root, transport.cookie_path, transport.profile_identity)
            if isinstance(transport, SharedProfileStopTransport):
                if transport.shared_owner_check is not None:
                    transport.shared_owner_check()
                    owner = getattr(transport.shared_owner_check, '__self__', None)
                    if owner is not None:
                        from .managed_network_stop_startup import selected_history_guard
                        stack.enter_context(selected_history_guard(owner))
                stack.enter_context(transport.gate.request())
                stack.enter_context(transport.shared_gate.request())
            elif isinstance(transport, ManagedNetworkStopTransport):
                stack.enter_context(transport.gate.request())
            yield

    def observe(self, reader, url, values, sent_cookies):
        self.ensure_current(reader)
        identity = reader._verified_identity
        if identity is None or self._rows is None:
            return
        self.bind_identity(identity)
        if any(item._verified_identity not in (None, identity) for item in self._readers if not item._closed):
            self._closed = True
            raise CookieSourceRejected()
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.netloc != 'www.pixiv.net':
            return
        host = parsed.hostname
        request_path = parsed.path or '/'
        default_path = request_path.rsplit('/', 1)[0] or '/'
        sent = {(domain, path, name, value) for domain, path, name, value in sent_cookies}
        updates = {}
        seen = set()
        for value in values:
            if any(ord(char) < 32 or ord(char) == 127 for char in value):
                continue
            try:
                parsed_cookie = SimpleCookie()
                parsed_cookie.load(value)
                if len(parsed_cookie) != 1:
                    continue
                morsel = next(iter(parsed_cookie.values()))
                domain_attr = morsel['domain'].lower()
                if domain_attr:
                    domain_host = domain_attr.lstrip('.')
                    if not domain_host or not (host == domain_host or host.endswith('.' + domain_host)):
                        continue
                    domain = '.' + domain_host
                else:
                    domain = host
                path = morsel['path'] or default_path
                key = _key(domain, path, morsel.key)
                if key in seen:
                    updates.pop(key, None)
                    continue
                seen.add(key)
                old = self._rows.get(key)
                if old is None or (key[0], key[1], key[2], old[0]) not in sent:
                    continue
                age = morsel['max-age']
                if age:
                    if re.fullmatch(r'[0-9]{1,12}', age) is None or int(age) <= 0:
                        continue
                    expiration = int(time.time()) + int(age)
                else:
                    parsed_date = parsedate_tz(morsel['expires']) if morsel['expires'] else None
                    if parsed_date is None:
                        continue
                    expiration = int(mktime_tz(parsed_date))
                secure = bool(morsel['secure'])
                if (expiration > _MAX_EXPIRY or expiration <= int(time.time())
                        or (old[2] is not None and expiration <= old[2]) or secure != old[1]):
                    continue
                if len(morsel.value) > 16384:
                    continue
                if morsel.value != old[0]:
                    if not _cookie_octet_value(morsel.value):
                        continue
                    if morsel.key == 'PHPSESSID':
                        claim = _SESSION_ID.fullmatch(morsel.value or '')
                        if claim is None or claim.group(1) != identity:
                            continue
                    else:
                        claims = (_SESSION_ID.fullmatch(sent_value) for _domain, _path, name, sent_value in sent
                                  if name == 'PHPSESSID')
                        if not any(claim is not None and claim.group(1) == identity for claim in claims):
                            continue
                updates[key] = (morsel.value, secure, expiration, bool(morsel['httponly']))
            except (CookieError, CookieSyncError, TypeError, ValueError, OverflowError):
                continue
        if not updates:
            return
        store = None
        candidate = None
        attempted = False
        try:
            self._check()
            store = self._store_factory()
            candidate = store.prepare_expiry_extensions(updates, expected_snapshot=self._snapshot,
                expected_proof=self._proof, verified_identity=identity)
            if candidate is None:
                return
            jar, claimed = _read_cookies(candidate.cookie_file)
            if claimed is not None and claimed != identity:
                store.discard(candidate)
                raise CookieSourceRejected()
            # Never adopt a generation that would make a later authenticated
            # GET exceed the established Cookie-header limit. Test each
            # relevant path locally; this does not dispatch a request.
            try:
                paths = {request_path} | {cookie.path for cookie in jar if cookie.path.startswith('/')}
                for path in paths:
                    probe = Request('https://www.pixiv.net' + path)
                    jar.add_cookie_header(probe)
                    header = probe.get_header('Cookie') or ''
                    if len(header) > 16384 or any(ord(char) < 32 or ord(char) == 127 for char in header):
                        store.discard(candidate)
                        return
            except Exception:
                store.discard(candidate)
                return
            with self._commit_admission(reader):
                with reader._expiry_cancel_lock:
                    from .request_policy import request_checkpoint
                    request_checkpoint()
                    reader._ensure_open()
                    self._check()
                    attempted = True
                    result = store.commit(candidate)
            from .frozen_source_pin import _open_held
            with ExitStack() as pins:
                cookie_fd = _open_held(result.cookie_file)
                pins.callback(os.close, cookie_fd)
                metadata_path = result.cookie_file.parent / 'generation.json'
                metadata_fd = _open_held(metadata_path)
                pins.callback(os.close, metadata_fd)
                snapshot, proof, rows = store.expiry_source()
                _, metadata_proof = _read_stable(metadata_path, 2048)
                if (snapshot.cookie_file != result.cookie_file or snapshot.revision != result.profile_revision
                        or proof.sha256 != result.sha256 or metadata_proof.sha256 != result.metadata_sha256
                        or _identity(os.fstat(cookie_fd)) != proof.identity
                        or _identity(os.fstat(metadata_fd)) != metadata_proof.identity):
                    raise CookieSourceRejected()
                from .managed_network_stop_profile_scope import _ProfileBoundTransport
                self._snapshot, self._proof, self._rows = snapshot, proof, rows
                self._adopted = True
                for item in self._readers:
                    if item._closed:
                        continue
                    with item._expiry_cancel_lock:
                        if item._closed:
                            continue
                        fresh, user_id = _read_cookies(result.cookie_file)
                        if user_id is not None and user_id != identity:
                            raise CookieSourceRejected()
                        item._cookies, item._cookie_user_id = fresh, user_id
                        if isinstance(item._transport, _ProfileBoundTransport):
                            item._transport.cookie_path = result.cookie_file
                            item.set_retry_policy(item._retry_policy)
        except (CookieSyncBusy, CookieSyncCapacityExceeded):
            if not attempted:
                return
            self._closed = True
            raise CookieSourceRejected() from None
        except CookieSyncCommitUnconfirmed:
            self._closed = True
            raise CookieSourceRejected() from None
        except Exception as error:
            from .job_service import JobCancelled
            from .managed_network_stop_gate import ManagedNetworkStopped, ManagedNetworkGateBusy
            from .pixiv_read_client import ClientClosed
            if not attempted and store is not None and candidate is not None:
                store.discard(candidate)
            if isinstance(error, ManagedNetworkGateBusy) and not attempted:
                return
            if isinstance(error, (ManagedNetworkStopped, JobCancelled, ClientClosed)):
                raise
            self._closed = True
            raise CookieSourceRejected() from None

    def generation_token(self, reader):
        self.ensure_current(reader)
        return self._snapshot, self._proof

    def recognizes_renewal(self, snapshot, launch_cookie):
        with self.lock:
            self._check()
            return (self._adopted and self._initial_cookie == launch_cookie
                    and snapshot == self._snapshot)

    def close(self):
        # An open reader may still own an admitted POST or expiry commit.
        # After native shutdown revokes all readers, a lingering GET may
        # hold the request lock, but cannot start any new profile update.
        readers_open = any(not reader._closed for reader in self._readers)
        if not self.lock.acquire(blocking=readers_open):
            self._closed = True
            return
        try:
            self._closed = True
            self._readers.clear()
        finally:
            self.lock.release()
