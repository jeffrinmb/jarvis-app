"""Summarise what the hook recorded, to review log-only data (tasks P2.6d and P2.10).

The questions this answers: how often would the gate have intervened, on which tools and rules,
which Claude Code tools have no mapping, and are the interventions sensible or false positives.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from typing import Any

MAX_EXAMPLE_PATHS = 3


def summarize(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate ``policy_decision`` records written by the Claude Code hook."""
    decisions: Counter[str] = Counter()
    by_tool: dict[str, Counter[str]] = defaultdict(Counter)
    rules: Counter[tuple[str, str]] = Counter()
    unmapped: Counter[str] = Counter()
    modes: Counter[str] = Counter()
    permission_modes: Counter[str] = Counter()
    shell_classes: Counter[str] = Counter()
    sessions: set[str] = set()
    patterns: dict[tuple[str, str, tuple[str, ...]], dict[str, Any]] = {}
    tainted = trifecta = unhealthy = 0
    first = last = None

    for record in records:
        event = record["event"]
        if event.get("kind") != "policy_decision":
            continue
        data = event.get("data") or {}
        context = data.get("context") or {}
        if context.get("harness") != "claude-code":
            continue

        decision = event.get("decision") or "unknown"
        tool_id = (event.get("tool") or {}).get("id") or "unknown"
        claude_tool = context.get("claude_tool") or "unknown"
        decisions[decision] += 1
        by_tool[tool_id][decision] += 1
        modes[str(context.get("mode"))] += 1
        permission_modes[str(context.get("permission_mode"))] += 1
        if context.get("shell_class"):
            shell_classes[context["shell_class"]] += 1
        if context.get("session_id"):
            sessions.add(context["session_id"])
        tainted += bool(event.get("tainted"))
        trifecta += bool(data.get("trifecta"))
        unhealthy += context.get("state_healthy") is False
        first = first or event.get("ts")
        last = event.get("ts") or last

        restrictive = [m for m in data.get("matches", []) if m.get("effect") != "allow"]
        for match in restrictive:
            rules[(match["rule_id"], match["effect"])] += 1
        if any(m.get("rule_id") == "builtin:unregistered-tool" for m in restrictive):
            unmapped[claude_tool] += 1

        if decision != "allow":
            key = (tool_id, decision, tuple(sorted(m["rule_id"] for m in restrictive)))
            entry = patterns.setdefault(key, {"count": 0, "paths": [], "claude_tool": claude_tool})
            entry["count"] += 1
            for path in data.get("paths", []):
                if path not in entry["paths"] and len(entry["paths"]) < MAX_EXAMPLE_PATHS:
                    entry["paths"].append(path)

    total = sum(decisions.values())
    would_block = total - decisions.get("allow", 0)
    return {
        "total": total,
        "decisions": dict(decisions),
        "would_block": would_block,
        "would_block_rate": (would_block / total) if total else 0.0,
        "sessions": len(sessions),
        "modes": dict(modes),
        "permission_modes": dict(permission_modes),
        "tainted_decisions": tainted,
        "trifecta_decisions": trifecta,
        "unhealthy_session_state": unhealthy,
        "shell_classes": dict(shell_classes),
        "by_tool": {tool: dict(counts) for tool, counts in sorted(by_tool.items())},
        "rules": [
            {"rule_id": rule, "effect": effect, "count": count}
            for (rule, effect), count in rules.most_common()
        ],
        "unmapped_tools": dict(unmapped.most_common()),
        "interventions": [
            {
                "tool": tool,
                "claude_tool": entry["claude_tool"],
                "decision": decision,
                "rules": list(rule_ids),
                "count": entry["count"],
                "example_paths": entry["paths"],
            }
            for (tool, decision, rule_ids), entry in sorted(
                patterns.items(), key=lambda item: -item[1]["count"]
            )
        ],
        "first": first,
        "last": last,
    }


def render_text(summary: dict[str, Any]) -> str:
    total = summary["total"]
    if not total:
        return "No Claude Code decisions recorded yet."
    lines = [
        f"{total} decisions in {summary['sessions']} session(s), {summary['first']} to "
        f"{summary['last']}",
        "decisions: " + ", ".join(f"{k} {v}" for k, v in sorted(summary["decisions"].items())),
        f"the gate would have intervened on {summary['would_block']} "
        f"({summary['would_block_rate']:.0%})",
        "hook mode: " + ", ".join(f"{k} {v}" for k, v in summary["modes"].items()),
        "permission mode: " + ", ".join(f"{k} {v}" for k, v in summary["permission_modes"].items()),
        f"tainted decisions {summary['tainted_decisions']}, "
        f"trifecta {summary['trifecta_decisions']}, "
        f"damaged session state {summary['unhealthy_session_state']}",
        "",
        "by tool (registry id):",
    ]
    for tool, counts in summary["by_tool"].items():
        lines.append(f"  {tool:16} " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    if summary["unmapped_tools"]:
        lines += ["", "Claude Code tools with no mapping (denied as unregistered):"]
        lines += [f"  {name:42} {count}" for name, count in summary["unmapped_tools"].items()]
    if summary["rules"]:
        lines += ["", "rules that fired:"]
        lines += [
            f"  {r['count']:4}  {r['effect']:7} {r['rule_id']}" for r in summary["rules"][:15]
        ]
    if summary["interventions"]:
        lines += ["", "what would have been stopped (review for false positives):"]
        for item in summary["interventions"][:20]:
            paths = f"  e.g. {', '.join(item['example_paths'])}" if item["example_paths"] else ""
            lines.append(
                f"  {item['count']:4}  {item['decision']:7} {item['tool']:14} "
                f"[{', '.join(item['rules'])}]{paths}"
            )
    return "\n".join(lines)
