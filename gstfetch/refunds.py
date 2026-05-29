"""Refund-application fetching: date-window chunking and ARN grouping.

The portal's *My Applications -> Refunds* register is queried by a from/to date
range, but the portal rejects ranges longer than a few months, so a long span
(e.g. Jan-2019 -> Mar-2026) must be split into smaller windows. The same refund
application (ARN) can appear in more than one window when windows touch, so the
results are de-duplicated and grouped by ARN.

The two functions here are pure and undocumented-endpoint-agnostic: they don't
know the refund API's URL or exact response shape. The list-response is reduced
to per-ARN records via a configurable key path, so when the real endpoint is
captured (`gstfetch capture`) only configuration changes, not this code.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

# The portal caps the My Applications date range. 180 days is comfortably within
# the limit while keeping the number of windows for a 7-year span reasonable.
DEFAULT_WINDOW_DAYS = 180


@dataclass(frozen=True, slots=True)
class DateWindow:
    """An inclusive [from_date, to_date] query window."""

    from_date: date
    to_date: date

    @property
    def label(self) -> str:
        """Stable key for checkpoints/storage, e.g. ``20190101-20190630``."""
        return f"{self.from_date:%Y%m%d}-{self.to_date:%Y%m%d}"

    def as_ddmmyyyy(self) -> tuple[str, str]:
        """Portal-style ``dd-mm-yyyy`` pair (the format its date pickers use)."""
        return (f"{self.from_date:%d-%m-%Y}", f"{self.to_date:%d-%m-%Y}")


def date_windows(
    start: date, end: date, window_days: int = DEFAULT_WINDOW_DAYS
) -> list[DateWindow]:
    """Split ``start``..``end`` (inclusive) into consecutive windows.

    Windows are contiguous and non-overlapping; the last one is truncated at
    ``end``. Returns ``[]`` if ``end`` precedes ``start``.
    """
    if window_days < 1:
        raise ValueError(f"window_days must be >= 1, got {window_days}")
    if end < start:
        return []
    windows: list[DateWindow] = []
    cur = start
    step = timedelta(days=window_days - 1)  # inclusive span of window_days
    while cur <= end:
        win_end = min(cur + step, end)
        windows.append(DateWindow(from_date=cur, to_date=win_end))
        cur = win_end + timedelta(days=1)
    return windows


def _dig(obj: Any, dotted_key: str) -> Any:
    """Follow a dotted path into nested dicts; return None if any hop misses."""
    cur = obj
    for part in dotted_key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def extract_records(payload: Any, records_key: str | None) -> list[dict[str, Any]]:
    """Pull the list of refund rows out of one window's raw response.

    ``records_key`` is a dotted path to the list inside the payload (verify it
    from the captured response). If it is None, the payload is assumed to be the
    list itself.
    """
    target = payload if records_key is None else _dig(payload, records_key)
    if isinstance(target, list):
        return [r for r in target if isinstance(r, dict)]
    return []


def group_by_arn(
    rows: Iterable[dict[str, Any]], arn_key: str = "arn"
) -> dict[str, dict[str, Any]]:
    """De-duplicate refund rows by ARN, preserving first-seen order.

    Rows without a usable ARN under ``arn_key`` are skipped (they cannot be
    grouped or fetched in detail). When the same ARN recurs across overlapping
    windows the first occurrence is kept.
    """
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        arn = row.get(arn_key)
        if not isinstance(arn, str) or not arn.strip():
            continue
        arn = arn.strip()
        if arn not in grouped:
            grouped[arn] = row
    return grouped
