"""Trace and span ids shared by the hook and the exporter (task P2.3a, ADR 0011).

Ids are derived from values already in a hook payload, so every writer that sees the same
session computes the same trace id and nothing has to be stored between hook calls. They are
labels for joining records, not secrets and not proof of anything: an agent that could write the
ledger could write any id.
"""

from __future__ import annotations

import hashlib


def _hex(text: str, length: int) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def trace_id_for(key: str) -> str:
    """The 32-hex trace id of everything that happened in one session (or task)."""
    return _hex(f"jarvis-trace:{key}", 32)


def agent_span_id_for(key: str) -> str:
    """The 16-hex id of the ``invoke_agent`` span that is the root of that trace."""
    return _hex(f"jarvis-agent:{key}", 16)


def span_id_for(*parts: str) -> str:
    """A 16-hex span id from the parts that name one operation, such as the session id and the
    tool use id. The gate's decision and the tool's outcome pass the same parts, so they share
    one span."""
    return _hex("jarvis-span:" + "\x1f".join(parts), 16)
