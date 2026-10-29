"""The policy gate's answer, recorded in the audit log with every decision."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .common import SCHEMA_VERSION, Decision, RiskClass, StrictModel

MatchCategory = Literal[
    "auto_approve",
    "council_review",
    "human_required",
    "never",
    "builtin",
    "registry",
    "approval",
]


class RuleMatch(StrictModel):
    rule_id: str = Field(min_length=1)
    category: MatchCategory
    effect: Decision
    reason: str = Field(min_length=1)


class Verdict(StrictModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    decision: Decision
    reason: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    risk: RiskClass | None = Field(default=None, description="Risk class taken from the registry")
    tainted: bool = False
    trifecta: bool = False
    diff_size_known: bool = True
    total_diff_lines: int = Field(default=0, ge=0)
    matches: list[RuleMatch] = Field(default_factory=list)
    approval_grant_ids: list[str] = Field(
        default_factory=list,
        description="Grants that let this call through; a single-use grant is spent by it",
    )
    approval_request: str | None = Field(
        default=None,
        description="Id of the approval request a person can answer to let this call through",
    )
