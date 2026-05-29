from __future__ import annotations

from pathlib import Path

import pytest

from gstfetch.config import Settings
from gstfetch.endpoints import load_catalog
from gstfetch.storage import Storage, content_hash


class TestSettings:
    def test_valid_gstin_uppercased(self) -> None:
        s = Settings(gstin="27aaacx1234f1z5", _env_file=None)  # type: ignore[call-arg]
        assert s.gstin == "27AAACX1234F1Z5"
        assert s.state_code == "27"

    def test_invalid_gstin(self) -> None:
        with pytest.raises(ValueError):
            Settings(gstin="not-a-gstin", _env_file=None)  # type: ignore[call-arg]

    def test_invalid_start_period(self) -> None:
        with pytest.raises(ValueError):
            Settings(start_period="2017", _env_file=None)  # type: ignore[call-arg]


class TestStorage:
    def test_write_read_roundtrip(self, tmp_path: Path) -> None:
        st = Storage(tmp_path / "27AAACX1234F1Z5")
        st.write("gstr3b", "072017", {"a": 1}, source="/x")
        got = st.read("gstr3b", "072017")
        assert got is not None
        assert got["data"] == {"a": 1}
        assert got["_meta"]["resource"] == "gstr3b"

    def test_content_hash_stable_across_key_order(self) -> None:
        assert content_hash({"a": 1, "b": 2}) == content_hash({"b": 2, "a": 1})


class TestCatalogOverride:
    def test_yaml_override_merges(self, tmp_path: Path) -> None:
        override = tmp_path / "endpoints.yaml"
        override.write_text(
            "resources:\n"
            "  gstr3b:\n"
            "    path: /services/api/returns/gstr3b/v2\n",
            encoding="utf-8",
        )
        catalog = {s.name: s for s in load_catalog(override)}
        assert catalog["gstr3b"].path == "/services/api/returns/gstr3b/v2"
        # Untouched fields retained from default.
        assert catalog["gstr3b"].scope == "monthly"
