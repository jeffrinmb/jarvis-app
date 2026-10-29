"""What may be approved, and how many people it takes (task P2.7, ADR 0009).

An approval can turn a ``human`` or ``council`` verdict into ``allow``. It must never turn a
*refusal* into one, and some human gates exist precisely because a stored approval is the wrong
tool for them. This module is the one place that says which is which, so the engine, the request
builder and the tests all agree.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from jarvis.core.schemas import Decision, ToolEntry
from jarvis.core.schemas.common import Effect, RiskClass
from jarvis.core.schemas.verdict import RuleMatch

# A shell command can do anything and its text is never written to the ledger, so a card could
# not show a person what they would be approving. It stays with the terminal prompt, where the
# command is on screen.
OPAQUE_TOOLS = frozenset({"shell.run"})

# Human gates that a stored approval must not lift. Each is a condition of the *session* or the
# *world* that can change after a person said yes: untrusted content arriving, production, a
# path that escapes the repository, or private data meeting an outbound channel.
NOT_APPROVABLE = frozenset(
    {
        "builtin:lethal-trifecta",
        "builtin:production",
        "builtin:suspicious-path",
        "builtin:tainted-irreversible-or-outbound",
        "builtin:kill-switch",
    }
)

# Pauses that exist to get a person to look, so a person's approval is exactly the way out.
APPROVABLE_BUILTINS = frozenset(
    {
        "builtin:no-go-path",
        "builtin:budget-task-calls",
        "builtin:budget-day-calls",
        "builtin:loop-detected",
        "builtin:anomaly-denials",
        "builtin:anomaly-new-tool",
        "builtin:anomaly-burst",
    }
)
APPROVABLE_CATEGORIES = frozenset({"human_required", "council_review", "registry"})

# No-go zones need two different people.
FOUR_EYES = frozenset({"builtin:no-go-path"})


@dataclass(frozen=True)
class Profile:
    approvable: bool
    four_eyes: bool
    exact_only: bool  # only a single-use approval of the exact call may cover it


def restrictive(matches: list[RuleMatch]) -> list[RuleMatch]:
    return [m for m in matches if m.effect in (Decision.HUMAN, Decision.COUNCIL)]


def profile_for(matches: list[RuleMatch], tool: ToolEntry) -> Profile:
    """Whether a stored approval may lift this verdict, and on what terms.

    Fail closed: a deny, an unknown rule, or an opaque tool makes it not approvable.
    """
    if any(m.effect == Decision.DENY for m in matches) or tool.id in OPAQUE_TOOLS:
        return Profile(False, False, False)
    flagged = restrictive(matches)
    approvable = all(
        m.rule_id not in NOT_APPROVABLE
        and (m.category in APPROVABLE_CATEGORIES or m.rule_id in APPROVABLE_BUILTINS)
        for m in flagged
    )
    four_eyes = any(m.rule_id in FOUR_EYES for m in flagged)
    # == so a tool built without validation (plain strings) still counts as high risk.
    exact_only = tool.risk.rank >= RiskClass.HIGH.rank or tool.effect in (
        Effect.WRITE_IRREVERSIBLE,
        Effect.EXTERNAL_COMMS,
    )
    return Profile(approvable, four_eyes, exact_only)


def request_id(task_id: str | None, fingerprint: str, matches: list[RuleMatch]) -> str:
    """Stable for the same task, call and reasons, so a retry reuses its card."""
    rules = sorted(m.rule_id for m in restrictive(matches))
    digest = hashlib.sha256("\0".join([task_id or "", fingerprint, *rules]).encode()).hexdigest()
    return f"req-{digest[:12]}"


def is_agent_name(approver: str, agent_id: str) -> bool:
    """An agent must never approve: refuse the agent's own id and anything in its family
    (``claude-code`` and ``claude-code:Explore:abc`` share a prefix)."""
    return approver.split(":")[0] == agent_id.split(":")[0]
