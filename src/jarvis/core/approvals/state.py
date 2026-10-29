"""Read the ledger to find which approvals still count, and whether one covers an action.

Everything here is derived from the ledger on every check, so there is no approval table to
tamper with or to fall out of step. A grant counts only if all of these hold:

* it was written by ``agentctl approvals approve`` (agent id ``agentctl:approve``) and parses;
* its approver is allowed by the policy (when a list is set) and is not an agent;
* its expiry is in the future and no more than ``max_scope_hours`` after it was granted;
* no later rejection revoked it, and, if single-use, no allowed decision has used it.

The gate re-checks all of this itself, so a grant written by hand with a longer expiry or by a
name outside the list does not count even if the command line would have refused it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError

from jarvis.core.matching import matches_any, normalize_path
from jarvis.core.schemas import Decision, EventKind, ToolEntry
from jarvis.core.schemas.approval import ApprovalGrant, ApprovalRequest, ApprovalScope
from jarvis.core.schemas.policy import ApprovalSettings

from .rules import Profile, is_agent_name

APPROVAL_AGENT = "agentctl:approve"


@dataclass(frozen=True)
class Grant:
    grant_id: str  # the ledger event id
    request_id: str
    approver: str
    scope: ApprovalScope
    granted_at: datetime


@dataclass
class ApprovalState:
    requests: dict[str, ApprovalRequest] = field(default_factory=dict)
    request_agents: dict[str, str] = field(default_factory=dict)  # request id -> raising agent
    grants: list[Grant] = field(default_factory=list)  # still valid
    rejected: set[str] = field(default_factory=set)  # request ids with a rejection on record
    used: set[str] = field(default_factory=set)  # single-use grant ids already consumed


def _moment(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment.astimezone(UTC) if moment.tzinfo else None


def _valid_grant(event: dict[str, Any], settings: ApprovalSettings, now: datetime) -> Grant | None:
    if event.get("agent_id") != APPROVAL_AGENT:
        return None
    try:
        grant = ApprovalGrant.model_validate((event.get("data") or {}).get("grant"))
    except (ValidationError, TypeError, ValueError):
        return None
    granted_at = _moment(event.get("ts"))
    scope = grant.scope
    if grant.via != "cli" or scope is None or granted_at is None:
        return None
    if settings.approvers and grant.approver not in settings.approvers:
        return None
    expires = scope.expires_at.astimezone(UTC)
    if expires <= now or expires > granted_at + timedelta(hours=settings.max_scope_hours):
        return None
    event_id = event.get("event_id")
    if not isinstance(event_id, str):
        return None
    return Grant(event_id, grant.request_id, grant.approver, scope, granted_at)


def scan_approvals(
    records: Iterable[dict[str, Any]], now: datetime, settings: ApprovalSettings
) -> ApprovalState:
    """Fold the ledger into the current approval state. ``now`` must be timezone-aware."""
    now = now.astimezone(UTC)
    state = ApprovalState()
    grants: list[Grant] = []
    for record in records:
        event = record["event"]
        kind = event.get("kind")
        data = event.get("data") or {}
        if kind == EventKind.APPROVAL_REQUESTED.value:
            try:
                request = ApprovalRequest.model_validate(data.get("request"))
            except (ValidationError, TypeError, ValueError):
                continue
            state.requests[request.request_id] = request
            state.request_agents[request.request_id] = str(event.get("agent_id") or "")
        elif kind == EventKind.APPROVAL_GRANTED.value:
            grant = _valid_grant(event, settings, now)
            if grant is not None:
                grants.append(grant)
        elif kind == EventKind.APPROVAL_DENIED.value and event.get("agent_id") == APPROVAL_AGENT:
            request_id = data.get("request_id")
            if isinstance(request_id, str):
                state.rejected.add(request_id)
                grants = [g for g in grants if g.request_id != request_id]  # revokes earlier ones
        elif kind == EventKind.POLICY_DECISION.value and event.get("decision") == (
            Decision.ALLOW.value
        ):
            ids = data.get("approval_grant_ids")
            if isinstance(ids, list):
                state.used.update(i for i in ids if isinstance(i, str))
    state.grants = [g for g in grants if not (g.scope.single_use and g.grant_id in state.used)]
    return state


def covers(
    grant: Grant,
    *,
    task_id: str | None,
    tool: ToolEntry,
    paths: list[str],
    call_fingerprint: str,
    total_diff: int,
    size_known: bool,
    profile: Profile,
) -> bool:
    """Whether this grant's scope includes the action. Every condition must hold."""
    scope = grant.scope
    if task_id is None or scope.task_id != task_id or tool.id not in scope.tools:
        return False
    # High-risk, irreversible and outbound tools: only a single-use approval of this exact call.
    if profile.exact_only and not (scope.single_use and scope.fingerprint == call_fingerprint):
        return False
    if scope.fingerprint is not None and scope.fingerprint != call_fingerprint:
        return False
    normalised = [normalize_path(p)[0] for p in paths]
    if normalised:
        if not scope.paths or not all(matches_any(scope.paths, p) for p in normalised):
            return False
    elif scope.paths:
        return False  # a path scope does not cover an action that names no path
    # Anything that changes state needs a known size inside the approved limit.
    if tool.effect != "read-only":
        if not size_known or scope.max_diff_lines is None or total_diff > scope.max_diff_lines:
            return False
    return True


def covering_grants(grants: list[Grant], *, agent_id: str, **facts: Any) -> list[Grant]:
    """The valid grants that cover the action. A grant by an agent never counts."""
    return [g for g in grants if not is_agent_name(g.approver, agent_id) and covers(g, **facts)]
