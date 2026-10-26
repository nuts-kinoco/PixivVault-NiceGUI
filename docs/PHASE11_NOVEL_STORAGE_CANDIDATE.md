# Novel storage candidate — proposal, not integrated or accepted

Date: 2026-10-26 JST
Base commit: phase11/media-parity at 31aad331846fc3d4c3aceac4f7a90fb61f05b111.
Scope: one already validated NovelDocument, explicit author name, TXT / EPUB / BOTH.
No edits to existing DTO, exporter, series exporter, JobService, UI, reader, or ugoira.

## Existing contract basis

- docs/PHASE11_MEDIA_PARITY_CONTRACT_2026-10-24.md lines 13-15 and 19-35:
  TXT/EPUB/BOTH generation exists; M2 calls for bounded atomic saving into an
  independent root; M3 later connects accepted queue/ledger/receipt/CAS contracts.
- media_parity_policy.NovelDocument is the validated DTO.
- novel_export.render_novel_txt / render_novel_epub produce deterministic bounded
  bytes without disk, HTTP, remote resources, or UI effects.
- The existing exporter explicitly says these bytes are not a durable managed
  receipt. Series and illustrations remain outside this candidate.

## Candidate-only proposed choices (not accepted application policy)

- New API: NovelExportStore(root), save(document, author_name, output_format),
  reconcile(relative_receipt, expected_sha256=None).
- A separate root marker, outputs/ and .staging/ namespaces, and writer lock.
  No writing into the old illustration library or the existing managed-download root.
- Only TXT, EPUB, BOTH are accepted. BOTH renders both byte payloads before any
  staging output is created and publishes them together by one directory rename.
- Proposed receipt schema: schema=1, kind=novel-export, work_id, author_id,
  format, and per-output name/byte length/SHA-256. A completion marker binds a
  generation token to the receipt SHA-256. This is not the illustration receipt
  schema and is not approved for application integration.
- Proposed limits: 32 MiB per output, 64 KiB receipt, 1 KiB completion marker,
  256 retained generations, 1024 retained files, 256 MiB retained bytes.
  These values require review; they are not inherited accepted limits.
- Existing output is never overwritten. Failed staging is preserved and counted
  against quotas. No automatic cleanup or deletion is performed.
- Symlink/reparse points, hardlinked files, unexpected children and paths outside
  the novel namespace are rejected. Public diagnostics are fixed and do not
  include title, author name, novel body or filesystem paths.

- Candidate error classes distinguish a missing generation (`state="missing"`), invalid/incomplete published data (`NovelStorageIntegrityError`), and filesystem I/O failure (`NovelStorageIOError`). These fixed messages do not expose paths or content; behavior remains candidate-only.

- Post-rename durability uncertainty is represented by `NovelStoragePublicationUncertain`,
  carrying only a relative receipt path and receipt digest. The caller must reconcile
  those coordinates before retrying; a new save call is not an idempotency key.
- Initialization uses an atomically replaced fixed ownership marker. Once that marker
  proves ownership, missing namespace directories can be recreated. A root with neither
  the exact marker nor a valid exact temporary marker is refused and never cleaned.
- `_usage` validates generation names and permitted child names as well as counting
  files/bytes, so unknown files are not silently treated as an acceptable generation.
- M3 gate: receipt/completion hashes prove internal consistency only. They do not prove
  authenticity if both are rewritten together. Accepted ledger/CAS ownership and an
  expected digest from that authority are still required before application integration.
- No cancellation/checkpoint callback is implemented in this storage primitive. Windows
  symlink testing may SKIP when privilege is absent. Cross-process lock coverage is
  limited to the synthetic test on the current Windows host; power-loss durability and
  Linux semantics are not claimed.
- A complete temporary marker is not enough by itself: any sibling entry causes refusal without replacement or deletion.

## Verification plan and current status

The candidate test file uses synthetic NovelDocument DTOs and temporary roots only.
It covers TXT/EPUB/BOTH, failure before staging, mid-write partial-stage retention,
non-overwrite on repeated saves, receipt/hash reconciliation, corruption and
incomplete generations, path and media namespace rejection, links, and quota
boundaries. No network, real Cookie, user content, GUI, GPU, model, or dependency
installation is in scope.

No tests are claimed passed until the exact candidate is tested in an S-only staging
copy with parent/child TEMP/TMP/gettempdir, PYTHONPATH, bytecode and pytest output
verified on S. Integration with JobService, series retrieval, illustrations,
library indexing/viewer and UI is not implemented by these three files.
