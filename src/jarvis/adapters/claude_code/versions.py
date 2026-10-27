"""Claude Code version checks (ADR 0003).

``"onFailure": "block"`` makes a crashed, timed-out or missing hook block the tool call. It
needs Claude Code 2.1.295 or later. On older versions such a failure lets the action through,
so enforce mode is only safe where this is supported.
"""

from __future__ import annotations

import re

Version = tuple[int, int, int]

MIN_ON_FAILURE: Version = (2, 1, 295)
_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def parse_version(text: str) -> Version | None:
    match = _VERSION.search(text or "")
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def supports_on_failure(version: Version | None) -> bool:
    return version is not None and version >= MIN_ON_FAILURE


def format_version(version: Version | None) -> str:
    return "unknown" if version is None else ".".join(str(p) for p in version)
