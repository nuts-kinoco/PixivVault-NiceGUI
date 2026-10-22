"""Original failed-list classification, per-client state and safe CSV export."""

import csv
from dataclasses import FrozenInstanceError
from io import StringIO

import pytest

from pixivvault_nicegui.failed_records_state import (
    CATEGORIES, FailureRecord, FailedRecordsState, PAGE_SIZE, classify_reason, csv_bytes,
)


def make_record(index: int, reason: str = "その他の失敗") -> FailureRecord:
    return FailureRecord(str(index), "artist", f"作品{index}", "illust", reason,
                         f"2026-10-22T00:00:{index:02d}", index)


def test_original_keyword_order_and_exact_case_and_raw_reason():
    assert [key for key, _, _ in CATEGORIES] == [
        "communication", "not_found", "permission_age", "save_dest", "format", "other"]
    assert classify_reason("Connection 403 broken image") == "communication"
    assert classify_reason("Timeout 404") == "communication"
    assert classify_reason("404 Not Found") == "not_found"
    assert classify_reason("403 Forbidden") == "permission_age"
    assert classify_reason("Permission denied") == "save_dest"
    assert classify_reason("0バイトのugoira") == "format"
    assert classify_reason("timeout connection forbidden") == "other"  # Original matching is case-sensitive.
    assert classify_reason("") == classify_reason(None) == "other"
    record = make_record(1, "Connection with 403 status")
    with pytest.raises(FrozenInstanceError):
        record.error_reason = "changed"
    assert record.error_reason == "Connection with 403 status"


def test_search_counts_across_categories_and_page_boundaries():
    records = [make_record(i, "Timeout" if i % 3 == 0 else "404" if i % 3 == 1 else "misc")
               for i in range(20)]
    state = FailedRecordsState(records)
    assert PAGE_SIZE == 8
    assert len(state.visible_records) == 8 and state.total_pages == 3
    assert state.counts == {
        "all": 20, "communication": 7, "not_found": 7,
        "permission_age": 0, "save_dest": 0, "format": 0, "other": 6,
    }
    assert state.set_page(2)
    assert [r.work_id for r in state.visible_records] == ["16", "17", "18", "19"]
    assert not state.set_page(3) and not state.set_page(True)
    assert state.select_category("communication")
    assert state.page_index == 0 and len(state.visible_records) == 7
    assert state.counts["not_found"] == 7  # Chips count before filtering.
    assert state.set_query("  12 ")
    assert state.query == "  12 "  # User input is preserved, including whitespace.
    assert [r.work_id for r in state.matched_records] == ["12"]
    assert state.select_category("not_found")
    assert state.matched_records == () and state.total_pages == 1
    assert not state.set_query(None) and not state.select_category("unknown")
    assert state.select_category("all")
    assert state.set_query("作 品") and not state.matched_records


def test_search_uses_original_lower_not_casefold_and_keeps_raw_id_case():
    state = FailedRecordsState((
        FailureRecord("ID-ABC", "u", "Straße", "illust", "", "", 0),
        FailureRecord("lower-id", "u", "STRASSE", "illust", "", "", 0),
    ))
    assert state.set_query("  STRASSE  ")
    assert state.query == "  STRASSE  "
    assert [record.work_id for record in state.matched_records] == ["lower-id"]
    assert state.set_query("straße")
    assert [record.work_id for record in state.matched_records] == ["ID-ABC"]
    assert state.set_query("ID-ABC")
    # gui.py lowercases the search but compares against the original ID.
    assert state.matched_records == ()
    assert state.set_query("lower-id")
    assert [record.work_id for record in state.matched_records] == ["lower-id"]
    assert state.set_query("  ") and len(state.matched_records) == 2
    assert state.query == "  "
    assert not state.set_query("  ")


