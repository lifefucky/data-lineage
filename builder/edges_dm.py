"""DM edges: project graph_node relationships onto physical dm.* tables."""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Set

from models import EdgeRule, FlowEdge, GraphNodeRelRow, GraphNodeRow, TableNode

from .prc_utils import mart_name_from_prc

__all__ = ["mart_name_from_prc", "resolve_dm_fqn", "build_dm_edges"]


def resolve_dm_fqn(
    node: GraphNodeRow,
    rel: Optional[GraphNodeRelRow],
    dm_names: Set[str],
) -> Optional[str]:
    candidates: List[str] = []
    if rel is not None:
        if rel.dst_mart_name:
            candidates.append(rel.dst_mart_name.strip().lower())
        if rel.dst_mart_code:
            candidates.append(rel.dst_mart_code.strip().lower())
    candidates.append(mart_name_from_prc(node.prc_code))
    for name in candidates:
        if name in dm_names:
            return f"dm.{name}"
    return None


def build_dm_edges(
    nodes: Iterable[TableNode],
    graph_nodes: Iterable[GraphNodeRow],
    relationships: Iterable[GraphNodeRelRow],
) -> List[FlowEdge]:
    dm_names = {n.name for n in nodes if n.schema_name == "dm"}
    by_id: Dict[int, GraphNodeRow] = {g.id_node: g for g in graph_nodes}
    edges: List[FlowEdge] = []
    seen: Set[tuple] = set()

    for rel in relationships:
        if rel.parent_id_node is None or rel.child_id_node is None:
            continue
        parent_gn = by_id.get(rel.parent_id_node)
        child_gn = by_id.get(rel.child_id_node)
        if parent_gn is None or child_gn is None:
            continue
        parent_fqn = resolve_dm_fqn(parent_gn, rel, dm_names)
        child_fqn = resolve_dm_fqn(child_gn, rel, dm_names)
        if not parent_fqn or not child_fqn or parent_fqn == child_fqn:
            continue
        key = (parent_fqn, child_fqn)
        if key in seen:
            continue
        seen.add(key)
        edges.append(
            FlowEdge(
                parent_fqn=parent_fqn,
                child_fqn=child_fqn,
                rule=EdgeRule.GRAPH_NODE,
                confidence=0.9,
            )
        )
    return edges
