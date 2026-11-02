"""OpenTelemetry GenAI export (P2.3, ADR 0011)."""

import json
import re

from jarvis.cli import main
from jarvis.core.ledger import Ledger
from jarvis.core.observability import SEMCONV_COMMIT, export_records
from jarvis.core.schemas import Decision, Event, EventKind
from jarvis.core.schemas.event import ModelRef, ToolRef, Usage
from jarvis.core.tokens import model_call_event

SESSION = "sess1"


def model_call(**over):
    base = {
        "agent_id": "agent",
        "on_behalf_of": "alice",
        "model": "m1",
        "provider": "anthropic",
        "tokens_in": 1000,
        "tokens_out": 500,
        "tokens_cached": 4000,
        "cost_usd": 0.5,
        "task_id": f"claude-{SESSION}",
        "extra_data": {"session_id": SESSION, "cache_creation_tokens": 200},
    }
    return model_call_event(**{**base, **over})


def tool_call(outcome="ok", duration_ms=250):
    return Event(
        kind=EventKind.TOOL_CALL,
        agent_id="agent",
        on_behalf_of="alice",
        task_id=f"claude-{SESSION}",
        tool=ToolRef(id="file.read"),
        outcome=outcome,
        data={"context": {"session_id": SESSION}, "duration_ms": duration_ms},
    )


def decision(rule="r1"):
    return Event(
        kind=EventKind.POLICY_DECISION,
        agent_id="agent",
        on_behalf_of="alice",
        task_id=f"claude-{SESSION}",
        tool=ToolRef(id="shell.run"),
        decision=Decision.DENY,
        outcome="deny",
        data={
            "reason": "SECRET REASON TEXT",
            "paths": ["/home/alice/secret.txt"],
            "matches": [{"rule_id": rule, "category": "c", "effect": "deny"}],
            "context": {"session_id": SESSION},
        },
    )


def build(tmp_path, *events):
    ledger = Ledger(tmp_path / "events.jsonl")
    for event in events:
        ledger.append(event)
    return ledger


def attrs(span):
    out = {}
    for item in span["attributes"]:
        ((kind, value),) = item["value"].items()
        out[item["key"]] = int(value) if kind == "intValue" else value
    return out


def spans_of(ledger):
    doc = export_records(ledger.records()).document
    return doc, doc["resourceSpans"][0]["scopeSpans"][0]["spans"]


def test_model_call_maps_to_a_chat_span_with_cached_tokens_added_back(tmp_path):
    _, spans = spans_of(build(tmp_path, model_call()))
    chat = next(s for s in spans if s["name"] == "chat m1")
    a = attrs(chat)
    assert a["gen_ai.operation.name"] == "chat"
    assert a["gen_ai.provider.name"] == "anthropic"
    assert a["gen_ai.request.model"] == "m1"
    assert a["gen_ai.usage.input_tokens"] == 5000  # tokens_in 1000 + tokens_cached 4000
    assert a["gen_ai.usage.cache_read.input_tokens"] == 4000
    assert a["gen_ai.usage.cache_write.input_tokens"] == 200
    assert a["gen_ai.usage.output_tokens"] == 500
    assert a["gen_ai.conversation.id"] == SESSION
    assert chat["kind"] == 3


def test_tool_call_maps_to_execute_tool_with_duration(tmp_path):
    _, spans = spans_of(build(tmp_path, tool_call()))
    span = next(s for s in spans if s["name"] == "execute_tool file.read")
    assert attrs(span)["gen_ai.tool.name"] == "file.read"
    elapsed = int(span["endTimeUnixNano"]) - int(span["startTimeUnixNano"])
    assert elapsed == 250 * 1_000_000
    assert "status" not in span


def test_failed_tool_call_and_denied_decision_are_errors_without_a_message(tmp_path):
    _, spans = spans_of(build(tmp_path, tool_call("failed"), decision()))
    for span in spans:
        if span["name"].startswith("invoke_agent"):
            continue
        assert span["status"] == {"code": 2}


