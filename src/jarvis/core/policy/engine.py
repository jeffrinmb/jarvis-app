"""Pure policy evaluation: facts in, verdict out. No I/O.

Decision order (ADR 0002): the most restrictive applicable outcome wins, in the order
``deny > human > council > allow``. ``allow`` is only possible when at least one
``auto_approve`` rule matched **and** nothing restrictive applied. If no rule matches, the
policy's ``default`` applies (never ``allow``).

Built-in guardrails apply regardless of what a policy file says, so a permissive or
mistaken policy cannot switch them off:

* unregistered tool, role not allowed, tool not enabled in the environment or project: deny;
* production, suspicious paths, project no-go paths: human;
* the lethal trifecta (private data + untrusted content + outbound communication): human;
* a tainted session using an irreversible or outbound tool: human;
* the tool registry's own approval mode is a floor the policy cannot lower.
"""

from __future__ import annotations

from dataclasses import dataclass

from jarvis.core.schemas import (
    Action,
    DataClass,
    Decision,
    Environment,
    Policy,
    ProjectConfig,
    ToolEntry,
    ToolRegistry,
    TrustLabel,
    Verdict,
)
from jarvis.core.schemas.common import Approval, Effect
from jarvis.core.schemas.policy import Condition, Rule
from jarvis.core.schemas.verdict import MatchCategory, RuleMatch

from .matching import matches_any, normalize_path
from .usage import Anomaly, Usage

SEVERITY: dict[Decision, int] = {
    Decision.ALLOW: 0,
    Decision.COUNCIL: 1,
    Decision.HUMAN: 2,
    Decision.DENY: 3,
}
_BY_SEVERITY = {v: k for k, v in SEVERITY.items()}

PRIVATE_DATA = {DataClass.CONFIDENTIAL, DataClass.RESTRICTED}
IRREVERSIBLE_OR_OUTBOUND = {Effect.WRITE_IRREVERSIBLE, Effect.EXTERNAL_COMMS}

_POLICY_CATEGORIES: list[tuple[MatchCategory, Decision]] = [
    ("never", Decision.DENY),
    ("human_required", Decision.HUMAN),
    ("council_review", Decision.COUNCIL),
    ("auto_approve", Decision.ALLOW),
]


@dataclass(frozen=True)
class _Facts:
    action: Action
    tool: ToolEntry
    paths: list[str]
    tainted: bool
    total_diff: int
    size_known: bool


def combine(matches: list[RuleMatch], default: Decision) -> tuple[Decision, str]:
    """Pick the final decision from all matches."""
    restrictive = [m for m in matches if m.effect != Decision.ALLOW]
    if restrictive:
        top = max(SEVERITY[m.effect] for m in restrictive)
        reasons = [m.reason for m in restrictive if SEVERITY[m.effect] == top]
        return _BY_SEVERITY[top], "; ".join(reasons)
    allows = [m for m in matches if m.effect == Decision.ALLOW]
    if allows:
        return Decision.ALLOW, "allowed by " + ", ".join(m.rule_id for m in allows)
    return default, f"no rule matched; policy default is {default.value}"


def _condition_matches(cond: Condition, category: MatchCategory, facts: _Facts) -> bool:
    action, tool = facts.action, facts.tool
    if cond.tools and not matches_any(cond.tools, action.tool_id):
        return False
    if cond.paths:
        if not facts.paths:
            return False
        hits = [matches_any(cond.paths, p) for p in facts.paths]
        if category == "auto_approve":
            if not all(hits):
                return False
        elif not any(hits):
            return False
    if cond.environments and action.environment not in cond.environments:
        return False
    if cond.risk_at_least is not None and tool.risk.rank < cond.risk_at_least.rank:
        return False
    if cond.risk_at_most is not None and tool.risk.rank > cond.risk_at_most.rank:
        return False
    if cond.max_diff_lines is not None and not (
        facts.size_known and facts.total_diff <= cond.max_diff_lines
    ):
        return False
    if cond.min_diff_lines is not None and (
        facts.size_known and facts.total_diff < cond.min_diff_lines
    ):
        return False
    return not (cond.tainted is not None and facts.tainted != cond.tainted)


