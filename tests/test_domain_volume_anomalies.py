"""Anomaly rules for new hosts and unusual outbound volume (P2.8c, ADR 0006, ASI10, RT-14)."""

import io
import json
import random
import shutil
from pathlib import Path

import pytest
import yaml

from helpers import action, default_registry, policy, rule
from jarvis.adapters.claude_code.mapping import MAX_DOMAINS, data_bytes_for, domains_for
from jarvis.cli import main
from jarvis.core.ledger import Ledger
from jarvis.core.ledger.summary import LedgerSummary, as_version_1
from jarvis.core.policy import run_check
from jarvis.core.policy.usage import Usage, detect_anomalies
from jarvis.core.schemas import Decision
from jarvis.core.schemas.policy import Anomalies
from test_ledger_summary import rewrite_active

REGISTRY = default_registry()


# ---------- what the hook extracts ----------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://Example.COM/a/b?q=secret#frag", ["example.com"]),
        ("https://user:p4ss@example.com:8443/x", ["example.com"]),  # no credentials, no port
        ("http://10.0.0.7:8080/admin", ["10.0.0.7"]),
        ("https://[2001:db8::1]/x", ["[2001:db8::1]"]),
        ("https://example.com./x", ["example.com"]),
        ("https://bücher.example/x", ["bücher.example"]),
        ("not a url", []),
        ("example.com/path", []),
    ],
)
def test_the_host_of_a_webfetch_url_is_taken_and_nothing_else(url, expected):
    assert domains_for("WebFetch", {"url": url}) == expected


def test_a_web_search_names_no_host_and_other_tools_none_either():
    assert domains_for("WebSearch", {"query": "see https://evil.example/x"}) == []
    assert domains_for("Bash", {"command": "curl https://evil.example/x"}) == []
    assert domains_for("WebFetch", {}) == []
    assert domains_for("WebFetch", {"url": 5}) == []


def test_an_mcp_tool_names_every_host_in_its_input_without_duplicates():
    tool_input = {
        "target": "https://a.example/x",
        "nested": {"list": ["see http://b.example/y and https://a.example/z", 7, None]},
    }
    assert sorted(domains_for("mcp__crm__sync", tool_input)) == ["a.example", "b.example"]


def test_the_number_of_hosts_is_capped():
    text = " ".join(f"https://h{i}.example/" for i in range(60))
    assert len(domains_for("mcp__x__y", {"t": text})) == MAX_DOMAINS


def test_the_data_size_is_the_size_of_the_input():
    assert data_bytes_for({}) == 2
    assert data_bytes_for({"a": "x" * 1000}) == len(
        json.dumps({"a": "x" * 1000}, separators=(",", ":"))
    )
    assert data_bytes_for({"a": "é"}) == len('{"a":"é"}'.encode())


# ---------- the rules ----------


def trip(**usage):
    return [a.rule_id for a in detect_anomalies(ANOM, Usage(**usage), 1)]


ANOM = Anomalies(
    new_domain=True,
    new_domain_min_history=3,
    max_call_bytes=1000,
    max_task_bytes=2500,
    on_anomaly="human",
)


def test_a_new_host_trips_only_after_a_baseline():
    assert trip(new_domains=("b.example",), domain_history=2) == []
    assert trip(new_domains=("b.example",), domain_history=3) == ["builtin:anomaly-new-domain"]
    assert trip(new_domains=(), domain_history=50) == []


def test_the_reason_names_the_new_hosts_but_not_more_than_three():
    found = detect_anomalies(
        ANOM, Usage(new_domains=("a.x", "b.x", "c.x", "d.x"), domain_history=9), 1
    )
    assert "a.x, b.x, c.x, ..." in found[0].reason


def test_one_big_call_trips_the_call_rule_and_the_total_trips_the_task_rule():
    assert trip(call_bytes=1000) == []
    assert trip(call_bytes=1001) == ["builtin:anomaly-call-bytes"]
    assert trip(task_bytes=1700, call_bytes=900) == ["builtin:anomaly-task-bytes"]
    assert trip(task_bytes=1600, call_bytes=900) == []  # exactly at the limit is not over it
    assert trip(task_bytes=1500, call_bytes=900) == []
    assert trip(task_bytes=9999, call_bytes=0) == []  # a call that sends nothing is not stopped


def test_every_detector_is_off_until_it_is_set():
    quiet = Anomalies()
    usage = Usage(new_domains=("a.x",), domain_history=99, task_bytes=10**9, call_bytes=10**9)
    assert detect_anomalies(quiet, usage, 1) == []


# ---------- the gate, end to end ----------

POLICY = policy(anomalies=ANOM, human_required=[rule("never-in-src", paths=["src/**"])])


