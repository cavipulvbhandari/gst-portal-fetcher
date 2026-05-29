"""Export fetched JSON into flat, analyst-friendly files (CSV / JSONL).

GST payload shapes are undocumented and vary per resource, so export is
deliberately schema-agnostic:

* ``jsonl`` — one line per period: ``{"period": ..., "data": <raw payload>}``.
* ``csv``  — each period flattened to dotted-key columns; the CSV header is the
  union of keys seen across all periods (period column first).

Both are lossless enough to load into Excel / pandas without guessing at the
portal's nesting.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


def flatten(obj: Any, prefix: str = "") -> dict[str, str]:
    """Flatten nested dict/list into ``{dotted.key: stringified_value}``."""
    out: dict[str, str] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            out.update(flatten(v, key))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            key = f"{prefix}[{i}]"
            out.update(flatten(v, key))
    else:
        out[prefix] = "" if obj is None else str(obj)
    return out


def _resource_dirs(client_dir: Path) -> list[Path]:
    # Skip underscore-prefixed dirs (e.g. the _export output folder).
    return sorted(
        p for p in client_dir.iterdir() if p.is_dir() and not p.name.startswith("_")
    )


def _period_files(resource_dir: Path) -> list[Path]:
    return sorted(resource_dir.glob("*.json"))


def _load_data(period_file: Path) -> Any:
    envelope = json.loads(period_file.read_text(encoding="utf-8"))
    # Files written by storage.Storage wrap the payload under "data".
    return envelope.get("data", envelope) if isinstance(envelope, dict) else envelope


def export_resource_jsonl(resource_dir: Path, out_file: Path) -> int:
    out_file.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with out_file.open("w", encoding="utf-8") as fh:
        for pf in _period_files(resource_dir):
            row = {"period": pf.stem, "data": _load_data(pf)}
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
    return n


def export_resource_csv(resource_dir: Path, out_file: Path) -> int:
    rows: list[dict[str, str]] = []
    keys: list[str] = ["period"]
    seen: set[str] = {"period"}
    for pf in _period_files(resource_dir):
        flat = flatten(_load_data(pf))
        flat["period"] = pf.stem
        for k in flat:
            if k not in seen:
                seen.add(k)
                keys.append(k)
        rows.append(flat)

    out_file.parent.mkdir(parents=True, exist_ok=True)
    with out_file.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return len(rows)


def export_all(client_dir: Path, out_dir: Path, fmt: str) -> dict[str, int]:
    """Export every resource folder under ``client_dir``. Returns {resource: rows}."""
    if fmt not in ("csv", "jsonl"):
        raise ValueError(f"unsupported format: {fmt!r}")
    out_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, int] = {}
    for rdir in _resource_dirs(client_dir):
        ext = "csv" if fmt == "csv" else "jsonl"
        out_file = out_dir / f"{rdir.name}.{ext}"
        if fmt == "csv":
            result[rdir.name] = export_resource_csv(rdir, out_file)
        else:
            result[rdir.name] = export_resource_jsonl(rdir, out_file)
    return result
