"""Scan gp_metadata_export DDL files into TableNode list."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable, List, Optional, Set, Tuple

from models import Layer, TableNode

NODE_SCHEMAS = ("stg_ods", "ods", "dds", "dm")
SKIP_SCHEMAS = frozenset({"meta", "dm_service"})
PARTITION_RE = re.compile(r"_1_prt_", re.IGNORECASE)

GP_RE = re.compile(r"^gp_(\d{3})_(.+)$", re.IGNORECASE)
INC_RE = re.compile(r"^t_(\d{3})_inc_(.+)$", re.IGNORECASE)
SNP_RE = re.compile(r"^t_(\d{3})_snp_(.+)$", re.IGNORECASE)
ODS_SRC_RE = re.compile(r"^t_(\d{3})_(.+)$", re.IGNORECASE)


def is_partition_name(name: str) -> bool:
    return bool(PARTITION_RE.search(name))


def infer_layer_and_src(schema: str, name: str) -> Tuple[Optional[Layer], Optional[str]]:
    """Return (layer, src_code) or (None, None) if name cannot be classified."""
    n = name.lower()
    if schema == "stg_ods":
        m = GP_RE.match(n)
        if m:
            return Layer.GP, m.group(1)
        m = INC_RE.match(n)
        if m:
            return Layer.INC, m.group(1)
        m = SNP_RE.match(n)
        if m:
            return Layer.SNP, m.group(1)
        return None, None
    if schema == "ods":
        m = ODS_SRC_RE.match(n)
        src = m.group(1) if m else None
        return Layer.ODS, src
    if schema == "dds":
        return Layer.DDS, None
    if schema == "dm":
        return Layer.DM, None
    return None, None


def _iter_table_sql_stems(tables_dir: Path) -> List[str]:
    """Non-partition *.sql stems, sorted. Bodies are never read."""
    keepers: List[str] = []
    try:
        with os.scandir(tables_dir) as it:
            for entry in it:
                if not entry.is_file(follow_symlinks=False):
                    continue
                name = entry.name
                if not name.lower().endswith(".sql"):
                    continue
                stem = Path(name).stem.lower()
                if is_partition_name(stem):
                    continue
                keepers.append(stem)
    except FileNotFoundError:
        return []
    keepers.sort()
    return keepers


class NodeScanner:
    def __init__(self, export_root: Path):
        self.export_root = Path(export_root)

    def scan(self) -> List[TableNode]:
        nodes: List[TableNode] = []
        seen: Set[str] = set()
        for schema in NODE_SCHEMAS:
            tables_dir = self.export_root / schema / "tables"
            if not tables_dir.is_dir():
                continue
            for name in _iter_table_sql_stems(tables_dir):
                layer, src_code = infer_layer_and_src(schema, name)
                if layer is None:
                    continue
                node = TableNode(schema=schema, name=name, layer=layer, src_code=src_code)
                if node.fqn in seen:
                    continue
                seen.add(node.fqn)
                nodes.append(node)
        return nodes

    def scan_schemas(self, schemas: Iterable[str]) -> List[TableNode]:
        allowed = set(schemas) - SKIP_SCHEMAS
        return [n for n in self.scan() if n.schema_name in allowed]
