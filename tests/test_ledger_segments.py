"""Rotating the ledger into archived segments (P2.1b, ADR 0014)."""

import json
import os
import shutil
import subprocess

import pytest

from jarvis.cli import main
from jarvis.core import signing
from jarvis.core.ledger import (
    Ledger,
    LedgerCorruptError,
    check_checkpoints,
    create_checkpoint,
    ledger_id,
)
from jarvis.core.schemas import Event, EventKind
from jarvis.core.signing import NS_CHECKPOINT


def note(n: int) -> Event:
    return Event(kind=EventKind.NOTE, agent_id="a", on_behalf_of="h", data={"n": n})


def filled(tmp_path, count=5, **kw) -> Ledger:
    ledger = Ledger(tmp_path / "events.jsonl", **kw)
    for n in range(count):
        ledger.append(note(n))
    return ledger


def numbers(records):
    return [r["event"]["data"].get("n") for r in records if r["event"]["kind"] == "note"]


# ---------- rotating ----------


def test_rotation_moves_the_records_and_continues_the_same_chain(tmp_path):
    ledger = filled(tmp_path, 5)
    segment = ledger.rotate()
    assert (segment.first_seq, segment.last_seq, segment.records) == (0, 4, 5)
    assert segment.path.name == "0000000000-0000000004.jsonl"
    assert segment.path.parent == tmp_path / "events.archive"

    active = list(ledger.active_records())
    assert len(active) == 1  # only the link record
    link = active[0]
    assert link["seq"] == 5
    assert link["event"]["kind"] == "ledger_rotated"
    assert link["prev_hash"] == list(ledger.records())[4]["hash"]
    assert link["event"]["data"]["previous_segment"] == segment.path.name

    ledger.append(note(5))
    assert numbers(ledger.records()) == [0, 1, 2, 3, 4, 5]  # archive first, then active
    assert numbers(ledger.active_records()) == [5]
    result = ledger.verify()
    assert result.ok and result.records == 7 and result.head_seq == 6


def test_rotating_an_empty_or_missing_ledger_does_nothing(tmp_path):
    ledger = Ledger(tmp_path / "events.jsonl")
    assert ledger.rotate() is None
    (tmp_path / "events.jsonl").write_bytes(b"")
    assert ledger.rotate() is None
    assert ledger.segments() == []


def test_rotating_twice_makes_two_segments_in_order(tmp_path):
    ledger = filled(tmp_path, 3)
    ledger.rotate()
    ledger.append(note(3))
    ledger.rotate()
    ledger.append(note(4))
    names = [s.path.name for s in ledger.segments()]
    assert names == ["0000000000-0000000002.jsonl", "0000000003-0000000004.jsonl"]
    assert numbers(ledger.records()) == [0, 1, 2, 3, 4]
    assert ledger.verify().ok
    assert ledger.head() == (ledger.verify().head_seq, ledger.verify().head_hash)


def test_a_write_past_the_size_limit_rotates_first(tmp_path):
    ledger = filled(tmp_path, 12, rotate_bytes=1500)
    assert len(ledger.segments()) >= 2
    assert numbers(ledger.records()) == list(range(12))
    assert ledger.verify().ok
    seqs = [r["seq"] for r in ledger.records()]
    assert seqs == list(range(len(seqs)))  # one unbroken sequence


def test_nothing_is_rotated_without_a_limit(tmp_path):
    ledger = filled(tmp_path, 20)
    assert ledger.segments() == []


def test_rotation_never_replaces_an_existing_segment(tmp_path):
    ledger = filled(tmp_path, 3)
    (tmp_path / "events.archive").mkdir()
    (tmp_path / "events.archive" / "0000000000-0000000002.jsonl").write_bytes(b"x")
    with pytest.raises(LedgerCorruptError, match="already exists"):
        ledger.rotate()
    assert ledger.path.exists()  # the active file was not touched


