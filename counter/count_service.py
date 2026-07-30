"""Fetch row counts from GP (read-only): batch fast via pg_class, exact per-table."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from pydantic import ValidationError

from models import CountMode, TableMetrics, TableNode

from .counts_store import CountsStore
from .gp_connector import GpConnector, GpConnectorError

log = logging.getLogger(__name__)

FAST_CHUNK_SIZE = 500
EXACT_PROGRESS_EVERY = 50
# Fast ladder:
# 1) parent pg_class.reltuples
# 2) max(parent, SUM(child.reltuples) via pg_inherits) when children exist
# 3) if still 0 → SELECT count(*)
FAST_ZERO_FALLBACK_PARTITIONS = True
FAST_ZERO_FALLBACK_EXACT = True

Pair = Tuple[str, str]


class CountServiceError(Exception):
    def __init__(self, message: str, failures: Optional[List[str]] = None):
        super().__init__(message)
        self.failures = failures or []


class RecountAborted(Exception):
    """User/UI stopped exact recount; partial upserts already in store."""

    def __init__(self, updated: List[TableMetrics], message: str = "recount stopped"):
        super().__init__(message)
        self.updated = updated


class CountService:
    def __init__(self, connector: GpConnector, store: CountsStore):
        self.connector = connector
        self.store = store

    def table_count_exact(self, schema: str, table: str) -> int:
        if not _safe_ident(schema) or not _safe_ident(table):
            raise CountServiceError(f"unsafe identifier: {schema}.{table}")
        sql = f'SELECT count(*) AS cnt FROM "{schema}"."{table}"'
        row = self.connector.fetch_one(sql)
        if row is None or "cnt" not in row:
            raise CountServiceError(f"bad count response for {schema}.{table}: {row}")
        try:
            return int(row["cnt"])
        except (TypeError, ValueError) as e:
            raise CountServiceError(
                f"count not int for {schema}.{table}: {row}"
            ) from e

    def fetch_reltuples_batch(
        self, pairs: Sequence[Pair]
    ) -> Dict[Pair, int]:
        """One SELECT joining pg_class to VALUES list of (schema, name)."""
        if not pairs:
            return {}
        for schema, name in pairs:
            if not _safe_ident(schema) or not _safe_ident(name):
                raise CountServiceError(f"unsafe identifier: {schema}.{name}")

        values_sql = ", ".join("(%s, %s)" for _ in pairs)
        params: List[str] = []
        for schema, name in pairs:
            params.extend([schema, name])

        sql = f"""
        SELECT n.nspname AS schema_name, c.relname AS name,
               CASE WHEN coalesce(c.reltuples, 0) < 0 THEN 0
                    ELSE coalesce(c.reltuples, 0)::bigint END AS cnt
          FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
          JOIN (VALUES {values_sql}) AS t(schema_name, name)
            ON n.nspname = t.schema_name AND c.relname = t.name
        """
        rows = self.connector.fetch_all(sql, params)
        out: Dict[Pair, int] = {}
        for r in rows:
            key = (str(r["schema_name"]), str(r["name"]))
            try:
                out[key] = int(r["cnt"])
            except (TypeError, ValueError) as e:
                raise CountServiceError(f"count not int for {key}: {r}") from e
        return out

    def fetch_partition_reltuples_sum_batch(
        self, pairs: Sequence[Pair]
    ) -> Dict[Pair, int]:
        """SUM(child.reltuples) via pg_inherits for parents in VALUES list.

        Tables without children are absent from the result (caller treats as 0).
        """
        if not pairs:
            return {}
        for schema, name in pairs:
            if not _safe_ident(schema) or not _safe_ident(name):
                raise CountServiceError(f"unsafe identifier: {schema}.{name}")

        values_sql = ", ".join("(%s, %s)" for _ in pairs)
        params: List[str] = []
        for schema, name in pairs:
            params.extend([schema, name])

        sql = f"""
        SELECT pn.nspname AS schema_name, parent.relname AS name,
               coalesce(sum(
                   CASE WHEN coalesce(child.reltuples, 0) < 0 THEN 0
                        ELSE coalesce(child.reltuples, 0)::bigint END
               ), 0)::bigint AS cnt
          FROM (VALUES {values_sql}) AS t(schema_name, name)
          JOIN pg_namespace pn ON pn.nspname = t.schema_name
          JOIN pg_class parent
            ON parent.relnamespace = pn.oid AND parent.relname = t.name
          JOIN pg_inherits i ON i.inhparent = parent.oid
          JOIN pg_class child ON child.oid = i.inhrelid
         GROUP BY pn.nspname, parent.relname
        """
        rows = self.connector.fetch_all(sql, params)
        out: Dict[Pair, int] = {}
        for r in rows:
            key = (str(r["schema_name"]), str(r["name"]))
            try:
                out[key] = int(r["cnt"])
            except (TypeError, ValueError) as e:
                raise CountServiceError(f"partition sum not int for {key}: {r}") from e
        return out

    def _select_targets(
        self,
        nodes: Sequence[TableNode],
        *,
        scope: str,
        existing: Dict[str, TableMetrics],
    ) -> List[TableNode]:
        if scope not in ("all", "empty"):
            raise CountServiceError(f"unknown scope {scope!r}")
        targets: List[TableNode] = []
        for n in nodes:
            if scope == "empty":
                prev = existing.get(n.fqn)
                if prev is not None and prev.row_count > 0:
                    continue
            targets.append(n)
        return targets

    def update_counts_fast_batch(
        self,
        nodes: Sequence[TableNode],
        *,
        scope: str = "all",
        existing: Optional[dict] = None,
        chunk_size: int = FAST_CHUNK_SIZE,
    ) -> List[TableMetrics]:
        existing = existing if existing is not None else self.store.load_all()
        targets = self._select_targets(nodes, scope=scope, existing=existing)
        t0 = time.monotonic()
        log.info(
            "update-counts start mode=fast scope=%s targets=%d",
            scope,
            len(targets),
        )
        if not targets:
            log.info("done updated=0 (no targets) elapsed=%.1fs", time.monotonic() - t0)
            return []

        updated: List[TableMetrics] = []
        failures: List[str] = []
        chunks = [
            targets[i : i + chunk_size] for i in range(0, len(targets), chunk_size)
        ]
        ts = datetime.now(timezone.utc)

        for i, chunk in enumerate(chunks, start=1):
            b0 = time.monotonic()
            pairs = [(n.schema_name, n.name) for n in chunk]
            try:
                found = self.fetch_reltuples_batch(pairs)
            except (GpConnectorError, CountServiceError) as e:
                failures.append(f"batch {i}/{len(chunks)}: {e}")
                log.error("fast batch %d/%d failed: %s", i, len(chunks), e)
                continue

            for n in chunk:
                key = (n.schema_name, n.name)
                if key not in found:
                    log.warning(
                        "reltuples missing for %s (not in pg_class join)", n.fqn
                    )

            # Partition sums for whole chunk (not only zeros): parent reltuples
            # may be stale/low while leaf stats are fresher (H1 / gp parents).
            part_sums: Dict[Pair, int] = {}
            if FAST_ZERO_FALLBACK_PARTITIONS and pairs:
                try:
                    part_sums = self.fetch_partition_reltuples_sum_batch(pairs)
                except (GpConnectorError, CountServiceError) as e:
                    log.warning(
                        "partition sum batch %d/%d failed (will try exact on zeros): %s",
                        i,
                        len(chunks),
                        e,
                    )
                    part_sums = {}

            batch_metrics: List[TableMetrics] = []
            part_fallback_n = 0
            exact_fallback_n = 0
            for n in chunk:
                key = (n.schema_name, n.name)
                parent_cnt = found.get(key, 0)
                cnt = parent_cnt
                mode = CountMode.FAST

                if key in part_sums and part_sums[key] > cnt:
                    cnt = part_sums[key]
                    mode = CountMode.FAST
                    part_fallback_n += 1

                if FAST_ZERO_FALLBACK_EXACT and cnt == 0:
                    try:
                        cnt = self.table_count_exact(n.schema_name, n.name)
                        mode = CountMode.EXACT
                        exact_fallback_n += 1
                    except (GpConnectorError, CountServiceError) as e:
                        log.warning(
                            "exact fallback failed for %s (keeping 0): %s", n.fqn, e
                        )

                batch_metrics.append(
                    TableMetrics(
                        fqn=n.fqn,
                        row_count=cnt,
                        count_mode=mode,
                        count_ts=ts,
                    )
                )
            try:
                self.store.upsert_many(batch_metrics)
                updated.extend(batch_metrics)
            except Exception as e:
                failures.append(f"batch {i}/{len(chunks)} store: {e}")
                continue

            log.info(
                "fast batch %d/%d size=%d part_fallback=%d exact_fallback=%d "
                "elapsed=%.1fs",
                i,
                len(chunks),
                len(chunk),
                part_fallback_n,
                exact_fallback_n,
                time.monotonic() - b0,
            )

        zero = sum(1 for m in updated if m.row_count == 0)
        nonzero = len(updated) - zero
        log.info(
            "done updated=%d zero=%d nonzero=%d elapsed=%.1fs",
            len(updated),
            zero,
            nonzero,
            time.monotonic() - t0,
        )
        if failures:
            raise CountServiceError(
                f"count update failed for {len(failures)} batch(es)",
                failures=failures,
            )
        return updated

    def update_counts_exact(
        self,
        nodes: Sequence[TableNode],
        *,
        scope: str = "all",
        existing: Optional[dict] = None,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> List[TableMetrics]:
        existing = existing if existing is not None else self.store.load_all()
        targets = self._select_targets(nodes, scope=scope, existing=existing)
        t0 = time.monotonic()
        log.info(
            "update-counts start mode=exact scope=%s targets=%d",
            scope,
            len(targets),
        )

        updated: List[TableMetrics] = []
        failures: List[str] = []
        total = len(targets)
        for idx, n in enumerate(targets, start=1):
            if should_stop is not None and should_stop():
                log.info(
                    "exact recount aborted at %d/%d elapsed=%.1fs",
                    idx - 1,
                    total,
                    time.monotonic() - t0,
                )
                raise RecountAborted(
                    updated,
                    f"stopped at {idx - 1}/{total} (updated={len(updated)})",
                )
            if on_progress is not None:
                on_progress(idx, total, n.fqn)
            try:
                cnt = self.table_count_exact(n.schema_name, n.name)
                metrics = TableMetrics(
                    fqn=n.fqn,
                    row_count=cnt,
                    count_mode=CountMode.EXACT,
                    count_ts=datetime.now(timezone.utc),
                )
                self.store.upsert(metrics)
                updated.append(metrics)
            except (GpConnectorError, CountServiceError, ValidationError) as e:
                failures.append(f"{n.fqn}: {e}")
            if idx % EXACT_PROGRESS_EVERY == 0 or idx == total:
                log.info(
                    "exact progress %d/%d elapsed=%.1fs",
                    idx,
                    total,
                    time.monotonic() - t0,
                )

        zero = sum(1 for m in updated if m.row_count == 0)
        log.info(
            "done updated=%d zero=%d nonzero=%d elapsed=%.1fs",
            len(updated),
            zero,
            len(updated) - zero,
            time.monotonic() - t0,
        )
        if failures:
            raise CountServiceError(
                f"count update failed for {len(failures)} table(s)",
                failures=failures,
            )
        return updated

    def update_counts(
        self,
        nodes: Sequence[TableNode],
        *,
        mode: CountMode = CountMode.FAST,
        scope: str = "all",
        existing: Optional[dict] = None,
        chunk_size: int = FAST_CHUNK_SIZE,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> List[TableMetrics]:
        if mode == CountMode.FAST:
            return self.update_counts_fast_batch(
                nodes, scope=scope, existing=existing, chunk_size=chunk_size
            )
        return self.update_counts_exact(
            nodes,
            scope=scope,
            existing=existing,
            on_progress=on_progress,
            should_stop=should_stop,
        )


def _safe_ident(name: str) -> bool:
    return bool(name) and all(c.isalnum() or c == "_" for c in name)
