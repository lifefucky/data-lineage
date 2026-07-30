from typing import Optional

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

from .enums import Layer

ALLOWED_SCHEMAS = frozenset({"stg_ods", "ods", "dds", "dm"})


class TableNode(BaseModel):
    schema_name: str = Field(alias="schema")
    name: str
    layer: Layer
    src_code: Optional[str] = None

    model_config = {"populate_by_name": True}

    @field_validator("schema_name")
    @classmethod
    def schema_allowed(cls, v: str) -> str:
        if v not in ALLOWED_SCHEMAS:
            raise ValueError(f"schema must be one of {sorted(ALLOWED_SCHEMAS)}, got {v!r}")
        return v

    @field_validator("name")
    @classmethod
    def name_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("name must be non-empty")
        return v.strip().lower()

    @model_validator(mode="after")
    def dm_view_requires_dm_schema(self) -> "TableNode":
        if self.layer == Layer.DM_VIEW and self.schema_name != "dm":
            raise ValueError("layer dm_view requires schema='dm'")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def fqn(self) -> str:
        return f"{self.schema_name}.{self.name}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_view(self) -> bool:
        return self.layer == Layer.DM_VIEW
