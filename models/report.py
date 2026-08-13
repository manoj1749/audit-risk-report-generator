"""Pydantic models for standards retrieval output and the final audit report (Layer 5)."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from models.financial import ComputedRatios, MovementRecord


class RetrievedChunk(BaseModel):
    text: str
    source: str       # Filename e.g. "Ind AS 116.pdf"
    page: int
    score: float


class ObservationResult(BaseModel):
    flag_id: str
    area: str
    observation: str
    risk_rating: Literal["High", "Medium", "Low"]
    standard_reference: str
    recommendation: str
    evidence: dict = Field(default_factory=dict)


class AuditReport(BaseModel):
    company_name: str | None
    period: str | None
    generated_at: datetime
    extraction_method: str
    summary: dict
    observations: list[ObservationResult]
    key_movements: list[MovementRecord]
    ratios: ComputedRatios
    flags_triggered: int
    observations_generated: int
    disclaimer: str = (
        "Preliminary desk-based observations from analytical review only. "
        "Not corroborated through vouching, third-party confirmation, or "
        "management inquiry. Validate during fieldwork before inclusion in "
        "any final report."
    )
