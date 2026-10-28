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
from jarvis.adapters.claude_code.config import DEFAULT_CONFIG, parse_config
from jarvis.adapters.claude_code.report import render_text, summarize
from jarvis.adapters.claude_code.source import (
    SOURCE_KINDS,
    SourceError,
    TrustedSource,
    make_source,
)
from jarvis.core.ledger import Ledger


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
    add_source_arguments(run)

    snippet = hook_sub.add_parser("snippet", help="print the hooks block for a settings file")
    snippet.add_argument("--mode", choices=[m.value for m in HookMode], default="log-only")
    snippet.add_argument(
        "--runner",
        choices=["direct", "uv", "venv"],
        default="direct",
        help="direct runs `agentctl` from PATH; uv runs it through `uv run --project`; "
        "venv runs the project's .venv Python directly (fastest)",
    )
    snippet.add_argument(
        "--minimal",
        action="store_true",
        help="register only PreToolUse, SessionStart and ConfigChange (about half the cost)",
    )
    add_source_arguments(snippet)

    doctor = hook_sub.add_parser("doctor", help="check this machine can run the hook in a mode")
    doctor.add_argument("--mode", choices=[m.value for m in HookMode], default="log-only")
    add_source_arguments(doctor)

    report = hook_sub.add_parser("report", help="summarise the decisions the hook recorded")
    report.add_argument("--ledger", help="default: <project>/.agents/ledger/events.jsonl")
    report.add_argument("--json", action="store_true", help="print the summary as JSON")


def add_source_arguments(parser: argparse.ArgumentParser) -> None:
    """Where policy and adapter config are read from (ADR 0004). These come from the command
    line or environment, never from a file the agent can edit."""
    parser.add_argument(
        "--policy-source",
        choices=SOURCE_KINDS,
        help="worktree (default, agent-writable, log-only only), git (content at a ref or "
        "commit) or directory (outside the project). Overrides $JARVIS_POLICY_SOURCE",
    )
    parser.add_argument(
        "--policy-ref",
        help="git ref or full commit SHA for --policy-source git (default origin/main; a full "
        "SHA is the strongest). Overrides $JARVIS_POLICY_REF",
    )
    parser.add_argument(
        "--policy-dir",
        help="directory for --policy-source directory. Overrides $JARVIS_POLICY_DIR",
    )


def source_from_args(args: argparse.Namespace, project_dir: Path) -> TrustedSource:
    return make_source(
        getattr(args, "policy_source", None) or os.environ.get("JARVIS_POLICY_SOURCE"),
        project_dir,
        getattr(args, "policy_ref", None) or os.environ.get("JARVIS_POLICY_REF"),
        getattr(args, "policy_dir", None) or os.environ.get("JARVIS_POLICY_DIR"),
    )


def load_config_from(source: TrustedSource, project_dir: Path, config: str | None) -> HookConfig:
    """Adapter config comes from the same source as the policy; otherwise an agent could edit
    it to remap tools or loosen trust rules while the policy itself stayed protected."""
    if source.protected:
        found = source.read(config or DEFAULT_CONFIG)
        return parse_config(found.text if found else None)
    return load_config(Path(config) if config else project_dir / DEFAULT_CONFIG)


def run_hook_command(args: argparse.Namespace) -> int:
    if args.hook_command == "run":
        return _run(args)
    if args.hook_command == "snippet":
        try:
            settings = build_settings(
                HookMode(args.mode),
                args.runner,
                minimal=args.minimal,
                policy_source=args.policy_source,
                policy_ref=args.policy_ref,
                policy_dir=args.policy_dir,
            )
        except ValueError as exc:
            print(f"cannot build settings: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(settings, indent=2))
        return 0
    if args.hook_command == "report":
        return _report(args)
    return _doctor(args)


def _report(args: argparse.Namespace) -> int:
    project_dir = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    path = Path(args.ledger) if args.ledger else project_dir / HookConfig().ledger_path
    ledger = Ledger(path)
    if not path.exists():
        print(f"ledger not found: {path}", file=sys.stderr)
        return 2
    result = ledger.verify()
    if not result.ok:
        print(
            f"ledger fails verification: {result.error} (seq {result.error_seq})", file=sys.stderr
        )
        return 1
    summary = summarize(ledger.records())
    print(json.dumps(summary, indent=2) if args.json else render_text(summary))
    return 0


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

    try:
        source = source_from_args(args, project_dir)
        cfg = load_config_from(source, project_dir, args.config)
    except SourceError as exc:
        return _failure(enforce_hint, f"policy source: {exc}")
    except (ValidationError, OSError, ValueError, yaml.YAMLError) as exc:
        return _failure(enforce_hint, f"cannot load adapter config: {type(exc).__name__}")

    mode = HookMode(mode_hint) if mode_hint in {m.value for m in HookMode} else cfg.mode
    state_dir = Path(args.state_dir) if args.state_dir else default_state_dir()
    result = handle(payload, HookContext(cfg, mode, project_dir, state_dir, source))

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
    try:
        source = source_from_args(args, project_dir)
        print(f"ok       policy source: {source.describe()} (protected: {source.protected})")
    except SourceError as exc:
        print(f"PROBLEM  policy source: {exc}")
        return 1
    if mode is HookMode.ENFORCE and not source.protected:
        print("PROBLEM  enforce mode needs --policy-source git or directory (ADR 0004)")
        findings.append(_problem())
    try:
        cfg = load_config_from(source, project_dir, None)
        print("ok       adapter config loaded")
    except (SourceError, ValidationError, OSError, ValueError, yaml.YAMLError) as exc:
        print(f"PROBLEM  cannot load adapter config: {type(exc).__name__}")
        findings.append(_problem())
    try:
        for label, rel in (("policy", cfg.policy_path), ("tool registry", cfg.registry_path)):
            found = source.read(rel)
            if found is not None:
                print(f"ok       {label}: {rel} (sha256 {found.sha256[:12]})")
            else:
                print(f"PROBLEM  {label} not found in the policy source: {rel}")
                findings.append(_problem())
    except SourceError as exc:
        print(f"PROBLEM  {exc}")
        findings.append(_problem())

    for finding in findings:
        print(f"{'ok      ' if finding.ok else 'PROBLEM '} {finding.message}")
    return 0 if all(f.ok for f in findings) else 1


def _problem():
    from jarvis.adapters.claude_code.snippet import Finding

    return Finding(False, "see the problems above")
