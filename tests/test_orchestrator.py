from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from gstfetch.client import WafBlockedError, count_records
from gstfetch.endpoints import load_catalog
from gstfetch.orchestrator import ALL, _fy_is_closed, build_work, run
from gstfetch.periods import Period
from gstfetch.state import StateStore
from gstfetch.storage import Storage

GSTIN = "27AAACX1234F1Z5"


class FakeClient:
    """Stand-in for GstClient returning canned payloads, no network."""

    def __init__(self, payloads: dict[str, object] | None = None) -> None:
        self.payloads = payloads or {}
        self.calls: list[tuple[str, dict[str, str]]] = []

    def fetch(self, spec: object, ctx: dict[str, str]) -> object:  # type: ignore[override]
        name = spec.name  # type: ignore[attr-defined]
        self.calls.append((name, dict(ctx)))
        return self.payloads.get(name, {"ok": True, "value": 1})


def test_fy_is_closed() -> None:
    open_period = Period.parse("042024")
    assert _fy_is_closed("2017-18", open_period) is True
    assert _fy_is_closed("2023-24", open_period) is True  # ends Mar-2024 < Apr-2024
    assert _fy_is_closed("2024-25", open_period) is False


def test_build_work_scopes() -> None:
    catalog = load_catalog(None)
    start = Period.parse("032024")
    open_period = Period.parse("042024")
    units = build_work(catalog, GSTIN, start, open_period)

    once = [u for u in units if u.spec.scope == "once"]
    assert all(u.period_key == ALL and not u.sealable for u in once)

    gstr3b = [u for u in units if u.spec.name == "gstr3b"]
    assert {u.period_key for u in gstr3b} == {"032024", "042024"}
    sealed = {u.period_key: u.sealable for u in gstr3b}
    assert sealed["032024"] is True  # closed period -> sealable
    assert sealed["042024"] is False  # open period -> always refetch


def test_count_records() -> None:
    assert count_records({"notices": [1, 2, 3]}, "notices") == 3
    assert count_records({"a": {"b": [1]}}, "a.b") == 1
    assert count_records({"x": 1}, "missing") is None
    assert count_records("not a dict", "x") is None


def test_run_writes_and_checkpoints(tmp_path: Path) -> None:
    catalog = load_catalog(None)
    store = StateStore(tmp_path / "cp.sqlite")
    storage = Storage(tmp_path / GSTIN)
    client = FakeClient()
    start = Period.parse("032024")

    stats = run(
        catalog=catalog,
        client=client,  # type: ignore[arg-type]
        store=store,
        storage=storage,
        gstin=GSTIN,
        start=start,
        today=date(2024, 5, 15),  # open period = 042024
    )
    assert stats.fetched > 0
    # A sealed closed period should be skipped on a second run.
    before = len(client.calls)
    stats2 = run(
        catalog=catalog,
        client=client,  # type: ignore[arg-type]
        store=store,
        storage=storage,
        gstin=GSTIN,
        start=start,
        today=date(2024, 5, 15),
    )
    assert stats2.skipped > 0
    assert len(client.calls) < before * 2  # fewer calls the second time

    # Data landed on disk.
    p = storage.path_for("gstr3b", "032024")
    assert p.exists()
    store.close()


class WafClient:
    """Client that always trips the firewall."""

    def fetch(self, spec: object, ctx: dict[str, str]) -> object:
        raise WafBlockedError("firewall rejected request")


def test_run_aborts_on_waf_block(tmp_path: Path) -> None:
    catalog = load_catalog(None)
    store = StateStore(tmp_path / "cp.sqlite")
    storage = Storage(tmp_path / GSTIN)
    start = Period.parse("032024")

    with pytest.raises(WafBlockedError):
        run(
            catalog=catalog,
            client=WafClient(),  # type: ignore[arg-type]
            store=store,
            storage=storage,
            gstin=GSTIN,
            start=start,
            today=date(2024, 5, 15),
        )
    # The first unit is left pending so a re-run resumes, not skips.
    summary = store.summary(GSTIN)
    assert summary.get("pending", 0) >= 1
    store.close()


def test_run_force_refetches(tmp_path: Path) -> None:
    catalog = load_catalog(None)
    store = StateStore(tmp_path / "cp.sqlite")
    storage = Storage(tmp_path / GSTIN)
    client = FakeClient()
    start = Period.parse("032024")
    common = dict(
        catalog=catalog, store=store, storage=storage, gstin=GSTIN,
        start=start, today=date(2024, 5, 15),
    )
    run(client=client, **common)  # type: ignore[arg-type]
    n_first = len(client.calls)
    run(client=client, force=True, **common)  # type: ignore[arg-type]
    assert len(client.calls) == 2 * n_first  # force ignores checkpoints
    store.close()
