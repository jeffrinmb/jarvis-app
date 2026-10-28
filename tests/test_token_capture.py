"""Capturing model-call usage from a Claude Code transcript (P2.2a)."""

import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from jarvis.adapters.claude_code import HookConfig, HookContext, HookMode, handle
from jarvis.adapters.claude_code.source import DirectorySource
from jarvis.adapters.claude_code.transcript import MAX_COUNT, read_transcript_usage
from jarvis.core.ledger import Ledger
from jarvis.core.tokens import summarize_tokens

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "claude-code"
PRICES = Path(__file__).resolve().parent.parent / "examples" / "prices.yaml"


def record(message_id="msg_1", model="example-model", usage=None, ts=None, **over):
    usage = {"input_tokens": 10, "output_tokens": 20, **(usage or {})}
    base = {
        "type": "assistant",
        "timestamp": ts or (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
        "isSidechain": False,
        "message": {"id": message_id, "model": model, "usage": usage, "content": "SECRET TEXT"},
    }
    return {**base, **over}


def write(path, *records, raw=()):
    lines = [json.dumps(r) for r in records] + list(raw)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / ".agents").mkdir(parents=True)
    for name in ("policy.yaml", "tool-registry.yaml"):
        shutil.copy(EXAMPLES / name, root / ".agents" / name)
    shutil.copy(PRICES, root / ".agents" / "prices.yaml")
    return root


def ctx_for(project, tmp_path, mode=HookMode.LOG_ONLY, **cfg):
    source = DirectorySource(project) if mode is HookMode.ENFORCE else None
    config = HookConfig(on_behalf_of="alice", **cfg)
    return HookContext(config, mode, project, tmp_path / "state", source)


def stop(project, transcript, event="Stop", session="s1"):
    return {
        "hook_event_name": event,
        "session_id": session,
        "cwd": str(project),
        "transcript_path": str(transcript),
    }


def rows(project):
    ledger = Ledger(project / ".agents" / "ledger" / "events.jsonl")
    return [r["event"] for r in ledger.records() if r["event"]["kind"] == "model_call"]


# ---------- the reader ----------


def test_one_row_per_message_even_when_a_message_is_split_into_several_records(tmp_path):
    same = record(usage={"output_tokens": 500})
    path = write(tmp_path / "t.jsonl", same, same, same, record("msg_2"))
    result = read_transcript_usage(path)
    assert [m.message_id for m in result.messages] == ["msg_1", "msg_2"]
    assert result.messages[0].output_tokens == 500


def test_the_most_complete_streaming_record_wins(tmp_path):
    early = record(usage={"output_tokens": 1})
    final = record(usage={"output_tokens": 800})
    path = write(tmp_path / "t.jsonl", early, final, early)
    assert read_transcript_usage(path).messages[0].output_tokens == 800


def test_cache_fields_are_read_and_default_to_zero(tmp_path):
    full = record(usage={"cache_read_input_tokens": 7, "cache_creation_input_tokens": 3})
    (m,) = read_transcript_usage(write(tmp_path / "t.jsonl", full)).messages
    assert (m.cache_read_tokens, m.cache_creation_tokens) == (7, 3)
    (m,) = read_transcript_usage(write(tmp_path / "u.jsonl", record())).messages
    assert (m.cache_read_tokens, m.cache_creation_tokens) == (0, 0)


@pytest.mark.parametrize(
    "bad",
    [
        record(usage={"input_tokens": -1}),
        record(usage={"output_tokens": "20"}),
        record(usage={"output_tokens": True}),
        record(usage={"output_tokens": MAX_COUNT + 1}),
        record(usage={"input_tokens": 1.5}),
        record(model="<synthetic>"),
        record(model="x" * 200),
        record(message_id="has space"),
        record(message_id=""),
    ],
)
def test_untrustworthy_records_are_dropped_not_guessed(tmp_path, bad):
    result = read_transcript_usage(write(tmp_path / "t.jsonl", bad, record("msg_ok")))
    assert [m.message_id for m in result.messages] == ["msg_ok"]
    assert result.skipped_records == 1


def test_other_record_types_and_broken_lines_are_ignored(tmp_path):
    user = {"type": "user", "message": {"usage": {"input_tokens": 99, "output_tokens": 99}}}
    path = write(tmp_path / "t.jsonl", user, record(), raw=['{ not json "usage"', "[1, 2]"])
    result = read_transcript_usage(path)
    assert len(result.messages) == 1 and result.skipped_lines == 1


