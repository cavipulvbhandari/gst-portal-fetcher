from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from gstfetch.export import export_all, export_resource_csv, flatten
from gstfetch.storage import Storage

GSTIN = "27AAACX1234F1Z5"


class TestFlatten:
    def test_nested_dict(self) -> None:
        assert flatten({"a": {"b": 1}}) == {"a.b": "1"}

    def test_list_indexing(self) -> None:
        assert flatten({"xs": [10, 20]}) == {"xs[0]": "10", "xs[1]": "20"}

    def test_list_of_dicts(self) -> None:
        out = flatten({"items": [{"k": "v"}, {"k": "w"}]})
        assert out == {"items[0].k": "v", "items[1].k": "w"}

    def test_none_becomes_empty(self) -> None:
        assert flatten({"a": None}) == {"a": ""}


def _seed(tmp_path: Path) -> Storage:
    st = Storage(tmp_path / GSTIN)
    st.write("gstr3b", "072017", {"liability": {"igst": 100}}, source="/x")
    st.write("gstr3b", "082017", {"liability": {"igst": 200, "cgst": 50}}, source="/x")
    st.write("profile", "_all_", {"legalName": "Acme", "status": "active"}, source="/x")
    return st


def test_export_csv_union_columns(tmp_path: Path) -> None:
    _seed(tmp_path)
    out = tmp_path / "export" / "gstr3b.csv"
    n = export_resource_csv(tmp_path / GSTIN / "gstr3b", out)
    assert n == 2
    with out.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert {r["period"] for r in rows} == {"072017", "082017"}
    # Union of keys across periods is present; missing cells are blank.
    assert "liability.cgst" in rows[0]
    aug = next(r for r in rows if r["period"] == "072017")
    assert aug["liability.cgst"] == ""
    assert aug["liability.igst"] == "100"


def test_export_all_jsonl(tmp_path: Path) -> None:
    _seed(tmp_path)
    result = export_all(tmp_path / GSTIN, tmp_path / "out", "jsonl")
    assert result == {"gstr3b": 2, "profile": 1}
    lines = (tmp_path / "out" / "gstr3b.jsonl").read_text(encoding="utf-8").splitlines()
    parsed = [json.loads(line) for line in lines]
    assert {p["period"] for p in parsed} == {"072017", "082017"}
    assert parsed[0]["data"]["liability"]["igst"] == 100


def test_export_skips_underscore_output_dir(tmp_path: Path) -> None:
    # Output dir lives inside the client dir; a second run must not treat the
    # prior _export folder as a resource.
    _seed(tmp_path)
    client = tmp_path / GSTIN
    export_all(client, client / "_export", "csv")
    result = export_all(client, client / "_export", "csv")
    assert "_export" not in result
    assert set(result) == {"gstr3b", "profile"}


def test_export_rejects_bad_format(tmp_path: Path) -> None:
    _seed(tmp_path)
    with pytest.raises(ValueError):
        export_all(tmp_path / GSTIN, tmp_path / "out", "xml")
