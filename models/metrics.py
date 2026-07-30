from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from .enums import CountMode, StatusColor
from .table import TableNode


class TableMetrics(BaseModel):
    fqn: str
    row_count: int = Field(ge=0)
    count_mode: CountMode
    count_ts: datetime

    @field_validator("fqn")
    @classmethod
    def fqn_non_empty(cls, v: str) -> str:
        if not v or "." not in v:
            raise ValueError(f"fqn must be schema.name, got {v!r}")
        return v.strip().lower()


class NodeView(BaseModel):
    node: TableNode
    metrics: Optional[TableMetrics] = None
    status_color: StatusColor = StatusColor.UNKNOWN
