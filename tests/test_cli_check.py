import json
import shutil
from pathlib import Path

import pytest

import jarvis.cli
from jarvis.cli import main
from jarvis.core.ledger import Ledger

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


@pytest.fixture
def files(tmp_path):
    shutil.copy(EXAMPLES / "policy.yaml", tmp_path / "policy.yaml")
    shutil.copy(EXAMPLES / "tool-registry.yaml", tmp_path / "registry.yaml")
    shutil.copy(EXAMPLES / "action.json", tmp_path / "action.json")
    return tmp_path


def check(files, *extra, action="action.json", ledger="ledger.jsonl"):
    args = [
        "check",
        "--action",
        action if action == "-" else str(files / action),
        "--policy",
        str(files / "policy.yaml"),
        "--registry",
        str(files / "registry.yaml"),
        "--ledger",
        str(files / ledger),
        *extra,
    ]
    return main(args)


def write_action(files, **over):
    data = json.loads((files / "action.json").read_text("utf-8"))
    data.update(over)
    (files / "changed.json").write_text(json.dumps(data), encoding="utf-8")
    return "changed.json"


def test_allow_exits_zero_prints_the_verdict_and_logs_it(files, capsys):
    assert check(files) == 0
    verdict = json.loads(capsys.readouterr().out)
    assert verdict["decision"] == "allow"
    assert verdict["policy_version"] == "0.1.0"
    assert verdict["risk"] == "low"
    ledger = Ledger(files / "ledger.jsonl")
    assert ledger.verify().records == 1
    assert next(ledger.records())["event"]["kind"] == "policy_decision"


@pytest.mark.parametrize(
    ("over", "code", "decision"),
    [
        ({"paths": [".github/workflows/ci.yml"]}, 11, "human"),
        ({"tool_id": "web.post", "paths": [], "diff_lines": 0}, 10, "council"),
        ({"paths": ["config/.env"]}, 12, "deny"),
        ({"tool_id": "no.such.tool"}, 12, "deny"),
        ({"role": "analyst"}, 12, "deny"),
    ],
)
def test_exit_codes_map_to_decisions(files, capsys, over, code, decision):
    assert check(files, action=write_action(files, **over)) == code
    assert json.loads(capsys.readouterr().out)["decision"] == decision


def test_a_tainted_outbound_action_is_not_allowed(files, capsys):
    over = {
        "tool_id": "web.post",
        "paths": [],
        "diff_lines": 0,
        "session": {"trust_labels_seen": ["untrusted"], "data_classes_seen": ["restricted"]},
    }
    assert check(files, action=write_action(files, **over)) == 11
    verdict = json.loads(capsys.readouterr().out)
    assert verdict["trifecta"] is True and verdict["tainted"] is True


def test_action_can_come_from_stdin(files, monkeypatch, capsys):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO((files / "action.json").read_text("utf-8")))
    assert check(files, action="-") == 0


def test_dry_run_does_not_create_a_ledger(files):
    assert check(files, "--no-log") == 0
    assert not (files / "ledger.jsonl").exists()


def test_dry_run_still_sees_history_when_a_ledger_exists(files, capsys):
    assert check(files) == 0
    capsys.readouterr()
    big = write_action(files, diff_lines=395)  # 12 already recorded, so 407 in total
    assert check(files, "--no-log", action=big) == 11  # over both limits: policy default (human)
    verdict = json.loads(capsys.readouterr().out)
    assert verdict["total_diff_lines"] == 407
    assert Ledger(files / "ledger.jsonl").verify().records == 1  # nothing new written


def test_yaml_object_tags_are_rejected_not_executed(files, tmp_path, capsys):
    marker = tmp_path / "pwned.txt"
    evil = f'tool_id: !!python/object/apply:os.system ["echo pwned > {marker.as_posix()}"]\n'
    (files / "evil.yaml").write_text(evil, encoding="utf-8")
    assert check(files, action="evil.yaml") == 2
    assert "invalid input" in capsys.readouterr().err
    assert not marker.exists()


def test_invalid_action_exits_2(files, capsys):
    (files / "bad.json").write_text('{"tool_id": "fs.write", "risk": "read"}', encoding="utf-8")
    assert check(files, action="bad.json") == 2
    assert "invalid input" in capsys.readouterr().err


def test_missing_policy_exits_2(files, capsys):
    (files / "policy.yaml").unlink()
    assert check(files) == 2
    assert "invalid input" in capsys.readouterr().err


def test_unsafe_policy_exits_2(files, capsys):
    (files / "policy.yaml").write_text("version: x\ndefault: allow\n", encoding="utf-8")
    assert check(files) == 2


def test_a_corrupt_ledger_denies(files, capsys):
    assert check(files) == 0
    path = files / "ledger.jsonl"
    path.write_bytes(path.read_bytes().replace(b"builder-1", b"mallory", 1))
    capsys.readouterr()
    assert check(files) == 12
    assert "audit ledger unavailable" in json.loads(capsys.readouterr().out)["reason"]


def test_an_unexpected_error_denies(files, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("bug")

    monkeypatch.setattr(jarvis.cli, "run_check", boom)
    assert check(files) == 12
    assert "internal error" in capsys.readouterr().err


def test_project_file_restricts_tools(files, capsys):
    project = {
        "project": "p",
        "mode": "greenfield",
        "stack": "python",
        "tiers": {"builder": "standard"},
        "environments": ["dev"],
        "tools_enabled": ["git.read"],
        "data_policy": {"allowed_models": ["m"]},
    }
    path = files / "project.json"
    path.write_text(json.dumps(project), encoding="utf-8")
    assert check(files, "--project", str(path)) == 12
    assert "builtin:tool-not-enabled" in capsys.readouterr().out
