"""``agentctl hook``: run, print the settings snippet for, and check the Claude Code hook.

``agentctl hook run`` is what Claude Code executes. Exit codes follow Claude Code's contract:
0 means "no objection" (a decision may be printed as JSON), 2 blocks the call. See ADR 0003.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import yaml
from pydantic import ValidationError

from jarvis.adapters.claude_code import (
    HookConfig,
    HookContext,
    HookMode,
    HookResult,
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
from jarvis.hook_paths import build_spec


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
    run.add_argument(
        "--server",
        action="store_true",
        help="ask a local hook server for the decision (started on first use), and decide here "
        "if it cannot be reached. Same decisions, much less start-up time (ADR 0012)",
    )

    serve = hook_sub.add_parser(
        "serve", help="run, stop or inspect the local hook server for this project"
    )
    serve.add_argument("--mode", choices=[m.value for m in HookMode])
    serve.add_argument("--config")
    serve.add_argument("--state-dir")
    serve.add_argument("--idle-minutes", type=float, default=30.0, help="exit after this long idle")
    serve.add_argument("--stop", action="store_true", help="stop the running server")
    serve.add_argument("--status", action="store_true", help="exit 0 if it is running")
    add_source_arguments(serve)

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
    snippet.add_argument(
        "--server",
        action="store_true",
        help="register the command with --server: ask a local hook server (much less start-up "
        "time per call; see ADR 0012)",
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


def main_hook(argv: list[str]) -> int:
    """``agentctl hook ...`` without building the parser for every other command."""
    parser = argparse.ArgumentParser(prog="agentctl", description="Jarvis framework tools")
    sub = parser.add_subparsers(dest="command", required=True)
    add_hook_parser(sub)
    return run_hook_command(parser.parse_args(argv))


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
                server=args.server,
            )
        except ValueError as exc:
            print(f"cannot build settings: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(settings, indent=2))
        return 0
    if args.hook_command == "report":
        return _report(args)
    if args.hook_command == "serve":
        return _serve(args)
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
    checkpoints = _checkpoint_status(ledger, project_dir)
    if args.json:
        print(json.dumps({**summary, "checkpoints": checkpoints}, indent=2))
    else:
        print(render_text(summary))
        print(_render_checkpoints(checkpoints))
    return 1 if checkpoints.get("failures") else 0


def _checkpoint_status(ledger: Ledger, project_dir: Path) -> dict:
    """Whether the ledger is covered by a recent signed checkpoint. Absence and age are warnings
    here too; only a checkpoint that contradicts the ledger is a failure (ADR 0010)."""
    from jarvis.adapters.claude_code.session import default_state_dir
    from jarvis.core.ledger import (
        check_checkpoints,
        default_checkpoint_path,
        ledger_id,
        milestone_checkpoint_path,
        read_checkpoint_files,
    )

    allowed_file = project_dir / ".agents" / "allowed-signers"
    if not allowed_file.is_file():
        return {"state": "not checked", "reason": "no .agents/allowed-signers file"}
    identity = ledger_id(ledger)
    if identity is None:
        return {"state": "not checked", "reason": "the ledger is empty"}
    text = read_checkpoint_files(
        default_checkpoint_path(default_state_dir(), identity),
        milestone_checkpoint_path(project_dir, identity),
    )
    found = check_checkpoints(ledger, text, allowed_file.read_text(encoding="utf-8"))
    return {
        "state": "failed" if found.failures else "checked",
        "checked": found.checked,
        "good": found.good,
        "latest_seq": found.latest_seq,
        "latest_signer": found.latest_signer,
        "records_after_latest": found.records_after_latest,
        "warnings": found.warnings,
        "failures": found.failures,
    }


def _render_checkpoints(status: dict) -> str:
    if status["state"] == "not checked":
        return f"Checkpoints: not checked ({status['reason']})"
    lines = [f"Checkpoints: {status['good']} of {status['checked']} good"]
    if status["latest_seq"] is not None:
        lines[0] += (
            f"; newest at seq {status['latest_seq']} signed by {status['latest_signer']}, "
            f"{status['records_after_latest']} records after it"
        )
    lines += [f"  WARNING: {w}" for w in status["warnings"]]
    lines += [f"  FAILED: {f}" for f in status["failures"]]
    return "\n".join(lines)


def _read_stdin() -> str:
    stream = sys.stdin
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            pass
    return stream.read()


def _failure_result(enforce: bool, message: str) -> HookResult:
    if enforce:
        return HookResult(2, None, f"jarvis policy gate blocked this call: {message}")
    return HookResult(0, None, f"jarvis hook (log-only): {message}")


def _emit(result: HookResult) -> int:
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return result.exit_code


def execute(payload: object, spec: dict) -> HookResult:
    """Judge one hook call. Used by the in-process hook and by the hook server, so a decision is
    the same wherever it is made. ``spec`` comes from ``jarvis.hook_paths.build_spec``."""
    mode_hint = spec.get("mode")
    enforce_hint = mode_hint == HookMode.ENFORCE.value
    project_dir = Path(spec["project_dir"])
    options = argparse.Namespace(
        policy_source=spec.get("policy_source"),
        policy_ref=spec.get("policy_ref"),
        policy_dir=spec.get("policy_dir"),
    )
    try:
        source = source_from_args(options, project_dir)
        cfg = load_config_from(source, project_dir, spec.get("config"))
    except SourceError as exc:
        return _failure_result(enforce_hint, f"policy source: {exc}")
    except (ValidationError, OSError, ValueError, yaml.YAMLError) as exc:
        return _failure_result(enforce_hint, f"cannot load adapter config: {type(exc).__name__}")

    mode = HookMode(mode_hint) if mode_hint in {m.value for m in HookMode} else cfg.mode
    return handle(payload, HookContext(cfg, mode, project_dir, Path(spec["state_dir"]), source))


def _run(args: argparse.Namespace) -> int:
    mode_hint = args.mode or os.environ.get("JARVIS_HOOK_MODE")
    enforce_hint = mode_hint == HookMode.ENFORCE.value

    try:
        payload = json.loads(_read_stdin())
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        return _emit(
            _failure_result(enforce_hint, f"hook input is not valid JSON ({type(exc).__name__})")
        )

    cwd = payload.get("cwd") if isinstance(payload, dict) else None
    spec = build_spec(
        mode=args.mode,
        config=args.config,
        state_dir=args.state_dir,
        policy_source=getattr(args, "policy_source", None),
        policy_ref=getattr(args, "policy_ref", None),
        policy_dir=getattr(args, "policy_dir", None),
        payload_cwd=cwd if isinstance(cwd, str) else None,
    )
    return _emit(execute(payload, spec))


def _serve(args: argparse.Namespace) -> int:
    """``agentctl hook serve``: run, stop or inspect the server for this project and settings."""
    from jarvis import hook_client

    spec = build_spec(
        mode=args.mode,
        config=args.config,
        state_dir=args.state_dir,
        policy_source=getattr(args, "policy_source", None),
        policy_ref=getattr(args, "policy_ref", None),
        policy_dir=getattr(args, "policy_dir", None),
        payload_cwd=None,
    )
    if args.stop:
        print("stopped" if hook_client.stop(spec) else "no server was running for these settings")
        return 0
    if args.status:
        running = hook_client.ping(spec)
        print("running" if running else "not running")
        return 0 if running else 1
    from jarvis.hook_server import serve

    return serve(spec, args.idle_minutes)


def _claude_version_text() -> str | None:
    import subprocess

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

    _doctor_signatures(source, cfg, mode, findings)

    for finding in findings:
        print(f"{'ok      ' if finding.ok else 'PROBLEM '} {finding.message}")
    return 0 if all(f.ok for f in findings) else 1


def _doctor_signatures(source, cfg, mode, findings) -> None:
    """Signed approvals (ADR 0010): the policy must ask for them in enforce mode, ``ssh-keygen -Y``
    must work, and the allowed-signers file should exist."""
    from jarvis.core import signing
    from jarvis.core.schemas import Policy, parse_yaml_model

    try:
        policy_file = source.read(cfg.policy_path)
        required = bool(
            policy_file and parse_yaml_model(policy_file.text, Policy).approvals.require_signatures
        )
    except (SourceError, ValidationError, ValueError, yaml.YAMLError):
        return  # the policy problem is already reported above
    if mode is HookMode.ENFORCE and not required:
        print("PROBLEM  enforce mode needs `approvals: require_signatures: true` (ADR 0010)")
        findings.append(_problem())
    if required or mode is HookMode.ENFORCE:
        try:
            print(f"ok       {signing.self_test()}")
        except signing.SigningError as exc:
            print(f"PROBLEM  signed approvals cannot work here: {exc}")
            findings.append(_problem())
    if required and mode is HookMode.ENFORCE:
        _doctor_checkpoint(cfg)
    try:
        allowed = source.read(cfg.allowed_signers_path)
    except SourceError:
        allowed = None
    if required and allowed is None:
        print(
            f"note     {cfg.allowed_signers_path} not found in the policy source: "
            "no approval can be counted until it lists a key"
        )
    elif allowed is not None:
        print(
            f"ok       allowed signers: {cfg.allowed_signers_path} (sha256 {allowed.sha256[:12]})"
        )


def _doctor_checkpoint(cfg) -> None:
    """A note, never a problem: with no signed checkpoint, truncating the ledger goes unnoticed."""
    from jarvis.core.ledger import Ledger, default_checkpoint_path, ledger_id

    project_dir = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    ledger = Ledger(project_dir / cfg.ledger_path)
    try:
        identity = ledger_id(ledger) if ledger.path.exists() else None
    except Exception:
        return
    if identity is None:
        return
    if not default_checkpoint_path(default_state_dir(), identity).is_file():
        print(
            "note     no signed ledger checkpoint found in the default place: truncating or "
            "rewriting the ledger would go unnoticed (agentctl checkpoint --key PATH)"
        )


def _problem():
    from jarvis.adapters.claude_code.snippet import Finding

    return Finding(False, "see the problems above")
