"""Approval requests and grants: how a human decision is recorded and later checked (P2.7).

Both travel as the ``data`` of ledger events (``approval_requested``, ``approval_granted``), so
they are tamper-evident like every other event and nothing needs a second store.

A **request** is built by the gate from its own verdict, never from text the agent wrote
(ASI09, RT-13): the tool, the paths, the size, the rules that fired and the taint flags. It
carries a *proposed* scope, which is what a person would approve if they accepted it as it is.

A **grant** is one named person's approval of a bounded scope that expires. The gate, not this
module, decides whether a grant still counts (see ``jarvis.core.approvals``).
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import AwareDatetime, Field, field_validator, model_validator

from .common import SCHEMA_VERSION, RiskClass, StrictModel

REQUEST_ID = r"^req-[0-9a-f]{12}$"
FINGERPRINT = r"^[0-9a-f]{16}$"
_GLOB_CHARS = re.compile(r"[*?\[\]{}]")
# Globs that would approve everything under a task. A person who wants that should say so by
# naming directories, not by typing a wildcard that happens to match all of them.
TOO_BROAD = {"*", "**", "**/*", "/**", "./**", "**/**"}


class ProposedScope(StrictModel):
    """What an approval would cover, before it has an owner or an expiry."""

    tools: list[str] = Field(min_length=1, description="Exact registry tool ids, never globs")
    paths: list[str] = Field(
        default_factory=list,
        description="Path globs. Empty covers only actions that touch no path",
    )
    max_diff_lines: int | None = Field(
        default=None,
        gt=0,
        description="Cumulative changed lines for the task; unset is valid only for read-only "
        "tools",
    )
    fingerprint: str | None = Field(
        default=None, pattern=FINGERPRINT, description="Restricts the approval to one exact call"
    )
    single_use: bool = False

    @field_validator("tools")
    @classmethod
    def _exact_tools(cls, value: list[str]) -> list[str]:
        if any(_GLOB_CHARS.search(t) for t in value):
            raise ValueError("tools must be exact registry ids, not globs")
        return value

    @field_validator("paths")
    @classmethod
    def _bounded_paths(cls, value: list[str]) -> list[str]:
        for glob in value:
            normalised = glob.replace("\\", "/").strip()
            if not normalised or normalised in TOO_BROAD:
                raise ValueError(f"path glob {glob!r} would approve everything; name directories")
            if normalised.startswith("/") or re.match(r"^[A-Za-z]:", normalised):
                raise ValueError("path globs must be relative to the repository")
            if ".." in normalised.split("/"):
                raise ValueError("path globs must stay inside the repository")
        return value

    @model_validator(mode="after")
    def _single_use_needs_a_call(self) -> ProposedScope:
        if self.single_use and self.fingerprint is None:
            raise ValueError("a single-use approval must name the exact call (fingerprint)")
        return self


class ApprovalScope(ProposedScope):
    task_id: str = Field(min_length=1)
    expires_at: AwareDatetime


class RequestRule(StrictModel):
    rule_id: str = Field(min_length=1)
    category: str = Field(min_length=1)
    effect: Literal["human", "council"]


class ApprovalRequest(StrictModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    request_id: str = Field(pattern=REQUEST_ID)
    task_id: str | None = None
    tool_id: str = Field(min_length=1)
    paths: list[str] = Field(default_factory=list, max_length=50)
    diff_lines: int = Field(default=0, ge=0)
    total_diff_lines: int = Field(default=0, ge=0)
    diff_size_known: bool = True
    fingerprint: str = Field(pattern=FINGERPRINT)
    decision: Literal["human", "council"]
    rules: list[RequestRule] = Field(default_factory=list)
    tainted: bool = False
    risk: RiskClass | None = None
    four_eyes: bool = Field(default=False, description="Two different approvers are needed")
    proposed_scope: ProposedScope


class ApprovalGrant(StrictModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    request_id: str | None = Field(
        default=None,
        pattern=REQUEST_ID,
        description="Required on the command line. A prompt approval may have none, for a gate "
        "(such as the lethal trifecta) that is answered in the terminal and never carded",
    )
    tool_use_id: str | None = Field(
        default=None, max_length=100, description="Claude Code's id for the call a person allowed"
    )
    approver: str = Field(min_length=1, max_length=100)
    via: Literal["cli", "claude-code-prompt"]
    scope: ApprovalScope | None = Field(
        default=None,
        description="Set for an approval that can be reused. A prompt approval is a record of "
        "one call a person allowed in the terminal and carries no scope",
    )

    @model_validator(mode="after")
    def _scope_matches_channel(self) -> ApprovalGrant:
        if self.via == "cli" and (self.scope is None or self.request_id is None):
            raise ValueError("an approval given on the command line needs a request and a scope")
        if self.via == "claude-code-prompt" and self.tool_use_id is None:
            raise ValueError("a prompt approval must name the call (tool_use_id)")
        if self.via == "claude-code-prompt" and self.scope is not None:
            raise ValueError("a prompt approval records one call and cannot carry a scope")
        return self
