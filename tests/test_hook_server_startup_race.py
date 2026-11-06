"""A call that reaches a server which is starting or stopping (task P2.6h, ADR 0012).

Found in the first daily check: a call that arrived while a server was shutting down was
accepted by the operating system and dropped unanswered, and the client, which must never decide
a call twice, gave up on it. In enforce mode that would have blocked a legitimate call. The fix:
the client sends its headers with ``Expect: 100-continue`` and the body only after the server says
it will read it, so a connection that ends before that provably carried no decision.
"""

import json
import os
import socket
import threading
import time

import pytest

import jarvis.hook_client as client
from jarvis.hook_paths import build_spec, discovery_path, secret_path
from jarvis.hook_server import HookHttpServer
from test_hook_server import (
    decisions,
    edit,
    hook_env,
    make_spec,
    silent_server,
    stdin_with,
)
from test_hook_server import project as _shared_project
from test_hook_server import running as _shared_running

project = _shared_project  # the same fixtures the main hook server tests use
running = _shared_running


def listener():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(4)
    return sock


def serve_once(sock, behave):
    def run():
        conn, _ = sock.accept()
        try:
            behave(conn)
        finally:
            conn.close()
            sock.close()

    threading.Thread(target=run, daemon=True).start()
    return sock.getsockname()[1]


BODY = b'{"v":1,"payload":{}}'


# ---------- what the client concludes from each way a server can behave ----------


def test_a_connection_closed_before_the_server_agreed_to_read_is_not_a_decision():
    port = serve_once(listener(), lambda conn: conn.recv(65536))  # reads the headers, hangs up
    with pytest.raises(client.Unavailable) as info:
        client._request(port, "s", "POST", "/hook", BODY)
    assert info.value.dead  # nothing was processed, so a fresh server may be asked


def test_a_reset_before_the_server_agreed_to_read_is_not_a_decision():
    def reset(conn):
        conn.recv(65536)
        conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")

    port = serve_once(listener(), reset)  # closing with linger 0 sends a reset
    with pytest.raises(client.Unavailable) as info:
        client._request(port, "s", "POST", "/hook", BODY)
    assert info.value.dead


def test_a_server_that_never_answers_the_handshake_is_not_a_decision(monkeypatch):
    monkeypatch.setattr(client, "HANDSHAKE_TIMEOUT", 0.3)
    stop = threading.Event()
    port = serve_once(listener(), lambda conn: (conn.recv(65536), stop.wait(2)))
    started = time.monotonic()
    with pytest.raises(client.Unavailable) as info:
        client._request(port, "s", "POST", "/hook", BODY)
    stop.set()
    assert info.value.dead and time.monotonic() - started < 1.5


def test_a_server_that_agreed_to_read_and_then_died_may_have_decided_so_it_is_failed():
    def take_then_die(conn):
        conn.recv(65536)
        conn.sendall(b"HTTP/1.1 100 Continue\r\n\r\n")
        conn.recv(65536)  # it has the body now

    port = serve_once(listener(), take_then_die)
    with pytest.raises(client.Failed):
        client._request(port, "s", "POST", "/hook", BODY)


def test_a_reset_after_the_server_agreed_to_read_is_failed_not_retried():
    def take_then_reset(conn):
        conn.recv(65536)
        conn.sendall(b"HTTP/1.1 100 Continue\r\n\r\n")
        conn.recv(65536)  # it has the body now
        conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")

    port = serve_once(listener(), take_then_reset)
    with pytest.raises(client.Failed):  # it may have decided the call, so it is never asked again
        client._request(port, "s", "POST", "/hook", BODY)


def test_the_body_is_not_sent_until_the_server_asks_for_it():
    seen = {}

    def inspect(conn):
        seen["head"] = conn.recv(65536)
        conn.settimeout(0.3)
        try:
            seen["early"] = conn.recv(65536)  # anything sent before the 100 Continue
        except TimeoutError:
            seen["early"] = b""
        conn.sendall(b"HTTP/1.1 100 Continue\r\n\r\n")
        conn.settimeout(2)
        seen["body"] = conn.recv(65536)
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")

    port = serve_once(listener(), inspect)
    assert client._request(port, "s", "POST", "/hook", BODY) == (200, b"{}")
    assert b"Expect: 100-continue" in seen["head"] and BODY not in seen["head"]
    assert seen["early"] == b"" and seen["body"] == BODY


