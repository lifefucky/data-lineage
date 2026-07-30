"""ODS edges from meta.metadata_tables: snp → ods."""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Set

from models import EdgeRule, FlowEdge, Layer, MetadataTableRow, TableNode

SNP_RE = re.compile(r"^t_(\d{3})_snp_(.+)$", re.IGNORECASE)


def build_ods_edges(
    nodes: Iterable[TableNode],
    metadata_rows: Iterable[MetadataTableRow],
) -> List[FlowEdge]:
    by_fqn: Dict[str, TableNode] = {n.fqn: n for n in nodes}
    snp_by_name = {
        n.name: n for n in by_fqn.values() if n.schema_name == "stg_ods" and n.layer == Layer.SNP
    }
    edges: List[FlowEdge] = []
    seen: Set[tuple] = set()

    for row in metadata_rows:
        # Prefer stg_ods snp rows pointing to ODS physical tables
        dwh = row.dwh_table_name.lower()
        src_schema = (row.src_schema or "").lower()
        type_table = (row.type_table or "").lower()

        snp_node = None
        src_code = row.src_code

        if src_schema == "stg_ods" or type_table == "snp":
            # dwh_table_name may be snp name or ods name depending on row
            if dwh in snp_by_name:
                snp_node = snp_by_name[dwh]
            elif row.src_table_name:
                candidate = row.src_table_name.lower()
                if candidate in snp_by_name:
                    snp_node = snp_by_name[candidate]
                elif not candidate.startswith("t_") and src_code:
                    cand = f"t_{src_code}_snp_{candidate}"
                    snp_node = snp_by_name.get(cand)

        if snp_node is None and SNP_RE.match(dwh):
            snp_node = snp_by_name.get(dwh)

        # ODS target: for stg_ods metadata rows, dwh_table_name is often the ods table
        ods_name = None
        if src_schema == "stg_ods":
            ods_name = dwh
            # if dwh is snp table, derive ods by stripping _snp_
            m = SNP_RE.match(dwh)
            if m:
                ods_name = f"t_{m.group(1)}_{m.group(2)}"
        elif type_table == "snp" and row.src_table_name:
            # external→inc row: find sibling stg_ods snp → ods via cut
            cut = None
            inc = dwh
            m_inc = re.match(r"^t_(\d{3})_inc_(.+)$", inc, re.IGNORECASE)
            if m_inc:
                src_code = m_inc.group(1)
                cut = m_inc.group(2)
                snp_node = snp_by_name.get(f"t_{src_code}_snp_{cut}")
                ods_name = f"t_{src_code}_{cut}"

        if snp_node is None or not ods_name:
            continue

        ods_fqn = f"ods.{ods_name}"
        if ods_fqn not in by_fqn:
            continue
        key = (snp_node.fqn, ods_fqn)
        if key in seen:
            continue
        seen.add(key)
        edges.append(
            FlowEdge(
                parent_fqn=snp_node.fqn,
                child_fqn=ods_fqn,
                rule=EdgeRule.METADATA,
                confidence=1.0,
                src_code=src_code or snp_node.src_code,
            )
        )
    return edges
