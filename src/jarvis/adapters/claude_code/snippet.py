"""The ``hooks`` block to paste into a Claude Code settings file, and an environment check.

Nothing here edits a settings file. A person copies the snippet, because changing the hook
configuration is itself a security-relevant action (see ADR 0003, decision 7).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any

from .config import HookMode
from .versions import MIN_ON_FAILURE, Version, format_version, supports_on_failure

DEFAULT_TIMEOUT_SECONDS = 10


def _venv_python() -> str:
    """The project virtualenv's interpreter, as Claude Code expands ``${CLAUDE_PROJECT_DIR}``."""
    if sys.platform == "win32":
        return "${CLAUDE_PROJECT_DIR}/.venv/Scripts/python.exe"
    return "${CLAUDE_PROJECT_DIR}/.venv/bin/python"


def _command(runner: str, mode: HookMode, source_args: tuple[str, ...] = ()) -> dict[str, Any]:
    run = ["hook", "run", "--mode", mode.value, *source_args]
    if runner == "uv":
        return {
            "command": "uv",
            "args": ["run", "--project", "${CLAUDE_PROJECT_DIR}", "agentctl", *run],
        }
    if runner == "venv":
        # Skips `uv` and the console-script launcher; the fastest way to start the hook.
        return {"command": _venv_python(), "args": ["-m", "jarvis.cli", *run]}
    return {"command": "agentctl", "args": run}


def build_settings(
    mode: HookMode = HookMode.LOG_ONLY,
    runner: str = "direct",
    timeout: int | None = None,
    minimal: bool = False,
    policy_source: str | None = None,
    policy_ref: str | None = None,
    policy_dir: str | None = None,
) -> dict[str, Any]:
    """Settings JSON with one hook command registered for each event the adapter handles.

    Exec form (``args``) is used so no shell is involved. Enforce mode adds
    ``"onFailure": "block"`` so a failed hook blocks the call (Claude Code 2.1.295 or later).

    ``minimal`` registers only ``PreToolUse``, ``SessionStart`` and ``ConfigChange``. Exposure
    is applied when a call is requested, so ``PostToolUse`` is not needed for safety; leaving
    it out halves the per-call cost (each hook start is about a second today) at the price of
    not recording whether calls succeeded.

    ``policy_source``, ``policy_ref`` and ``policy_dir`` are written into the command so the
    choice of where policy is read from is not in a file the agent can edit (ADR 0004). Enforce
    mode refuses the agent-writable working tree.
    """
    if mode is HookMode.ENFORCE and policy_source in (None, "worktree"):
        raise ValueError(
            "enforce mode needs --policy-source git or directory: the working tree is "
            "agent-writable (ADR 0004)"
        )
    source_args: list[str] = []
    if policy_source:
        source_args += ["--policy-source", policy_source]
    if policy_ref:
        source_args += ["--policy-ref", policy_ref]
    if policy_dir:
        source_args += ["--policy-dir", policy_dir]
    handler: dict[str, Any] = {
        "type": "command",
        **_command(runner, mode, tuple(source_args)),
        "timeout": timeout or DEFAULT_TIMEOUT_SECONDS,
    }
    if mode is HookMode.ENFORCE:
        handler["onFailure"] = "block"
    entry = {"hooks": [handler]}
    matched = {"matcher": "*", "hooks": [handler]}
    hooks: dict[str, Any] = {"SessionStart": [entry], "PreToolUse": [matched]}
    if not minimal:
        hooks["PostToolUse"] = [matched]
        hooks["PostToolUseFailure"] = [matched]
    hooks["ConfigChange"] = [entry]
    return {"hooks": hooks}


@dataclass(frozen=True)
class Finding:
    ok: bool
    message: str


def check_environment(version: Version | None, mode: HookMode) -> list[Finding]:
    """Findings about whether this Claude Code version suits the chosen mode."""
    findings = [Finding(version is not None, f"Claude Code version: {format_version(version)}")]
    if mode is HookMode.ENFORCE:
        needed = format_version(MIN_ON_FAILURE)
        if supports_on_failure(version):
            findings.append(Finding(True, f"onFailure: block is supported (needs {needed})"))
        else:
            findings.append(
                Finding(
                    False,
                    f"enforce mode needs Claude Code {needed} or later: on this version a "
                    "crashed, timed-out or missing hook lets the action through",
                )
            )
    else:
        findings.append(
            Finding(
                True,
                "log-only mode records decisions and never blocks, so any version works",
            )
        )
    return findings
