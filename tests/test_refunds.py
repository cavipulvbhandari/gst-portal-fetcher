from __future__ import annotations

from datetime import date

from gstfetch.refunds import (
    DateWindow,
    date_windows,
    extract_records,
    group_by_arn,
)


def test_date_windows_splits_long_span() -> None:
    wins = date_windows(date(2019, 1, 1), date(2026, 3, 31), window_days=180)
    # Contiguous, non-overlapping, fully covering the span.
    assert wins[0].from_date == date(2019, 1, 1)
    assert wins[-1].to_date == date(2026, 3, 31)
    for earlier, later in zip(wins, wins[1:], strict=True):
        assert later.from_date == earlier.to_date + (date(2019, 1, 2) - date(2019, 1, 1))
        assert earlier.to_date < later.from_date


def test_date_windows_window_length() -> None:
    wins = date_windows(date(2020, 1, 1), date(2020, 12, 31), window_days=180)
    # First window spans exactly 180 inclusive days.
    assert wins[0] == DateWindow(date(2020, 1, 1), date(2020, 6, 28))


def test_date_windows_edge_cases() -> None:
    assert date_windows(date(2021, 5, 1), date(2021, 4, 1)) == []  # end before start
    single = date_windows(date(2021, 1, 1), date(2021, 1, 1))
    assert single == [DateWindow(date(2021, 1, 1), date(2021, 1, 1))]


def test_date_window_labels_and_format() -> None:
    w = DateWindow(date(2019, 1, 1), date(2019, 6, 30))
    assert w.label == "20190101-20190630"
    assert w.as_ddmmyyyy() == ("01-01-2019", "30-06-2019")


def test_extract_records_with_key() -> None:
    payload = {"data": {"applications": [{"arn": "A1"}, {"arn": "A2"}, "junk"]}}
    rows = extract_records(payload, "data.applications")
    assert rows == [{"arn": "A1"}, {"arn": "A2"}]


def test_extract_records_missing_key() -> None:
    assert extract_records({"data": {}}, "data.applications") == []
    assert extract_records([{"arn": "A1"}], None) == [{"arn": "A1"}]
    assert extract_records("not a list", None) == []


def test_group_by_arn_dedups_across_windows() -> None:
    rows = [
        {"arn": "AA01", "status": "Filed"},
        {"arn": "AA02", "status": "Filed"},
        {"arn": "AA01", "status": "Processed"},  # duplicate across windows
        {"status": "no arn"},  # skipped
        {"arn": "  ", "status": "blank arn"},  # skipped
    ]
    grouped = group_by_arn(rows)
    assert list(grouped) == ["AA01", "AA02"]  # first-seen order preserved
    assert grouped["AA01"]["status"] == "Filed"  # first occurrence kept


def test_group_by_arn_custom_key() -> None:
    rows = [{"applicationReferenceNumber": "X1"}]
    grouped = group_by_arn(rows, arn_key="applicationReferenceNumber")
    assert list(grouped) == ["X1"]