def test_policy_decision_carries_rule_ids_and_no_reason_or_paths(tmp_path):
    doc, spans = spans_of(build(tmp_path, decision("rule-x")))
    span = next(s for s in spans if s["name"].startswith("jarvis.policy_decision"))
    assert attrs(span)["jarvis.decision"] == "deny"
    text = json.dumps(doc)
    assert "rule-x" in text
    assert "SECRET REASON TEXT" not in text
    assert "secret.txt" not in text


def test_session_shares_one_trace_under_a_synthesised_agent_span(tmp_path):
    _, spans = spans_of(build(tmp_path, model_call(), tool_call(), decision()))
    agent = next(s for s in spans if s["name"] == "invoke_agent agent")
    children = [s for s in spans if s is not agent]
    assert len(children) == 3
    assert {s["traceId"] for s in spans} == {agent["traceId"]}
    assert {s["parentSpanId"] for s in children} == {agent["spanId"]}
    assert re.fullmatch(r"[0-9a-f]{32}", agent["traceId"])
    assert all(re.fullmatch(r"[0-9a-f]{16}", s["spanId"]) for s in spans)
    assert len({s["spanId"] for s in spans}) == len(spans)
    assert attrs(agent)["gen_ai.agent.name"] == "agent"
    assert attrs(agent)["jarvis.span_synthesised"] is True


def test_ledger_trace_and_span_ids_are_used_when_present(tmp_path):
    event = model_call(trace_id="a" * 32, span_id="b" * 16)
    _, spans = spans_of(build(tmp_path, event))
    chat = next(s for s in spans if s["name"] == "chat m1")
    assert chat["traceId"] == "a" * 32
    assert chat["spanId"] == "b" * 16
    assert "jarvis.ids_derived" not in attrs(chat)


def test_derived_ids_are_stable_and_marked(tmp_path):
    ledger = build(tmp_path, model_call())
    first = export_records(ledger.records()).document
    second = export_records(ledger.records()).document
    assert first == second
    spans = first["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert attrs(next(s for s in spans if s["name"] == "chat m1"))["jarvis.ids_derived"] is True


def test_other_kinds_are_counted_as_skipped(tmp_path):
    note = Event(kind=EventKind.NOTE, agent_id="a", on_behalf_of="alice", data={"x": 1})
    result = export_records(build(tmp_path, note, model_call()).records())
    assert result.skipped == 1
    assert result.sessions == 1


def test_no_content_leaks_from_free_text_fields(tmp_path):
    event = Event(
        kind=EventKind.MODEL_CALL,
        agent_id="agent",
        on_behalf_of="alice",
        model=ModelRef(name="m1", provider="anthropic", prompt_version="v1"),
        usage=Usage(tokens_in=1, tokens_out=1),
        outcome="ok",
        data={"prompt": "TOP SECRET PROMPT", "message_id": "msg_123", "cost_source": "reported"},
    )
    doc, _ = spans_of(build(tmp_path, event))
    text = json.dumps(doc)
    assert "TOP SECRET PROMPT" not in text
    assert "msg_123" not in text


def test_resource_pins_the_conventions_commit(tmp_path):
    doc, _ = spans_of(build(tmp_path, model_call()))
    resource = {a["key"]: a["value"] for a in doc["resourceSpans"][0]["resource"]["attributes"]}
    assert resource["jarvis.semconv.commit"] == {"stringValue": SEMCONV_COMMIT}
    assert re.fullmatch(r"[0-9a-f]{40}", SEMCONV_COMMIT)
    assert resource["jarvis.content_exported"] == {"boolValue": False}


def test_cli_exports_a_json_line_and_refuses_a_broken_ledger(tmp_path, capsys):
    ledger = build(tmp_path, model_call(), tool_call())
    out = tmp_path / "out" / "traces.json"
    assert main(["otel", "export", "--ledger", str(ledger.path), "--out", str(out)]) == 0
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["resourceSpans"]
    assert "3 spans in 1 sessions" in capsys.readouterr().out

    ledger.path.write_text(ledger.path.read_text(encoding="utf-8").replace("m1", "m2", 1))
    assert main(["otel", "export", "--ledger", str(ledger.path), "--out", str(out)]) == 1


def test_cli_missing_ledger_is_bad_input(tmp_path):
    missing = tmp_path / "none.jsonl"
    assert main(["otel", "export", "--ledger", str(missing), "--out", str(tmp_path / "o")]) == 2
