"""Pydantic model for analytical audit risk flags (Layer 4 output)."""
from typing import Literal

from pydantic import BaseModel, Field


class AuditFlag(BaseModel):
    flag_id: str
    area: str
    severity: Literal["High", "Medium", "Low"]
    evidence: dict = Field(default_factory=dict)
    note_ids: list[str] = Field(default_factory=list)
    standard_query: str
    triggered_by: str
