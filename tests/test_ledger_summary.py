"""The carried summary: the gate decides the same from a summary plus the active file as from the
whole history (P2.4a, ADR 0014)."""

import json
import random
from datetime import UTC, datetime, timedelta

import pytest

from helpers import action, default_registry, policy, rule, variant
from jarvis.core.approvals import APPROVAL_AGENT, scan_approvals
from jarvis.core.ledger import Ledger
from jarvis.core.ledger.ledger import canonical_json, compute_hash
from jarvis.core.ledger.summary import RECENT_KEEP, LedgerSummary
from jarvis.core.policy import run_check
from jarvis.core.policy.usage import (
    COUNTED,
    Usage,
    fingerprint,
    prior_diff_lines_from,
    usage_from_summary,
)
from jarvis.core.schemas import Decision, Event, EventKind
from jarvis.core.schemas.approval import ApprovalGrant, ApprovalScope
from jarvis.core.schemas.policy import Anomalies, Budgets

REGISTRY = default_registry()
T0 = datetime(2026, 11, 1, 8, 0, tzinfo=UTC)


# ---------- the code this replaced, kept here as the reference ----------


def old_usage(records, act, budgets, now, anomalies=None):
    if budgets is None and anomalies is None:
        return Usage()
    from datetime import timedelta as td

    from jarvis.core.ledger.summary import parse_moment

    now = now.astimezone(UTC)
    today = now.date().isoformat()
    mine = fingerprint(act.tool_id, act.paths)
    task_calls = day_calls = history = 0
    tool_seen = False
    recent = []
    for record in records:
        event = record["event"]
        if event.get("kind") != "policy_decision":
            continue
        decision = event.get("decision")
        counted = decision in COUNTED
        moment = parse_moment(event.get("ts"))
        if counted:
            history += 1
            if moment is not None and moment.date().isoformat() == today:
                day_calls += 1
            if (event.get("tool") or {}).get("id") == act.tool_id:
                tool_seen = True
        if event.get("task_id") != act.task_id:
            continue
        if counted:
            task_calls += 1
        recent.append(((event.get("data") or {}).get("fingerprint"), decision, moment))
    repeats = 0
    if budgets is not None and budgets.loop_window:
        window = budgets.loop_window - 1
        repeats = sum(1 for f, _, _ in recent[-window:] if f == mine) if window > 0 else 0
    denials = burst = 0
    if anomalies is not None:
        if anomalies.denial_window:
            tail = recent[-anomalies.denial_window :]
            denials = sum(1 for _, d, _ in tail if d == "deny")
        if anomalies.burst_seconds:
            since = now - td(seconds=anomalies.burst_seconds)
            burst = sum(1 for _, d, m in recent if d in COUNTED and m is not None and m >= since)
    return Usage(task_calls, day_calls, repeats, denials, history, tool_seen, burst)


def old_prior(records, task_id):
    if task_id is None:
        return 0
    total = 0
    for record in records:
        event = record["event"]
        if event.get("kind") != "policy_decision":
            continue
        if event.get("task_id") != task_id or event.get("decision") not in COUNTED:
            continue
        lines = (event.get("data") or {}).get("diff_lines", 0)
        if isinstance(lines, int) and lines > 0:
            total += lines
    return total


# ---------- random histories ----------

TOOLS = ["fs.write", "git.read", "web.post", "mail.send"]
TASKS = ["T-1", "T-2", "T-3", None, "T-4"]


def make_history(seed: int, n: int, hot_task_share: float = 0.0):
    rng = random.Random(seed)
    events, when = [], T0
    for _ in range(n):
        when += timedelta(seconds=rng.choice([1, 2, 5, 30, 600, 7200, 90000]))
        task = "T-HOT" if rng.random() < hot_task_share else rng.choice(TASKS)
        tool = rng.choice(TOOLS)
        decision = rng.choice(["allow", "allow", "allow", "human", "council", "deny"])
        ev = {
            "kind": "policy_decision",
            "ts": when.isoformat().replace("+00:00", "Z"),
            "task_id": task,
            "decision": decision,
            "tool": {"id": tool, "version": "1"},
            "data": {
                "fingerprint": f"f{rng.randrange(6)}",
                "diff_lines": rng.choice([0, 3, 10, 40, -1, "x"]),
            },
        }
        if decision == "allow" and rng.random() < 0.1:
            ev["data"]["approval_grant_ids"] = [f"g{rng.randrange(5)}"]
        events.append({"event": ev})
        if rng.random() < 0.05:
            events.append({"event": {"kind": "note", "ts": ev["ts"], "data": {}}})
    return events


