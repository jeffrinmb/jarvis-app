"""Real trace and span ids from the Claude Code hook, and the export that joins on them (P2.3a)."""

import json
import re
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from jarvis.adapters.claude_code import HookConfig, HookContext, HookMode, handle
from jarvis.core.ledger import Ledger
from jarvis.core.observability import agent_span_id_for, export_records, span_id_for, trace_id_for

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "claude-code"


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / ".agents").mkdir(parents=True)
    for name in ("policy.yaml", "tool-registry.yaml"):
        shutil.copy(EXAMPLES / name, root / ".agents" / name)
    return root


def ctx_for(project, tmp_path):
    return HookContext(HookConfig(on_behalf_of="alice"), HookMode.LOG_ONLY, project, tmp_path / "s")


def payload(project, event, session="s1", tool_use_id="toolu_1", **extra):
    base = {
        "hook_event_name": event,
        "session_id": session,
        "cwd": str(project),
        "permission_mode": "default",
        "tool_name": "Edit",
        "tool_input": {"file_path": str(project / "docs" / "a.md"), "old_string": "a"},
        **extra,
    }
    if tool_use_id:
        base["tool_use_id"] = tool_use_id
    return base


def records(project):
    return list(Ledger(project / ".agents" / "ledger" / "events.jsonl").records())


def events_of(project, kind):
    return [r["event"] for r in records(project) if r["event"]["kind"] == kind]


def attrs(span):
    out = {}
    for item in span["attributes"]:
        ((kind, value),) = item["value"].items()
        if kind == "arrayValue":
            value = [next(iter(v.values())) for v in value["values"]]
        out[item["key"]] = value
    return out


def exported(project):
    doc = export_records(records(project)).document
    return doc["resourceSpans"][0]["scopeSpans"][0]["spans"]


def test_a_decision_and_its_outcome_share_a_trace_and_a_span(project, tmp_path):
    ctx = ctx_for(project, tmp_path)
    handle(payload(project, "PreToolUse"), ctx)
    handle(payload(project, "PostToolUse", duration_ms=40), ctx)
    (decision,) = events_of(project, "policy_decision")
    (call,) = events_of(project, "tool_call")
    assert decision["trace_id"] == call["trace_id"] == trace_id_for("s1")
    assert decision["span_id"] == call["span_id"] == span_id_for("s1", "tool", "toolu_1")
    assert re.fullmatch(r"[0-9a-f]{32}", decision["trace_id"])
    assert re.fullmatch(r"[0-9a-f]{16}", decision["span_id"])


def test_different_calls_and_different_sessions_get_different_ids(project, tmp_path):
    ctx = ctx_for(project, tmp_path)
    handle(payload(project, "PreToolUse", tool_use_id="toolu_1"), ctx)
    handle(payload(project, "PreToolUse", tool_use_id="toolu_2"), ctx)
    handle(payload(project, "PreToolUse", session="s2", tool_use_id="toolu_1"), ctx)
    one, two, other = events_of(project, "policy_decision")
    assert one["trace_id"] == two["trace_id"] != other["trace_id"]
    assert len({one["span_id"], two["span_id"], other["span_id"]}) == 3


def test_without_a_tool_use_id_there_is_a_trace_but_no_span(project, tmp_path):
    handle(payload(project, "PreToolUse", tool_use_id=None), ctx_for(project, tmp_path))
    (decision,) = events_of(project, "policy_decision")
    assert decision["trace_id"] == trace_id_for("s1")
    assert decision["span_id"] is None


def test_an_unsafe_session_id_still_gives_valid_ids(project, tmp_path):
    handle(payload(project, "PreToolUse", session="../x y"), ctx_for(project, tmp_path))
    (decision,) = events_of(project, "policy_decision")
    assert re.fullmatch(r"[0-9a-f]{32}", decision["trace_id"])


def test_session_notes_carry_the_trace(project, tmp_path):
    handle(
        {"hook_event_name": "SessionStart", "session_id": "s1", "cwd": str(project)},
        ctx_for(project, tmp_path),
    )
    (note,) = events_of(project, "note")
    assert note["trace_id"] == trace_id_for("s1")


