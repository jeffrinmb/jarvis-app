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
import json
import os
import re
import shlex
import sys
from dataclasses import dataclass, field
from typing import Any

from jarvis.core.observability import span_id_for, trace_id_for
from jarvis.core.policy import matches_any, normalize_path
from jarvis.core.schemas import Action, DataClass, Environment, TrustLabel
from jarvis.core.schemas.action import SessionState
from jarvis.core.security.redact import find_secrets_in

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
    # Claude Code's own bookkeeping tools: they read or write only session state.
    "AskUserQuestion": "claude.internal",
    "ExitPlanMode": "claude.internal",
    "EnterPlanMode": "claude.internal",
    "TodoWrite": "claude.internal",
    "ToolSearch": "claude.internal",
    "Skill": "claude.internal",
    "TaskCreate": "claude.internal",
    "TaskUpdate": "claude.internal",
    "TaskList": "claude.internal",
    "TaskGet": "claude.internal",
    "TaskOutput": "claude.internal",
    "TaskStop": "claude.internal",
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


def trace_ids(inp: HookInput) -> tuple[str, str | None]:
    """The trace id of this session and the span id of this tool call (task P2.3a).

    The decision before a call and its outcome after it get the same span id because both come
    from the session id and ``tool_use_id``. There is no span id without a ``tool_use_id``.
    """
    session = safe_session_id(inp.session_id)
    span = span_id_for(session, "tool", inp.tool_use_id) if inp.tool_use_id else None
    return trace_id_for(session), span


def registry_tool_id(claude_tool: str, cfg: HookConfig) -> str:
    """Registry id for a Claude Code tool name. Unknown tools get an id that is not in the
    registry, so the gate denies them. MCP tools keep their full name and must be registered."""
    if claude_tool in cfg.tool_map:
        return cfg.tool_map[claude_tool]
    if claude_tool in BUILTIN_TOOL_MAP:
        return BUILTIN_TOOL_MAP[claude_tool]
    return claude_tool if claude_tool.startswith("mcp__") else f"claude.{claude_tool}"


_LEADING_CD = re.compile(
    r"""^\s*cd\s+(?:"(?P<dq>[^"$`]+)"|'(?P<sq>[^']+)'|(?P<bare>[A-Za-z0-9_./:-]+))"""
    r"""\s*&&\s*(?P<rest>\S.*)$""",
    re.DOTALL,
)


def _msys_to_native(path: str) -> str:
    """Git Bash on Windows writes ``D:\\x`` as ``/d/x``."""
    if sys.platform == "win32" and re.match(r"^/[A-Za-z]/", path):
        return f"{path[1].upper()}:{path[2:]}"
    return path


def strip_leading_cd(command: str, project_dir: str) -> str:
    """Drop a leading ``cd <dir> &&`` when ``<dir>`` is inside the project.

    Claude Code often writes ``cd <project> && git status``. The ``cd`` has no effect beyond
    the rest of that one command line, so classifying the remainder is safe. A ``cd`` to
    anywhere outside the project, a variable or a substitution is left in place, which makes
    the command a general shell call.
    """
    match = _LEADING_CD.match(command)
    if not match:
        return command
    target = _msys_to_native(match.group("dq") or match.group("sq") or match.group("bare"))
    if not os.path.isabs(target):
        target = os.path.join(project_dir, target)
    inside = project_relative(target, project_dir)
    if os.path.isabs(inside):
        return command
    return match.group("rest")


_OUTPUT_FILTER = re.compile(
    r"\s*(?:2>&1|\|\s*(?:head|tail)(?:\s+-n?\s*\d+|\s+-\d+)?|\|\s*wc\s+-l)\s*$"
)


def strip_output_filters(command: str) -> str:
    """Drop trailing ``2>&1``, ``| head -N``, ``| tail -N`` and ``| wc -l``.

    Claude Code commonly trims output this way. These only read the previous command's output
    and take no file arguments, so removing them before classification is safe; anything else
    after a pipe (including ``head file`` or ``tee``) is left in place and keeps the command a
    general shell call.
    """
    previous = None
    while previous != command:
        previous = command
        command = _OUTPUT_FILTER.sub("", command)
    return command


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


MAX_CHAIN = 8
_PIPEFAIL = re.compile(r"^\s*set\s+-o\s+pipefail\s+&&\s+")


