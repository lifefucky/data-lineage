"""Meta data providers: live SQL (read-only) or fixtures."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Sequence, Type

from pydantic import BaseModel, ValidationError

from models import GraphNodeRelRow, GraphNodeRow, MetadataTableRow, ViewMappingRow


class MetaProviderError(Exception):
    """Raised when meta fetch fails or rows are invalid."""


def parse_meta_rows(
    model: Type[BaseModel],
    rows: Sequence[dict],
    *,
    fail_on_invalid: bool,
    skipped: list[int],
) -> list:
    """Validate rows; either raise or skip invalid. skipped[0] accumulates count."""
    out = []
    for row in rows:
        try:
            out.append(model.model_validate(row))
        except ValidationError:
            skipped[0] += 1
            if fail_on_invalid:
                raise MetaProviderError(f"invalid {model.__name__}: {row}") from None
    return out


class MetaProvider(ABC):
    @abstractmethod
    def fetch_metadata_tables(self) -> List[MetadataTableRow]:
        ...

    @abstractmethod
    def fetch_view_mapping_tables(self) -> List[ViewMappingRow]:
        ...

    @abstractmethod
    def fetch_graph_nodes(self) -> List[GraphNodeRow]:
        ...

    @abstractmethod
    def fetch_graph_node_relationships(self) -> List[GraphNodeRelRow]:
        ...


class FixtureMetaProvider(MetaProvider):
    def __init__(
        self,
        metadata_tables: Sequence[dict] | None = None,
        view_mappings: Sequence[dict] | None = None,
        graph_nodes: Sequence[dict] | None = None,
        graph_rels: Sequence[dict] | None = None,
        *,
        fail_on_invalid: bool = True,
    ):
        self._metadata_tables = list(metadata_tables or [])
        self._view_mappings = list(view_mappings or [])
        self._graph_nodes = list(graph_nodes or [])
        self._graph_rels = list(graph_rels or [])
        self.fail_on_invalid = fail_on_invalid
        self.skipped_invalid = 0

    def _parse(self, model, rows: Sequence[dict]):
        skipped = [0]
        out = parse_meta_rows(
            model, rows, fail_on_invalid=self.fail_on_invalid, skipped=skipped
        )
        self.skipped_invalid += skipped[0]
        return out

    def fetch_metadata_tables(self) -> List[MetadataTableRow]:
        return self._parse(MetadataTableRow, self._metadata_tables)

    def fetch_view_mapping_tables(self) -> List[ViewMappingRow]:
        return self._parse(ViewMappingRow, self._view_mappings)

    def fetch_graph_nodes(self) -> List[GraphNodeRow]:
        return self._parse(GraphNodeRow, self._graph_nodes)

    def fetch_graph_node_relationships(self) -> List[GraphNodeRelRow]:
        return self._parse(GraphNodeRelRow, self._graph_rels)


class LiveSQLMetaProvider(MetaProvider):
    """Read-only SELECT against Greenplum via injected connection factory.

    Dirty meta rows are skipped (counted in skipped_invalid), not aborting the build.
    """

    def __init__(self, connector, *, fail_on_invalid: bool = False):
        self.connector = connector
        self.fail_on_invalid = fail_on_invalid
        self.skipped_invalid = 0

    def _parse(self, model, rows: Sequence[dict]):
        skipped = [0]
        out = parse_meta_rows(
            model, rows, fail_on_invalid=self.fail_on_invalid, skipped=skipped
        )
        self.skipped_invalid += skipped[0]
        return out

    def fetch_metadata_tables(self) -> List[MetadataTableRow]:
        sql = """
        SELECT src_code, src_schema, src_table_name, lower(dwh_table_name) AS dwh_table_name,
               type_table, is_active, is_archive_load
          FROM meta.metadata_tables
         WHERE is_active = true
           AND dwh_table_name IS NOT NULL
        """
        rows = self.connector.fetch_all(sql)
        return self._parse(MetadataTableRow, [dict(r) for r in rows])

    def fetch_view_mapping_tables(self) -> List[ViewMappingRow]:
        sql = """
        SELECT DISTINCT src_table_name, trg_table_name, src_schema, dst_schema, dst_tablename
          FROM meta.view_mapping_tables
         WHERE src_table_name IS NOT NULL AND trg_table_name IS NOT NULL
        """
        rows = self.connector.fetch_all(sql)
        return self._parse(ViewMappingRow, [dict(r) for r in rows])

    def fetch_graph_nodes(self) -> List[GraphNodeRow]:
        sql = """
        SELECT id_node, schema_code, prc_code, is_active
          FROM meta.graph_node
         WHERE prc_code IS NOT NULL
        """
        rows = self.connector.fetch_all(sql)
        return self._parse(GraphNodeRow, [dict(r) for r in rows])

    def fetch_graph_node_relationships(self) -> List[GraphNodeRelRow]:
        sql = """
        SELECT id_rel, parent_id_node, child_id_node, dst_mart_code, dst_mart_name, dag_name
          FROM meta.graph_node_relationships
        """
        rows = self.connector.fetch_all(sql)
        return self._parse(GraphNodeRelRow, [dict(r) for r in rows])
