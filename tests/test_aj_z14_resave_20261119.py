"""FL-Z14: a re-saved work whose image changed must not keep stale inference.

The original deletes work_page_scan / work_embed_failures records after a re-save
(core.py _after_illust_saved) so the worker re-analyses. The port keys each indexed
page by (image sha256, receipt sha256, model sha256): a changed image or receipt is
reported as not-indexed (source_status False) and commit() replaces the vectors, and
no persistent give-up record exists to survive. Generated data only.
"""
import hashlib
import struct

from pixivvault_nicegui.managed_similarity import ManagedSimilarityIndex, STYLE, COMPOSITION


def _vec(seed):
    values = [0.0] * 1024
    values[seed % 1024] = 1.0
    return struct.pack('<1024f', *values)


def _vectors(seed):
    return {STYLE: _vec(seed), COMPOSITION: _vec(seed + 7)}


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def test_changed_image_or_receipt_is_stale_and_reanalysis_replaces_vectors(tmp_path):
    index = ManagedSimilarityIndex(tmp_path / 'sim')
    try:
        image_a, image_b, receipt_a, receipt_b = (_sha(x) for x in ('image-a', 'image-b', 'receipt-a', 'receipt-b'))
        assert index.source_status('1001', 1, image_a, receipt_a) is False      # never analysed
        assert index.commit('1001', 1, image_a, receipt_a, _vectors(1)) is True
        assert index.source_status('1001', 1, image_a, receipt_a) is True       # unchanged: indexed
        # re-saved with a different image: no stale result is accepted
        assert index.source_status('1001', 1, image_b, receipt_a) is False
        # re-saved with a different receipt (same image): also stale
        assert index.source_status('1001', 1, image_a, receipt_b) is False
        # re-analysis replaces the old vectors and the new key becomes current
        assert index.commit('1001', 1, image_b, receipt_b, _vectors(2)) is True
        assert index.source_status('1001', 1, image_b, receipt_b) is True
        assert index.source_status('1001', 1, image_a, receipt_a) is False      # old key no longer current
        # an identical re-commit is a no-op (no needless rewrite)
        assert index.commit('1001', 1, image_b, receipt_b, _vectors(2)) is False
    finally:
        index.close()
