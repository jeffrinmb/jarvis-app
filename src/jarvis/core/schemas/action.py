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
    domains: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="Hosts named in this call's input (a URL's host, lowercase, with no path, "
        "query or credentials). Filled by the adapter's own scan, not by the agent",
    )
    data_bytes: int | None = Field(
        default=None,
        ge=0,
        description="Size in bytes of this call's input as the adapter measured it, which bounds "
        "what a tool that sends data outward can carry. The gate counts it only for tools whose "
        "registry effect is external-comms. Filled by the adapter, not by the agent",
    )
    secrets_in_input: list[str] = Field(
        default_factory=list,
        description="Kinds of secret the adapter found in this call's input (for example "
        "github-token), never the secret itself. Filled by the adapter's own scan, not by the "
        "agent. Empty means none was found, or the input was not scanned",
    )
    session: SessionState = Field(default_factory=SessionState)
