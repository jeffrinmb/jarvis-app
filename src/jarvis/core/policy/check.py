"""Run the policy gate end to end: read history, decide, and record the decision.

The decision is only returned to the caller after it has been written to the audit ledger.
If the ledger cannot be read or written, the answer is ``deny``: an action that cannot be
audited is not allowed.
"""

from __future__ import annotations

from typing import Any

from jarvis.core.ledger import Ledger, LedgerError
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
from jarvis.core.schemas.event import ToolRef
from jarvis.core.schemas.verdict import RuleMatch

from .engine import evaluate

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
            "matches": [
                {"rule_id": m.rule_id, "category": m.category, "effect": m.effect.value}
                for m in verdict.matches
            ],
            "context": dict(extra or {}),
        },
    )


def run_check(
    action: Action,
    policy: Policy,
    registry: ToolRegistry,
    project: ProjectConfig | None = None,
    ledger: Ledger | None = None,
    record: bool = True,
    extra: dict[str, Any] | None = None,
) -> Verdict:
    """Evaluate ``action`` and, when a ledger is given and ``record`` is true, log the verdict.

    With a ledger, history is read and the decision is appended under one lock, so concurrent
    checks for the same task see each other's decisions. ``extra`` is stored with the event.
    """
    if ledger is None:
        return evaluate(action, policy, registry, project)

    tool = next((t for t in registry.tools if t.id == action.tool_id), None)
    version = tool.version if tool else None
    try:
        with ledger.transaction() as tx:
            prior = prior_diff_lines(tx.records(), action.task_id)
            verdict = evaluate(action, policy, registry, project, prior_diff_lines=prior)
            if record:
                tx.append(decision_event(action, verdict, version, extra))
        return verdict
    except (LedgerError, ValueError, OSError) as exc:
        return _fail_closed(policy, action, f"audit ledger unavailable: {exc}")