def test_a_crash_after_the_move_is_healed_by_the_next_write(tmp_path):
    ledger = filled(tmp_path, 3)
    archive = tmp_path / "events.archive"
    archive.mkdir()
    os.replace(ledger.path, archive / "0000000000-0000000002.jsonl")  # the move, then "crash"
    assert not ledger.path.exists()
    assert ledger.verify().ok  # a ledger of archives alone is a valid chain
    ledger.append(note(3))
    kinds = [r["event"]["kind"] for r in ledger.active_records()]
    assert kinds == ["ledger_rotated", "note"]
    assert ledger.verify().ok and numbers(ledger.records()) == [0, 1, 2, 3]


# ---------- verification across segments ----------


def rotated(tmp_path, per_segment=3, segments=3) -> Ledger:
    ledger = Ledger(tmp_path / "events.jsonl")
    n = 0
    for _ in range(segments):
        for _ in range(per_segment):
            ledger.append(note(n))
            n += 1
        ledger.rotate()
    ledger.append(note(n))
    assert ledger.verify().ok
    return ledger


def test_editing_a_record_in_an_archive_is_detected(tmp_path):
    ledger = rotated(tmp_path)
    first = ledger.segments()[0].path
    first.write_bytes(first.read_bytes().replace(b'"n":1', b'"n":9', 1))
    result = ledger.verify()
    assert not result.ok and "modified" in result.error


def test_a_whitespace_edit_that_keeps_every_hash_is_still_detected(tmp_path):
    """The link record pins the archive file's SHA-256, so even bytes the hashes do not cover
    (here: spaces after the colons) cannot change."""
    ledger = rotated(tmp_path)
    first = ledger.segments()[0].path
    first.write_bytes(first.read_bytes().replace(b'"seq":', b'"seq": ', 1))
    result = ledger.verify()
    assert not result.ok and "not the one the ledger recorded" in result.error


def test_a_missing_middle_archive_is_detected(tmp_path):
    ledger = rotated(tmp_path)
    ledger.segments()[1].path.unlink()
    result = ledger.verify()
    assert not result.ok and "does not hold the records it is named for" in result.error


def test_a_missing_oldest_archive_is_detected(tmp_path):
    ledger = rotated(tmp_path)
    ledger.segments()[0].path.unlink()
    assert not ledger.verify().ok


def test_a_missing_newest_archive_is_detected(tmp_path):
    ledger = rotated(tmp_path)
    ledger.segments()[-1].path.unlink()
    result = ledger.verify()
    assert not result.ok


def test_all_archives_missing_is_detected(tmp_path):
    ledger = rotated(tmp_path)
    shutil.rmtree(tmp_path / "events.archive")
    result = ledger.verify()
    assert not result.ok and "expected 0" in result.error


def test_an_archive_named_for_other_records_is_detected(tmp_path):
    ledger = rotated(tmp_path)
    first, second = (s.path for s in ledger.segments()[:2])
    swapped = first.with_name("tmp")
    first.rename(swapped)
    second.rename(first)
    swapped.rename(second)
    result = ledger.verify()
    assert not result.ok and "named for" in result.error


def test_an_unexpected_file_in_the_archive_is_reported(tmp_path):
    ledger = rotated(tmp_path)
    (tmp_path / "events.archive" / "notes.txt").write_text("hi", encoding="utf-8")
    result = ledger.verify()
    assert not result.ok and "notes.txt" in result.error


def test_a_segment_that_does_not_start_with_the_link_is_detected(tmp_path):
    ledger = rotated(tmp_path)
    lines = ledger.path.read_bytes().split(b"\n")
    ledger.path.write_bytes(b"\n".join(lines[1:]))  # drop the link record
    assert not ledger.verify().ok


def test_a_partial_final_line_in_an_archive_is_detected(tmp_path):
    ledger = rotated(tmp_path)
    first = ledger.segments()[0].path
    first.write_bytes(first.read_bytes().rstrip(b"\n"))
    result = ledger.verify()
    assert not result.ok and "partial final line" in result.error


def test_an_anchor_in_an_archive_is_found_and_a_changed_one_is_not(tmp_path):
    ledger = rotated(tmp_path)
    records = list(ledger.records())
    old = records[1]
    assert ledger.verify(anchor=(old["seq"], old["hash"])).ok
    assert "anchor mismatch" in ledger.verify(anchor=(old["seq"], "0" * 64)).error
    assert "anchor not found" in ledger.verify(anchor=(10_000, "0" * 64)).error


