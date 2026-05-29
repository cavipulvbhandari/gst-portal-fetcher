"""Disk storage for fetched payloads.

Layout::

    data/<GSTIN>/<resource>/<period>.json

Each file is the raw JSON payload returned by the portal, plus a small
``_meta`` envelope recording when and from where it was fetched.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def content_hash(payload: Any) -> str:
    """Stable hash of a JSON-serialisable payload (key order independent)."""
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class Storage:
    def __init__(self, client_dir: Path) -> None:
        self.client_dir = client_dir
        self.client_dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, resource: str, period: str) -> Path:
        return self.client_dir / resource / f"{period}.json"

    def write(
        self,
        resource: str,
        period: str,
        payload: Any,
        *,
        source: str,
    ) -> Path:
        path = self.path_for(resource, period)
        path.parent.mkdir(parents=True, exist_ok=True)
        envelope = {
            "_meta": {
                "resource": resource,
                "period": period,
                "source": source,
                "fetched_at": datetime.now(UTC).isoformat(),
                "content_hash": content_hash(payload),
            },
            "data": payload,
        }
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(envelope, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        return path

    def read(self, resource: str, period: str) -> Any | None:
        path = self.path_for(resource, period)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