def snapshot(prefix, at: datetime) -> LedgerSummary:
    """What a rotation after ``prefix`` would store, as the gate then loads it."""
    stored = LedgerSummary().fold(prefix).to_data(at)
    return LedgerSummary.from_data(json.loads(json.dumps(stored)), keep=None)


def ts_of(record):
    return datetime.fromisoformat(record["event"]["ts"].replace("Z", "+00:00"))


@pytest.mark.parametrize("seed", range(12))
def test_a_summary_plus_the_active_records_equals_the_whole_history(seed):
    events = make_history(seed, 260, hot_task_share=0.35)
    rng = random.Random(seed + 1000)
    exact_cases = inexact_cases = 0
    for _ in range(14):
        cut = rng.randrange(1, len(events))
        budgets = Budgets(
            max_calls_per_task=999,
            loop_threshold=3,
            loop_window=rng.choice([3, 5, 20, 55, 80]),
        )
        anomalies = Anomalies(
            denial_window=rng.choice([3, 10, 60]),
            max_denials=2,
            burst_seconds=rng.choice([10, 300, 100000]),
            burst_calls=3,
            new_tool=True,
            new_tool_min_history=5,
        )
        now = ts_of(events[-1]) + timedelta(seconds=rng.choice([0, 100, 5000]))
        base = snapshot(events[:cut], ts_of(events[cut - 1]))
        folded = base.fold(events[cut:])
        full = LedgerSummary(keep=None).fold(events)
        for task in [*TASKS, "T-HOT"]:
            for tool in TOOLS:
                act = action(task_id=task, tool_id=tool, paths=["docs/a.md"])
                # The new fold agrees with the code it replaced, on the whole history.
                assert usage_from_summary(full, act, budgets, now, anomalies) == old_usage(
                    events, act, budgets, now, anomalies
                )
                assert prior_diff_lines_from(full, task) == old_prior(events, task)
        # Re-load the base for each check: folding mutates it.
        for task in [*TASKS, "T-HOT"]:
            probe = snapshot(events[:cut], ts_of(events[cut - 1]))
            if not probe.exact_for(
                task, budgets.loop_window, anomalies.denial_window, anomalies.burst_seconds, now
            ):
                inexact_cases += 1
                continue
            exact_cases += 1
            act = action(task_id=task, tool_id="fs.write", paths=["docs/a.md"])
            got = usage_from_summary(probe.fold(events[cut:]), act, budgets, now, anomalies)
            assert got == usage_from_summary(full, act, budgets, now, anomalies), (task, cut)
            assert prior_diff_lines_from(probe, task) == prior_diff_lines_from(full, task)
        assert folded.history == full.history and folded.tools_seen == full.tools_seen
        assert folded.day_calls == full.day_calls
        assert folded.used_grant_ids == full.used_grant_ids
    assert exact_cases > 30  # the comparison above is not vacuous


def test_a_task_with_more_decisions_than_the_summary_keeps_is_flagged_not_guessed():
    events = [
        {
            "event": {
                "kind": "policy_decision",
                "ts": (T0 + timedelta(seconds=i)).isoformat().replace("+00:00", "Z"),
                "task_id": "T-HOT",
                "decision": "allow",
                "tool": {"id": "fs.write"},
                "data": {"fingerprint": "same"},
            }
        }
        for i in range(RECENT_KEEP + 30)
    ]
    now = T0 + timedelta(hours=1)
    base = snapshot(events, T0 + timedelta(minutes=10))
    assert base.stats("T-HOT").n == RECENT_KEEP + 30
    assert len(base.stats("T-HOT").recent) == RECENT_KEEP
    assert base.exact_for("T-HOT", 10, None, None, now)  # a window that fits what was kept
    assert not base.exact_for("T-HOT", RECENT_KEEP + 20, None, None, now)  # one that does not
    assert not base.exact_for("T-HOT", None, RECENT_KEEP + 5, None, now)
    assert not base.exact_for("T-HOT", None, None, 100000, now)  # a burst window reaching back
    assert base.exact_for(
        "T-OTHER", RECENT_KEEP + 20, None, None, now
    )  # unknown task: nothing missing


def test_a_task_idle_for_days_keeps_its_totals_and_loses_its_recent_list():
    events = make_history(3, 80)
    late = ts_of(events[-1]) + timedelta(days=30)
    data = LedgerSummary().fold(events).to_data(late)
    assert all(t["recent"] == [] for t in data["tasks"].values())
    assert sum(t["calls"] for t in data["tasks"].values()) > 0
    restored = LedgerSummary.from_data(data, keep=None)
    assert not restored.exact_for("T-1", 5, None, None, late)  # needs the list it no longer has


