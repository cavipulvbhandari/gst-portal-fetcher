"""Drive incremental fetching across the whole endpoint catalog."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from gstfetch.client import GstClient, SessionExpiredError, count_records
from gstfetch.endpoints import ResourceSpec
from gstfetch.logging import get_logger
from gstfetch.periods import (
    Period,
    current_open_period,
    financial_years,
    iter_periods,
)
from gstfetch.state import StateStore
from gstfetch.storage import Storage, content_hash

log = get_logger(__name__)

ALL = "_all_"


@dataclass(frozen=True, slots=True)
class WorkUnit:
    spec: ResourceSpec
    period_key: str  # MMYYYY | FY label | '_all_'
    ctx: dict[str, str]
    sealable: bool  # may be marked sealed (skipped next run) once complete


@dataclass
class RunStats:
    fetched: int = 0
    skipped: int = 0
    empty: int = 0
    errors: int = 0


def _fy_is_closed(fy_label: str, open_period: Period) -> bool:
    """An FY is closed once its final period (March) is before the open period."""
    end_year = int(fy_label.split("-")[0]) + 1
    fy_last = Period(month=3, year=end_year)
    return fy_last < open_period


def build_work(
    catalog: list[ResourceSpec],
    gstin: str,
    start: Period,
    open_period: Period,
) -> list[WorkUnit]:
    units: list[WorkUnit] = []
    for spec in catalog:
        if spec.scope == "once":
            units.append(
                WorkUnit(spec, ALL, {"gstin": gstin}, sealable=False)
            )
        elif spec.scope == "monthly":
            for p in iter_periods(start, open_period):
                sealable = spec.seal_closed and p < open_period
                units.append(
                    WorkUnit(
                        spec,
                        p.mmyyyy,
                        {"gstin": gstin, "ret_period": p.mmyyyy},
                        sealable=sealable,
                    )
                )
        elif spec.scope == "fy":
            for fy in financial_years(start, open_period):
                sealable = spec.seal_closed and _fy_is_closed(fy, open_period)
                units.append(
                    WorkUnit(
                        spec,
                        fy,
                        {"gstin": gstin, "fy": fy},
                        sealable=sealable,
                    )
                )
    return units


def run(
    *,
    catalog: list[ResourceSpec],
    client: GstClient,
    store: StateStore,
    storage: Storage,
    gstin: str,
    start: Period,
    today: date | None = None,
    force: bool = False,
) -> RunStats:
    """Fetch every catalog resource for every applicable period, incrementally."""
    open_period = current_open_period(today)
    units = build_work(catalog, gstin, start, open_period)
    stats = RunStats()
    log.info("Planned %d work units (open period %s)", len(units), open_period.mmyyyy)

    for unit in units:
        spec, period = unit.spec, unit.period_key
        if not force and store.is_done(gstin, spec.name, period):
            stats.skipped += 1
            continue

        try:
            payload = client.fetch(spec, unit.ctx)
        except SessionExpiredError:
            store.mark(gstin, spec.name, period, status="pending")
            raise
        except Exception as exc:  # noqa: BLE001 — record and continue
            stats.errors += 1
            store.mark(gstin, spec.name, period, status="error", error=str(exc))
            log.error("%s %s failed: %s", spec.name, period, exc)
            continue

        n = count_records(payload, spec.records_key)
        is_empty = payload in ({}, [], None) or (n == 0)
        storage.write(spec.name, period, payload, source=spec.path)
        store.mark(
            gstin,
            spec.name,
            period,
            status="empty" if is_empty else "complete",
            sealed=unit.sealable,
            content_hash=content_hash(payload),
            record_count=n,
        )
        if is_empty:
            stats.empty += 1
        else:
            stats.fetched += 1
        log.info(
            "%s %s -> %s%s%s",
            spec.name,
            period,
            "empty" if is_empty else "ok",
            f" ({n} records)" if n is not None else "",
            " [sealed]" if unit.sealable else "",
        )

    return stats
