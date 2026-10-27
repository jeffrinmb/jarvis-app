"""Policy file schema. The engine that evaluates it lives in ``jarvis.core.policy``.

Evaluation semantics (see ADR 0002), summarised here because they define what a rule means:

* A rule matches when **every** field set in ``when`` matches. Unset fields match anything.
* ``paths``: for ``auto_approve`` rules **all** touched paths must match one of the globs
  (and at least one path must be touched); for ``council_review``, ``human_required`` and
  ``never`` rules **any** touched path matching is enough. This asymmetry stops one allowed
  file from carrying a disallowed one through.
* ``max_diff_lines`` matches only when the cumulative size for the task is known and at most
  the limit. ``min_diff_lines`` matches when the size is unknown or at least the limit. Unknown
  size therefore never helps an action get approved.
* An ``auto_approve`` rule must set at least one condition, so it can never match everything.
"""

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
    risk_at_most: RiskClass | None = None
    max_diff_lines: int | None = Field(default=None, gt=0)
    min_diff_lines: int | None = Field(default=None, gt=0)
    tainted: bool | None = None

    def is_empty(self) -> bool:
        return not (
            self.tools
            or self.paths
            or self.environments
            or self.risk_at_least is not None
            or self.risk_at_most is not None
            or self.max_diff_lines is not None
            or self.min_diff_lines is not None
            or self.tainted is not None
        )

    @model_validator(mode="after")
    def _check_ranges(self) -> Condition:
        if (
            self.risk_at_least is not None
            and self.risk_at_most is not None
            and self.risk_at_least.rank > self.risk_at_most.rank
        ):
            raise ValueError("risk_at_least is higher than risk_at_most; the rule can never match")
        if (
            self.min_diff_lines is not None
            and self.max_diff_lines is not None
            and self.min_diff_lines > self.max_diff_lines
        ):
            raise ValueError("min_diff_lines is above max_diff_lines; the rule can never match")
        return self


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
        unconditional = [r.id for r in self.auto_approve if r.when.is_empty()]
        if unconditional:
            raise ValueError(
                "auto_approve rules must set at least one condition (otherwise they match "
                f"everything): {', '.join(unconditional)}"
            )
        return self