def test_refresh_is_atomic_retains_filter_and_clamps_page_and_failure_keeps_records():
    state = FailedRecordsState(make_record(i) for i in range(24))
    assert state.set_page(2)
    first = state.begin_refresh()
    assert state.begin_refresh() is None  # No second DB worker for same client.
    assert state.loading and len(state.records) == 24
    assert state.complete_refresh(first, (make_record(i) for i in range(9)), True)
    assert state.page_index == 1 and len(state.visible_records) == 1
    assert state.set_query("作品")
    assert state.select_category("other")
    failed = state.begin_refresh()
    assert state.complete_refresh(failed, (), False)
    assert state.error and not state.loading and len(state.records) == 9
    assert state.query == "作品" and state.category == "other"
    successful = state.begin_refresh()
    assert state.complete_refresh(successful, (), True)
    assert not state.error and state.records == () and state.total_pages == 1


def test_bad_success_payload_never_partially_replaces_and_closed_tabs_reject_old_response():
    original = (make_record(1),)
    state = FailedRecordsState(original)
    token = state.begin_refresh()
    with pytest.raises(ValueError, match="duplicate"):
        state.complete_refresh(token, (make_record(2), make_record(2)), True)
    assert state.records == original and state.loading
    assert state.complete_refresh(token, (make_record(2),), True)
    old = state.begin_refresh()
    state.set_active(False)
    assert not state.complete_refresh(old, (make_record(3),), True)
    assert state.begin_refresh() is None and not state.set_query("3")
    state.set_active(True)
    new = state.begin_refresh()
    assert not state.complete_refresh(old, (make_record(4),), True)
    state.close()
    assert not state.complete_refresh(new, (make_record(5),), True)
    assert state.records == (make_record(2),)
    state.set_active(True)
    assert state.closed and not state.active


def test_tab_invalidation_explicitly_allows_new_request_after_active_refresh():
    state = FailedRecordsState((make_record(1),))
    old_token = state.begin_refresh()
    assert state.begin_refresh() is None
    state.set_active(False)
    state.set_active(True)
    new_token = state.begin_refresh()
    assert new_token is not None and new_token != old_token
    assert not state.complete_refresh(old_token, (make_record(2),), True)
    assert state.complete_refresh(new_token, (make_record(3),), True)
    assert state.records == (make_record(3),)


def test_csv_bom_columns_all_filtered_pages_and_formula_neutralization():
    records = [make_record(i, "Timeout") for i in range(12)]
    records[0] = FailureRecord("=HYPERLINK(1)", "+123", "  @SUM(1,2)", "\t=1+1",
                               "\r\n-9\nsecond line Timeout", "plain", 0)
    state = FailedRecordsState(records)
    assert state.select_category("communication")
    assert len(state.visible_records) == 8
    payload = csv_bytes(state.matched_records)
    assert payload.startswith(b"\xef\xbb\xbf")
    parsed = list(csv.reader(StringIO(payload.decode("utf-8-sig"), newline="")))
    assert parsed[0] == ["work_id", "user_id", "title", "work_type", "error_reason", "failed_at", "retry_count"]
    assert len(parsed) == 13  # Every filtered record, including the second page.
    assert parsed[1] == ["'=HYPERLINK(1)", "'+123", "'  @SUM(1,2)", "'\t=1+1",
                         "'\r\n-9\nsecond line Timeout", "plain", "0"]
    assert records[0].title == "  @SUM(1,2)"  # CSV escaping is presentation-only.
    assert parsed[2][2] == "作品1"


def test_invalid_records_and_csv_input_rejected():
    with pytest.raises(ValueError, match="retry"):
        FailureRecord("a", "", "", "illust", "", "", -1)
    with pytest.raises(ValueError, match="text"):
        FailureRecord("a", "", None, "illust", "", "", 0)
    with pytest.raises(ValueError, match="duplicate"):
        FailedRecordsState((make_record(1), make_record(1)))
    with pytest.raises(ValueError, match="invalid"):
        csv_bytes((make_record(1), "invalid"))
