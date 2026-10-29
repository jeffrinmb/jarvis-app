"""The approvals command line and the Claude Code hook's part in the approval flow (P2.7)."""

import json
import shutil
from pathlib import Path

import pytest

from helpers import action, default_registry, policy, rule
from jarvis.adapters.claude_code import HookConfig, HookContext, HookMode, handle
from jarvis.adapters.claude_code.source import DirectorySource
from jarvis.cli import main
from jarvis.core.ledger import Ledger
from jarvis.core.policy import run_check
from jarvis.core.schemas import Decision

REGISTRY = default_registry()
EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "claude-code"


def pol():
    return policy(human_required=[rule("src-needs-a-person", paths=["src/**"])])


def src_edit(**over):
    return action(**{"paths": ["src/x.py"], **over})


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "events.jsonl")


@pytest.fixture
def request_id(ledger):
    verdict = run_check(src_edit(), pol(), REGISTRY, ledger=ledger)
    assert verdict.decision == Decision.HUMAN
    return verdict.approval_request


def cli(ledger, *args, policy_file=None):
    argv = ["approvals", args[0], "--ledger", str(ledger.path)]
    if policy_file:
        argv += ["--policy", str(policy_file)]
    return main([*argv, *args[1:]])


def recheck(ledger, act=None):
    return run_check(act or src_edit(), pol(), REGISTRY, ledger=ledger).decision


# ---------- the command line ----------


def test_list_show_approve_and_the_gate_lets_the_call_through(ledger, request_id, capsys):
    assert cli(ledger, "list") == 0
    out = capsys.readouterr().out
    assert request_id in out and "open" in out and "fs.write" in out

    assert cli(ledger, "show", request_id) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["status"] == "open" and shown["detail"]["proposed_scope"]["tools"] == ["fs.write"]

    assert cli(ledger, "approve", request_id, "--by", "alice") == 0
    assert "approved" in capsys.readouterr().out
    assert recheck(ledger) == Decision.ALLOW
    grant = [r["event"] for r in ledger.records() if r["event"]["kind"] == "approval_granted"][-1]
    assert grant["agent_id"] == "agentctl:approve" and grant["on_behalf_of"] == "alice"
    assert cli(ledger, "list") == 0
    assert "No requests waiting" in capsys.readouterr().out
    assert ledger.verify().ok


def test_list_json_and_all(ledger, request_id, capsys):
    cli(ledger, "approve", request_id, "--by", "alice")
    capsys.readouterr()
    assert cli(ledger, "list", "--all", "--json") == 0
    (row,) = json.loads(capsys.readouterr().out)
    assert row["status"] == "approved" and row["request"] == request_id


def test_approve_can_widen_the_scope_and_the_default_lifetime_is_short(ledger, request_id):
    assert (
        cli(
            ledger,
            "approve",
            request_id,
            "--by",
            "alice",
            "--paths",
            "src/**",
            "--max-diff-lines",
            "50",
        )
        == 0
    )
    assert recheck(ledger, src_edit(paths=["src/deep/y.py"], diff_lines=10)) == Decision.ALLOW
    grant = [r["event"] for r in ledger.records() if r["event"]["kind"] == "approval_granted"][-1]
    scope = grant["data"]["grant"]["scope"]
    from datetime import datetime

    hours = (
        datetime.fromisoformat(scope["expires_at"]) - datetime.fromisoformat(grant["ts"])
    ).total_seconds() / 3600
    assert 3.9 < hours < 4.1


def test_the_lifetime_cannot_exceed_the_policys_cap(ledger, request_id, tmp_path, capsys):
    assert cli(ledger, "approve", request_id, "--by", "alice", "--hours", "25") == 2
    assert "between 1 and 24" in capsys.readouterr().err
    assert cli(ledger, "approve", request_id, "--by", "alice", "--hours", "0") == 2


def test_a_scope_that_approves_everything_is_refused(ledger, request_id, capsys):
    assert cli(ledger, "approve", request_id, "--by", "alice", "--paths", "**") == 2
    assert "everything" in capsys.readouterr().err
    assert recheck(ledger) == Decision.HUMAN


def test_an_agent_cannot_approve_through_the_command(ledger, request_id, capsys):
    assert cli(ledger, "approve", request_id, "--by", "builder-1") == 2
    assert "a person must answer" in capsys.readouterr().err
    assert cli(ledger, "approve", request_id, "--by", "builder-1:Explore:x") == 2
    assert recheck(ledger) == Decision.HUMAN


def test_only_listed_approvers_may_approve(ledger, request_id, tmp_path, capsys):
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text("version: v\napprovals:\n  approvers: [alice]\n", encoding="utf-8")
    assert cli(ledger, "approve", request_id, "--by", "mallory", policy_file=policy_file) == 2
    assert "not in the policy's list" in capsys.readouterr().err
    assert cli(ledger, "approve", request_id, "--by", "alice", policy_file=policy_file) == 0


def test_unknown_requests_and_a_missing_ledger_are_input_errors(ledger, request_id, tmp_path):
    assert cli(ledger, "approve", "req-ffffffffffff", "--by", "alice") == 2
    empty = Ledger(tmp_path / "none.jsonl")
    assert cli(empty, "list") == 2


def test_an_irreversible_tool_cannot_be_widened_from_the_command_line(ledger, capsys):
    act = action(tool_id="danger.irreversible", paths=["ops/x.sh"])
    rid = run_check(act, pol(), REGISTRY, ledger=ledger).approval_request
    assert cli(ledger, "approve", rid, "--by", "alice", "--paths", "ops/**") == 2
    assert "exact call" in capsys.readouterr().err
    assert cli(ledger, "approve", rid, "--by", "alice") == 0  # the exact call, single use
    assert run_check(act, pol(), REGISTRY, ledger=ledger).decision == Decision.ALLOW
    assert run_check(act, pol(), REGISTRY, ledger=ledger).decision != Decision.ALLOW


