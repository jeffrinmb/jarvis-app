"""Small builders shared by the policy tests."""

from __future__ import annotations

from pydantic import BaseModel

from jarvis.core.schemas import Action, Policy, ToolEntry, ToolRegistry

_TOOL_DEFAULTS = {
    "source": "native",
    "version": "1",
    "risk": "low",
    "effect": "write-reversible",
    "data_class": "internal",
    "allowed_roles": ["builder"],
    "environments": ["dev"],
    "approval": "auto",
    "owner": "platform-team",
}


def tool(tool_id: str, **over) -> ToolEntry:
    return ToolEntry(id=tool_id, **{**_TOOL_DEFAULTS, **over})


def variant[M: BaseModel](model: M, **over) -> M:
    """Copy a model with changes, re-validating (``model_copy`` would skip enum coercion)."""
    return type(model).model_validate({**model.model_dump(mode="json"), **over})


def default_registry() -> ToolRegistry:
    return ToolRegistry(
        tools=[
            tool(
                "git.read",
                risk="read",
                effect="read-only",
                allowed_roles=["builder", "reviewer"],
                environments=["dev", "staging"],
            ),
            tool("fs.write"),
            tool(
                "web.post",
                risk="high",
                effect="external-comms",
                data_class="confidential",
                approval="council",
            ),
            tool("mail.send", risk="medium", effect="external-comms", approval="council"),
            tool(
                "danger.irreversible", risk="high", effect="write-irreversible", approval="council"
            ),
            tool(
                "deploy.production",
                risk="critical",
                effect="write-irreversible",
                data_class="restricted",
                allowed_roles=["release-engineer"],
                environments=["prod"],
                approval="human",
            ),
        ]
    )


def action(**over) -> Action:
    base = {
        "agent_id": "builder-1",
        "on_behalf_of": "alice",
        "role": "builder",
        "task_id": "T-1",
        "tool_id": "fs.write",
        "environment": "dev",
        "paths": ["docs/a.md"],
        "diff_lines": 5,
    }
    return Action(**{**base, **over})


def rule(rule_id: str, **when) -> dict:
    return {"id": rule_id, "description": f"test rule {rule_id}", "when": when}


def policy(**over) -> Policy:
    base = {
        "version": "test-1",
        "auto_approve": [rule("docs", paths=["docs/**", "tests/**"], max_diff_lines=200)],
    }
    return Policy(**{**base, **over})