def call(ledger, domains=(), size=100, tool="web.post", task="T-1", **over):
    act = action(
        task_id=task,
        tool_id=tool,
        paths=[],
        diff_lines=None if tool in ("web.post", "mail.send") else 3,
        domains=list(domains),
        data_bytes=size,
        **over,
    )
    return run_check(act, POLICY, REGISTRY, None, ledger=ledger)


def rule_ids(verdict):
    return {m.rule_id for m in verdict.matches}


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "events.jsonl")


def baseline(ledger, host="a.example", n=3):
    for _ in range(n):
        call(ledger, [host])


def test_a_new_host_after_a_baseline_needs_a_person(ledger):
    baseline(ledger)
    again = call(ledger, ["a.example"])
    assert "builtin:anomaly-new-domain" not in rule_ids(again)
    fresh = call(ledger, ["b.example"])
    assert "builtin:anomaly-new-domain" in rule_ids(fresh)
    assert fresh.decision is Decision.HUMAN and "b.example" in fresh.reason
    events = [r["event"] for r in ledger.records()]
    assert any(e["kind"] == "anomaly_detected" for e in events)


def test_the_decision_records_the_hosts_and_the_outbound_bytes_but_nothing_else(ledger):
    call(ledger, ["a.example"], size=321)
    (event,) = [r["event"] for r in ledger.records() if r["event"]["kind"] == "policy_decision"]
    assert event["data"]["domains"] == ["a.example"]
    assert event["data"]["outbound_bytes"] == 321


def test_a_denied_attempt_does_not_make_a_host_seen(ledger):
    baseline(ledger)
    denied = call(ledger, ["c.example"], role="analyst")  # not allowed for this role
    assert denied.decision is Decision.DENY
    later = call(ledger, ["c.example"])
    assert "builtin:anomaly-new-domain" in rule_ids(later)  # still new: retrying cannot launder it


def test_the_volume_rules_count_only_tools_that_send_data_outward(ledger):
    big = call(ledger, [], size=5000)  # web.post is external-comms
    assert "builtin:anomaly-call-bytes" in rule_ids(big)
    local = call(ledger, [], size=5000, tool="fs.write")  # writing a file sends nothing out
    assert not {"builtin:anomaly-call-bytes", "builtin:anomaly-task-bytes"} & rule_ids(local)


def test_many_small_calls_add_up_per_task_and_tasks_are_separate(ledger):
    for _ in range(2):
        assert "builtin:anomaly-task-bytes" not in rule_ids(call(ledger, size=900))
    third = call(ledger, size=900)  # 2700 bytes in total, over 2500
    assert "builtin:anomaly-task-bytes" in rule_ids(third)
    other = call(ledger, size=900, task="T-2")
    assert "builtin:anomaly-task-bytes" not in rule_ids(other)


def test_alert_mode_records_without_stopping(ledger):
    from helpers import variant

    soft = variant(POLICY, anomalies={**ANOM.model_dump(mode="json"), "on_anomaly": "alert"})
    for _ in range(3):
        run_check(
            action(
                tool_id="web.post", paths=[], domains=["a.example"], diff_lines=None, data_bytes=10
            ),
            soft,
            REGISTRY,
            None,
            ledger=ledger,
        )
    verdict = run_check(
        action(tool_id="web.post", paths=[], domains=["z.example"], diff_lines=None, data_bytes=10),
        soft,
        REGISTRY,
        None,
        ledger=ledger,
    )
    assert "builtin:anomaly-new-domain" not in rule_ids(verdict)  # alert adds no rule
    assert any(r["event"]["kind"] == "anomaly_detected" for r in ledger.records())


# ---------- across a rotation ----------


def verdict_key(v):
    return (v.decision, v.reason, sorted(m.rule_id for m in v.matches))


@pytest.mark.parametrize("seed", range(4))
def test_hosts_and_volumes_are_the_same_on_a_ledger_that_rotates(tmp_path, seed):
    plain = Ledger(tmp_path / "plain" / "events.jsonl")
    rotating = Ledger(tmp_path / "rot" / "events.jsonl", rotate_bytes=3500)
    rng = random.Random(seed)
    hosts = [f"h{i}.example" for i in range(12)]
    seen_rules = set()
    for step in range(80):
        act = action(
            task_id=rng.choice(["T-1", "T-2"]),
            tool_id=rng.choice(["web.post", "web.post", "fs.write"]),
            paths=[],
            diff_lines=None,
            domains=rng.sample(hosts, rng.choice([0, 1, 2])),
            data_bytes=rng.choice([10, 200, 900, 3000]),
            role=rng.choice(["builder", "builder", "analyst"]),
        )
        a = run_check(act, POLICY, REGISTRY, None, ledger=plain)
        b = run_check(act, POLICY, REGISTRY, None, ledger=rotating)
        assert verdict_key(a) == verdict_key(b), f"seed {seed} step {step}"
        seen_rules |= {r for r in rule_ids(a) if r.startswith("builtin:anomaly")}
    assert len(rotating.segments()) >= 2
    assert "builtin:anomaly-new-domain" in seen_rules and "builtin:anomaly-task-bytes" in seen_rules
    assert rotating.verify().ok


