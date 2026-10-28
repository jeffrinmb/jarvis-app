"""Keep the agent-facing context honest: AGENTS.md, CLAUDE.md and docs/HANDOFF.md must point at
things that exist, stay short, and carry no personal data or local paths."""

import re
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ROOT / "AGENTS.md"
CLAUDE = ROOT / "CLAUDE.md"
HANDOFF = ROOT / "docs" / "HANDOFF.md"
TODO = ROOT / "docs" / "TODO.md"

CONTEXT_FILES = [AGENTS, CLAUDE, HANDOFF]

# Paths that are mentioned on purpose but do not exist in a fresh checkout: they are created
# on a developer's machine (hook wiring and the audit ledger) and are git-ignored. A test that
# only passed because these exist locally would fail in CI.
LOCAL_ONLY = {
    ".claude/",
    ".claude/settings.local.json",
    ".agents/ledger/",
    ".agents/ledger/events.jsonl",
}
ROOT_FILES = {
    "README.md",
    "AGENTS.md",
    "CLAUDE.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "LICENSE",
    "NOTICE",
    "pyproject.toml",
    "uv.lock",
}
PATH_PREFIXES = ("docs/", "src/", "tests/", "examples/", ".github/", ".agents/", ".claude/")


def _todo_ids():
    return set(re.findall(r"\*\*(P\d+\.\d+[a-z]?)\*\*", TODO.read_text(encoding="utf-8")))


def _backticked(path):
    return re.findall(r"`([^`\n]+)`", path.read_text(encoding="utf-8"))


def _is_repo_path(token):
    if any(c in token for c in "*<>{} ") or token in LOCAL_ONLY:
        return False
    return token in ROOT_FILES or token.startswith(PATH_PREFIXES)


@pytest.mark.parametrize("path", CONTEXT_FILES, ids=lambda p: p.name)
def test_files_named_in_the_context_docs_exist(path):
    missing = [
        token
        for token in _backticked(path)
        if _is_repo_path(token) and not (ROOT / token.rstrip("/")).exists()
    ]
    assert not missing, f"{path.name} names files that do not exist: {missing}"


@pytest.mark.parametrize("path", CONTEXT_FILES, ids=lambda p: p.name)
def test_task_ids_in_the_context_docs_exist_in_the_todo_list(path):
    mentioned = set(re.findall(r"\bP\d+\.\d+[a-z]?\b", path.read_text(encoding="utf-8")))
    unknown = sorted(mentioned - _todo_ids())
    assert not unknown, f"{path.name} mentions tasks that are not in docs/TODO.md: {unknown}"


def test_agents_md_is_short_because_every_session_pays_to_read_it():
    lines = AGENTS.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 120, f"AGENTS.md has {len(lines)} lines; keep it short and stable"


@pytest.mark.parametrize("path", CONTEXT_FILES, ids=lambda p: p.name)
def test_no_personal_data_or_local_paths(path):
    text = path.read_text(encoding="utf-8")
    assert not re.search(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}", text), "email address found"
    assert not re.search(r"[A-Za-z]:\\(Users|Documents)", text), "Windows user path found"
    assert not re.search(r"/(Users|home)/\w+", text), "home directory path found"


def test_claude_md_defers_to_agents_md_instead_of_duplicating_it():
    text = CLAUDE.read_text(encoding="utf-8")
    assert "@AGENTS.md" in text and "AGENTS.md" in text
    assert len(text.splitlines()) <= 20


def test_handoff_has_a_valid_last_updated_date():
    match = re.search(r"^Last updated: (\d{4}-\d{2}-\d{2})$", HANDOFF.read_text("utf-8"), re.M)
    assert match, "docs/HANDOFF.md needs a 'Last updated: YYYY-MM-DD' line"
    date.fromisoformat(match.group(1))


def test_agents_md_states_the_rules_a_new_agent_must_not_miss():
    text = AGENTS.read_text(encoding="utf-8")
    for phrase in (
        "Do not push",
        "pull request",
        "Fail closed",
        "enforce mode",
        "pipefail",
        "untrusted",
    ):
        assert phrase in text, f"AGENTS.md should mention {phrase!r}"


def test_handoff_lists_what_waits_for_the_owner():
    text = HANDOFF.read_text(encoding="utf-8")
    for task in ("P0.1", "P0.4", "P0.13", "P2.10a", "P2.10"):
        assert f"`{task}`" in text, f"HANDOFF.md should list the pending decision {task}"
