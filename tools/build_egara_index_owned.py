"""One-time explicit accelerator indexing of already verified managed images.

No Pixiv API, Cookies, original DB, download, ZIP mutation or network I/O.
An interrupted run resumes per page from the separately owned SQLite ledger.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

from pixivvault_nicegui.egara_inference import EgaraInference
from pixivvault_nicegui.managed_downloads import (
    ManagedPixivDownloads, ManagedPageQuarantined, MARKER, IDENTITY, _read_bounded, _json_unique, _safe_path,
)
from pixivvault_nicegui.managed_similarity import ManagedSimilarityIndex


def _absolute(value):
    path = Path(value)
    if not path.is_absolute():
        raise argparse.ArgumentTypeError('Use an explicit absolute path')
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description='Offline EgaraNet index (verified managed images only)')
    parser.add_argument('--saved-root', type=_absolute, required=True)
    parser.add_argument('--index-root', type=_absolute, required=True)
    parser.add_argument('--model', type=_absolute, required=True)
    parser.add_argument('--provider', choices=('cuda', 'cpu', 'auto'), default='cuda')
    parser.add_argument('--limit-pages', type=int, default=0)
    parser.add_argument('--output', type=_absolute, required=True)
    args = parser.parse_args(argv)
    if (args.limit_pages < 0 or args.output.parent != args.index_root
            or args.output.exists()):
        parser.error('invalid output/limit; previous evidence must not be overwritten')
    if not args.saved_root.is_dir():
        parser.error('saved root must be a previously owned directory')
    # Refuse a new or unowned root before constructing ManagedPixivDownloads:
    # that constructor is a writer and may otherwise create its own directories.
    marker = args.saved_root / MARKER
    if not marker.is_file() or json.loads(_read_bounded(marker, 4096),
                                         object_pairs_hook=_json_unique) != IDENTITY:
        parser.error('saved root ownership rejected')
    from pixivvault_nicegui.media_jobs import validate_media_roots, validate_readonly_boundaries
    validate_media_roots(args.saved_root, args.index_root)
    validate_readonly_boundaries((args.index_root,), (args.model.parent,))

    class NoNetwork:
        def __getattr__(self, name):
            raise RuntimeError('offline image indexing forbids external requests')

    writer = ManagedPixivDownloads(args.saved_root, NoNetwork(),
                                    resolve_record=lambda *_: (_ for _ in ()).throw(
                                        RuntimeError('offline image indexing forbids remote lookup')))
    index = None
    inference = None
    stat = dict(indexed=0, resumed=0, unavailable=0, errors=0, examined=0,
                inference_provider=None, fallback_reason=None,
                model_sha256=None, limit_pages=args.limit_pages)
    started = time.perf_counter()
    try:
        rows = writer.saved_receipts()
        index = ManagedSimilarityIndex(args.index_root)
        for _, receipt, _ in rows:
            for zero_page in range(receipt['page_count']):
                if args.limit_pages and stat['examined'] >= args.limit_pages:
                    break
                stat['examined'] += 1
                try:
                    writer.assert_page_visible(receipt['work_id'], zero_page)
                    source = receipt['files'][zero_page]['sha256']
                    current = writer.verified_saved_receipt(receipt['work_id'])
                    if current is None:
                        raise RuntimeError('receipt changed')
                    if index.source_status(receipt['work_id'], zero_page+1, source, current[2]):
                        stat['resumed'] += 1
                        continue
                    if inference is None:
                        inference = EgaraInference(args.model, provider=args.provider)
                    if index.analyze_saved_page(writer, inference, receipt['work_id'], zero_page):
                        stat['indexed'] += 1
                    else:
                        stat['resumed'] += 1
                except ManagedPageQuarantined:
                    stat['unavailable'] += 1
                except Exception:
                    stat['errors'] += 1
                    if stat['errors'] >= 5:
                        break
            if stat['errors'] >= 5 or (args.limit_pages and stat['examined'] >= args.limit_pages):
                break
        if inference is not None:
            stat.update(inference_provider=inference.provider_active,
                        fallback_reason=inference.fallback_reason,
                        model_sha256=inference.model_sha256)
        stat['elapsed_seconds'] = round(time.perf_counter() - started, 3)
        stat['completed'] = stat['errors'] == 0
        _safe_path(args.output, file=True)
        with args.output.open('x', encoding='utf-8') as stream:
            stream.write(json.dumps(stat, indent=2))
        print(json.dumps(stat))
        return 0 if stat['completed'] else 1
    finally:
        if inference is not None:
            inference.close()
        if index is not None:
            index.close()
        writer.close()


if __name__ == '__main__':
    raise SystemExit(main())