@pytest.mark.parametrize("name", ["t.json", "t.txt", "t"])
def test_only_jsonl_files_are_read(tmp_path, name):
    (tmp_path / name).write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="jsonl"):
        read_transcript_usage(tmp_path / name)


def test_a_directory_or_missing_file_is_refused(tmp_path):
    (tmp_path / "d.jsonl").mkdir()
    for target in (tmp_path / "d.jsonl", tmp_path / "missing.jsonl"):
        with pytest.raises(ValueError, match="regular file"):
            read_transcript_usage(target)


def test_an_oversized_file_is_refused(tmp_path, monkeypatch):
    import jarvis.adapters.claude_code.transcript as transcript

    monkeypatch.setattr(transcript, "MAX_FILE_BYTES", 10)
    path = write(tmp_path / "t.jsonl", record())
    with pytest.raises(ValueError, match="too large"):
        read_transcript_usage(path)


# ---------- the hook ----------


def test_stop_records_one_row_per_message_with_tokens_and_cost_unknown_without_prices(
    project, tmp_path
):
    usage = {"cache_read_input_tokens": 100, "cache_creation_input_tokens": 5}
    path = write(tmp_path / "t.jsonl", record(usage=usage), record("msg_2"))
    result = handle(stop(project, path), ctx_for(project, tmp_path))
    assert (result.exit_code, result.stdout) == (0, None)
    first, second = rows(project)
    assert first["usage"]["tokens_in"] == 15  # input plus cache creation
    assert first["usage"]["tokens_cached"] == 100
    assert first["usage"]["tokens_out"] == 20
    assert first["data"]["cost_source"] == "unknown"
    assert first["data"]["cache_creation_tokens"] == 5
    assert first["data"]["message_id"] == "msg_1" and second["data"]["message_id"] == "msg_2"
    assert first["task_id"] == "claude-s1" and first["agent_id"] == "claude-code"
    assert first["model"]["name"] == "example-model"


def test_a_price_table_from_the_policy_source_computes_cost_and_records_its_hash(project, tmp_path):
    path = write(tmp_path / "t.jsonl", record(usage={"input_tokens": 1_000_000}))
    ctx = ctx_for(project, tmp_path, prices_path=".agents/prices.yaml")
    handle(stop(project, path), ctx)
    (row,) = rows(project)
    assert row["data"]["cost_source"] == "computed"
    assert row["usage"]["cost_usd"] > 1.0
    assert len(row["data"]["prices_sha256"]) == 64


def test_a_broken_or_missing_price_table_degrades_to_unknown_cost(project, tmp_path):
    (project / ".agents" / "prices.yaml").write_text("as_of: nope\n", encoding="utf-8")
    path = write(tmp_path / "t.jsonl", record())
    handle(stop(project, path), ctx_for(project, tmp_path, prices_path=".agents/prices.yaml"))
    handle(stop(project, path, session="s2"), ctx_for(project, tmp_path, prices_path="gone.yaml"))
    assert {r["data"]["cost_source"] for r in rows(project)} == {"unknown"}


def test_enforce_mode_ignores_an_agent_writable_price_table(project, tmp_path):
    path = write(tmp_path / "t.jsonl", record())
    unprotected = HookContext(
        HookConfig(on_behalf_of="alice", prices_path=".agents/prices.yaml"),
        HookMode.ENFORCE,
        project,
        tmp_path / "state",
        None,
    )
    result = handle(stop(project, path), unprotected)
    assert result.exit_code == 0  # capture never blocks
    assert [r["data"]["cost_source"] for r in rows(project)] == ["unknown"]


def test_repeated_stops_and_a_session_end_do_not_duplicate_rows(project, tmp_path):
    ctx = ctx_for(project, tmp_path)
    path = write(tmp_path / "t.jsonl", record("msg_1"))
    handle(stop(project, path), ctx)
    handle(stop(project, path), ctx)
    handle(stop(project, path, "SessionEnd"), ctx)
    assert len(rows(project)) == 1


