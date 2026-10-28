"""Claude Code hook handler: events in, a decision and audit records out (ADR 0003).

Modes
-----
``log-only`` (the default) records every decision and **never blocks**: it always exits 0 and
prints no decision, so a bug here cannot stop work. ``enforce`` turns gate verdicts into
Claude Code decisions and fails closed.

Verdict to Claude Code, in enforce mode
---------------------------------------
* allow: no output. The gate's allow means "no objection", not "approve", so Claude Code's own
  permission rules and prompts still apply. We never emit ``allow`` and never ``updatedInput``.
* council or human: ``ask`` (a person answers in the terminal), or ``deny`` when the run is
  non-interactive. This is temporary until the approval flow exists (P2.7).
* deny: ``deny`` with the reason.

What is recorded
----------------
Decisions and outcomes with tool names, project-relative paths, rule ids, the session id, the
permission mode and hashes of the policy files. Never file contents, command text, tool output
or error text (secret redaction arrives with P2.12).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from jarvis.core.killswitch import halt_path
from jarvis.core.ledger import Ledger
from jarvis.core.policy import run_check
from jarvis.core.schemas import (
    Decision,
    Event,
    EventKind,
    Policy,
    ProjectConfig,
    Verdict,
    parse_tool_registry,
    parse_yaml_model,
)
from jarvis.core.schemas.event import ToolRef

from .capture import capture_usage
from .config import HookConfig, HookMode
from .mapping import agent_identity, build_action, safe_session_id
from .payload import HookInput
from .session import SessionStore
from .source import SourceError, SourceFile, TrustedSource, WorktreeSource

SETTINGS_SOURCES = {"user_settings", "project_settings", "local_settings"}
BLOCKING_EVENTS = {"PreToolUse", "ConfigChange"}
USAGE_EVENTS = {"Stop", "SubagentStop", "SessionEnd"}
MAX_REASON = 1500


@dataclass(frozen=True)
class HookResult:
    exit_code: int = 0
    stdout: str | None = None
    stderr: str | None = None


@dataclass(frozen=True)
class HookContext:
    cfg: HookConfig
    mode: HookMode
    project_dir: Path
    state_dir: Path
    # Where the policy files are read from. ``None`` means the working tree, which is
    # agent-writable and therefore refused in enforce mode (ADR 0004).
    source: TrustedSource | None = None

    def resolve(self, relative: str) -> Path:
        path = Path(relative)
        return path if path.is_absolute() else self.project_dir / path

    @property
    def enforcing(self) -> bool:
        return self.mode is HookMode.ENFORCE

    @property
    def policy_source(self) -> TrustedSource:
        return self.source if self.source is not None else WorktreeSource(self.project_dir)


def handle(payload: Any, ctx: HookContext) -> HookResult:
    """Handle one hook event. Never raises."""
    try:
        inp = HookInput.model_validate(payload)
    except ValidationError as exc:
        return _failure(ctx, f"invalid hook input ({exc.error_count()} problems)", blocking=True)
    try:
        return _dispatch(inp, ctx)
    except Exception as exc:  # a hook must never crash; see _failure for what happens next
        _try_record_error(inp, ctx, exc)
        return _failure(
            ctx,
            f"{type(exc).__name__}: {str(exc)[:200]}",
            blocking=inp.hook_event_name in BLOCKING_EVENTS,
        )


def _failure(ctx: HookContext, message: str, blocking: bool) -> HookResult:
    if ctx.enforcing and blocking:
        return HookResult(2, None, f"jarvis policy gate blocked this call: {message}")
    prefix = (
        "jarvis hook (enforce, non-blocking event)" if ctx.enforcing else "jarvis hook (log-only)"
    )
    return HookResult(0, None, f"{prefix}: {message}")


def _dispatch(inp: HookInput, ctx: HookContext) -> HookResult:
    event = inp.hook_event_name
    if event == "PreToolUse":
        return _pre_tool_use(inp, ctx)
    if event in ("PostToolUse", "PostToolUseFailure"):
        return _post_tool_use(inp, ctx)
    if event == "SessionStart":
        return _session_start(inp, ctx)
    if event == "ConfigChange":
        return _config_change(inp, ctx)
    if event in USAGE_EVENTS:
        return _capture_usage(inp, ctx)
    return HookResult()


def _ledger(ctx: HookContext) -> Ledger:
    return Ledger(ctx.resolve(ctx.cfg.ledger_path), lock_timeout=ctx.cfg.lock_timeout_seconds)


def _store(ctx: HookContext) -> SessionStore:
    return SessionStore(ctx.state_dir, ctx.cfg.lock_timeout_seconds)


def _context(inp: HookInput, ctx: HookContext) -> dict[str, Any]:
    server = None
    if inp.mcp_server is not None:
        server = {"name": inp.mcp_server.name, "source": inp.mcp_server.source}
    return {
        "mcp_server": server,
        "harness": "claude-code",
        "mode": ctx.mode.value,
        "hook_event": inp.hook_event_name,
        "session_id": inp.session_id,
        "permission_mode": inp.permission_mode,
        "tool_use_id": inp.tool_use_id,
        "agent_id": inp.agent_id,
        "transcript_path": inp.transcript_path,
    }


def _read_required(source: TrustedSource, relative: str) -> SourceFile:
    found = source.read(relative)
    if found is None:
        raise SourceError(f"{relative} was not found in the policy source {source.describe()}")
    return found


def _load_policy_files(ctx: HookContext):
    """Read the policy, registry and project files from the trusted source.

    In enforce mode the source must not be agent-writable. Anything missing, unreadable or
    invalid raises, which the caller turns into a block (enforce) or an error record (log-only).
    """
    source = ctx.policy_source
    if ctx.enforcing and not source.protected:
        raise SourceError(
            "enforce mode needs a policy source the agent cannot write: use --policy-source git "
            "or directory (the working tree is agent-writable)"
        )
    cfg = ctx.cfg
    policy_file = _read_required(source, cfg.policy_path)
    registry_file = _read_required(source, cfg.registry_path)
    policy = parse_yaml_model(policy_file.text, Policy)
    registry = parse_tool_registry(registry_file.text)
    project = None
    provenance = {
        "policy_source": source.describe(),
        "policy_source_protected": source.protected,
        "policy_sha256": policy_file.sha256,
        "registry_sha256": registry_file.sha256,
    }
    if cfg.project_path:
        project_file = _read_required(source, cfg.project_path)
        project = parse_yaml_model(project_file.text, ProjectConfig)
        provenance["project_sha256"] = project_file.sha256
    return policy, registry, project, provenance


def _pre_tool_use(inp: HookInput, ctx: HookContext) -> HookResult:
    if not inp.tool_name:
        raise ValueError("PreToolUse input has no tool_name")
    policy, registry, project, provenance = _load_policy_files(ctx)
    store = _store(ctx)
    loaded = store.load(inp.session_id)
    plan = build_action(inp, ctx.cfg, str(ctx.project_dir), loaded.state)
    extra = {
        **_context(inp, ctx),
        **provenance,
        "claude_tool": plan.claude_tool,
        "shell_class": plan.shell_class,
        "state_healthy": loaded.healthy,
    }
    try:
        verdict = run_check(
            plan.action,
            policy,
            registry,
            project,
            ledger=_ledger(ctx),
            record=True,
            extra=extra,
            halt_file=halt_path(ctx.state_dir),
        )
    finally:
        # Applied when the call is requested, not when it finishes: if PostToolUse never
        # arrives, the exposure is still counted. A call the gate then blocks is counted too,
        # which is the conservative side.
        store.update(inp.session_id, plan.exposure)
    return _pre_result(verdict, ctx)


def _pre_result(verdict: Verdict, ctx: HookContext) -> HookResult:
    if not ctx.enforcing or verdict.decision == Decision.ALLOW:
        return HookResult()
    if verdict.decision == Decision.DENY or ctx.cfg.non_interactive:
        permission = "deny"
    else:
        permission = "ask"
    reason = f"jarvis policy gate ({verdict.decision.value}): {verdict.reason}"[:MAX_REASON]
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": permission,
            "permissionDecisionReason": reason,
        }
    }
    return HookResult(0, json.dumps(output))


def _post_tool_use(inp: HookInput, ctx: HookContext) -> HookResult:
    if not inp.tool_name:
        return HookResult()
    from jarvis.core.schemas.action import SessionState

    plan = build_action(inp, ctx.cfg, str(ctx.project_dir), SessionState())
    _store(ctx).update(inp.session_id, plan.exposure)
    failed = inp.hook_event_name == "PostToolUseFailure"
    _ledger(ctx).append(
        Event(
            kind=EventKind.TOOL_CALL,
            agent_id=plan.action.agent_id,
            on_behalf_of=plan.action.on_behalf_of,
            task_id=plan.action.task_id,
            tool=ToolRef(id=plan.action.tool_id),
            outcome="failed" if failed else "ok",
            data={
                "context": {**_context(inp, ctx), "claude_tool": plan.claude_tool},
                "duration_ms": inp.duration_ms,
            },
        )
    )
    return HookResult()


def _note(inp: HookInput, ctx: HookContext, data: dict[str, Any]) -> Event:
    agent_id, behalf = agent_identity(inp, ctx.cfg)
    return Event(
        kind=EventKind.NOTE,
        agent_id=agent_id,
        on_behalf_of=behalf,
        task_id=f"claude-{safe_session_id(inp.session_id)}",
        data={**data, "context": _context(inp, ctx)},
    )


def _session_start(inp: HookInput, ctx: HookContext) -> HookResult:
    # A fresh or cleared session starts unexposed; resumed, forked or compacted ones keep state.
    if inp.source in (None, "startup", "clear"):
        _store(ctx).reset(inp.session_id)
    _ledger(ctx).append(_note(inp, ctx, {"event": "session_start", "source": inp.source}))
    return HookResult()


def _config_change(inp: HookInput, ctx: HookContext) -> HookResult:
    block = ctx.enforcing and ctx.cfg.block_config_changes and inp.source in SETTINGS_SOURCES
    shown = None
    if inp.file_path:
        from .mapping import project_relative

        shown = project_relative(inp.file_path, str(ctx.project_dir))
    _ledger(ctx).append(
        _note(
            inp,
            ctx,
            {
                "event": "config_change",
                "config_source": inp.source,
                "file": shown,
                "blocked": block,
            },
        )
    )
    if block:
        return HookResult(
            2,
            None,
            "jarvis policy gate: changes to Claude Code settings are blocked during a session "
            "because they can remove the hook. Edit them outside the session.",
        )
    return HookResult()


def _capture_usage(inp: HookInput, ctx: HookContext) -> HookResult:
    """Record the session's model calls. Never blocks, in any mode: a failure here is an error
    record, not a refusal."""
    if inp.transcript_path:
        capture_usage(inp, ctx, _ledger(ctx))
    return HookResult()


def _try_record_error(inp: HookInput, ctx: HookContext, exc: Exception) -> None:
    """Best effort: an error while handling an event is itself worth an audit record."""
    try:
        agent_id, behalf = agent_identity(inp, ctx.cfg)
        _ledger(ctx).append(
            Event(
                kind=EventKind.ERROR,
                agent_id=agent_id,
                on_behalf_of=behalf,
                outcome="hook-error",
                data={
                    "error": f"{type(exc).__name__}: {str(exc)[:200]}",
                    "context": _context(inp, ctx),
                },
            )
        )
    except Exception:  # nothing more can be done; the caller still fails safe
        return
