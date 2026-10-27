"""An action presented to the policy gate for a decision.

The action carries **facts**, not opinions. There is deliberately no ``risk`` field:
risk is computed by the gate from the tool registry and the evidence below, so an agent
cannot lower it by describing its own action as harmless. The harness adapter (not the
agent) is responsible for filling ``session`` and ``input_trust`` truthfully.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .common import SCHEMA_VERSION, DataClass, Environment, StrictModel, TrustLabel


class SessionState(StrictModel):
    """What the session has already been exposed to."""

    trust_labels_seen: list[TrustLabel] = Field(default_factory=list)
    data_classes_seen: list[DataClass] = Field(default_factory=list)


class Action(StrictModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    agent_id: str = Field(min_length=1)
    on_behalf_of: str = Field(min_length=1)
    role: str = Field(min_length=1, description="Role the agent is acting in")
    task_id: str | None = None
    tool_id: str = Field(min_length=1)
    environment: Environment
    paths: list[str] = Field(default_factory=list, description="Paths the action touches")
    diff_lines: int | None = Field(
        default=None,
        ge=0,
        description="Lines this action changes. Unknown (null) is treated as unbounded for "
        "tools that are not read-only.",
    )
    input_trust: list[TrustLabel] = Field(
        default_factory=list,
        description="Trust labels of the content that directly feeds this action",
    )
    session: SessionState = Field(default_factory=SessionState)
