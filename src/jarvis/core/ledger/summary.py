"""What the policy gate needs from the ledger's history, folded into a small summary (task P2.4a,
ADR 0014).

The gate counts usage, diff totals and approvals from earlier records. Folding every record on
every call is what made a long ledger slow. A *summary* holds the result of that fold up to a
point, so a call needs only the records after it. The summary of everything before a rotation is
written into the ``ledger_rotated`` record that starts the next segment, so it is covered by the
hash chain and ``verify`` recomputes it.

One implementation does the folding for all three uses (the gate, rotation and verification), and
the gate's answers are tested to be identical to counting the full history.

What is kept, and what is not exact:

* per task, forever: the number of counted decisions, the total diff lines, and the number of
  decisions. These are small;
* per task, only while it is recent (``RECENT_DAYS``) and only the last ``RECENT_KEEP``: the
  decisions themselves (fingerprint, verdict, time), which the loop breaker, the denial window
  and the burst rule read. For a task whose list is incomplete, ``exact_for`` says whether the
  policy's windows still fit; when they do not the gate reads the whole ledger instead, as it
  always did. So a summary never changes a decision, it only decides how much to read;
* ledger-wide: the number of counted decisions, the tools used, calls per day, every approval
  event verbatim (they are rare) and every approval grant id used.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

SUMMARY_VERSION = 1
RECENT_KEEP = 50
RECENT_DAYS = 2
COUNTED = frozenset({"allow", "council", "human"})

_APPROVAL_KINDS = frozenset({"approval_requested", "approval_granted", "approval_denied"})


def parse_moment(ts: Any) -> datetime | None:
    """A timezone-aware time in UTC, or ``None`` if ``ts`` cannot be read."""
    if not isinstance(ts, str):
        return None
    try:
        moment = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment.astimezone(UTC) if moment.tzinfo else None


@dataclass
class TaskStats:
    calls: int = 0  # decisions that were not denied
    diff_lines: int = 0  # their summed diff lines
    n: int = 0  # all decisions
    last_ts: str | None = None
    # [fingerprint, decision, ts] for the task's decisions, oldest first
    recent: list[list[Any]] = field(default_factory=list)


class LedgerSummary:
    """The folded history. ``keep`` caps each task's recent list (``None``: unlimited, which is
    what an in-memory fold for a single decision uses)."""

    def __init__(self, keep: int | None = RECENT_KEEP) -> None:
        self.keep = keep
        self.tasks: dict[str, TaskStats] = {}
        self.untasked = TaskStats()
        self.history = 0
        self.tools_seen: set[str] = set()
        self.day_calls: dict[str, int] = {}
        self.approval_events: list[dict[str, Any]] = []
        self.used_grant_ids: set[str] = set()

    def stats(self, task_id: str | None) -> TaskStats | None:
        return self.untasked if task_id is None else self.tasks.get(task_id)

    # ---------- folding ----------

    def fold(self, records: Any) -> LedgerSummary:
        for record in records:
            self._add(record["event"])
        return self

    def _add(self, event: dict[str, Any]) -> None:
        kind = event.get("kind")
        if kind in _APPROVAL_KINDS:
            self.approval_events.append(event)
            return
        if kind != "policy_decision":
            return
        data = event.get("data") or {}
        decision = event.get("decision")
        counted = decision in COUNTED
        task_id = event.get("task_id")
        stats = self.untasked if task_id is None else self.tasks.setdefault(task_id, TaskStats())
        ts = event.get("ts")
        stats.n += 1
        stats.last_ts = ts if isinstance(ts, str) else stats.last_ts
        stats.recent.append([data.get("fingerprint"), decision, ts])
        if self.keep is not None and len(stats.recent) > self.keep:
            del stats.recent[: len(stats.recent) - self.keep]
        if counted:
            stats.calls += 1
            lines = data.get("diff_lines", 0)
            if isinstance(lines, int) and lines > 0:
                stats.diff_lines += lines
            self.history += 1
            moment = parse_moment(ts)
            if moment is not None:
                day = moment.date().isoformat()
                self.day_calls[day] = self.day_calls.get(day, 0) + 1
            tool_id = (event.get("tool") or {}).get("id")
            if isinstance(tool_id, str):
                self.tools_seen.add(tool_id)
        if decision == "allow":
            ids = data.get("approval_grant_ids")
            if isinstance(ids, list):
                self.used_grant_ids.update(i for i in ids if isinstance(i, str))

    # ---------- exactness ----------

    def exact_for(
        self,
        task_id: str | None,
        loop_window: int | None,
        denial_window: int | None,
        burst_seconds: int | None,
        now: datetime,
    ) -> bool:
        """Whether counting from this summary plus the later records gives the same answer as
        counting the whole history, for this task and these windows."""
        stats = self.stats(task_id)
        if stats is None or stats.n == len(stats.recent):
            return True  # nothing is missing from the task's recent list
        held = len(stats.recent)
        if loop_window and held < loop_window - 1:
            return False
        if denial_window and held < denial_window:
            return False
        if burst_seconds:
            oldest = parse_moment(stats.recent[0][2]) if stats.recent else None
            if oldest is None or oldest >= now.astimezone(UTC) - timedelta(seconds=burst_seconds):
                return False
        return True

    # ---------- storage ----------

    def to_data(self, now: datetime) -> dict[str, Any]:
        """The JSON-ready form, with recent lists dropped for tasks idle longer than
        ``RECENT_DAYS`` and trimmed to ``RECENT_KEEP``."""
        horizon = now.astimezone(UTC) - timedelta(days=RECENT_DAYS)

        def pack(stats: TaskStats) -> dict[str, Any]:
            last = parse_moment(stats.last_ts)
            fresh = last is not None and last >= horizon
            recent = stats.recent[-RECENT_KEEP:] if fresh else []
            return {
                "calls": stats.calls,
                "diff_lines": stats.diff_lines,
                "n": stats.n,
                "last_ts": stats.last_ts,
                "recent": recent,
            }

        return {
            "v": SUMMARY_VERSION,
            "recent_keep": RECENT_KEEP,
            "history": self.history,
            "tasks": {k: pack(v) for k, v in sorted(self.tasks.items())},
            "untasked": pack(self.untasked),
            "tools_seen": sorted(self.tools_seen),
            "day_calls": dict(sorted(self.day_calls.items())),
            "approval_events": self.approval_events,
            "used_grant_ids": sorted(self.used_grant_ids),
        }

    @classmethod
    def from_data(cls, data: Any, keep: int | None = RECENT_KEEP) -> LedgerSummary | None:
        """Rebuild from ``to_data`` output; ``None`` if it is not a summary this code can read
        (the caller then reads the whole ledger instead)."""
        try:
            if not isinstance(data, dict) or data.get("v") != SUMMARY_VERSION:
                return None
            summary = cls(keep)
            summary.history = _int(data["history"])
            summary.untasked = _stats(data["untasked"])
            summary.tasks = {str(k): _stats(v) for k, v in dict(data["tasks"]).items()}
            summary.tools_seen = {str(t) for t in list(data["tools_seen"])}
            summary.day_calls = {str(k): _int(v) for k, v in dict(data["day_calls"]).items()}
            events = list(data["approval_events"])
            if not all(isinstance(e, dict) for e in events):
                return None
            summary.approval_events = events
            summary.used_grant_ids = {str(i) for i in list(data["used_grant_ids"])}
            return summary
        except (KeyError, TypeError, ValueError):
            return None


def _int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("not a count")
    return value


def _stats(raw: Any) -> TaskStats:
    recent = []
    for item in list(raw["recent"]):
        fingerprint, decision, ts = item
        recent.append([fingerprint, decision, ts])
    last = raw["last_ts"]
    if last is not None and not isinstance(last, str):
        raise ValueError("bad time")
    return TaskStats(_int(raw["calls"]), _int(raw["diff_lines"]), _int(raw["n"]), last, recent)