def test_the_summary_carries_hosts_and_task_bytes(ledger):
    call(ledger, ["a.example"], size=400)
    call(ledger, ["b.example"], size=100, task="T-2")
    folded = LedgerSummary(keep=None).fold(ledger.records())
    assert folded.domains_seen == {"a.example", "b.example"} and folded.domain_history == 2
    assert folded.stats("T-1").out_bytes == 400 and folded.stats("T-2").out_bytes == 100


# ---------- a link written before this existed ----------


def make_rotated(tmp_path):
    ledger = Ledger(tmp_path / "events.jsonl")
    baseline(ledger)
    ledger.rotate()
    return ledger


def test_a_version_1_link_still_verifies_and_the_gate_reads_everything(tmp_path):
    plain = Ledger(tmp_path / "plain.jsonl")
    baseline(plain)
    ledger = make_rotated(tmp_path)

    def old_style(event):
        event["data"]["summary"] = as_version_1(event["data"]["summary"])

    rewrite_active(ledger, old_style)
    assert ledger.verify().ok  # a ledger rotated by the previous release stays valid
    for host in ("a.example", "z.example"):
        a = call(plain, [host])
        b = call(ledger, [host])
        assert verdict_key(a) == verdict_key(b)  # it fell back to the whole ledger, and agrees


def test_an_edited_host_list_in_a_summary_is_caught(tmp_path):
    ledger = make_rotated(tmp_path)
    rewrite_active(ledger, lambda event: event["data"]["summary"].update(domains_seen=[]))
    result = ledger.verify()
    assert not result.ok and "summary" in result.error


def test_an_edited_version_1_summary_is_still_caught(tmp_path):
    ledger = make_rotated(tmp_path)

    def tamper(event):
        old = as_version_1(event["data"]["summary"])
        old["history"] = 0
        event["data"]["summary"] = old

    rewrite_active(ledger, tamper)
    assert not ledger.verify().ok


# ---------- through the Claude Code hook ----------

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "claude-code"


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / ".agents").mkdir(parents=True)
    for name in ("policy.yaml", "tool-registry.yaml"):
        shutil.copy(EXAMPLES / name, root / ".agents" / name)
    document = yaml.safe_load((root / ".agents" / "policy.yaml").read_text(encoding="utf-8"))
    document["anomalies"] = {
        "new_domain": True,
        "new_domain_min_history": 2,
        "max_call_bytes": 2000,
        "on_anomaly": "human",
    }
    (root / ".agents" / "policy.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.setenv("JARVIS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("JARVIS_ON_BEHALF_OF", "alice")
    for name in (
        "JARVIS_HOOK_MODE",
        "JARVIS_POLICY_SOURCE",
        "JARVIS_POLICY_REF",
        "JARVIS_POLICY_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
    return root


def fetch(project, monkeypatch, url):
    payload = {
        "hook_event_name": "PreToolUse",
        "session_id": "s1",
        "cwd": str(project),
        "tool_name": "WebFetch",
        "tool_input": {"url": url, "prompt": "summarise"},
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert main(["hook", "run"]) == 0


def decisions(project):
    ledger = Ledger(project / ".agents" / "ledger" / "events.jsonl")
    return [r["event"] for r in ledger.records() if r["event"]["kind"] == "policy_decision"]


def test_the_hook_records_the_host_and_flags_a_new_one(project, monkeypatch):
    for path in ("a", "b"):
        fetch(project, monkeypatch, f"https://docs.example.com/{path}?token=abc123xyz")
    fetch(project, monkeypatch, "https://exfil.example.net/upload")
    first, _, last = decisions(project)
    assert first["data"]["domains"] == ["docs.example.com"]  # the host only, never the query
    assert "token" not in json.dumps(first["data"]["domains"])
    ids = {m["rule_id"] for m in last["data"]["matches"]}
    assert "builtin:anomaly-new-domain" in ids
    assert last["decision"] == "human"
    assert last["data"]["domains"] == ["exfil.example.net"]


def test_the_hook_flags_a_large_call(project, monkeypatch):
    fetch(project, monkeypatch, "https://docs.example.com/" + "x" * 2500)
    (event,) = decisions(project)
    assert event["data"]["outbound_bytes"] > 2000
    assert "builtin:anomaly-call-bytes" in {m["rule_id"] for m in event["data"]["matches"]}
