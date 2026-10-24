# Migration scope amendment — iPhone app integration excluded

Date: 2026-10-24
Applies to: PixivVault Flet → NiceGUI migration, including the remaining Phase10 work.

## Decision

**iPhone/iOS app integration is out of scope.** Do not design, implement, test, or add acceptance criteria for pairing with an iPhone app, companion iOS client, mobile authentication bridge, or iPhone-side synchronization. If a future source diff introduces such integration, remove only that integration and its dedicated tests/wiring; do not remove adjacent desktop functionality, user data, receipts, artifacts, or historical evidence.

## Audit result

- The current tracked source and documentation were searched for `iPhone`, `iOS app`, `mobile app`, and equivalent integration names. No integration code or explicit iPhone acceptance criteria were found.
- Git history across the local refs was searched for iPhone/iOS integration commits and the literal `iPhone`; no matching implementation/history was found.
- Therefore no runtime code was removed in this amendment. The correct action is to exclude this feature from future work, not to invent a deletion or change unrelated desktop/auth code.

## Plan and acceptance mapping

- `docs/BASELINE.md` remains an unchanged historical baseline and operation ledger. Its V01–V13 criteria remain as originally recorded; none is an iPhone integration criterion. V12 remains **Windows native startup/restore/exit/tray** acceptance and must not be confused with iPhone support.
- `docs/LEGACY_BASELINE_PREPARATION.md` and all legacy baseline manifests, fixture hashes, screenshots, and measured evidence remain historical artifacts; do not rewrite or delete them to reflect this scope decision.
- The Phase5–Phase9 migration work (managed downloads, author differential download, legacy failed-queue retry, ZIP export, and scheduler) remains in scope as previously approved. This amendment does not waive any existing tests, reviews, Windows/Edge/native acceptance, or data-preservation conditions.
- Phase10 M1–M4 work and M5 review/acceptance remain in scope. No iPhone/iOS work is required for any Phase10 milestone; no iPhone acceptance gate should be added.
- Original Flet feature parity, data preservation, authenticated Pixiv read-only GETs, isolated managed-root writes, exact-HEAD CI, and Windows/Edge/native acceptance remain required. Excluding iPhone integration does not mean the overall migration is complete.

## Authentication boundary

The user's explicit approval to use the existing real Pixiv cookie permits bounded validation through the application's **existing explicit-cookie read-only path only**. It does not authorize new credential discovery/configuration, permission changes, cookie refresh/replacement, external sharing, writes to Pixiv, or displaying cookie values. Cookie contents must never enter conversation output, logs, test artifacts, or Git.