def test_a_malformed_summary_is_not_used():
    assert LedgerSummary.from_data(None) is None
    assert LedgerSummary.from_data({"v": 99}) is None
    good = LedgerSummary().fold(make_history(1, 20)).to_data(T0 + timedelta(days=1))
    assert LedgerSummary.from_data(good) is not None
    for key in ("history", "tasks", "approval_events"):
        broken = {**good, key: "nonsense"}
        assert LedgerSummary.from_data(broken) is None


# ---------- the gate, end to end ----------

SRC_RULE = [rule("src-needs-a-person", paths=["src/**"])]


def pol(**over):
    base = {
        "human_required": SRC_RULE,
        "budgets": Budgets(
            max_calls_per_task=40, max_calls_per_day=400, loop_threshold=3, loop_window=6
        ),
        "anomalies": Anomalies(
            denial_window=8,
            max_denials=4,
            burst_seconds=3600,
            burst_calls=30,
            new_tool=True,
            new_tool_min_history=3,
            on_anomaly="human",
        ),
    }
    return policy(**{**base, **over})


def grant_for(ledger, request_id, task):
    scope = ApprovalScope(
        task_id=task,
        tools=["fs.write"],
        paths=["src/**"],
        max_diff_lines=500,
        expires_at=datetime.now(UTC) + timedelta(hours=4),
    )
    body = ApprovalGrant(request_id=request_id, approver="alice", via="cli", scope=scope)
    ledger.append(
        Event(
            kind=EventKind.APPROVAL_GRANTED,
            agent_id=APPROVAL_AGENT,
            on_behalf_of="alice",
            task_id=task,
            data={"grant": body.model_dump(mode="json")},
        )
    )


def verdict_key(v):
    return (v.decision, v.reason, sorted(m.rule_id for m in v.matches), v.total_diff_lines)


@pytest.mark.parametrize("seed", range(6))
def test_a_rotating_ledger_gives_the_same_verdicts_as_one_that_never_rotates(tmp_path, seed):
    plain = Ledger(tmp_path / "plain" / "events.jsonl")
    rotating = Ledger(tmp_path / "rot" / "events.jsonl", rotate_bytes=4000)
    rng = random.Random(seed)
    policy_ = pol()
    seen = 0
    for step in range(90):
        task = rng.choice(["T-1", "T-2", "T-3"])
        path = rng.choice(["docs/a.md", "docs/b.md", "src/x.py", "src/y.py"])
        tool = rng.choice(["fs.write", "fs.write", "git.read", "web.post"])
        act = action(
            task_id=task,
            tool_id=tool,
            paths=[path],
            diff_lines=rng.choice([1, 5, 30]),
            role=rng.choice(["builder", "builder", "builder", "analyst"]),
        )
        a = run_check(act, policy_, REGISTRY, None, ledger=plain)
        b = run_check(act, policy_, REGISTRY, None, ledger=rotating)
        assert verdict_key(a) == verdict_key(b), f"seed {seed} step {step}"
        if a.approval_request and rng.random() < 0.5:
            for ledger in (plain, rotating):
                grant_for(ledger, a.approval_request, task)
            seen += 1
    assert len(rotating.segments()) >= 3  # it really did rotate many times
    assert not plain.segments()
    assert rotating.verify().ok and plain.verify().ok
    assert seen > 0  # approvals were part of the mix


def test_the_gate_reads_only_the_active_file_after_a_rotation(tmp_path, monkeypatch):
    ledger = Ledger(tmp_path / "events.jsonl", rotate_bytes=3000)
    policy_ = pol()
    for i in range(40):
        run_check(
            action(task_id="T-1", paths=[f"docs/{i}.md"]), policy_, REGISTRY, None, ledger=ledger
        )
    assert len(ledger.segments()) >= 2
    read = []
    original = Ledger.records

    def spy(self):
        read.append("all")
        return original(self)

    monkeypatch.setattr(Ledger, "records", spy)
    run_check(action(task_id="T-1", paths=["docs/new.md"]), policy_, REGISTRY, None, ledger=ledger)
    assert read == []  # the whole ledger was never walked


