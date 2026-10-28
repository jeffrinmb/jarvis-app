"""What a task and the day have already used, counted from the ledger (tasks P2.8, P2.8a).

Everything here reads earlier decisions only. The numbers describe the history before the
current call, and the caller decides what a number means (see ``engine.evaluate``).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from jarvis.core.schemas import Action, Decision, EventKind
from jarvis.core.schemas.policy import Anomalies, Budgets

from .matching import normalize_path

COUNTED = {Decision.ALLOW.value, Decision.COUNCIL.value, Decision.HUMAN.value}


@dataclass(frozen=True)
class Usage:
    task_calls: int = 0
    day_calls: int = 0
    repeats: int = 0  # earlier identical calls inside the loop window
    recent_denials: int = 0  # denials among the task's last `denial_window` decisions
    history: int = 0  # decisions in the ledger that were not denied, all tasks
    tool_seen: bool = True  # this tool was used (not denied) before; True when not measured
    burst: int = 0  # the task's calls within the last `burst_seconds`


@dataclass(frozen=True)
class Anomaly:
    rule_id: str  # always starts with "builtin:anomaly-"
    reason: str


def fingerprint(tool_id: str, paths: list[str]) -> str:
    """Identifies "the same call" without keeping its content: the tool and normalised paths."""
    normalised = sorted(normalize_path(p)[0] for p in paths)
    return hashlib.sha256("\0".join([tool_id, *normalised]).encode()).hexdigest()[:16]


def _moment(ts: Any) -> datetime | None:
    if not isinstance(ts, str):
        return None
    try:
        moment = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment.astimezone(UTC) if moment.tzinfo else None


def compute_usage(
    records,
    action: Action,
    budgets: Budgets | None,
    now: datetime,
    anomalies: Anomalies | None = None,
) -> Usage:
    """Count from the ledger. A timestamp that cannot be read is not counted toward the day or
    a burst; the per-task, loop, denial and tool counts do not depend on timestamps."""
    if budgets is None and anomalies is None:
        return Usage()
    now = now.astimezone(UTC)
    today = now.date().isoformat()
    mine = fingerprint(action.tool_id, action.paths)
    task_calls = day_calls = history = 0
    tool_seen = False
    recent: list[tuple[str | None, str | None, datetime | None]] = []
    for record in records:
        event = record["event"]
        if event.get("kind") != EventKind.POLICY_DECISION.value:
            continue
        decision = event.get("decision")
        counted = decision in COUNTED
        moment = _moment(event.get("ts"))
        if counted:
            history += 1
            if moment is not None and moment.date().isoformat() == today:
                day_calls += 1
            if (event.get("tool") or {}).get("id") == action.tool_id:
                tool_seen = True
        if event.get("task_id") != action.task_id:
            continue
        if counted:
            task_calls += 1
        recent.append(((event.get("data") or {}).get("fingerprint"), decision, moment))

    repeats = 0
    if budgets is not None and budgets.loop_window:
        window = budgets.loop_window - 1  # this call takes one slot
        repeats = sum(1 for f, _, _ in recent[-window:] if f == mine) if window > 0 else 0

    denials = burst = 0
    if anomalies is not None:
        if anomalies.denial_window:
            tail = recent[-anomalies.denial_window :]
            denials = sum(1 for _, d, _ in tail if d == Decision.DENY.value)
        if anomalies.burst_seconds:
            since = now - timedelta(seconds=anomalies.burst_seconds)
            burst = sum(1 for _, d, m in recent if d in COUNTED and m is not None and m >= since)
    return Usage(task_calls, day_calls, repeats, denials, history, tool_seen, burst)


def detect_anomalies(
    anomalies: Anomalies | None, usage: Usage, risk_rank: int | None
) -> list[Anomaly]:
    """Which anomaly rules this call trips. ``risk_rank`` is the tool's rank from the registry."""
    if anomalies is None:
        return []
    found: list[Anomaly] = []
    if anomalies.max_denials is not None and usage.recent_denials >= anomalies.max_denials:
        found.append(
            Anomaly(
                "builtin:anomaly-denials",
                f"{usage.recent_denials} of this task's last {anomalies.denial_window} "
                "decisions were denials",
            )
        )
    if (
        anomalies.new_tool
        and not usage.tool_seen
        and usage.history >= anomalies.new_tool_min_history
        and risk_rank is not None
        and risk_rank >= anomalies.new_tool_risk_at_least.rank
    ):
        found.append(
            Anomaly(
                "builtin:anomaly-new-tool",
                f"this tool has not been used before in {usage.history} earlier decisions",
            )
        )
    if anomalies.burst_calls is not None and usage.burst >= anomalies.burst_calls:
        found.append(
            Anomaly(
                "builtin:anomaly-burst",
                f"{usage.burst} calls in the last {anomalies.burst_seconds} seconds",
            )
        )
    return found