def test_a_rejection_that_comes_before_the_handshake_is_read_as_an_answer():
    def refuse(conn):
        conn.recv(65536)
        conn.sendall(b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\n\r\n")

    port = serve_once(listener(), refuse)
    assert client._request(port, "s", "POST", "/hook", BODY) == (401, b"")


def test_requests_without_a_body_are_sent_in_one_piece():
    seen = {}

    def answer(conn):
        seen["data"] = conn.recv(65536)
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")

    port = serve_once(listener(), answer)
    assert client._request(port, "s", "GET", "/health")[0] == 200
    assert b"Expect" not in seen["data"]


# ---------- through the client, with real servers ----------


def hook_spec(root, mode="log-only"):
    protected = mode == "enforce"  # enforce mode needs a policy source the agent cannot write
    return build_spec(
        mode=mode,
        config=None,
        state_dir=None,
        policy_source="directory" if protected else None,
        policy_ref=None,
        policy_dir=str(root) if protected else None,
        payload_cwd=str(root),
    )


def hook_args(root, mode):
    args = ["hook", "run", "--mode", mode, "--server"]
    if mode == "enforce":
        args += ["--policy-source", "directory", "--policy-dir", str(root)]
    return args


@pytest.mark.parametrize("mode", ["log-only", "enforce"])
def test_a_server_that_hangs_up_before_reading_is_replaced_and_the_call_decided_once(
    project,
    tmp_path,
    monkeypatch,
    capsys,
    mode,
):
    hook_env(monkeypatch, project, tmp_path)
    stdin_with(monkeypatch, edit(project))
    spec = hook_spec(project, mode)
    silent_server(spec, acknowledge=False)  # accepts, never reads, closes: a dying server
    code = client.main_client(hook_args(project, mode))
    assert code == 0, capsys.readouterr().err  # not blocked, not "allowed with a note"
    assert len(decisions(project)) == 1  # decided exactly once, by the replacement
    client.stop(spec)


def test_a_server_that_took_the_call_and_died_is_still_never_asked_again(
    project,
    tmp_path,
    monkeypatch,
    capsys,
):
    hook_env(monkeypatch, project, tmp_path)
    stdin_with(monkeypatch, edit(project))
    spec = hook_spec(project, "enforce")
    silent_server(spec, acknowledge=True)
    code = client.main_client(hook_args(project, "enforce"))
    assert code == 2 and "blocked this call" in capsys.readouterr().err
    assert decisions(project) == []  # the rule that matters is unchanged


@pytest.mark.parametrize("gap", [0.0, 0.03, 0.12])
def test_calls_made_just_after_a_stop_are_all_answered_and_each_decided_once(
    project,
    tmp_path,
    monkeypatch,
    gap,
):
    hook_env(monkeypatch, project, tmp_path)
    spec = hook_spec(project)
    rounds = 8
    for i in range(rounds):
        payload = edit(project, tool_use_id=f"warm{i}")
        client.decide(spec, payload)  # a server is up
        client.stop(spec)  # ... and is told to stop
        time.sleep(gap)
        client.decide(spec, edit(project, tool_use_id=f"call{i}"))  # a call arrives right after
    client.stop(spec)
    ids = [d["data"]["context"]["tool_use_id"] for d in decisions(project)]
    assert sorted(ids) == sorted(
        [f"warm{i}" for i in range(rounds)] + [f"call{i}" for i in range(rounds)]
    )
    assert len(ids) == len(set(ids))  # nothing decided twice, nothing lost


# ---------- the server's side of a shutdown ----------


@pytest.fixture
def bare_server(project, tmp_path):
    spec = make_spec(project, tmp_path)
    server = HookHttpServer(spec, "secret-one", 600)
    yield spec, server
    server.server_close()


def write_files(spec, pid, secret):
    os.makedirs(os.path.dirname(discovery_path(spec)), exist_ok=True)
    with open(discovery_path(spec), "w", encoding="utf-8") as handle:
        json.dump({"pid": pid, "port": 1}, handle)
    with open(secret_path(spec), "w", encoding="utf-8") as handle:
        handle.write(secret)


def test_a_server_removes_its_own_files(bare_server):
    spec, server = bare_server
    write_files(spec, os.getpid(), "secret-one")
    server.release_files()
    assert not os.path.exists(discovery_path(spec)) and not os.path.exists(secret_path(spec))


def test_a_server_never_removes_the_files_of_the_server_that_replaced_it(bare_server):
    spec, server = bare_server
    write_files(spec, os.getpid() + 12345, "a-newer-servers-secret")
    server.release_files()
    assert os.path.exists(discovery_path(spec)) and os.path.exists(secret_path(spec))


def test_a_shutdown_waits_for_a_request_that_is_still_being_handled(bare_server):
    _, server = bare_server
    server.connection_opened()
    threading.Timer(0.3, server.connection_closed).start()
    started = time.monotonic()
    assert server.drain(timeout=3)
    assert time.monotonic() - started >= 0.25  # it did wait
    server.connection_opened()
    assert not server.drain(timeout=0.2)  # a request that never finishes is given up on
    server.connection_closed()


def test_beginning_a_shutdown_hides_the_server_from_new_clients_at_once(bare_server):
    spec, server = bare_server
    write_files(spec, os.getpid(), "secret-one")
    serving = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    serving.start()
    server.begin_shutdown()
    assert not os.path.exists(discovery_path(spec))  # before the loop has even stopped
    server.begin_shutdown()  # a second call changes nothing
    serving.join(timeout=3)
    assert not serving.is_alive()


def test_the_server_speaks_http_1_1_and_still_serves_one_request_per_connection(project, tmp_path):
    from test_hook_server import running  # noqa: F401

    spec = make_spec(project, tmp_path)
    client._ensure_server(spec)
    try:
        port, secret = client._credentials(spec)
        with socket.create_connection(("127.0.0.1", port), timeout=3) as conn:
            request = (
                f"GET /health HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                f"Authorization: Bearer {secret}\r\n\r\n"
            )
            conn.sendall(request.encode())
            conn.settimeout(3)
            data = b""
            while True:  # without `Connection: close` the server must still end the exchange
                chunk = conn.recv(65536)
                if not chunk:
                    break
                data += chunk
        assert data.startswith(b"HTTP/1.1 200") and b"Connection: close" in data
    finally:
        client.stop(spec)


# ---------- a refusal must reach the client, whatever the size of the body ----------


def raw_exchange(spec, head_lines, body=b""):
    """Send ``head_lines`` (with `Expect: 100-continue`) and read what the server says first."""
    port, secret = client._credentials(spec)
    lines = [line.format(port=port, secret=secret) for line in head_lines]
    with socket.create_connection(("127.0.0.1", port), timeout=3) as conn:
        conn.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        conn.settimeout(3)
        first = conn.recv(65536)
        return first


GOOD = [
    "POST /hook HTTP/1.1",
    "Host: 127.0.0.1:{port}",
    "Authorization: Bearer {secret}",
    "Content-Type: application/json",
    "Content-Length: 20",
    "Expect: 100-continue",
]


def changed(index, line):
    return [line if i == index else value for i, value in enumerate(GOOD)]


@pytest.mark.parametrize(
    ("lines", "status"),
    [
        (changed(2, "Authorization: Bearer not-the-secret"), b"401"),
        (changed(1, "Host: evil.example"), b"403"),
        (changed(0, "POST /elsewhere HTTP/1.1"), b"404"),
        (changed(3, "Content-Type: text/plain"), b"415"),
        (changed(4, "Content-Length: nonsense"), b"411"),
        (changed(4, "Content-Length: 999999999"), b"413"),
    ],
)
def test_a_request_that_will_be_refused_is_refused_before_any_100_continue(running, lines, status):
    first = raw_exchange(running, lines)
    assert first.startswith(b"HTTP/1.1 " + status), first[:60]
    assert b"100 Continue" not in first


def test_a_request_that_will_be_accepted_gets_its_100_continue(running):
    assert raw_exchange(running, GOOD).startswith(b"HTTP/1.1 100 Continue")


def test_a_stop_with_the_wrong_secret_is_refused_without_a_100(running):
    lines = [
        "POST /stop HTTP/1.1",
        "Host: 127.0.0.1:{port}",
        "Authorization: Bearer nope",
        "Content-Length: 0",
        "Expect: 100-continue",
    ]
    assert raw_exchange(running, lines).startswith(b"HTTP/1.1 401")
    assert client.ping(running)  # and the server is still there


@pytest.mark.parametrize("size", [50, 200_000, 2_000_000])
def test_a_refusal_is_always_read_by_the_client_even_with_a_big_body(running, size):
    """Seen on Windows CI: the server said 100, refused, and closed with the body unread, which
    resets the connection and can throw the refusal away. Before the fix about 4 in 60 refusals
    were lost with a small body and 48 in 60 with 2 MB."""
    port, _ = client._credentials(running)
    body = b'{"v":1,"payload":{"pad":"' + b"x" * size + b'"}}'
    for _ in range(40):
        status, _ = client._request(port, "wrong-secret", "POST", "/hook", body)
        assert status == 401


def test_a_client_that_leaves_after_the_100_changes_nothing_and_prints_nothing(
    project, running, capsys
):
    from test_hook_server import decisions as recorded

    port, secret = client._credentials(running)
    head = (
        f"POST /hook HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAuthorization: Bearer {secret}\r\n"
        "Content-Type: application/json\r\nContent-Length: 500\r\nExpect: 100-continue\r\n\r\n"
    )
    with socket.create_connection(("127.0.0.1", port), timeout=3) as conn:
        conn.sendall(head.encode())
        conn.settimeout(3)
        assert conn.recv(100).startswith(b"HTTP/1.1 100")
    # it walks away without sending the body
    time.sleep(0.2)
    assert client.ping(running)
    assert recorded(project) == []
    assert "Traceback" not in capsys.readouterr().err
