"""Offline unit tests for the Microvista fetcher's pure helpers."""

from __future__ import annotations

from microvista import portal
from microvista.config import Settings


def test_gstin_regex_matches_valid_and_ignores_noise() -> None:
    text = "Company 27ABMPL5400A1Z4 | NARESH MANAKLAL LUNAWAT"
    m = portal.GSTIN_RE.search(text)
    assert m is not None
    assert m.group(0) == "27ABMPL5400A1Z4"


def test_gstin_regex_rejects_malformed() -> None:
    assert portal.GSTIN_RE.search("not-a-gstin 12345") is None


def test_ref_id_regex_extracts_reference() -> None:
    row = "Additional Notices ZD2711250732895 DETERMINATION Closed"
    m = portal.REF_ID_RE.search(row)
    assert m is not None
    assert m.group(0) == "ZD2711250732895"


def test_sanitize_makes_safe_filenames() -> None:
    assert portal._sanitize("ZD270925120641E") == "ZD270925120641E"
    assert portal._sanitize("bad/name:with*chars") == "bad_name_with_chars"
    assert portal._sanitize("///") == "notice"


def test_notice_row_to_dict_roundtrips() -> None:
    row = portal.NoticeRow(ref_id="ZD123456789", status="Open", section="Notices")
    d = row.to_dict()
    assert d["ref_id"] == "ZD123456789"
    assert d["status"] == "Open"
    assert d["pdf_file"] is None


def test_settings_gstin_filter_parsing() -> None:
    s = Settings(gstins="27ABMPL5400A1Z4, 09aaacx1234f1z5")
    assert s.gstin_filter == {"27ABMPL5400A1Z4", "09AAACX1234F1Z5"}


def test_settings_empty_gstin_filter() -> None:
    assert Settings(gstins="").gstin_filter == set()


def test_settings_strips_trailing_slash_and_timeout_ms() -> None:
    s = Settings(base_url="https://noticealert.microvistatech.com/", timeout=30.0)
    assert s.base_url == "https://noticealert.microvistatech.com"
    assert s.timeout_ms == 30_000
