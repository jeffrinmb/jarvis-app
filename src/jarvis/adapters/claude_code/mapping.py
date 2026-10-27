"""Turn a Claude Code tool call into a policy-gate ``Action``.

The action is built from the **real tool call** (its name and arguments), never from the
agent's own description of it. Fields such as ``description`` in ``tool_input`` are ignored.
Anything the adapter cannot determine is left unknown, which the gate treats as the risky
case (an unregistered tool is denied, an unknown diff size never satisfies a size limit, and
paths outside the project are sent to a human).
"""

from __future__ import annotations

import getpass
import hashlib
import os
import re
import shlex
from dataclasses import dataclass, field
from typing import Any

from jarvis.core.policy import matches_any, normalize_path
from jarvis.core.schemas import Action, DataClass, Environment, TrustLabel
from jarvis.core.schemas.action import SessionState

from .config import HookConfig
from .payload import HookInput

BUILTIN_TOOL_MAP: dict[str, str] = {
    "Read": "fs.read",
    "Glob": "fs.read",
    "Grep": "fs.read",
    "Write": "fs.write",
    "Edit": "fs.write",
    "MultiEdit": "fs.write",
    "NotebookEdit": "fs.write",
    "Bash": "shell.run",
    "PowerShell": "shell.run",
    "WebFetch": "web.fetch",
    "WebSearch": "web.fetch",
    "Agent": "agent.spawn",
    "Task": "agent.spawn",
    "AskUserQuestion": "claude.internal",
    "ExitPlanMode": "claude.internal",
    "EnterPlanMode": "claude.internal",
    "TodoWrite": "claude.internal",
}

SHELL_TOOLS = {"Bash", "PowerShell"}
READ_TOOLS = {"Read", "Glob", "Grep"}
FILE_PATH_TOOLS = {"Read", "Write", "Edit", "MultiEdit", "NotebookEdit"}

_SHELL_METACHARACTERS = set(";&|<>`$()\n\r\\")
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_./:=@^~+,-]+$")
_BANNED_ARGUMENT_PREFIXES = (
    "--output",
    "--ext-diff",
    "--textconv",
    "--exec",
    "--git-dir",
    "--work-tree",
    "--config",
    "-c",
)


@dataclass(frozen=True)
class Exposure:
    """What a tool call brings into the session once it runs."""

    trust: list[TrustLabel] = field(default_factory=list)
    data: list[DataClass] = field(default_factory=list)


@dataclass(frozen=True)
class ActionPlan:
    action: Action
    claude_tool: str
    shell_class: str | None
    exposure: Exposure


def safe_session_id(session_id: str) -> str:
    """A file-name-safe form of a session id. Unsafe ids (path separators, dots, long) are
    hashed, so a crafted id can never point outside the state directory."""
    if re.fullmatch(r"[A-Za-z0-9_-]{1,100}", session_id):
        return session_id
    return "h-" + hashlib.sha256(session_id.encode("utf-8", "replace")).hexdigest()[:32]


def registry_tool_id(claude_tool: str, cfg: HookConfig) -> str:
    """Registry id for a Claude Code tool name. Unknown tools get an id that is not in the
    registry, so the gate denies them. MCP tools keep their full name and must be registered."""
    if claude_tool in cfg.tool_map:
        return cfg.tool_map[claude_tool]
    if claude_tool in BUILTIN_TOOL_MAP:
        return BUILTIN_TOOL_MAP[claude_tool]
    return claude_tool if claude_tool.startswith("mcp__") else f"claude.{claude_tool}"


def classify_shell(command: str, cfg: HookConfig) -> str:
    """``read``, ``test`` or ``run``. Only a single simple command that matches an explicit
    allowed prefix, with plain arguments, is ever treated as read or test. Anything with shell
    metacharacters (chaining, pipes, redirects, substitution) is ``run``."""
    if not command or any(c in _SHELL_METACHARACTERS for c in command):
        return "run"
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return "run"
    if not tokens:
        return "run"

    def matches(prefixes: list[list[str]]) -> bool:
        for prefix in prefixes:
            if tokens[: len(prefix)] != prefix:
                continue
            rest = tokens[len(prefix) :]
            if all(
                _SAFE_TOKEN.match(t) and not t.startswith(_BANNED_ARGUMENT_PREFIXES) for t in rest
            ):
                return True
        return False

    if matches(cfg.shell_read_prefixes):
        return "read"
    if matches(cfg.shell_test_prefixes):
        return "test"
    return "run"


def project_relative(path: str, project_dir: str) -> str:
    """Project-relative POSIX path when ``path`` is inside the project, else the original path.

    Symlinks are resolved first, so a link that points outside the project is treated as
    outside. A path outside the project stays absolute, which the gate sends to a human.
    """
    try:
        root = os.path.normcase(os.path.realpath(project_dir))
        target = os.path.normcase(os.path.realpath(path))
        if os.path.commonpath([root, target]) == root:
            rel = os.path.relpath(target, root)
            return "." if rel == "." else rel.replace(os.sep, "/")
    except (ValueError, OSError):
        pass
    return path


def _lines(text: Any) -> int:
    if not isinstance(text, str) or not text:
        return 0
    return text.count("\n") + 1


