"""SQLite persistence for topology (nodes + edges). Never touches counts DB."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from models import EdgeRule, FlowEdge, Layer, TableNode


class SchemaStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS nodes (
                    fqn TEXT PRIMARY KEY,
                    schema_name TEXT NOT NULL,
                    name TEXT NOT NULL,
                    layer TEXT NOT NULL,
                    src_code TEXT
                );
                CREATE TABLE IF NOT EXISTS edges (
                    parent_fqn TEXT NOT NULL,
                    child_fqn TEXT NOT NULL,
                    rule TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    src_code TEXT,
                    PRIMARY KEY (parent_fqn, child_fqn, rule)
                );
                """
            )

    def replace_all(self, nodes: List[TableNode], edges: List[FlowEdge]) -> None:
        if not all(isinstance(n, TableNode) for n in nodes):
            raise TypeError("nodes must be TableNode instances")
        if not all(isinstance(e, FlowEdge) for e in edges):
            raise TypeError("edges must be FlowEdge instances")
        self.init_schema()
        with self._connect() as conn:
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("BEGIN")
            conn.execute("DELETE FROM nodes")
            conn.execute("DELETE FROM edges")
            conn.executemany(
                "INSERT INTO nodes (fqn, schema_name, name, layer, src_code) VALUES (?,?,?,?,?)",
                [
                    (n.fqn, n.schema_name, n.name, n.layer.value, n.src_code)
                    for n in nodes
                ],
            )
            conn.executemany(
                "INSERT INTO edges (parent_fqn, child_fqn, rule, confidence, src_code) VALUES (?,?,?,?,?)",
                [
                    (e.parent_fqn, e.child_fqn, e.rule.value, e.confidence, e.src_code)
                    for e in edges
                ],
            )
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('built_at', ?)",
                (datetime.now(timezone.utc).isoformat(),),
            )
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
                ("1",),
            )
            conn.commit()

    def load(self) -> Tuple[List[TableNode], List[FlowEdge]]:
        if not self.db_path.exists():
            return [], []
        with self._connect() as conn:
            node_rows = conn.execute("SELECT * FROM nodes").fetchall()
            edge_rows = conn.execute("SELECT * FROM edges").fetchall()
        nodes = [
            TableNode(
                schema=r["schema_name"],
                name=r["name"],
                layer=Layer(r["layer"]),
                src_code=r["src_code"],
            )
            for r in node_rows
        ]
        edges = [
            FlowEdge(
                parent_fqn=r["parent_fqn"],
                child_fqn=r["child_fqn"],
                rule=EdgeRule(r["rule"]),
                confidence=r["confidence"],
                src_code=r["src_code"],
            )
            for r in edge_rows
        ]
        return nodes, edges

    def edge_count(self) -> int:
        if not self.db_path.exists():
            return 0
        with self._connect() as conn:
            return int(conn.execute("SELECT count(*) FROM edges").fetchone()[0])

    def built_at(self) -> Optional[str]:
        if not self.db_path.exists():
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM meta WHERE key='built_at'"
            ).fetchone()
            return row["value"] if row else None

    def mtime(self) -> Optional[float]:
        if not self.db_path.exists():
            return None
        return self.db_path.stat().st_mtime