# ---------- what else depends on one continuous chain ----------


@pytest.fixture(scope="module")
def key(tmp_path_factory):
    directory = tmp_path_factory.mktemp("segkeys")
    path = directory / "alice"
    subprocess.run(
        [signing.ssh_keygen(), "-q", "-t", "ed25519", "-N", "", "-C", "alice", "-f", str(path)],
        check=True,
        capture_output=True,
    )
    kind, blob = path.with_suffix(".pub").read_text(encoding="utf-8").split()[:2]
    return path, f'alice namespaces="{NS_CHECKPOINT}" {kind} {blob}\n'


def test_a_checkpoint_signed_before_a_rotation_still_verifies_after_it(tmp_path, key):
    path, allowed = key
    ledger = filled(tmp_path, 4)
    identity = ledger_id(ledger)
    file = tmp_path / "cp.jsonl"
    create_checkpoint(ledger, path, allowed, file)
    ledger.rotate()
    ledger.append(note(4))
    assert ledger_id(ledger) == identity  # the id is the very first record, now in the archive
    report = check_checkpoints(ledger, file.read_text(encoding="utf-8"), allowed)
    assert report.ok and report.good == 1
    create_checkpoint(ledger, path, allowed, file)  # a new one after the rotation
    assert check_checkpoints(ledger, file.read_text(encoding="utf-8"), allowed).good == 2


def test_a_truncation_after_a_rotation_is_still_caught_by_a_checkpoint(tmp_path, key):
    path, allowed = key
    ledger = filled(tmp_path, 4)
    file = tmp_path / "cp.jsonl"
    ledger.rotate()
    ledger.append(note(4))
    ledger.append(note(5))
    create_checkpoint(ledger, path, allowed, file)
    lines = ledger.path.read_bytes().split(b"\n")
    ledger.path.write_bytes(b"\n".join(lines[:2]) + b"\n")  # cut the active file short
    report = check_checkpoints(ledger, file.read_text(encoding="utf-8"), allowed)
    assert not report.ok and "truncated" in report.failures[0]


def test_redaction_still_applies_to_the_first_write_of_a_new_segment(tmp_path):
    ledger = filled(tmp_path, 2)
    ledger.rotate()
    secret = "gh" + "p_" + "aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bD1fH3jL5"
    ledger.append(Event(kind=EventKind.NOTE, agent_id="a", on_behalf_of="h", data={"x": secret}))
    assert secret not in ledger.path.read_text(encoding="utf-8")


# ---------- the command line ----------


def test_the_ledger_commands_rotate_and_report(tmp_path, capsys):
    ledger = filled(tmp_path, 4)
    assert main(["ledger", "status", "--ledger", str(ledger.path)]) == 0
    assert "0 archived segment(s)" in capsys.readouterr().out
    assert main(["ledger", "rotate", "--ledger", str(ledger.path)]) == 0
    assert "rotated records 0-3" in capsys.readouterr().out
    assert main(["ledger", "status", "--ledger", str(ledger.path)]) == 0
    out = capsys.readouterr().out
    assert "0000000000-0000000003.jsonl: 4 records" in out and "1 archived segment(s)" in out
    assert main(["verify", "--ledger", str(ledger.path)]) == 0
    assert "OK: 5 records" in capsys.readouterr().out


def test_the_rotate_command_refuses_a_ledger_that_fails_verification(tmp_path, capsys):
    ledger = filled(tmp_path, 4)
    ledger.path.write_bytes(ledger.path.read_bytes().replace(b'"n":1', b'"n":9', 1))
    assert main(["ledger", "rotate", "--ledger", str(ledger.path)]) == 1
    assert "will not rotate" in capsys.readouterr().err
    assert ledger.segments() == []


def test_the_log_command_keeps_working_after_a_rotation(tmp_path, capsys):
    ledger = filled(tmp_path, 3)
    ledger.rotate()
    code = main(
        ["log", "--ledger", str(ledger.path), "--kind", "note", "--agent", "a"]
        + ["--on-behalf-of", "h", "--data", json.dumps({"n": 3})]
    )
    assert code == 0
    assert numbers(ledger.records()) == [0, 1, 2, 3] and ledger.verify().ok
