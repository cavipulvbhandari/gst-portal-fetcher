"""Catalog of GST portal internal endpoints.

IMPORTANT: the GST portal's internal JSON APIs are undocumented and change
over time. The paths below are a best-effort starting catalog. Verify and
correct them using ``gstfetch capture`` (which records live traffic to a HAR
file and lists the real endpoints) before relying on production output.

A user-supplied ``endpoints.yaml`` is merged over this default catalog, so you
can fix paths without editing code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

Scope = Literal["once", "monthly", "fy"]


@dataclass(frozen=True, slots=True)
class ResourceSpec:
    """How to fetch one logical resource from the portal.

    Attributes:
        name: stable resource key, also the on-disk folder name.
        scope: ``once`` (single snapshot), ``monthly`` (per return period),
            or ``fy`` (per financial year).
        method: HTTP method.
        path: path template, may contain ``{gstin}``, ``{ret_period}``, ``{fy}``.
        params: query-param template; values may contain the same placeholders.
        records_key: dotted path into the response used to count records.
        seal_closed: if True, a period older than the current open period is
            marked sealed once fetched (skipped on later runs).
    """

    name: str
    scope: Scope
    method: str
    path: str
    params: dict[str, str] = field(default_factory=dict)
    records_key: str | None = None
    seal_closed: bool = True


# Best-effort default catalog. Paths under /services/api/ mirror the portal SPA.
DEFAULT_CATALOG: tuple[ResourceSpec, ...] = (
    ResourceSpec(
        name="profile",
        scope="once",
        method="GET",
        path="/services/api/get/gstndata",
        params={"gstin": "{gstin}"},
        seal_closed=False,
    ),
    ResourceSpec(
        name="filing_history",
        scope="fy",
        method="GET",
        path="/services/api/returns/dashboard",
        params={"gstin": "{gstin}", "fy": "{fy}"},
        records_key="filingStatus",
    ),
    ResourceSpec(
        name="gstr1",
        scope="monthly",
        method="GET",
        path="/services/api/returns/gstr1",
        params={"gstin": "{gstin}", "ret_period": "{ret_period}"},
    ),
    ResourceSpec(
        name="gstr3b",
        scope="monthly",
        method="GET",
        path="/services/api/returns/gstr3b",
        params={"gstin": "{gstin}", "ret_period": "{ret_period}"},
    ),
    ResourceSpec(
        name="gstr2b",
        scope="monthly",
        method="GET",
        path="/services/api/returns/gstr2b",
        params={"gstin": "{gstin}", "ret_period": "{ret_period}"},
    ),
    ResourceSpec(
        name="ledger_cash",
        scope="fy",
        method="GET",
        path="/services/api/ledger/cash",
        params={"gstin": "{gstin}", "fy": "{fy}"},
    ),
    ResourceSpec(
        name="ledger_credit",
        scope="fy",
        method="GET",
        path="/services/api/ledger/itc",
        params={"gstin": "{gstin}", "fy": "{fy}"},
    ),
    ResourceSpec(
        name="ledger_liability",
        scope="fy",
        method="GET",
        path="/services/api/ledger/liability",
        params={"gstin": "{gstin}", "fy": "{fy}"},
    ),
    ResourceSpec(
        name="notices_orders",
        scope="once",
        method="GET",
        path="/services/api/notices",
        params={"gstin": "{gstin}"},
        records_key="notices",
        seal_closed=False,
    ),
    ResourceSpec(
        name="challans",
        scope="fy",
        method="GET",
        path="/services/api/payments/challans",
        params={"gstin": "{gstin}", "fy": "{fy}"},
    ),
)


def load_catalog(override_file: Path | None = None) -> list[ResourceSpec]:
    """Return the catalog, merging an optional YAML override by resource name."""
    by_name = {spec.name: spec for spec in DEFAULT_CATALOG}
    if override_file and override_file.exists():
        raw = yaml.safe_load(override_file.read_text(encoding="utf-8")) or {}
        for name, patch in (raw.get("resources") or {}).items():
            base = by_name.get(name)
            merged = {
                "name": name,
                "scope": patch.get("scope", base.scope if base else "once"),
                "method": patch.get("method", base.method if base else "GET"),
                "path": patch.get("path", base.path if base else ""),
                "params": patch.get("params", dict(base.params) if base else {}),
                "records_key": patch.get(
                    "records_key", base.records_key if base else None
                ),
                "seal_closed": patch.get(
                    "seal_closed", base.seal_closed if base else True
                ),
            }
            by_name[name] = ResourceSpec(**merged)
    return list(by_name.values())
