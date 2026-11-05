"""Committed checkpoints only grow (P2.9f, ADR 0010)."""

import shutil
import subprocess
from pathlib import Path

import pytest

from jarvis.cli import main
from jarvis.core import signing
from jarvis.core.ledger import Ledger, check_history, create_checkpoint, ledger_id
from jarvis.core.schemas import Event, EventKind
from jarvis.core.signing import NS_CHECKPOINT

pytestmark = pytest.mark.skipif(
    shutil.which("ssh-keygen") is None or shutil.which("git") is None,
    reason="needs the OpenSSH client and git",
)


def make_key(directory: Path, name: str) -> Path:
    path = directory / name
    subprocess.run(
        [signing.ssh_keygen(), "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(path)],
        check=True,
        capture_output=True,
    )
    return path


def allowed_line(principal: str, key: Path) -> str:
    kind, blob = key.with_suffix(".pub").read_text(encoding="utf-8").split()[:2]
    return f'{principal} namespaces="{NS_CHECKPOINT}" {kind} {blob}\n'


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
    ledger = Ledger(tmp_path / "ledger" / "events.jsonl")
    for n in range(4):
        ledger.append(note(n))
    return ledger


class Signer:
    """Signs checkpoints of one ledger into a named file, like ``checkpoint --milestone``."""

    def __init__(self, ledger, tmp_path, key, allowed):
        self.ledger, self.key, self.allowed = ledger, key, allowed
        self.state = tmp_path / "state"
        self.name = f"{ledger_id(ledger)[:16]}.jsonl"

    def sign(self, file: Path) -> str:
        create_checkpoint(
            self.ledger, self.key, self.allowed, state_dir=self.state, milestone_file=file
        )
        return file.read_text(encoding="utf-8").splitlines()[-1]


@pytest.fixture
def signer(ledger, keys, allowed, tmp_path):
    return Signer(ledger, tmp_path, keys["alice"], allowed)


def two_checkpoints(signer, tmp_path):
    file = tmp_path / "scratch" / signer.name
    first = signer.sign(file)
    signer.ledger.append(note(9))
    second = signer.sign(file)
    return first, second


# ---------- the rule itself ----------


def test_an_unchanged_directory_passes(signer, allowed, tmp_path):
    first, _ = two_checkpoints(signer, tmp_path)
    same = {signer.name: first + "\n"}
    assert check_history(same, dict(same), allowed) == []
    assert check_history({}, {}, allowed) == []


def test_appending_a_signed_checkpoint_passes(signer, allowed, tmp_path):
    first, second = two_checkpoints(signer, tmp_path)
    assert (
        check_history(
            {signer.name: first + "\n"}, {signer.name: first + "\n" + second + "\n"}, allowed
        )
        == []
    )


def test_a_first_checkpoint_in_a_new_file_passes(signer, allowed, tmp_path):
    first, _ = two_checkpoints(signer, tmp_path)
    assert check_history({}, {signer.name: first + "\n"}, allowed) == []


def test_removing_a_file_fails(signer, allowed, tmp_path):
    first, _ = two_checkpoints(signer, tmp_path)
    failures = check_history({signer.name: first + "\n"}, {}, allowed)
    assert failures == [f"{signer.name}: the checkpoint file was removed"]


def test_removing_the_newest_line_fails(signer, allowed, tmp_path):
    first, second = two_checkpoints(signer, tmp_path)
    failures = check_history(
        {signer.name: first + "\n" + second + "\n"}, {signer.name: first + "\n"}, allowed
    )
    assert "changed, reordered or removed" in failures[0]


def test_editing_a_line_fails(signer, allowed, tmp_path):
    first, _ = two_checkpoints(signer, tmp_path)
    edited = first.replace('"seq": ', '"seq": 1', 1)
    assert edited != first
    failures = check_history({signer.name: first + "\n"}, {signer.name: edited + "\n"}, allowed)
    assert "changed, reordered or removed" in failures[0]


def test_reordering_lines_fails(signer, allowed, tmp_path):
    first, second = two_checkpoints(signer, tmp_path)
    failures = check_history(
        {signer.name: first + "\n" + second + "\n"},
        {signer.name: second + "\n" + first + "\n"},
        allowed,
    )
    assert "changed, reordered or removed" in failures[0]


def test_a_new_line_signed_by_an_unlisted_key_fails(ledger, keys, allowed, tmp_path):
    forger = Signer(ledger, tmp_path, keys["mallory"], allowed_line("mallory", keys["mallory"]))
    # Mallory signs; the base commit lists only alice.
    file = tmp_path / "scratch" / forger.name
    forged = forger.sign(file)
    failures = check_history({}, {forger.name: forged + "\n"}, allowed)
    assert "does not verify for a key listed at the base commit" in failures[0]


def test_a_pull_request_cannot_bless_its_own_key(ledger, keys, tmp_path):
    """The allowed-signers text that counts is the base commit's, so adding a key and signing
    with it in the same change does not pass."""
    mallory_only = allowed_line("mallory", keys["mallory"])
    forger = Signer(ledger, tmp_path, keys["mallory"], mallory_only)
    forged = forger.sign(tmp_path / "scratch" / forger.name)
    base_allowed = allowed_line("alice", keys["alice"])  # what the base commit lists
    assert check_history({}, {forger.name: forged + "\n"}, base_allowed)
    assert check_history({}, {forger.name: forged + "\n"}, mallory_only) == []  # the head's file


def test_a_new_line_cannot_be_checked_without_an_allowed_signers_file(signer, tmp_path):
    first, _ = two_checkpoints(signer, tmp_path)
    failures = check_history({}, {signer.name: first + "\n"}, None)
    assert "no allowed-signers file" in failures[0]


def test_unreadable_and_misnamed_lines_and_stray_files_fail(signer, allowed, tmp_path):
    first, _ = two_checkpoints(signer, tmp_path)
    assert "not a readable checkpoint" in check_history({}, {signer.name: "garbage\n"}, allowed)[0]
    other = "0123456789abcdef.jsonl"
    assert "another ledger" in check_history({}, {other: first + "\n"}, allowed)[0]
    assert "not a checkpoint file" in check_history({}, {"notes.txt": "x\n"}, allowed)[0]
    assert "not a checkpoint file" in check_history({}, {signer.name + ".lock": ""}, allowed)[0]


# ---------- the command, against a real git repository ----------


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


@pytest.fixture
def repo(signer, allowed, tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / ".agents" / "checkpoints").mkdir(parents=True)
    (root / ".agents" / "allowed-signers").write_text(allowed, encoding="utf-8")
    file = root / ".agents" / "checkpoints" / signer.name
    signer.sign(file)
    git(root.parent, "init", "-q", str(root))
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "base")
    monkeypatch.chdir(root)
    return root, file, git(root, "rev-parse", "HEAD")


