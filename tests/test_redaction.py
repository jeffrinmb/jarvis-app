"""Redaction and secret scanning (P2.12, threat model T-5).

Every "secret" here is assembled at run time from pieces. A whole token in the source would be
a real finding for the repository's secret scanner, and none of these values is a credential.
"""

import json
import shutil
from pathlib import Path

import pytest

from helpers import action, default_registry, policy, rule, variant
from jarvis.cli import main
from jarvis.core.ledger import Ledger
from jarvis.core.policy import evaluate
from jarvis.core.schemas import Decision, Event, EventKind
from jarvis.core.security.redact import (
    MAX_SCAN_CHARS,
    NOT_FULLY_SCANNED,
    find_secrets,
    find_secrets_in,
    redact_text,
    redact_value,
)

GITHUB = "gh" + "p_" + "aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bD1fH3jL5"
AWS = "AK" + "IA" + "ABCDEFGHIJKLMNOP"
JWT = "ey" + "J" + "hbGciOiJIUzI1NiJ9" + "." + "eyJzdWIiOiIxMjM0NTY3" + "." + "c2lnbmF0dXJlMTIz"
PEM = "-----BEGIN " + "RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END " + "RSA PRIVATE KEY-----"
SLACK = "xo" + "xb-" + "1234567890-abcdefghij"
ANTHROPIC = "sk-" + "ant-" + "api03-abcdefghijklmnopqrstuvwx"
CARD = "4111 1111 1111 1111"  # a well-known test number that passes the Luhn check

SECRETS = {
    "github-token": f"token {GITHUB}",
    "aws-access-key": f"key {AWS}",
    "jwt": f"jwt {JWT}",
    "private-key": f"before\n{PEM}\nafter",
    "slack-token": f"slack {SLACK}",
    "anthropic-key": f"key {ANTHROPIC}",
    "bearer-token": "Authorization: Bearer " + "abcdefghijklmnopqrstuvwxyz012345",
    "url-credentials": "git clone https://deploy:" + "s3cr3tpw" + "@example.com/x.git",
    "credential-assignment": "export API_" + "KEY=" + "abc123def456",
}


@pytest.mark.parametrize(("kind", "text"), SECRETS.items())
def test_each_known_secret_format_is_found_and_redacted(kind, text):
    assert find_secrets(text) == [kind]
    redacted = redact_text(text)
    assert f"[REDACTED:{kind}]" in redacted
    for piece in (GITHUB, AWS, JWT, SLACK, ANTHROPIC, "s3cr3tpw", "abc123def456"):
        assert piece not in redacted


def test_a_private_key_block_is_removed_whole_and_text_around_it_is_kept():
    redacted = redact_text(SECRETS["private-key"])
    assert redacted == "before\n[REDACTED:private-key]\nafter"
    assert "MIIEow" not in redacted


def test_a_private_key_block_with_no_end_marker_is_removed_to_the_end():
    text = "x\n-----BEGIN " + "OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXk"
    assert redact_text(text) == "x\n[REDACTED:private-key]"


def test_findings_are_kinds_only_sorted_and_without_duplicates():
    text = f"{GITHUB} and {GITHUB} and {AWS}"
    assert find_secrets(text) == ["aws-access-key", "github-token"]


def test_everyday_text_is_left_alone():
    text = (
        "token = request.value\n"
        "sha256 " + "a" * 64 + " seq 1626\n"
        "ts 1730000000000 and 1234567890123456\n"
        "password=\n"
        "def get_token(user): return user.token"
    )
    assert find_secrets(text) == []
    assert redact_text(text) == text


def test_personal_data_is_redacted_but_is_not_a_secret_finding():
    text = f"mail jeffrin@example.com card {CARD}"
    assert find_secrets(text) == []
    redacted = redact_text(text)
    assert redacted == "mail [REDACTED:email] card [REDACTED:card-number]"


def test_a_digit_run_that_fails_the_luhn_check_is_not_a_card():
    assert redact_text("order 4111 1111 1111 1112") == "order 4111 1111 1111 1112"


def test_values_are_redacted_through_nested_dicts_and_lists_and_numbers_are_kept():
    value = {"a": [{"b": f"x {GITHUB}"}, 7, None, True], "c": {"d": f"{AWS}"}}
    out = redact_value(value)
    assert out == {
        "a": [{"b": "x [REDACTED:github-token]"}, 7, None, True],
        "c": {"d": "[REDACTED:aws-access-key]"},
    }
    assert value["c"]["d"] == AWS  # the caller's value is not changed


def test_signatures_and_digests_are_never_touched():
    armoured = "-----BEGIN SSH SIGNATURE-----\nU1NIU0lH" + AWS + "\n-----END SSH SIGNATURE-----"
    value = {"signature": armoured, "policy_sha256": AWS, "hash": AWS, "note": AWS}
    out = redact_value(value)
    assert out["signature"] == armoured and out["policy_sha256"] == AWS and out["hash"] == AWS
    assert out["note"] == "[REDACTED:aws-access-key]"


def test_input_beyond_the_scan_cap_is_reported_not_waved_through():
    long = "x" * (MAX_SCAN_CHARS + 1)
    assert find_secrets(long) == [NOT_FULLY_SCANNED]
    assert find_secrets_in({"a": "x" * (MAX_SCAN_CHARS - 5), "b": "y" * 100}) == [NOT_FULLY_SCANNED]