def split_and_chain(command: str) -> list[str] | None:
    """The parts of ``a && b && c``, split only at an ``&&`` that is outside quotes. ``None`` for
    a command with an unbalanced quote."""
    parts: list[str] = []
    current: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(command):
        c = command[i]
        if quote:
            current.append(c)
            if c == "\\" and quote == '"' and i + 1 < len(command):
                current.append(command[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "'\"":
            quote = c
            current.append(c)
        elif command.startswith("&&", i):
            parts.append("".join(current))
            current = []
            i += 2
            continue
        else:
            current.append(c)
        i += 1
    if quote:
        return None
    parts.append("".join(current))
    return parts


def classify_command(command: str, cfg: HookConfig) -> str:
    """``read``, ``test`` or ``run`` for a whole command line (task P2.10a).

    A single command is classified exactly as ``classify_shell`` does. A chain ``a && b && c`` is
    ``read`` or ``test`` only when **every** part is one, with nothing else in it: no ``;``, no
    pipe, no redirect, no substitution, no second ``&``, no empty part and at most ``MAX_CHAIN``
    parts. ``&&`` only runs the next part if the previous succeeded, so a chain of commands that are
    each already read or test commands can do nothing that they could not do one at a time. A
    leading ``set -o pipefail &&`` only changes how the same line reports a failure.
    """
    command = _PIPEFAIL.sub("", command, count=1)
    parts = split_and_chain(command)
    if parts is None or len(parts) > MAX_CHAIN or any(not p.strip(" 	") for p in parts):
        return "run"
    kinds = [classify_shell(p.strip(" 	"), cfg) for p in parts]  # a newline is not padding
    if "run" in kinds:
        return "run"
    return "test" if "test" in kinds else "read"


_WORD = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
_COMMAND_SEPARATORS = re.compile(r"&&|\|\||[;|\n]")
MAX_PREFIXES = 10


def command_prefixes_for(command: str) -> list[str]:
    """The first one or two plain words of each command in a shell line, for measuring what the
    shell prompts are made of (task P2.10a). Never the arguments: the second word is kept only if
    it is a plain word (not a flag, a path or an assignment), the first word is its file name
    without any directory, and a ``cd`` is recorded without its target. Anything else is
    ``(other)``. The ledger redacts what it stores as well."""
    found: list[str] = []
    for part in _COMMAND_SEPARATORS.split(command)[:MAX_PREFIXES]:
        words = part.split()
        if not words:
            continue
        first = re.split(r"[\\/]", words[0])[-1]
        if not _WORD.match(first):
            found.append("(other)")
            continue
        if first == "cd":
            found.append("cd")
            continue
        second = words[1] if len(words) > 1 else ""
        keep = second and _WORD.match(second) and not second.startswith("-")
        found.append(f"{first} {second}" if keep else first)
    return found


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


MAX_DOMAINS = 20
_MAX_SCAN_STRINGS = 200
_URL_HOST = re.compile(
    r"(?i)\b[a-z][a-z0-9+.-]*://(?:[^\s/@?#]*@)?(\[[0-9a-f:.]+\]|[^\s/:?#@\[\]]+)"
)


def _hosts_in(text: str, found: list[str]) -> None:
    for match in _URL_HOST.finditer(text):
        host = match.group(1).lower().rstrip(".")
        if host and len(host) <= 253 and host not in found and len(found) < MAX_DOMAINS:
            found.append(host)


def domains_for(claude_tool: str, tool_input: dict[str, Any]) -> list[str]:
    """The hosts a call names: the host of a ``WebFetch`` URL, or the hosts of any URL in an MCP
    tool's input (strings only, bounded). A web search names no host, because the search engine
    is the tool. Host names only: never a path, query or credentials."""
    found: list[str] = []
    if claude_tool == "WebFetch":
        url = tool_input.get("url")
        if isinstance(url, str):
            _hosts_in(url, found)
    elif claude_tool.startswith("mcp__"):
        stack: list[Any] = [tool_input]
        budget = _MAX_SCAN_STRINGS
        while stack and budget > 0:
            item = stack.pop()
            if isinstance(item, str):
                budget -= 1
                _hosts_in(item, found)
            elif isinstance(item, dict):
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)
    return found


def data_bytes_for(tool_input: dict[str, Any]) -> int:
    """The size of a call's input, which bounds what a tool that sends data can carry."""
    return len(json.dumps(tool_input, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


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
        command = strip_leading_cd(str(inp.tool_input.get("command") or ""), project_dir)
        shell_class = classify_command(strip_output_filters(command), cfg)
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
        # The kinds only: the input itself is never kept (task P2.12).
        command_prefixes=(
            command_prefixes_for(str(inp.tool_input.get("command") or ""))
            if cfg.record_command_prefixes and claude_tool in SHELL_TOOLS
            else []
        ),
        domains=domains_for(claude_tool, inp.tool_input),
        data_bytes=data_bytes_for(inp.tool_input),
        secrets_in_input=find_secrets_in(inp.tool_input),
        session=session_state,
    )
    exposure = exposure_for(claude_tool, tool_id, inp.tool_input, paths, cfg)
    return ActionPlan(action, claude_tool, shell_class, exposure)
