"""The hook server and its tiny client (P2.6f, ADR 0012).

The server is a local door into the policy gate and the audit ledger, so these tests are about
what must never happen: a wrong caller being served, a call being decided twice, a missing server
leaving a call undecided, or one project's server answering for another.
"""

import io
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import jarvis.hook_client as client
from jarvis.cli import main
from jarvis.core.ledger import Ledger
from jarvis.hook_cli import execute
from jarvis.hook_paths import (
    build_spec,
    discovery_path,
    secret_path,
    spec_json,
    spec_key,
)
from jarvis.hook_server import serve

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "claude-code"


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / ".agents").mkdir(parents=True)
    for name in ("policy.yaml", "tool-registry.yaml"):
        shutil.copy(EXAMPLES / name, root / ".agents" / name)
    return root


def make_spec(project, tmp_path, mode="log-only", **extra):
    env = {
        "CLAUDE_PROJECT_DIR": str(project),
        "JARVIS_ON_BEHALF_OF": "alice",
        "JARVIS_STATE_DIR": str(tmp_path / "state"),
    }
    return build_spec(
        mode=mode,
        config=None,
        state_dir=None,
        policy_source=extra.get("policy_source"),
        policy_ref=None,
        policy_dir=extra.get("policy_dir"),
        payload_cwd=str(project),
        environ=env,
    )


def ledger_of(project):
    return Ledger(project / ".agents" / "ledger" / "events.jsonl")


def decisions(project):
    path = project / ".agents" / "ledger" / "events.jsonl"
    if not path.exists():
        return []
    return [
        r["event"] for r in ledger_of(project).records() if r["event"]["kind"] == "policy_decision"
    ]


def edit(project, name="docs/a.md", tool_use_id="tu_1", event="PreToolUse"):
    return {
        "hook_event_name": event,
        "session_id": "s1",
        "cwd": str(project),
        "tool_name": "Edit",
        "tool_use_id": tool_use_id,
        "tool_input": {"file_path": str(project / name), "old_string": "a", "new_string": "b"},
    }