def test_a_legacy_link_without_a_summary_makes_the_gate_read_everything(tmp_path):
    plain = Ledger(tmp_path / "plain" / "events.jsonl")
    ledger = Ledger(tmp_path / "rot" / "events.jsonl")
    policy_ = pol()
    for i in range(12):
        act = action(task_id="T-1", paths=[f"docs/{i}.md"], diff_lines=20)
        run_check(act, policy_, REGISTRY, None, ledger=plain)
        run_check(act, policy_, REGISTRY, None, ledger=ledger)
    ledger.rotate()
    strip_summary(ledger)
    act = action(task_id="T-1", paths=["docs/z.md"], diff_lines=20)
    a = run_check(act, policy_, REGISTRY, None, ledger=plain)
    b = run_check(act, policy_, REGISTRY, None, ledger=ledger)
    assert verdict_key(a) == verdict_key(b) and a.total_diff_lines == 12 * 20 + 20


def rewrite_active(ledger: Ledger, change) -> None:
    """Rewrite the active file with ``change(event)`` applied to its first record and the chain
    recomputed from there, so only the summary check can notice."""
    records = list(ledger.active_records())
    prev = records[0]["prev_hash"]
    lines = []
    for number, record in enumerate(records):
        event = record["event"]
        if number == 0:
            change(event)
        rebuilt = {
            "seq": record["seq"],
            "prev_hash": prev,
            "event": event,
            "hash": compute_hash(record["seq"], prev, event),
        }
        prev = rebuilt["hash"]
        lines.append(canonical_json(rebuilt))
    ledger.path.write_bytes(b"\n".join(lines) + b"\n")


def strip_summary(ledger: Ledger) -> None:
    rewrite_active(ledger, lambda event: event["data"].pop("summary"))


# ---------- verification of the summary ----------


def filled(tmp_path, n=14):
    ledger = Ledger(tmp_path / "events.jsonl")
    for i in range(n):
        run_check(
            action(task_id="T-1", paths=[f"docs/{i}.md"]), pol(), REGISTRY, None, ledger=ledger
        )
    return ledger


def test_verify_accepts_the_summary_it_wrote(tmp_path):
    ledger = filled(tmp_path)
    ledger.rotate()
    ledger.append(Event(kind=EventKind.NOTE, agent_id="a", on_behalf_of="h"))
    ledger.rotate()
    assert ledger.verify().ok


def test_a_summary_edited_with_the_chain_rebuilt_is_still_caught(tmp_path):
    ledger = filled(tmp_path)
    ledger.rotate()
    rewrite_active(ledger, lambda event: event["data"]["summary"].update(history=0))
    result = ledger.verify()
    assert not result.ok and "summary" in result.error


def test_a_summary_that_hides_a_task_total_is_caught(tmp_path):
    ledger = filled(tmp_path)
    ledger.rotate()

    def hide(event):
        event["data"]["summary"]["tasks"]["T-1"]["diff_lines"] = 0
        event["data"]["summary"]["tasks"]["T-1"]["calls"] = 0

    rewrite_active(ledger, hide)
    assert not ledger.verify().ok


def test_a_link_without_a_summary_is_still_a_valid_chain(tmp_path):
    ledger = filled(tmp_path)
    ledger.rotate()
    strip_summary(ledger)
    assert ledger.verify().ok  # written before summaries existed


# ---------- robustness ----------


def test_a_failed_automatic_rotation_never_stops_a_write(tmp_path, monkeypatch):
    ledger = Ledger(tmp_path / "events.jsonl")
    for i in range(3):
        ledger.append(Event(kind=EventKind.NOTE, agent_id="a", on_behalf_of="h", data={"n": i}))

    ledger.rotate_bytes = 1000  # on only now, so the setup writes did not rotate

    def refuse(*args, **kwargs):
        raise PermissionError("the file is open in another process")

    monkeypatch.setattr("jarvis.core.ledger.ledger.os.replace", refuse)
    ledger.append(Event(kind=EventKind.NOTE, agent_id="a", on_behalf_of="h", data={"n": 3}))
    assert ledger.segments() == [] and ledger.verify().ok
    with pytest.raises(OSError):
        ledger.rotate()  # asked for by hand, so the failure is reported
    monkeypatch.undo()
    ledger.append(Event(kind=EventKind.NOTE, agent_id="a", on_behalf_of="h", data={"n": 4}))
    assert len(ledger.segments()) == 1 and ledger.verify().ok


def test_records_containing_skips_lines_without_parsing_them(tmp_path):
    ledger = Ledger(tmp_path / "events.jsonl")
    for i in range(6):
        kind = EventKind.MODEL_CALL if i % 2 else EventKind.NOTE
        ledger.append(Event(kind=kind, agent_id="a", on_behalf_of="h", data={"n": i}))
        if i == 3:
            ledger.rotate()
    found = [r["event"]["data"]["n"] for r in ledger.records_containing(b'"kind":"model_call"')]
    assert found == [1, 3, 5]  # across the archive and the active file


