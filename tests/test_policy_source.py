"""Trusted policy source (P2.4b, ADR 0004): the agent cannot change what is enforced."""

import io
import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import jarvis.hook_cli as hook_cli
from jarvis.adapters.claude_code import HookConfig, HookContext, HookMode, handle
from jarvis.adapters.claude_code.source import (
    DirectorySource,
    GitSource,
    SourceError,
    WorktreeSource,
    is_full_sha,
    make_source,
)
from jarvis.cli import main
from jarvis.core.ledger import Ledger

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "claude-code"
GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}


def git(root, *args):
    env = {**os.environ, **GIT_ENV}
    done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, env=env, check=True)
    return done.stdout.decode().strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A real git repository whose committed policy is the example one."""
    root = tmp_path / "proj"
    (root / ".agents").mkdir(parents=True)
    for name in ("policy.yaml", "tool-registry.yaml"):
        shutil.copy(EXAMPLES / name, root / ".agents" / name)
    git(root, "init", "-q", "-b", "main")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "policy")
    for name in ("JARVIS_POLICY_SOURCE", "JARVIS_POLICY_REF", "JARVIS_POLICY_DIR"):
        monkeypatch.delenv(name, raising=False)
    return SimpleNamespace(
        root=root,
        state=tmp_path / "state",
        ledger=root / ".agents" / "ledger" / "events.jsonl",
        sha=git(root, "rev-parse", "HEAD"),
    )


def edit_ci(repo):
    return {
        "hook_event_name": "PreToolUse",
        "session_id": "s1",
        "cwd": str(repo.root),
        "tool_name": "Edit",
        "tool_input": {
            "file_path": str(repo.root / ".github/workflows/ci.yml"),
            "old_string": "a",
            "new_string": "b",
        },
    }


def ctx(repo, mode, source):
    return HookContext(HookConfig(on_behalf_of="alice"), mode, repo.root, repo.state, source)


# ---------- the sources ----------


def test_worktree_reads_the_files_and_reports_a_hash(repo):
    found = WorktreeSource(repo.root).read(".agents/policy.yaml")
    assert found is not None and len(found.sha256) == 64
    assert WorktreeSource(repo.root).protected is False


def test_a_missing_file_is_none_not_an_empty_policy(repo):
    assert WorktreeSource(repo.root).read(".agents/nope.yaml") is None
    assert GitSource(repo.root, "main").read(".agents/nope.yaml") is None


def test_git_source_ignores_edits_in_the_working_tree(repo):
    source = GitSource(repo.root, repo.sha)
    committed = source.read(".agents/policy.yaml")
    (repo.root / ".agents" / "policy.yaml").write_text("default: allow\n", encoding="utf-8")
    assert source.read(".agents/policy.yaml") == committed
    assert WorktreeSource(repo.root).read(".agents/policy.yaml") != committed


def test_git_source_resolves_the_ref_once_per_instance(repo):
    source = GitSource(repo.root, "main")
    first = source.commit()
    (repo.root / "x.txt").write_text("x", encoding="utf-8")
    git(repo.root, "add", ".")
    git(repo.root, "commit", "-q", "-m", "next")
    assert source.commit() == first == repo.sha
    assert GitSource(repo.root, "main").commit() != first
    assert source.describe() == f"git:main@{first[:12]}"


@pytest.mark.parametrize("ref", ["", "--upload-pack=x", "a..b", "a b", "-x", "a\nb"])
def test_git_source_rejects_refs_that_could_be_options_or_ranges(repo, ref):
    with pytest.raises(SourceError):
        GitSource(repo.root, ref)


def test_an_unknown_ref_is_an_error(repo):
    with pytest.raises(SourceError, match="not found"):
        GitSource(repo.root, "no-such-branch").commit()


def test_git_environment_variables_cannot_redirect_the_lookup(repo, tmp_path, monkeypatch):
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init", "-q")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    assert GitSource(repo.root, "main").commit() == repo.sha


@pytest.mark.parametrize("relative", ["../x", "/etc/passwd", "C:/x", "a/../../x", "", ".", "..\\x"])
def test_paths_cannot_leave_the_source(repo, tmp_path, relative):
    for source in (GitSource(repo.root, "main"), DirectorySource(tmp_path)):
        with pytest.raises(SourceError):
            source.read(relative)


def test_directory_source_does_not_follow_a_link_out(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("x", encoding="utf-8")
    inside = tmp_path / "inside"
    inside.mkdir()
    try:
        (inside / "link").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")
    with pytest.raises(SourceError, match="outside"):
        DirectorySource(inside).read("link/secret.txt")


def test_non_utf8_content_is_an_error(tmp_path):
    (tmp_path / "bad.yaml").write_bytes(b"\xff\xfe\x00")
    with pytest.raises(SourceError, match="UTF-8"):
        DirectorySource(tmp_path).read("bad.yaml")


def test_make_source_validates_its_inputs(tmp_path):
    assert make_source(None, tmp_path).kind == "worktree"
    assert make_source("git", tmp_path).kind == "git"
    assert isinstance(make_source("directory", tmp_path, directory=str(tmp_path)), DirectorySource)
    with pytest.raises(SourceError, match="--policy-dir"):
        make_source("directory", tmp_path)
    with pytest.raises(SourceError, match="unknown"):
        make_source("ftp", tmp_path)


def test_is_full_sha():
    assert is_full_sha("a" * 40)
    assert not is_full_sha("a" * 39) and not is_full_sha("A" * 40) and not is_full_sha("main")


# ---------- the hook ----------


def test_enforce_refuses_the_agent_writable_working_tree(repo):
    result = handle(edit_ci(repo), ctx(repo, HookMode.ENFORCE, WorktreeSource(repo.root)))
    assert result.exit_code == 2
    assert "agent-writable" in (result.stderr or "")


def test_enforce_with_no_source_given_also_refuses(repo):
    assert handle(edit_ci(repo), ctx(repo, HookMode.ENFORCE, None)).exit_code == 2


def test_log_only_may_use_the_working_tree_and_records_the_source(repo):
    result = handle(edit_ci(repo), ctx(repo, HookMode.LOG_ONLY, None))
    assert result.exit_code == 0
    (record,) = Ledger(repo.ledger).records()
    context = record["event"]["data"]["context"]
    assert context["policy_source"] == "worktree"
    assert context["policy_source_protected"] is False
    assert len(context["policy_sha256"]) == 64


def test_tampering_with_the_working_tree_policy_does_not_loosen_enforcement(repo):
    """The attack in threat-model gap G2: the agent rewrites the policy it is judged by."""
    before = handle(edit_ci(repo), ctx(repo, HookMode.ENFORCE, GitSource(repo.root, repo.sha)))
    assert json.loads(before.stdout)["hookSpecificOutput"]["permissionDecision"] == "ask"

    for name in ("policy.yaml", "tool-registry.yaml"):
        (repo.root / ".agents" / name).write_text("tools: []\n", encoding="utf-8")
    after = handle(edit_ci(repo), ctx(repo, HookMode.ENFORCE, GitSource(repo.root, repo.sha)))
    assert after.stdout == before.stdout and after.exit_code == 0

    # reading the tampered working tree is not even a valid policy, which is why it is refused
    tampered = handle(edit_ci(repo), ctx(repo, HookMode.LOG_ONLY, WorktreeSource(repo.root)))
    assert tampered.exit_code == 0 and tampered.stderr


def test_each_decision_records_the_commit_and_hashes_it_was_judged_by(repo):
    handle(edit_ci(repo), ctx(repo, HookMode.ENFORCE, GitSource(repo.root, repo.sha)))
    (record,) = Ledger(repo.ledger).records()
    context = record["event"]["data"]["context"]
    assert context["policy_source"] == f"git:{repo.sha}@{repo.sha[:12]}"
    assert context["policy_source_protected"] is True
    committed = GitSource(repo.root, repo.sha).read(".agents/policy.yaml")
    assert context["policy_sha256"] == committed.sha256


def test_a_missing_policy_in_the_source_blocks_in_enforce(repo):
    git(repo.root, "rm", "-q", ".agents/policy.yaml")
    git(repo.root, "commit", "-q", "-m", "remove")
    result = handle(edit_ci(repo), ctx(repo, HookMode.ENFORCE, GitSource(repo.root, "main")))
    assert result.exit_code == 2 and "not found" in (result.stderr or "")


def test_an_unknown_ref_blocks_in_enforce_and_never_blocks_in_log_only(repo):
    for mode, code in ((HookMode.ENFORCE, 2), (HookMode.LOG_ONLY, 0)):
        result = handle(edit_ci(repo), ctx(repo, mode, GitSource(repo.root, "gone")))
        assert result.exit_code == code


# ---------- the command line ----------


def send(monkeypatch, repo, *args):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo.root))
    monkeypatch.setenv("JARVIS_STATE_DIR", str(repo.state))
    monkeypatch.setenv("JARVIS_ON_BEHALF_OF", "alice")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(edit_ci(repo))))
    return main(["hook", "run", *args])


def test_cli_enforce_with_the_default_source_is_blocked(repo, monkeypatch, capsys):
    assert send(monkeypatch, repo, "--mode", "enforce") == 2
    assert "agent-writable" in capsys.readouterr().err


def test_cli_enforce_with_a_git_source_decides_from_the_commit(repo, monkeypatch, capsys):
    args = ("--mode", "enforce", "--policy-source", "git", "--policy-ref", repo.sha)
    assert send(monkeypatch, repo, *args) == 0
    assert "permissionDecision" in capsys.readouterr().out


def test_the_source_can_come_from_the_environment(repo, monkeypatch, capsys):
    monkeypatch.setenv("JARVIS_POLICY_SOURCE", "git")
    monkeypatch.setenv("JARVIS_POLICY_REF", repo.sha)
    assert send(monkeypatch, repo, "--mode", "enforce") == 0
    assert "permissionDecision" in capsys.readouterr().out


def test_adapter_config_is_read_from_the_protected_source_not_the_working_tree(
    repo, monkeypatch, capsys
):
    """An edited adapter config must not be able to remap tools or lower the mode."""
    args = ("--mode", "enforce", "--policy-source", "git", "--policy-ref", repo.sha)
    (repo.root / ".agents" / "claude-code.yaml").write_text(
        "tool_map:\n  Edit: fs.read\n", encoding="utf-8"
    )
    assert send(monkeypatch, repo, *args) == 0
    assert "permissionDecision" in capsys.readouterr().out  # still judged as a write


def test_an_invalid_policy_source_blocks_in_enforce(repo, monkeypatch):
    assert send(monkeypatch, repo, "--mode", "enforce", "--policy-source", "directory") == 2


def test_enforce_snippet_needs_a_protected_source(capsys):
    assert main(["hook", "snippet", "--mode", "enforce"]) == 2
    assert "ADR 0004" in capsys.readouterr().err
    assert main(["hook", "snippet", "--mode", "enforce", "--policy-source", "git"]) == 0
    out = json.loads(capsys.readouterr().out)
    args = out["hooks"]["PreToolUse"][0]["hooks"][0]["args"]
    assert args[-2:] == ["--policy-source", "git"]


def test_doctor_reports_the_source_and_refuses_enforce_on_the_working_tree(
    repo, monkeypatch, capsys
):
    monkeypatch.setattr(hook_cli, "_claude_version_text", lambda: "2.1.300")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo.root))
    assert main(["hook", "doctor", "--mode", "enforce"]) == 1
    assert "ADR 0004" in capsys.readouterr().out
    git_args = ["--policy-source", "git", "--policy-ref", repo.sha]
    assert main(["hook", "doctor", "--mode", "enforce", *git_args]) == 0
    assert "git:" in capsys.readouterr().out