def diff_lines_for(claude_tool: str, tool_input: dict[str, Any]) -> int | None:
    """Lines an edit touches, as an upper bound. ``None`` when it cannot be known."""
    if claude_tool == "Write":
        return _lines(tool_input.get("content"))
    if claude_tool == "Edit":
        return _lines(tool_input.get("old_string")) + _lines(tool_input.get("new_string"))
    if claude_tool == "MultiEdit":
        edits = tool_input.get("edits")
        if not isinstance(edits, list):
            return None
        total = 0
        for edit in edits:
            if not isinstance(edit, dict):
                return None
            total += _lines(edit.get("old_string")) + _lines(edit.get("new_string"))
        return total
    if claude_tool == "NotebookEdit":
        return _lines(tool_input.get("new_source"))
    return None


def paths_for(claude_tool: str, tool_input: dict[str, Any], project_dir: str) -> list[str]:
    raw: list[str] = []
    if claude_tool in FILE_PATH_TOOLS:
        for key in ("file_path", "notebook_path"):
            value = tool_input.get(key)
            if isinstance(value, str) and value:
                raw.append(value)
    elif claude_tool in {"Glob", "Grep"}:
        value = tool_input.get("path")
        if isinstance(value, str) and value:
            raw.append(value)
    return [project_relative(p, project_dir) for p in raw]


def exposure_for(
    claude_tool: str,
    tool_id: str,
    tool_input: dict[str, Any],
    paths: list[str],
    cfg: HookConfig,
) -> Exposure:
    """What this call is about to put in front of the model. Applied when the call is
    *requested*, so a lost or failed ``PostToolUse`` cannot hide an exposure."""
    trust: list[TrustLabel] = []
    data: list[DataClass] = []

    if tool_id in cfg.trust_overrides:
        trust.append(cfg.trust_overrides[tool_id])
    elif claude_tool in {"WebFetch", "WebSearch"} or claude_tool.startswith("mcp__"):
        trust.append(TrustLabel.UNTRUSTED)
    elif claude_tool in READ_TOOLS:
        normalised = [normalize_path(p)[0] for p in paths]
        untrusted = any(matches_any(cfg.untrusted_paths, p) for p in normalised)
        trust.append(TrustLabel.UNTRUSTED if untrusted else TrustLabel.INTERNAL)
    elif claude_tool in SHELL_TOOLS:
        command = str(tool_input.get("command") or "")
        lowered = command.lower()
        if any(marker in lowered for marker in cfg.network_markers):
            trust.append(TrustLabel.UNTRUSTED)
        else:
            trust.append(TrustLabel.INTERNAL)
    elif claude_tool in {"Agent", "Task"}:
        trust.append(TrustLabel.INTERNAL)

    if claude_tool in READ_TOOLS:
        candidates = [normalize_path(p)[0] for p in paths]
    elif claude_tool in SHELL_TOOLS:
        candidates = _shell_path_tokens(str(tool_input.get("command") or ""))
    else:
        candidates = []
    if any(matches_any(cfg.sensitive_paths, c) for c in candidates):
        data.append(DataClass.RESTRICTED)

    return Exposure(trust=trust, data=data)


def _shell_path_tokens(command: str) -> list[str]:
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        tokens = command.split()
    return [normalize_path(t)[0] for t in tokens]


def on_behalf_of(cfg: HookConfig) -> str:
    if cfg.on_behalf_of:
        return cfg.on_behalf_of
    from_env = os.environ.get("JARVIS_ON_BEHALF_OF")
    if from_env:
        return from_env
    try:
        return getpass.getuser()
    except Exception:  # no usable OS user (some containers); never fail the hook for this
        return "unknown-user"


def agent_identity(inp: HookInput, cfg: HookConfig) -> tuple[str, str]:
    """``(agent_id, on_behalf_of)`` from configuration and the hook input, not from agent text."""
    if inp.agent_id:
        agent_id = f"{cfg.agent_name}:{inp.agent_type or 'subagent'}:{inp.agent_id}"
    else:
        agent_id = cfg.agent_name
    return agent_id, on_behalf_of(cfg)


def build_action(
    inp: HookInput,
    cfg: HookConfig,
    project_dir: str,
    session_state: SessionState,
) -> ActionPlan:
    claude_tool = inp.tool_name or "unknown"
    tool_id = registry_tool_id(claude_tool, cfg)
    shell_class: str | None = None

    if claude_tool in SHELL_TOOLS and tool_id == "shell.run":
        shell_class = classify_shell(str(inp.tool_input.get("command") or ""), cfg)
        tool_id = {"read": "shell.read", "test": "shell.test"}.get(shell_class, "shell.run")

    paths = paths_for(claude_tool, inp.tool_input, project_dir)
    agent_id, behalf = agent_identity(inp, cfg)

    action = Action(
        agent_id=agent_id,
        on_behalf_of=behalf,
        role=cfg.role,
        task_id=os.environ.get("JARVIS_TASK_ID") or f"claude-{safe_session_id(inp.session_id)}",
        tool_id=tool_id,
        environment=Environment(cfg.environment),
        paths=paths,
        diff_lines=diff_lines_for(claude_tool, inp.tool_input),
        session=session_state,
    )
    exposure = exposure_for(claude_tool, tool_id, inp.tool_input, paths, cfg)
    return ActionPlan(action, claude_tool, shell_class, exposure)
