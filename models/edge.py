from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class EdgeRule(str, Enum):
    NAMING = "naming"
    METADATA = "metadata"
    GRAPH_NODE = "graph_node"
    SQL_PARSE = "sql_parse"


class FlowEdge(BaseModel):
    parent_fqn: str
    child_fqn: str
    rule: EdgeRule
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    src_code: Optional[str] = None

    @field_validator("parent_fqn", "child_fqn")
    @classmethod
    def fqn_non_empty(cls, v: str) -> str:
        if not v or "." not in v:
            raise ValueError(f"fqn must be schema.name, got {v!r}")
        return v.strip().lower()
