import json

import pytest

from jarvis.cli import main
from jarvis.core.ledger import Ledger
from jarvis.core.schemas import SCHEMAS

BASE = ["--agent", "builder-1", "--on-behalf-of", "alice"]


def log(ledger_path, *extra, kind="note"):
    return main(["log", "--ledger", str(ledger_path), "--kind", kind, *BASE, *extra])


@pytest.fixture
def path(tmp_path):
    return tmp_path / "events.jsonl"


def test_log_prints_seq_and_hash(path, capsys):
    assert log(path) == 0
    out = capsys.readouterr().out.strip()
    seq, _, digest = out.partition(":")
    assert seq == "0" and len(digest) == 64


def test_log_records_structured_fields(path):
    code = log(
        path,
        "--task",
        "T-1",
        "--tool-id",
        "git.read",
        "--tool-version",
        "1",
        "--model",
        "mid-model-a",
        "--tokens-in",
        "120",
        "--tokens-out",
        "30",
        "--cost-usd",
        "0.002",
        "--risk",
        "read",
        "--decision",
        "allow",
        "--trust",
        "internal",
        "--no-tainted",
        "--data",
        '{"note": "ok"}',
        kind="tool_call",
    )
    assert code == 0
    event = next(Ledger(path).records())["event"]
    assert event["tool"] == {"id": "git.read", "version": "1"}
    assert event["usage"]["tokens_in"] == 120
    assert event["tainted"] is False
    assert event["data"] == {"note": "ok"}


def test_verify_ok_and_head_anchor_round_trip(path, capsys):
    log(path)
    log(path)
    capsys.readouterr()
    assert main(["head", "--ledger", str(path)]) == 0
    anchor = capsys.readouterr().out.strip()
    log(path)  # the ledger grows after the anchor is taken
    assert main(["verify", "--ledger", str(path), "--anchor", anchor]) == 0
    assert "OK: 3 records" in capsys.readouterr().out


def test_verify_fails_on_tampering(path, capsys):
    log(path)
    log(path)
    text = path.read_bytes().replace(b'"agent_id":"builder-1"', b'"agent_id":"mallory"', 1)
    path.write_bytes(text)
    capsys.readouterr()
    assert main(["verify", "--ledger", str(path)]) == 1
    assert "FAILED at seq 0" in capsys.readouterr().err


def test_verify_missing_ledger_is_a_usage_error(path, capsys):
    assert main(["verify", "--ledger", str(path)]) == 2
    assert "not found" in capsys.readouterr().err


def test_head_of_empty_ledger_fails_cleanly(path, capsys):
    path.write_bytes(b"")
    assert main(["head", "--ledger", str(path)]) == 1
    assert "empty" in capsys.readouterr().err


def test_invalid_event_is_rejected_and_not_written(path, capsys):
    assert log(path, "--trace-id", "not-hex") == 2
    assert "invalid event" in capsys.readouterr().err
    assert not path.exists()


def test_data_must_be_a_json_object(path, capsys):
    assert log(path, "--data", "[1, 2]") == 2
    assert "JSON object" in capsys.readouterr().err


def test_tool_version_without_tool_id_is_rejected(path):
    assert log(path, "--tool-version", "1") == 2


def test_bad_anchor_is_an_argument_error(path):
    log(path)
    with pytest.raises(SystemExit) as exc:
        main(["verify", "--ledger", str(path), "--anchor", "nope"])
    assert exc.value.code == 2


def test_unknown_kind_is_an_argument_error(path):
    with pytest.raises(SystemExit) as exc:
        main(["log", "--ledger", str(path), "--kind", "bogus", *BASE])
    assert exc.value.code == 2


def test_ledger_path_from_environment(tmp_path, monkeypatch, capsys):
    target = tmp_path / "env-ledger.jsonl"
    monkeypatch.setenv("JARVIS_LEDGER", str(target))
    assert main(["log", "--kind", "note", *BASE]) == 0
    assert target.exists()
    assert main(["verify"]) == 0


def test_schema_export_command(tmp_path, capsys):
    out = tmp_path / "schemas"
    assert main(["schema", "export", "--out", str(out)]) == 0
    names = {p.name for p in out.iterdir()}
    assert names == {f"{n}.schema.json" for n in SCHEMAS}
    assert json.loads((out / "event.schema.json").read_text(encoding="utf-8"))["type"] == "object"
