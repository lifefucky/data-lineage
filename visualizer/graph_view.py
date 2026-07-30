"""Pure helpers for Streamlit/Pyvis (unit-tested without browser)."""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Sequence, Set, Tuple

from builder.schema_store import SchemaStore
from counter.counts_store import CountsStore
from graph.lineage_graph import BRANCH_MAX_NODES, LineageGraph
from graph.status import apply_status_colors, merge_node_views
from models import NodeView, TableNode

# Physical DWH schema bands (UI filter «Слой данных»); not pipeline Layer.
SCHEMA_BANDS: Tuple[str, ...] = ("stg_ods", "ods", "dds", "dm")
_SCHEMA_BANDS_SET = frozenset(SCHEMA_BANDS)


def db_fingerprint(path) -> Tuple[float, int]:
    """(mtime, size) for cache keys; (0.0, 0) if missing."""
    p = Path(path)
    if not p.exists():
        return (0.0, 0)
    st = p.stat()
    return (st.st_mtime, st.st_size)


def load_views(schema_db, counts_db) -> tuple[list, list, List[NodeView], LineageGraph]:
    nodes, edges = SchemaStore(schema_db).load()
    metrics = CountsStore(counts_db).load_all()
    views = apply_status_colors(merge_node_views(nodes, metrics), edges)
    lg = LineageGraph.from_topology(nodes, edges)
    return nodes, edges, views, lg


def list_table_fqns_from_nodes(
    nodes: Sequence[TableNode],
    src_code: Optional[str] = None,
) -> List[str]:
    """FQNs for Table selectbox; soft-filter by src_code when set."""
    if not nodes:
        return []
    if not src_code:
        return sorted(n.fqn for n in nodes)
    return sorted(
        n.fqn
        for n in nodes
        if n.src_code is None or n.src_code == src_code
    )


def list_table_fqns(
    schema_db,
    src_code: Optional[str] = None,
) -> List[str]:
    """FQNs for Table selectbox; soft-filter by src_code when set."""
    nodes, _ = SchemaStore(schema_db).load()
    return list_table_fqns_from_nodes(nodes, src_code=src_code)


def _soft_src_keep(
    lg: LineageGraph,
    nodes: Set[str],
    table_fqn: str,
    src_code: Optional[str],
) -> Set[str]:
    if not src_code:
        return nodes
    return {
        n
        for n in nodes
        if n == table_fqn or lg.g.nodes[n].get("src_code") in (None, src_code)
    }


def _schema_of(fqn: str) -> str:
    return fqn.split(".", 1)[0]


def _apply_schema_bands(
    sub: LineageGraph,
    bands: Optional[Sequence[str]],
) -> Tuple[LineageGraph, int]:
    """Keep nodes whose FQN schema is in bands. Returns (subgraph, removed_count).

    ``None`` or the full SCHEMA_BANDS set → no-op.
    Empty ``bands`` → empty graph.
    Edges survive only if both endpoints are kept (NX subgraph).
    """
    n_before = sub.g.number_of_nodes()
    if bands is None:
        return sub, 0
    band_set = frozenset(bands)
    if band_set == _SCHEMA_BANDS_SET:
        return sub, 0
    if not band_set:
        empty = LineageGraph()
        return empty, n_before
    keep = {n for n in sub.g.nodes if _schema_of(n) in band_set}
    out = LineageGraph()
    out.g = sub.g.subgraph(keep).copy() if keep else LineageGraph().g
    return out, n_before - out.g.number_of_nodes()


def prepare_pyvis_data(
    schema_db,
    counts_db,
    *,
    src_code: Optional[str] = None,
    table_fqn: Optional[str] = None,
    branch_direction: str = "both",
    hops: int = 2,
    branch_max_nodes: int = BRANCH_MAX_NODES,
    show_counts_on_label: bool = False,
    group_by_layer: bool = True,
    schema_bands: Optional[Sequence[str]] = None,
    topology: Optional[tuple] = None,
) -> dict:
    if topology is None:
        nodes, edges, views, lg = load_views(schema_db, counts_db)
    else:
        nodes, edges, views, lg = topology
    if not nodes:
        return {
            "nodes": [],
            "edges": [],
            "subgraph_fqns": [],
            "highlight": [],
            "caption": None,
            "warning": None,
            "error": "empty schema DB",
        }

    highlight: Sequence[str] = []
    caption: Optional[str] = None
    warning: Optional[str] = None
    sub = lg

    if table_fqn:
        branch = lg.branch_subgraph(table_fqn, direction=branch_direction)
        keep = _soft_src_keep(
            lg, set(branch.g.nodes), table_fqn, src_code
        )
        filtered = LineageGraph()
        filtered.g = lg.g.subgraph(keep).copy() if keep else LineageGraph().g

        if filtered.g.number_of_nodes() > branch_max_nodes:
            warning = (
                f"branch too large (>{branch_max_nodes} nodes), "
                f"showing ego hops={hops}"
            )
            sub = lg.ego_subgraph(table_fqn, hops=hops)
            if src_code:
                keep_ego = _soft_src_keep(
                    lg, set(sub.g.nodes), table_fqn, src_code
                )
                ego = LineageGraph()
                ego.g = lg.g.subgraph(keep_ego).copy() if keep_ego else LineageGraph().g
                sub = ego
        else:
            sub = filtered

        n_nodes = sub.g.number_of_nodes()
        n_edges = sub.g.number_of_edges()
        caption = f"Branch of {table_fqn} ({n_nodes} nodes, {n_edges} edges)"
        highlight = [table_fqn] if table_fqn in sub.g else []
    elif src_code:
        sub = lg.subgraph_by_src_code(src_code)

    # Order: src/branch first, then schema-band (Stage 2b).
    sub, removed = _apply_schema_bands(sub, schema_bands)
    if schema_bands is not None and len(schema_bands) == 0:
        caption = "No schema bands selected"
    elif removed > 0:
        band_msg = f"filtered {removed} nodes by schema band"
        warning = f"{warning}; {band_msg}" if warning else band_msg
    if highlight:
        highlight = [h for h in highlight if h in sub.g]

    payload = sub.pyvis_payload(
        views,
        highlight=highlight,
        show_counts_on_label=show_counts_on_label,
        group_by_layer=group_by_layer,
    )
    payload["highlight"] = list(highlight)
    payload["subgraph_fqns"] = sorted(n["id"] for n in payload["nodes"])
    payload["caption"] = caption
    payload["warning"] = warning
    payload["error"] = None
    return payload


def status_banner_message(error: Optional[str]) -> Optional[str]:
    if error:
        return f"Error: {error}"
    return None
