"""Budgets, loop breaker and kill switch (P2.8, G5)."""

import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

import jarvis.hook_cli  # noqa: F401  (import order: the CLI pulls in the adapter)
from helpers import default_registry
from jarvis.adapters.claude_code import HookConfig, HookContext, HookMode, handle
from jarvis.cli import main
from jarvis.core.killswitch import clear_halt, halt_path, read_halt, set_halt
from jarvis.core.ledger import Ledger
from jarvis.core.policy import compute_usage, evaluate, fingerprint, run_check
from jarvis.core.schemas import Action, Decision, EventKind, Policy
from jarvis.core.schemas.policy import Budgets

NOW = datetime.now(UTC)  # events carry the real clock, so the tests must too


def policy(**budgets):
    return Policy(
        version="t",
        default=Decision.HUMAN,
        auto_approve=[
            {"id": "writes", "description": "docs", "when": {"tools": ["fs.write"]}},
        ],
        budgets=budgets or None,
    )


def act(task="t1", path="a.md", tool="fs.write"):
    return Action(
        agent_id="agent",
        on_behalf_of="alice",
        role="builder",
        task_id=task,
        tool_id=tool,
        environment="dev",
        paths=[path],
        diff_lines=1,
    )


def check(ledger, pol, action=None, now=NOW, halt_file=None):
    return run_check(
        action or act(), pol, default_registry(), None, ledger=ledger, now=now, halt_file=halt_file
    )


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "events.jsonl")


# ---------- schema ----------


def test_budgets_are_optional_and_validated():
    assert Policy(version="x").budgets is None
    with pytest.raises(ValidationError):
        Budgets(max_calls_per_task=0)
    with pytest.raises(ValidationError, match="together"):
        Budgets(loop_threshold=3)
    with pytest.raises(ValidationError, match="never trip|could never"):
        Budgets(loop_threshold=5, loop_window=3)
    with pytest.raises(ValidationError):
        Budgets(on_exceed="allow")


def test_a_policy_file_can_set_budgets_from_yaml_text():
    from jarvis.core.schemas import parse_yaml_model

    text = "version: v\nbudgets:\n  max_calls_per_task: 5\n  on_exceed: deny\n"
    assert parse_yaml_model(text, Policy).budgets.on_exceed == Decision.DENY


# ---------- per-task and per-day budgets ----------


def test_task_budget_trips_after_the_limit_and_not_before(ledger):
    pol = policy(max_calls_per_task=3)
    decisions = [check(ledger, pol, act(path=f"f{i}.md")).decision for i in range(5)]
    assert decisions == [Decision.ALLOW] * 3 + [Decision.HUMAN] * 2


def test_another_task_has_its_own_budget(ledger):
    pol = policy(max_calls_per_task=1)
    check(ledger, pol, act(task="t1"))
    assert check(ledger, pol, act(task="t1", path="b.md")).decision == Decision.HUMAN
    assert check(ledger, pol, act(task="t2")).decision == Decision.ALLOW


def test_on_exceed_deny_is_a_hard_stop(ledger):
    pol = policy(max_calls_per_task=1, on_exceed="deny")
    check(ledger, pol)
    verdict = check(ledger, pol, act(path="b.md"))
    assert verdict.decision == Decision.DENY
    assert "builtin:budget-task-calls" in {m.rule_id for m in verdict.matches}


def test_denied_calls_do_not_use_the_budget(ledger):
    pol = policy(max_calls_per_task=2)
    for _ in range(4):
        check(ledger, pol, act(tool="not-registered"))  # denied
    assert check(ledger, pol).decision == Decision.ALLOW


def test_day_budget_spans_tasks_and_resets_each_utc_day(ledger):
    pol = policy(max_calls_per_day=2)
    check(ledger, pol, act(task="a"))
    check(ledger, pol, act(task="b"))
    assert check(ledger, pol, act(task="c")).decision == Decision.HUMAN
    tomorrow = NOW + timedelta(days=1)
    assert check(ledger, pol, act(task="c"), now=tomorrow).decision == Decision.ALLOW


def test_a_trip_is_recorded_as_its_own_event(ledger):
    pol = policy(max_calls_per_task=1)
    check(ledger, pol)
    check(ledger, pol, act(path="b.md"))
    kinds = [r["event"]["kind"] for r in ledger.records()]
    assert kinds.count(EventKind.BUDGET_EXCEEDED.value) == 1
    assert ledger.verify().ok


