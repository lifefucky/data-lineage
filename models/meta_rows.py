from typing import Optional

from pydantic import BaseModel, Field, field_validator


class MetadataTableRow(BaseModel):
    # GP has rows with NULL src_code; edges may still resolve via snp name / cut
    src_code: Optional[str] = None
    src_schema: Optional[str] = None
    src_table_name: Optional[str] = None
    dwh_table_name: str
    type_table: Optional[str] = None
    is_active: Optional[bool] = True
    is_archive_load: Optional[int] = None

    @field_validator("dwh_table_name")
    @classmethod
    def dwh_required(cls, v: str) -> str:
        if not v or not str(v).strip():
            raise ValueError("dwh_table_name is required")
        return str(v).strip().lower()

    @field_validator("src_code", mode="before")
    @classmethod
    def src_code_optional(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None


class ViewMappingRow(BaseModel):
    src_table_name: str
    trg_table_name: str
    src_schema: Optional[str] = None
    dst_schema: Optional[str] = None
    dst_tablename: Optional[str] = None

    @field_validator("src_table_name", "trg_table_name")
    @classmethod
    def names_non_empty(cls, v: str) -> str:
        if not v or not str(v).strip():
            raise ValueError("table name is required")
        return str(v).strip().lower()


class GraphNodeRow(BaseModel):
    id_node: int
    schema_code: str
    prc_code: str
    is_active: Optional[bool] = True

    @field_validator("prc_code")
    @classmethod
    def prc_required(cls, v: str) -> str:
        if not v or not str(v).strip():
            raise ValueError("prc_code is required")
        return str(v).strip()


class GraphNodeRelRow(BaseModel):
    id_rel: int
    parent_id_node: Optional[int] = None
    child_id_node: Optional[int] = None
    dst_mart_code: Optional[str] = None
    dst_mart_name: Optional[str] = None
    dag_name: Optional[str] = None
