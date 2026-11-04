"""The tiny client that asks the hook server for a decision (task P2.6f, ADR 0012).

Standard library only and almost no modules (``json``, ``os``, ``socket``, ``time``, ``zlib``), so
it starts in milliseconds instead of importing pydantic. It is what ``agentctl hook run --server``
runs. The rules that make it safe to put in front of the gate:

* **Nothing is ever decided twice.** If the server cannot be reached, or rejects the request
  before processing it (bad secret, bad host), the client decides *in-process* with the same code
  the server would have run, just slower. If a request was sent and no valid answer came back, it
  may have been processed, so the client does **not** retry or decide again: in enforce mode it
  blocks the call, and in log-only mode it lets it through with a note on stderr.
* **A missing server is never a missing decision.** The fallback means a stopped, crashed or
  never-started server only costs time.
* **It never trusts a server it did not expect.** The server's discovery file must hold exactly
  this call's spec, and every request carries the secret kept beside it.
* **It starts the server when needed**, detached, once per spec, with a lock so concurrent calls
  do not start several, waits a few seconds for it, and after a failed start decides in-process
  for a minute instead of waiting again on every call.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
from typing import TYPE_CHECKING

from jarvis.hook_paths import (
    DEFAULT_IDLE_MINUTES,
    PROTOCOL,
    build_spec,
    discovery_path,
    failed_path,
    lock_path,
    log_path,
    secret_path,
    server_dir,
    spec_json,
)

if TYPE_CHECKING:
    from typing import Any

CONNECT_TIMEOUT = 2.0
REQUEST_TIMEOUT = 8.0  # the hook's own timeout is longer; a healthy server answers in ~50 ms
START_WAIT_SECONDS = 6.0
START_LOCK_STALE_SECONDS = 30.0
FAILED_BACKOFF_SECONDS = 60.0
MAX_RESPONSE = 1024 * 1024

_OPTIONS = {
    "--mode": "mode",
    "--config": "config",
    "--state-dir": "state_dir",
    "--policy-source": "policy_source",
    "--policy-ref": "policy_ref",
    "--policy-dir": "policy_dir",
}


class Unavailable(Exception):
    """Nothing was processed by a server: it is down, or it rejected the request first."""


class Failed(Exception):
    """A request was sent and no valid answer came back, so it may have been processed."""


def _read_json(path: str) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _remove(*paths: str) -> None:
    for path in paths:
        try:
            os.unlink(path)
        except OSError:
            pass


def _request(
    port: int,
    secret: str,
    method: str,
    path: str,
    body: bytes = b"",
    timeout: float = REQUEST_TIMEOUT,
) -> tuple[int, bytes]:
    """One HTTP exchange over loopback. ``Unavailable`` if it never reached the server,
    ``Failed`` if it did and no answer came back."""
    try:
        connection = socket.create_connection(("127.0.0.1", port), timeout=CONNECT_TIMEOUT)
    except OSError as exc:
        raise Unavailable(type(exc).__name__) from None
    try:
        connection.settimeout(timeout)
        head = (
            f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
            f"Authorization: Bearer {secret}\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
        ).encode("ascii")
        try:
            connection.sendall(head + body)  # the server reads the whole body before acting
        except OSError as exc:
            raise Unavailable(type(exc).__name__) from None
        data = b""
        try:
            while True:
                chunk = connection.recv(65536)
                if not chunk:
                    break
                data += chunk
                if len(data) > MAX_RESPONSE:
                    raise Failed("the answer was too large")
        except OSError as exc:
            raise Failed(type(exc).__name__) from None
    finally:
        connection.close()
    first, _, rest = data.partition(b"\r\n\r\n")
    try:
        status = int(first.split(b" ", 2)[1])
    except (IndexError, ValueError):
        raise Failed("the answer was not HTTP") from None
    return status, rest


def _credentials(spec: dict[str, Any]) -> tuple[int, str] | None:
    """The port and secret of a server for exactly this spec, or ``None``."""
    found = _read_json(discovery_path(spec))
    if not found or found.get("spec") != spec_json(spec) or not isinstance(found.get("port"), int):
        return None
    try:
        with open(secret_path(spec), encoding="utf-8") as handle:
            secret = handle.read().strip()
    except OSError:
        return None
    return (found["port"], secret) if secret else None


def ping(spec: dict[str, Any]) -> bool:
    """Is a server for this spec running and answering?"""
    creds = _credentials(spec)
    if creds is None:
        return False
    try:
        status, body = _request(creds[0], creds[1], "GET", "/health", timeout=3.0)
        return status == 200 and bool(json.loads(body or b"{}").get("ok"))
    except (Unavailable, Failed, ValueError):
        return False


def stop(spec: dict[str, Any]) -> bool:
    """Ask a running server for this spec to stop. True if there was one to stop."""
    creds = _credentials(spec)
    if creds is None:
        return False
    try:
        status, _ = _request(creds[0], creds[1], "POST", "/stop", b"{}", timeout=3.0)
    except (Unavailable, Failed):
        return False
    return status == 200


def _spawn(spec: dict[str, Any]) -> None:
    import subprocess  # only when a server has to be started

    directory = server_dir(spec["state_dir"])
    os.makedirs(directory, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "jarvis.hook_server",
        "--spec",
        spec_json(spec),
        "--idle-minutes",
        os.environ.get("JARVIS_HOOK_SERVER_IDLE_MINUTES", str(DEFAULT_IDLE_MINUTES)),
    ]
    with open(log_path(spec), "ab") as log:
        options: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": log,
            "stderr": log,
            "close_fds": True,
            "cwd": directory,
        }
        if sys.platform == "win32":
            # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
            options["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000
        else:
            options["start_new_session"] = True
        subprocess.Popen(command, **options)  # noqa: S603 (our own interpreter and module)


def _ensure_server(spec: dict[str, Any]) -> tuple[int, str]:
    """Credentials of a running server, starting one if needed. ``Unavailable`` if it cannot."""
    creds = _credentials(spec)
    if creds is not None:
        return creds
    try:
        if time.time() - os.stat(failed_path(spec)).st_mtime < FAILED_BACKOFF_SECONDS:
            raise Unavailable("the server failed to start recently")
    except OSError:
        pass
    lock = lock_path(spec)
    os.makedirs(server_dir(spec["state_dir"]), exist_ok=True)
    mine = False
    try:
        os.close(os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL))
        mine = True
    except FileExistsError:
        try:
            if time.time() - os.stat(lock).st_mtime > START_LOCK_STALE_SECONDS:
                _remove(lock)  # a starter died; the next call will take the lock
        except OSError:
            pass
    except OSError as exc:
        raise Unavailable(type(exc).__name__) from None
    try:
        if mine:
            try:
                _spawn(spec)
            except OSError as exc:
                raise Unavailable(f"cannot start the server ({type(exc).__name__})") from None
        deadline = time.monotonic() + START_WAIT_SECONDS
        while time.monotonic() < deadline:
            creds = _credentials(spec)
            if creds is not None:
                return creds
            time.sleep(0.05)
        if mine:
            try:
                with open(failed_path(spec), "w", encoding="utf-8") as handle:
                    handle.write("1")
            except OSError:
                pass
        raise Unavailable("the server did not come up in time")
    finally:
        if mine:
            _remove(lock)


def decide(spec: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """The server's answer for ``payload``, or ``Unavailable`` / ``Failed`` as documented above."""
    port, secret = _ensure_server(spec)
    body = json.dumps({"v": PROTOCOL, "payload": payload}).encode("utf-8")
    status, answer = _request(port, secret, "POST", "/hook", body)
    if status == 200:
        try:
            result = json.loads(answer)
            if isinstance(result, dict) and isinstance(result.get("exit_code"), int):
                return result
        except ValueError:
            pass
        raise Failed("the server's answer was not understood")
    if status in (400, 401, 403, 404, 411, 413, 415):
        # Rejected before anything was processed. A stale secret means a stale server: forget it.
        if status in (401, 403):
            _remove(discovery_path(spec), secret_path(spec))
        raise Unavailable(f"the server refused the request (HTTP {status})")
    raise Failed(f"the server answered HTTP {status}")


