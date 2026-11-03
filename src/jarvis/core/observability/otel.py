"""Export the ledger as OpenTelemetry GenAI traces in OTLP/JSON (task P2.3, ADR 0011).

This is the only module that knows the OpenTelemetry GenAI attribute names, which are in
Development status and can change. ``SEMCONV_COMMIT`` names the commit of
``open-telemetry/semantic-conventions-genai`` whose registry and span definitions the names
and span naming below were checked against. Our ledger schema stays the source of truth; this
is an adapter that reads it and never writes to it.

**Content is never exported.** Only an allow-list of fields is read from an event: model and
tool names, token counts, cost, decisions, rule ids, ids and timestamps. No prompt or answer
text, no command text, no file paths or contents, and no free-text reason or error message.

Ids: an event's own ``trace_id`` and ``span_id`` are used when it has them. The Claude Code hook
sets both (task P2.3a): one trace per session, and for a tool call one span id shared by the
gate's decision and the call's outcome, which are then folded into one ``execute_tool`` span.
Events without ids (older rows, ``agentctl log``) get them derived from values already in the
ledger (the session id, falling back to the task id, for the trace; the event id for the span)
and are marked ``jarvis.ids_derived``. The derivation matches the hook's, so a session that
straddles the change stays in one trace. Every trace sits under one ``invoke_agent`` span that
this module synthesises for the session.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .ids import agent_span_id_for, span_id_for, trace_id_for

SEMCONV_REPO = "open-telemetry/semantic-conventions-genai"
SEMCONV_COMMIT = "16f3130892e2e02fa3f0eef72c0e6b554ff08e5c"
SCOPE_NAME = "jarvis.core.observability"
SCOPE_VERSION = "1"
SERVICE_NAME = "jarvis-agents"

# OTLP span kinds.
KIND_INTERNAL = 1
KIND_CLIENT = 3
# OTLP status codes. No message is ever set, because error text is not recorded.
STATUS_OK = 1
STATUS_ERROR = 2

EXPORTED_KINDS = ("model_call", "tool_call", "tool_denied", "policy_decision")
FAILED_OUTCOMES = {"failed", "error", "deny"}


def _attr(key: str, value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        encoded: dict[str, Any] = {"boolValue": value}
    elif isinstance(value, int):
        encoded = {"intValue": str(value)}  # OTLP/JSON carries 64-bit integers as strings
    elif isinstance(value, float):
        encoded = {"doubleValue": value}
    elif isinstance(value, list):
        encoded = {"arrayValue": {"values": [_attr("", v)["value"] for v in value]}}
    else:
        encoded = {"stringValue": str(value)}
    return {"key": key, "value": encoded}


def _attributes(values: dict[str, Any]) -> list[dict[str, Any]]:
    return [_attr(k, v) for k, v in values.items() if v is not None]


def _nanos(moment: datetime) -> str:
    delta = moment.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    return str((delta.days * 86400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1000)


def _session_of(event: dict[str, Any]) -> str | None:
    data = event.get("data") or {}
    context = data.get("context") if isinstance(data.get("context"), dict) else {}
    value = data.get("session_id") or context.get("session_id")
    return str(value) if value else None


def _trace_of(event: dict[str, Any]) -> str:
    """The event's own trace id, or the one the hook would have given its session. They agree
    for an ordinary session id, so rows written before the hook set ids join the same trace."""
    own = event.get("trace_id")
    if own:
        return own
    key = _session_of(event) or event.get("task_id") or f"agent:{event.get('agent_id')}"
    return trace_id_for(key)


@dataclass
class _Session:
    trace_id: str
    span_id: str
    conversation_id: str | None
    agent: str
    start: datetime
    end: datetime
    spans: list[dict[str, Any]] = field(default_factory=list)
    by_id: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class ExportResult:
    document: dict[str, Any]
    spans: int
    sessions: int
    skipped: int  # events whose kind is not exported


def _tool_name(event: dict[str, Any]) -> str | None:
    tool = event.get("tool")
    return tool.get("id") if isinstance(tool, dict) else None


def _span_for(
    event: dict[str, Any], seq: int, session: _Session, when: datetime
) -> tuple[dict[str, Any], datetime] | None:
    kind = event["kind"]
    usage = event.get("usage") or {}
    model = event.get("model") or {}
    data = event.get("data") or {}
    outcome = event.get("outcome")
    common = {
        # Lists, because one span can join several events: a gate decision and its outcome.
        "jarvis.ledger.seq": [seq],
        "jarvis.event_id": [event["event_id"]],
        "jarvis.event_kind": [kind],
        "jarvis.task_id": event.get("task_id"),
        "jarvis.stage": event.get("stage"),
        "jarvis.outcome": outcome,
    }
    start = end = when
    if kind == "model_call":
        name = model.get("name")
        if not name:
            return None
        cached = usage.get("tokens_cached", 0)
        attrs = {
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": model.get("provider"),
            "gen_ai.request.model": name,
            "gen_ai.conversation.id": session.conversation_id,
            # Ours excludes cached tokens; OpenTelemetry's includes them (ADR 0011).
            "gen_ai.usage.input_tokens": usage.get("tokens_in", 0) + cached,
            "gen_ai.usage.output_tokens": usage.get("tokens_out", 0),
            "gen_ai.usage.cache_read.input_tokens": cached,
            "gen_ai.usage.cache_write.input_tokens": data.get("cache_creation_tokens"),
            "jarvis.cost_usd": usage.get("cost_usd"),
            "jarvis.cost_source": data.get("cost_source"),
        }
        span_name, span_kind = f"chat {name}", KIND_CLIENT
    elif kind in ("tool_call", "tool_denied"):
        tool = _tool_name(event)
        if not tool:
            return None
        attrs = {
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": tool,
            "gen_ai.tool.type": "function",
            "gen_ai.conversation.id": session.conversation_id,
            "jarvis.duration_ms": data.get("duration_ms"),
        }
        duration = data.get("duration_ms")
        if isinstance(duration, int | float) and not isinstance(duration, bool) and duration > 0:
            start = when - timedelta(milliseconds=min(duration, 86_400_000))
        span_name, span_kind = f"execute_tool {tool}", KIND_INTERNAL
    elif kind == "policy_decision":
        tool = _tool_name(event) or "unknown"
        matches = data.get("matches") if isinstance(data.get("matches"), list) else []
        attrs = {
            "gen_ai.tool.name": tool,
            "gen_ai.conversation.id": session.conversation_id,
            "jarvis.decision": event.get("decision"),
            "jarvis.risk": event.get("risk"),
            "jarvis.tainted": event.get("tainted"),
            "jarvis.policy_version": data.get("policy_version"),
            "jarvis.rule_ids": [m["rule_id"] for m in matches if isinstance(m, dict)],
            "jarvis.fingerprint": data.get("fingerprint"),
        }
        if event.get("span_id"):
            # The decision gates a real call that has its own span, shared with the call's
            # outcome (P2.3a): it is that call's span, so it is named for the call.
            attrs["gen_ai.operation.name"] = "execute_tool"
            attrs["gen_ai.tool.type"] = "function"
            span_name, span_kind = f"execute_tool {tool}", KIND_INTERNAL
        else:
            span_name, span_kind = f"jarvis.policy_decision {tool}", KIND_INTERNAL
    else:
        return None

    own_span = event.get("span_id")
    derived = not (event.get("trace_id") and own_span)
    span = {
        "traceId": session.trace_id,
        "spanId": own_span or span_id_for("event", event["event_id"]),
        "parentSpanId": session.span_id,
        "name": span_name,
        "kind": span_kind,
        "startTimeUnixNano": start,
        "endTimeUnixNano": end,
        "attributes": {**attrs, **common, "jarvis.ids_derived": derived or None},
    }
    if outcome in FAILED_OUTCOMES or event.get("decision") == "deny" or kind == "tool_denied":
        span["status"] = {"code": STATUS_ERROR}
    return span, start


_JOINED = ("jarvis.ledger.seq", "jarvis.event_id", "jarvis.event_kind")


def _merge(into: dict[str, Any], other: dict[str, Any]) -> None:
    """Fold a later event of the same span into the first. Where both set an attribute the later
    one wins (the outcome over the decision), the identifying lists are joined, the span covers
    both times, and an error on either makes the span an error."""
    a, b = into["attributes"], other["attributes"]
    joined = {key: a.get(key, []) + b.get(key, []) for key in _JOINED}
    a.update({k: v for k, v in b.items() if v is not None})
    a.update(joined)
    into["startTimeUnixNano"] = min(into["startTimeUnixNano"], other["startTimeUnixNano"])
    into["endTimeUnixNano"] = max(into["endTimeUnixNano"], other["endTimeUnixNano"])
    if "status" in other:
        into["status"] = other["status"]


def _finish(span: dict[str, Any]) -> dict[str, Any]:
    """Encode the times and attributes once all events of the span are folded in."""
    return {
        **span,
        "startTimeUnixNano": _nanos(span["startTimeUnixNano"]),
        "endTimeUnixNano": _nanos(span["endTimeUnixNano"]),
        "attributes": _attributes(span["attributes"]),
    }


def _agent_span(session: _Session) -> dict[str, Any]:
    return {
        "traceId": session.trace_id,
        "spanId": session.span_id,
        "name": f"invoke_agent {session.agent}",
        "kind": KIND_INTERNAL,
        "startTimeUnixNano": _nanos(session.start),
        "endTimeUnixNano": _nanos(session.end),
        "attributes": _attributes(
            {
                "gen_ai.operation.name": "invoke_agent",
                "gen_ai.agent.name": session.agent,
                "gen_ai.conversation.id": session.conversation_id,
                "jarvis.span_synthesised": True,
            }
        ),
        "status": {"code": STATUS_OK},
    }


def export_records(records: Iterable[dict[str, Any]]) -> ExportResult:
    """Turn ledger records into one OTLP/JSON ``ExportTraceServiceRequest`` document."""
    sessions: dict[str, _Session] = {}
    skipped = 0
    for record in records:
        event = record["event"]
        if event["kind"] not in EXPORTED_KINDS:
            skipped += 1
            continue
        when = datetime.fromisoformat(event["ts"])
        trace = _trace_of(event)
        session = sessions.get(trace)
        if session is None:
            session = sessions[trace] = _Session(
                trace_id=trace,
                span_id=agent_span_id_for(trace),
                conversation_id=_session_of(event),
                agent=str(event.get("agent_id")),
                start=when,
                end=when,
            )
        built = _span_for(event, record["seq"], session, when)
        if built is None:
            skipped += 1
            continue
        span, start = built
        earlier = session.by_id.get(span["spanId"])
        if earlier is not None:
            _merge(earlier, span)
        else:
            session.by_id[span["spanId"]] = span
            session.spans.append(span)
        session.start = min(session.start, start)
        session.end = max(session.end, when)

    spans = []
    for session in sessions.values():
        if session.spans:
            spans.append(_agent_span(session))
            spans.extend(_finish(s) for s in session.spans)
    document = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": _attributes(
                        {
                            "service.name": SERVICE_NAME,
                            "jarvis.semconv.repo": SEMCONV_REPO,
                            "jarvis.semconv.commit": SEMCONV_COMMIT,
                            "jarvis.content_exported": False,
                        }
                    )
                },
                "scopeSpans": [
                    {"scope": {"name": SCOPE_NAME, "version": SCOPE_VERSION}, "spans": spans}
                ],
            }
        ]
    }
    return ExportResult(
        document=document,
        spans=len(spans),
        sessions=sum(1 for s in sessions.values() if s.spans),
        skipped=skipped,
    )


def write_export(document: dict[str, Any], out: str | Path) -> Path:
    """Write the document as one JSON line (the OTLP file format), replacing ``out`` atomically."""
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(document, separators=(",", ":"), ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path
