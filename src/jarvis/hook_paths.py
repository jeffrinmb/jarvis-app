"""Paths, keys and the "spec" shared by the hook, its server and its client (task P2.6f, ADR 0012).

**Standard library only, and very few modules, on purpose.** The hook client runs before every
tool call and must start fast, so this module imports only ``json``, ``os``, ``sys`` and ``zlib``
at run time (no ``pathlib``, ``hashlib`` or ``typing``: together they cost tens of milliseconds
that every call would pay). Paths are plain strings here.

A *spec* is everything that decides how a hook call is judged: the project directory, the mode,
where the policy is read from, the state directory, who is accountable, and a fingerprint of the
code. Two calls with the same spec are judged the same way, so they may share one server; a call
with a different spec gets its own. Because the code fingerprint is part of it, editing the code
starts a new server instead of silently using an old one.

The key that names a spec's files is a cheap checksum, **not** a security boundary: the server
stores the full spec in its discovery file and the client compares it exactly before trusting the
server, so a key collision can never make two different specs share a server.
"""

from __future__ import annotations

import json
import os
import sys
import zlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Any

PROTOCOL = 1
DEFAULT_IDLE_MINUTES = 30.0


def default_state_dir(environ: Mapping[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    override = env.get("JARVIS_STATE_DIR")
    if override:
        return override
    home = os.path.expanduser("~")
    if sys.platform == "win32":
        base = env.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
        return os.path.join(base, "jarvis", "sessions")
    base = env.get("XDG_STATE_HOME") or os.path.join(home, ".local", "state")
    return os.path.join(base, "jarvis", "sessions")


def code_fingerprint() -> str:
    """A short checksum of the names, sizes and times of this package's source files."""
    root = os.path.dirname(os.path.abspath(__file__))
    value = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for name in sorted(filenames):
            if name.endswith(".py"):
                path = os.path.join(dirpath, name)
                info = os.stat(path)
                text = f"{os.path.relpath(path, root)}|{info.st_mtime_ns}|{info.st_size}\n"
                value = zlib.crc32(text.encode("utf-8"), value)
    return f"{value:08x}"


def build_spec(
    *,
    mode: str | None,
    config: str | None,
    state_dir: str | None,
    policy_source: str | None,
    policy_ref: str | None,
    policy_dir: str | None,
    payload_cwd: str | None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Resolve command-line options and the environment into one spec (see the module doc)."""
    env = os.environ if environ is None else environ
    project = env.get("CLAUDE_PROJECT_DIR") or payload_cwd or os.getcwd()
    return {
        "v": PROTOCOL,
        "project_dir": os.path.abspath(project),
        "mode": mode or env.get("JARVIS_HOOK_MODE"),
        "config": config,
        "state_dir": os.path.abspath(state_dir) if state_dir else default_state_dir(env),
        "policy_source": policy_source or env.get("JARVIS_POLICY_SOURCE"),
        "policy_ref": policy_ref or env.get("JARVIS_POLICY_REF"),
        "policy_dir": policy_dir or env.get("JARVIS_POLICY_DIR"),
        "on_behalf_of": env.get("JARVIS_ON_BEHALF_OF"),
        "code": code_fingerprint(),
    }


def spec_json(spec: Mapping[str, Any]) -> str:
    return json.dumps(spec, sort_keys=True, separators=(",", ":"))


def spec_key(spec: Mapping[str, Any]) -> str:
    """A 16-hex name for a spec's files. A checksum, not a security boundary (see above)."""
    data = spec_json(spec).encode("utf-8")
    return f"{zlib.crc32(data):08x}{zlib.crc32(data, 0x9E3779B9):08x}"


def server_dir(state_dir: str) -> str:
    return os.path.join(state_dir, "server")


def _file(spec: Mapping[str, Any], suffix: str) -> str:
    return os.path.join(server_dir(spec["state_dir"]), f"{spec_key(spec)}.{suffix}")


def discovery_path(spec: Mapping[str, Any]) -> str:
    return _file(spec, "json")


def secret_path(spec: Mapping[str, Any]) -> str:
    return _file(spec, "secret")


def log_path(spec: Mapping[str, Any]) -> str:
    return _file(spec, "log")


def lock_path(spec: Mapping[str, Any]) -> str:
    return _file(spec, "start")


def failed_path(spec: Mapping[str, Any]) -> str:
    return _file(spec, "failed")
