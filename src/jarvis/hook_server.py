"""A long-lived local server for the Claude Code hook (task P2.6f, ADR 0012).

A fresh Python process per tool call spends most of its time importing pydantic and our own
modules. This server pays that once and answers hook calls from a tiny client in a few
milliseconds. It runs the **same** ``hook_cli.execute`` as the in-process hook, so decisions and
ledger records are identical; only where the code runs differs.

What protects the endpoint (it is a local door into the policy gate and the audit ledger):

* it binds ``127.0.0.1`` only, on a port the operating system picks;
* every request needs ``Authorization: Bearer <secret>``, a random secret written to a per-user
  file outside the project when the server starts and compared in constant time;
* the ``Host`` header must be this address, which defeats DNS rebinding;
* the body must be ``application/json``, which a web page cannot send to another origin without a
  preflight the server never answers;
* the body is capped, nothing from a payload is logged, and a rejected request is answered
  before anything is processed (so a client can safely fall back).

It serves exactly one spec (see ``hook_paths``) and exits after an idle period.

The secret is a file the same user can read. An agent running as that user can read it too: this
stops other programs, web pages and other users, not an agent with a shell (ADR 0012).
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import secrets
import sys
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from jarvis.hook_paths import (
    DEFAULT_IDLE_MINUTES,
    PROTOCOL,
    discovery_path,
    log_path,
    secret_path,
    spec_json,
    spec_key,
)

MAX_BODY = 8 * 1024 * 1024
LOG_LIMIT = 100_000


DRAIN_SECONDS = 15.0


class HookHttpServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, spec: dict[str, Any], secret: str, idle_seconds: float) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.spec = spec
        self.secret = secret
        self.idle_seconds = idle_seconds
        self.last_request = time.monotonic()
        self.key = spec_key(spec)
        self._connections = 0
        self._lock = threading.Lock()
        self._released = False

    def touch(self) -> None:
        self.last_request = time.monotonic()

    def handle_error(self, request: Any, client_address: Any) -> None:
        """A client that leaves in the middle of a request is normal, not an error to print."""
        if isinstance(sys.exc_info()[1], OSError):
            return
        super().handle_error(request, client_address)

    # ---------- connections in progress, so a shutdown can wait for them ----------

    def connection_opened(self) -> None:
        with self._lock:
            self._connections += 1

    def connection_closed(self) -> None:
        with self._lock:
            self._connections -= 1

    def drain(self, timeout: float = DRAIN_SECONDS) -> bool:
        """Wait until no connection is being handled. True if they all finished in time."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if self._connections <= 0:
                    return True
            time.sleep(0.01)
        return False

    # ---------- the files that tell clients where to find this server ----------

    def release_files(self) -> None:
        """Remove this server's discovery and secret files, but only if they are still its own.

        A client that read them just before this ran can still connect and be dropped; the
        client's handshake (see ``hook_client``) tells it nothing was processed, so it starts a
        new server and asks again. Removing only our own files means a shutdown that finishes late
        can never delete the files of the server that replaced us.
        """
        try:
            with open(discovery_path(self.spec), encoding="utf-8") as handle:
                mine = json.load(handle).get("pid") == os.getpid()
        except (OSError, ValueError, AttributeError):
            mine = False
        if mine:
            _unlink(discovery_path(self.spec))
        try:
            with open(secret_path(self.spec), encoding="utf-8") as handle:
                ours = hmac.compare_digest(handle.read().encode(), self.secret.encode())
        except OSError:
            ours = False
        if ours:
            _unlink(secret_path(self.spec))

    def begin_shutdown(self) -> None:
        """Stop serving: stop being found first, then stop the loop. Safe to call twice."""
        with self._lock:
            if self._released:
                return
            self._released = True
        self.release_files()
        threading.Thread(target=self.shutdown, daemon=True).start()


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


