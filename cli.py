"""CLI: build-schema, update-counts, run-ui."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from builder.meta_provider import FixtureMetaProvider, LiveSQLMetaProvider, MetaProviderError
from builder.schema_builder import build_schema_full
from builder.schema_store import SchemaStore
from counter.count_service import CountService, CountServiceError
from counter.counts_store import CountsStore
from counter.gp_connector import GpConnector, GpConnectorError, default_ssh_factory
from counter.logging_setup import configure_live_logging
from models import CountMode

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
DEFAULT_EXPORT = ROOT.parent / "gp_metadata" / "gp_metadata_export"
DEFAULT_SCHEMA_DB = ROOT / "data" / "schema_cache.db"
DEFAULT_COUNTS_DB = ROOT / "data" / "counts_cache.db"


def cmd_build_schema(args: argparse.Namespace) -> int:
    export_root = Path(args.export_root)
    if not export_root.is_dir():
        print(f"export path not found: {export_root}", file=sys.stderr)
        return 2

    meta = None
    if args.meta_fixture:
        try:
            payload = json.loads(Path(args.meta_fixture).read_text(encoding="utf-8"))
            meta = FixtureMetaProvider(
                metadata_tables=payload.get("metadata_tables", []),
                view_mappings=payload.get("view_mappings", []),
                graph_nodes=payload.get("graph_nodes", []),
                graph_rels=payload.get("graph_rels", []),
            )
        except (OSError, json.JSONDecodeError, MetaProviderError) as e:
            print(f"meta fixture error: {e}", file=sys.stderr)
            return 1
    elif args.live_meta:
        configure_live_logging()
        connector = GpConnector(default_ssh_factory)
        try:
            with connector.session():
                meta = LiveSQLMetaProvider(connector)
                nodes, edges, timing = build_schema_full(
                    export_root,
                    Path(args.schema_db),
                    meta_provider=meta,
                    include_dds_dm=not args.stg_ods_only,
                    with_sql_parse=not args.no_sql_parse,
                )
        except (MetaProviderError, GpConnectorError, OSError, TypeError) as e:
            print(f"build-schema failed: {e}", file=sys.stderr)
            return 1
        skipped = getattr(meta, "skipped_invalid", 0)
        extra = f", skipped_invalid_meta={skipped}" if skipped else ""
        print(_format_schema_built(nodes, edges, args.schema_db, timing, extra))
        return 0

    try:
        nodes, edges, timing = build_schema_full(
            export_root,
            Path(args.schema_db),
            meta_provider=meta,
            include_dds_dm=not args.stg_ods_only,
            with_sql_parse=not args.no_sql_parse,
        )
    except (MetaProviderError, GpConnectorError, OSError, TypeError) as e:
        print(f"build-schema failed: {e}", file=sys.stderr)
        return 1

    skipped = getattr(meta, "skipped_invalid", 0) if meta is not None else 0
    extra = f", skipped_invalid_meta={skipped}" if skipped else ""
    print(_format_schema_built(nodes, edges, args.schema_db, timing, extra))
    return 0


def _format_schema_built(nodes, edges, schema_db, timing, extra: str = "") -> str:
    views_part = ""
    if "views_n" in timing:
        views_part = (
            f" views={int(timing['views_n'])}"
            f" indexed={int(timing['views_indexed'])}"
            f" empty={int(timing['views_empty'])}"
        )
    sql_part = ""
    if "sql_parse_edges" in timing:
        sql_part = (
            f" sql_parse_edges={int(timing['sql_parse_edges'])}"
            f" coverage_pct={timing['coverage_pct']}"
            f" dm_no_inbound={int(timing['dm_no_inbound'])}"
        )
    sql_parse_s = timing.get("sql_parse", 0.0)
    views_s = timing.get("views", 0.0)
    return (
        f"schema built: {len(nodes)} nodes, {len(edges)} edges -> {schema_db}"
        f"{extra}{views_part}{sql_part} "
        f"(scan={timing['scan']:.2f}s edges={timing['edges']:.2f}s "
        f"views={views_s:.2f}s sql_parse={sql_parse_s:.2f}s "
        f"store={timing['store']:.2f}s total={timing['total']:.2f}s)"
    )


def cmd_update_counts(args: argparse.Namespace) -> int:
    schema_store = SchemaStore(Path(args.schema_db))
    nodes, _edges = schema_store.load()
    if not nodes:
        print("no nodes in schema DB; run build-schema first", file=sys.stderr)
        return 2

    if args.mock_counts:
        try:
            raw = json.loads(Path(args.mock_counts).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"mock counts error: {e}", file=sys.stderr)
            return 1
        store = CountsStore(Path(args.counts_db))
        from datetime import datetime, timezone
        from models import TableMetrics

        for fqn, cnt in raw.items():
            store.upsert(
                TableMetrics(
                    fqn=fqn,
                    row_count=int(cnt),
                    count_mode=CountMode(args.mode),
                    count_ts=datetime.now(timezone.utc),
                )
            )
        print(f"mock counts written: {len(raw)} -> {args.counts_db}")
        return 0

    configure_live_logging()
    log.info(
        "update-counts start mode=%s scope=%s nodes=%d schema_db=%s",
        args.mode,
        args.scope,
        len(nodes),
        args.schema_db,
    )
    counts_db = Path(args.counts_db)
    connector = GpConnector(default_ssh_factory)
    try:
        with connector.session():
            service = CountService(connector, CountsStore(counts_db))
            updated = service.update_counts(
                nodes,
                mode=CountMode(args.mode),
                scope=args.scope,
            )
    except (CountServiceError, GpConnectorError) as e:
        print(f"update-counts failed: {e}", file=sys.stderr)
        if isinstance(e, CountServiceError) and e.failures:
            for f in e.failures[:20]:
                print(f"  - {f}", file=sys.stderr)
        return 1

    zero = sum(1 for m in updated if m.row_count == 0)
    print(
        f"updated {len(updated)} counts (zero={zero}, nonzero={len(updated) - zero}) "
        f"-> {counts_db}"
    )
    return 0


def cmd_run_ui(args: argparse.Namespace) -> int:
    import subprocess

    app = ROOT / "visualizer" / "app.py"
    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(app),
        "--",
        "--schema-db",
        str(args.schema_db),
        "--counts-db",
        str(args.counts_db),
    ]
    return subprocess.call(cmd)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="entities_lineage")
    sub = p.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build-schema", help="Rebuild topology cache")
    b.add_argument("--full", action="store_true", default=True)
    b.add_argument("--export-root", default=str(DEFAULT_EXPORT))
    b.add_argument("--schema-db", default=str(DEFAULT_SCHEMA_DB))
    b.add_argument("--meta-fixture", default=None, help="JSON meta fixture")
    b.add_argument("--live-meta", action="store_true", help="SELECT meta from GP")
    b.add_argument(
        "--stg-ods-only",
        action="store_true",
        help="Skip DDS/DM edge builders (also skips SQL_PARSE)",
    )
    b.add_argument(
        "--no-sql-parse",
        action="store_true",
        help="Skip dm/functions SQL_PARSE edges (dds|ods → dm)",
    )
    b.set_defaults(func=cmd_build_schema)

    c = sub.add_parser("update-counts", help="Refresh row counts cache")
    c.add_argument("--schema-db", default=str(DEFAULT_SCHEMA_DB))
    c.add_argument("--counts-db", default=str(DEFAULT_COUNTS_DB))
    c.add_argument("--mode", choices=["fast", "exact"], default="fast")
    c.add_argument("--scope", choices=["all", "empty"], default="all")
    c.add_argument("--mock-counts", default=None, help="JSON fqn->count for offline")
    c.set_defaults(func=cmd_update_counts)

    u = sub.add_parser("run-ui", help="Launch Streamlit UI")
    u.add_argument("--schema-db", default=str(DEFAULT_SCHEMA_DB))
    u.add_argument("--counts-db", default=str(DEFAULT_COUNTS_DB))
    u.set_defaults(func=cmd_run_ui)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        code = e.code
        return int(code) if isinstance(code, int) else 1
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
