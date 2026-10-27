from concurrent.futures import ThreadPoolExecutor

import pytest

from helpers import action, default_registry, policy, rule
from jarvis.core.ledger import Ledger
from jarvis.core.policy import prior_diff_lines, run_check
from jarvis.core.schemas import Decision, EventKind

REGISTRY = default_registry()


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "ledger" / "events.jsonl")


def events(ledger):
    return [r["event"] for r in ledger.records()]


def test_without_a_ledger_the_check_is_a_plain_evaluation():
    assert run_check(action(), policy(), REGISTRY).decision is Decision.ALLOW


def test_every_decision_is_recorded_and_the_chain_still_verifies(ledger):
    verdict = run_check(action(), policy(), REGISTRY, ledger=ledger)
    assert verdict.decision is Decision.ALLOW
    (event,) = events(ledger)
    assert event["kind"] == EventKind.POLICY_DECISION.value
    assert event["decision"] == "allow"
    assert event["agent_id"] == "builder-1" and event["on_behalf_of"] == "alice"
    assert event["tool"] == {"id": "fs.write", "version": "1"}
    assert event["risk"] == "low"
    assert event["data"]["policy_version"] == "test-1"
    assert event["data"]["matches"] == [
        {"rule_id": "docs", "category": "auto_approve", "effect": "allow"}
    ]
    assert ledger.verify().ok


def test_denied_and_pending_decisions_are_recorded_too(ledger):
    run_check(action(tool_id="mystery"), policy(), REGISTRY, ledger=ledger)
    run_check(action(paths=["src/x.py"]), policy(), REGISTRY, ledger=ledger)
    assert [e["decision"] for e in events(ledger)] == ["deny", "human"]


def test_dry_run_reads_history_but_writes_nothing(ledger):
    run_check(action(diff_lines=150), policy(), REGISTRY, ledger=ledger)
    before = ledger.path.read_bytes()
    verdict = run_check(action(diff_lines=60), policy(), REGISTRY, ledger=ledger, record=False)
    assert verdict.total_diff_lines == 210  # saw the 150 already recorded
    assert verdict.decision is Decision.HUMAN
    assert ledger.path.read_bytes() == before


def test_cumulative_limit_stops_a_change_split_into_small_pieces(ledger):
    pol = policy(auto_approve=[rule("small", paths=["docs/**"], max_diff_lines=10)])
    results = [
        run_check(action(diff_lines=4), pol, REGISTRY, ledger=ledger).decision for _ in range(3)
    ]
    assert results == [Decision.ALLOW, Decision.ALLOW, Decision.HUMAN]  # 4, 8, then 12 > 10


def test_cumulative_totals_are_per_task(ledger):
    pol = policy(auto_approve=[rule("small", paths=["docs/**"], max_diff_lines=10)])
    for _ in range(2):
        run_check(action(task_id="T-1", diff_lines=4), pol, REGISTRY, ledger=ledger)
    other = run_check(action(task_id="T-2", diff_lines=4), pol, REGISTRY, ledger=ledger)
    assert other.decision is Decision.ALLOW and other.total_diff_lines == 4


def test_pending_human_decisions_count_but_denied_ones_do_not(ledger):
    pol = policy(
        auto_approve=[rule("small", paths=["docs/**"], max_diff_lines=10)],
        human_required=[rule("auth", paths=["**/auth/**"])],
    )
    run_check(action(paths=["src/auth/x.py"], diff_lines=8), pol, REGISTRY, ledger=ledger)  # human
    run_check(action(role="analyst", diff_lines=100), pol, REGISTRY, ledger=ledger)  # denied
    nxt = run_check(action(diff_lines=4), pol, REGISTRY, ledger=ledger)
    assert nxt.total_diff_lines == 12  # 8 from the pending decision, nothing from the denied one
    assert nxt.decision is Decision.HUMAN


def test_actions_without_a_task_have_no_cumulative_history(ledger):
    run_check(action(task_id=None, diff_lines=150), policy(), REGISTRY, ledger=ledger)
    again = run_check(action(task_id=None, diff_lines=150), policy(), REGISTRY, ledger=ledger)
    assert again.total_diff_lines == 150


def test_prior_diff_lines_ignores_other_events_and_bad_data():
    records = [
        {
            "event": {
                "kind": "note",
                "task_id": "T-1",
                "decision": "allow",
                "data": {"diff_lines": 9},
            }
        },
        {
            "event": {
                "kind": "policy_decision",
                "task_id": "T-1",
                "decision": "allow",
                "data": {"diff_lines": "lots"},
            }
        },
        {
            "event": {
                "kind": "policy_decision",
                "task_id": "T-1",
                "decision": "allow",
                "data": {"diff_lines": 3},
            }
        },
        {
            "event": {
                "kind": "policy_decision",
                "task_id": "T-1",
                "decision": "deny",
                "data": {"diff_lines": 50},
            }
        },
        {"event": {"kind": "policy_decision", "task_id": "T-1", "decision": "allow"}},
    ]
    assert prior_diff_lines(records, "T-1") == 3
    assert prior_diff_lines(records, None) == 0


# ---------- the gate fails closed when it cannot audit ----------


def test_a_corrupt_ledger_denies_instead_of_allowing(ledger):
    run_check(action(), policy(), REGISTRY, ledger=ledger)
    raw = ledger.path.read_bytes().replace(b'"agent_id":"builder-1"', b'"agent_id":"mallory"', 1)
    ledger.path.write_bytes(raw)
    verdict = run_check(action(), policy(), REGISTRY, ledger=ledger)
    assert verdict.decision is Decision.DENY
    assert "builtin:audit-unavailable" in {m.rule_id for m in verdict.matches}


def test_a_stuck_lock_denies_instead_of_allowing(tmp_path):
    ledger = Ledger(tmp_path / "events.jsonl", lock_timeout=0.1)
    (tmp_path / "events.jsonl.lock").write_text("999999")
    verdict = run_check(action(), policy(), REGISTRY, ledger=ledger)
    assert verdict.decision is Decision.DENY
    assert "audit ledger unavailable" in verdict.reason


def test_a_partial_final_line_denies(ledger):
    run_check(action(), policy(), REGISTRY, ledger=ledger)
    with open(ledger.path, "ab") as fh:
        fh.write(b"half a record")
    assert run_check(action(), policy(), REGISTRY, ledger=ledger).decision is Decision.DENY


# ---------- concurrency ----------


def test_concurrent_checks_cannot_overshoot_a_cumulative_limit(tmp_path):
    """Read-decide-append is one transaction, so six racing checks see each other."""
    path = tmp_path / "events.jsonl"
    pol = policy(auto_approve=[rule("small", paths=["docs/**"], max_diff_lines=10)])

    def one(_):
        local = Ledger(path, lock_timeout=30)
        return run_check(action(diff_lines=4), pol, REGISTRY, ledger=local).decision

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(one, range(6)))

    assert results.count(Decision.ALLOW) == 2  # 4 + 4 = 8 fits; a third would make 12
    assert results.count(Decision.HUMAN) == 4
    assert Ledger(path).verify().records == 6