def test_no_budgets_means_no_limits_and_no_extra_events(ledger):
    pol = policy()
    for i in range(20):
        assert check(ledger, pol, act(path=f"f{i}.md")).decision == Decision.ALLOW
    assert {r["event"]["kind"] for r in ledger.records()} == {EventKind.POLICY_DECISION.value}


def test_a_budget_cannot_be_dodged_by_the_default_decision_or_a_matching_allow_rule(ledger):
    pol = policy(max_calls_per_task=1)
    check(ledger, pol)
    verdict = check(ledger, pol, act(path="b.md"))  # matches the auto_approve rule
    assert verdict.decision == Decision.HUMAN


# ---------- loop breaker ----------


def test_the_same_call_repeated_trips_the_loop_breaker(ledger):
    pol = policy(loop_threshold=3, loop_window=10)
    results = [check(ledger, pol, act(path="same.md")).decision for _ in range(4)]
    assert results == [Decision.ALLOW, Decision.ALLOW, Decision.HUMAN, Decision.HUMAN]
    kinds = [r["event"]["kind"] for r in ledger.records()]
    assert EventKind.LOOP_DETECTED.value in kinds


def test_varied_calls_do_not_trip_it(ledger):
    pol = policy(loop_threshold=3, loop_window=10)
    assert all(
        check(ledger, pol, act(path=f"f{i}.md")).decision == Decision.ALLOW for i in range(8)
    )


def test_repeats_outside_the_window_are_forgotten(ledger):
    pol = policy(loop_threshold=3, loop_window=4)
    for path in ("same.md", "x.md", "y.md", "z.md", "w.md", "same.md"):
        assert check(ledger, pol, act(path=path)).decision == Decision.ALLOW


def test_path_spelling_does_not_hide_a_loop():
    assert fingerprint("fs.write", ["./a//b.md"]) == fingerprint("fs.write", ["a/b.md"])
    assert fingerprint("fs.write", ["a.md"]) != fingerprint("fs.read", ["a.md"])
    assert fingerprint("fs.write", ["a.md", "b.md"]) == fingerprint("fs.write", ["b.md", "a.md"])


def test_the_fingerprint_stores_no_path_text(ledger):
    check(ledger, policy(loop_threshold=2, loop_window=3), act(path="secret-name.md"))
    (record,) = [r for r in ledger.records()]
    assert "secret-name" not in record["event"]["data"]["fingerprint"]


def test_usage_with_no_budgets_is_zero():
    assert compute_usage([], act(), None, NOW).task_calls == 0


# ---------- kill switch ----------


def test_the_kill_switch_denies_everything_including_reads(ledger, tmp_path):
    halt_file = tmp_path / "state" / "HALT"
    pol = policy()
    assert check(ledger, pol, halt_file=halt_file).decision == Decision.ALLOW
    set_halt(halt_file, by="alice")
    for tool in ("fs.write", "git.read", "not-registered"):
        verdict = check(ledger, pol, act(tool=tool), halt_file=halt_file)
        assert verdict.decision == Decision.DENY
        assert verdict.matches[0].rule_id == "builtin:kill-switch"
    assert "alice" in verdict.reason
    clear_halt(halt_file)
    assert check(ledger, pol, halt_file=halt_file).decision == Decision.ALLOW


def test_the_denial_is_recorded_in_the_ledger(ledger, tmp_path):
    halt_file = tmp_path / "HALT"
    set_halt(halt_file)
    check(ledger, policy(), halt_file=halt_file)
    (record,) = ledger.records()
    assert record["event"]["decision"] == "deny"


def test_the_switch_fails_closed_when_it_cannot_be_read(tmp_path):
    halt_file = tmp_path / "HALT"
    halt_file.write_text("{ not json", encoding="utf-8")
    assert read_halt(halt_file) is not None
    halt_file.write_bytes(b"\xff\xfe")
    assert read_halt(halt_file) is not None
    halt_file.unlink()
    halt_file.mkdir()  # a directory in the way cannot be read either
    assert read_halt(halt_file) is not None


def test_no_file_means_off(tmp_path):
    assert read_halt(tmp_path / "missing" / "HALT") is None
    assert clear_halt(tmp_path / "HALT") is False


