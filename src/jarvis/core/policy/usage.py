"""What a task and the day have already used, counted from the ledger (task P2.8)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from jarvis.core.schemas import Action, Decision, EventKind
from jarvis.core.schemas.policy import Budgets

from .matching import normalize_path

COUNTED = {Decision.ALLOW.value, Decision.COUNCIL.value, Decision.HUMAN.value}


@dataclass(frozen=True)
class Usage:
    task_calls: int = 0
    day_calls: int = 0
    repeats: int = 0  # earlier identical calls inside the loop window


def fingerprint(tool_id: str, paths: list[str]) -> str:
    """Identifies "the same call" without keeping its content: the tool and normalised paths."""
    normalised = sorted(normalize_path(p)[0] for p in paths)
    return hashlib.sha256("\0".join([tool_id, *normalised]).encode()).hexdigest()[:16]


def _day(ts: Any) -> str | None:
    if not isinstance(ts, str):
        return None
    try:
        moment = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment.astimezone(UTC).date().isoformat() if moment.tzinfo else None


def compute_usage(records, action: Action, budgets: Budgets | None, now: datetime) -> Usage:
    """Count from the ledger. A timestamp that cannot be read is not counted toward the day;
    the per-task and loop counts do not depend on timestamps."""
    if budgets is None:
        return Usage()
    today = now.astimezone(UTC).date().isoformat()
    mine = fingerprint(action.tool_id, action.paths)
    task_calls = day_calls = 0
    recent: list[str | None] = []
    for record in records:
        event = record["event"]
        if event.get("kind") != EventKind.POLICY_DECISION.value:
            continue
        counted = event.get("decision") in COUNTED
        if counted and _day(event.get("ts")) == today:
            day_calls += 1
        if event.get("task_id") != action.task_id:
            continue
        if counted:
            task_calls += 1
        recent.append((event.get("data") or {}).get("fingerprint"))
    window = (budgets.loop_window or 1) - 1  # this call takes one slot
    repeats = sum(1 for f in recent[-window:] if f == mine) if window > 0 else 0
    return Usage(task_calls, day_calls, repeats)
