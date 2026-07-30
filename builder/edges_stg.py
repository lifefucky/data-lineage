"""STG naming edges: gp → inc → snp."""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Set

from models import EdgeRule, FlowEdge, Layer, TableNode

GP_RE = re.compile(r"^gp_(\d{3})_(.+)$", re.IGNORECASE)
INC_RE = re.compile(r"^t_(\d{3})_inc_(.+)$", re.IGNORECASE)
SNP_RE = re.compile(r"^t_(\d{3})_snp_(.+)$", re.IGNORECASE)


def _index_stg(nodes: Iterable[TableNode]) -> Dict[str, TableNode]:
    return {
        n.name: n
        for n in nodes
        if n.schema_name == "stg_ods" and n.layer in (Layer.GP, Layer.INC, Layer.SNP)
    }


def build_stg_naming_edges(nodes: Iterable[TableNode]) -> List[FlowEdge]:
    by_name = _index_stg(nodes)
    edges: List[FlowEdge] = []
    seen: Set[tuple] = set()

    for name, node in by_name.items():
        if node.layer != Layer.GP:
            continue
        m = GP_RE.match(name)
        if not m:
            continue
        src, cut = m.group(1), m.group(2)
        inc_name = f"t_{src}_inc_{cut}"
        snp_name = f"t_{src}_snp_{cut}"
        inc = by_name.get(inc_name)
        snp = by_name.get(snp_name)
        if inc is not None:
            key = (node.fqn, inc.fqn)
            if key not in seen:
                seen.add(key)
                edges.append(
                    FlowEdge(
                        parent_fqn=node.fqn,
                        child_fqn=inc.fqn,
                        rule=EdgeRule.NAMING,
                        confidence=1.0,
                        src_code=src,
                    )
                )
        if inc is not None and snp is not None:
            key = (inc.fqn, snp.fqn)
            if key not in seen:
                seen.add(key)
                edges.append(
                    FlowEdge(
                        parent_fqn=inc.fqn,
                        child_fqn=snp.fqn,
                        rule=EdgeRule.NAMING,
                        confidence=1.0,
                        src_code=src,
                    )
                )
        elif snp is not None:
            # gp → snp direct only if no inc (rare); prefer chain via naming when both exist
            key = (node.fqn, snp.fqn)
            if key not in seen and inc is None:
                seen.add(key)
                edges.append(
                    FlowEdge(
                        parent_fqn=node.fqn,
                        child_fqn=snp.fqn,
                        rule=EdgeRule.NAMING,
                        confidence=0.8,
                        src_code=src,
                    )
                )
    return edges
