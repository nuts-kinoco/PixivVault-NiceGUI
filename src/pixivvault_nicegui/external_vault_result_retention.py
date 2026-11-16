"""Bound known-result admission; opt-in collection retires verified normal pairs.

This encrypted index reserves a slot before a normal result journal is written,
then marks that slot normal after primary publication. Both phases count toward
sixteen, so repeated completion-mark failures cannot bypass the cap. Failed or
unconfirmed result files are never touched. The caller retains the external
vault owner lock and serializes the complete operation. This is a cumulative
normal-result cap unless the explicitly enabled collector runs; can_record alone reserves nothing.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from threading import RLock
from uuid import uuid4

from .fs_paths import native_io_path
from .managed_downloads import _fsync_dir, _read_bounded, _safe_path, _write

MAX_NORMAL_RESULTS = 16
INDEX_NAME = 'KNOWN_RESULT_RETENTION_V1.enc'
_PURPOSE = 'oauth-known-result-normal-retention-v1'
_INDEX_LIMIT = 8192
_MARKERS = (b'PixivVault external vault sidecar v1\n',
            b'PixivVault external vault sidecar v2\n')
_REGISTRY_LOCK = RLock()
_ROOT_LOCKS = {}


class KnownResultRetentionError(ValueError):
    def __init__(self, code='known-result-retention-unavailable'):
        self.code = code
        super().__init__(code)


def _hex(value, length):
    return (type(value) is str and len(value) == length
            and all(char in '0123456789abcdef' for char in value))


def _lock_for(root):
    # The service's lifetime owner lock excludes other processes. Share this
    # local lock across helper instances so thread callers cannot lose slots.
    identity = os.path.normcase(str(root)).casefold()
    with _REGISTRY_LOCK:
        return _ROOT_LOCKS.setdefault(identity, RLock())


class KnownResultRetention:
    def __init__(self, root):
        self.root = Path(root).absolute()

    def _root(self):
        _safe_path(self.root)
        if not native_io_path(self.root).is_dir():
            raise KnownResultRetentionError()
        root = self.root.resolve(strict=True)
        if _read_bounded(root / 'EXTERNAL_VAULT_V1', 128) not in _MARKERS:
            raise KnownResultRetentionError()
        return root

    @staticmethod
    def _binding(root):
        return hashlib.sha256(os.path.normcase(str(root)).casefold().encode('utf-8')).hexdigest()

    def _validate(self, item, root):
        if (type(item) is not dict
                or set(item) != {'version', 'purpose', 'root_binding', 'records'}
                or type(item['version']) is not int or item['version'] != 1
                or item['purpose'] != _PURPOSE
                or item['root_binding'] != self._binding(root)
                or type(item['records']) is not list
                or len(item['records']) > MAX_NORMAL_RESULTS):
            raise KnownResultRetentionError()
        records = []
        bases = set()
        proofs = set()
        for record in item['records']:
            if (type(record) is not dict or set(record) != {'base', 'proof_sha256', 'phase'}
                    or not _hex(record['base'], 32) or not _hex(record['proof_sha256'], 64)
                    or type(record['phase']) is not str or record['phase'] not in ('reserved', 'normal')
                    or record['base'] in bases or record['proof_sha256'] in proofs):
                raise KnownResultRetentionError()
            bases.add(record['base'])
            proofs.add(record['proof_sha256'])
            records.append((record['base'], record['proof_sha256'], record['phase']))
        return tuple(records)

    def _read(self, root, key, crypto):
        if self._root() != root:
            raise KnownResultRetentionError()
        current = root / INDEX_NAME
        _safe_path(current, file=True)
        if not native_io_path(current).exists():
            return None, ()
        raw = _read_bounded(current, _INDEX_LIMIT)
        records = self._validate(crypto.decrypt(raw, key), root)
        if _read_bounded(current, _INDEX_LIMIT) != raw:
            raise KnownResultRetentionError()
        return raw, records

    def can_record(self, key, crypto) -> bool:
        """True only while reserved and normal records together have a free slot."""
        try:
            root = self._root()
            with _lock_for(root):
                _, records = self._read(root, key, crypto)
                return len(records) < MAX_NORMAL_RESULTS
        except KnownResultRetentionError:
            raise
        except Exception:
            raise KnownResultRetentionError() from None

    def _publish(self, root, previous, expected, key, crypto):
        payload = {'version': 1, 'purpose': _PURPOSE, 'root_binding': self._binding(root),
                   'records': [{'base': item[0], 'proof_sha256': item[1], 'phase': item[2]}
                               for item in expected]}
        encrypted = crypto.encrypt(payload, key)
        if (type(encrypted) is not bytes or not 0 < len(encrypted) <= _INDEX_LIMIT
                or self._validate(crypto.decrypt(encrypted, key), root) != expected):
            raise KnownResultRetentionError()
        current = root / INDEX_NAME
        pending = root / (INDEX_NAME + '.next.' + uuid4().hex)
        try:
            _write(pending, encrypted)
            if _read_bounded(pending, _INDEX_LIMIT) != encrypted:
                raise KnownResultRetentionError()
            _fsync_dir(native_io_path(root))
            if self._read(root, key, crypto)[0] != previous:
                raise KnownResultRetentionError()
            _safe_path(current, file=True)
            os.replace(native_io_path(pending), native_io_path(current))
            _fsync_dir(native_io_path(root))
            if self._read(root, key, crypto)[1] != expected:
                raise KnownResultRetentionError()
        except Exception:
            try:
                committed = self._read(root, key, crypto)[1] == expected
            except Exception:
                committed = False
            if not committed:
                raise KnownResultRetentionError() from None

    def _change(self, action, base, proof_sha256, key, crypto):
        try:
            if not _hex(base, 32) or not _hex(proof_sha256, 64):
                raise KnownResultRetentionError()
            root = self._root()
            with _lock_for(root):
                previous, records = self._read(root, key, crypto)
                for position, (recorded_base, recorded_proof, phase) in enumerate(records):
                    if recorded_base == base:
                        if recorded_proof != proof_sha256:
                            raise KnownResultRetentionError()
                        if action == 'release':
                            if phase != 'reserved':
                                raise KnownResultRetentionError()
                            expected = records[:position] + records[position + 1:]
                        elif action == 'normal' and phase == 'reserved':
                            expected = (records[:position] + ((base, proof_sha256, 'normal'),)
                                        + records[position + 1:])
                        else:
                            # Neither a repeated reservation nor an identical
                            # normal mark demotes or duplicates an existing slot.
                            return
                        self._publish(root, previous, expected, key, crypto)
                        return
                    if recorded_proof == proof_sha256:
                        raise KnownResultRetentionError()
                if action == 'release':
                    return  # Already absent; no record or file is reclassified.
                if len(records) >= MAX_NORMAL_RESULTS:
                    raise KnownResultRetentionError('known-result-retention-full')
                expected = records + ((base, proof_sha256, action),)
                self._publish(root, previous, expected, key, crypto)
        except KnownResultRetentionError:
            raise
        except Exception:
            raise KnownResultRetentionError() from None

    def reserve_normal(self, base, proof_sha256, key, crypto) -> None:
        """Durably reserve before writing a new normal result/proof journal.

        Same-base/proof reserved or normal entries are idempotent. Reservation
        failure means the caller must defer normal journaling; primary failure
        records remain outside this budget and must still be preserved.
        """
        self._change('reserved', base, proof_sha256, key, crypto)

    def mark_success(self, base, proof_sha256, key, crypto) -> None:
        """Change reserved to normal only after successful primary publication.

        Failure leaves the reservation counted. Direct marks remain supported
        for compatible callers, but normal journal admission must reserve first.
        Identical normal marks are idempotent, including at full capacity.
        """
        self._change('normal', base, proof_sha256, key, crypto)

    def release_failed(self, base, proof_sha256, key, crypto) -> None:
        """Release only a matching reserved slot; preserve every result/proof file.

        The caller decides when a failure is known or explicit recovery has
        completed. Normal entries cannot be released. Uncertain interruptions
        retain their reservation. An already-absent slot is an idempotent no-op.
        """
        self._change('release', base, proof_sha256, key, crypto)

    def confirm_generation(self,base,key,crypto):
        """Authenticate a normal pair and remember its completed/observed CURRENT."""
        root=self._root()
        with _lock_for(root):
            _,records=self._read(root,key,crypto)
            entry=next((r for r in records if r[0]==base and r[2]=='normal'),None)
            if entry is None:return
            directory=root/'known-results'/base;_safe_path(directory)
            target=directory/'normal-complete.enc';_safe_path(target,file=True)
            if native_io_path(target).exists():return
            proof_raw=_read_bounded(directory/'proof.enc',1024*1024+28)
            result=_read_bounded(directory/'result.enc',1024*1024+28)
            proof=crypto.decrypt(proof_raw,key)
            source=_read_bounded(root/'generations'/base/'vault.enc',1024*1024+28)
            if (hashlib.sha256(proof_raw).hexdigest()!=entry[1]
                or proof.get('purpose')!='oauth-known-result-v1' or proof.get('base')!=base
                or proof.get('source_sha256')!=hashlib.sha256(source).hexdigest()
                or proof.get('result_sha256')!=hashlib.sha256(result).hexdigest()):raise KnownResultRetentionError()
            from .external_vault import normalize_data
            result_data=normalize_data(crypto.decrypt(result,key))
            if any(s.get('oauth_send_pending') for s in result_data['slots'].values()):raise KnownResultRetentionError()
            generation=_read_bounded(root/'CURRENT',64).decode('ascii')
            if not _hex(generation,32):raise KnownResultRetentionError()
            from .external_vault import normalize_data
            current=normalize_data(crypto.decrypt(_read_bounded(root/'generations'/generation/'vault.enc',1024*1024+28),key))
            if any(s.get('oauth_send_pending') for s in current['slots'].values()):return
            payload={'purpose':'completed-normal-v1','root':self._binding(root),'base':base,
                     'generation':generation,'proof':entry[1],'result':hashlib.sha256(result).hexdigest()}
            _write(target,crypto.encrypt(payload,key));_fsync_dir(directory)
            if crypto.decrypt(_read_bounded(target,8192),key)!=payload:raise KnownResultRetentionError()

    def collect_completed(self,key,crypto):
        """Rolling budget; uncertain/reserved records and files are never collected."""
        from .completed_maintenance import remove_verified,verified_survivor
        from .external_vault import normalize_data
        root=self._root();removed=[]
        with _lock_for(root):
            previous,records=self._read(root,key,crypto)
            generation=_read_bounded(root/'CURRENT',64).decode('ascii')
            if not _hex(generation,32):raise KnownResultRetentionError()
            current=normalize_data(crypto.decrypt(_read_bounded(root/'generations'/generation/'vault.enc',1024*1024+28),key))
            if any(s.get('oauth_send_pending') for s in current['slots'].values()):return ()
            # Existing cumulative entries get a conservative observation barrier:
            # they require another confirmed publication before release.
            for base,proof,phase in records:
                if phase=='normal':
                    try:self.confirm_generation(base,key,crypto)
                    except Exception:pass
            parent=root/'known-results';_safe_path(parent)
            if not native_io_path(parent).exists():return ()
            for directory in sorted(native_io_path(parent).iterdir()):
                if not _hex(directory.name,32):continue
                _safe_path(directory)
                target=directory/'normal-complete.enc';_safe_path(target,file=True)
                if not target.exists():continue
                try:
                    raw=_read_bounded(target,8192);m=crypto.decrypt(raw,key)
                    if (set(m)!={'purpose','root','base','generation','proof','result'}
                        or m['purpose']!='completed-normal-v1' or m['root']!=self._binding(root)
                        or m['base']!=directory.name or not _hex(m['generation'],32)
                        or not _hex(m['proof'],64) or not _hex(m['result'],64)):continue
                    if m['generation']==generation:continue
                    completed=normalize_data(crypto.decrypt(_read_bounded(root/'generations'/m['generation']/'vault.enc',1024*1024+28),key))
                    if any(s.get('oauth_send_pending') for s in completed['slots'].values()):continue
                    previous,records=self._read(root,key,crypto)
                    entry=next((r for r in records if r[0]==m['base']),None)
                    if entry is not None and entry!=(m['base'],m['proof'],'normal'):continue
                    # Validate every remaining byte before releasing a slot.
                    for name,digest in [('result.enc',m['result']),('proof.enc',m['proof'])]:
                        p=directory/name;_safe_path(p,file=True)
                        if p.exists() and hashlib.sha256(_read_bounded(p,1024*1024+28)).hexdigest()!=digest:raise KnownResultRetentionError()
                    with verified_survivor(root/'CURRENT',hashlib.sha256(generation.encode('ascii')).hexdigest()):
                        if entry is not None:self._publish(root,previous,tuple(r for r in records if r!=entry),key,crypto)
                        for name,digest in [('result.enc',m['result']),('proof.enc',m['proof'])]:
                            p=directory/name
                            if p.exists():remove_verified(p,digest)
                        remove_verified(target,hashlib.sha256(raw).hexdigest())
                        try:directory.rmdir()
                        except OSError:pass
                    removed.append(m['base'])
                except Exception:
                    # Keep partial cleanup evidence, retry only its authenticated
                    # exact files on a later pass; never block primary publish.
                    continue
        return tuple(removed)