def run(base: str) -> int:
    return main(["checkpoint-history", "--base", base])


def test_the_command_passes_when_a_checkpoint_is_appended(repo, signer, capsys):
    _, file, base = repo
    signer.ledger.append(note(7))
    signer.sign(file)
    assert run(base) == 0
    assert "OK (1 file(s), 1 new line(s)" in capsys.readouterr().out


def test_the_command_passes_when_nothing_changed(repo, capsys):
    _, _, base = repo
    assert run(base) == 0
    assert "0 new line(s)" in capsys.readouterr().out


def test_the_command_fails_when_the_file_is_deleted(repo, capsys):
    _, file, base = repo
    file.unlink()
    assert run(base) == 1
    assert "was removed" in capsys.readouterr().err


def test_the_command_fails_when_a_line_is_edited(repo, capsys):
    _, file, base = repo
    file.write_text(
        file.read_text(encoding="utf-8").replace('"seq": ', '"seq": 1', 1), encoding="utf-8"
    )
    assert run(base) == 1
    assert "changed, reordered or removed" in capsys.readouterr().err


def test_the_command_fails_closed_on_a_base_it_cannot_read(repo, capsys):
    assert run("not-a-commit") == 2
    assert "cannot compare" in capsys.readouterr().err


def test_an_empty_or_all_zero_base_means_nothing_to_compare(repo, capsys):
    assert run("0" * 40) == 0
    assert run("") == 0
    assert "nothing to check" in capsys.readouterr().out


def test_the_check_uses_the_base_commits_allowed_signers_not_the_working_tree(
    repo, signer, keys, capsys
):
    root, file, base = repo
    # In the working tree only, list mallory and append a checkpoint signed by mallory.
    (root / ".agents" / "allowed-signers").write_text(
        allowed_line("mallory", keys["mallory"]), encoding="utf-8"
    )
    signer.ledger.append(note(8))
    mallory = Signer(
        signer.ledger,
        signer.state.parent,
        keys["mallory"],
        allowed_line("mallory", keys["mallory"]),
    )
    mallory.sign(file)
    assert run(base) == 1
    assert "does not verify for a key listed at the base commit" in capsys.readouterr().err
