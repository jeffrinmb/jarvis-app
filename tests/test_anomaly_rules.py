"""Anomaly rules on the ledger (P2.8a, ASI10, red-team case RT-14)."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from helpers import default_registry
from jarvis.core.ledger import Ledger
from jarvis.core.policy import Anomaly, Usage, detect_anomalies, run_check
from jarvis.core.schemas import Action, Decision, EventKind, Policy, RiskClass
from jarvis.core.schemas.policy import Anomalies

# Events are stamped with the real clock, so "now" must be the real clock too.
NOW = datetime.now(UTC)


def policy(rules=True, **anomalies):
    return Policy(
        version="t",
        default=Decision.HUMAN,
        auto_approve=[{"id": "writes", "description": "w", "when": {"tools": ["fs.write"]}}]
        if rules
        else [],
        anomalies=anomalies or None,
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


def check(ledger, pol, action=None, now=NOW):
    return run_check(action or act(), pol, default_registry(), None, ledger=ledger, now=now)


def rule_ids(verdict):
    return {m.rule_id for m in verdict.matches}


def kinds(ledger):
    return [r["event"]["kind"] for r in ledger.records()]


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "events.jsonl")


# ---------- schema ----------


def test_anomalies_are_optional_and_validated():
    assert Policy(version="x").anomalies is None
    with pytest.raises(ValidationError, match="together"):
        Anomalies(max_denials=3)
    with pytest.raises(ValidationError, match="never trip|could never"):
        Anomalies(max_denials=5, denial_window=3)
    with pytest.raises(ValidationError, match="together"):
        Anomalies(burst_calls=5)
    with pytest.raises(ValidationError):
        Anomalies(on_anomaly="allow")
    assert Anomalies().on_anomaly == "human"


def test_anomaly_settings_load_from_yaml_text():
    from jarvis.core.schemas import parse_yaml_model

    text = "version: v\nanomalies:\n  new_tool: true\n  new_tool_risk_at_least: high\n"
    assert parse_yaml_model(text, Policy).anomalies.new_tool_risk_at_least == RiskClass.HIGH


# ---------- RT-14: retrying a denied action with small variations ----------


def test_retries_with_variations_trip_the_denial_detector(ledger):
    pol = policy(max_denials=3, denial_window=10)
    for i in range(3):  # three different attempts, all denied (unregistered tool names)
        assert check(ledger, pol, act(tool=f"shell-variant-{i}", path=f"p{i}")).decision == (
            Decision.DENY
        )
    verdict = check(ledger, pol)  # an ordinary write is now paused for a person
    assert verdict.decision == Decision.HUMAN
    assert "builtin:anomaly-denials" in rule_ids(verdict)
    assert EventKind.ANOMALY_DETECTED.value in kinds(ledger)


def test_fewer_denials_than_the_limit_do_not_trip_it(ledger):
    pol = policy(max_denials=3, denial_window=10)
    for i in range(2):
        check(ledger, pol, act(tool=f"nope-{i}"))
    assert check(ledger, pol).decision == Decision.ALLOW


def test_old_denials_scroll_out_of_the_window(ledger):
    pol = policy(max_denials=2, denial_window=4)
    check(ledger, pol, act(tool="nope-1"))
    check(ledger, pol, act(tool="nope-2"))
    # the pause itself is not a denial, so ordinary calls push the denials out of the window
    decisions = [check(ledger, pol, act(path=f"f{i}.md")).decision for i in range(5)]
    assert decisions[-1] == Decision.ALLOW


def test_denials_in_another_task_do_not_count(ledger):
    pol = policy(max_denials=2, denial_window=5)
    for i in range(3):
        check(ledger, pol, act(task="other", tool=f"nope-{i}"))
    assert check(ledger, pol, act(task="t1")).decision == Decision.ALLOW


# ---------- a tool never used before ----------


def warm(ledger, pol, n=5):
    for i in range(n):
        check(ledger, pol, act(path=f"warm{i}.md"))


def test_a_new_risky_tool_is_flagged_once_there_is_a_baseline(ledger):
    pol = policy(new_tool=True, new_tool_min_history=5, on_anomaly="deny")
    warm(ledger, pol)
    verdict = check(ledger, pol, act(tool="web.post"))  # high risk, never used
    assert verdict.decision == Decision.DENY
    assert "builtin:anomaly-new-tool" in rule_ids(verdict)


def test_a_fresh_ledger_does_not_flag_everything(ledger):
    pol = policy(new_tool=True, new_tool_min_history=5, on_anomaly="deny")
    verdict = check(ledger, pol, act(tool="web.post"))
    assert "builtin:anomaly-new-tool" not in rule_ids(verdict)


def test_low_risk_and_already_used_tools_are_not_flagged(ledger):
    pol = policy(new_tool=True, new_tool_min_history=5, on_anomaly="deny")
    warm(ledger, pol)
    assert check(ledger, pol, act(path="more.md")).decision == Decision.ALLOW  # seen, low risk
    low = policy(new_tool=True, new_tool_min_history=5, new_tool_risk_at_least="high")
    assert "builtin:anomaly-new-tool" not in rule_ids(check(ledger, low, act(tool="mail.send")))


def test_a_denied_attempt_does_not_make_a_tool_seen(ledger):
    pol = policy(new_tool=True, new_tool_min_history=5, on_anomaly="deny")
    warm(ledger, pol)
    first = check(ledger, pol, act(tool="web.post"))
    second = check(ledger, pol, act(tool="web.post"))
    assert first.decision == second.decision == Decision.DENY


# ---------- bursts ----------


def test_a_burst_of_calls_trips_the_rate_detector_and_then_clears(ledger):
    pol = policy(burst_calls=3, burst_seconds=10)
    decisions = [check(ledger, pol, act(path=f"f{i}.md")).decision for i in range(5)]
    assert decisions == [Decision.ALLOW] * 3 + [Decision.HUMAN] * 2
    later = NOW + timedelta(seconds=60)
    assert check(ledger, pol, act(path="calm.md"), now=later).decision == Decision.ALLOW


# ---------- what a detection does ----------


def test_alert_records_the_anomaly_and_does_not_change_the_decision(ledger):
    pol = policy(burst_calls=1, burst_seconds=60, on_anomaly="alert")
    check(ledger, pol)
    verdict = check(ledger, pol, act(path="b.md"))
    assert verdict.decision == Decision.ALLOW
    assert kinds(ledger).count(EventKind.ANOMALY_DETECTED.value) == 1


def test_alert_can_never_approve_something_no_rule_allows(ledger):
    pol = policy(rules=False, burst_calls=1, burst_seconds=60, on_anomaly="alert")
    check(ledger, pol)
    assert check(ledger, pol, act(path="b.md")).decision == Decision.HUMAN  # the policy default


def test_a_matching_allow_rule_cannot_override_a_detection(ledger):
    pol = policy(burst_calls=1, burst_seconds=60)
    check(ledger, pol)
    verdict = check(ledger, pol, act(path="b.md"))  # matches the auto_approve rule
    assert verdict.decision == Decision.HUMAN


def test_without_anomaly_settings_nothing_is_detected_or_recorded(ledger):
    pol = policy()
    for i in range(10):
        assert check(ledger, pol, act(path=f"f{i}.md")).decision == Decision.ALLOW
    assert EventKind.ANOMALY_DETECTED.value not in kinds(ledger)
    assert ledger.verify().ok


def test_detections_are_not_recorded_on_a_dry_run(ledger):
    pol = policy(burst_calls=1, burst_seconds=60)
    check(ledger, pol)
    run_check(act(path="b.md"), pol, default_registry(), None, ledger=ledger, record=False, now=NOW)
    assert EventKind.ANOMALY_DETECTED.value not in kinds(ledger)


# ---------- the detector is a pure function ----------


def test_detect_anomalies_reads_only_its_inputs():
    config = Anomalies(max_denials=2, denial_window=5, burst_calls=4, burst_seconds=10)
    quiet = Usage()
    assert detect_anomalies(config, quiet, 1) == []
    assert detect_anomalies(None, Usage(recent_denials=99), 1) == []
    loud = Usage(recent_denials=2, burst=4)
    ids = {a.rule_id for a in detect_anomalies(config, loud, 1)}
    assert ids == {"builtin:anomaly-denials", "builtin:anomaly-burst"}
    assert all(isinstance(a, Anomaly) for a in detect_anomalies(config, loud, 1))
