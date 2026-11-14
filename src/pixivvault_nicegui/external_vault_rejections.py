"""Exact encrypted rejection history outside the bounded active vault payload.

Immutable linked chunks keep old proofs without eviction or probabilistic
membership. The active AES-GCM generation authenticates the chain head.
"""
import copy
import hashlib
import json
import uuid
from pathlib import Path
from .managed_downloads import _safe_path, _read_bounded, _write, _fsync_dir, native_io_path
from .ugoira_storage import _publish_directory

CACHE_LIMIT = 128
CHUNK_LIMIT = 1024
SEND_RESERVE = 4096
WRITE_RESERVE = 1024
ARCHIVE_FIELD = 'rejected_refresh_archive'


class RejectionHistoryError(ValueError):
    def __init__(self, code='rejected-history-unavailable'):
        self.code = code
        super().__init__(code)


def valid_hex(value, length):
    return type(value) is str and len(value) == length and all(c in '0123456789abcdef' for c in value)


def valid_pointer(value):
    return (type(value) is dict and set(value) == {'generation', 'sha256'}
            and valid_hex(value['generation'], 32) and valid_hex(value['sha256'], 64))


def payload_size(data):
    return len(json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8'))


class RejectedRefreshArchive:
    def __init__(self, root):
        self.root = Path(root)

    def _directory(self, create=False):
        directory = native_io_path(self.root / 'rejected-refresh-history-v1')
        _safe_path(directory)
        marker = directory / 'HISTORY_V1'
        if not directory.exists() and create:
            # Never expose a canonical directory without a complete marker.
            # Interrupted staging is retained, but is not adopted on retry.
            staging = native_io_path(self.root / ('.rejected-refresh-init-'+uuid.uuid4().hex))
            _safe_path(staging)
            staging.mkdir()
            _write(staging / 'HISTORY_V1', b'PixivVault rejected refresh history v1\n')
            _fsync_dir(staging)
            _publish_directory(staging, directory)
            _fsync_dir(self.root)
        if _read_bounded(marker, 64) != b'PixivVault rejected refresh history v1\n':
            raise RejectionHistoryError()
        return directory

    def read(self, data, key, crypto, limit, *, required_head=None):
        head = data.get(ARCHIVE_FIELD)
        if head is None:
            if required_head is not None:raise RejectionHistoryError()
            return frozenset()
        try:
            directory = self._directory()
            result, seen = set(), set()
            found_required = required_head is None
            while head is not None:
                if not valid_pointer(head) or head['generation'] in seen:
                    raise RejectionHistoryError()
                seen.add(head['generation'])
                if head == required_head:found_required = True
                encrypted = _read_bounded(directory / (head['generation'] + '.enc'), limit + 28)
                if hashlib.sha256(encrypted).hexdigest() != head['sha256']:
                    raise RejectionHistoryError()
                item = crypto.decrypt(encrypted, key)
                if (type(item) is not dict or set(item) != {'version', 'previous', 'digests'}
                        or type(item['version']) is not int or item['version'] != 1
                        or type(item['digests']) is not list or not 0 < len(item['digests']) <= CHUNK_LIMIT
                        or any(not valid_hex(d, 64) for d in item['digests'])
                        or (item['previous'] is not None and not valid_pointer(item['previous']))):
                    raise RejectionHistoryError()
                result.update(item['digests'])
                head = item['previous']
            # Equal digest sets in a different chain are not an extension.
            # Authenticate the entire chain, including the required base head.
            if not found_required:raise RejectionHistoryError()
            return frozenset(result)
        except RejectionHistoryError:
            raise
        except Exception:
            raise RejectionHistoryError() from None

    def contains(self, digest, data, key, crypto, limit):
        return digest in self.read(data, key, crypto, limit)

    def compact(self, data, key, crypto, limit, reserve=WRITE_RESERVE, force=False, extra_digests=()):
        histories = [slot.get('rejected_refresh_hashes', []) for slot in data['slots'].values()]
        histories.append(extra_digests)
        if not any(histories):
            return
        if not force and not extra_digests and all(len(h) <= CACHE_LIMIT for h in histories) and payload_size(data) <= limit-reserve:
            return
        try:
            archived = self.read(data, key, crypto, limit)
            additions = list(dict.fromkeys(d for h in histories for d in h if d not in archived))
            projected = copy.deepcopy(data)
            for slot in projected['slots'].values():
                if 'rejected_refresh_hashes' in slot:
                    slot['rejected_refresh_hashes'] = []
            if additions:
                projected[ARCHIVE_FIELD] = {'generation': '0'*32, 'sha256': '0'*64}
            if payload_size(projected) > limit-WRITE_RESERVE:
                raise RejectionHistoryError('vault-too-large')
            head = data.get(ARCHIVE_FIELD)
            if additions:
                directory = self._directory(create=True)
                for offset in range(0, len(additions), CHUNK_LIMIT):
                    encrypted = crypto.encrypt({'version': 1, 'previous': head,
                                                'digests': additions[offset:offset+CHUNK_LIMIT]}, key)
                    generation = uuid.uuid4().hex
                    _write(directory / (generation + '.enc'), encrypted)
                    head = {'generation': generation, 'sha256': hashlib.sha256(encrypted).hexdigest()}
                _fsync_dir(directory)
            # Publish the new reference only after every immutable chunk is durable.
            if head is not None:
                data[ARCHIVE_FIELD] = head
            for slot in data['slots'].values():
                if 'rejected_refresh_hashes' in slot:
                    slot['rejected_refresh_hashes'] = []
        except RejectionHistoryError:
            raise
        except Exception:
            raise RejectionHistoryError() from None

