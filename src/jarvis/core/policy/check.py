"""Run the policy gate end to end: read history, decide, and record the decision.

The decision is only returned to the caller after it has been written to the audit ledger.
If the ledger cannot be read or written, the answer is ``deny``: an action that cannot be
audited is not allowed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jarvis.core.approvals import profile_for, request_id, restrictive, scan_approvals
from jarvis.core.killswitch import read_halt
from jarvis.core.ledger import Ledger, LedgerError
from jarvis.core.matching import normalize_path
from jarvis.core.schemas import (
    Action,
    Decision,
    Event,
    EventKind,
    Policy,
    ProjectConfig,
    ToolRegistry,
    TrustLabel,
    Verdict,
)
from jarvis.core.schemas.approval import ApprovalRequest, ProposedScope, RequestRule
from jarvis.core.schemas.event import ToolRef
from jarvis.core.schemas.verdict import RuleMatch

from .engine import evaluate
from .usage import Anomaly, compute_usage, detect_anomalies, fingerprint

COUNTED = {Decision.ALLOW.value, Decision.COUNCIL.value, Decision.HUMAN.value}
MAX_LOGGED_PATHS = 50


def prior_diff_lines(records, task_id: str | None) -> int:
    """Sum the diff lines of earlier decisions for ``task_id`` that did not end in deny.

    Counting pending (council or human) decisions as well as allowed ones stops a large
    change being split into small pieces that each fit under a limit.
    """
    if task_id is None:
        return 0
    total = 0
    for record in records:
        event = record["event"]
        if event.get("kind") != EventKind.POLICY_DECISION.value:
            continue
        if event.get("task_id") != task_id or event.get("decision") not in COUNTED:
            continue
        lines = (event.get("data") or {}).get("diff_lines", 0)
        if isinstance(lines, int) and lines > 0:
            total += lines
    return total


def _fail_closed(policy: Policy, action: Action, reason: str) -> Verdict:
    match = RuleMatch(
        rule_id="builtin:audit-unavailable",
        category="builtin",
        effect=Decision.DENY,
        reason=reason,
    )
    return Verdict(
        decision=Decision.DENY,
        reason=reason,
        policy_version=policy.version,
        tainted=TrustLabel.UNTRUSTED in {*action.session.trust_labels_seen, *action.input_trust},
        matches=[match],
    )


def decision_event(
    action: Action,
    verdict: Verdict,
    version: str | None,
    extra: dict[str, Any] | None = None,
) -> Event:
    """Build the audit event for a decision. ``extra`` is caller context (for example the
    harness, session and permission mode) stored under ``data["context"]``; it must already
    be free of secrets and file contents."""
    return Event(
        kind=EventKind.POLICY_DECISION,
        agent_id=action.agent_id,
        on_behalf_of=action.on_behalf_of,
        task_id=action.task_id,
        tool=ToolRef(id=action.tool_id, version=version),
        risk=verdict.risk,
        decision=verdict.decision,
        trust=TrustLabel.UNTRUSTED if verdict.tainted else None,
        tainted=verdict.tainted,
        outcome=verdict.decision.value,
        data={
            "policy_version": verdict.policy_version,
            "reason": verdict.reason,
            "diff_lines": action.diff_lines or 0,
            "total_diff_lines": verdict.total_diff_lines,
            "diff_size_known": verdict.diff_size_known,
            "trifecta": verdict.trifecta,
            "environment": action.environment.value,
            "paths": action.paths[:MAX_LOGGED_PATHS],
            "approval_grant_ids": verdict.approval_grant_ids,
            "approval_request": verdict.approval_request,
            "fingerprint": fingerprint(action.tool_id, action.paths),
            "matches": [
                {"rule_id": m.rule_id, "category": m.category, "effect": m.effect.value}
                for m in verdict.matches
            ],
            "context": dict(extra or {}),
        },
    )


def _pending_request(action: Action, verdict: Verdict, tool) -> ApprovalRequest | None:
    """The request a person could answer to let this call through, built from the verdict and
    the registry and never from anything the agent wrote (P2.7c). ``None`` when nothing can be
    approved: an allow, a deny, or a human gate that a stored approval must not lift."""
    if tool is None or verdict.decision not in (Decision.HUMAN, Decision.COUNCIL):
        return None
    profile = profile_for(verdict.matches, tool)
    if not profile.approvable:
        return None
    call = fingerprint(action.tool_id, action.paths)
    paths = [normalize_path(p)[0] for p in action.paths[:MAX_LOGGED_PATHS]]
    try:
        scope = ProposedScope(
            tools=[tool.id],
            paths=paths,
            # The held call already counts toward the task's total, so the retry that follows an
            # approval adds its own size on top. Leave room for exactly that one retry.
            max_diff_lines=(
                None
                if not verdict.diff_size_known
                else max(verdict.total_diff_lines + (action.diff_lines or 0), 1)
            ),
            fingerprint=call if profile.exact_only else None,
            single_use=profile.exact_only,
        )
        return ApprovalRequest(
            request_id=request_id(action.task_id, call, verdict.matches),
            task_id=action.task_id,
            tool_id=action.tool_id,
            paths=paths,
            diff_lines=action.diff_lines or 0,
            total_diff_lines=verdict.total_diff_lines,
            diff_size_known=verdict.diff_size_known,
            fingerprint=call,
            decision=verdict.decision.value,
            rules=[
                RequestRule(rule_id=m.rule_id, category=m.category, effect=m.effect.value)
                for m in restrictive(verdict.matches)
            ],
            tainted=verdict.tainted,
            risk=verdict.risk,
            four_eyes=profile.four_eyes,
            proposed_scope=scope,
        )
    except ValueError:
        return None  # a path that cannot be scoped safely is left to the terminal prompt


def _request_event(action: Action, request: ApprovalRequest, version: str | None) -> Event:
    return Event(
        kind=EventKind.APPROVAL_REQUESTED,
        agent_id=action.agent_id,
        on_behalf_of=action.on_behalf_of,
        task_id=action.task_id,
        tool=ToolRef(id=action.tool_id, version=version),
        outcome="pending",
        data={"request": request.model_dump(mode="json")},
    )


LIMIT_EVENTS = {
    "builtin:budget-task-calls": EventKind.BUDGET_EXCEEDED,
    "builtin:budget-day-calls": EventKind.BUDGET_EXCEEDED,
    "builtin:loop-detected": EventKind.LOOP_DETECTED,
}


def _limit_events(
    action: Action, verdict: Verdict, version: str | None, anomalies: list[Anomaly]
) -> list[Event]:
    """A budget, loop or anomaly trip is also its own event, so it can be searched and alerted
    on. Anomalies are recorded whatever ``on_anomaly`` says, including ``alert``."""
    events = [
        Event(
            kind=LIMIT_EVENTS[m.rule_id],
            agent_id=action.agent_id,
            on_behalf_of=action.on_behalf_of,
            task_id=action.task_id,
            tool=ToolRef(id=action.tool_id, version=version),
            outcome=verdict.decision.value,
            data={"rule_id": m.rule_id, "reason": m.reason},
        )
        for m in verdict.matches
        if m.rule_id in LIMIT_EVENTS
    ]
    events += [
        Event(
            kind=EventKind.ANOMALY_DETECTED,
            agent_id=action.agent_id,
            on_behalf_of=action.on_behalf_of,
            task_id=action.task_id,
            tool=ToolRef(id=action.tool_id, version=version),
            outcome=verdict.decision.value,
            data={"rule_id": a.rule_id, "reason": a.reason},
        )
        for a in anomalies
    ]
    return events


def run_check(
    action: Action,
    policy: Policy,
    registry: ToolRegistry,
    project: ProjectConfig | None = None,
    ledger: Ledger | None = None,
    record: bool = True,
    extra: dict[str, Any] | None = None,
    halt_file: Path | None = None,
    now: datetime | None = None,
) -> Verdict:
    """Evaluate ``action`` and, when a ledger is given and ``record`` is true, log the verdict.

    With a ledger, history is read and the decision is appended under one lock, so concurrent
    checks for the same task see each other's decisions. ``extra`` is stored with the event.
    ``halt_file`` is the kill switch (task P2.8); ``now`` exists so tests can fix the day.
    """
    halt = read_halt(halt_file) if halt_file is not None else None
    halted = halt.describe() if halt is not None else None
    if ledger is None:
        return evaluate(action, policy, registry, project, halted=halted)

    tool = next((t for t in registry.tools if t.id == action.tool_id), None)
    version = tool.version if tool else None
    try:
        with ledger.transaction() as tx:
            history = list(tx.records())
            prior = prior_diff_lines(history, action.task_id)
            moment = now or datetime.now(UTC)
            usage = compute_usage(history, action, policy.budgets, moment, policy.anomalies)
            approvals = scan_approvals(history, moment, policy.approvals)
            found = detect_anomalies(policy.anomalies, usage, tool.risk.rank if tool else None)
            verdict = evaluate(
                action,
                policy,
                registry,
                project,
                prior_diff_lines=prior,
                usage=usage,
                halted=halted,
                anomalies=found,
                grants=approvals.grants,
            )
            pending = _pending_request(action, verdict, tool)
            if pending is not None:
                verdict = verdict.model_copy(update={"approval_request": pending.request_id})
            if record:
                tx.append(decision_event(action, verdict, version, extra))
                for event in _limit_events(action, verdict, version, found):
                    tx.append(event)
                if pending is not None and pending.request_id not in approvals.requests:
                    tx.append(_request_event(action, pending, version))
        return verdict
    except (LedgerError, ValueError, OSError) as exc:
        return _fail_closed(policy, action, f"audit ledger unavailable: {exc}")
