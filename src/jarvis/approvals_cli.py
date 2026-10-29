"""``agentctl approvals``: answer the requests the gate raised (task P2.7, ADR 0009).

An approval is a named person's decision about a bounded scope that expires. The command line
checks what it can, but the **gate re-checks everything when it counts a grant**, so a grant
written by hand outside these rules does not count.

Until identity is authenticated (task P2.9a) the name given with ``--by`` is a claim. An agent
that can run this command could make that claim, which is why shell commands stay human-gated
and why the policy's ``approvers`` list should be set.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml
from pydantic import ValidationError

from jarvis.core.approvals import APPROVAL_AGENT, ApprovalState, is_agent_name, scan_approvals
from jarvis.core.ledger import Ledger, LedgerError
from jarvis.core.schemas import Event, EventKind, Policy, load_yaml_model
from jarvis.core.schemas.approval import ApprovalGrant, ApprovalScope
from jarvis.core.schemas.policy import ApprovalSettings

DEFAULT_POLICY = ".agents/policy.yaml"
DEFAULT_HOURS = 4
REFUSED_KINDS = (EventKind.APPROVAL_GRANTED.value, EventKind.APPROVAL_DENIED.value)


def add_approvals_parser(sub: argparse._SubParsersAction) -> None:
    approvals = sub.add_parser("approvals", help="list, approve or reject approval requests")
    asub = approvals.add_subparsers(dest="approvals_command", required=True)

    def common(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--ledger")
        parser.add_argument("--policy", default=DEFAULT_POLICY, help=f"default: {DEFAULT_POLICY}")

    listing = asub.add_parser("list", help="requests that still need an answer")
    common(listing)
    listing.add_argument("--all", action="store_true", help="include answered requests")
    listing.add_argument("--json", action="store_true")

    show = asub.add_parser("show", help="one request and where it stands")
    common(show)
    show.add_argument("request")

    approve = asub.add_parser("approve", help="approve a request for a bounded scope")
    common(approve)
    approve.add_argument("request")
    approve.add_argument("--by", help="who is approving (default: $JARVIS_ON_BEHALF_OF, then OS)")
    approve.add_argument("--paths", nargs="+", help="path globs; default: what was requested")
    approve.add_argument("--max-diff-lines", type=int, help="default: what was requested")
    approve.add_argument(
        "--hours",
        type=int,
        default=None,
        help=f"validity, default {DEFAULT_HOURS}, capped by policy",
    )

    reject = asub.add_parser("reject", help="reject a request, and revoke approvals already given")
    common(reject)
    reject.add_argument("request")
    reject.add_argument("--by")


def _who(by: str | None) -> str:
    if by:
        return by
    if os.environ.get("JARVIS_ON_BEHALF_OF"):
        return os.environ["JARVIS_ON_BEHALF_OF"]
    try:
        return os.getlogin()
    except OSError:
        return os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"


def _settings(path: str) -> ApprovalSettings:
    if not Path(path).exists():
        return ApprovalSettings()
    return load_yaml_model(path, Policy).approvals


def _status(state: ApprovalState, request_id: str) -> tuple[str, int, int]:
    request = state.requests[request_id]
    needed = 2 if request.four_eyes else 1
    approvers = {g.approver for g in state.grants if g.request_id == request_id}
    if len(approvers) >= needed:
        return "approved", len(approvers), needed
    if approvers:
        return "partly approved", len(approvers), needed
    if request_id in state.rejected:
        return "rejected", 0, needed
    return "open", 0, needed


def _describe(state: ApprovalState, request_id: str) -> dict:
    request = state.requests[request_id]
    status, have, needed = _status(state, request_id)
    return {
        "request": request_id,
        "status": status,
        "approvers": f"{have} of {needed}",
        "task": request.task_id,
        "tool": request.tool_id,
        "paths": request.paths[:5],
        "size": f"{request.total_diff_lines} lines" if request.diff_size_known else "unknown size",
        "rules": [r.rule_id for r in request.rules],
        "tainted": request.tainted,
        "four_eyes": request.four_eyes,
    }


def run_approvals_command(args: argparse.Namespace, ledger: Ledger) -> int:
    try:
        settings = _settings(args.policy)
    except (ValidationError, ValueError, OSError, yaml.YAMLError) as exc:
        print(f"cannot read the policy {args.policy}: {type(exc).__name__}", file=sys.stderr)
        return 2
    if not ledger.path.exists():
        print(f"ledger not found: {ledger.path}", file=sys.stderr)
        return 2
    now = datetime.now(UTC)
    try:
        state = scan_approvals(ledger.records(), now, settings)
    except LedgerError as exc:
        print(f"ledger error: {exc}", file=sys.stderr)
        return 1

    command = args.approvals_command
    if command == "list":
        rows = [_describe(state, rid) for rid in state.requests]
        if not args.all:
            rows = [r for r in rows if r["status"] in ("open", "partly approved")]
        if args.json:
            print(json.dumps(rows, indent=2))
        elif not rows:
            print("No requests waiting.")
        for r in rows if not args.json else []:
            print(
                f"{r['request']}  {r['status']} ({r['approvers']})  {r['tool']}  "
                f"{', '.join(r['paths']) or '-'}  {r['size']}"
                + ("  FOUR-EYES" if r["four_eyes"] else "")
                + ("  TAINTED" if r["tainted"] else "")
            )
        return 0

    if args.request not in state.requests:
        print(f"unknown request {args.request}", file=sys.stderr)
        return 2
    request = state.requests[args.request]

    if command == "show":
        print(
            json.dumps(
                {**_describe(state, args.request), "detail": request.model_dump(mode="json")},
                indent=2,
            )
        )
        return 0

    approver = _who(args.by)
    agent = state.request_agents.get(args.request, "")
    if agent and is_agent_name(approver, agent):
        print(
            f"'{approver}' is the agent that raised this request; a person must answer",
            file=sys.stderr,
        )
        return 2
    if settings.approvers and approver not in settings.approvers:
        print(f"'{approver}' is not in the policy's list of approvers", file=sys.stderr)
        return 2

    if command == "reject":
        event = Event(
            kind=EventKind.APPROVAL_DENIED,
            agent_id=APPROVAL_AGENT,
            on_behalf_of=approver,
            task_id=request.task_id,
            outcome="rejected",
            data={"request_id": args.request, "approver": approver},
        )
        message = f"rejected {args.request}; approvals already given for it are revoked"
    else:
        built = _build_grant(args, request, approver, settings, now)
        if isinstance(built, str):
            print(built, file=sys.stderr)
            return 2
        event = Event(
            kind=EventKind.APPROVAL_GRANTED,
            agent_id=APPROVAL_AGENT,
            on_behalf_of=approver,
            task_id=request.task_id,
            outcome="granted",
            data={"grant": built.model_dump(mode="json")},
        )
        message = f"approved {args.request} until {built.scope.expires_at.isoformat()}"
    try:
        record = ledger.append(event)
    except LedgerError as exc:
        print(f"ledger error: {exc}", file=sys.stderr)
        return 1
    print(f"{message} ({record['seq']}:{record['hash'][:12]})")
    return 0


def _build_grant(args, request, approver: str, settings: ApprovalSettings, now: datetime):
    """The grant to record, or a message saying why not."""
    proposed = request.proposed_scope
    if request.task_id is None:
        return "this request has no task, so it cannot be approved for one"
    exact = proposed.single_use
    if exact and (args.paths or args.max_diff_lines):
        return "this tool is high risk or irreversible: only the exact call can be approved"
    hours = args.hours if args.hours is not None else min(DEFAULT_HOURS, settings.max_scope_hours)
    if not 1 <= hours <= settings.max_scope_hours:
        return f"--hours must be between 1 and {settings.max_scope_hours} (the policy's limit)"
    try:
        scope = ApprovalScope(
            task_id=request.task_id,
            tools=proposed.tools,
            paths=args.paths if args.paths else proposed.paths,
            max_diff_lines=args.max_diff_lines or proposed.max_diff_lines,
            fingerprint=proposed.fingerprint,
            single_use=proposed.single_use,
            expires_at=now + timedelta(hours=hours),
        )
        return ApprovalGrant(
            request_id=request.request_id, approver=approver, via="cli", scope=scope
        )
    except ValidationError as exc:
        return "invalid scope: " + "; ".join(e["msg"] for e in exc.errors())
