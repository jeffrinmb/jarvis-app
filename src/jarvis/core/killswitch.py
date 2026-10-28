"""The kill switch: one file that stops every action the gate is asked about.

The switch lives in the per-user state directory, outside the project the agent works in
(and a no-go path for the gate), so setting it is a human act and clearing it is too. While
the file exists the gate answers ``deny`` for everything, reads included.

It fails closed: if the file cannot be inspected for any reason other than not existing, the
switch counts as on.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

HALT_FILE = "HALT"


def halt_path(state_dir: Path) -> Path:
    return Path(state_dir) / HALT_FILE


@dataclass(frozen=True)
class Halt:
    """Why the switch is on. Never holds free text from an agent."""

    since: str | None = None
    by: str | None = None

    def describe(self) -> str:
        parts = ["the kill switch is on"]
        if self.since:
            parts.append(f"since {self.since}")
        if self.by:
            parts.append(f"set by {self.by}")
        return " ".join(parts) + "; a human must run `agentctl resume`"


def read_halt(path: Path) -> Halt | None:
    """``None`` when the switch is off; a ``Halt`` when it is on or cannot be checked."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError):
        return Halt()
    try:
        data = json.loads(text)
        since, by = data.get("since"), data.get("by")
        return Halt(str(since)[:40] if since else None, str(by)[:80] if by else None)
    except (ValueError, AttributeError):
        return Halt()  # present but unreadable still halts


def set_halt(path: Path, by: str | None = None) -> Halt:
    who = by or os.environ.get("JARVIS_ON_BEHALF_OF") or _os_user()
    halt = Halt(datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), who)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"since": halt.since, "by": halt.by}), encoding="utf-8")
    return halt


def clear_halt(path: Path) -> bool:
    """Turn the switch off. Returns whether it was on."""
    try:
        Path(path).unlink()
    except FileNotFoundError:
        return False
    return True


def _os_user() -> str:
    try:
        return os.getlogin()
    except OSError:
        return os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"
