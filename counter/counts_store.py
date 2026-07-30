"""SQLite cache for table metrics. Independent from schema_cache.db."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from models import CountMode, TableMetrics


class CountsStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS table_metrics (
                    fqn TEXT PRIMARY KEY,
                    row_count INTEGER NOT NULL,
                    count_mode TEXT NOT NULL,
                    count_ts TEXT NOT NULL
                )
                """
            )

    def upsert(self, metrics: TableMetrics) -> None:
        if not isinstance(metrics, TableMetrics):
            raise TypeError("metrics must be TableMetrics")
        self.init_schema()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO table_metrics (fqn, row_count, count_mode, count_ts)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(fqn) DO UPDATE SET
                    row_count=excluded.row_count,
                    count_mode=excluded.count_mode,
                    count_ts=excluded.count_ts
                """,
                (
                    metrics.fqn,
                    metrics.row_count,
                    metrics.count_mode.value,
                    metrics.count_ts.isoformat(),
                ),
            )
            conn.commit()

    def upsert_many(self, items: List[TableMetrics]) -> None:
        if not items:
            return
        if not all(isinstance(m, TableMetrics) for m in items):
            raise TypeError("items must be TableMetrics instances")
        self.init_schema()
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO table_metrics (fqn, row_count, count_mode, count_ts)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(fqn) DO UPDATE SET
                    row_count=excluded.row_count,
                    count_mode=excluded.count_mode,
                    count_ts=excluded.count_ts
                """,
                [
                    (m.fqn, m.row_count, m.count_mode.value, m.count_ts.isoformat())
                    for m in items
                ],
            )
            conn.commit()

    def get(self, fqn: str) -> Optional[TableMetrics]:
        if not self.db_path.exists():
            return None
        self.init_schema()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM table_metrics WHERE fqn = ?", (fqn.lower(),)
            ).fetchone()
        if not row:
            return None
        return TableMetrics(
            fqn=row["fqn"],
            row_count=row["row_count"],
            count_mode=CountMode(row["count_mode"]),
            count_ts=datetime.fromisoformat(row["count_ts"]),
        )

    def load_all(self) -> Dict[str, TableMetrics]:
        if not self.db_path.exists():
            return {}
        self.init_schema()
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM table_metrics").fetchall()
        return {
            r["fqn"]: TableMetrics(
                fqn=r["fqn"],
                row_count=r["row_count"],
                count_mode=CountMode(r["count_mode"]),
                count_ts=datetime.fromisoformat(r["count_ts"]),
            )
            for r in rows
        }

    def mtime(self) -> Optional[float]:
        if not self.db_path.exists():
            return None
        return self.db_path.stat().st_mtime