def _split(args: list[str]) -> tuple[dict[str, str | None], list[str]]:
    """The few options that decide the spec, and the arguments with ``--server`` removed."""
    found: dict[str, str | None] = dict.fromkeys(_OPTIONS.values())
    rest: list[str] = []
    index = 2  # skip "hook run"
    while index < len(args):
        arg = args[index]
        if arg == "--server":
            index += 1
            continue
        if arg in _OPTIONS and index + 1 < len(args):
            found[_OPTIONS[arg]] = args[index + 1]
        rest.append(arg)
        index += 1
    return found, rest


def _fall_back(args: list[str], text: str) -> int:
    """Decide in this process with the full hook, the same way the server would have."""
    import io

    from jarvis.hook_cli import main_hook

    sys.stdin = io.StringIO(text)
    return main_hook(["hook", "run", *_split(args)[1]])


def main_client(args: list[str]) -> int:
    """``agentctl hook run --server ...``: ask the server, and fall back or fail safely."""
    try:
        sys.stdin.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError, OSError):
        pass
    text = sys.stdin.read()
    options, _ = _split(args)
    try:
        payload = json.loads(text)
    except ValueError:
        return _fall_back(args, text)  # the full hook owns the "not valid JSON" policy
    if not isinstance(payload, dict):
        return _fall_back(args, text)
    cwd = payload.get("cwd")
    spec = build_spec(
        mode=options["mode"],
        config=options["config"],
        state_dir=options["state_dir"],
        policy_source=options["policy_source"],
        policy_ref=options["policy_ref"],
        policy_dir=options["policy_dir"],
        payload_cwd=cwd if isinstance(cwd, str) else None,
    )
    try:
        answer = decide(spec, payload)
    except Unavailable:
        return _fall_back(args, text)
    except Failed as exc:
        # A request may have been processed: never decide again. Fail the way the hook does.
        if spec["mode"] == "enforce":
            print(
                f"jarvis policy gate blocked this call: the hook server failed ({exc})",
                file=sys.stderr,
            )
            return 2
        print(
            f"jarvis hook (log-only): the hook server failed ({exc}); the call was allowed",
            file=sys.stderr,
        )
        return 0
    if answer.get("stdout"):
        print(answer["stdout"])
    if answer.get("stderr"):
        print(answer["stderr"], file=sys.stderr)
    return int(answer["exit_code"])
