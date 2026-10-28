"""Audit and operational event: the unit written to the ledger."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import AwareDatetime, Field

from .common import SCHEMA_VERSION, Decision, RiskClass, StrictModel, TrustLabel


class EventKind(StrEnum):
    TASK_CREATED = "task_created"
    TASK_STATE_CHANGED = "task_state_changed"
    MODEL_CALL = "model_call"
    TOOL_CALL = "tool_call"
    TOOL_DENIED = "tool_denied"
    POLICY_DECISION = "policy_decision"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_GRANTED = "approval_granted"
    APPROVAL_DENIED = "approval_denied"
    COUNCIL_VOTE = "council_vote"
    MEMORY_PROPOSED = "memory_proposed"
    MEMORY_PROMOTED = "memory_promoted"
    HANDOFF = "handoff"
    FILE_EDIT = "file_edit"
    BUDGET_WARNING = "budget_warning"
    BUDGET_EXCEEDED = "budget_exceeded"
    LOOP_DETECTED = "loop_detected"
    ANOMALY_DETECTED = "anomaly_detected"
    KILL_SWITCH = "kill_switch"
    ERROR = "error"
    NOTE = "note"


class ToolRef(StrictModel):
    id: str = Field(min_length=1)
    version: str | None = None


class ModelRef(StrictModel):
    name: str = Field(min_length=1)
    provider: str | None = None
    prompt_version: str | None = None


class Usage(StrictModel):
    tokens_in: int = Field(default=0, ge=0)
    tokens_out: int = Field(default=0, ge=0)
    tokens_cached: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0)


def _now() -> datetime:
    return datetime.now(UTC)


class Event(StrictModel):
    """One thing that happened. Free-form detail goes in ``data`` and must be redacted
    before it is written (no secrets or PII)."""

    schema_version: Literal[1] = SCHEMA_VERSION
    event_id: str = Field(default_factory=lambda: uuid4().hex, pattern=r"^[0-9a-f]{32}$")
    ts: AwareDatetime = Field(default_factory=_now)
    kind: EventKind
    agent_id: str = Field(min_length=1)
    on_behalf_of: str = Field(min_length=1)
    task_id: str | None = None
    stage: str | None = None
    trace_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    span_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{16}$")
    tool: ToolRef | None = None
    model: ModelRef | None = None
    usage: Usage | None = None
    risk: RiskClass | None = None
    decision: Decision | None = None
    trust: TrustLabel | None = None
    tainted: bool | None = None
    approver: str | None = None
    outcome: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
