"""Status colors: red empty, yellow anomaly, blue orphan dm; yellow > red > blue."""
from __future__ import annotations

from typing import Dict, Iterable, List, Set

from models import FlowEdge, Layer, NodeView, StatusColor, TableNode

YELLOW_LAYERS = frozenset({Layer.GP, Layer.INC, Layer.SNP, Layer.ODS})


def merge_node_views(
    nodes: Iterable[TableNode],
    metrics: Dict[str, TableMetrics],
) -> List[NodeView]:
    return [
        NodeView(node=n, metrics=metrics.get(n.fqn), status_color=StatusColor.UNKNOWN)
        for n in nodes
    ]


def apply_status_colors(
    views: List[NodeView],
    edges: Iterable[FlowEdge],
) -> List[NodeView]:
    by_fqn = {v.node.fqn: v for v in views}
    yellow: Set[str] = set()
    inbound: Set[str] = set()

    for e in edges:
        inbound.add(e.child_fqn)
        parent = by_fqn.get(e.parent_fqn)
        child = by_fqn.get(e.child_fqn)
        if parent is None or child is None:
            continue
        if parent.node.is_view or child.node.is_view:
            continue
        if child.node.layer not in YELLOW_LAYERS:
            continue
        pc = parent.metrics.row_count if parent.metrics else None
        cc = child.metrics.row_count if child.metrics else None
        if pc is not None and cc is not None and pc > 0 and cc == 0:
            yellow.add(child.node.fqn)

    out: List[NodeView] = []
    for v in views:
        if v.node.is_view:
            color = StatusColor.UNKNOWN
        elif v.metrics is None:
            color = StatusColor.UNKNOWN
        elif v.node.fqn in yellow:
            color = StatusColor.YELLOW  # yellow > red > blue
        elif v.metrics.row_count == 0:
            color = StatusColor.RED
        elif v.node.layer == Layer.DM and v.node.fqn not in inbound:
            color = StatusColor.BLUE
        else:
            color = StatusColor.NEUTRAL
        out.append(
            NodeView(node=v.node, metrics=v.metrics, status_color=color)
        )
    return out
