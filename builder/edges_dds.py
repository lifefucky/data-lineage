"""DDS edges from meta.view_mapping_tables: ods → dds."""
from __future__ import annotations

from typing import Dict, Iterable, List, Set

from models import EdgeRule, FlowEdge, TableNode, ViewMappingRow


def build_dds_edges(
    nodes: Iterable[TableNode],
    mappings: Iterable[ViewMappingRow],
) -> List[FlowEdge]:
    by_fqn: Dict[str, TableNode] = {n.fqn: n for n in nodes}
    edges: List[FlowEdge] = []
    seen: Set[tuple] = set()

    for row in mappings:
        src = row.src_table_name
        trg = row.trg_table_name
        # Prefer explicit schemas from transformation_mapping when present
        src_schema = (row.src_schema or "ods").lower()
        dst_schema = (row.dst_schema or "dds").lower()
        if dst_schema not in ("dds",) and row.dst_tablename:
            trg = row.dst_tablename.lower()
            dst_schema = "dds" if dst_schema not in ("ods", "stg_ods") else dst_schema

        # Normalize: mapping usually ods → dds physical names without schema prefix
        if src_schema not in ("ods", "dds", "stg_ods"):
            src_schema = "ods"
        if dst_schema not in ("dds", "ods"):
            dst_schema = "dds"

        parent = f"{src_schema}.{src}"
        child = f"{dst_schema}.{trg}"
        # Fallback try ods→dds if schemas wrong
        candidates = [(parent, child)]
        if parent not in by_fqn or child not in by_fqn:
            candidates.append((f"ods.{src}", f"dds.{trg}"))

        for p, c in candidates:
            if p in by_fqn and c in by_fqn and p != c:
                key = (p, c)
                if key not in seen:
                    seen.add(key)
                    edges.append(
                        FlowEdge(
                            parent_fqn=p,
                            child_fqn=c,
                            rule=EdgeRule.METADATA,
                            confidence=1.0,
                        )
                    )
                break
    return edges
