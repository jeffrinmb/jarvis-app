"""`agentctl`: command line for the policy gate, the ledger and schemas.

Exit codes for ``log``, ``verify``, ``head`` and ``schema``: 0 success, 1 verification failed
or ledger problem, 2 bad input.

Exit codes for ``check`` (the policy gate): 0 allow, 10 council, 11 human, 12 deny, and 2 for
invalid input. **Callers must treat every code other than 0 as "not allowed".** An internal
error also exits 12, so the gate fails closed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import yaml
from pydantic import ValidationError

from jarvis.adapters.claude_code import default_state_dir
from jarvis.approvals_cli import REFUSED_KINDS, add_approvals_parser, run_approvals_command
from jarvis.core.killswitch import clear_halt, halt_path, read_halt, set_halt
from jarvis.core.ledger import Ledger, LedgerError
from jarvis.core.policy import run_check
from jarvis.core.schemas import (
    Action,
    Decision,
    Event,
    EventKind,
    Policy,
    ProjectConfig,
    RiskClass,
    TrustLabel,
    export_json_schemas,
    load_tool_registry,
    load_yaml_model,
)
from jarvis.core.schemas.event import ModelRef, ToolRef, Usage
from jarvis.core.tokens import (
    GROUPS,
    load_price_table,
    record_model_call,
    render_tokens,
    summarize_tokens,
)
from jarvis.hook_cli import add_hook_parser, run_hook_command

DEFAULT_LEDGER = ".agents/ledger/events.jsonl"
DEFAULT_POLICY = ".agents/policy.yaml"
DEFAULT_REGISTRY = ".agents/tool-registry.yaml"

CHECK_EXIT_CODES = {
    Decision.ALLOW: 0,
    Decision.COUNCIL: 10,
    Decision.HUMAN: 11,
    Decision.DENY: 12,
}


def _ledger_path(arg: str | None) -> Path:
    return Path(arg or os.environ.get("JARVIS_LEDGER") or DEFAULT_LEDGER)


def _parse_anchor(value: str) -> tuple[int, str]:
    seq, sep, digest = value.partition(":")
    if not sep or not seq.isdigit() or len(digest) != 64:
        raise argparse.ArgumentTypeError("anchor must look like SEQ:HASH (64 hex characters)")
    return int(seq), digest


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentctl", description="Jarvis framework tools")
    sub = parser.add_subparsers(dest="command", required=True)

    log = sub.add_parser("log", help="append an event to the ledger")
    log.add_argument("--ledger", help=f"ledger file (default: $JARVIS_LEDGER or {DEFAULT_LEDGER})")
    log.add_argument("--kind", required=True, choices=[k.value for k in EventKind])
    log.add_argument("--agent", required=True, help="agent id")
    log.add_argument("--on-behalf-of", required=True, help="accountable human or team")
    log.add_argument("--task")
    log.add_argument("--stage")
    log.add_argument("--trace-id")
    log.add_argument("--span-id")
    log.add_argument("--tool-id")
    log.add_argument("--tool-version")
    log.add_argument("--model")
    log.add_argument("--provider")
    log.add_argument("--prompt-version")
    log.add_argument("--tokens-in", type=int)
    log.add_argument("--tokens-out", type=int)
    log.add_argument("--tokens-cached", type=int)
    log.add_argument("--cost-usd", type=float)
    log.add_argument("--risk", choices=[r.value for r in RiskClass])
    log.add_argument("--decision", choices=[d.value for d in Decision])
    log.add_argument("--trust", choices=[t.value for t in TrustLabel])
    log.add_argument("--tainted", action=argparse.BooleanOptionalAction, default=None)
    log.add_argument("--approver")
    log.add_argument("--outcome")
    log.add_argument("--data", help="JSON object with redacted detail")

    verify = sub.add_parser("verify", help="verify the hash chain")
    verify.add_argument("--ledger")
    verify.add_argument("--anchor", type=_parse_anchor, help="SEQ:HASH recorded earlier")

    head = sub.add_parser("head", help="print SEQ:HASH of the last record, for anchoring")
    head.add_argument("--ledger")

    check = sub.add_parser(
        "check",
        help="ask the policy gate about an action (exit 0 allow, 10 council, 11 human, 12 deny)",
    )
    check.add_argument("--action", required=True, help="action JSON or YAML file, or - for stdin")
    check.add_argument("--policy", default=DEFAULT_POLICY, help=f"default: {DEFAULT_POLICY}")
    check.add_argument(
        "--registry", default=DEFAULT_REGISTRY, help=f"tool registry, default: {DEFAULT_REGISTRY}"
    )
    check.add_argument("--project", help="project.yaml with enabled tools and no-go paths")
    check.add_argument("--ledger", help=f"default: $JARVIS_LEDGER or {DEFAULT_LEDGER}")
    check.add_argument("--state-dir", help="where the kill switch lives (default: user state)")
    check.add_argument(
        "--no-log",
        action="store_true",
        help="dry run: read history for cumulative limits but do not record the decision",
    )

    halt = sub.add_parser("halt", help="kill switch: deny every action until `resume`")
    halt.add_argument("--ledger")
    halt.add_argument("--state-dir", help="default: the user state directory the hook uses")
    halt.add_argument("--by", help="who is stopping the agents (default: $JARVIS_ON_BEHALF_OF)")
    resume = sub.add_parser("resume", help="turn the kill switch off")
    resume.add_argument("--ledger")
    resume.add_argument("--state-dir")
    resume.add_argument("--by")
    status = sub.add_parser("status", help="show whether the kill switch is on (exit 1 if on)")
    status.add_argument("--ledger")
    status.add_argument("--state-dir")

    tokens = sub.add_parser("tokens", help="token and cost ledger: record a call, report usage")
    tokens_sub = tokens.add_subparsers(dest="tokens_command", required=True)
    rec = tokens_sub.add_parser("record", help="append one model-call row to the ledger")
    rec.add_argument("--ledger")
    rec.add_argument("--agent", required=True)
    rec.add_argument("--on-behalf-of", required=True)
    rec.add_argument("--model", required=True)
    rec.add_argument("--provider")
    rec.add_argument("--prompt-version")
    rec.add_argument("--task")
    rec.add_argument("--stage")
    rec.add_argument("--tokens-in", type=int, required=True, help="input tokens not from a cache")
    rec.add_argument("--tokens-out", type=int, required=True)
    rec.add_argument("--tokens-cached", type=int, default=0, help="input tokens from a cache")
    rec.add_argument("--cost-usd", type=float, help="provider-reported cost; wins over --prices")
    rec.add_argument("--prices", help="price table YAML, used when --cost-usd is not given")
    rec.add_argument("--outcome")
    rep = tokens_sub.add_parser("report", help="summarise recorded model calls")
    rep.add_argument("--ledger")
    rep.add_argument("--by", choices=GROUPS, default="model")
    rep.add_argument("--json", action="store_true")

    schema = sub.add_parser("schema", help="schema tools")
    schema_sub = schema.add_subparsers(dest="schema_command", required=True)
    export = schema_sub.add_parser("export", help="write JSON Schema files")
    export.add_argument("--out", default="schemas")

    add_hook_parser(sub)
    add_approvals_parser(sub)

    return parser


def _build_event(args: argparse.Namespace) -> Event:
    fields: dict = {
        "kind": args.kind,
        "agent_id": args.agent,
        "on_behalf_of": args.on_behalf_of,
        "task_id": args.task,
        "stage": args.stage,
        "trace_id": args.trace_id,
        "span_id": args.span_id,
        "risk": args.risk,
        "decision": args.decision,
        "trust": args.trust,
        "tainted": args.tainted,
        "approver": args.approver,
        "outcome": args.outcome,
    }
    if args.tool_id:
        fields["tool"] = ToolRef(id=args.tool_id, version=args.tool_version)
    elif args.tool_version:
        raise ValueError("--tool-version needs --tool-id")
    if args.model:
        fields["model"] = ModelRef(
            name=args.model, provider=args.provider, prompt_version=args.prompt_version
        )
    if any(v is not None for v in (args.tokens_in, args.tokens_out, args.tokens_cached)) or (
        args.cost_usd is not None
    ):
        fields["usage"] = Usage(
            tokens_in=args.tokens_in or 0,
            tokens_out=args.tokens_out or 0,
            tokens_cached=args.tokens_cached or 0,
            cost_usd=args.cost_usd or 0.0,
        )
    if args.data:
        data = json.loads(args.data)
        if not isinstance(data, dict):
            raise ValueError("--data must be a JSON object")
        fields["data"] = data
    return Event(**{k: v for k, v in fields.items() if v is not None})


def _halt_file(args: argparse.Namespace) -> Path:
    state = getattr(args, "state_dir", None)
    return halt_path(Path(state) if state else default_state_dir())


def _record_switch(ledger: Ledger, state: str, by: str | None) -> None:
    """Best effort: the switch works even when the ledger does not."""
    try:
        ledger.append(
            Event(
                kind=EventKind.KILL_SWITCH,
                agent_id="agentctl",
                on_behalf_of=by or os.environ.get("JARVIS_ON_BEHALF_OF") or "unknown",
                outcome=state,
                data={"state": state},
            )
        )
    except Exception as exc:
        print(f"note: the ledger was not updated ({type(exc).__name__})", file=sys.stderr)


def _switch_command(args: argparse.Namespace, ledger: Ledger) -> int:
    path = _halt_file(args)
    if args.command == "halt":
        try:
            halt = set_halt(path, args.by)
        except OSError as exc:
            print(f"cannot set the kill switch at {path}: {type(exc).__name__}", file=sys.stderr)
            return 1
        _record_switch(ledger, "on", halt.by)
        print(f"kill switch ON: every action is denied until `agentctl resume` ({path})")
        return 0
    if args.command == "resume":
        try:
            was_on = clear_halt(path)
        except OSError as exc:
            print(f"cannot clear the kill switch at {path}: {type(exc).__name__}", file=sys.stderr)
            return 1
        if was_on:
            _record_switch(ledger, "off", args.by)
        print("kill switch OFF" if was_on else "kill switch was already off")
        return 0
    halt = read_halt(path)
    print(halt.describe() if halt else "kill switch is off")
    return 1 if halt else 0


def _tokens_command(args: argparse.Namespace, ledger: Ledger) -> int:
    if args.tokens_command == "report":
        if not ledger.path.exists():
            print(f"ledger not found: {ledger.path}", file=sys.stderr)
            return 2
        result = ledger.verify()
        if not result.ok:
            print(f"ledger fails verification: {result.error}", file=sys.stderr)
            return 1
        summary = summarize_tokens(ledger.records(), args.by)
        print(json.dumps(summary, indent=2) if args.json else render_tokens(summary))
        return 0
    try:
        prices = load_price_table(args.prices) if args.prices else None
        record = record_model_call(
            ledger,
            agent_id=args.agent,
            on_behalf_of=args.on_behalf_of,
            model=args.model,
            provider=args.provider,
            prompt_version=args.prompt_version,
            task_id=args.task,
            stage=args.stage,
            tokens_in=args.tokens_in,
            tokens_out=args.tokens_out,
            tokens_cached=args.tokens_cached,
            cost_usd=args.cost_usd,
            prices=prices,
            outcome=args.outcome,
        )
    except (ValidationError, ValueError, OSError, yaml.YAMLError) as exc:
        print(f"invalid input: {exc}", file=sys.stderr)
        return 2
    except LedgerError as exc:
        print(f"ledger error: {exc}", file=sys.stderr)
        return 1
    source = record["event"]["data"]["cost_source"]
    print(f"{record['seq']}:{record['hash']} cost {source}")
    return 0


def _load_action(source: str) -> Action:
    text = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    return Action.model_validate(yaml.safe_load(text))  # YAML is a superset of JSON


def _check_command(args: argparse.Namespace, ledger: Ledger) -> int:
    try:
        action = _load_action(args.action)
        policy = load_yaml_model(args.policy, Policy)
        registry = load_tool_registry(args.registry)
        project = load_yaml_model(args.project, ProjectConfig) if args.project else None
    except (ValidationError, ValueError, OSError, yaml.YAMLError) as exc:
        print(f"invalid input: {exc}", file=sys.stderr)
        return 2
    use_ledger = ledger if not (args.no_log and not ledger.path.exists()) else None
    try:
        verdict = run_check(
            action,
            policy,
            registry,
            project,
            ledger=use_ledger,
            record=not args.no_log,
            halt_file=_halt_file(args),
        )
    except Exception as exc:  # the gate must fail closed on any unexpected error
        print(f"internal error, denying: {exc!r}", file=sys.stderr)
        return CHECK_EXIT_CODES[Decision.DENY]
    print(verdict.model_dump_json(indent=2))
    return CHECK_EXIT_CODES[verdict.decision]


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "schema":
        for path in export_json_schemas(args.out):
            print(path)
        return 0

    if args.command == "hook":
        return run_hook_command(args)

    ledger = Ledger(_ledger_path(args.ledger))

    if args.command == "check":
        return _check_command(args, ledger)

    if args.command == "tokens":
        return _tokens_command(args, ledger)

    if args.command in ("halt", "resume", "status"):
        return _switch_command(args, ledger)

    if args.command == "approvals":
        return run_approvals_command(args, ledger)

    if args.command == "log":
        if args.kind in REFUSED_KINDS:
            print(
                "approvals are recorded with `agentctl approvals approve` or `reject`",
                file=sys.stderr,
            )
            return 2
        try:
            event = _build_event(args)
        except (ValidationError, ValueError) as exc:
            print(f"invalid event: {exc}", file=sys.stderr)
            return 2
        try:
            record = ledger.append(event)
        except LedgerError as exc:
            print(f"ledger error: {exc}", file=sys.stderr)
            return 1
        print(f"{record['seq']}:{record['hash']}")
        return 0

    if args.command == "verify":
        if not ledger.path.exists():
            print(f"ledger not found: {ledger.path}", file=sys.stderr)
            return 2
        result = ledger.verify(anchor=args.anchor)
        if result.ok:
            print(f"OK: {result.records} records, head {result.head_seq}:{result.head_hash}")
            return 0
        print(f"FAILED at seq {result.error_seq}: {result.error}", file=sys.stderr)
        return 1

    if args.command == "head":
        try:
            head = ledger.head()
        except LedgerError as exc:
            print(f"ledger error: {exc}", file=sys.stderr)
            return 1
        if head is None:
            print("ledger is empty", file=sys.stderr)
            return 1
        print(f"{head[0]}:{head[1]}")
        return 0

    return 2  # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())
