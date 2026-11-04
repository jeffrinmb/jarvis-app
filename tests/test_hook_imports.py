"""The hook runs before every tool call, so what it imports is its latency (task P2.6e).

Measured on the maintainer's machine, one hook call costs about 1.2 s and almost all of it is
importing (the Python start about 0.19 s, pydantic about 0.6 s, YAML, then our own modules). These
tests do not time anything, because timings are noisy. They guard what was taken out: the modules
below are expensive to import and a hook call does not need them. If one of these fails, something
the hook runs has started importing a command, the exporter, the sender or a subprocess runner at
module level; import it inside the function that needs it instead.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "claude-code"

NOT_FOR_A_HOOK_CALL = [
    "subprocess",
    "urllib.request",
    "http.client",
    "ssl",
    "jarvis.commands",
    "jarvis.approvals_cli",
    "jarvis.core.observability.otel",
    "jarvis.core.observability.otlp_http",
]

SCRIPT = """
import io, json, sys
from jarvis.cli import main

for payload in json.loads(sys.argv[1]):
    sys.stdin = io.StringIO(json.dumps(payload))
    code = main(["hook", "run", "--mode", "log-only"])
    assert code == 0, code
print(json.dumps([m for m in json.loads(sys.argv[2]) if m in sys.modules]))
"""


@pytest.fixture
def hook_project(tmp_path):
    root = tmp_path / "proj"
    (root / ".agents").mkdir(parents=True)
    for name in ("policy.yaml", "tool-registry.yaml"):
        shutil.copy(EXAMPLES / name, root / ".agents" / name)
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "id": "msg_1",
                    "model": "claude-sonnet-5-5",
                    "usage": {"input_tokens": 1, "output_tokens": 2},
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return root, transcript


def run_hook_in_a_fresh_interpreter(root, transcript, tmp_path, payloads, watch):
    env = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "CLAUDE_PROJECT_DIR": str(root),
        "JARVIS_STATE_DIR": str(tmp_path / "state"),
        "JARVIS_ON_BEHALF_OF": "alice",
    }
    done = subprocess.run(
        [sys.executable, "-c", SCRIPT, json.dumps(payloads), json.dumps(watch)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(root),
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_a_hook_call_does_not_import_what_it_does_not_need(hook_project, tmp_path):
    root, transcript = hook_project
    base = {"session_id": "s1", "cwd": str(root)}
    edit = {
        "tool_name": "Edit",
        "tool_use_id": "tu_1",
        "tool_input": {
            "file_path": str(root / "docs" / "a.md"),
            "old_string": "a",
            "new_string": "b",
        },
    }
    payloads = [
        {**base, "hook_event_name": "SessionStart", "source": "startup"},
        {**base, **edit, "hook_event_name": "PreToolUse"},
        {**base, **edit, "hook_event_name": "PostToolUse"},
        {**base, "hook_event_name": "Stop", "transcript_path": str(transcript)},
    ]
    loaded = run_hook_in_a_fresh_interpreter(
        root, transcript, tmp_path, payloads, NOT_FOR_A_HOOK_CALL
    )
    assert loaded == [], f"the hook path imported modules it should not need: {loaded}"


def test_other_commands_still_load_the_full_command_set(tmp_path):
    """The split must not lose a command: anything but `hook` goes through jarvis.commands."""
    code = (
        "import sys; from jarvis.cli import main;"
        "rc = main(['status', '--state-dir', sys.argv[1]]);"
        "print(rc, 'jarvis.commands' in sys.modules)"
    )
    done = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)], capture_output=True, text=True, timeout=60
    )
    assert done.stdout.split()[-2:] == ["0", "True"], done.stderr


def test_the_exporter_names_are_still_importable_from_the_package():
    """The package loads them on first use, so the old import paths keep working."""
    from jarvis.core.observability import (
        OtlpSendError,
        export_records,
        normalize_endpoint,
        send_export,
        trace_id_for,
        write_export,
    )

    assert callable(export_records) and callable(send_export) and callable(write_export)
    assert callable(normalize_endpoint) and callable(trace_id_for)
    assert issubclass(OtlpSendError, Exception)
    import jarvis.core.observability as observability

    assert not hasattr(observability, "nope")  # an unknown name is still an error


def test_the_hook_subcommand_help_still_works(capsys):
    from jarvis.cli import main

    with pytest.raises(SystemExit) as stop:
        main(["hook", "--help"])
    assert stop.value.code == 0
    assert "run" in capsys.readouterr().out
