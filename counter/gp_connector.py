"""Read-only GP connector with SELECT whitelist and optional session reuse."""
from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence

_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|UPSERT|TRUNCATE|CREATE|ALTER|DROP|RENAME|GRANT|REVOKE|CALL|COPY|VACUUM)\b",
    re.IGNORECASE,
)
_ALLOWED_START = re.compile(r"^\s*(SELECT|WITH|EXPLAIN|SHOW|DESCRIBE)\b", re.IGNORECASE)

log = logging.getLogger(__name__)


class GpConnectorError(Exception):
    pass


class ReadOnlySqlGuard:
    @staticmethod
    def assert_readonly(sql: str) -> None:
        text = sql.strip()
        if not text:
            raise GpConnectorError("empty SQL")
        if not _ALLOWED_START.match(text):
            raise GpConnectorError(
                f"only SELECT/WITH/EXPLAIN/SHOW allowed, got: {text[:80]}"
            )
        stripped = re.sub(r"'[^']*'", "''", text)
        if _FORBIDDEN.search(stripped):
            raise GpConnectorError(f"mutating SQL rejected: {text[:80]}")


ConnectionFactory = Callable[[], Any]


class GpConnector:
    def __init__(self, connection_factory: Optional[ConnectionFactory] = None):
        self._factory = connection_factory
        self._session_conn: Any = None

    def set_connection_factory(self, factory: ConnectionFactory) -> None:
        self._factory = factory

    @property
    def in_session(self) -> bool:
        return self._session_conn is not None

    def open_session(self) -> None:
        if self._session_conn is not None:
            return
        if self._factory is None:
            raise GpConnectorError("connection factory not configured")
        self._session_conn = self._factory()
        log.info("ssh session open")

    def close_session(self) -> None:
        if self._session_conn is None:
            return
        try:
            self._session_conn.close()
        except Exception:
            pass
        self._session_conn = None
        log.info("ssh session closed")

    @contextmanager
    def session(self) -> Iterator["GpConnector"]:
        self.open_session()
        try:
            yield self
        finally:
            self.close_session()

    @contextmanager
    def connection(self) -> Iterator[Any]:
        if self._session_conn is not None:
            yield self._session_conn
            return
        if self._factory is None:
            raise GpConnectorError("connection factory not configured")
        conn = self._factory()
        try:
            yield conn
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def fetch_all(
        self, sql: str, params: Optional[Sequence[Any]] = None
    ) -> List[Dict[str, Any]]:
        ReadOnlySqlGuard.assert_readonly(sql)
        try:
            with self.connection() as conn:
                cur = conn.cursor()
                cur.execute(sql, params or ())
                cols = [d[0] for d in cur.description] if cur.description else []
                return [dict(zip(cols, row)) for row in cur.fetchall()]
        except GpConnectorError:
            raise
        except Exception as e:
            raise GpConnectorError(str(e)) from e

    def fetch_one(
        self, sql: str, params: Optional[Sequence[Any]] = None
    ) -> Optional[Dict[str, Any]]:
        rows = self.fetch_all(sql, params)
        return rows[0] if rows else None


def default_ssh_factory():
    """SSH tunnel + psycopg2; only configured pkey (no default id_rsa scan)."""
    import sys
    from pathlib import Path

    from sshtunnel import SSHTunnelForwarder
    import psycopg2

    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    import gp_metadata.gp_metadata as g

    cfg = dict(g.SSH_CONFIG)
    cfg["allow_agent"] = False
    cfg["host_pkey_directories"] = []
    tunnel = SSHTunnelForwarder(**cfg)
    tunnel.start()
    db = dict(g.DB_CONFIG)
    db["host"] = tunnel.local_bind_host
    db["port"] = tunnel.local_bind_port
    conn = psycopg2.connect(**db)

    class _ConnProxy:
        def __init__(self, c, t):
            self._c = c
            self._t = t

        def cursor(self):
            return self._c.cursor()

        def close(self):
            try:
                self._c.close()
            finally:
                self._t.stop()

    return _ConnProxy(conn, tunnel)
