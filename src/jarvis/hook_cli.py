"""``agentctl hook``: run, print the settings snippet for, and check the Claude Code hook.

``agentctl hook run`` is what Claude Code executes. Exit codes follow Claude Code's contract:
0 means "no objection" (a decision may be printed as JSON), 2 blocks the call. See ADR 0003.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml
from pydantic import ValidationError

from jarvis.adapters.claude_code import (
    HookConfig,
    HookContext,
    HookMode,
    build_settings,
    check_environment,
    default_state_dir,
    handle,
    load_config,
    parse_version,
)
from jarvis.adapters.claude_code.config import DEFAULT_CONFIG


def add_hook_parser(sub: argparse._SubParsersAction) -> None:
    hook = sub.add_parser("hook", help="Claude Code hook adapter for the policy gate")
    hook_sub = hook.add_subparsers(dest="hook_command", required=True)

    run = hook_sub.add_parser("run", help="handle one hook event from stdin (used by Claude Code)")
    run.add_argument(
        "--mode",
        choices=[m.value for m in HookMode],
        help="log-only never blocks (default); enforce blocks. Overrides $JARVIS_HOOK_MODE "
        "and the config file",
    )
    run.add_argument("--config", help=f"adapter config (default: <project>/{DEFAULT_CONFIG})")
    run.add_argument("--state-dir", help="per-session state directory (default: user state dir)")

    snippet = hook_sub.add_parser("snippet", help="print the hooks block for a settings file")
    snippet.add_argument("--mode", choices=[m.value for m in HookMode], default="log-only")
    snippet.add_argument(
        "--runner",
        choices=["direct", "uv"],
        default="direct",
        help="direct runs `agentctl` from PATH; uv runs it through `uv run --project`",
    )

    doctor = hook_sub.add_parser("doctor", help="check this machine can run the hook in a mode")
    doctor.add_argument("--mode", choices=[m.value for m in HookMode], default="log-only")


def run_hook_command(args: argparse.Namespace) -> int:
    if args.hook_command == "run":
        return _run(args)
    if args.hook_command == "snippet":
        settings = build_settings(HookMode(args.mode), args.runner)
        print(json.dumps(settings, indent=2))
        return 0
    return _doctor(args)


def _read_stdin() -> str:
    stream = sys.stdin
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            pass
    return stream.read()


def _failure(enforce: bool, message: str) -> int:
    if enforce:
        print(f"jarvis policy gate blocked this call: {message}", file=sys.stderr)
        return 2
    print(f"jarvis hook (log-only): {message}", file=sys.stderr)
    return 0


def _run(args: argparse.Namespace) -> int:
    mode_hint = args.mode or os.environ.get("JARVIS_HOOK_MODE")
    enforce_hint = mode_hint == HookMode.ENFORCE.value

    try:
        payload = json.loads(_read_stdin())
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        return _failure(enforce_hint, f"hook input is not valid JSON ({type(exc).__name__})")

    cwd = payload.get("cwd") if isinstance(payload, dict) else None
    project_dir = Path(os.environ.get("CLAUDE_PROJECT_DIR") or cwd or os.getcwd())
    config_path = Path(args.config) if args.config else project_dir / DEFAULT_CONFIG

    try:
        cfg = load_config(config_path)
    except (ValidationError, OSError, ValueError, yaml.YAMLError) as exc:
        return _failure(enforce_hint, f"cannot load {config_path}: {type(exc).__name__}")

    mode = HookMode(mode_hint) if mode_hint in {m.value for m in HookMode} else cfg.mode
    state_dir = Path(args.state_dir) if args.state_dir else default_state_dir()
    result = handle(payload, HookContext(cfg, mode, project_dir, state_dir))

    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return result.exit_code


def _claude_version_text() -> str | None:
    try:
        done = subprocess.run(
            ["claude", "--version"], capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return (done.stdout or done.stderr or "").strip() or None


def _doctor(args: argparse.Namespace) -> int:
    mode = HookMode(args.mode)
    findings = check_environment(parse_version(_claude_version_text() or ""), mode)

    project_dir = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    cfg = HookConfig()
    config_path = project_dir / DEFAULT_CONFIG
    try:
        cfg = load_config(config_path)
        config_note = f"config: {config_path}" if config_path.exists() else "config: defaults"
        print(f"ok       {config_note}")
    except (ValidationError, OSError, ValueError, yaml.YAMLError) as exc:
        print(f"PROBLEM  cannot load {config_path}: {type(exc).__name__}")
        findings.append(_problem())
    for label, rel in (("policy", cfg.policy_path), ("tool registry", cfg.registry_path)):
        path = project_dir / rel
        if path.exists():
            print(f"ok       {label}: {path}")
        else:
            print(f"PROBLEM  {label} not found: {path}")
            findings.append(_problem())

    for finding in findings:
        print(f"{'ok      ' if finding.ok else 'PROBLEM '} {finding.message}")
    return 0 if all(f.ok for f in findings) else 1


def _problem():
    from jarvis.adapters.claude_code.snippet import Finding

    return Finding(False, "see the problems above")