def _policy_matches(policy: Policy, facts: _Facts) -> list[RuleMatch]:
    found: list[RuleMatch] = []
    for category, effect in _POLICY_CATEGORIES:
        rules: list[Rule] = getattr(policy, category)
        for rule in rules:
            if _condition_matches(rule.when, category, facts):
                found.append(
                    RuleMatch(
                        rule_id=rule.id,
                        category=category,
                        effect=effect,
                        reason=f"{category} rule '{rule.id}': {rule.description}",
                    )
                )
    return found


def _builtin(rule_id: str, effect: Decision, reason: str) -> RuleMatch:
    return RuleMatch(rule_id=f"builtin:{rule_id}", category="builtin", effect=effect, reason=reason)


def _anomaly_matches(policy: Policy, anomalies: list[Anomaly] | None) -> list[RuleMatch]:
    """``alert`` records the anomaly elsewhere and does not change the decision. It must not
    become an allow match, which would count as an approval."""
    config = policy.anomalies
    if config is None or not anomalies or config.on_anomaly == "alert":
        return []
    effect = Decision.DENY if config.on_anomaly == "deny" else Decision.HUMAN
    return [
        _builtin(a.rule_id.removeprefix("builtin:"), effect, f"anomaly: {a.reason}")
        for a in anomalies
    ]


def _budget_matches(policy: Policy, usage: Usage | None) -> list[RuleMatch]:
    budgets = policy.budgets
    if budgets is None or usage is None:
        return []
    effect = budgets.on_exceed
    found: list[RuleMatch] = []
    # `usage` counts earlier calls, so this call is the next one: limit reached means trip.
    if budgets.max_calls_per_task is not None and usage.task_calls >= budgets.max_calls_per_task:
        found.append(
            _builtin(
                "budget-task-calls",
                effect,
                f"task has used its budget of {budgets.max_calls_per_task} calls",
            )
        )
    if budgets.max_calls_per_day is not None and usage.day_calls >= budgets.max_calls_per_day:
        found.append(
            _builtin(
                "budget-day-calls",
                effect,
                f"today's budget of {budgets.max_calls_per_day} calls is used",
            )
        )
    if budgets.loop_threshold is not None and usage.repeats + 1 >= budgets.loop_threshold:
        found.append(
            _builtin(
                "loop-detected",
                effect,
                f"the same call was made {budgets.loop_threshold} times within "
                f"{budgets.loop_window} calls",
            )
        )
    return found