def test_model_calls_join_the_session_trace_with_their_own_span(project, tmp_path):
    stamp = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    message = {
        "id": "msg_1",
        "model": "example-model",
        "usage": {"input_tokens": 1, "output_tokens": 2},
    }
    line = {"type": "assistant", "timestamp": stamp, "isSidechain": False, "message": message}
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps(line) + "\n", encoding="utf-8")
    stop = {
        "hook_event_name": "Stop",
        "session_id": "s1",
        "cwd": str(project),
        "transcript_path": str(transcript),
    }
    handle(stop, ctx_for(project, tmp_path))
    (call,) = events_of(project, "model_call")
    assert call["trace_id"] == trace_id_for("s1")
    assert call["span_id"] == span_id_for("s1", "model", "msg_1")


def test_the_export_folds_a_decision_and_its_outcome_into_one_real_span(project, tmp_path):
    ctx = ctx_for(project, tmp_path)
    handle(payload(project, "PreToolUse"), ctx)
    handle(payload(project, "PostToolUse", duration_ms=40), ctx)
    handle(payload(project, "PreToolUse", tool_use_id="toolu_2"), ctx)
    handle(payload(project, "PostToolUseFailure", tool_use_id="toolu_2"), ctx)

    spans = exported(project)
    agent = next(s for s in spans if s["name"].startswith("invoke_agent"))
    tools = [s for s in spans if s["name"].startswith("execute_tool")]
    assert len(spans) == 3  # one agent span and two calls, not four
    assert {s["traceId"] for s in spans} == {trace_id_for("s1")}
    assert agent["spanId"] == agent_span_id_for(trace_id_for("s1"))
    assert {t["parentSpanId"] for t in tools} == {agent["spanId"]}
    first, second = sorted(tools, key=lambda s: s["spanId"] != span_id_for("s1", "tool", "toolu_1"))
    assert first["spanId"] == span_id_for("s1", "tool", "toolu_1")
    a = attrs(first)
    assert a["jarvis.event_kind"] == ["policy_decision", "tool_call"]
    assert len(a["jarvis.ledger.seq"]) == 2
    assert a["jarvis.decision"] == "allow"
    assert a["jarvis.outcome"] == "ok"  # the outcome wins over the decision
    assert "jarvis.ids_derived" not in a
    assert "status" not in first
    assert int(first["endTimeUnixNano"]) > int(first["startTimeUnixNano"])
    assert second["status"] == {"code": 2}  # the failed call
    assert attrs(second)["jarvis.outcome"] == "failed"


def test_a_denied_decision_stays_an_error_after_it_is_joined_to_a_later_event(project, tmp_path):
    ctx = ctx_for(project, tmp_path)
    handle(payload(project, "PreToolUse", tool_name="Nonexistent"), ctx)  # unregistered: deny
    handle(payload(project, "PostToolUse", tool_name="Nonexistent"), ctx)
    (decision,) = events_of(project, "policy_decision")
    assert decision["decision"] == "deny"
    tools = [s for s in exported(project) if s["name"].startswith("execute_tool")]
    assert len(tools) == 1
    assert tools[0]["status"] == {"code": 2}


def test_rows_written_before_ids_existed_join_the_same_trace(project, tmp_path):
    ctx = ctx_for(project, tmp_path)
    handle(payload(project, "PreToolUse"), ctx)
    legacy = {
        "event_id": "c" * 32,
        "ts": datetime.now(UTC).isoformat(),
        "kind": "model_call",
        "agent_id": "claude-code",
        "on_behalf_of": "alice",
        "task_id": "claude-s1",
        "model": {"name": "m1", "provider": "anthropic"},
        "usage": {"tokens_in": 1, "tokens_out": 1, "tokens_cached": 0, "cost_usd": 0.0},
        "data": {"session_id": "s1"},
    }
    rows = records(project) + [{"seq": 99, "event": legacy}]
    spans = export_records(rows).document["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert {s["traceId"] for s in spans} == {trace_id_for("s1")}
    chat = next(s for s in spans if s["name"] == "chat m1")
    assert attrs(chat)["jarvis.ids_derived"] is True
    assert sum(s["name"].startswith("invoke_agent") for s in spans) == 1