def test_halt_is_pure_in_the_engine():
    verdict = evaluate(act(), policy(), default_registry(), halted="stop")
    assert verdict.decision == Decision.DENY and verdict.reason == "stop"


def test_a_dry_run_without_a_ledger_still_honours_the_switch(tmp_path):
    halt_file = tmp_path / "HALT"
    set_halt(halt_file)
    verdict = run_check(act(), policy(), default_registry(), halt_file=halt_file)
    assert verdict.decision == Decision.DENY


# ---------- the command line ----------


def test_halt_resume_status_cycle_and_the_ledger_records_it(tmp_path, capsys):
    base = ["--state-dir", str(tmp_path / "state"), "--ledger", str(tmp_path / "l.jsonl")]
    assert main(["status", *base]) == 0
    assert main(["halt", "--by", "alice", *base]) == 0
    assert main(["status", *base]) == 1
    assert "alice" in capsys.readouterr().out
    assert main(["resume", "--by", "alice", *base]) == 0
    assert main(["resume", *base]) == 0  # idempotent
    assert main(["status", *base]) == 0
    kinds = [
        (r["event"]["kind"], r["event"]["outcome"]) for r in Ledger(tmp_path / "l.jsonl").records()
    ]
    assert kinds == [("kill_switch", "on"), ("kill_switch", "off")]


def test_check_denies_while_halted(tmp_path, monkeypatch, capsys):
    state = tmp_path / "state"
    action = act().model_dump_json()
    paths = {
        "--policy": tmp_path / "policy.yaml",
        "--registry": tmp_path / "registry.yaml",
    }
    paths["--policy"].write_text(
        "version: v\nauto_approve:\n"
        "  - id: w\n    description: d\n    when:\n      tools: [fs.write]\n",
        encoding="utf-8",
    )
    paths["--registry"].write_text(
        default_registry().model_dump_json(), encoding="utf-8"
    )  # JSON is YAML
    argv = ["check", "--action", "-", "--state-dir", str(state), "--no-log"]
    for flag, path in paths.items():
        argv += [flag, str(path)]
    monkeypatch.setattr("sys.stdin", io.StringIO(action))
    assert main(argv) == 0
    set_halt(halt_path(state))
    monkeypatch.setattr("sys.stdin", io.StringIO(action))
    assert main(argv) == 12


# ---------- the hook ----------


def hook_payload(root):
    return {
        "hook_event_name": "PreToolUse",
        "session_id": "s1",
        "cwd": str(root),
        "tool_name": "Edit",
        "tool_input": {
            "file_path": str(Path(root) / "docs" / "a.md"),
            "old_string": "a",
            "new_string": "b",
        },
    }


def hook_ctx(tmp_path, mode):
    import shutil

    from jarvis.adapters.claude_code.source import DirectorySource

    root = tmp_path / "proj"
    (root / ".agents").mkdir(parents=True)
    examples = Path(__file__).resolve().parent.parent / "examples" / "claude-code"
    for name in ("policy.yaml", "tool-registry.yaml"):
        shutil.copy(examples / name, root / ".agents" / name)
    source = DirectorySource(root) if mode is HookMode.ENFORCE else None
    return root, HookContext(
        HookConfig(on_behalf_of="alice"), mode, root, tmp_path / "state", source
    )


def test_the_hook_blocks_everything_in_enforce_mode_while_halted(tmp_path):
    root, ctx = hook_ctx(tmp_path, HookMode.ENFORCE)
    assert handle(hook_payload(root), ctx).stdout is None  # allowed first
    set_halt(halt_path(ctx.state_dir))
    result = handle(hook_payload(root), ctx)
    out = json.loads(result.stdout)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny"
    assert "kill switch" in out["permissionDecisionReason"]


def test_the_hook_in_log_only_mode_records_but_never_blocks_while_halted(tmp_path):
    root, ctx = hook_ctx(tmp_path, HookMode.LOG_ONLY)
    set_halt(halt_path(ctx.state_dir))
    result = handle(hook_payload(root), ctx)
    assert (result.exit_code, result.stdout) == (0, None)
    (record,) = Ledger(root / ".agents" / "ledger" / "events.jsonl").records()
    assert record["event"]["decision"] == "deny"