class _Handler(BaseHTTPRequestHandler):
    server_version = "jarvis-hook"
    sys_version = ""
    # HTTP/1.1 so that a client can ask "may I send the body?" with `Expect: 100-continue` and
    # learn that a dying server never read its request. Still one request per connection.
    protocol_version = "HTTP/1.1"

    server: HookHttpServer

    def log_message(self, *args: Any) -> None:  # never log requests: they carry tool calls
        return

    def setup(self) -> None:
        super().setup()
        self.server.connection_opened()

    def finish(self) -> None:
        try:
            super().finish()
        finally:
            self.server.connection_closed()

    def _reply(self, status: int, body: dict[str, Any] | None = None) -> None:
        data = json.dumps(body).encode("utf-8") if body is not None else b""
        self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def _authorised(self) -> bool:
        port = self.server.server_address[1]
        if self.headers.get("Host") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            self._reply(403)
            return False
        given = self.headers.get("Authorization", "")
        expected = f"Bearer {self.server.secret}"
        if not hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8")):
            self._reply(401)
            return False
        return True

    def do_GET(self) -> None:
        if not self._authorised():
            return
        if self.path != "/health":
            self._reply(404)
            return
        self.server.touch()
        self._reply(200, {"ok": True, "pid": os.getpid(), "key": self.server.key, "v": PROTOCOL})

    def _call_length(self) -> int | None:
        """The size of the call's body, once the request has passed every check that needs only
        the headers; otherwise the refusal has been sent and this is ``None``."""
        if not self._authorised():
            return None
        if self.path != "/hook":
            self._reply(404)
            return None
        if not (self.headers.get("Content-Type") or "").lower().startswith("application/json"):
            self._reply(415)
            return None
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._reply(411)
            return None
        if not 0 <= length <= MAX_BODY:
            self._reply(413)
            return None
        return length

    def handle_expect_100(self) -> bool:
        """Agree to read the body only for a request that will be accepted.

        Saying ``100 Continue`` first and refusing afterwards made the client send a body into a
        connection the server was about to close with that data unread, which resets the
        connection and can discard the refusal before the client reads it (seen on Windows). A
        request that will be refused gets its final answer now, and no body is ever sent."""
        if self.command == "POST" and self.path == "/stop":
            if not self._authorised():
                return False
        elif self._call_length() is None:
            return False
        self.send_response_only(100)
        self.end_headers()
        return True

    def do_POST(self) -> None:
        if self.path == "/stop":
            if not self._authorised():
                return
            self._reply(200, {"stopping": True})
            self.server.begin_shutdown()
            return
        length = self._call_length()
        if length is None:
            return
        raw = self.rfile.read(length)
        try:
            request = json.loads(raw)
            payload = request["payload"]
            if request.get("v") != PROTOCOL or not isinstance(payload, dict):
                raise ValueError("bad request")
        except (ValueError, KeyError, TypeError):
            self._reply(400)
            return
        self.server.touch()
        from jarvis.hook_cli import execute  # the same code the in-process hook runs

        try:
            result = execute(payload, self.server.spec)
        except Exception:  # execute never raises; if it does, the call was not decided
            self._reply(500)
            return
        self._reply(
            200,
            {
                "v": PROTOCOL,
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
            },
        )


def _write_private(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _log(spec: dict[str, Any], message: str) -> None:
    try:
        path = log_path(spec)
        if os.path.exists(path) and os.path.getsize(path) > LOG_LIMIT:
            open(path, "w", encoding="utf-8").close()
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{datetime.now(UTC).isoformat()} {message}\n")
    except OSError:
        return


def serve(spec: dict[str, Any], idle_minutes: float = DEFAULT_IDLE_MINUTES) -> int:
    """Run the server for ``spec`` until it has been idle for ``idle_minutes``. Returns 0."""
    from jarvis.hook_client import (
        ping,
    )  # already running for this spec? then there is nothing to do

    if ping(spec):
        return 0
    secret = secrets.token_hex(32)
    server = HookHttpServer(spec, secret, idle_minutes * 60)
    _write_private(secret_path(spec), secret)
    port = server.server_address[1]
    _write_private(
        discovery_path(spec),
        json.dumps(
            {
                "port": port,
                "pid": os.getpid(),
                "started": datetime.now(UTC).isoformat(),
                "v": PROTOCOL,
                "spec": spec_json(spec),  # the client compares it exactly before trusting us
            }
        ),
    )
    _log(spec, f"started on port {port}, idle limit {idle_minutes} minutes")

    def watch_idle() -> None:
        while True:
            time.sleep(min(5.0, max(server.idle_seconds / 4, 0.05)))
            if time.monotonic() - server.last_request > server.idle_seconds:
                _log(spec, "idle: shutting down")
                server.begin_shutdown()
                return

    threading.Thread(target=watch_idle, daemon=True).start()
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
        if not server.drain():  # a request still running after this long is left to the client
            _log(spec, "stopped with a request still running")
        server.release_files()
        _log(spec, "stopped")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m jarvis.hook_server")
    parser.add_argument("--spec", required=True, help="JSON spec (see jarvis.hook_paths)")
    parser.add_argument("--idle-minutes", type=float, default=DEFAULT_IDLE_MINUTES)
    args = parser.parse_args(argv)
    return serve(json.loads(args.spec), args.idle_minutes)


if __name__ == "__main__":
    sys.exit(main())
