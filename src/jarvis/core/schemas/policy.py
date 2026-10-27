"""Policy file schema. The engine that evaluates it is built in P2.4."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .common import SCHEMA_VERSION, Decision, Environment, RiskClass, StrictModel

RULE_ID = r"^[a-z0-9][a-z0-9_.-]*$"


class Condition(StrictModel):
    """All specified fields must match. Unspecified fields match anything."""

    tools: list[str] = Field(default_factory=list, description="Tool id globs")
    paths: list[str] = Field(default_factory=list, description="Path globs touched")
    environments: list[Environment] = Field(default_factory=list)
    risk_at_least: RiskClass | None = None
    max_diff_lines: int | None = Field(default=None, gt=0)
    tainted: bool | None = None


class Rule(StrictModel):
    id: str = Field(pattern=RULE_ID)
    description: str = Field(min_length=1)
    when: Condition = Field(default_factory=Condition)


class Policy(StrictModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    version: str = Field(min_length=1, description="Policy version, recorded in audit events")
    default: Decision = Decision.HUMAN
    auto_approve: list[Rule] = Field(default_factory=list)
    council_review: list[Rule] = Field(default_factory=list)
    human_required: list[Rule] = Field(default_factory=list)
    never: list[Rule] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> Policy:
        if self.default is Decision.ALLOW:
            raise ValueError("default must fail closed: use 'human' or 'deny', not 'allow'")
        ids = [
            r.id
            for r in (*self.auto_approve, *self.council_review, *self.human_required, *self.never)
        ]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate rule ids: {', '.join(dupes)}")
        return self