def evaluate(
    action: Action,
    policy: Policy,
    registry: ToolRegistry,
    project: ProjectConfig | None = None,
    prior_diff_lines: int = 0,
    usage: Usage | None = None,
    halted: str | None = None,
    anomalies: list[Anomaly] | None = None,
) -> Verdict:
    """Decide what should happen to ``action``. Pure and deterministic.

    ``usage`` is what the task and the day have used so far (from the ledger) and ``halted``
    is the kill switch's description when it is on. ``anomalies`` are the detections for this
    call (see ``usage.detect_anomalies``). All three come from the caller, not the agent.
    """
    tainted = TrustLabel.UNTRUSTED in {
        *action.session.trust_labels_seen,
        *action.input_trust,
    }

    if halted is not None:  # nothing else matters while the switch is on
        match = _builtin("kill-switch", Decision.DENY, halted)
        return Verdict(
            decision=Decision.DENY,
            reason=match.reason,
            policy_version=policy.version,
            tainted=tainted,
            matches=[match],
        )

    tool = next((t for t in registry.tools if t.id == action.tool_id), None)
    if tool is None:
        match = _builtin(
            "unregistered-tool", Decision.DENY, f"tool '{action.tool_id}' is not in the registry"
        )
        return Verdict(
            decision=Decision.DENY,
            reason=match.reason,
            policy_version=policy.version,
            tainted=tainted,
            matches=[match],
        )

    # Unknown size is only tolerated for read-only tools, which change nothing.
    # Comparisons below use == (not `is`) so a tool entry built without validation, with plain
    # strings, still triggers every guardrail instead of silently skipping it.
    size_known = action.diff_lines is not None or tool.effect == Effect.READ_ONLY
    total_diff = prior_diff_lines + (action.diff_lines or 0)

    normalized = [normalize_path(p) for p in action.paths]
    paths = [p for p, _ in normalized]
    facts = _Facts(action, tool, paths, tainted, total_diff, size_known)

    private = bool(
        PRIVATE_DATA & {*action.session.data_classes_seen, tool.data_class},
    )
    outbound = tool.effect == Effect.EXTERNAL_COMMS
    trifecta = tainted and private and outbound

    matches: list[RuleMatch] = []

    if action.role not in tool.allowed_roles:
        matches.append(
            _builtin(
                "role-not-allowed",
                Decision.DENY,
                f"role '{action.role}' may not use tool '{tool.id}'",
            )
        )
    if action.environment not in tool.environments:
        matches.append(
            _builtin(
                "environment-not-enabled",
                Decision.DENY,
                f"tool '{tool.id}' is not enabled in '{action.environment.value}'",
            )
        )
    if project is not None:
        if not matches_any(project.tools_enabled, tool.id):
            matches.append(
                _builtin(
                    "tool-not-enabled",
                    Decision.DENY,
                    f"tool '{tool.id}' is not enabled for project '{project.project}'",
                )
            )
        if action.environment != Environment.PROD and (
            action.environment not in project.environments
        ):
            matches.append(
                _builtin(
                    "environment-not-in-project",
                    Decision.DENY,
                    f"environment '{action.environment.value}' is not enabled for the project",
                )
            )
        if any(matches_any(project.no_go_paths, p) for p in paths):
            matches.append(
                _builtin(
                    "no-go-path",
                    Decision.HUMAN,
                    "action touches a no-go path; a human must decide",
                )
            )

    if any(suspicious for _, suspicious in normalized):
        matches.append(
            _builtin(
                "suspicious-path",
                Decision.HUMAN,
                "a path is absolute, empty or escapes the repository",
            )
        )
    if action.environment == Environment.PROD:
        matches.append(_builtin("production", Decision.HUMAN, "production actions are human-gated"))
    if trifecta:
        matches.append(
            _builtin(
                "lethal-trifecta",
                Decision.HUMAN,
                "private data, untrusted content and outbound communication in one session",
            )
        )
    if tainted and tool.effect in IRREVERSIBLE_OR_OUTBOUND:
        matches.append(
            _builtin(
                "tainted-irreversible-or-outbound",
                Decision.HUMAN,
                "a session that read untrusted content is using an irreversible or outbound tool",
            )
        )

    if tool.approval == Approval.HUMAN:
        matches.append(
            RuleMatch(
                rule_id="registry:approval",
                category="registry",
                effect=Decision.HUMAN,
                reason=f"tool '{tool.id}' requires human approval",
            )
        )
    elif tool.approval == Approval.COUNCIL:
        matches.append(
            RuleMatch(
                rule_id="registry:approval",
                category="registry",
                effect=Decision.COUNCIL,
                reason=f"tool '{tool.id}' requires council review",
            )
        )

    matches.extend(_budget_matches(policy, usage))
    matches.extend(_anomaly_matches(policy, anomalies))
    matches.extend(_policy_matches(policy, facts))

    decision, reason = combine(matches, policy.default)
    return Verdict(
        decision=decision,
        reason=reason,
        policy_version=policy.version,
        risk=tool.risk,
        tainted=tainted,
        trifecta=trifecta,
        diff_size_known=size_known,
        total_diff_lines=total_diff,
        matches=matches,
    )
