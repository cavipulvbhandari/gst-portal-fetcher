"""SQLite-backed checkpoint store — the engine of incremental fetching.

Each (gstin, resource, period) pair gets one row recording whether it has
been fetched, when, a hash of the payload, and whether the period is *sealed*
(immutable, e.g. a filed return for a closed period) so future runs skip it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fetch_log (
    gstin        TEXT NOT NULL,
    resource     TEXT NOT NULL,
    period       TEXT NOT NULL,           -- MMYYYY, FY label, or '_all_'
    status       TEXT NOT NULL,           -- pending | complete | empty | error
    sealed       INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT,
    record_count INTEGER,
    error        TEXT,
    fetched_at   TEXT,
    PRIMARY KEY (gstin, resource, period)
);
CREATE INDEX IF NOT EXISTS idx_fetch_log_status ON fetch_log (gstin, status);
"""


@dataclass(frozen=True, slots=True)
class FetchRecord:
    gstin: str
    resource: str
    period: str
    status: str
    sealed: bool
    content_hash: str | None
    record_count: int | None
    error: str | None
    fetched_at: str | None


def _now() -> str:
    return datetime.now(UTC).isoformat()


class StateStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def get(self, gstin: str, resource: str, period: str) -> FetchRecord | None:
        row = self._conn.execute(
            "SELECT * FROM fetch_log WHERE gstin = ? AND resource = ? AND period = ?",
            (gstin, resource, period),
        ).fetchone()
        return _row_to_record(row) if row else None

    def is_done(self, gstin: str, resource: str, period: str) -> bool:
        """A unit is skippable only if it completed AND is sealed.

        Open periods (sealed=0) are always re-fetched so late filings and
        amendments are picked up.
        """
        rec = self.get(gstin, resource, period)
        return rec is not None and rec.status in ("complete", "empty") and rec.sealed

    def mark(
        self,
        gstin: str,
        resource: str,
        period: str,
        *,
        status: str,
        sealed: bool = False,
        content_hash: str | None = None,
        record_count: int | None = None,
        error: str | None = None,
    ) -> None:
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO fetch_log
                    (gstin, resource, period, status, sealed,
                     content_hash, record_count, error, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(gstin, resource, period) DO UPDATE SET
                    status = excluded.status,
                    sealed = excluded.sealed,
                    content_hash = excluded.content_hash,
                    record_count = excluded.record_count,
                    error = excluded.error,
                    fetched_at = excluded.fetched_at
                """,
                (
                    gstin, resource, period, status, int(sealed),
                    content_hash, record_count, error, _now(),
                ),
            )

    def summary(self, gstin: str) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT status, COUNT(*) AS n FROM fetch_log WHERE gstin = ? GROUP BY status",
            (gstin,),
        ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    def records(self, gstin: str) -> list[FetchRecord]:
        rows = self._conn.execute(
            "SELECT * FROM fetch_log WHERE gstin = ? ORDER BY resource, period",
            (gstin,),
        ).fetchall()
        return [_row_to_record(r) for r in rows]


def _row_to_record(row: sqlite3.Row) -> FetchRecord:
    return FetchRecord(
        gstin=row["gstin"],
        resource=row["resource"],
        period=row["period"],
        status=row["status"],
        sealed=bool(row["sealed"]),
        content_hash=row["content_hash"],
        record_count=row["record_count"],
        error=row["error"],
        fetched_at=row["fetched_at"],
    )
