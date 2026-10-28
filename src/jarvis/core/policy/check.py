"""Run the policy gate end to end: read history, decide, and record the decision.

The decision is only returned to the caller after it has been written to the audit ledger.
If the ledger cannot be read or written, the answer is ``deny``: an action that cannot be
audited is not allowed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jarvis.core.killswitch import read_halt
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
            "fingerprint": fingerprint(action.tool_id, action.paths),
            "matches": [
                {"rule_id": m.rule_id, "category": m.category, "effect": m.effect.value}
                for m in verdict.matches
            ],
            "context": dict(extra or {}),
        },
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
            usage = compute_usage(
                history, action, policy.budgets, now or datetime.now(UTC), policy.anomalies
            )
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
            )
            if record:
                tx.append(decision_event(action, verdict, version, extra))
                for event in _limit_events(action, verdict, version, found):
                    tx.append(event)
        return verdict
    except (LedgerError, ValueError, OSError) as exc:
        return _fail_closed(policy, action, f"audit ledger unavailable: {exc}")
