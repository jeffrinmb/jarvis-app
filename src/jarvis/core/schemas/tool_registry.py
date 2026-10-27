"""Tool registry. Unregistered tools cannot be called (enforced by the gateway, P4.4)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .common import (
    SCHEMA_VERSION,
    Approval,
    DataClass,
    Effect,
    Environment,
    RiskClass,
    StrictModel,
)


class ToolEntry(StrictModel):
    id: str = Field(min_length=1)
    source: str = Field(min_length=1, description="native, cli, api or mcp:<server>")
    version: str = Field(min_length=1, description="Pinned version")
    risk: RiskClass
    effect: Effect
    data_class: DataClass
    allowed_roles: list[str] = Field(min_length=1)
    environments: list[Environment] = Field(min_length=1)
    approval: Approval
    owner: str = Field(min_length=1)
    cost_hint: Literal["low", "medium", "high"] | None = None

    @model_validator(mode="after")
    def _check_approval(self) -> ToolEntry:
        if self.approval is Approval.AUTO:
            if self.effect in (Effect.WRITE_IRREVERSIBLE, Effect.EXTERNAL_COMMS):
                raise ValueError("irreversible or external tools cannot be auto-approved")
            if self.risk is RiskClass.CRITICAL:
                raise ValueError("critical-risk tools cannot be auto-approved")
        if Environment.PROD in self.environments and self.approval is not Approval.HUMAN:
            raise ValueError("a tool usable in prod needs human approval")
        return self


class ToolRegistry(StrictModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    tools: list[ToolEntry]

    @model_validator(mode="after")
    def _unique_ids(self) -> ToolRegistry:
        ids = [t.id for t in self.tools]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate tool ids: {', '.join(dupes)}")
        return self
