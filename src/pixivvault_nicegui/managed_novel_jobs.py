"""Durable single-novel jobs using an explicitly injected offline fixture.

JobService owns execution/control/idempotency. This separate novel namespace
owns accepted intents, publication proofs and the generation CAS pointer.
No network transport, Cookie, legacy DB, UI or automatic retry is used here.
"""
from __future__ import annotations

from .request_policy import job_request_runner

from copy import deepcopy
from dataclasses import asdict, dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
from threading import RLock
from uuid import uuid4

from .job_service import JobService, UNFINISHED
from .managed_downloads import _json_unique, _read_bounded
from .media_parity_policy import parse_novel_detail
from .novel_storage import (
    NovelExportStore, NovelStoragePublicationUncertain, SavedNovel,
    _fsync_dir, _safe, _write_new,
)

_ID = re.compile(r"[1-9][0-9]{0,19}\Z")
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_JID = re.compile(r"[a-f0-9]{32}\Z")
_REL = re.compile(r"outputs/novel-([1-9][0-9]{0,19})-[a-f0-9]{32}/receipt\.json\Z")
_MARKER = b'{"application":"pixivvault-nicegui","purpose":"managed-novel-jobs","version":1}\n'
_MAX_LEDGER = 8 * 1024 * 1024
_MAX_ENTRIES = 10000
KIND = "novel-export"


class ManagedNovelError(ValueError):
    def __init__(self):
        super().__init__("managed novel operation failed")


def _require(condition):
    if not condition:
        raise ManagedNovelError()


