"""Hash-chained JSONL ledger.

Each line is one record::

    {"seq": 0, "prev_hash": "<64 hex>", "event": {...}, "hash": "<64 hex>"}

``hash`` is SHA-256 over the canonical JSON of ``{seq, prev_hash, event}``, and each
record's ``prev_hash`` is the previous record's ``hash`` (the first uses ``GENESIS_HASH``).
Editing, deleting or reordering a record breaks the chain. Truncating the tail or
rewriting the whole chain consistently cannot be seen from the file alone, so callers
should record an anchor (``seq:hash`` from ``head()``) somewhere else, such as Git, and
pass it to ``verify(anchor=...)``.

Files are always opened in binary mode so hashes do not depend on platform newlines.
Only one writer appends at a time, guarded by a lock file.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from jarvis.core.schemas import Event

GENESIS_HASH = "0" * 64
_RECORD_KEYS = {"seq", "prev_hash", "event", "hash"}


class LedgerError(Exception):
    """Base class for ledger problems."""


class LedgerCorruptError(LedgerError):
    """The ledger fails verification; refusing to extend it."""


class LedgerLockError(LedgerError):
    """Could not take the writer lock in time."""


def canonical_json(obj: Any) -> bytes:
    """Deterministic JSON bytes: sorted keys, no whitespace, UTF-8."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def compute_hash(seq: int, prev_hash: str, event: dict[str, Any]) -> str:
    body = {"seq": seq, "prev_hash": prev_hash, "event": event}
    return hashlib.sha256(canonical_json(body)).hexdigest()


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    records: int
    head_seq: int | None
    head_hash: str
    error: str | None = None
    error_seq: int | None = None


def _fail(count: int, head_seq: int | None, head_hash: str, seq: int, msg: str) -> VerifyResult:
    return VerifyResult(False, count, head_seq, head_hash, error=msg, error_seq=seq)


