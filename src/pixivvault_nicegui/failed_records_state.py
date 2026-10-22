"""Per-client, UI-independent view of legacy failed_queue snapshots.

The source database owns all records. Filters never mutate them; failed or
obsolete refreshes preserve the last successful snapshot. CSV export only
serializes the explicitly supplied records, so callers pass matched_records
when exporting the entire filtered result (including other pages).
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from io import StringIO
from itertools import count
from typing import Iterable


PAGE_SIZE = 8
MAX_RECORDS = 10_000
CSV_COLUMNS = ("work_id", "user_id", "title", "work_type", "error_reason",
               "failed_at", "retry_count")

# The order and case-sensitive literal keyword matching follow gui.py:4082-4097.
CATEGORIES = (
    ("communication", "通信", ("Connection", "接続", "Timeout", "タイムアウト",
                              "応答がありません", "Network", "リトライ回数")),
    ("not_found", "見つからない", ("404", "見つかりません", "見つかりませんでした", "存在しません")),
    ("permission_age", "権限・年齢制限", ("403", "年齢", "権限", "Forbidden")),
    ("save_dest", "保存先", ("Permission", "書き込めません", "No space", "ディスク", "OSError")),
    ("format", "形式", ("0バイト", "ページ不足", "対応していない形式", "変換に失敗",
                       "ugoira", "破損", "unsupported")),
    ("other", "その他", ()),
)
_CATEGORY_KEYS = frozenset(key for key, _, _ in CATEGORIES)
_refresh_tokens = count(1)
_CSV_IGNORABLE_PREFIX = " " + "".join(chr(code) for code in range(33)) + "\x7f"


@dataclass(frozen=True, slots=True)
class FailureRecord:
    work_id: str
    user_id: str
    title: str
    work_type: str
    error_reason: str
    failed_at: str
    retry_count: int

    def __post_init__(self) -> None:
        if any(type(getattr(self, key)) is not str for key in CSV_COLUMNS[:-1]):
            raise ValueError("failed record text must be strings")
        if type(self.retry_count) is not int or self.retry_count < 0:
            raise ValueError("failed record retry count must be non-negative")


def classify_reason(reason: str | None) -> str:
    """Map raw failure text to the *first* matching legacy category.

    Preserve raw text in FailureRecord. In particular, the legacy GUI compares
    keyword case exactly; `timeout` does not match `Timeout`.
    """
    text = reason or ""
    if type(text) is not str:
        raise TypeError("error reason must be text")
    for key, _, keywords in CATEGORIES:
        if any(keyword in text for keyword in keywords):
            return key
    return "other"


def _validated(records: Iterable[FailureRecord]) -> tuple[FailureRecord, ...]:
    result: list[FailureRecord] = []
    seen: set[str] = set()
    for item in records:
        if not isinstance(item, FailureRecord) or item.work_id in seen:
            raise ValueError("invalid or duplicate failed record")
        result.append(item)
        seen.add(item.work_id)
        if len(result) > MAX_RECORDS:
            raise ValueError("failed record limit exceeded")
    return tuple(result)


def _csv_safe(value: str) -> str:
    """Neutralize spreadsheet formulas without changing displayed UI content.

    Spreadsheet apps can execute values beginning with = + - @, and may skip
    leading whitespace or control characters. A literal apostrophe prefixes
    those cells; it is applied only in the exported CSV, not in FailureRecord.
    """
    check = value.lstrip(_CSV_IGNORABLE_PREFIX)
    if (value and (ord(value[0]) < 32 or ord(value[0]) == 127
                   or (check and check[0] in "=+-@"))):
        return "'" + value
    return value


def csv_bytes(records: Iterable[FailureRecord]) -> bytes:
    """Return Excel-readable UTF-8-BOM CSV using the original seven columns.

    This function performs no filesystem write. Never truncate to the visible
    page: pass FailedRecordsState.matched_records for a filtered export.
    """
    valid = _validated(records)
    stream = StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    for record in valid:
        writer.writerow([_csv_safe(getattr(record, name)) for name in CSV_COLUMNS[:-1]]
                        + [record.retry_count])
    return stream.getvalue().encode("utf-8-sig")


class FailedRecordsState:
    """One client's search, category, page and generation-guarded refresh."""

    def __init__(self, records: Iterable[FailureRecord] = ()) -> None:
        self.records = _validated(records)
        self.query = ""
        self.category = "all"
        self.page_index = 0
        self.active = True
        self.closed = False
        self.loading = False
        self.error = False
        self._pending: int | None = None

    @property
    def counts(self) -> dict[str, int]:
        counts = {"all": len(self.records), **{key: 0 for key, _, _ in CATEGORIES}}
        for record in self.records:
            counts[classify_reason(record.error_reason)] += 1
        return counts

    @property
    def matched_records(self) -> tuple[FailureRecord, ...]:
        # gui.py:4165-4170 strips/lowers only for matching. Keep the user's
        # typed value untouched so the input does not get rewritten on render.
        needle = self.query.strip().lower()
        return tuple(record for record in self.records
                     if (self.category == "all" or classify_reason(record.error_reason) == self.category)
                     and (not needle or needle in record.title.lower()
                          or needle in record.work_id))

    @property
    def total_pages(self) -> int:
        return max(1, (len(self.matched_records) + PAGE_SIZE - 1) // PAGE_SIZE)

    @property
    def visible_records(self) -> tuple[FailureRecord, ...]:
        first = self.page_index * PAGE_SIZE
        return self.matched_records[first:first + PAGE_SIZE]

    def _ready(self) -> bool:
        return self.active and not self.closed

    def set_query(self, query: str) -> bool:
        if not self._ready() or type(query) is not str:
            return False
        if query == self.query:
            return False
        self.query = query
        self.page_index = 0
        return True

    def select_category(self, category: str) -> bool:
        if (not self._ready() or type(category) is not str
                or category not in _CATEGORY_KEYS | {"all"} or category == self.category):
            return False
        self.category = category
        self.page_index = 0
        return True

    def set_page(self, index: int) -> bool:
        if (not self._ready() or type(index) is not int
                or not 0 <= index < self.total_pages or index == self.page_index):
            return False
        self.page_index = index
        return True

    def begin_refresh(self) -> int | None:
        # One in-flight read per client. A deliberate tab deactivation first
        # invalidates the old token if a new read must supersede it.
        if not self._ready() or self.loading:
            return None
        self._pending = next(_refresh_tokens)
        self.loading = True
        self.error = False
        return self._pending

    def complete_refresh(self, token: int, records: Iterable[FailureRecord], ok: bool) -> bool:
        """False for stale outcomes; failed loads retain every previous record."""
        if (not self._ready() or not self.loading or type(token) is not int
                or token != self._pending):
            return False
        if type(ok) is not bool:
            raise TypeError("ok must be bool")
        # Fully validate before changing current snapshot or the refresh token.
        replacement = _validated(records) if ok else None
        self._pending = None
        self.loading = False
        self.error = not ok
        if replacement is not None:
            self.records = replacement
            self.page_index = min(self.page_index, self.total_pages - 1)
        return True

    def set_active(self, active: bool) -> None:
        if type(active) is not bool:
            raise TypeError("active must be bool")
        if self.closed:
            return
        if active != self.active:
            self.active = active
            self._pending = None
            self.loading = False

    def close(self) -> None:
        self.closed = True
        self.active = False
        self.loading = False
        self._pending = None
