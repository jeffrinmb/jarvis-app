"""The approval flow: requests built by the gate, grants that expire, and what can never be
approved (P2.7, ADR 0009, ASI09, RT-13)."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from helpers import action, default_registry, policy, rule
from jarvis.core.approvals import APPROVAL_AGENT, is_agent_name, scan_approvals
from jarvis.core.ledger import Ledger
from jarvis.core.policy import run_check
from jarvis.core.schemas import Decision, Event, EventKind, ProjectConfig
from jarvis.core.schemas.approval import ApprovalGrant, ApprovalScope, ProposedScope
from jarvis.core.schemas.policy import ApprovalSettings

REGISTRY = default_registry()


def pol(**over):
    base = {"human_required": [rule("src-needs-a-person", paths=["src/**"])]}
    return policy(**{**base, **over})


def src_edit(**over):
    return action(**{"paths": ["src/x.py"], **over})


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "events.jsonl")


def check(ledger, act, policy_=None, project=None, now=None):
    return run_check(act, policy_ or pol(), REGISTRY, project, ledger=ledger, now=now)


def kinds(ledger):
    return [r["event"]["kind"] for r in ledger.records()]


def grant(
    ledger,
    request_id,
    *,
    approver="alice",
    task="T-1",
    tools=("fs.write",),
    paths=("src/**",),
    max_diff_lines=50,
    hours=4,
    fingerprint=None,
    single_use=False,
    agent=APPROVAL_AGENT,
):
    """Write a grant the way ``agentctl approvals approve`` does."""
    scope = ApprovalScope(
        task_id=task,
        tools=list(tools),
        paths=list(paths),
        max_diff_lines=max_diff_lines,
        fingerprint=fingerprint,
        single_use=single_use,
        expires_at=datetime.now(UTC) + timedelta(hours=hours),
    )
    body = ApprovalGrant(request_id=request_id, approver=approver, via="cli", scope=scope)
    return ledger.append(
        Event(
            kind=EventKind.APPROVAL_GRANTED,
            agent_id=agent,
            on_behalf_of=approver,
            task_id=task,
            data={"grant": body.model_dump(mode="json")},
        )
    )


def open_request(ledger, act=None, **kw):
    verdict = check(ledger, act or src_edit(), **kw)
    assert verdict.decision == Decision.HUMAN
    assert verdict.approval_request
    return verdict.approval_request


# ---------- requests are built by the gate ----------


def test_a_pending_decision_opens_one_request_and_a_retry_reuses_it(ledger):
    first = open_request(ledger)
    again = open_request(ledger)
    assert first == again
    assert kinds(ledger).count("approval_requested") == 1
    state = scan_approvals(ledger.records(), datetime.now(UTC), ApprovalSettings())
    request = state.requests[first]
    assert request.tool_id == "fs.write" and request.paths == ["src/x.py"]
    assert [r.rule_id for r in request.rules] == ["src-needs-a-person"]
    # the held call counts toward the task's total, so the proposal leaves room for one retry
    assert request.proposed_scope.max_diff_lines == 10


def test_a_different_call_or_task_gets_its_own_request(ledger):
    a = open_request(ledger)
    b = open_request(ledger, action(paths=["src/y.py"]))
    c = open_request(ledger, src_edit(task_id="T-2"))
    assert len({a, b, c}) == 3


def test_the_request_comes_from_the_verdict_not_from_agent_text(ledger):
    open_request(ledger)
    event = next(r["event"] for r in ledger.records() if r["event"]["kind"] == "approval_requested")
    assert set(event["data"]) == {"request"}
    assert event["agent_id"] == "builder-1"  # recorded as the requester, not as an approver


def test_a_dry_run_opens_no_request(ledger):
    run_check(src_edit(), pol(), REGISTRY, ledger=ledger, record=False)
    assert "approval_requested" not in kinds(ledger)


def test_allowed_and_denied_calls_open_no_request(ledger):
    check(ledger, action())  # docs: auto-approved
    check(ledger, action(tool_id="nope"))  # unregistered: denied
    assert "approval_requested" not in kinds(ledger)


# ---------- an approval lets the call through ----------


def test_an_approval_lets_the_same_call_through_and_is_recorded(ledger):
    rid = open_request(ledger)
    grant(ledger, rid)
    verdict = check(ledger, src_edit())
    assert verdict.decision == Decision.ALLOW
    assert "approved by alice" in verdict.reason
    assert f"approval:{rid}" in {m.rule_id for m in verdict.matches}
    decisions = [r["event"] for r in ledger.records() if r["event"]["kind"] == "policy_decision"]
    assert decisions[-1]["data"]["approval_grant_ids"]
    assert ledger.verify().ok


def test_an_approval_covers_the_scope_it_names_and_nothing_else(ledger):
    rid = open_request(ledger)
    grant(ledger, rid, paths=["src/**"], max_diff_lines=20)
    assert check(ledger, src_edit(paths=["src/deep/z.py"], diff_lines=10)).decision == (
        Decision.ALLOW
    )
    # outside the paths, another task, another tool, and over the size limit are not covered
    assert check(ledger, action(paths=["lib/x.py"], diff_lines=1)).decision == Decision.HUMAN
    assert check(ledger, src_edit(task_id="T-2")).decision == Decision.HUMAN
    assert check(ledger, src_edit(paths=["src/x.py", "other/y.py"])).decision == Decision.HUMAN
    assert check(ledger, src_edit(diff_lines=15)).decision == Decision.HUMAN  # 15 + 15 > 20


def test_every_path_must_be_inside_the_scope_not_just_one(ledger):
    rid = open_request(ledger)
    grant(ledger, rid, paths=["src/**"], max_diff_lines=100)
    mixed = src_edit(paths=["src/x.py", "other/y.py"])
    assert check(ledger, mixed).decision == Decision.HUMAN
    assert check(ledger, src_edit(paths=["src/x.py", "src/z.py"])).decision == Decision.ALLOW


def test_a_default_deny_policy_is_not_lifted_by_an_approval(ledger):
    """No rule matched, so nothing named the refusal; the decision itself must stop it."""
    strict = pol(default="deny", human_required=[])
    assert check(ledger, src_edit(), strict).decision == Decision.DENY
    grant(ledger, "req-000000000000", paths=["src/**"], max_diff_lines=100)
    assert check(ledger, src_edit(), strict).decision == Decision.DENY


def test_the_lists_of_what_may_and_may_not_be_approved_never_overlap():
    from jarvis.core.approvals import rules

    assert not rules.NOT_APPROVABLE & rules.APPROVABLE_BUILTINS
    assert "shell.run" in rules.OPAQUE_TOOLS
    assert rules.FOUR_EYES <= rules.APPROVABLE_BUILTINS


def test_one_approvable_reason_does_not_make_a_mixed_verdict_approvable():
    from helpers import tool
    from jarvis.core.approvals import profile_for
    from jarvis.core.schemas.verdict import RuleMatch

    human = RuleMatch(rule_id="r", category="human_required", effect=Decision.HUMAN, reason="x")
    trifecta = RuleMatch(
        rule_id="builtin:lethal-trifecta", category="builtin", effect=Decision.HUMAN, reason="x"
    )
    assert profile_for([human], tool("fs.write")).approvable
    assert not profile_for([human, trifecta], tool("fs.write")).approvable
    assert not profile_for([human], tool("shell.run")).approvable


def test_unknown_size_is_never_covered_for_a_write(ledger):
    rid = open_request(ledger)
    grant(ledger, rid)
    assert check(ledger, src_edit(diff_lines=None)).decision == Decision.HUMAN


def test_an_expired_approval_stops_counting(ledger):
    rid = open_request(ledger)
    grant(ledger, rid, hours=1)
    soon = datetime.now(UTC) + timedelta(minutes=30)
    later = datetime.now(UTC) + timedelta(hours=2)
    assert check(ledger, src_edit(), now=soon).decision == Decision.ALLOW
    assert check(ledger, src_edit(), now=later).decision == Decision.HUMAN


def test_the_gate_caps_an_expiry_the_command_line_would_have_refused(ledger):
    rid = open_request(ledger)
    grant(ledger, rid, hours=100)  # a hand-written grant, valid for 100 hours
    assert check(ledger, src_edit()).decision == Decision.HUMAN  # policy cap is 24


def test_a_policy_can_shorten_the_cap(ledger):
    rid = open_request(ledger)
    grant(ledger, rid, hours=3)
    short = pol(approvals={"max_scope_hours": 2})
    assert check(ledger, src_edit(), short).decision == Decision.HUMAN


def test_only_listed_approvers_count_when_the_policy_lists_them(ledger):
    listed = pol(approvals={"approvers": ["alice"]})
    rid = open_request(ledger, policy_=listed)
    grant(ledger, rid, approver="mallory")
    assert check(ledger, src_edit(), listed).decision == Decision.HUMAN
    grant(ledger, rid, approver="alice")
    assert check(ledger, src_edit(), listed).decision == Decision.ALLOW


def test_a_grant_not_written_by_the_approval_command_does_not_count(ledger):
    rid = open_request(ledger)
    grant(ledger, rid, agent="builder-1")
    assert check(ledger, src_edit()).decision == Decision.HUMAN


def test_an_agent_cannot_approve_its_own_call(ledger):
    rid = open_request(ledger)
    grant(ledger, rid, approver="builder-1")
    grant(ledger, rid, approver="builder-1:Explore:abc")
    assert check(ledger, src_edit()).decision == Decision.HUMAN
    assert is_agent_name("claude-code:Explore:x", "claude-code")
    assert not is_agent_name("alice", "claude-code")


def test_a_rejection_revokes_earlier_approvals_and_a_later_one_restores_it(ledger):
    rid = open_request(ledger)
    grant(ledger, rid)
    ledger.append(
        Event(
            kind=EventKind.APPROVAL_DENIED,
            agent_id=APPROVAL_AGENT,
            on_behalf_of="alice",
            data={"request_id": rid, "approver": "alice"},
        )
    )
    assert check(ledger, src_edit()).decision == Decision.HUMAN
    grant(ledger, rid)
    assert check(ledger, src_edit()).decision == Decision.ALLOW


# ---------- what an approval can never do ----------


def test_an_approval_never_overrides_a_deny(ledger):
    deny_policy = pol(never=[rule("no-secrets", paths=["src/**"])])
    verdict = check(ledger, src_edit(), deny_policy)
    assert verdict.decision == Decision.DENY and verdict.approval_request is None
    grant(ledger, "req-000000000000")
    for act in (src_edit(), action(tool_id="nope"), src_edit(role="intruder")):
        assert check(ledger, act, deny_policy).decision == Decision.DENY


@pytest.mark.parametrize(
    "act",
    [
        pytest.param(action(paths=["/etc/passwd"]), id="suspicious-path"),
        pytest.param(action(paths=["../x"]), id="escapes-the-repo"),
    ],
)
def test_gates_a_stored_approval_must_not_lift_open_no_card(ledger, act):
    verdict = check(ledger, act)
    assert verdict.decision == Decision.HUMAN and verdict.approval_request is None
    assert "approval_requested" not in kinds(ledger)


def test_a_tainted_session_using_an_irreversible_tool_is_not_approvable(ledger):
    tainted = src_edit(tool_id="danger.irreversible", session={"trust_labels_seen": ["untrusted"]})
    verdict = check(ledger, tainted)
    assert verdict.decision == Decision.HUMAN and verdict.approval_request is None


def test_the_lethal_trifecta_is_not_approvable(ledger):
    trifecta = action(
        tool_id="web.post",
        paths=[],
        session={"trust_labels_seen": ["untrusted"], "data_classes_seen": ["restricted"]},
    )
    verdict = check(ledger, trifecta)
    assert verdict.trifecta and verdict.approval_request is None


def test_an_opaque_shell_is_never_approvable(ledger):
    shell_registry = default_registry()
    from helpers import tool

    shell = tool("shell.run", risk="medium", approval="human")
    registry = type(shell_registry)(tools=[*shell_registry.tools, shell])
    verdict = run_check(
        action(tool_id="shell.run", paths=[]), pol(), registry, ledger=ledger, now=None
    )
    assert verdict.decision == Decision.HUMAN and verdict.approval_request is None


def test_a_budget_pause_is_approvable_and_the_approval_is_the_way_out(ledger):
    budgeted = pol(budgets={"max_calls_per_task": 2})
    check(ledger, action(), budgeted)
    check(ledger, action(paths=["docs/b.md"]), budgeted)
    paused = check(ledger, action(paths=["docs/c.md"]), budgeted)
    assert paused.decision == Decision.HUMAN and paused.approval_request
    grant(ledger, paused.approval_request, paths=["docs/**"])
    assert check(ledger, action(paths=["docs/c.md"]), budgeted).decision == Decision.ALLOW


# ---------- four-eyes ----------


NO_GO = ProjectConfig(
    project="demo",
    mode="brownfield",
    stack="python",
    tiers={"builder": "standard"},
    tools_enabled=["*"],
    environments=["dev"],
    no_go_paths=["billing/**"],
    data_policy={"allowed_models": ["any"]},
)


def test_a_no_go_zone_needs_two_different_people(ledger):
    billing = action(paths=["billing/x.py"])
    verdict = check(ledger, billing, project=NO_GO)
    assert verdict.decision == Decision.HUMAN and verdict.approval_request
    rid = verdict.approval_request
    state = scan_approvals(ledger.records(), datetime.now(UTC), ApprovalSettings())
    assert state.requests[rid].four_eyes is True

    grant(ledger, rid, paths=["billing/**"])
    assert check(ledger, billing, project=NO_GO).decision == Decision.HUMAN  # one is not enough
    grant(ledger, rid, paths=["billing/**"])  # the same person again
    assert check(ledger, billing, project=NO_GO).decision == Decision.HUMAN
    grant(ledger, rid, approver="bob", paths=["billing/**"])
    assert check(ledger, billing, project=NO_GO).decision == Decision.ALLOW


# ---------- high-risk and irreversible tools ----------


def test_an_irreversible_tool_needs_a_single_use_approval_of_the_exact_call(ledger):
    act = action(tool_id="danger.irreversible", paths=["ops/x.sh"])
    verdict = check(ledger, act)
    assert verdict.decision == Decision.COUNCIL and verdict.approval_request
    state = scan_approvals(ledger.records(), datetime.now(UTC), ApprovalSettings())
    proposed = state.requests[verdict.approval_request].proposed_scope
    assert proposed.single_use and proposed.fingerprint

    broad = dict(tools=("danger.irreversible",), paths=("ops/**",))
    grant(ledger, verdict.approval_request, **broad)  # a bounded scope is not enough
    assert check(ledger, act).decision == Decision.COUNCIL

    grant(
        ledger,
        verdict.approval_request,
        fingerprint=proposed.fingerprint,
        single_use=True,
        **{**broad, "paths": ("ops/x.sh",)},
    )
    assert check(ledger, act).decision == Decision.ALLOW
    assert check(ledger, act).decision == Decision.COUNCIL  # spent: it covered one call


def test_a_read_only_tool_needs_no_size_limit(ledger):
    review = pol(human_required=[rule("repo-reads", tools=["git.read"])])
    act = action(tool_id="git.read", paths=["src/x.py"], diff_lines=None)
    rid = check(ledger, act, review).approval_request
    assert rid
    grant(ledger, rid, tools=("git.read",), max_diff_lines=None)
    assert check(ledger, act, review).decision == Decision.ALLOW


# ---------- the schemas ----------


@pytest.mark.parametrize("bad", ["*", "**", "**/*", "/abs/**", "../x", "C:/x", ""])
def test_a_scope_cannot_name_everything_or_leave_the_repository(bad):
    with pytest.raises(ValidationError):
        ProposedScope(tools=["fs.write"], paths=[bad])


def test_a_scope_names_exact_tools_and_single_use_needs_a_call():
    with pytest.raises(ValidationError, match="exact"):
        ProposedScope(tools=["fs.*"])
    with pytest.raises(ValidationError, match="exact call"):
        ProposedScope(tools=["fs.write"], single_use=True)


def test_a_grant_needs_what_its_channel_needs():
    scope = ApprovalScope(
        task_id="T",
        tools=["fs.write"],
        paths=["src/**"],
        max_diff_lines=5,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    with pytest.raises(ValidationError):
        ApprovalGrant(approver="a", via="cli", scope=scope)  # no request id
    with pytest.raises(ValidationError):
        ApprovalGrant(request_id="req-aaaaaaaaaaaa", approver="a", via="cli")  # no scope
    with pytest.raises(ValidationError):
        ApprovalGrant(approver="a", via="claude-code-prompt")  # no call named
    with pytest.raises(ValidationError):
        ApprovalGrant(tool_use_id="t", approver="a", via="claude-code-prompt", scope=scope)


def test_a_policy_cannot_set_an_unbounded_scope_lifetime():
    for hours in (0, 169):
        with pytest.raises(ValidationError):
            ApprovalSettings(max_scope_hours=hours)
