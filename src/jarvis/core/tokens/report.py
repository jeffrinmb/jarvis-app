"""Summarise the token ledger: calls, tokens, cost and cache hits by model, task, agent, stage
or day (task P2.2).

Only ``model_call`` rows with usage are read. A row whose cost is unknown is counted and shown
separately, so a total never quietly treats an unpriced model as free.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from jarvis.core.schemas import EventKind

GROUPS = ("model", "task", "agent", "stage", "day")
NONE_LABEL = "(none)"


def _day(ts: Any) -> str:
    if isinstance(ts, str):
        try:
            moment = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return NONE_LABEL
        if moment.tzinfo:
            return moment.astimezone(UTC).date().isoformat()
    return NONE_LABEL


def _key(event: dict[str, Any], group: str) -> str:
    if group == "model":
        value = (event.get("model") or {}).get("name")
    elif group == "task":
        value = event.get("task_id")
    elif group == "agent":
        value = event.get("agent_id")
    elif group == "stage":
        value = event.get("stage")
    else:
        return _day(event.get("ts"))
    return value or NONE_LABEL


def _blank() -> dict[str, Any]:
    return {
        "calls": 0,
        "tokens_in": 0,
        "tokens_out": 0,
        "tokens_cached": 0,
        "cost_usd": 0.0,
        "calls_without_cost": 0,
    }


def _finish(row: dict[str, Any]) -> dict[str, Any]:
    row["cost_usd"] = round(row["cost_usd"], 6)
    context = row["tokens_in"] + row["tokens_cached"]
    # share of input tokens that came from a cache
    row["cache_hit_rate"] = round(row["tokens_cached"] / context, 4) if context else None
    return row


def summarize_tokens(records: Iterable[dict[str, Any]], group_by: str = "model") -> dict[str, Any]:
    if group_by not in GROUPS:
        raise ValueError(f"group_by must be one of {', '.join(GROUPS)}")
    total = _blank()
    groups: dict[str, dict[str, Any]] = {}
    for record in records:
        event = record["event"]
        usage = event.get("usage")
        if event.get("kind") != EventKind.MODEL_CALL.value or not isinstance(usage, dict):
            continue
        unpriced = (event.get("data") or {}).get("cost_source") == "unknown"
        for row in (total, groups.setdefault(_key(event, group_by), _blank())):
            row["calls"] += 1
            row["tokens_in"] += int(usage.get("tokens_in") or 0)
            row["tokens_out"] += int(usage.get("tokens_out") or 0)
            row["tokens_cached"] += int(usage.get("tokens_cached") or 0)
            row["cost_usd"] += float(usage.get("cost_usd") or 0.0)
            row["calls_without_cost"] += 1 if unpriced else 0
    rows = [{"key": key, **_finish(row)} for key, row in groups.items()]
    rows.sort(key=lambda r: (-r["cost_usd"], -r["tokens_in"] - r["tokens_out"], r["key"]))
    return {"group_by": group_by, "total": _finish(total), "rows": rows}


def render_tokens(summary: dict[str, Any]) -> str:
    total = summary["total"]
    if not total["calls"]:
        return "No model calls recorded."
    lines = [
        f"{total['calls']} model calls: {total['tokens_in']} in, {total['tokens_cached']} cached, "
        f"{total['tokens_out']} out, ${total['cost_usd']:.4f}",
    ]
    if total["cache_hit_rate"] is not None:
        lines[0] += f", cache hit rate {total['cache_hit_rate']:.0%}"
    if total["calls_without_cost"]:
        lines.append(
            f"WARNING: {total['calls_without_cost']} calls have no cost (unknown model price); "
            "the total is a lower bound."
        )
    lines.append(f"By {summary['group_by']}:")
    for row in summary["rows"]:
        note = f"  ({row['calls_without_cost']} unpriced)" if row["calls_without_cost"] else ""
        lines.append(
            f"  {row['key']}: {row['calls']} calls, {row['tokens_in']} in, "
            f"{row['tokens_cached']} cached, {row['tokens_out']} out, ${row['cost_usd']:.4f}{note}"
        )
    return "\n".join(lines)