def test_a_session_that_spans_a_rotation_is_not_recorded_twice(tmp_path):
    from jarvis.adapters.claude_code.capture import capture_usage  # noqa: F401

    ledger = Ledger(tmp_path / "events.jsonl")
    for i in range(3):
        ledger.append(
            Event(
                kind=EventKind.MODEL_CALL,
                agent_id="a",
                on_behalf_of="h",
                data={
                    "source": "claude-code-transcript",
                    "session_id": "s1",
                    "message_id": f"m{i}",
                },
            )
        )
    ledger.rotate()
    seen = {
        r["event"]["data"]["message_id"]
        for r in ledger.records_containing(b'"kind":"model_call"')
        if r["event"]["data"].get("session_id") == "s1"
    }
    assert seen == {"m0", "m1", "m2"}  # the archived ones are still found


def test_the_summary_is_unchanged_by_redaction_of_what_it_carries(tmp_path):
    """The link record is written unredacted and verify recomputes it, so a carried approval
    event must come back exactly as stored."""
    ledger = Ledger(tmp_path / "events.jsonl")
    run_check(action(task_id="T-1", paths=["src/x.py"]), pol(), REGISTRY, None, ledger=ledger)
    request = [r for r in ledger.records() if r["event"]["kind"] == "approval_requested"]
    assert request
    grant_for(ledger, request[0]["event"]["data"]["request"]["request_id"], "T-1")
    ledger.rotate()
    carried = list(ledger.active_records())[0]["event"]["data"]["summary"]["approval_events"]
    stored = [r["event"] for r in ledger.records() if r["event"]["kind"].startswith("approval_")]
    assert carried == stored and ledger.verify().ok
    state = scan_approvals(
        (),
        datetime.now(UTC),
        pol().approvals,
        base=LedgerSummary.from_data(
            list(ledger.active_records())[0]["event"]["data"]["summary"], keep=None
        ),
    )
    assert len(state.grants) == 1


def test_a_policy_without_budgets_or_anomalies_still_gets_the_diff_total(tmp_path):
    ledger = Ledger(tmp_path / "events.jsonl", rotate_bytes=3000)
    plain = Ledger(tmp_path / "plain.jsonl")
    bare = variant(policy(), human_required=SRC_RULE)
    for i in range(25):
        act = action(task_id="T-1", paths=[f"docs/{i}.md"], diff_lines=9)
        a = run_check(act, bare, REGISTRY, None, ledger=plain)
        b = run_check(act, bare, REGISTRY, None, ledger=ledger)
        assert a.total_diff_lines == b.total_diff_lines
    assert len(ledger.segments()) >= 1
    assert Decision.ALLOW  # the two ledgers agreed at every step, including the totals


def test_a_single_use_approval_stays_used_after_a_rotation(tmp_path):
    plain = Ledger(tmp_path / "plain" / "events.jsonl")
    rotating = Ledger(tmp_path / "rot" / "events.jsonl")
    policy_ = pol(budgets=None, anomalies=None)

    def ask(ledger):
        return run_check(
            action(task_id="T-1", paths=["src/x.py"], diff_lines=5),
            policy_,
            REGISTRY,
            None,
            ledger=ledger,
        )

    requests = {}
    for name, ledger in (("plain", plain), ("rot", rotating)):
        requests[name] = ask(ledger).approval_request
        scope = ApprovalScope(
            task_id="T-1",
            tools=["fs.write"],
            paths=["src/**"],
            max_diff_lines=100,
            single_use=True,
            fingerprint=fingerprint("fs.write", ["src/x.py"]),
            expires_at=datetime.now(UTC) + timedelta(hours=4),
        )
        body = ApprovalGrant(request_id=requests[name], approver="alice", via="cli", scope=scope)
        ledger.append(
            Event(
                kind=EventKind.APPROVAL_GRANTED,
                agent_id=APPROVAL_AGENT,
                on_behalf_of="alice",
                task_id="T-1",
                data={"grant": body.model_dump(mode="json")},
            )
        )
    used = [ask(plain), ask(rotating)]
    assert [v.decision for v in used] == [Decision.ALLOW, Decision.ALLOW]  # the grant covered it
    rotating.rotate()
    again = [ask(plain), ask(rotating)]
    assert again[0].decision is Decision.HUMAN  # a single-use grant is spent
    assert verdict_key(again[0]) == verdict_key(again[1])
