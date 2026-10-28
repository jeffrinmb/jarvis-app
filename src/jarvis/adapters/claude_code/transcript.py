"""Read model-call token usage from a Claude Code session transcript (task P2.2a, ADR 0008).

**The transcript format is not a documented interface.** Claude Code's hooks reference
describes ``transcript_path`` only as a path, says the file is written asynchronously and may
lag the current turn, and says nothing about its content. The shape read here was observed in
real transcripts (a JSONL file with one object per line, assistant records carrying
``message.id``, ``message.model`` and ``message.usage``) and is confirmed only by third-party
descriptions. So this reader is deliberately defensive:

* it reads only numeric counts, a model name and a message id, never message content;
* it ignores anything that does not look exactly right instead of guessing;
* one API response is written as several records that repeat the same usage (observed: about
  three records per message), so records are **de-duplicated by message id**;
* a count that is not a plausible integer drops that record rather than inventing a number.

When Claude Code changes the format the result is fewer rows, not wrong rows, and the
``skipped`` counters say so.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_LINE_BYTES = 8 * 1024 * 1024
MAX_COUNT = 10**9  # larger than any real request; a bigger number is treated as corrupt

_MESSAGE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,100}$")
_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,99}$")


@dataclass(frozen=True)
class MessageUsage:
    message_id: str
    model: str
    input_tokens: int  # not served from a cache, and not cache writes
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    ts: datetime | None
    sidechain: bool


@dataclass(frozen=True)
class TranscriptUsage:
    messages: list[MessageUsage]
    skipped_lines: int  # not JSON, or too long
    skipped_records: int  # assistant records whose usage, id or model could not be trusted


def _count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= MAX_COUNT else None


def _moment(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment.astimezone(UTC) if moment.tzinfo else None


def _record_usage(obj: dict[str, Any]) -> MessageUsage | None:
    """The usage in one transcript record, or ``None`` when it cannot be trusted."""
    message = obj.get("message")
    if not isinstance(message, dict):
        return None
    usage = message.get("usage")
    message_id, model = message.get("id"), message.get("model")
    if not isinstance(usage, dict) or not isinstance(message_id, str):
        return None
    if not isinstance(model, str) or not _MODEL.match(model) or not _MESSAGE_ID.match(message_id):
        return None
    counts = [
        _count(usage.get("input_tokens")),
        _count(usage.get("output_tokens")),
        _count(usage.get("cache_read_input_tokens", 0)),
        _count(usage.get("cache_creation_input_tokens", 0)),
    ]
    if any(c is None for c in counts):
        return None
    return MessageUsage(
        message_id=message_id,
        model=model,
        input_tokens=counts[0],
        output_tokens=counts[1],
        cache_read_tokens=counts[2],
        cache_creation_tokens=counts[3],
        ts=_moment(obj.get("timestamp")),
        sidechain=obj.get("isSidechain") is True,
    )


def read_transcript_usage(path: str | Path) -> TranscriptUsage:
    """Per-message usage from ``path``, one entry per message id.

    Raises ``ValueError`` for a path that is not a reasonable transcript (wrong extension,
    not a regular file, too large) and ``OSError`` if it cannot be read.
    """
    file = Path(path)
    if file.suffix != ".jsonl":
        raise ValueError("transcript path must end in .jsonl")
    if not file.is_file():
        raise ValueError("transcript path is not a regular file")
    if file.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("transcript is too large to read")

    best: dict[str, MessageUsage] = {}
    order: list[str] = []
    skipped_lines = skipped_records = 0
    with file.open("rb") as handle:
        for raw in handle:
            if len(raw) > MAX_LINE_BYTES:
                skipped_lines += 1
                continue
            if b'"usage"' not in raw:  # cheap filter: most records carry no usage
                continue
            try:
                obj = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                skipped_lines += 1
                continue
            if not isinstance(obj, dict) or obj.get("type") != "assistant":
                continue
            found = _record_usage(obj)
            if found is None:
                skipped_records += 1
                continue
            known = best.get(found.message_id)
            if known is None:
                order.append(found.message_id)
            # Streaming can leave an early, partial record: keep the most complete one.
            if known is None or found.output_tokens >= known.output_tokens:
                best[found.message_id] = found
    return TranscriptUsage([best[i] for i in order], skipped_lines, skipped_records)
