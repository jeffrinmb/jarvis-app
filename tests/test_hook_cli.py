"""`agentctl hook`: run, snippet and doctor, the version rules, and the shipped examples."""

import io
import json
import shutil
from pathlib import Path

import pytest

import jarvis.hook_cli as hook_cli
from jarvis.adapters.claude_code import (
    MIN_ON_FAILURE,
    HookConfig,
    HookMode,
    build_settings,
    check_environment,
    load_config,
    parse_version,
    supports_on_failure,
)
from jarvis.cli import main
from jarvis.core.ledger import Ledger
from jarvis.core.schemas import Policy, load_tool_registry, load_yaml_model

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "claude-code"


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / ".agents").mkdir(parents=True)
    for name in ("policy.yaml", "tool-registry.yaml"):
        shutil.copy(EXAMPLES / name, root / ".agents" / name)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.setenv("JARVIS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("JARVIS_ON_BEHALF_OF", "alice")
    monkeypatch.delenv("JARVIS_HOOK_MODE", raising=False)
    return root


def send(monkeypatch, payload, *args):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    monkeypatch.setattr("sys.stdin", io.StringIO(text))
    return main(["hook", "run", *args])


def pre(project, tool, tool_input):
    return {
        "hook_event_name": "PreToolUse",
        "session_id": "s1",
        "cwd": str(project),
        "tool_name": tool,
        "tool_input": tool_input,
    }


def edit(project, relative):
    return pre(
        project,
        "Edit",
        {"file_path": str(project / relative), "old_string": "a", "new_string": "b"},
    )


# ---------- hook run ----------


def test_run_defaults_to_log_only_and_records(project, monkeypatch, capsys):
    assert send(monkeypatch, edit(project, "docs/a.md")) == 0
    assert capsys.readouterr().out == ""
    ledger = Ledger(project / ".agents" / "ledger" / "events.jsonl")
    assert [r["event"]["decision"] for r in ledger.records()] == ["allow"]


def test_run_enforce_prints_the_decision_json(project, monkeypatch, capsys):
    assert send(monkeypatch, edit(project, ".github/workflows/ci.yml"), "--mode", "enforce") == 0
    out = json.loads(capsys.readouterr().out)
    assert out["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_mode_can_come_from_the_environment(project, monkeypatch, capsys):
    monkeypatch.setenv("JARVIS_HOOK_MODE", "enforce")
    send(monkeypatch, edit(project, ".github/workflows/ci.yml"))
    assert "permissionDecision" in capsys.readouterr().out


def test_mode_can_come_from_the_config_file(project, monkeypatch, capsys):
    (project / ".agents" / "claude-code.yaml").write_text("mode: enforce\n", encoding="utf-8")
    send(monkeypatch, edit(project, ".github/workflows/ci.yml"))
    assert "permissionDecision" in capsys.readouterr().out


def test_invalid_json_blocks_only_in_enforce_mode(project, monkeypatch, capsys):
    assert send(monkeypatch, "{ not json") == 0
    assert send(monkeypatch, "{ not json", "--mode", "enforce") == 2
    assert "blocked" in capsys.readouterr().err


def test_an_unreadable_config_blocks_in_enforce_and_not_in_log_only(project, monkeypatch):
    (project / ".agents" / "claude-code.yaml").write_text("mode: banana\n", encoding="utf-8")
    assert send(monkeypatch, edit(project, "docs/a.md")) == 0
    assert send(monkeypatch, edit(project, "docs/a.md"), "--mode", "enforce") == 2


def test_run_uses_the_project_directory_from_the_environment(project, monkeypatch):
    payload = edit(project, "docs/a.md")
    payload["cwd"] = str(project / "docs")  # the agent changed directory
    send(monkeypatch, payload)
    assert (project / ".agents" / "ledger" / "events.jsonl").exists()


# ---------- report ----------


def test_report_summarises_what_the_hook_recorded(project, monkeypatch, capsys):
    send(monkeypatch, edit(project, "docs/a.md"))
    send(monkeypatch, edit(project, ".github/workflows/ci.yml"))
    send(monkeypatch, pre(project, "BrandNewTool", {}))
    capsys.readouterr()
    assert main(["hook", "report"]) == 0
    out = capsys.readouterr().out
    assert "3 decisions" in out
    assert "would have intervened on 2" in out
    assert "BrandNewTool" in out  # listed as a tool with no mapping
    assert "framework-and-claude-config" in out


def test_report_json_is_machine_readable(project, monkeypatch, capsys):
    send(monkeypatch, edit(project, "docs/a.md"))
    send(monkeypatch, pre(project, "BrandNewTool", {}))
    capsys.readouterr()
    assert main(["hook", "report", "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["total"] == 2
    assert summary["decisions"] == {"allow": 1, "deny": 1}
    assert summary["unmapped_tools"] == {"BrandNewTool": 1}
    assert summary["would_block_rate"] == 0.5


def test_report_needs_a_ledger_and_refuses_a_broken_one(project, monkeypatch, capsys):
    assert main(["hook", "report"]) == 2
    send(monkeypatch, edit(project, "docs/a.md"))
    ledger = project / ".agents" / "ledger" / "events.jsonl"
    ledger.write_bytes(ledger.read_bytes().replace(b"claude-code", b"mallory", 1))
    capsys.readouterr()
    assert main(["hook", "report"]) == 1
    assert "fails verification" in capsys.readouterr().err


def _record(
    decision, tool="fs.write", rules=(), paths=(), harness="claude-code", kind="policy_decision"
):
    return {
        "event": {
            "kind": kind,
            "decision": decision,
            "tool": {"id": tool},
            "ts": "2026-10-27T10:00:00Z",
            "data": {
                "paths": list(paths),
                "matches": [{"rule_id": r, "effect": decision} for r in rules],
                "context": {"harness": harness, "mode": "log-only", "claude_tool": "Edit"},
            },
        }
    }


def test_summarize_counts_only_this_harness_and_only_decisions():
    from jarvis.adapters.claude_code.report import summarize

    records = [
        _record("allow"),
        _record("human", rules=["r1"], paths=["a", "b", "c", "d", "e"]),
        _record("human", rules=["r1"], paths=["f"]),
        _record("allow", harness="something-else"),
        _record("allow", kind="note"),
    ]
    summary = summarize(records)
    assert summary["total"] == 3
    assert summary["would_block"] == 2
    (intervention,) = summary["interventions"]
    assert intervention["count"] == 2 and intervention["rules"] == ["r1"]
    assert len(intervention["example_paths"]) == 3  # capped


def test_an_empty_ledger_reports_nothing_recorded():
    from jarvis.adapters.claude_code.report import render_text, summarize

    assert "No Claude Code decisions" in render_text(summarize([]))


# ---------- settings snippet ----------


def test_snippet_registers_every_handled_event_in_exec_form():
    hooks = build_settings(HookMode.LOG_ONLY)["hooks"]
    assert set(hooks) == {
        "SessionStart",
        "PreToolUse",
        "PostToolUse",
        "PostToolUseFailure",
        "ConfigChange",
    }
    handler = hooks["PreToolUse"][0]["hooks"][0]
    assert hooks["PreToolUse"][0]["matcher"] == "*"
    assert handler["type"] == "command" and handler["command"] == "agentctl"
    assert handler["args"] == ["hook", "run", "--mode", "log-only"]
    assert handler["timeout"] == 10
    assert "onFailure" not in handler


def test_minimal_snippet_drops_the_post_events_that_double_the_cost():
    hooks = build_settings(HookMode.LOG_ONLY, minimal=True)["hooks"]
    assert set(hooks) == {"SessionStart", "PreToolUse", "ConfigChange"}


def test_venv_runner_uses_the_project_python_directly():
    handler = build_settings(HookMode.LOG_ONLY, runner="venv")["hooks"]["PreToolUse"][0]["hooks"][0]
    assert handler["command"].startswith("${CLAUDE_PROJECT_DIR}/.venv/")
    assert handler["args"][:2] == ["-m", "jarvis.cli"]


def test_enforce_snippet_blocks_on_hook_failure():
    handler = build_settings(HookMode.ENFORCE)["hooks"]["PreToolUse"][0]["hooks"][0]
    assert handler["onFailure"] == "block"
    assert handler["args"][-1] == "enforce"


def test_uv_runner_goes_through_uv(project):
    handler = build_settings(HookMode.LOG_ONLY, runner="uv")["hooks"]["PreToolUse"][0]["hooks"][0]
    assert handler["command"] == "uv"
    assert handler["args"][:4] == ["run", "--project", "${CLAUDE_PROJECT_DIR}", "agentctl"]


def test_snippet_command_prints_valid_json(capsys):
    assert main(["hook", "snippet", "--mode", "enforce"]) == 0
    assert "hooks" in json.loads(capsys.readouterr().out)


def test_shipped_settings_examples_match_the_generator():
    for name, mode, runner in (
        ("settings.log-only.json", HookMode.LOG_ONLY, "direct"),
        ("settings.enforce.json", HookMode.ENFORCE, "uv"),
    ):
        shipped = json.loads((EXAMPLES / name).read_text(encoding="utf-8"))
        assert shipped == build_settings(mode, runner), f"regenerate examples/claude-code/{name}"


# ---------- versions and doctor ----------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2.1.276 (Claude Code)", (2, 1, 276)),
        ("claude 2.1.295", (2, 1, 295)),
        ("v10.0.1", (10, 0, 1)),
        ("no version here", None),
        ("", None),
    ],
)
def test_parse_version(text, expected):
    assert parse_version(text) == expected


def test_on_failure_needs_2_1_295():
    assert MIN_ON_FAILURE == (2, 1, 295)
    assert not supports_on_failure((2, 1, 276))
    assert supports_on_failure((2, 1, 295))
    assert supports_on_failure((2, 2, 0))
    assert not supports_on_failure(None)


def test_enforce_is_refused_on_a_version_that_fails_open():
    problems = [f for f in check_environment((2, 1, 276), HookMode.ENFORCE) if not f.ok]
    assert problems and "2.1.295" in problems[0].message


def test_log_only_works_on_any_known_version():
    assert all(f.ok for f in check_environment((2, 1, 276), HookMode.LOG_ONLY))


def test_doctor_fails_for_enforce_on_an_old_version(project, monkeypatch, capsys):
    monkeypatch.setattr(hook_cli, "_claude_version_text", lambda: "2.1.276 (Claude Code)")
    assert main(["hook", "doctor", "--mode", "enforce"]) == 1
    assert "PROBLEM" in capsys.readouterr().out


def test_doctor_passes_when_version_and_files_are_fine(project, monkeypatch, capsys):
    monkeypatch.setattr(hook_cli, "_claude_version_text", lambda: "2.1.300 (Claude Code)")
    assert main(["hook", "doctor", "--mode", "enforce"]) == 0
    assert "PROBLEM" not in capsys.readouterr().out


def test_doctor_reports_missing_policy_files(project, monkeypatch, capsys):
    (project / ".agents" / "policy.yaml").unlink()
    monkeypatch.setattr(hook_cli, "_claude_version_text", lambda: "2.1.300")
    assert main(["hook", "doctor"]) == 1
    assert "policy not found" in capsys.readouterr().out


def test_doctor_reports_when_claude_cannot_be_run(project, monkeypatch):
    monkeypatch.setattr(hook_cli, "_claude_version_text", lambda: None)
    assert main(["hook", "doctor"]) == 1


# ---------- this repository's own agent configuration is valid ----------

REPO = Path(__file__).resolve().parent.parent


def test_this_repositorys_own_agent_files_load_and_stay_in_log_only_mode():
    policy = load_yaml_model(REPO / ".agents" / "policy.yaml", Policy)
    registry = load_tool_registry(REPO / ".agents" / "tool-registry.yaml")
    cfg = load_config(REPO / ".agents" / "claude-code.yaml")
    assert policy.default.value == "human"
    assert {"fs.read", "fs.write", "shell.run"} <= {t.id for t in registry.tools}
    assert cfg.mode is HookMode.LOG_ONLY, "switching this repository to enforce is task P2.10"
    # every tool whose output is trusted by override must be registered
    registered = {t.id for t in registry.tools}
    assert set(cfg.trust_overrides) <= registered


def test_shell_run_is_never_auto_approved_in_this_repositorys_registry():
    registry = load_tool_registry(REPO / ".agents" / "tool-registry.yaml")
    shell = next(t for t in registry.tools if t.id == "shell.run")
    assert shell.approval.value == "human"


# ---------- shipped examples are valid ----------


def test_example_policy_and_registry_are_valid_and_consistent():
    policy = load_yaml_model(EXAMPLES / "policy.yaml", Policy)
    registry = load_tool_registry(EXAMPLES / "tool-registry.yaml")
    ids = {t.id for t in registry.tools}
    assert {"fs.read", "fs.write", "shell.read", "shell.test", "shell.run", "web.fetch"} <= ids
    assert policy.default.value == "human"


def test_example_adapter_config_loads_and_defaults_to_log_only():
    cfg = load_config(EXAMPLES / "claude-code.yaml")
    assert cfg.mode is HookMode.LOG_ONLY
    assert load_config(None) == HookConfig()


def test_shell_run_is_never_auto_approved_in_the_example_registry():
    registry = load_tool_registry(EXAMPLES / "tool-registry.yaml")
    shell = next(t for t in registry.tools if t.id == "shell.run")
    assert shell.approval.value == "human"