@pytest.fixture
def running(project, tmp_path):
    """A real server for the spec, in a thread, with its discovery and secret files."""
    spec = make_spec(project, tmp_path)
    thread = threading.Thread(target=serve, args=(spec, 5.0), daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not client.ping(spec):
        assert time.monotonic() < deadline, "the test server did not start"
        time.sleep(0.05)
    yield spec
    client.stop(spec)
    thread.join(timeout=10)


# ---------- the server decides exactly like the in-process hook ----------


def test_the_server_gives_the_same_answer_as_deciding_in_process(project, tmp_path, running):
    for payload in (
        edit(project),
        edit(project, ".github/workflows/ci.yml", "tu_2"),
        {
            "hook_event_name": "SessionStart",
            "session_id": "s9",
            "cwd": str(project),
            "source": "startup",
        },
    ):
        direct = execute(payload, running)
        answer = client.decide(running, payload)
        assert (answer["exit_code"], answer["stdout"], answer["stderr"]) == (
            direct.exit_code,
            direct.stdout,
            direct.stderr,
        )


def test_enforce_mode_decides_the_same_through_the_server(project, tmp_path):
    spec = make_spec(
        project, tmp_path, "enforce", policy_source="directory", policy_dir=str(project)
    )
    thread = threading.Thread(target=serve, args=(spec, 5.0), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not client.ping(spec):
            assert time.monotonic() < deadline
            time.sleep(0.05)
        payload = edit(project, ".github/workflows/ci.yml")
        direct = execute(payload, spec)
        answer = client.decide(spec, payload)
        assert answer["stdout"] == direct.stdout and answer["exit_code"] == direct.exit_code
        assert "permissionDecision" in (answer["stdout"] or "")
    finally:
        client.stop(spec)
        thread.join(timeout=10)


def test_many_parallel_calls_are_all_recorded_and_the_chain_still_verifies(project, running):
    results = []

    def call(i):
        results.append(
            client.decide(running, edit(project, f"docs/f{i}.md", f"tu_{i}"))["exit_code"]
        )

    threads = [threading.Thread(target=call, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join(timeout=30) for t in threads]
    assert results == [0] * 8
    assert len(decisions(project)) == 8
    assert ledger_of(project).verify().ok


# ---------- the endpoint is closed to everyone but the client ----------


def raw(spec, request_bytes):
    port = client._credentials(spec)[0]
    with socket.create_connection(("127.0.0.1", port), timeout=5) as conn:
        conn.sendall(request_bytes)
        data = b""
        while chunk := conn.recv(65536):
            data += chunk
    return int(data.split(b" ", 2)[1]), data.partition(b"\r\n\r\n")[2]


def post(
    spec, *, auth=True, host=None, ctype="application/json", body=None, length=None, path="/hook"
):
    port, secret = client._credentials(spec)
    body = (
        json.dumps({"v": 1, "payload": edit(Path(spec["project_dir"]))}).encode()
        if body is None
        else body
    )
    lines = [f"POST {path} HTTP/1.1", f"Host: {host or f'127.0.0.1:{port}'}"]
    if auth:
        lines.append(f"Authorization: Bearer {secret if auth is True else auth}")
    if ctype:
        lines.append(f"Content-Type: {ctype}")
    lines += [
        f"Content-Length: {len(body) if length is None else length}",
        "Connection: close",
        "",
        "",
    ]
    return raw(spec, "\r\n".join(lines).encode() + body)


def test_a_request_without_the_secret_or_with_a_wrong_one_is_refused_and_nothing_is_decided(
    project, running
):
    assert post(running, auth=False)[0] == 401
    assert post(running, auth="wrong")[0] == 401
    assert post(running, auth="")[0] == 401
    assert decisions(project) == []


def test_the_host_header_must_be_this_address_which_stops_dns_rebinding(project, running):
    assert post(running, host="evil.example.com")[0] == 403
    assert post(running, host="127.0.0.1:1")[0] == 403
    assert decisions(project) == []


def test_only_json_is_accepted_which_a_web_page_cannot_send_cross_origin(project, running):
    assert post(running, ctype="text/plain")[0] == 415
    assert post(running, ctype="application/x-www-form-urlencoded")[0] == 415
    assert post(running, ctype=None)[0] == 415
    assert decisions(project) == []


@pytest.mark.parametrize(
    "body, expected",
    [
        (b"not json", 400),
        (b'{"v": 1}', 400),
        (b'{"v": 2, "payload": {}}', 400),
        (b'{"v":1,"payload":[1]}', 400),
    ],
)
def test_malformed_requests_are_refused_before_anything_is_decided(
    project, running, body, expected
):
    assert post(running, body=body)[0] == expected
    assert decisions(project) == []


def test_an_oversized_body_is_refused_without_reading_it(project, running):
    assert post(running, body=b"", length=50 * 1024 * 1024)[0] == 413


def test_unknown_paths_and_methods_are_not_served(running):
    assert post(running, path="/elsewhere")[0] == 404
    port, secret = client._credentials(running)
    status, _ = raw(
        running,
        (
            f"GET /hook HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
            f"Authorization: Bearer {secret}\r\n\r\n"
        ).encode(),
    )
    assert status == 404
    status, _ = raw(running, f"GET /health HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode())
    assert status == 401  # even the health check needs the secret


def test_stopping_the_server_needs_the_secret_too(running):
    assert post(running, auth="wrong", path="/stop")[0] == 401
    assert client.ping(running)


def test_the_server_binds_loopback_only_and_keeps_its_secret_out_of_the_project(project, running):
    port, secret = client._credentials(running)
    assert len(secret) == 64
    assert not any(
        secret in p.read_text(errors="ignore") for p in project.rglob("*") if p.is_file()
    )
    assert Path(secret_path(running)).parent == Path(discovery_path(running)).parent
    assert str(project) not in secret_path(running)  # kept under the per-user state directory
    with socket.socket() as probe:
        probe.settimeout(1)
        # a connection to the loopback address works; the server never listens on 0.0.0.0
        assert probe.connect_ex(("127.0.0.1", port)) == 0


# ---------- a missing server is never a missing decision ----------


def stdin_with(monkeypatch, payload):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))


def hook_env(monkeypatch, project, tmp_path):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    monkeypatch.setenv("JARVIS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("JARVIS_ON_BEHALF_OF", "alice")
    monkeypatch.delenv("JARVIS_HOOK_MODE", raising=False)


def test_with_no_server_and_none_that_can_start_the_client_decides_in_process_once(
    project, tmp_path, monkeypatch
):
    hook_env(monkeypatch, project, tmp_path)

    def cannot_start(_spec):
        raise OSError("no")

    monkeypatch.setattr(client, "_spawn", cannot_start)
    stdin_with(monkeypatch, edit(project))
    assert client.main_client(["hook", "run", "--mode", "log-only", "--server"]) == 0
    assert len(decisions(project)) == 1  # decided exactly once, here


def test_a_server_that_never_comes_up_costs_one_wait_and_then_a_minute_of_in_process_decisions(
    project, tmp_path, monkeypatch
):
    hook_env(monkeypatch, project, tmp_path)
    monkeypatch.setattr(client, "START_WAIT_SECONDS", 0.3)
    spawned = []
    monkeypatch.setattr(client, "_spawn", lambda spec: spawned.append(1))  # starts nothing

    for _ in range(3):
        stdin_with(monkeypatch, edit(project))
        assert client.main_client(["hook", "run", "--mode", "log-only", "--server"]) == 0
    assert spawned == [1]  # the failed start is remembered, so only the first call tried
    assert len(decisions(project)) == 3


def test_a_server_for_other_settings_is_never_used(project, tmp_path, monkeypatch, running):
    """The discovery file must hold exactly this call's spec, so a different mode, project or
    policy source never reaches a server built for another."""
    hook_env(monkeypatch, project, tmp_path)
    monkeypatch.setattr(client, "_spawn", lambda spec: (_ for _ in ()).throw(OSError("no")))
    other = {**running, "mode": "enforce"}
    assert client._credentials(other) is None
    assert spec_key(other) != spec_key(running)
    before = len(decisions(project))
    stdin_with(monkeypatch, edit(project, "docs/zzz.md", "tu_9"))
    code = client.main_client(
        [
            "hook",
            "run",
            "--mode",
            "enforce",
            "--policy-source",
            "directory",
            "--policy-dir",
            str(project),
            "--server",
        ]
    )
    assert code == 0
    assert len(decisions(project)) == before + 1  # decided in-process, not by the other server


def test_a_discovery_file_naming_a_different_spec_is_ignored(running):
    path = Path(discovery_path(running))
    data = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(
        json.dumps({**data, "spec": spec_json({**running, "mode": "enforce"})}), encoding="utf-8"
    )
    assert client._credentials(running) is None


def test_a_stale_secret_is_refused_so_the_client_decides_itself_and_forgets_the_server(
    project, tmp_path, monkeypatch, running
):
    hook_env(monkeypatch, project, tmp_path)
    Path(secret_path(running)).write_text("0" * 64, encoding="utf-8")
    monkeypatch.setattr(client, "_spawn", lambda spec: (_ for _ in ()).throw(OSError("no")))
    stdin_with(monkeypatch, edit(project))
    assert client.main_client(["hook", "run", "--mode", "log-only", "--server"]) == 0
    assert len(decisions(project)) == 1
    assert not Path(discovery_path(running)).exists()  # the stale server is forgotten


# ---------- a dead server must be replaced, not trusted forever ----------


def leave_stale_files(spec):
    """What a killed, crashed or rebooted-away server leaves behind: a discovery file and a
    secret that point at a port nobody listens on."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.close()  # nothing listens here any more
    os.makedirs(os.path.dirname(discovery_path(spec)), exist_ok=True)
    Path(discovery_path(spec)).write_text(
        json.dumps({"port": port, "pid": 1, "spec": spec_json(spec)}), encoding="utf-8"
    )
    Path(secret_path(spec)).write_text("d" * 64, encoding="utf-8")


def test_a_killed_server_leaves_stale_files_and_the_client_starts_a_new_one(project, tmp_path):
    """Found live: after a server was killed (not stopped), the client kept trying the dead port,
    fell back to deciding in-process, and never started another. The same happens after a crash
    or a reboot, because the state directory outlives the process."""
    spec = make_spec(project, tmp_path)
    leave_stale_files(spec)
    started = []

    def start_for_real(wanted):
        started.append(1)
        threading.Thread(target=serve, args=(wanted, 5.0), daemon=True).start()

    original = client._spawn
    client._spawn = start_for_real
    try:
        answer = client.decide(spec, edit(project))
    finally:
        client._spawn = original
        client.stop(spec)
    assert started == [1]  # the stale server was noticed and replaced
    assert answer["exit_code"] == 0
    assert len(decisions(project)) == 1  # decided once, by the new server


def test_stale_files_are_removed_even_when_no_new_server_can_start(project, tmp_path, monkeypatch):
    hook_env(monkeypatch, project, tmp_path)
    spec = build_spec(
        mode="log-only",
        config=None,
        state_dir=None,
        policy_source=None,
        policy_ref=None,
        policy_dir=None,
        payload_cwd=str(project),
    )
    leave_stale_files(spec)
    monkeypatch.setattr(client, "_spawn", lambda wanted: (_ for _ in ()).throw(OSError("no")))
    stdin_with(monkeypatch, edit(project))
    assert client.main_client(["hook", "run", "--mode", "log-only", "--server"]) == 0
    assert len(decisions(project)) == 1  # still decided, in-process, once
    assert not Path(discovery_path(spec)).exists()  # the dead server is forgotten


def test_a_live_server_is_not_replaced_just_because_one_request_was_refused(project, running):
    """Only a connection that never reached a server counts as dead; a refusal by a live one
    (bad secret) is a different case with its own handling."""
    before = client._credentials(running)
    assert client.decide(running, edit(project))["exit_code"] == 0
    assert client._credentials(running) == before


# ---------- a request that may have been processed is never decided again ----------


def silent_server(spec):
    """A 'server' that accepts the request and closes without answering."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    os.makedirs(os.path.dirname(discovery_path(spec)), exist_ok=True)
    Path(discovery_path(spec)).write_text(
        json.dumps({"port": listener.getsockname()[1], "spec": spec_json(spec)}), encoding="utf-8"
    )
    Path(secret_path(spec)).write_text("s" * 64, encoding="utf-8")

    def accept():
        conn, _ = listener.accept()
        conn.recv(65536)
        conn.close()
        listener.close()

    threading.Thread(target=accept, daemon=True).start()


def test_if_a_sent_request_gets_no_answer_enforce_mode_blocks_and_nothing_is_decided_again(
    project, tmp_path, monkeypatch, capsys
):
    hook_env(monkeypatch, project, tmp_path)
    stdin_with(monkeypatch, edit(project))
    spec = build_spec(
        mode="enforce",
        config=None,
        state_dir=None,
        policy_source=None,
        policy_ref=None,
        policy_dir=None,
        payload_cwd=str(project),
    )
    silent_server(spec)
    code = client.main_client(["hook", "run", "--mode", "enforce", "--server"])
    assert code == 2
    assert "blocked this call" in capsys.readouterr().err
    assert decisions(project) == []  # the client did not go on to decide it a second time


def test_if_a_sent_request_gets_no_answer_log_only_lets_the_call_through_with_a_note(
    project, tmp_path, monkeypatch, capsys
):
    hook_env(monkeypatch, project, tmp_path)
    stdin_with(monkeypatch, edit(project))
    spec = build_spec(
        mode="log-only",
        config=None,
        state_dir=None,
        policy_source=None,
        policy_ref=None,
        policy_dir=None,
        payload_cwd=str(project),
    )
    silent_server(spec)
    assert client.main_client(["hook", "run", "--mode", "log-only", "--server"]) == 0
    assert "the call was allowed" in capsys.readouterr().err
    assert decisions(project) == []


def test_invalid_json_goes_to_the_full_hook_which_owns_that_policy(
    project, tmp_path, monkeypatch, capsys
):
    hook_env(monkeypatch, project, tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO("{ not json"))
    assert client.main_client(["hook", "run", "--mode", "enforce", "--server"]) == 2
    assert "not valid JSON" in capsys.readouterr().err


# ---------- lifecycle ----------


def test_an_idle_server_shuts_itself_down_and_removes_its_files(project, tmp_path):
    spec = make_spec(project, tmp_path)
    thread = threading.Thread(target=serve, args=(spec, 0.05), daemon=True)  # 3 seconds idle
    thread.start()
    deadline = time.monotonic() + 10
    while not client.ping(spec):
        assert time.monotonic() < deadline
        time.sleep(0.05)
    thread.join(timeout=15)
    assert not thread.is_alive()
    assert not Path(discovery_path(spec)).exists() and not Path(secret_path(spec)).exists()
    assert not client.ping(spec)


def test_a_second_server_for_the_same_spec_does_not_start(running):
    first = client._credentials(running)
    assert serve(running, 5.0) == 0  # returns at once: one is already answering
    assert client._credentials(running) == first


def test_stop_and_status_commands(project, tmp_path, monkeypatch, running, capsys):
    hook_env(monkeypatch, project, tmp_path)
    assert main(["hook", "serve", "--status", "--mode", "log-only"]) == 0
    assert "running" in capsys.readouterr().out
    assert main(["hook", "serve", "--stop", "--mode", "log-only"]) == 0
    assert "stopped" in capsys.readouterr().out
    deadline = time.monotonic() + 10
    while client.ping(running):
        assert time.monotonic() < deadline
        time.sleep(0.05)
    assert main(["hook", "serve", "--status", "--mode", "log-only"]) == 1
    assert main(["hook", "serve", "--stop", "--mode", "log-only"]) == 0
    assert "no server was running" in capsys.readouterr().out


# ---------- the command line ----------


def test_the_server_flag_routes_to_the_client_and_without_it_nothing_changes(monkeypatch):
    calls = []
    monkeypatch.setattr(client, "main_client", lambda args: calls.append(args) or 0)
    assert main(["hook", "run", "--mode", "log-only", "--server"]) == 0
    assert calls == [["hook", "run", "--mode", "log-only", "--server"]]
    monkeypatch.setenv("JARVIS_HOOK_SERVER", "1")
    assert main(["hook", "run", "--mode", "log-only"]) == 0
    assert len(calls) == 2


def test_the_snippet_can_register_the_server_flag(capsys):
    assert main(["hook", "snippet", "--server"]) == 0
    hooks = json.loads(capsys.readouterr().out)["hooks"]
    assert "--server" in hooks["PreToolUse"][0]["hooks"][0]["args"]
    assert "--server" not in json.dumps(
        __import__("jarvis.adapters.claude_code", fromlist=["build_settings"]).build_settings()
    )


# ---------- starting the server must never open a window ----------

CREATE_NEW_PROCESS_GROUP = 0x00000200
DETACHED_PROCESS = 0x00000008
CREATE_NO_WINDOW = 0x08000000


def test_on_windows_the_server_runs_under_pythonw_so_no_console_window_can_open(tmp_path):
    """DETACHED_PROCESS leaves the server without a console, and a venv's python.exe launcher
    starts the real interpreter (a console program) which then makes Windows open a terminal
    window. pythonw.exe never creates a console."""
    python = tmp_path / "python.exe"
    pythonw = tmp_path / "pythonw.exe"
    python.write_text("", encoding="utf-8")
    assert client._server_interpreter("win32", str(python)) == str(python)  # no pythonw: fall back
    pythonw.write_text("", encoding="utf-8")
    assert client._server_interpreter("win32", str(python)) == str(pythonw)
    assert client._server_interpreter("linux", str(python)) == str(python)


def test_the_windows_flags_stay_the_ones_that_survive_under_claude_code():
    """Observed: with DETACHED_PROCESS the server outlives the hook that started it; with
    CREATE_NO_WINDOW alone it was killed within seconds. Do not drop DETACHED_PROCESS."""
    flags = client._spawn_options("win32")["creationflags"]
    assert (
        flags & DETACHED_PROCESS and flags & CREATE_NO_WINDOW and flags & CREATE_NEW_PROCESS_GROUP
    )


def test_elsewhere_the_server_gets_its_own_session():
    options = client._spawn_options("linux")
    assert options == {"start_new_session": True}


# ---------- end to end, with a real detached server ----------


def run_hook(project, state, payload, *extra, idle="1"):
    env = {
        **os.environ,
        "CLAUDE_PROJECT_DIR": str(project),
        "JARVIS_STATE_DIR": str(state),
        "JARVIS_ON_BEHALF_OF": "alice",
        "JARVIS_HOOK_SERVER_IDLE_MINUTES": idle,
    }
    env.pop("JARVIS_HOOK_MODE", None)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "jarvis.cli",
            "hook",
            "run",
            "--mode",
            "log-only",
            "--server",
            *extra,
        ],
        input=json.dumps(payload).encode(),
        capture_output=True,
        env=env,
        cwd=str(project),
        timeout=60,
    )


def stop_server(project, state):
    env = {
        **os.environ,
        "CLAUDE_PROJECT_DIR": str(project),
        "JARVIS_STATE_DIR": str(state),
        "JARVIS_ON_BEHALF_OF": "alice",
    }
    subprocess.run(
        [sys.executable, "-m", "jarvis.cli", "hook", "serve", "--stop", "--mode", "log-only"],
        env=env,
        cwd=str(project),
        capture_output=True,
        timeout=30,
    )


def test_the_first_call_starts_a_detached_server_and_later_calls_use_it(project, tmp_path):
    state = tmp_path / "state"
    try:
        first = run_hook(project, state, edit(project))
        assert first.returncode == 0, first.stderr
        deadline = time.monotonic() + 15
        files = []
        while time.monotonic() < deadline and not files:
            files = list((state / "server").glob("*.json")) if (state / "server").exists() else []
            time.sleep(0.1)
        assert files, "no server was left running"
        second = run_hook(project, state, edit(project, "docs/b.md", "tu_2"))
        assert second.returncode == 0, second.stderr
        assert len(decisions(project)) == 2  # one each, never twice
        assert ledger_of(project).verify().ok
    finally:
        stop_server(project, state)


def test_a_client_that_finds_a_server_loads_neither_pydantic_nor_the_hook(project, tmp_path):
    """The point of the server is that the per-call process stays small."""
    state = tmp_path / "state"
    script = (
        "import io, json, sys\n"
        "from jarvis.cli import main\n"
        "sys.stdin = io.StringIO(sys.argv[1])\n"
        "code = main(['hook', 'run', '--mode', 'log-only', '--server'])\n"
        "heavy = [m for m in ('pydantic', 'yaml', 'jarvis.hook_cli', 'jarvis.core.schemas', "
        "'jarvis.core.ledger') if m in sys.modules]\n"
        "print(json.dumps([code, heavy]))\n"
    )
    env = {
        **os.environ,
        "CLAUDE_PROJECT_DIR": str(project),
        "JARVIS_STATE_DIR": str(state),
        "JARVIS_ON_BEHALF_OF": "alice",
        "JARVIS_HOOK_SERVER_IDLE_MINUTES": "1",
    }
    env.pop("JARVIS_HOOK_MODE", None)
    try:
        assert run_hook(project, state, edit(project)).returncode == 0  # starts the server
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not (state / "server").exists():
            time.sleep(0.1)
        done = subprocess.run(
            [sys.executable, "-c", script, json.dumps(edit(project, "docs/c.md", "tu_3"))],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(project),
            timeout=60,
        )
        assert done.returncode == 0, done.stderr
        code, heavy = json.loads(done.stdout.strip().splitlines()[-1])
        assert code == 0 and heavy == [], f"the client imported {heavy}"
    finally:
        stop_server(project, state)
