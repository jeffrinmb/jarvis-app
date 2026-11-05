"""Signed ledger checkpoints (P2.9b, ADR 0010, threat-model gap G4, RT-12)."""

import json
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from jarvis.cli import main
from jarvis.core import signing
from jarvis.core.ledger import (
    Ledger,
    check_checkpoints,
    create_checkpoint,
    default_checkpoint_path,
    ledger_id,
    read_checkpoint_files,
)
from jarvis.core.ledger.checkpoint import canonical_payload
from jarvis.core.schemas import Event, EventKind
from jarvis.core.schemas.checkpoint import Checkpoint
from jarvis.core.signing import NS_APPROVAL, NS_CHECKPOINT

pytestmark = pytest.mark.skipif(
    shutil.which("ssh-keygen") is None, reason="needs the OpenSSH client"
)


def make_key(directory: Path, name: str) -> Path:
    path = directory / name
    subprocess.run(
        [signing.ssh_keygen(), "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(path)],
        check=True,
        capture_output=True,
    )
    return path


def allowed_line(principal: str, key: Path, namespaces: str = NS_CHECKPOINT) -> str:
    kind, blob = key.with_suffix(".pub").read_text(encoding="utf-8").split()[:2]
    return f'{principal} namespaces="{namespaces}" {kind} {blob}\n'


@pytest.fixture(scope="module")
def keys(tmp_path_factory):
    directory = tmp_path_factory.mktemp("keys")
    return {name: make_key(directory, name) for name in ("alice", "mallory")}


@pytest.fixture
def allowed(keys):
    return allowed_line("alice", keys["alice"])


def note(n: int) -> Event:
    return Event(kind=EventKind.NOTE, agent_id="a", on_behalf_of="h", data={"n": n})


@pytest.fixture
def ledger(tmp_path):
    ledger = Ledger(tmp_path / "events.jsonl")
    for n in range(6):
        ledger.append(note(n))
    return ledger


def sign_now(ledger, keys, allowed, tmp_path, **kw):
    file = tmp_path / "checkpoints.jsonl"
    made, signer, path = create_checkpoint(ledger, keys["alice"], allowed, file, **kw)
    assert path == file and signer == "alice"
    return made, file


def report(ledger, file, allowed, **kw):
    return check_checkpoints(ledger, file.read_text(encoding="utf-8"), allowed, **kw)


# ---------- creating ----------


def test_a_checkpoint_records_the_head_and_the_ledger_it_belongs_to(
    ledger, keys, allowed, tmp_path
):
    made, file = sign_now(ledger, keys, allowed, tmp_path)
    seq, head = ledger.head()
    assert (made.seq, made.hash) == (seq, head)
    assert made.ledger_id == ledger_id(ledger)
    line = file.read_text(encoding="utf-8").strip()
    assert Checkpoint.model_validate_json(line) == made


def test_checkpoints_are_appended_not_replaced(ledger, keys, allowed, tmp_path):
    sign_now(ledger, keys, allowed, tmp_path)
    ledger.append(note(99))
    sign_now(ledger, keys, allowed, tmp_path)
    assert len((tmp_path / "checkpoints.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_the_default_place_is_the_state_directory_by_ledger(ledger, keys, allowed, tmp_path):
    _, _, path = create_checkpoint(ledger, keys["alice"], allowed, state_dir=tmp_path / "state")
    assert path == default_checkpoint_path(tmp_path / "state", ledger_id(ledger))
    assert path.is_file()


def test_it_refuses_to_sign_a_ledger_that_fails_verification(ledger, keys, allowed, tmp_path):
    ledger.path.write_bytes(ledger.path.read_bytes().replace(b'"n":2', b'"n":9', 1))
    with pytest.raises(ValueError, match="fails verification"):
        create_checkpoint(ledger, keys["alice"], allowed, tmp_path / "c.jsonl")


def test_it_refuses_an_empty_ledger_and_an_unlisted_key(tmp_path, keys, allowed, ledger):
    with pytest.raises(ValueError, match="empty"):
        create_checkpoint(Ledger(tmp_path / "none.jsonl"), keys["alice"], allowed, tmp_path / "c")
    with pytest.raises(signing.SigningError, match="not listed"):
        create_checkpoint(ledger, keys["mallory"], allowed, tmp_path / "c.jsonl")
    assert not (tmp_path / "c.jsonl").exists()


# ---------- checking ----------


def test_a_good_checkpoint_passes_and_counts_what_came_after(ledger, keys, allowed, tmp_path):
    _, file = sign_now(ledger, keys, allowed, tmp_path)
    for n in range(3):
        ledger.append(note(100 + n))
    result = report(ledger, file, allowed)
    assert result.ok and result.good == 1 and not result.warnings
    assert result.latest_signer == "alice" and result.records_after_latest == 3


def test_truncating_the_ledger_after_a_checkpoint_is_detected(ledger, keys, allowed, tmp_path):
    _, file = sign_now(ledger, keys, allowed, tmp_path)
    lines = ledger.path.read_bytes().split(b"\n")
    ledger.path.write_bytes(b"\n".join(lines[:3]) + b"\n")  # keep three records
    assert ledger.verify().ok  # the chain alone cannot tell
    result = report(ledger, file, allowed)
    assert not result.ok and "truncated" in result.failures[0]


def test_rewriting_history_consistently_is_detected(ledger, keys, allowed, tmp_path):
    """Keep the first record, rebuild the rest with different content: a valid chain of the
    same length. Only the checkpoint knows it is not the original."""
    _, file = sign_now(ledger, keys, allowed, tmp_path)
    first = ledger.path.read_bytes().split(b"\n")[0]
    forged = tmp_path / "forged.jsonl"
    forged.write_bytes(first + b"\n")
    rebuilt = Ledger(forged)
    for n in range(5):
        rebuilt.append(note(1000 + n))
    assert rebuilt.verify().ok and rebuilt.head()[0] == ledger.head()[0]
    result = report(rebuilt, file, allowed)
    assert not result.ok and "rewritten" in result.failures[0]


def test_a_replaced_ledger_does_not_pass_as_the_original(ledger, keys, allowed, tmp_path):
    _, file = sign_now(ledger, keys, allowed, tmp_path)
    other = Ledger(tmp_path / "other.jsonl")
    for n in range(8):
        other.append(note(n))
    result = report(other, file, allowed)
    assert not result.ok and "different ledger" in result.failures[0]


def test_a_checkpoint_signed_by_an_unlisted_key_fails(ledger, keys, allowed, tmp_path):
    file = tmp_path / "c.jsonl"
    only_mallory = allowed_line("alice", keys["mallory"])
    create_checkpoint(ledger, keys["mallory"], only_mallory, file)
    result = report(ledger, file, allowed)  # verified against the real, protected file
    assert not result.ok and "does not verify" in result.failures[0]


def test_editing_a_checkpoint_breaks_its_signature(ledger, keys, allowed, tmp_path):
    made, file = sign_now(ledger, keys, allowed, tmp_path)
    data = json.loads(file.read_text(encoding="utf-8"))
    data["seq"] = made.seq - 1
    file.write_text(json.dumps(data) + "\n", encoding="utf-8")
    result = report(ledger, file, allowed)
    assert not result.ok and "does not verify" in result.failures[0]


def test_every_field_is_covered_by_the_signature(ledger, keys, allowed, tmp_path):
    """Relabelling a checkpoint (here its ledger id) must break the signature, not just the
    comparison that follows."""
    _, file = sign_now(ledger, keys, allowed, tmp_path)
    data = json.loads(file.read_text(encoding="utf-8"))
    data["ledger_id"] = "f" * 64
    file.write_text(json.dumps(data) + "\n", encoding="utf-8")
    result = report(ledger, file, allowed)
    assert not result.ok and "does not verify" in result.failures[0]


def test_a_signature_made_for_something_else_is_not_a_checkpoint(ledger, keys, allowed, tmp_path):
    """An approval signature (another namespace) over the same bytes must not pass."""
    seq, head = ledger.head()
    created = datetime.now(UTC)
    payload = canonical_payload(ledger_id(ledger), seq, head, created)
    both = allowed_line("alice", keys["alice"], f"{NS_APPROVAL},{NS_CHECKPOINT}")
    wrong = signing.sign(payload, keys["alice"], NS_APPROVAL)
    file = tmp_path / "c.jsonl"
    file.write_text(
        Checkpoint(
            ledger_id=ledger_id(ledger), seq=seq, hash=head, created_at=created, signature=wrong
        ).model_dump_json()
        + "\n",
        encoding="utf-8",
    )
    result = report(ledger, file, both)
    assert not result.ok and "does not verify" in result.failures[0]


def test_one_bad_checkpoint_among_good_ones_still_fails(ledger, keys, allowed, tmp_path):
    _, file = sign_now(ledger, keys, allowed, tmp_path)
    with file.open("a", encoding="utf-8") as handle:
        handle.write("{ not a checkpoint\n")
    ledger.append(note(50))
    sign_now(ledger, keys, allowed, tmp_path)
    result = report(ledger, file, allowed)
    assert not result.ok and result.good == 2 and result.checked == 3
    assert "cannot be read" in result.failures[0]


def test_no_checkpoint_is_a_warning_not_a_failure(ledger, allowed):
    result = check_checkpoints(ledger, "", allowed)
    assert result.ok and "no signed checkpoint" in result.warnings[0]


def test_an_old_newest_checkpoint_warns(ledger, keys, allowed, tmp_path):
    _, file = sign_now(ledger, keys, allowed, tmp_path)
    later = datetime.now(UTC) + timedelta(days=30)
    result = report(ledger, file, allowed, now=later)
    assert result.ok and "older than 7 days" in result.warnings[0]
    assert report(ledger, file, allowed, max_age_days=60, now=later).warnings == []


def test_a_ledger_that_fails_its_own_chain_fails_before_checkpoints_are_read(
    ledger, keys, allowed, tmp_path
):
    _, file = sign_now(ledger, keys, allowed, tmp_path)
    ledger.path.write_bytes(ledger.path.read_bytes().replace(b'"n":2', b'"n":9', 1))
    result = report(ledger, file, allowed)
    assert not result.ok and "fails verification" in result.failures[0]


# ---------- the command line ----------


def cli_checkpoint(ledger, key, allowed_file, *extra):
    return main(
        [
            "checkpoint",
            "--ledger",
            str(ledger.path),
            "--key",
            str(key),
            "--allowed-signers",
            str(allowed_file),
            *extra,
        ]
    )


def cli_verify(ledger, allowed_file, *extra):
    return main(
        ["verify", "--ledger", str(ledger.path), "--allowed-signers", str(allowed_file), *extra]
    )


def test_checkpoint_then_verify_from_the_command_line(ledger, keys, allowed, tmp_path, capsys):
    allowed_file = tmp_path / "allowed"
    allowed_file.write_text(allowed, encoding="utf-8")
    file = tmp_path / "cp.jsonl"
    assert cli_checkpoint(ledger, keys["alice"], allowed_file, "--checkpoints", str(file)) == 0
    assert "signed by alice" in capsys.readouterr().out
    assert cli_verify(ledger, allowed_file, "--checkpoints", str(file)) == 0
    assert "checkpoints: 1 of 1 good" in capsys.readouterr().out

    lines = ledger.path.read_bytes().split(b"\n")
    ledger.path.write_bytes(b"\n".join(lines[:3]) + b"\n")
    assert cli_verify(ledger, allowed_file, "--checkpoints", str(file)) == 1
    assert "FAILED" in capsys.readouterr().err


def test_the_default_place_is_used_when_no_path_is_given(ledger, keys, allowed, tmp_path, capsys):
    allowed_file = tmp_path / "allowed"
    allowed_file.write_text(allowed, encoding="utf-8")
    state = ["--state-dir", str(tmp_path / "state")]
    assert cli_checkpoint(ledger, keys["alice"], allowed_file, *state) == 0
    assert cli_verify(ledger, allowed_file, "--checkpoints", *state) == 0
    assert "1 of 1 good" in capsys.readouterr().out


def test_verify_without_a_checkpoint_warns_but_passes(ledger, allowed, tmp_path, capsys):
    allowed_file = tmp_path / "allowed"
    allowed_file.write_text(allowed, encoding="utf-8")
    code = cli_verify(ledger, allowed_file, "--checkpoints", "--state-dir", str(tmp_path / "none"))
    assert code == 0 and "WARNING: no signed checkpoint" in capsys.readouterr().err


def test_verify_without_the_checkpoints_flag_is_unchanged(ledger, capsys):
    assert main(["verify", "--ledger", str(ledger.path)]) == 0
    assert "OK: 6 records" in capsys.readouterr().out


def test_the_command_line_needs_the_allowed_signers_file_and_a_listed_key(
    ledger, keys, allowed, tmp_path, capsys
):
    missing = tmp_path / "missing"
    assert cli_checkpoint(ledger, keys["alice"], missing) == 2
    assert cli_verify(ledger, missing, "--checkpoints") == 2
    allowed_file = tmp_path / "allowed"
    allowed_file.write_text(allowed, encoding="utf-8")
    capsys.readouterr()
    assert cli_checkpoint(ledger, keys["mallory"], allowed_file, "--state-dir", str(tmp_path)) == 2
    assert "not listed" in capsys.readouterr().err


def test_the_doctor_notes_when_a_ledger_has_no_signed_checkpoint(tmp_path, monkeypatch, capsys):
    import jarvis.hook_cli as hook_cli

    examples = Path(__file__).resolve().parent.parent / "examples" / "claude-code"
    root = tmp_path / "proj"
    (root / ".agents").mkdir(parents=True)
    for name in ("policy.yaml", "tool-registry.yaml"):
        shutil.copy(examples / name, root / ".agents" / name)
    ledger = Ledger(root / ".agents" / "ledger" / "events.jsonl")
    ledger.append(note(1))
    monkeypatch.setattr(hook_cli, "_claude_version_text", lambda: "2.1.300")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.setenv("JARVIS_STATE_DIR", str(tmp_path / "state"))
    args = ["hook", "doctor", "--mode", "enforce", "--policy-source", "directory"]
    assert main([*args, "--policy-dir", str(root)]) == 0
    assert "no signed ledger checkpoint" in capsys.readouterr().out


# ---------- milestone checkpoints and the report (P2.9e) ----------


def test_a_milestone_checkpoint_is_also_written_inside_the_project(
    ledger, keys, allowed, tmp_path, monkeypatch, capsys
):
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)
    allowed_file = tmp_path / "allowed"
    allowed_file.write_text(allowed, encoding="utf-8")
    state = ["--state-dir", str(tmp_path / "state")]
    assert cli_checkpoint(ledger, keys["alice"], allowed_file, "--milestone", *state) == 0
    assert "commit it through a pull request" in capsys.readouterr().out
    (committed,) = (project / ".agents" / "checkpoints").glob("*.jsonl")
    assert committed.read_text(encoding="utf-8").count("\n") == 1
    stored = json.loads(committed.read_text(encoding="utf-8"))
    assert set(stored) == {
        "schema_version",
        "ledger_id",
        "seq",
        "hash",
        "created_at",
        "signature",
    }  # ids, a hash and a signature: no event data

    # With the per-user copy gone, the committed one still counts when no path is given.
    shutil.rmtree(tmp_path / "state")
    assert cli_verify(ledger, allowed_file, "--checkpoints", *state) == 0
    assert "1 of 1 good" in capsys.readouterr().out


def test_without_the_milestone_flag_nothing_is_written_in_the_project(
    ledger, keys, allowed, tmp_path, monkeypatch
):
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)
    allowed_file = tmp_path / "allowed"
    allowed_file.write_text(allowed, encoding="utf-8")
    state = ["--state-dir", str(tmp_path / "state")]
    assert cli_checkpoint(ledger, keys["alice"], allowed_file, *state) == 0
    assert not (project / ".agents").exists()


@pytest.fixture
def reported(tmp_path, monkeypatch, allowed):
    """A project whose ledger the hook report reads, with its own state directory."""
    root = tmp_path / "proj"
    (root / ".agents").mkdir(parents=True)
    ledger = Ledger(root / ".agents" / "ledger" / "events.jsonl")
    for n in range(4):
        ledger.append(note(n))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.setenv("JARVIS_STATE_DIR", str(tmp_path / "state"))
    return root, ledger


def test_the_report_says_it_did_not_check_without_an_allowed_signers_file(reported, capsys):
    assert main(["hook", "report"]) == 0
    assert "Checkpoints: not checked (no .agents/allowed-signers file)" in capsys.readouterr().out


def test_the_report_warns_when_there_is_no_checkpoint(reported, allowed, capsys):
    root, _ = reported
    (root / ".agents" / "allowed-signers").write_text(allowed, encoding="utf-8")
    assert main(["hook", "report"]) == 0
    out = capsys.readouterr().out
    assert "Checkpoints: 0 of 0 good" in out and "WARNING: no signed checkpoint" in out


def test_the_report_shows_a_good_checkpoint_and_the_records_after_it(
    reported, keys, allowed, tmp_path, capsys
):
    root, ledger = reported
    (root / ".agents" / "allowed-signers").write_text(allowed, encoding="utf-8")
    create_checkpoint(ledger, keys["alice"], allowed, state_dir=tmp_path / "state")
    ledger.append(note(9))
    assert main(["hook", "report"]) == 0
    out = capsys.readouterr().out
    assert "1 of 1 good" in out and "signed by alice, 1 records after it" in out
    assert "WARNING" not in out


def test_the_report_warns_about_a_stale_checkpoint(reported, keys, allowed, tmp_path, capsys):
    root, ledger = reported
    (root / ".agents" / "allowed-signers").write_text(allowed, encoding="utf-8")
    old = datetime.now(UTC) - timedelta(days=30)
    create_checkpoint(ledger, keys["alice"], allowed, state_dir=tmp_path / "state", now=old)
    assert main(["hook", "report"]) == 0
    assert "older than 7 days" in capsys.readouterr().out


def test_the_report_fails_on_a_checkpoint_the_ledger_contradicts(
    reported, keys, allowed, tmp_path, capsys
):
    root, ledger = reported
    (root / ".agents" / "allowed-signers").write_text(allowed, encoding="utf-8")
    create_checkpoint(ledger, keys["alice"], allowed, state_dir=tmp_path / "state")
    lines = ledger.path.read_bytes().split(b"\n")
    ledger.path.write_bytes(b"\n".join(lines[:2]) + b"\n")  # truncated after the checkpoint
    assert main(["hook", "report"]) == 1
    assert "FAILED" in capsys.readouterr().out


def test_the_json_report_carries_the_checkpoint_status(reported, keys, allowed, tmp_path, capsys):
    root, ledger = reported
    (root / ".agents" / "allowed-signers").write_text(allowed, encoding="utf-8")
    create_checkpoint(ledger, keys["alice"], allowed, state_dir=tmp_path / "state")
    assert main(["hook", "report", "--json"]) == 0
    status = json.loads(capsys.readouterr().out)["checkpoints"]
    assert status["state"] == "checked" and status["good"] == 1 and status["failures"] == []


def test_the_same_checkpoint_in_two_files_counts_once(ledger, keys, allowed, tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    state = tmp_path / "state"
    kept = project / "milestone.jsonl"
    create_checkpoint(ledger, keys["alice"], allowed, state_dir=state, milestone_file=kept)
    user_file = default_checkpoint_path(state, ledger_id(ledger))
    assert user_file.read_text(encoding="utf-8") == kept.read_text(encoding="utf-8")
    text = read_checkpoint_files(user_file, kept, tmp_path / "missing.jsonl")
    assert text.count("\n") == 1
    assert check_checkpoints(ledger, text, allowed).good == 1


def test_a_tilde_in_the_key_path_is_expanded_by_the_program(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    # Generated in place, not copied: ssh-keygen sets the file permissions that Windows OpenSSH
    # insists on, and a copy would inherit looser ones and be refused.
    make_key(home / ".ssh", "approver")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # what Windows uses
    signature = signing.sign(b"data", "~/.ssh/approver", NS_CHECKPOINT)
    assert "BEGIN SSH SIGNATURE" in signature
    with pytest.raises(signing.SigningError, match="not found"):
        signing.sign(b"data", "~/.ssh/nothing", NS_CHECKPOINT)
