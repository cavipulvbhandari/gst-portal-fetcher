"""GST return-period and financial-year helpers.

GST periods are encoded as ``MMYYYY`` (e.g. ``072017``). The Indian financial
year runs April -> March; ``2017-18`` is written ``2017-18`` and abbreviated
in portal payloads as the starting calendar year.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class Period:
    """A single GST tax period."""

    month: int  # 1-12
    year: int  # full calendar year, e.g. 2017

    def __post_init__(self) -> None:
        if not 1 <= self.month <= 12:
            raise ValueError(f"month out of range: {self.month}")
        if self.year < 2017:
            raise ValueError(f"GST did not exist before 2017: {self.year}")

    @classmethod
    def parse(cls, mmyyyy: str) -> Period:
        if len(mmyyyy) != 6 or not mmyyyy.isdigit():
            raise ValueError(f"period must be MMYYYY, got {mmyyyy!r}")
        return cls(month=int(mmyyyy[:2]), year=int(mmyyyy[2:]))

    @property
    def mmyyyy(self) -> str:
        return f"{self.month:02d}{self.year}"

    @property
    def financial_year(self) -> str:
        """Return the FY label, e.g. '2017-18'. Apr-Mar boundary."""
        start = self.year if self.month >= 4 else self.year - 1
        return f"{start}-{str(start + 1)[-2:]}"

    @property
    def quarter(self) -> int:
        """GST fiscal quarter (1: Apr-Jun ... 4: Jan-Mar)."""
        return ((self.month - 4) % 12) // 3 + 1

    def next(self) -> Period:
        if self.month == 12:
            return Period(month=1, year=self.year + 1)
        return Period(month=self.month + 1, year=self.year)

    def __lt__(self, other: Period) -> bool:
        return (self.year, self.month) < (other.year, other.month)

    def __le__(self, other: Period) -> bool:
        return (self.year, self.month) <= (other.year, other.month)


def current_open_period(today: date | None = None) -> Period:
    """The latest period whose return could exist.

    A month's return is filed in the *following* month, so the most recent
    period worth fetching is the previous calendar month.
    """
    today = today or date.today()
    if today.month == 1:
        return Period(month=12, year=today.year - 1)
    return Period(month=today.month - 1, year=today.year)


def iter_periods(start: Period, end: Period) -> list[Period]:
    """Inclusive list of periods from ``start`` to ``end``."""
    if end < start:
        return []
    out: list[Period] = []
    cur = start
    while cur <= end:
        out.append(cur)
        cur = cur.next()
    return out


def financial_years(start: Period, end: Period) -> list[str]:
    """Distinct FY labels spanning ``start``..``end``, in order."""
    seen: list[str] = []
    for p in iter_periods(start, end):
        if p.financial_year not in seen:
            seen.append(p.financial_year)
    return seen