def _canonical(value):
    return (json.dumps(value, ensure_ascii=True, sort_keys=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("ascii")


def _hash(value):
    return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True, slots=True)
class NovelJobIntent:
    work_id: str = field(repr=False)
    author_id: str = field(repr=False)
    output_format: str = "TXT"
    expected_relative_receipt: str | None = field(default=None, repr=False)
    expected_receipt_sha256: str | None = field(default=None, repr=False)

    chapter_ids: tuple[str, ...] = field(default=(), repr=False)
    series_title: str | None = field(default=None, repr=False)
    expected_source_sha256: str | None = field(default=None, repr=False)

    def spec(self):
        _require(type(self.work_id) is str and _ID.fullmatch(self.work_id))
        _require(type(self.author_id) is str and _ID.fullmatch(self.author_id))
        _require(type(self.output_format) is str and self.output_format in {"TXT", "EPUB", "BOTH"})
        relative, digest = self.expected_relative_receipt, self.expected_receipt_sha256
        if relative is not None or digest is not None:
            _require(type(relative) is str and (match := _REL.fullmatch(relative))
                     and match.group(1) == self.work_id)
            _require(type(digest) is str and _SHA.fullmatch(digest))
        _require(self.expected_source_sha256 is None or
                 (type(self.expected_source_sha256) is str and _SHA.fullmatch(self.expected_source_sha256)))
        result = asdict(self)
        if self.expected_source_sha256 is None:
            result.pop("expected_source_sha256")
        if self.chapter_ids:
            _require(type(self.chapter_ids) in (list, tuple) and 2 <= len(self.chapter_ids) <= 100
                     and self.chapter_ids[0] == self.work_id and len(set(self.chapter_ids)) == len(self.chapter_ids)
                     and all(type(w) is str and _ID.fullmatch(w) for w in self.chapter_ids))
            _require(type(self.series_title) is str and 0 < len(self.series_title) <= 256)
            result['chapter_ids'] = list(self.chapter_ids)
        else:
            _require(self.series_title is None)
            result.pop('chapter_ids')
            result.pop('series_title')
        return result

    @property
    def target(self):
        self.spec()
        suffix = (":series:" + _hash(_canonical(list(self.chapter_ids)))[:16]) if self.chapter_ids else ""
        return self.work_id + ":" + self.output_format + suffix


class FixtureNovelSource:
    """Finite copied synthetic payloads. Deliberately offers no HTTP fallback."""

    def __init__(self, payloads: dict):
        _require(type(payloads) is dict and len(payloads) <= 256)
        # JSON round-trip excludes arbitrary objects, callbacks and custom maps.
        try:
            data = _canonical(payloads)
            _require(len(data) <= 16 * 1024 * 1024)
            self._payloads = json.loads(data)
        except Exception:
            raise ManagedNovelError() from None

    def fetch(self, work_id: str):
        try:
            return deepcopy(self._payloads[work_id])
        except Exception:
            raise ManagedNovelError() from None


class PinnedNovelSource:
    """Explicit finite selection backed by the PR33 bounded reader.

    The caller owns the authenticated reader and supplies trusted author pins.
    Constructing this adapter performs no request and never reads a Cookie.
    """
    def __init__(self, reader, authors: dict[str, str]):
        _require(type(authors) is dict and 0 <= len(authors) <= 256)
        _require(all(type(k) is str and _ID.fullmatch(k) and
                     type(v) is str and _ID.fullmatch(v) for k, v in authors.items()))
        self._selection_lock = RLock()
        self._authors = dict(authors)
        self._reader = reader

    def select(self, work_id, author_id):
        with self._selection_lock:
            _require(type(work_id) is str and _ID.fullmatch(work_id) and
                     type(author_id) is str and _ID.fullmatch(author_id))
            _require(work_id not in self._authors or self._authors[work_id] == author_id)
            _require(work_id in self._authors or len(self._authors) < 256)
            self._authors[work_id] = author_id

    @property
    def authors(self):
        with self._selection_lock:
            return dict(self._authors)

    def fetch(self, work_id):
        _require(type(work_id) is str and work_id in self._authors)
        try:
            self._reader.require_identity()
            body = self._reader.get_novel_payload(work_id, self._authors[work_id])
            parse_novel_detail(body, work_id=work_id, expected_author_id=self._authors[work_id])
            self._reader.require_identity()
            return deepcopy(body)
        except Exception:
            raise ManagedNovelError() from None


    def catalog(self, author_id=None, rest='show'):
        if author_id is not None:
            ids = self._reader.get_author_novel_ids(author_id)
            rows = [(wid, author_id, wid) for wid in ids]
        else:
            uid = self._reader.require_identity()
            rows = []
            for item in self._reader.get_bookmarked_novels(uid, rest):
                _require(len(rows) < 256)
                rows.append((item['id'], item['user_id'], item['title']))
        # Catalog rows do not enqueue or pin every item; the user selects one.
        return tuple(rows)

    def bundle(self, body, intent, context):
        from .novel_practical_export import NovelAsset, render_practical_bundle
        import time
        deadline = time.monotonic() + 90
        assets, cover = {}, None
        documents = []
        payloads = [body]
        for wid in intent.chapter_ids[1:]:
            context.checkpoint()
            self.select(wid, intent.author_id)
            _require(time.monotonic() < deadline)
            payloads.append(self.fetch(wid))
            _require(sum(len(p['content']) for p in payloads) <= 2_000_000)
            _require(time.monotonic() < deadline)
        used = 0
        for payload in payloads:
            doc = parse_novel_detail(payload, work_id=payload['id'], expected_author_id=intent.author_id)
            documents.append(doc)
            rows = payload.get('textEmbeddedImages') or {}
            for iid, item in rows.items():
                _require(type(iid) is str and _ID.fullmatch(iid) and type(item) is dict)
                _require(len(assets) < 100 and type(item.get('urls')) is dict)
                url = item['urls'].get('original')
                context.checkpoint()
                _require(time.monotonic() < deadline)
                data = self._reader.get_image_bytes(url)
                context.checkpoint()
                used += len(data)
                _require(used <= 32 * 1024 * 1024 and time.monotonic() < deadline)
                assets[(doc.work_id, iid)] = NovelAsset(iid, data)
            if len(documents) == 1 and payload.get('coverUrl'):
                context.checkpoint()
                _require(time.monotonic() < deadline)
                data = self._reader.get_image_bytes(payload['coverUrl'])
                used += len(data)
                _require(used <= 32 * 1024 * 1024 and time.monotonic() < deadline)
                cover = NovelAsset('cover', data)
        context.checkpoint()
        title = intent.series_title if len(documents) > 1 else documents[0].title
        author = body.get('userName')
        bundle = render_practical_bundle(documents, author_name=author, output_format=intent.output_format,
            assets=assets, cover=cover, series_title=intent.series_title,
            expected_work_ids=tuple(intent.chapter_ids) if intent.chapter_ids else None)
        metadata = dict(title=title, author=author, chapters=[d.work_id for d in documents],
                        series_id=documents[0].series_id)
        return bundle, metadata


class ManagedNovelJobs:
    """One JobService writer, one bounded durable journal, immutable outputs.

    root is a new, explicitly selected synthetic-only root, separate from every
    illustration/ugoira/legacy root. Run enqueue/current off the UI loop.
    """

    def __init__(self, root: Path, source: FixtureNovelSource):
        _require(type(source) in (FixtureNovelSource, PinnedNovelSource))
        self.root = Path(root)
        _require(self.root.is_absolute() and self.root.parent != self.root)
        self._source = source
        self._lock = RLock()
        self._enqueue_lock = RLock()
        self.jobs = None
        self._closed = False
        self._closing = False
        self._faulted = False
        try:
            _safe(self.root)
            if not self.root.exists():
                self.root.mkdir()
                _fsync_dir(self.root.parent)
            marker = self.root / ".pixivvault-novel-jobs.json"
            if not marker.exists():
                _require(not any(self.root.iterdir()))
                _write_new(marker, _MARKER)
                _fsync_dir(self.root)
            _require(_read_bounded(marker, 4096) == _MARKER)
            # Acquire the existing persistent JobService owner lock BEFORE
            # reading or touching any journal/output state on a reopen.
            self.jobs = JobService(job_request_runner(self._run), self.root / "jobs.sqlite3")
            self.store = NovelExportStore(self.root / "exports")
            self._journal_path = self.root / "novel-journal.json"
            _safe(self._journal_path, file=True)
            self._journal = self._load()
            self._restore()
        except BaseException:
            if self.jobs is not None:
                self.jobs.close()
            raise

    def _load(self):
        if not self._journal_path.exists():
            _require(not self.jobs.snapshot())
            return {"version": 1, "intents": {}, "jobs": {}, "active": {}}
        try:
            value = json.loads(_read_bounded(self._journal_path, _MAX_LEDGER),
                               object_pairs_hook=_json_unique)
            _require(type(value) is dict and value.keys() == {"version", "intents", "jobs", "active"})
            _require(type(value["version"]) is int and value["version"] == 1)
            for name in ("intents", "jobs", "active"):
                _require(type(value[name]) is dict and len(value[name]) <= _MAX_ENTRIES)
            for op, spec in value["intents"].items():
                _require(_SHA.fullmatch(op) and type(spec) is dict)
                _require(NovelJobIntent(**spec).spec() == spec)
            for jid, row in value["jobs"].items():
                _require(_JID.fullmatch(jid) and type(row) is dict
                         and row.keys() == {"fingerprint", "pending"})
                _require(type(row["fingerprint"]) is str and _SHA.fullmatch(row["fingerprint"]))
                if row["pending"] is not None:
                    self._validate_proof(row["pending"])
            for target, row in value["active"].items():
                _require(type(target) is str and re.fullmatch(r"[1-9][0-9]{0,19}:(TXT|EPUB|BOTH)(:series:[a-f0-9]{16})?", target))
                _require(type(row) is dict and row.keys() == {"job_id", "fingerprint", "proof"})
                _require(type(row["job_id"]) is str and _JID.fullmatch(row["job_id"]))
                _require(type(row["fingerprint"]) is str and _SHA.fullmatch(row["fingerprint"]))
                self._validate_proof(row["proof"])
                accepted = value["jobs"].get(row["job_id"])
                _require(accepted is not None and accepted["fingerprint"] == row["fingerprint"]
                         and accepted["pending"] == row["proof"])
                _require(_REL.fullmatch(row["proof"]["relative_receipt"]).group(1) == target.split(":")[0])
            return value
        except Exception:
            raise ManagedNovelError() from None

    @staticmethod
    def _validate_proof(proof):
        _require(type(proof) is dict and proof.keys() == {"relative_receipt", "sha256", "attempt"})
        _require(type(proof["relative_receipt"]) is str and _REL.fullmatch(proof["relative_receipt"]))
        _require(type(proof["sha256"]) is str and _SHA.fullmatch(proof["sha256"]))
        _require(type(proof["attempt"]) is int and proof["attempt"] >= 1)

    def _write(self, value):
        _require(not self._faulted)
        data = _canonical(value)
        _require(len(data) <= _MAX_LEDGER and all(len(value[k]) <= _MAX_ENTRIES
                                                for k in ("intents", "jobs", "active")))
        _safe(self.root)
        _safe(self._journal_path, file=True)
        # Preserve failed journal stages, but keep their storage use finite.
        staged_count = staged_bytes = 0
        with os.scandir(self.root) as entries:
            for entry in entries:
                if re.fullmatch(r"\.novel-journal-[a-f0-9]{32}\.tmp", entry.name):
                    _safe(Path(entry.path), file=True)
                    staged_count += 1
                    staged_bytes += entry.stat(follow_symlinks=False).st_size
        _require(staged_count < 256 and staged_bytes + len(data) <= 2 * _MAX_LEDGER)
        temporary = self.root / (".novel-journal-" + uuid4().hex + ".tmp")
        try:
            _write_new(temporary, data)
            os.replace(temporary, self._journal_path)
            _fsync_dir(self.root)
        except Exception:
            # A failed replace/fsync may have become visible. Fence all further
            # operations until reopen; never overwrite using stale RAM state.
            self._faulted = True
            raise ManagedNovelError() from None
        self._journal = value

    def _intent(self, job):
        identifiers = self.jobs.operation_ids(job.job_id)
        _require(bool(identifiers) and job.kind == KIND and job.title == "")
        specs = [self._journal["intents"].get(_hash(op.encode("utf-8"))) for op in identifiers]
        _require(specs[0] is not None and all(spec == specs[0] for spec in specs))
        intent = NovelJobIntent(**specs[0])
        _require(intent.target == job.target_id)
        return intent

    @staticmethod
    def _fingerprint(job, intent):
        return _hash(job.job_id.encode("ascii") + b"\0" + _canonical(intent.spec()))

    def _bind(self, job, intent):
        fingerprint = self._fingerprint(job, intent)
        row = self._journal["jobs"].get(job.job_id)
        if row is not None:
            _require(row["fingerprint"] == fingerprint)
            return
        value = deepcopy(self._journal)
        value["jobs"][job.job_id] = {"fingerprint": fingerprint, "pending": None}
        self._write(value)

    def _matches_predecessor(self, intent):
        active = self._journal["active"].get(intent.target)
        if intent.expected_relative_receipt is None:
            return active is None
        return (active is not None and
                active["proof"]["relative_receipt"] == intent.expected_relative_receipt and
                active["proof"]["sha256"] == intent.expected_receipt_sha256)

    def _verified(self, intent, proof):
        self._validate_proof(proof)
        result = self.store.reconcile(proof["relative_receipt"], expected_sha256=proof["sha256"])
        if result.saved is None:
            return None
        saved = result.saved
        _require(saved.work_id == intent.work_id and saved.output_format == intent.output_format)
        # Storage validates all file bytes; additionally pin accepted authorship.
        metadata = json.loads(_read_bounded(self.store.root / saved.relative_receipt, 64 * 1024),
                              object_pairs_hook=_json_unique)
        _require(metadata["author_id"] == intent.author_id)
        if saved.metadata is not None:
            _require(saved.metadata["chapters"] == (list(intent.chapter_ids) if intent.chapter_ids else [intent.work_id]))
            if intent.chapter_ids:
                _require(saved.metadata["title"] == intent.series_title)
        else:
            _require(not intent.chapter_ids)
        return saved

    def _activate(self, job, intent, proof):
        row = self._journal["jobs"][job.job_id]
        _require(row["pending"] == proof and proof["attempt"] <= job.attempt)
        active = self._journal["active"].get(intent.target)
        if active is not None and active["job_id"] == job.job_id:
            _require(active == {"job_id": job.job_id, "fingerprint": row["fingerprint"], "proof": proof})
            return
        _require(self._matches_predecessor(intent))
        value = deepcopy(self._journal)
        value["active"][intent.target] = {
            "job_id": job.job_id, "fingerprint": row["fingerprint"], "proof": proof}
        self._write(value)

    def _restore(self):
        for job in self.jobs.snapshot():
            intent = self._intent(job)
            self._bind(job, intent)
            pending = self._journal["jobs"][job.job_id]["pending"]
            if pending is not None:
                _require(pending["attempt"] <= job.attempt)
            if job.state == "done":
                _require(pending is not None and job.result == pending["relative_receipt"])
                _require(self._verified(intent, pending) is not None)
            elif job.state == "stopped" and job.error_code == "interrupted":
                # Invalid/missing proof stays an explicit retryable error. An
                # explicit Stop never enters this path and never becomes done.
                try:
                    saved = self._verified(intent, pending) if pending is not None else None
                    if saved is not None:
                        self._activate(job, intent, pending)
                except Exception:
                    saved = None
                if self._faulted:
                    raise ManagedNovelError()
                self.jobs.reconcile_interrupted(job.job_id, receipt=(saved.relative_receipt if saved else None),
                                                page_count=(1 if saved else None))

    def enqueue(self, intent: NovelJobIntent, operation_id: str):
        _require(type(intent) is NovelJobIntent)
        spec = intent.spec()
        _require(type(operation_id) is str and 0 < len(operation_id) <= 128
                 and not any(ord(c) < 32 or ord(c) == 127 for c in operation_id))
        # Never take the JobService control lock while holding the journal
        # lock: publication takes them in the opposite order. This acceptance
        # mutex serializes callers while allowing the worker to bind itself.
        with self._enqueue_lock:
            with self._lock:
                _require(not self._closing and not self._closed and not self._faulted)
                key = _hash(operation_id.encode("utf-8"))
                previous = self._journal["intents"].get(key)
                if previous is not None:
                    _require(previous == spec)
                else:
                    _require(self._matches_predecessor(intent))
                    for job in self.jobs.snapshot():
                        if job.kind == KIND and job.target_id == intent.target and job.state in UNFINISHED:
                            _require(self._intent(job).spec() == spec)
                    value = deepcopy(self._journal)
                    value["intents"][key] = spec
                    self._write(value)
            job = self.jobs.enqueue(KIND, intent.target, operation_id=operation_id)
            with self._lock:
                self._bind(job, intent)
            return job

    def _run(self, job, context):
        # Enqueue may start its worker before returning the job ID. The intent
        # is already durable; bind/recover its job identity under the journal lock.
        with self._lock:
            _require(not self._closed and not self._faulted)
            intent = self._intent(job)
            self._bind(job, intent)
            pending = self._journal["jobs"][job.job_id]["pending"]
            active = self._journal["active"].get(intent.target)
            _require(self._matches_predecessor(intent) or
                     (active is not None and active["job_id"] == job.job_id))
        if active is not None and active["job_id"] != job.job_id:
            # A matching pointer is not proof that its bytes still exist. Do
            # not turn a missing/corrupt predecessor into an ordinary update.
            _require(self._verified(intent, active["proof"]) is not None)
        context.progress(0, 1)
        saved = self._verified(intent, pending) if pending is not None else None
        if saved is None:
            context.checkpoint()
            if type(self._source) is PinnedNovelSource:
                self._source.select(intent.work_id, intent.author_id)
            body = self._source.fetch(intent.work_id)
            if intent.expected_source_sha256 is not None:
                _require(_hash(_canonical(body)) == intent.expected_source_sha256)
            document = parse_novel_detail(body, work_id=intent.work_id,
                                          expected_author_id=intent.author_id)
            _require(type(self._source) is PinnedNovelSource or not body.get("textEmbeddedImages"))
            author_name = body.get("userName")
            context.checkpoint()

            def before_publish(candidate):
                proof = {"relative_receipt": candidate.relative_receipt,
                         "sha256": candidate.receipt_sha256, "attempt": job.attempt}

                def journal():
                    with self._lock:
                        _require(not self._closed and self._matches_predecessor(intent))
                        value = deepcopy(self._journal)
                        value["jobs"][job.job_id]["pending"] = proof
                        self._write(value)
                context.publish(journal)

            options = {}
            if type(self._source) is PinnedNovelSource:
                bundle, metadata = self._source.bundle(body, intent, context)
                options = dict(bundle=bundle, metadata=metadata)
            try:
                saved = self.store.save(document, author_name=author_name,
                                        output_format=intent.output_format,
                                        before_publish=before_publish, **options)
            except NovelStoragePublicationUncertain as uncertain:
                proof = self._journal["jobs"][job.job_id]["pending"]
                _require(proof is not None and proof["relative_receipt"] == uncertain.relative_receipt
                         and proof["sha256"] == uncertain.receipt_sha256)
                saved = self._verified(intent, proof)
                _require(saved is not None)
        context.checkpoint()
        with self._lock:
            proof = deepcopy(self._journal["jobs"][job.job_id]["pending"])
        _require(saved == self._verified(intent, proof))

        def activate():
            with self._lock:
                _require(not self._closed)
                self._activate(job, intent, proof)
        context.publish(activate)
        context.progress(1, 1)
        return saved.relative_receipt

    def saved_entries(self):
        with self._lock:
            _require(not self._closing and not self._closed and not self._faulted)
            entries = [(target, deepcopy(row)) for target, row in sorted(self._journal['active'].items())]
        result = []
        for target, row in entries:
            saved = self.store.reconcile(row['proof']['relative_receipt'], expected_sha256=row['proof']['sha256']).saved
            _require(saved is not None)
            with self._lock:
                _require(not self._closing and self._journal["active"].get(target) == row)
            result.append(saved)
        return tuple(result)

    def current(self, work_id: str, output_format: str = "TXT", *, chapter_ids=()) -> SavedNovel | None:
        with self._lock:
            _require(not self._closing and not self._closed and not self._faulted)
            _require(type(work_id) is str and _ID.fullmatch(work_id)
                     and type(output_format) is str and output_format in {"TXT", "EPUB", "BOTH"})
            target = work_id + ":" + output_format
            if chapter_ids:
                _require(type(chapter_ids) in (list, tuple) and 2 <= len(chapter_ids) <= 100
                         and chapter_ids[0] == work_id and all(type(w) is str and _ID.fullmatch(w) for w in chapter_ids))
                target += ":series:" + _hash(_canonical(list(chapter_ids)))[:16]
            active = deepcopy(self._journal["active"].get(target))
            if active is None:
                return None
            row = self._journal["jobs"][active["job_id"]]
            accepted = None
            for spec in self._journal["intents"].values():
                candidate = NovelJobIntent(**spec)
                fingerprint = _hash(active["job_id"].encode("ascii") + b"\0" + _canonical(spec))
                if candidate.target == target and fingerprint == row["fingerprint"]:
                    accepted = candidate
                    break
            _require(accepted is not None)
        # Never wait for the storage writer while holding the journal lock:
        # save's pre-rename hook needs the journal while owning storage.
        saved = self._verified(accepted, active["proof"])
        _require(saved is not None)
        with self._lock:
            _require(not self._closing and not self._closed and not self._faulted
                     and self._journal["active"].get(target) == active)
        return saved

    def close(self, *, timeout=10.0):
        with self._enqueue_lock:
            with self._lock:
                self._closing = True
        # Do not hold the journal lock while joining a worker that needs it.
        if self.jobs is not None:
            self.jobs.close(timeout=timeout)
        with self._lock:
            self._closed = True
        if type(self._source) is PinnedNovelSource:
            close = getattr(self._source._reader, "close", None)
            if close is not None:
                close()
