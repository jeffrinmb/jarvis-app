"""Per-session exposure state: what untrusted content or private data a session has seen.

The policy gate can only tighten for untrusted content or the lethal trifecta if something
remembers that the session was exposed, and each hook call is a separate short-lived process.
State is kept in one small JSON file per session, **outside the project workspace**, and only
ever grows within a session. It is only as trustworthy as filesystem permissions until the
sandbox work (P4.2); the gate also treats the state directory as a no-go path.

If a state file is unreadable or corrupt the session is assumed to be fully exposed
(untrusted content and restricted data), so a damaged file makes the gate stricter, not looser.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from jarvis.core.filelock import file_lock
from jarvis.core.schemas import DataClass, TrustLabel
from jarvis.core.schemas.action import SessionState

from .mapping import Exposure, safe_session_id

_PESSIMISTIC = SessionState(
    trust_labels_seen=[TrustLabel.UNTRUSTED],
    data_classes_seen=[DataClass.RESTRICTED],
)


@dataclass(frozen=True)
class LoadedState:
    state: SessionState
    healthy: bool


def default_state_dir() -> Path:
    override = os.environ.get("JARVIS_STATE_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "jarvis" / "sessions"
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "jarvis" / "sessions"


class SessionStore:
    def __init__(self, state_dir: Path, lock_timeout: float = 3.0) -> None:
        self.state_dir = Path(state_dir)
        self.lock_timeout = lock_timeout

    def path_for(self, session_id: str) -> Path:
        return self.state_dir / f"{safe_session_id(session_id)}.json"

    def _read(self, path: Path) -> LoadedState:
        if not path.exists():
            return LoadedState(SessionState(), True)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            state = SessionState(
                trust_labels_seen=[TrustLabel(v) for v in data["trust_labels_seen"]],
                data_classes_seen=[DataClass(v) for v in data["data_classes_seen"]],
            )
            return LoadedState(state, True)
        except (OSError, ValueError, KeyError, TypeError):
            return LoadedState(_PESSIMISTIC, False)

    def load(self, session_id: str) -> LoadedState:
        return self._read(self.path_for(session_id))

    def update(self, session_id: str, exposure: Exposure) -> LoadedState:
        """Add the exposure to the session's state (union only) and return the new state."""
        path = self.path_for(session_id)
        with file_lock(path.with_suffix(".lock"), self.lock_timeout):
            current = self._read(path)
            trust = {*current.state.trust_labels_seen, *exposure.trust}
            data = {*current.state.data_classes_seen, *exposure.data}
            state = SessionState(
                trust_labels_seen=sorted(trust, key=lambda t: t.value),
                data_classes_seen=sorted(data, key=lambda d: d.value),
            )
            self._write(path, state)
            return LoadedState(state, current.healthy)

    def reset(self, session_id: str) -> None:
        path = self.path_for(session_id)
        with file_lock(path.with_suffix(".lock"), self.lock_timeout):
            self._write(path, SessionState())

    def _write(self, path: Path, state: SessionState) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "trust_labels_seen": [t.value for t in state.trust_labels_seen],
            "data_classes_seen": [d.value for d in state.data_classes_seen],
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
