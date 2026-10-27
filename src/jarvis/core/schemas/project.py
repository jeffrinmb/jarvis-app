"""Per-project configuration (`.agents/project.yaml`) and model tier mapping."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .common import SCHEMA_VERSION, Environment, StrictModel, Tier


class ProjectMode(StrEnum):
    GREENFIELD = "greenfield"
    BROWNFIELD = "brownfield"


class Budgets(StrictModel):
    per_task_tokens: int | None = Field(default=None, gt=0)
    per_day_cost_usd: float | None = Field(default=None, gt=0)


class DataPolicy(StrictModel):
    allowed_models: list[str] = Field(min_length=1)
    retention: Literal["zero", "30d", "standard"] = "zero"


class ProjectConfig(StrictModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    project: str = Field(min_length=1)
    mode: ProjectMode
    stack: str = Field(min_length=1)
    commands: dict[str, str] = Field(default_factory=dict)
    tiers: dict[str, Tier] = Field(description="Role name to model tier")
    budgets: Budgets = Field(default_factory=Budgets)
    no_go_paths: list[str] = Field(default_factory=list)
    tools_enabled: list[str] = Field(default_factory=list)
    environments: list[Environment] = Field(min_length=1)
    data_policy: DataPolicy

    @field_validator("environments")
    @classmethod
    def _agents_never_get_prod(cls, value: list[Environment]) -> list[Environment]:
        if Environment.PROD in value:
            raise ValueError("agents never get the prod environment; deploys are human-gated")
        return value

    @model_validator(mode="after")
    def _brownfield_needs_no_go(self) -> ProjectConfig:
        if self.mode is ProjectMode.BROWNFIELD and not self.no_go_paths:
            raise ValueError(
                "brownfield projects must list no_go_paths (use a placeholder if none)"
            )
        return self


class TiersConfig(StrictModel):
    """Maps each tier to concrete model ids, in fallback order."""

    schema_version: Literal[1] = SCHEMA_VERSION
    tiers: dict[Tier, list[str]]

    @model_validator(mode="after")
    def _all_tiers_present(self) -> TiersConfig:
        missing = [t.value for t in Tier if not self.tiers.get(t)]
        if missing:
            raise ValueError(f"every tier needs at least one model; missing: {', '.join(missing)}")
        return self
