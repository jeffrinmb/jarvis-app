"""Configuration for the Claude Code adapter (``.agents/claude-code.yaml``).

Everything here is configuration, not agent input: the role, the accountable human and the
trust rules come from this file and the environment, never from what the agent says.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field

from jarvis.core.schemas import TrustLabel
from jarvis.core.schemas.common import SCHEMA_VERSION, StrictModel

DEFAULT_CONFIG = ".agents/claude-code.yaml"


class HookMode(StrEnum):
    LOG_ONLY = "log-only"
    ENFORCE = "enforce"


class HookConfig(StrictModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    mode: HookMode = HookMode.LOG_ONLY

    # Who is acting. Replaced by authenticated identity in P2.9a.
    role: str = Field(default="builder", min_length=1)
    on_behalf_of: str | None = Field(
        default=None,
        description="Accountable human; defaults to $JARVIS_ON_BEHALF_OF, then the OS user",
    )
    agent_name: str = Field(default="claude-code", min_length=1)
    environment: Literal["dev", "staging"] = Field(
        default="dev", description="Agents never act in prod; production is a human gate"
    )

    # Where the framework's files live, relative to the project directory.
    policy_path: str = ".agents/policy.yaml"
    registry_path: str = ".agents/tool-registry.yaml"
    project_path: str | None = None
    ledger_path: str = ".agents/ledger/events.jsonl"
    prices_path: str | None = Field(
        default=None,
        description="Price table (examples/prices.yaml) used to cost model calls captured from "
        "the transcript. Read through the policy source. Unset means costs are unknown.",
    )

    # Claude Code tool name -> registry tool id. Merged over the built-in table in mapping.py.
    tool_map: dict[str, str] = Field(default_factory=dict)

    # Trust rules for what a tool brings into the session.
    untrusted_paths: list[str] = Field(
        default_factory=lambda: [
            "**/node_modules/**",
            "**/vendor/**",
            "**/third_party/**",
            "**/.venv/**",
            "**/downloads/**",
        ]
    )
    sensitive_paths: list[str] = Field(
        default_factory=lambda: [
            "**/.env",
            "**/.env.*",
            "**/secrets/**",
            "**/*.pem",
            "**/*.key",
            "**/id_rsa*",
        ],
        description="Reading these adds restricted data to the session",
    )
    trust_overrides: dict[str, TrustLabel] = Field(
        default_factory=dict,
        description="Registry tool id -> trust of its output (for example a vetted MCP tool)",
    )
    network_markers: list[str] = Field(
        default_factory=lambda: [
            "curl",
            "wget",
            "invoke-webrequest",
            "invoke-restmethod",
            "iwr ",
            "irm ",
            "http://",
            "https://",
            "git clone",
            "git fetch",
            "git pull",
            "pip install",
            "pip download",
            "uv add",
            "uv pip",
            "npm ",
            "pnpm ",
            "yarn ",
            "npx ",
            "docker pull",
            "ssh ",
            "scp ",
        ],
        description="A shell command containing one of these may bring untrusted content in",
    )

    # Shell commands that are safe to treat as read-only or test runs (exact token prefixes).
    shell_read_prefixes: list[list[str]] = Field(
        default_factory=lambda: [
            ["git", "status"],
            ["git", "diff"],
            ["git", "log"],
            ["git", "show"],
            ["git", "rev-parse"],
            ["git", "ls-files"],
            ["ls"],
            ["pwd"],
        ]
    )
    shell_test_prefixes: list[list[str]] = Field(
        default_factory=lambda: [
            ["uv", "run", "pytest"],
            ["uv", "run", "ruff", "check"],
            ["uv", "run", "ruff", "format", "--check"],
        ]
    )

    # Behaviour when the gate says council or human in enforce mode.
    non_interactive: bool = Field(
        default=False,
        description="True for unattended (-p) runs: ask cannot be answered, so deny instead",
    )
    block_config_changes: bool = Field(
        default=True, description="Block mid-session edits to Claude Code settings (enforce mode)"
    )
    lock_timeout_seconds: float = Field(default=3.0, gt=0)


def parse_config(text: str | None) -> HookConfig:
    """Parse adapter config text; ``None`` means no config file, so the safe defaults apply."""
    if text is None:
        return HookConfig()
    return HookConfig.model_validate(yaml.safe_load(text) or {})


def load_config(path: str | Path | None) -> HookConfig:
    """Load the config file if it exists; otherwise return the safe defaults."""
    if path is None or not Path(path).exists():
        return HookConfig()
    return parse_config(Path(path).read_text(encoding="utf-8"))
