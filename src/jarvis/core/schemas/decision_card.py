"""Decision card: what a human sees when a task is parked for a major decision."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from .common import SCHEMA_VERSION, RiskClass, StrictModel


class Urgency(StrEnum):
    DIGEST = "digest"
    URGENT = "urgent"


class Option(StrictModel):
    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    tradeoffs: str = Field(min_length=1)


class Evidence(StrictModel):
    kind: str = Field(min_length=1, description="diff, test, council, cost, log, ...")
    ref: str = Field(min_length=1, description="Path, URL or event id")
    summary: str = Field(min_length=1)


class CouncilVote(StrictModel):
    member: str = Field(min_length=1)
    vote: Literal["approve", "concerns", "block"]
    confidence: float = Field(ge=0, le=1)
    reasons: list[str] = Field(default_factory=list, max_length=5)


class RiskAxes(StrictModel):
    """Escalation uses any single axis crossing its threshold, never an average."""

    reversibility: Literal["easy", "hard"]
    blast_radius: Literal["one-file", "service", "many-services", "customers"]
    risk_class: RiskClass
    cost_usd: float | None = Field(default=None, ge=0)
    uncertainty: Literal["low", "medium", "high"] = "low"


class DecisionCard(StrictModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    decision: str = Field(min_length=1, description="The decision needed, in one sentence")
    options: list[Option] = Field(min_length=2, max_length=4)
    recommendation: str = Field(min_length=1, description="Option id the agent recommends")
    confidence: float = Field(ge=0, le=1)
    evidence: list[Evidence] = Field(min_length=1)
    risk: RiskAxes
    council_votes: list[CouncilVote] = Field(default_factory=list)
    urgency: Urgency = Urgency.DIGEST
    created_at: AwareDatetime
    expires_at: AwareDatetime | None = None
    default_on_timeout: Literal["stay_parked"] = "stay_parked"

    @model_validator(mode="after")
    def _check_options(self) -> DecisionCard:
        ids = [o.id for o in self.options]
        if len(set(ids)) != len(ids):
            raise ValueError("option ids must be unique")
        if self.recommendation not in ids:
            raise ValueError("recommendation must be one of the option ids")
        if self.expires_at is not None and self.expires_at <= self.created_at:
            raise ValueError("expires_at must be after created_at")
        return self
