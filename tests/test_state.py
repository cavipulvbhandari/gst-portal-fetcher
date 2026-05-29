from __future__ import annotations

from pathlib import Path

from gstfetch.state import StateStore

GSTIN = "27AAACX1234F1Z5"


def _store(tmp_path: Path) -> StateStore:
    return StateStore(tmp_path / "checkpoints.sqlite")


def test_unknown_unit_is_not_done(tmp_path: Path) -> None:
    s = _store(tmp_path)
    assert s.is_done(GSTIN, "gstr3b", "072017") is False
    s.close()


def test_sealed_complete_is_done(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.mark(GSTIN, "gstr3b", "072017", status="complete", sealed=True)
    assert s.is_done(GSTIN, "gstr3b", "072017") is True
    s.close()


def test_unsealed_complete_is_not_done(tmp_path: Path) -> None:
    # Open periods must be re-fetched to catch late filings / amendments.
    s = _store(tmp_path)
    s.mark(GSTIN, "gstr3b", "042024", status="complete", sealed=False)
    assert s.is_done(GSTIN, "gstr3b", "042024") is False
    s.close()


def test_empty_sealed_is_done(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.mark(GSTIN, "gstr1", "082017", status="empty", sealed=True)
    assert s.is_done(GSTIN, "gstr1", "082017") is True
    s.close()


def test_error_is_not_done(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.mark(GSTIN, "gstr1", "082017", status="error", sealed=True, error="boom")
    assert s.is_done(GSTIN, "gstr1", "082017") is False
    s.close()


def test_upsert_overwrites(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.mark(GSTIN, "gstr1", "082017", status="error", error="boom")
    s.mark(GSTIN, "gstr1", "082017", status="complete", sealed=True, record_count=5)
    rec = s.get(GSTIN, "gstr1", "082017")
    assert rec is not None
    assert rec.status == "complete"
    assert rec.record_count == 5
    assert rec.error is None
    s.close()


def test_summary_counts(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.mark(GSTIN, "gstr1", "072017", status="complete", sealed=True)
    s.mark(GSTIN, "gstr1", "082017", status="complete", sealed=True)
    s.mark(GSTIN, "gstr3b", "072017", status="error")
    assert s.summary(GSTIN) == {"complete": 2, "error": 1}
    s.close()
