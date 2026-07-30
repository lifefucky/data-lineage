"""NetworkX digraph: parent → child (data flow). Upstream = reverse."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Set, Union

import networkx as nx

from models import CountMode, FlowEdge, NodeView, TableNode

# Soft cap for UI: larger branches fall back to ego hops.
BRANCH_MAX_NODES = 100

_BRANCH_DIRECTIONS = frozenset({"both", "upstream", "downstream"})

# Fixed-x band layout (vis.js): level * LAYER_X_STEP → x coordinate.
# Wide enough that labels in adjacent layers do not overlap.
LAYER_X_STEP = 720
LAYER_LEVEL: Dict[str, int] = {
    "gp": 0,
    "inc": 1,
    "snp": 2,
    "ods": 3,
    "dds": 4,
    "dm_view": 5,
    "dm": 6,
}
LAYER_TINT: Dict[str, str] = {
    "gp": "#d6eaf8",
    "inc": "#d5f5e3",
    "snp": "#fcf3cf",
    "ods": "#fadbd8",
    "dds": "#e8daef",
    "dm_view": "#d0ece7",
    "dm": "#fdebd0",
    "unknown": "#ecf0f1",
}
STATUS_COLOR: Dict[str, str] = {
    "red": "#e74c3c",
    "yellow": "#f1c40f",
    "blue": "#3498db",
    "neutral": "#95a5a6",
    "unknown": "#bdc3c7",
}
_STATUS_OVERRIDE = frozenset({"red", "yellow", "blue"})


def layer_level(layer: str) -> int:
    """Pipeline band index; unknown layers sit left of gp (−1)."""
    return LAYER_LEVEL.get(layer, -1)


def layer_x(layer: str) -> int:
    return layer_level(layer) * LAYER_X_STEP


def node_vis_color(
    layer: str,
    status: str,
    *,
    group_by_layer: bool,
) -> Union[str, Dict[str, Any]]:
    """Status hex when ungrouped; tint + status border when grouped (red/yellow/blue win fill)."""
    status_hex = STATUS_COLOR.get(status, STATUS_COLOR["unknown"])
    if not group_by_layer:
        return status_hex
    if status in _STATUS_OVERRIDE:
        return {
            "background": status_hex,
            "border": status_hex,
            "highlight": {"background": status_hex, "border": status_hex},
        }
    tint = LAYER_TINT.get(layer, LAYER_TINT["unknown"])
    return {
        "background": tint,
        "border": status_hex,
        "highlight": {"background": tint, "border": status_hex},
    }


def format_node_title(
    view: Optional[NodeView],
    *,
    fqn: str,
    layer: str,
) -> str:
    """Plain-text vis.js tooltip: fqn, layer, row_count, count_mode, count_ts, status."""
    status = view.status_color.value if view else "unknown"
    stale_note = ""
    if view and view.metrics:
        row_count = str(view.metrics.row_count)
        count_mode = view.metrics.count_mode.value
        count_ts = view.metrics.count_ts.isoformat()
        if (
            view.metrics.row_count == 0
            and view.metrics.count_mode == CountMode.FAST
        ):
            stale_note = "note: reltuples may be stale\n"
    else:
        row_count = "n/a"
        count_mode = "n/a"
        count_ts = "n/a"
    return (
        f"fqn: {fqn}\n"
        f"layer: {layer}\n"
        f"row_count: {row_count}\n"
        f"count_mode: {count_mode}\n"
        f"{stale_note}"
        f"count_ts: {count_ts}\n"
        f"status: {status}"
    )


class LineageGraph:
    def __init__(self):
        self.g = nx.DiGraph()

    @classmethod
    def from_topology(
        cls,
        nodes: Sequence[TableNode],
        edges: Sequence[FlowEdge],
    ) -> "LineageGraph":
        lg = cls()
        for n in nodes:
            lg.g.add_node(n.fqn, layer=n.layer.value, src_code=n.src_code)
        for e in edges:
            if e.parent_fqn in lg.g and e.child_fqn in lg.g:
                lg.g.add_edge(
                    e.parent_fqn,
                    e.child_fqn,
                    rule=e.rule.value,
                    confidence=e.confidence,
                )
        return lg

    def shortest_upstream_path(self, fqn: str) -> Optional[List[str]]:
        """Path from a root-ish ancestor to fqn following reverse edges (upstream)."""
        if fqn not in self.g:
            return None
        # Walk predecessors; pick shortest path from any source (in-degree 0) in ancestors
        ancestors = nx.ancestors(self.g, fqn)
        if not ancestors:
            return [fqn]
        sources = [a for a in ancestors if self.g.in_degree(a) == 0] or list(ancestors)
        best: Optional[List[str]] = None
        for s in sources:
            try:
                path = nx.shortest_path(self.g, s, fqn)
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                continue
            if best is None or len(path) < len(best):
                best = path
        return best if best is not None else [fqn]

    def shortest_upstream_break(
        self,
        fqn: str,
        counts: Dict[str, int],
    ) -> Optional[List[str]]:
        """
        Upstream path (root→fqn) highlighting break: walk from fqn upstream
        until first non-empty node; return path from that node (or root) to fqn.
        """
        if fqn not in self.g:
            return None
        path = self.shortest_upstream_path(fqn)
        if not path:
            return None
        # path is root → ... → fqn; find last non-empty from start, or first empty break
        break_idx = 0
        for i, node in enumerate(path):
            cnt = counts.get(node)
            if cnt is not None and cnt > 0:
                break_idx = i
        # return from last non-empty (or start) to target
        return path[break_idx:]

    def subgraph_by_src_code(self, src_code: str) -> "LineageGraph":
        keep = {
            n
            for n, data in self.g.nodes(data=True)
            if data.get("src_code") == src_code
        }
        # also keep neighbors one hop for context
        extra: Set[str] = set()
        for n in list(keep):
            extra.update(self.g.predecessors(n))
            extra.update(self.g.successors(n))
        keep |= extra
        lg = LineageGraph()
        lg.g = self.g.subgraph(keep).copy()
        return lg

    def ego_subgraph(self, fqn: str, hops: int = 2) -> "LineageGraph":
        if fqn not in self.g:
            lg = LineageGraph()
            return lg
        nodes = {fqn}
        frontier = {fqn}
        for _ in range(hops):
            nxt: Set[str] = set()
            for n in frontier:
                nxt.update(self.g.predecessors(n))
                nxt.update(self.g.successors(n))
            nodes |= nxt
            frontier = nxt
        lg = LineageGraph()
        lg.g = self.g.subgraph(nodes).copy()
        return lg

    def branch_subgraph(
        self,
        fqn: str,
        *,
        direction: str = "both",
    ) -> "LineageGraph":
        """Full pipeline branch: ancestors ∪ {fqn} ∪ descendants (no hop limit)."""
        if direction not in _BRANCH_DIRECTIONS:
            raise ValueError(
                f"direction must be one of {sorted(_BRANCH_DIRECTIONS)}, got {direction!r}"
            )
        if fqn not in self.g:
            return LineageGraph()
        keep: Set[str] = {fqn}
        if direction in ("both", "upstream"):
            keep |= nx.ancestors(self.g, fqn)
        if direction in ("both", "downstream"):
            keep |= nx.descendants(self.g, fqn)
        lg = LineageGraph()
        lg.g = self.g.subgraph(keep).copy()
        return lg

    def pyvis_payload(
        self,
        views: Sequence[NodeView],
        highlight: Optional[Sequence[str]] = None,
        *,
        show_counts_on_label: bool = False,
        group_by_layer: bool = True,
    ) -> dict:
        by_fqn = {v.node.fqn: v for v in views}
        hl = set(highlight or [])
        nodes = []
        for n in self.g.nodes:
            v = by_fqn.get(n)
            status = v.status_color.value if v else "unknown"
            layer = (
                v.node.layer.value
                if v
                else str(self.g.nodes[n].get("layer", "unknown"))
            )
            name = n.split(".")[-1]
            if v and v.metrics:
                row_count: Optional[int] = v.metrics.row_count
                count_mode: Optional[str] = v.metrics.count_mode.value
                count_ts: Optional[str] = v.metrics.count_ts.isoformat()
                count_label = str(row_count)
            else:
                row_count = None
                count_mode = None
                count_ts = None
                count_label = "n/a"
            label = f"{name}\n{count_label}" if show_counts_on_label else name
            level = layer_level(layer)
            node: Dict[str, Any] = {
                "id": n,
                "label": label,
                "title": format_node_title(v, fqn=n, layer=layer),
                "color": node_vis_color(
                    layer, status, group_by_layer=group_by_layer
                ),
                "borderWidth": 3 if n in hl else 1,
                "layer": layer,
                "group": layer,
                "level": level,
                "row_count": row_count,
                "count_mode": count_mode,
                "count_ts": count_ts,
                "status": status,
            }
            if group_by_layer:
                node["x"] = level * LAYER_X_STEP
                node["fixed"] = {"x": True, "y": False}
            nodes.append(node)
        edges = [
            {"from": u, "to": v, "arrows": "to"}
            for u, v in self.g.edges
        ]
        return {"nodes": nodes, "edges": edges}
