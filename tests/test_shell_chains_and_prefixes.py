"""Safe shell chains, command prefixes for evidence, and the decisions recorded in the policy
(task P2.10a, ADR 0015)."""

import json
import random
import shutil
from pathlib import Path

import pytest

from jarvis.adapters.claude_code import HookConfig
from jarvis.adapters.claude_code.mapping import (
    MAX_CHAIN,
    build_action,
    classify_command,
    classify_shell,
    command_prefixes_for,
    split_and_chain,
)
from jarvis.adapters.claude_code.payload import HookInput
from jarvis.adapters.claude_code.report import render_text, summarize
from jarvis.cli import main
from jarvis.core.schemas import Policy, load_yaml_model
from jarvis.core.schemas.action import SessionState

CFG = HookConfig(on_behalf_of="alice")
ROOT = Path(__file__).resolve().parent.parent


def pre(command, tool="Bash"):
    return HookInput.model_validate(
        {
            "hook_event_name": "PreToolUse",
            "session_id": "s1",
            "tool_name": tool,
            "tool_input": {"command": command},
        }
    )


# ---------- chains of commands that are each already safe ----------


@pytest.mark.parametrize(
    ("command", "kind"),
    [
        ("git status && git diff", "read"),
        ("git log -n 3 && git show HEAD && ls", "read"),
        ("uv run ruff check . && uv run ruff format --check . && uv run pytest -q", "test"),
        ("git status && uv run pytest", "test"),
        ("set -o pipefail && uv run pytest -q", "test"),
        ("uv run pytest && uv run pytest", "test"),
    ],
)
def test_a_chain_of_safe_commands_is_as_safe_as_its_parts(command, kind):
    assert classify_command(command, CFG) == kind


@pytest.mark.parametrize(
    "command",
    [
        "git status && rm -rf .",
        "rm -rf . && git status",
        "git status && git push",
        "git status ; git diff",
        "git status || git diff",
        "git status && uv run pytest | cat",
        "git status && git diff > out.txt",
        "git status && git diff $(whoami)",
        "git status && git diff `whoami`",
        "git status &&& git diff",
        "git status && && git diff",
        "git status &&",
        "&& git status",
        "git status &\ngit diff",
        "git status\n&& git diff",
        "git status && 'git diff",
        "git log --grep 'a && git status'",
        "git log --grep 'a' && git status && curl http://evil.example",
        "set -o pipefail; uv run pytest",
        "set -o pipefail && rm -rf .",
        "set -o pipefail",
        "cd / && git status",
    ],
)
def test_a_chain_with_anything_unsafe_is_a_general_shell_command(command):
    assert classify_command(command, CFG) == "run"


def test_a_long_chain_is_not_safe_however_harmless_it_looks():
    ok = " && ".join(["git status"] * MAX_CHAIN)
    too_long = " && ".join(["git status"] * (MAX_CHAIN + 1))
    assert classify_command(ok, CFG) == "read"
    assert classify_command(too_long, CFG) == "run"


def test_a_single_command_is_classified_exactly_as_before():
    for command in ("git status", "uv run pytest -q", "rm -rf .", "git push", "git status; ls"):
        assert classify_command(command, CFG) == classify_shell(command, CFG)


def test_splitting_ignores_an_and_inside_quotes_and_refuses_an_unbalanced_quote():
    assert split_and_chain("a && b") == ["a ", " b"]
    assert split_and_chain('echo "x && y" && z') == ['echo "x && y" ', " z"]
    assert split_and_chain("echo 'x && y'") == ["echo 'x && y'"]
    assert split_and_chain('echo "unclosed && y') is None


def test_no_chain_with_anything_unsafe_in_it_is_ever_read_or_test():
    """Random chains of safe and unsafe parts: the result is safe only if every part is."""
    safe = ["git status", "git diff", "ls", "pwd", "uv run pytest -q", "uv run ruff check ."]
    unsafe = [
        "rm -rf .",
        "git push",
        "curl http://x.example",
        "git status; ls",
        "git diff > f",
        "uv run pytest | cat",
        "echo $(id)",
        "git -c core.pager=x log",
        "python -c 'print(1)'",
    ]
    rng = random.Random(1)
    for _ in range(400):
        count = rng.randint(1, MAX_CHAIN + 2)
        parts = [
            rng.choice(unsafe) if rng.random() < 0.25 else rng.choice(safe) for _ in range(count)
        ]
        verdict = classify_command(" && ".join(parts), CFG)
        should_be_safe = count <= MAX_CHAIN and all(p in safe for p in parts)
        assert (verdict != "run") == should_be_safe, parts


def test_the_action_for_a_chain_uses_the_safe_tool_and_a_leading_cd_still_works(tmp_path):
    root = str(tmp_path)
    here = tmp_path.as_posix()

    def tool(command):
        return build_action(pre(command), CFG, root, SessionState()).action.tool_id

    assert tool(f"cd {here} && uv run ruff check . && uv run pytest -q") == "shell.test"
    assert tool("uv run pytest -q 2>&1 | tail -5") == "shell.test"
    assert tool("git status && uv run pytest 2>&1 | tail -20") == "shell.test"
    assert tool("git status && git push") == "shell.run"
    assert tool("cd / && git status") == "shell.run"