def test_a_lagging_transcript_is_caught_up_by_the_next_event(project, tmp_path):
    ctx = ctx_for(project, tmp_path)
    path = write(tmp_path / "t.jsonl", record("msg_1"))
    handle(stop(project, path), ctx)
    write(path, record("msg_1"), record("msg_2"), record("msg_3"))
    handle(stop(project, path, "SessionEnd"), ctx)
    assert [r["data"]["message_id"] for r in rows(project)] == ["msg_1", "msg_2", "msg_3"]


def test_the_same_message_id_in_another_session_is_a_separate_row(project, tmp_path):
    ctx = ctx_for(project, tmp_path)
    path = write(tmp_path / "t.jsonl", record("msg_1"))
    handle(stop(project, path, session="a"), ctx)
    handle(stop(project, path, session="b"), ctx)
    assert len(rows(project)) == 2


def test_subagent_stop_is_captured_and_marked_as_a_subagent(project, tmp_path):
    path = write(tmp_path / "t.jsonl", record(isSidechain=True))
    payload = {**stop(project, path, "SubagentStop"), "agent_id": "ag1", "agent_type": "Explore"}
    handle(payload, ctx_for(project, tmp_path))
    (row,) = rows(project)
    assert row["agent_id"] == "claude-code:Explore:ag1" and row["data"]["sidechain"] is True


def test_message_time_is_used_and_a_future_time_is_not(project, tmp_path):
    past = "2026-01-02T03:04:05+00:00"
    future = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    path = write(tmp_path / "t.jsonl", record("old", ts=past), record("new", ts=future))
    handle(stop(project, path), ctx_for(project, tmp_path))
    old, new = rows(project)
    assert old["ts"].startswith("2026-01-02T03:04:05")
    assert datetime.fromisoformat(new["ts"]) <= datetime.now(UTC) + timedelta(seconds=5)


def test_no_message_content_reaches_the_ledger(project, tmp_path):
    path = write(tmp_path / "t.jsonl", record())
    handle(stop(project, path), ctx_for(project, tmp_path))
    text = (project / ".agents" / "ledger" / "events.jsonl").read_text(encoding="utf-8")
    assert "SECRET TEXT" not in text


def test_a_bad_transcript_never_blocks_even_in_enforce_mode(project, tmp_path):
    ctx = ctx_for(project, tmp_path, HookMode.ENFORCE)
    for target in (tmp_path / "missing.jsonl", tmp_path / "notes.txt"):
        result = handle(stop(project, target), ctx)
        assert result.exit_code == 0
    assert rows(project) == []
    kinds = [
        r["event"]["kind"]
        for r in Ledger(project / ".agents" / "ledger" / "events.jsonl").records()
    ]
    assert kinds.count("error") == 2  # recorded as errors, not silently dropped


def test_an_event_without_a_transcript_path_does_nothing(project, tmp_path):
    payload = {"hook_event_name": "Stop", "session_id": "s1", "cwd": str(project)}
    assert handle(payload, ctx_for(project, tmp_path)).exit_code == 0
    assert rows(project) == []


def test_captured_rows_feed_the_report(project, tmp_path):
    path = write(tmp_path / "t.jsonl", record("a"), record("b", usage={"output_tokens": 30}))
    handle(stop(project, path), ctx_for(project, tmp_path))
    ledger = Ledger(project / ".agents" / "ledger" / "events.jsonl")
    total = summarize_tokens(ledger.records(), "task")["total"]
    assert total["calls"] == 2 and total["tokens_out"] == 50 and total["calls_without_cost"] == 2
    assert ledger.verify().ok


def test_the_reader_handles_a_real_shaped_record_from_claude_code(tmp_path):
    """Field names as observed in a real transcript (not a documented interface)."""
    real = {
        "type": "assistant",
        "uuid": "u1",
        "parentUuid": "p1",
        "sessionId": "s",
        "timestamp": "2026-10-28T10:00:00.000Z",
        "isSidechain": False,
        "requestId": "req_1",
        "message": {
            "id": "msg_01",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-5-5",
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "hi"}],
            "usage": {
                "input_tokens": 2,
                "output_tokens": 2387,
                "cache_read_input_tokens": 48205,
                "cache_creation_input_tokens": 20810,
                "service_tier": "standard",
            },
        },
    }
    (m,) = read_transcript_usage(write(tmp_path / "t.jsonl", real)).messages
    assert (m.model, m.input_tokens, m.cache_read_tokens) == ("claude-sonnet-5-5", 2, 48205)
    assert m.ts == datetime(2026, 10, 28, 10, 0, tzinfo=UTC)
