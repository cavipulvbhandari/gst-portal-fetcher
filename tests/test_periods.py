from __future__ import annotations

from datetime import date

import pytest

from gstfetch.periods import (
    Period,
    current_open_period,
    financial_years,
    iter_periods,
)


class TestPeriodParse:
    def test_roundtrip(self) -> None:
        assert Period.parse("072017").mmyyyy == "072017"

    @pytest.mark.parametrize("bad", ["7201", "132017", "002017", "abcdef", "072016"])
    def test_invalid(self, bad: str) -> None:
        with pytest.raises(ValueError):
            Period.parse(bad)


class TestFinancialYear:
    @pytest.mark.parametrize(
        "mmyyyy,fy",
        [
            ("042017", "2017-18"),
            ("032018", "2017-18"),
            ("042018", "2018-19"),
            ("122017", "2017-18"),
            ("012018", "2017-18"),
        ],
    )
    def test_fy(self, mmyyyy: str, fy: str) -> None:
        assert Period.parse(mmyyyy).financial_year == fy


class TestQuarter:
    @pytest.mark.parametrize(
        "mmyyyy,q",
        [("042017", 1), ("062017", 1), ("072017", 2), ("102017", 3), ("012018", 4)],
    )
    def test_quarter(self, mmyyyy: str, q: int) -> None:
        assert Period.parse(mmyyyy).quarter == q


class TestIter:
    def test_inclusive(self) -> None:
        ps = iter_periods(Period.parse("112017"), Period.parse("022018"))
        assert [p.mmyyyy for p in ps] == ["112017", "122017", "012018", "022018"]

    def test_year_wrap_next(self) -> None:
        assert Period.parse("122017").next().mmyyyy == "012018"

    def test_empty_when_reversed(self) -> None:
        assert iter_periods(Period.parse("022018"), Period.parse("112017")) == []


class TestCurrentOpen:
    def test_prev_month(self) -> None:
        assert current_open_period(date(2024, 5, 15)).mmyyyy == "042024"

    def test_january_wraps(self) -> None:
        assert current_open_period(date(2024, 1, 10)).mmyyyy == "122023"


class TestFinancialYears:
    def test_distinct_in_order(self) -> None:
        fys = financial_years(Period.parse("012018"), Period.parse("072018"))
        assert fys == ["2017-18", "2018-19"]