# ---------- command prefixes, for choosing what may be allowed from evidence ----------


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("git add docs/x.md", ["git add"]),
        ("git commit -m 'a message'", ["git commit"]),
        ("git -C somewhere status", ["git"]),
        ("uv run python - <<EOF\nprint(1)\nEOF", ["uv run", "(other)", "EOF"]),
        ("/c/Users/someone/proj/.venv/Scripts/python.exe -I s.py", ["python.exe"]),
        ("cd /d/Proj && uv run pytest -q", ["cd", "uv run"]),
        ("a | b ; c || d && e", ["a", "b", "c", "d", "e"]),
        ("rm -rf build", ["rm"]),
        ("", []),
    ],
)
def test_a_prefix_is_one_or_two_plain_words_and_never_an_argument(command, expected):
    assert command_prefixes_for(command) == expected


@pytest.mark.parametrize(
    "command",
    [
        "TOKEN=abc123 curl https://example.com",
        "mysql -pSECRETPASSWORD",
        "curl https://user:hunter2pw@example.com/x",
        "cat /etc/passwd",
        "ssh deploy@host.example",
        "echo password=hunter2",
    ],
)
def test_a_secret_in_the_arguments_never_reaches_the_prefix(command):
    joined = " ".join(command_prefixes_for(command))
    for secret in ("abc123", "SECRETPASSWORD", "hunter2", "/etc", "deploy@"):
        assert secret not in joined


def test_prefixes_are_capped():
    assert len(command_prefixes_for(" ; ".join(f"cmd{i} sub" for i in range(40)))) == 10


def test_nothing_is_recorded_unless_the_config_asks_for_it(tmp_path):
    root = str(tmp_path)
    off = build_action(pre("git add x"), CFG, root, SessionState()).action
    assert off.command_prefixes == []
    on_cfg = HookConfig(on_behalf_of="alice", record_command_prefixes=True)
    on = build_action(pre("git add x"), on_cfg, root, SessionState()).action
    assert on.command_prefixes == ["git add"]
    edit = pre("ignored", tool="Edit")
    edit = HookInput.model_validate(
        {
            "hook_event_name": "PreToolUse",
            "session_id": "s",
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(tmp_path / "docs" / "a.md"),
                "old_string": "a",
                "new_string": "b",
            },
        }
    )
    assert build_action(edit, on_cfg, root, SessionState()).action.command_prefixes == []


# ---------- through the hook, the ledger and the report ----------

EXAMPLES = ROOT / "examples" / "claude-code"


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / ".agents").mkdir(parents=True)
    for name in ("policy.yaml", "tool-registry.yaml"):
        shutil.copy(EXAMPLES / name, root / ".agents" / name)
    (root / ".agents" / "claude-code.yaml").write_text(
        "schema_version: 1\nrecord_command_prefixes: true\n", encoding="utf-8"
    )
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


def run_hook(project, monkeypatch, command):
    import io

    payload = {
        "hook_event_name": "PreToolUse",
        "session_id": "s1",
        "cwd": str(project),
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert main(["hook", "run"]) == 0


def test_the_hook_records_prefixes_and_the_report_counts_them_without_the_arguments(
    project, monkeypatch, capsys
):
    secret = "hunter2" + "pw99"
    run_hook(project, monkeypatch, "git add docs/a.md")
    run_hook(project, monkeypatch, "git add src/b.py")
    run_hook(project, monkeypatch, f"git commit -m 'fix {secret}'")
    run_hook(project, monkeypatch, "uv run pytest -q && git status")
    ledger_text = (project / ".agents" / "ledger" / "events.jsonl").read_text(encoding="utf-8")
    assert secret not in ledger_text and "docs/a.md" not in ledger_text
    from jarvis.core.ledger import Ledger

    summary = summarize(Ledger(project / ".agents" / "ledger" / "events.jsonl").records())
    assert summary["shell_prefixes"] == {
        "git add": 2,
        "git commit": 1,
        "uv run": 1,
        "git status": 1,
    }
    text = render_text(summary)
    assert "shell command prefixes" in text and "git add" in text
    assert main(["hook", "report"]) == 0
    assert "git add" in capsys.readouterr().out


# ---------- the decisions made about this repository's policy ----------


def test_this_repositorys_policy_keeps_the_taint_rule_and_the_two_thousand_line_cap():
    """P2.10a: the taint rule is kept as it is (it costs about 1% of decisions) and the edit cap
    is 2,000 lines per session. A quiet change to either should fail here and be reviewed."""
    policy = load_yaml_model(ROOT / ".agents" / "policy.yaml", Policy)
    taint = next(r for r in policy.human_required if r.id == "writes-after-untrusted-content")
    assert taint.when.tainted is True and taint.when.risk_at_least is not None
    edits = next(r for r in policy.auto_approve if r.id == "edits-in-project")
    assert edits.when.max_diff_lines == 2000


def test_this_repositorys_hook_config_records_prefixes_for_the_evidence_period():
    from jarvis.adapters.claude_code.config import parse_config

    config = parse_config((ROOT / ".agents" / "claude-code.yaml").read_text(encoding="utf-8"))
    assert config.record_command_prefixes is True