def test_reject_revokes_and_is_recorded(ledger, request_id, capsys):
    cli(ledger, "approve", request_id, "--by", "alice")
    assert cli(ledger, "reject", request_id, "--by", "alice") == 0
    assert recheck(ledger) == Decision.HUMAN
    capsys.readouterr()
    cli(ledger, "list", "--all")
    assert "rejected" in capsys.readouterr().out


def test_four_eyes_shows_in_the_listing_and_counts_people(ledger, capsys):
    from jarvis.core.schemas import ProjectConfig

    project = ProjectConfig(
        project="demo",
        mode="brownfield",
        stack="python",
        tiers={"builder": "standard"},
        tools_enabled=["*"],
        environments=["dev"],
        no_go_paths=["billing/**"],
        data_policy={"allowed_models": ["any"]},
    )
    rid = run_check(
        action(paths=["billing/x.py"]), pol(), REGISTRY, project, ledger=ledger
    ).approval_request
    cli(ledger, "list")
    assert "FOUR-EYES" in capsys.readouterr().out
    cli(ledger, "approve", rid, "--by", "alice")
    cli(ledger, "list")
    assert "partly approved (1 of 2)" in capsys.readouterr().out
    cli(ledger, "approve", rid, "--by", "bob")
    cli(ledger, "list", "--all")
    assert "approved (2 of 2)" in capsys.readouterr().out


def test_the_log_command_will_not_forge_an_approval(ledger, capsys):
    base = ["log", "--ledger", str(ledger.path), "--agent", "x", "--on-behalf-of", "y"]
    for kind in ("approval_granted", "approval_denied"):
        assert main([*base, "--kind", kind]) == 2
    assert "agentctl approvals" in capsys.readouterr().err
    assert main([*base, "--kind", "note"]) == 0


# ---------- the hook ----------


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / ".agents").mkdir(parents=True)
    for name in ("policy.yaml", "tool-registry.yaml"):
        shutil.copy(EXAMPLES / name, root / ".agents" / name)
    return root


def ctx(project, tmp_path, mode=HookMode.ENFORCE, **cfg):
    source = DirectorySource(project) if mode is HookMode.ENFORCE else None
    return HookContext(
        HookConfig(on_behalf_of="alice", **cfg), mode, project, tmp_path / "state", source
    )


def pre(project, tool_use_id="tu_1", event="PreToolUse"):
    return {
        "hook_event_name": event,
        "session_id": "s1",
        "cwd": str(project),
        "tool_name": "Edit",
        "tool_use_id": tool_use_id,
        "tool_input": {
            "file_path": str(project / ".github" / "workflows" / "ci.yml"),
            "old_string": "a",
            "new_string": "b",
        },
    }


def approvals(project):
    ledger = Ledger(project / ".agents" / "ledger" / "events.jsonl")
    return [r["event"] for r in ledger.records() if r["event"]["kind"] == "approval_granted"]


def test_an_unattended_denial_tells_the_agent_which_request_a_person_can_answer(project, tmp_path):
    result = handle(pre(project), ctx(project, tmp_path, non_interactive=True))
    reason = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "approval request req-" in reason and "agentctl approvals approve" in reason


def test_a_person_who_answers_yes_in_the_terminal_is_recorded_as_approving(project, tmp_path):
    context = ctx(project, tmp_path)
    handle(pre(project), context)  # the gate says ask
    assert (tmp_path / "state" / "asks" / "tu_1.json").exists()
    assert approvals(project) == []
    handle(pre(project, event="PostToolUse"), context)  # the call ran, so they said yes
    (event,) = approvals(project)
    assert event["agent_id"] == "jarvis-hook" and event["on_behalf_of"] == "alice"
    assert event["data"]["grant"]["via"] == "claude-code-prompt"
    assert event["data"]["grant"]["tool_use_id"] == "tu_1" and "scope" in event["data"]["grant"]
    assert event["data"]["grant"]["scope"] is None  # a record, never a reusable approval
    handle(pre(project, event="PostToolUse"), context)  # a repeat does not record twice
    assert len(approvals(project)) == 1


def test_a_prompt_approval_is_never_a_reusable_grant(project, tmp_path):
    from datetime import UTC, datetime

    from jarvis.core.approvals import scan_approvals
    from jarvis.core.schemas.policy import ApprovalSettings

    context = ctx(project, tmp_path)
    handle(pre(project), context)
    handle(pre(project, event="PostToolUse"), context)
    ledger = Ledger(project / ".agents" / "ledger" / "events.jsonl")
    assert scan_approvals(ledger.records(), datetime.now(UTC), ApprovalSettings()).grants == []


def test_nothing_is_recorded_when_the_call_failed_or_was_never_asked_about(project, tmp_path):
    context = ctx(project, tmp_path)
    handle(pre(project), context)
    handle(pre(project, event="PostToolUseFailure"), context)
    assert approvals(project) == []
    handle(pre(project, tool_use_id="never_asked", event="PostToolUse"), context)
    assert approvals(project) == []


def test_log_only_mode_asks_nobody_so_records_no_approval(project, tmp_path):
    context = ctx(project, tmp_path, HookMode.LOG_ONLY)
    handle(pre(project), context)
    handle(pre(project, event="PostToolUse"), context)
    assert approvals(project) == []
    assert not (tmp_path / "state" / "asks").exists()