def test_a_secret_hidden_after_the_cap_cannot_make_the_input_look_clean():
    hidden = "x" * MAX_SCAN_CHARS + GITHUB
    assert NOT_FULLY_SCANNED in find_secrets(hidden)


def test_find_secrets_in_walks_nested_input():
    assert find_secrets_in({"a": [{"b": GITHUB}], "c": {"d": AWS}}) == [
        "aws-access-key",
        "github-token",
    ]
    assert find_secrets_in({"a": 1, "b": None, "c": ["fine"]}) == []


# ---------- the ledger ----------


def raw(ledger: Ledger) -> str:
    return ledger.path.read_text(encoding="utf-8")


def test_a_secret_in_an_events_data_never_reaches_the_file(tmp_path):
    ledger = Ledger(tmp_path / "events.jsonl")
    ledger.append(
        Event(
            kind=EventKind.NOTE,
            agent_id="a",
            on_behalf_of="h",
            data={"error": f"boom {GITHUB}", "nested": {"who": "jeffrin@example.com"}},
        )
    )
    text = raw(ledger)
    assert GITHUB not in text and "jeffrin@example.com" not in text
    assert "[REDACTED:github-token]" in text and "[REDACTED:email]" in text
    assert ledger.verify().ok  # the hash covers what was stored


def test_a_transaction_append_is_redacted_too(tmp_path):
    ledger = Ledger(tmp_path / "events.jsonl")
    with ledger.transaction() as tx:
        tx.append(Event(kind=EventKind.NOTE, agent_id="a", on_behalf_of="h", data={"x": AWS}))
    assert AWS not in raw(ledger) and ledger.verify().ok


def test_the_log_command_cannot_write_a_secret_either(tmp_path, capsys):
    path = tmp_path / "events.jsonl"
    code = main(
        ["log", "--ledger", str(path), "--kind", "note", "--agent", "a", "--on-behalf-of", "h"]
        + ["--data", json.dumps({"detail": GITHUB})]
    )
    assert code == 0
    assert GITHUB not in path.read_text(encoding="utf-8")


def test_fields_outside_data_are_not_changed(tmp_path):
    ledger = Ledger(tmp_path / "events.jsonl")
    ledger.append(
        Event(kind=EventKind.NOTE, agent_id="a", on_behalf_of="alice@example.com", data={})
    )
    assert "alice@example.com" in raw(ledger)  # identity fields must stay exact


# ---------- the gate ----------


def run(act, **over):
    return evaluate(act, variant(policy(), **over), default_registry())


def test_a_secret_in_the_input_of_a_writing_tool_needs_a_human():
    verdict = run(action(secrets_in_input=["github-token"]))
    assert verdict.decision is Decision.HUMAN
    assert "builtin:secret-in-input" in {m.rule_id for m in verdict.matches}
    assert "github-token" in verdict.reason


def test_an_allow_rule_cannot_lift_it():
    allowing = [rule("docs", tools=["fs.write"], paths=["docs/**"])]
    verdict = run(action(secrets_in_input=["jwt"]), auto_approve=allowing)
    assert verdict.decision is Decision.HUMAN


def test_without_a_secret_nothing_changes():
    assert run(action()).decision is Decision.ALLOW


def test_a_read_only_tool_is_not_stopped_by_it():
    verdict = run(action(tool_id="git.read", secrets_in_input=["github-token"]))
    assert "builtin:secret-in-input" not in {m.rule_id for m in verdict.matches}


def test_alert_mode_records_but_does_not_stop():
    verdict = run(action(secrets_in_input=["github-token"]), secrets_in_input="alert")
    assert verdict.decision is Decision.ALLOW


def test_the_setting_cannot_be_set_to_allow():
    with pytest.raises(ValueError):
        variant(policy(), secrets_in_input="allow")


# ---------- the Claude Code hook ----------

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
    for name in (
        "JARVIS_HOOK_MODE",
        "JARVIS_POLICY_SOURCE",
        "JARVIS_POLICY_REF",
        "JARVIS_POLICY_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
    return root


def send_edit(project, monkeypatch, new_string):
    import io

    payload = {
        "hook_event_name": "PreToolUse",
        "session_id": "s1",
        "cwd": str(project),
        "tool_name": "Edit",
        "tool_input": {
            "file_path": str(project / "docs" / "a.md"),
            "old_string": "a",
            "new_string": new_string,
        },
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    return main(["hook", "run"])


def decisions(project):
    ledger = Ledger(project / ".agents" / "ledger" / "events.jsonl")
    return [r["event"] for r in ledger.records() if r["event"]["kind"] == "policy_decision"]


def test_the_hook_asks_a_human_and_records_the_kind_but_never_the_secret(project, monkeypatch):
    send_edit(project, monkeypatch, f"api = '{GITHUB}'")
    (event,) = decisions(project)
    assert event["decision"] == "human"
    assert event["data"]["secrets_in_input"] == ["github-token"]
    assert "builtin:secret-in-input" in {m["rule_id"] for m in event["data"]["matches"]}
    assert GITHUB not in (project / ".agents" / "ledger" / "events.jsonl").read_text(
        encoding="utf-8"
    )


def test_an_ordinary_edit_is_not_flagged(project, monkeypatch):
    send_edit(project, monkeypatch, "just some documentation text")
    (event,) = decisions(project)
    assert event["decision"] == "allow" and event["data"]["secrets_in_input"] == []
