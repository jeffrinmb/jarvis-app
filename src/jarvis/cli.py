"""`agentctl`: command line for the ledger and schemas.

Exit codes: 0 success, 1 verification failed or ledger problem, 2 bad input.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from pydantic import ValidationError

from jarvis.core.ledger import Ledger, LedgerError
from jarvis.core.schemas import (
    Decision,
    Event,
    EventKind,
    RiskClass,
    TrustLabel,
    export_json_schemas,
)
from jarvis.core.schemas.event import ModelRef, ToolRef, Usage

DEFAULT_LEDGER = ".agents/ledger/events.jsonl"


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

    schema = sub.add_parser("schema", help="schema tools")
    schema_sub = schema.add_subparsers(dest="schema_command", required=True)
    export = schema_sub.add_parser("export", help="write JSON Schema files")
    export.add_argument("--out", default="schemas")

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


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "schema":
        for path in export_json_schemas(args.out):
            print(path)
        return 0

    ledger = Ledger(_ledger_path(args.ledger))

    if args.command == "log":
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
