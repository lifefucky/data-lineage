"""Functions-first SQL_PARSE: dm/functions → expand views → dds|ods|dm → dm.

Multi-mart: union of primary target from function name (if known dm table)
and all write-targets (UPDATE/INTO/DELETE FROM dm.*) that exist as table nodes.
View endpoints are never nodes; expand via ViewSourceIndex (one hop).
Prefer GRAPH_NODE when the same (parent, child) already exists.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from models import EdgeRule, FlowEdge, TableNode

from .edges_dm_views import ViewSourceIndex
from .prc_utils import mart_name_from_prc
from .sql_refs import extract_read_refs, extract_write_refs

log = logging.getLogger(__name__)

_WHITELIST_PREFIXES = ("func_load_", "func_build_", "func_reload_", "func_calc_")
_SQL_PARSE_CONFIDENCE = 0.8

FunctionEdgeStats = Dict[str, object]


def _is_whitelisted(stem: str) -> bool:
    return any(stem.startswith(p) for p in _WHITELIST_PREFIXES)


def _looks_like_view_fqn(fqn: str) -> bool:
    name = fqn.split(".", 1)[-1]
    return name.endswith("_v") or name.endswith("_pafo_v")


def _expand_read_refs(
    read_refs: Iterable[str],
    view_index: ViewSourceIndex,
    *,
    unresolved: List[str],
    view_to_view: List[str],
) -> List[str]:
    """One-hop expand: view → underlying sources; no deep recurse."""
    expanded: List[str] = []
    seen: Set[str] = set()
    for ref in read_refs:
        if ref in view_index:
            sources = view_index[ref]
            if not sources:
                unresolved.append(ref)
                continue
            for src in sources:
                if src in view_index:
                    view_to_view.append(f"{ref}->{src}")
                    log.warning("view→view (no deep recurse): %s → %s", ref, src)
                    continue
                if src not in seen:
                    seen.add(src)
                    expanded.append(src)
        else:
            if _looks_like_view_fqn(ref):
                unresolved.append(ref)
                log.warning("unresolved view ref (not in index): %s", ref)
                continue
            if ref not in seen:
                seen.add(ref)
                expanded.append(ref)
    return expanded


def build_dm_function_edges(
    export_root: Path,
    nodes: Iterable[TableNode],
    view_index: ViewSourceIndex,
    *,
    graph_node_pairs: Optional[Set[Tuple[str, str]]] = None,
) -> Tuple[List[FlowEdge], FunctionEdgeStats]:
    """Scan dm/functions whitelist → SQL_PARSE edges (expanded sources → targets).

    Endpoints must exist in ``nodes`` (table nodes only). Returns edges + stats;
    never raises on parse gaps (warnings + stats only).
    """
    by_fqn = {n.fqn: n for n in nodes}
    dm_fqns = {fqn for fqn in by_fqn if fqn.startswith("dm.")}
    prefer = graph_node_pairs or set()

    funcs_dir = Path(export_root) / "dm" / "functions"
    edges: List[FlowEdge] = []
    seen_pairs: Set[Tuple[str, str]] = set()
    unresolved_view_refs: List[str] = []
    view_to_view_warnings: List[str] = []
    functions_scanned = 0
    functions_skipped = 0
    functions_no_target = 0
    dm_with_inbound: Set[str] = set()

    if not funcs_dir.is_dir():
        stats = _make_stats(
            functions_scanned=0,
            functions_skipped=0,
            functions_no_target=0,
            sql_parse_edges=0,
            dm_with_sql_parse=0,
            dm_tables=len(dm_fqns),
            unresolved_view_refs=0,
            view_to_view_warnings=0,
        )
        log.info(
            "functions_scanned=%s sql_parse_edges=%s coverage_pct=%s",
            0,
            0,
            stats["coverage_pct"],
        )
        return edges, stats

    for path in sorted(funcs_dir.glob("*.sql")):
        stem = path.stem.lower()
        if not _is_whitelisted(stem):
            functions_skipped += 1
            continue
        functions_scanned += 1
        try:
            body = path.read_text(encoding="utf-8")
        except OSError as e:
            log.warning("skip function %s: %s", path, e)
            continue

        # Targets: primary from name + write refs to known dm tables.
        targets: List[str] = []
        primary = f"dm.{mart_name_from_prc(stem)}"
        if primary in dm_fqns:
            targets.append(primary)
        for wref in extract_write_refs(body):
            if wref in dm_fqns and wref not in targets:
                targets.append(wref)
        if not targets:
            functions_no_target += 1
            log.warning("function without known dm target: %s", path.name)
            continue

        target_set = set(targets)
        expanded = _expand_read_refs(
            extract_read_refs(body),
            view_index,
            unresolved=unresolved_view_refs,
            view_to_view=view_to_view_warnings,
        )

        for src in expanded:
            if src in target_set:
                continue  # self-read of a write target
            if src not in by_fqn:
                continue
            for tgt in targets:
                if src == tgt:
                    continue
                key = (src, tgt)
                if key in prefer:
                    continue  # prefer GRAPH_NODE
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)
                edges.append(
                    FlowEdge(
                        parent_fqn=src,
                        child_fqn=tgt,
                        rule=EdgeRule.SQL_PARSE,
                        confidence=_SQL_PARSE_CONFIDENCE,
                    )
                )
                dm_with_inbound.add(tgt)

    stats = _make_stats(
        functions_scanned=functions_scanned,
        functions_skipped=functions_skipped,
        functions_no_target=functions_no_target,
        sql_parse_edges=len(edges),
        dm_with_sql_parse=len(dm_with_inbound),
        dm_tables=len(dm_fqns),
        unresolved_view_refs=len(unresolved_view_refs),
        view_to_view_warnings=len(view_to_view_warnings),
    )
    log.info(
        "functions_scanned=%s skipped=%s sql_parse_edges=%s "
        "dm_with_sql_parse=%s/%s coverage_pct=%s unresolved_views=%s",
        functions_scanned,
        functions_skipped,
        len(edges),
        len(dm_with_inbound),
        len(dm_fqns),
        stats["coverage_pct"],
        len(unresolved_view_refs),
    )
    return edges, stats


def _make_stats(
    *,
    functions_scanned: int,
    functions_skipped: int,
    functions_no_target: int,
    sql_parse_edges: int,
    dm_with_sql_parse: int,
    dm_tables: int,
    unresolved_view_refs: int,
    view_to_view_warnings: int,
) -> FunctionEdgeStats:
    coverage = (
        round(100.0 * dm_with_sql_parse / dm_tables, 1) if dm_tables else 0.0
    )
    return {
        "functions_scanned": functions_scanned,
        "functions_skipped": functions_skipped,
        "functions_no_target": functions_no_target,
        "sql_parse_edges": sql_parse_edges,
        "dm_with_sql_parse": dm_with_sql_parse,
        "dm_tables": dm_tables,
        "coverage_pct": coverage,
        "unresolved_view_refs": unresolved_view_refs,
        "view_to_view_warnings": view_to_view_warnings,
    }
