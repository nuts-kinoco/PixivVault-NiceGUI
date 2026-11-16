"""Single-pass opt-in metadata backfill over an injected transport.

This module has no scheduler, UI, legacy database, cookie discovery or hidden
background thread. The caller supplies one immutable candidate snapshot and a
managed-root ledger; this method performs at most one GET per selected work.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
import time
from threading import Event
from typing import Callable

from .metadata_ledger import ManagedMetadataLedger, MetadataLedgerError
from .metadata_policy import (
    FailureEvidence, MetadataCandidate, MetadataFailureClass, classify_failure,
    select_candidates,
)
from .metadata_transport import (
    MetadataFetchResult, MetadataPayloadInvalid, MetadataTransport, failure_evidence,
)


_ID = re.compile(r'[1-9][0-9]{0,19}\Z')


@dataclass(frozen=True, slots=True)
class MetadataRunSummary:
    target: int = 0
    filled: int = 0
    no_value: int = 0
    work_error: int = 0
    stale: int = 0
    skipped: int = 0
    skipped_busy: int = 0
    stopped: str = ''


class ManagedMetadataBackfillRunner:
    """Explicit one-pass runner. It never starts itself or retries internally."""

    def __init__(self, ledger: ManagedMetadataLedger, transport: MetadataTransport,
                 *, active_receipt_sha256: Callable[[str], str | None],
                 is_download_busy: Callable[[], bool]):
        if not isinstance(ledger, ManagedMetadataLedger):
            raise TypeError('managed metadata ledger required')
        if not callable(getattr(transport, 'verify_identity', None)) or not callable(
                getattr(transport, 'fetch_detail', None)):
            raise TypeError('metadata transport contract required')
        if not callable(active_receipt_sha256) or not callable(is_download_busy):
            raise TypeError('managed state callbacks required')
        self._ledger = ledger
        self._transport = transport
        self._active = active_receipt_sha256
        self._busy = is_download_busy

    @staticmethod
    def _stopped(summary: dict, reason: str) -> MetadataRunSummary:
        summary['stopped'] = reason
        return MetadataRunSummary(**summary)

    @staticmethod
    def _active_digest(callback, work_id: str) -> str | None:
        try:
            value = callback(work_id)
        except Exception:
            raise MetadataLedgerError('active managed receipt unavailable') from None
        if value is None:
            return None
        if type(value) is not str or not re.fullmatch(r'[a-f0-9]{64}\Z', value):
            raise MetadataLedgerError('active managed receipt rejected')
        return value

    def _wait_for_idle(self, *, stop_event: Event | None, wait_for_idle: bool,
                       poll_seconds: float) -> tuple[bool, bool]:
        try:
            busy = bool(self._busy())
        except Exception:
            raise MetadataLedgerError('download state unavailable') from None
        if busy and not wait_for_idle:
            return False, True
        waited = False
        while busy:
            if stop_event is not None and stop_event.is_set():
                return False, waited
            waited = True
            if stop_event is None:
                time.sleep(poll_seconds)
            else:
                stop_event.wait(poll_seconds)
            try:
                busy = bool(self._busy())
            except Exception:
                raise MetadataLedgerError('download state unavailable') from None
        return True, waited

    def run_once(self, candidates: tuple[MetadataCandidate, ...], *,
                 stop_event: Event | None = None, wait_for_idle: bool = True,
                 poll_seconds: float = 0.25) -> MetadataRunSummary:
        if type(wait_for_idle) is not bool or type(poll_seconds) not in (int, float) or not (
                0 < poll_seconds <= 5):
            raise ValueError('invalid metadata run options')
        if stop_event is not None and not isinstance(stop_event, Event):
            raise TypeError('threading.Event required')
        settings = self._ledger.settings()
        if not settings.enabled:
            return MetadataRunSummary(stopped='disabled')
        selected = select_candidates(candidates)  # Freeze exactly once per run.
        if not selected:
            return MetadataRunSummary()
        summary = {
            'target': len(selected), 'filled': 0, 'no_value': 0,
            'work_error': 0, 'stale': 0, 'skipped': 0,
            'skipped_busy': 0, 'stopped': '',
        }

        if stop_event is not None and stop_event.is_set():
            return self._stopped(summary, 'cancelled')

        try:
            ready, waited = self._wait_for_idle(
                stop_event=stop_event, wait_for_idle=wait_for_idle,
                poll_seconds=float(poll_seconds))
            summary['skipped_busy'] += int(waited)
            if not ready:
                return self._stopped(summary, 'cancelled' if stop_event and stop_event.is_set()
                                     else 'download_busy')
            identity = self._transport.verify_identity()
            if type(identity) is not str or _ID.fullmatch(identity) is None:
                return self._stopped(summary, 'authentication_unverified')
        except Exception:
            return self._stopped(summary, 'authentication_or_state_unavailable')

        consecutive_unknown = 0
        for candidate in selected:
            if stop_event is not None and stop_event.is_set():
                return self._stopped(summary, 'cancelled')
            try:
                ready, waited = self._wait_for_idle(
                    stop_event=stop_event, wait_for_idle=wait_for_idle,
                    poll_seconds=float(poll_seconds))
                summary['skipped_busy'] += int(waited)
                if not ready:
                    return self._stopped(
                        summary, 'cancelled' if stop_event and stop_event.is_set()
                        else 'download_busy')
                digest = self._active_digest(self._active, candidate.work_id)
                if digest is None:
                    summary['skipped'] += 1
                    continue
                prior = self._ledger.entry(candidate.work_id)
                if prior is None or prior.base_receipt_sha256 != digest:
                    self._ledger.offer(candidate, digest)
                else:
                    if prior.state != 'pending':
                        summary['skipped'] += 1
                        continue
                lease = self._ledger.claim(candidate.work_id, digest)
                if lease is None:
                    summary['skipped'] += 1
                    continue
            except MetadataLedgerError:
                return self._stopped(summary, 'managed_state_changed')

            try:
                result = self._transport.fetch_detail(candidate.work_id, candidate.user_id)
                if (not isinstance(result, MetadataFetchResult)
                        or result.work_id != candidate.work_id
                        or result.author_id != candidate.user_id):
                    raise MetadataPayloadInvalid('metadata identity mismatch')
                if result.missing_bookmark_count:
                    evidence = FailureEvidence('missing_bookmark_count')
                    decision = classify_failure(evidence, consecutive_unknown=consecutive_unknown)
                    self._ledger.complete_failure(candidate.work_id, digest, lease, decision)
                    summary['no_value'] += 1
                    consecutive_unknown = 0
                    continue
                if (type(result.metadata) is not dict
                        or type(result.metadata.get('bookmark_count')) is not int
                        or result.metadata['bookmark_count'] < 0):
                    raise MetadataPayloadInvalid('metadata payload invalid')
                payload = dict(result.metadata)
                payload['meta_fetched_at'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
                active_after = self._active_digest(self._active, candidate.work_id)
                if active_after != digest:
                    self._ledger.discard_stale_result(candidate.work_id, digest, lease)
                    summary['stale'] += 1
                    continue
                self._ledger.complete_success(
                    candidate.work_id, digest, lease, payload,
                    verified_active_receipt_sha256=active_after)
                summary['filled'] += 1
                consecutive_unknown = 0
            except Exception as exc:
                # No exception text or response content is displayed or persisted.
                try:
                    evidence = failure_evidence(exc)
                    decision = classify_failure(evidence, consecutive_unknown=consecutive_unknown)
                    self._ledger.complete_failure(candidate.work_id, digest, lease, decision)
                except MetadataLedgerError:
                    try:
                        self._ledger.discard_stale_result(candidate.work_id, digest, lease)
                        summary['stale'] += 1
                        # A global outage/rate-limit is still global when the
                        # specific work generation happened to rotate mid-GET.
                        if decision.classification == MetadataFailureClass.GLOBAL:
                            return self._stopped(summary, 'global_failure')
                        continue
                    except MetadataLedgerError:
                        return self._stopped(summary, 'managed_state_changed')
                if decision.classification == MetadataFailureClass.WORK:
                    summary['work_error'] += 1
                    consecutive_unknown = 0
                elif decision.classification == MetadataFailureClass.GLOBAL:
                    return self._stopped(summary, 'global_failure')
                else:
                    consecutive_unknown = decision.consecutive_unknown
                    if decision.stop_run:
                        return self._stopped(summary, 'five_consecutive_unknown_failures')
        return MetadataRunSummary(**summary)


__all__ = ['MetadataRunSummary', 'ManagedMetadataBackfillRunner']