class Ledger:
    def __init__(self, path: str | Path, lock_timeout: float = 5.0) -> None:
        self.path = Path(path)
        self.lock_timeout = lock_timeout

    # ---------- writing ----------

    def append(self, event: Event) -> dict[str, Any]:
        """Validate the tail, then append ``event``. Returns the stored record."""
        payload = event.model_dump(mode="json")
        with self._lock():
            last = self._last_record()
            seq = 0 if last is None else last["seq"] + 1
            prev = GENESIS_HASH if last is None else last["hash"]
            record = {
                "seq": seq,
                "prev_hash": prev,
                "event": payload,
                "hash": compute_hash(seq, prev, payload),
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "ab") as fh:
                fh.write(canonical_json(record) + b"\n")
                fh.flush()
                os.fsync(fh.fileno())
        return record

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_name(self.path.name + ".lock")
        deadline = time.monotonic() + self.lock_timeout
        while True:
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except (FileExistsError, PermissionError) as exc:
                # On Windows, creating a file that another writer has just deleted (still
                # "delete pending") raises PermissionError instead of FileExistsError.
                if time.monotonic() > deadline:
                    raise LedgerLockError(
                        f"could not lock {lock_path} within {self.lock_timeout}s "
                        f"({type(exc).__name__}); if no writer is running the lock is "
                        "stale and can be deleted"
                    ) from None
                time.sleep(0.02)
        try:
            os.write(fd, str(os.getpid()).encode())
            yield
        finally:
            os.close(fd)
            self._remove_lock(lock_path)

    @staticmethod
    def _remove_lock(lock_path: Path) -> None:
        for attempt in range(50):
            try:
                os.unlink(lock_path)
                return
            except FileNotFoundError:
                return
            except PermissionError:
                # Windows: a virus scanner or another handle can briefly block deletion.
                if attempt == 49:
                    raise
                time.sleep(0.02)

    def _last_record(self) -> dict[str, Any] | None:
        """Read and check only the final record, so appends stay cheap."""
        if not self.path.exists() or self.path.stat().st_size == 0:
            return None
        with open(self.path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(size - 1)
            if fh.read(1) != b"\n":
                raise LedgerCorruptError("ledger does not end with a newline (partial write?)")
            # Walk backwards in blocks to the start of the final line.
            pos = size - 1
            block = 4096
            chunk = b""
            while pos > 0:
                step = min(block, pos)
                pos -= step
                fh.seek(pos)
                chunk = fh.read(step) + chunk
                idx = chunk[:-1].rfind(b"\n")
                if idx != -1:
                    chunk = chunk[idx + 1 :]
                    break
            line = chunk.rstrip(b"\n")
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise LedgerCorruptError(f"final record is not valid JSON: {exc}") from exc
        if (
            not isinstance(record, dict)
            or set(record) != _RECORD_KEYS
            or not isinstance(record["seq"], int)
            or record["hash"] != compute_hash(record["seq"], record["prev_hash"], record["event"])
        ):
            raise LedgerCorruptError("final record fails its hash check; run `agentctl verify`")
        return record

    # ---------- reading ----------

    def records(self) -> Iterator[dict[str, Any]]:
        """Yield stored records without verifying them."""
        if not self.path.exists():
            return
        with open(self.path, "rb") as fh:
            for raw in fh:
                yield json.loads(raw)

    def head(self) -> tuple[int, str] | None:
        """Return ``(seq, hash)`` of the last record, or ``None`` when empty.

        This does a full verification first so an anchor is never taken from a broken chain.
        """
        result = self.verify()
        if not result.ok:
            raise LedgerCorruptError(f"cannot take head: {result.error} (seq {result.error_seq})")
        if result.head_seq is None:
            return None
        return result.head_seq, result.head_hash

    def verify(self, anchor: tuple[int, str] | None = None) -> VerifyResult:
        """Check every record, and optionally that ``anchor`` is still part of the chain."""
        prev = GENESIS_HASH
        count = 0
        head_seq: int | None = None
        anchor_found = anchor is None

        data = self.path.read_bytes() if self.path.exists() else b""
        lines = data.split(b"\n")
        trailing = lines.pop()  # empty when the file ends with a newline
        if trailing:
            return _fail(0, None, GENESIS_HASH, len(lines), "partial final line (no newline)")

        for expected_seq, raw in enumerate(lines):
            where = (count, head_seq, prev)
            try:
                record = json.loads(raw)
            except json.JSONDecodeError as exc:
                return _fail(*where, expected_seq, f"not valid JSON: {exc}")
            if not isinstance(record, dict) or set(record) != _RECORD_KEYS:
                return _fail(*where, expected_seq, "record has unexpected fields")
            if record["seq"] != expected_seq:
                return _fail(
                    *where,
                    expected_seq,
                    f"sequence gap or reorder: expected {expected_seq}, found {record['seq']}",
                )
            if record["prev_hash"] != prev:
                return _fail(*where, expected_seq, "prev_hash does not match previous record")
            if record["hash"] != compute_hash(expected_seq, record["prev_hash"], record["event"]):
                return _fail(*where, expected_seq, "hash mismatch: record was modified")
            try:
                Event.model_validate(record["event"])
            except ValidationError as exc:
                return _fail(
                    *where, expected_seq, f"event fails schema: {exc.error_count()} errors"
                )
            prev = record["hash"]
            head_seq = expected_seq
            count += 1
            if anchor is not None and expected_seq == anchor[0]:
                if record["hash"] != anchor[1]:
                    return _fail(
                        count,
                        head_seq,
                        prev,
                        expected_seq,
                        "anchor mismatch: history was rewritten",
                    )
                anchor_found = True

        if not anchor_found:
            return _fail(
                count,
                head_seq,
                prev,
                anchor[0] if anchor else 0,
                "anchor not found: ledger is shorter than the anchor (truncated?)",
            )
        return VerifyResult(True, count, head_seq, prev)
