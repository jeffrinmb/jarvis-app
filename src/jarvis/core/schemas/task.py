"""Task record. State transitions are enforced later by the orchestrator (P4.1)."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from .common import SCHEMA_VERSION, StrictModel


class TaskState(StrEnum):
    CREATED = "created"
    SPECIFIED = "specified"
    PLANNED = "planned"
    BUILDING = "building"
    VERIFYING = "verifying"
    REVIEWING = "reviewing"
    AWAITING_HUMAN = "awaiting_human"
    PARKED = "parked"
    MERGED = "merged"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskBudget(StrictModel):
    max_tokens: int | None = Field(default=None, gt=0)
    max_cost_usd: float | None = Field(default=None, gt=0)


class Task(StrictModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    owner: str = Field(min_length=1, description="Named human accountable for the task")
    state: TaskState = TaskState.CREATED
    stage: str | None = None
    baseline_revision: str | None = Field(
        default=None, description="Git revision recorded before any change is made"
    )
    branch: str | None = None
    worktree_path: str | None = None
    budget: TaskBudget = Field(default_factory=TaskBudget)
    tainted: bool = False
    parked_reason: str | None = None
    done_by: str | None = Field(
        default=None, description="Human who marked the task done; agents cannot"
    )
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def _check_state_fields(self) -> Task:
        if self.state is TaskState.PARKED and not self.parked_reason:
            raise ValueError("a parked task needs parked_reason")
        if self.state is TaskState.DONE and not self.done_by:
            raise ValueError("a done task needs done_by (a human)")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at is before created_at")
        return self
