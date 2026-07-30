"""Orchestrate full schema rebuild: nodes + edges → SchemaStore."""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from models import EdgeRule, FlowEdge, Layer, TableNode

from .edges_dds import build_dds_edges
from .edges_dm import build_dm_edges
from .edges_dm_functions import build_dm_function_edges
from .edges_dm_views import build_view_source_index
from .edges_ods import build_ods_edges
from .edges_stg import build_stg_naming_edges
from .meta_provider import MetaProvider
from .node_scanner import NodeScanner
from .schema_store import SchemaStore

log = logging.getLogger(__name__)

Timing = Dict[str, float]


def build_schema_full(
    export_root: Path,
    schema_db: Path,
    meta_provider: Optional[MetaProvider] = None,
    *,
    include_dds_dm: bool = True,
    with_sql_parse: bool = True,
) -> Tuple[List[TableNode], List[FlowEdge], Timing]:
    t0 = time.perf_counter()
    nodes = NodeScanner(export_root).scan()
    t_scan = time.perf_counter()

    edges: List[FlowEdge] = []
    edges.extend(build_stg_naming_edges(nodes))

    if meta_provider is not None:
        meta_rows = meta_provider.fetch_metadata_tables()
        edges.extend(build_ods_edges(nodes, meta_rows))
        if include_dds_dm:
            edges.extend(build_dds_edges(nodes, meta_provider.fetch_view_mapping_tables()))
            edges.extend(
                build_dm_edges(
                    nodes,
                    meta_provider.fetch_graph_nodes(),
                    meta_provider.fetch_graph_node_relationships(),
                )
            )
    t_edges = time.perf_counter()

    # Stage 3: ViewSourceIndex in-memory (feeds Stage 4 expand).
    view_index, view_stats = build_view_source_index(export_root)
    t_views = time.perf_counter()

    sql_parse_edges_n = 0
    coverage_pct = 0.0
    if with_sql_parse and include_dds_dm:
        graph_node_pairs: Set[Tuple[str, str]] = {
            (e.parent_fqn, e.child_fqn)
            for e in edges
            if e.rule == EdgeRule.GRAPH_NODE
        }
        parse_edges, parse_stats = build_dm_function_edges(
            export_root,
            nodes,
            view_index,
            graph_node_pairs=graph_node_pairs,
        )
        edges.extend(parse_edges)
        sql_parse_edges_n = int(parse_stats["sql_parse_edges"])
        coverage_pct = float(parse_stats["coverage_pct"])
    t_sql_parse = time.perf_counter()

    store = SchemaStore(schema_db)
    store.replace_all(nodes, edges)
    t_store = time.perf_counter()

    dm_no_inbound = _count_dm_no_inbound(nodes, edges)

    timing: Timing = {
        "scan": t_scan - t0,
        "edges": t_edges - t_scan,
        "views": t_views - t_edges,
        "sql_parse": t_sql_parse - t_views,
        "store": t_store - t_sql_parse,
        "total": t_store - t0,
        "views_n": float(view_stats["views"]),
        "views_indexed": float(view_stats["indexed"]),
        "views_empty": float(view_stats["empty"]),
        "sql_parse_edges": float(sql_parse_edges_n),
        "coverage_pct": coverage_pct,
        "dm_no_inbound": float(dm_no_inbound),
    }
    return nodes, edges, timing


def _count_dm_no_inbound(nodes: List[TableNode], edges: List[FlowEdge]) -> int:
    inbound = {e.child_fqn for e in edges}
    return sum(
        1 for n in nodes if n.layer == Layer.DM and n.fqn not in inbound
    )
