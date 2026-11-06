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

**Segments (task P2.1b, ADR 0014).** A long ledger is slow to read, so it can be *rotated*: the
active file is moved whole into ``<name>.archive/`` (named for the sequence numbers it holds) and
a new active file starts with a ``ledger_rotated`` record that continues the same chain (the next
sequence number and the last record's hash) and pins the archived file's SHA-256. The chain is
therefore one chain: ``records()``, ``verify()`` and ``head()`` read every segment in order, and
``active_records()`` reads only the file being written. Nothing is ever deleted or rewritten.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from jarvis.core.filelock import LockTimeoutError, file_lock
from jarvis.core.schemas import Event, EventKind
from jarvis.core.security.redact import redact_value

from .summary import LedgerSummary, as_version_1, parse_moment

GENESIS_HASH = "0" * 64
_RECORD_KEYS = {"seq", "prev_hash", "event", "hash"}
_SEGMENT_NAME = re.compile(r"^(\d{10})-(\d{10})\.jsonl$")
ROTATION_AGENT = "jarvis-ledger"
_LINK_FIELDS = (
    "previous_segment",
    "previous_sha256",
    "previous_records",
    "previous_first_seq",
    "previous_last_seq",
    "previous_last_hash",
)


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


@dataclass(frozen=True)
class Segment:
    """An archived part of the ledger: the file and the sequence numbers its name claims."""

    path: Path
    first_seq: int
    last_seq: int

    @property
    def records(self) -> int:
        return self.last_seq - self.first_seq + 1


@dataclass(frozen=True)
class GateView:
    """The history the policy gate reads for one decision: ``records`` and, when they are only
    the active file, ``base``, the summary of everything before them. ``base`` is ``None`` when
    ``records`` is the whole ledger."""

    base: LedgerSummary | None
    records: list[dict[str, Any]]


def _read_records(path: Path) -> Iterator[dict[str, Any]]:
    with open(path, "rb") as fh:
        for raw in fh:
            yield json.loads(raw)


class LedgerTransaction:
    """Handle valid only inside ``Ledger.transaction()``; the writer lock is already held."""

    def __init__(self, ledger: Ledger) -> None:
        self._ledger = ledger

    def records(self) -> Iterator[dict[str, Any]]:
        return self._ledger.records()

    def records_containing(self, needle: bytes) -> Iterator[dict[str, Any]]:
        return self._ledger.records_containing(needle)

    def gate_view(self) -> GateView:
        return self._ledger._gate_view()

    def append(self, event: Event) -> dict[str, Any]:
        return self._ledger._append_locked(event.model_dump(mode="json"))


class Ledger:
    def __init__(
        self, path: str | Path, lock_timeout: float = 5.0, rotate_bytes: int | None = None
    ) -> None:
        self.path = Path(path)
        self.lock_timeout = lock_timeout
        # When set, a write that finds the active file at least this big rotates it first.
        self.rotate_bytes = rotate_bytes

    # ---------- writing ----------

    def append(self, event: Event) -> dict[str, Any]:
        """Validate the tail, then append ``event``. Returns the stored record."""
        with self._lock():
            return self._append_locked(event.model_dump(mode="json"))

    @contextmanager
    def transaction(self) -> Iterator[LedgerTransaction]:
        """Hold the writer lock across a read-decide-append sequence.

        Used by the policy gate so that cumulative limits (for example total diff lines per
        task) cannot be exceeded by concurrent checks that all read the same history.
        """
        with self._lock():
            yield LedgerTransaction(self)

    def _append_locked(self, payload: dict[str, Any]) -> dict[str, Any]:
        # Every write passes through here, so this is where secrets and personal data in the
        # free-form detail are replaced (task P2.12, threat model T-5). The hash covers what is
        # stored, so a verified ledger never holds the redacted text.
        if "data" in payload:
            payload = {**payload, "data": redact_value(payload["data"])}
        if (
            self.rotate_bytes is not None
            and self.path.exists()
            and self.path.stat().st_size >= self.rotate_bytes
        ):
            try:
                self._rotate_locked()
            except OSError:
                # Rotation only keeps the ledger small. If another process has the file open
                # (Windows refuses to rename it) the write must still go through; the next
                # write tries again. A move that did succeed is finished just below.
                pass
        self._ensure_link_locked()
        return self._write_locked(payload)

    def _write_locked(self, payload: dict[str, Any]) -> dict[str, Any]:
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

    # ---------- segments ----------

    @property
    def archive_dir(self) -> Path:
        return self.path.with_name(self.path.stem + ".archive")

    def segments(self) -> list[Segment]:
        """The archived segments, oldest first. Files whose names do not fit are not listed here
        (``verify`` reports them)."""
        if not self.archive_dir.is_dir():
            return []
        found = []
        for entry in self.archive_dir.iterdir():
            named = _SEGMENT_NAME.match(entry.name)
            if named and entry.is_file():
                found.append(Segment(entry, int(named[1]), int(named[2])))
        return sorted(found, key=lambda s: s.first_seq)

    def rotate(self) -> Segment | None:
        """Move the active file into the archive and start a new one that continues the chain.

        Returns the new archived segment, or ``None`` when there was nothing to rotate. Safe to
        call at any time; it takes the writer lock.
        """
        with self._lock():
            return self._rotate_locked()

    def _rotate_locked(self) -> Segment | None:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return None
        first = self._first_record(self.path)
        last = self._tail_record(self.path)
        if first is None or last is None:
            return None
        name = f"{first['seq']:010d}-{last['seq']:010d}.jsonl"
        destination = self.archive_dir / name
        if destination.exists():
            raise LedgerCorruptError(
                f"archive segment {name} already exists; refusing to replace it"
            )
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        os.replace(self.path, destination)  # atomic on one volume
        segment = Segment(destination, first["seq"], last["seq"])
        # A crash here leaves no active file; the next write links it (see _ensure_link_locked).
        self._ensure_link_locked()
        return segment

    def _ensure_link_locked(self) -> None:
        """When the active file is missing or empty but segments exist, write the record that
        continues the chain and pins the newest segment."""
        if self.path.exists() and self.path.stat().st_size > 0:
            return
        segments = self.segments()
        if not segments:
            return
        newest = segments[-1]
        last = self._tail_record(newest.path)
        if last is None:
            raise LedgerCorruptError(f"archive segment {newest.path.name} is empty")
        event = Event(
            kind=EventKind.LEDGER_ROTATED,
            agent_id=ROTATION_AGENT,
            on_behalf_of="system",
            outcome="rotated",
            ts=datetime.now(UTC),
            data={
                "previous_segment": newest.path.name,
                "previous_sha256": hashlib.sha256(newest.path.read_bytes()).hexdigest(),
                "previous_records": newest.records,
                "previous_first_seq": newest.first_seq,
                "previous_last_seq": last["seq"],
                "previous_last_hash": last["hash"],
            },
        )
        payload = event.model_dump(mode="json")
        # What the gate needs from everything before this record, so it can read only what
        # follows (ADR 0014). It is written unredacted on purpose: it is derived from records
        # that were already redacted, and verify recomputes it exactly.
        stored_at = parse_moment(payload["ts"])
        payload["data"]["summary"] = self._summary_through(newest).to_data(stored_at)
        self._write_locked(payload)

    def _summary_through(self, upto: Segment) -> LedgerSummary:
        """The summary of every record up to the end of the segment ``upto``. It starts from the
        newest link record that already carries one, so a rotation folds one segment, not all."""
        segments = [s for s in self.segments() if s.first_seq <= upto.first_seq]
        start, summary = 0, LedgerSummary()
        for index in range(len(segments) - 1, -1, -1):
            first = self._first_record(segments[index].path)
            event = (first or {}).get("event") or {}
            if event.get("kind") == EventKind.LEDGER_ROTATED.value:
                loaded = LedgerSummary.from_data((event.get("data") or {}).get("summary"))
                if loaded is not None:
                    start, summary = index, loaded
                    break
        for segment in segments[start:]:
            summary.fold(_read_records(segment.path))
        return summary

    def _gate_view(self) -> GateView:
        if not self.segments():
            return GateView(None, list(self.active_records()))  # never rotated: this is all of it
        active = list(self.active_records())
        if active:
            event = active[0]["event"]
            if event.get("kind") == EventKind.LEDGER_ROTATED.value:
                base = LedgerSummary.from_data((event.get("data") or {}).get("summary"), keep=None)
                if base is not None:
                    return GateView(base, active)
        return GateView(None, list(self.records()))  # no usable summary: read everything

    @contextmanager
    def _lock(self) -> Iterator[None]:
        lock_path = self.path.with_name(self.path.name + ".lock")
        try:
            with file_lock(lock_path, self.lock_timeout):
                yield
        except LockTimeoutError as exc:
            raise LedgerLockError(str(exc)) from None

    def _last_record(self) -> dict[str, Any] | None:
        """The final record of the whole ledger, so appends stay cheap. When the active file is
        missing or empty (a crash during rotation) it is the newest segment's last record."""
        if self.path.exists() and self.path.stat().st_size > 0:
            return self._tail_record(self.path)
        segments = self.segments()
        return self._tail_record(segments[-1].path) if segments else None

    @staticmethod
    def _first_record(path: Path) -> dict[str, Any] | None:
        with open(path, "rb") as fh:
            line = fh.readline()
        if not line:
            return None
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise LedgerCorruptError(f"first record is not valid JSON: {exc}") from exc
        if not isinstance(record, dict) or not isinstance(record.get("seq"), int):
            raise LedgerCorruptError("first record has no sequence number")
        return record

    def _tail_record(self, path: Path) -> dict[str, Any] | None:
        """Read and check only the final record of ``path``."""
        if not path.exists() or path.stat().st_size == 0:
            return None
        with open(path, "rb") as fh:
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
        """Yield every stored record, archived segments first, without verifying them."""
        for segment in self.segments():
            yield from _read_records(segment.path)
        yield from self.active_records()

    def records_containing(self, needle: bytes) -> Iterator[dict[str, Any]]:
        """Yield the records whose stored line contains ``needle``, across every segment. A cheap
        filter before parsing; the caller still checks the fields it cares about."""
        paths = [s.path for s in self.segments()]
        if self.path.exists():
            paths.append(self.path)
        for path in paths:
            with open(path, "rb") as fh:
                for raw in fh:
                    if needle in raw:
                        yield json.loads(raw)

    def active_records(self) -> Iterator[dict[str, Any]]:
        """Yield only the records of the file being written (not the archive)."""
        if self.path.exists():
            yield from _read_records(self.path)

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
        """Check every record of every segment as one chain, and optionally that ``anchor`` is
        still part of it."""
        state = _Walk(anchor)
        for entry in self._unexpected_archive_files():
            return _fail(0, None, GENESIS_HASH, 0, f"unexpected file in the archive: {entry}")
        files: list[tuple[Path, Segment | None]] = [(s.path, s) for s in self.segments()]
        if self.path.exists():
            files.append((self.path, None))
        previous: tuple[Segment, str] | None = None
        for path, segment in files:
            data = path.read_bytes()
            lines = data.split(b"\n")
            trailing = lines.pop()  # empty when the file ends with a newline
            if trailing:
                where = (state.count, state.head_seq, state.prev)
                text = (
                    "partial final line (no newline)"
                    if segment is None
                    else f"partial final line in {path.name} (no newline)"
                )
                return _fail(*where, len(lines), text)
            if segment is not None and (
                len(lines) != segment.records or state.expected != segment.first_seq
            ):
                return _fail(
                    state.count,
                    state.head_seq,
                    state.prev,
                    state.expected,
                    f"archive {path.name} does not hold the records it is named for",
                )
            failure = state.walk(lines, previous)
            if failure is not None:
                return failure
            if segment is not None:
                previous = (segment, hashlib.sha256(data).hexdigest())
        if not state.anchor_found:
            return _fail(
                state.count,
                state.head_seq,
                state.prev,
                state.anchor[0] if state.anchor else 0,
                "anchor not found: ledger is shorter than the anchor (truncated?)",
            )
        return VerifyResult(True, state.count, state.head_seq, state.prev)

    def _unexpected_archive_files(self) -> list[str]:
        if not self.archive_dir.is_dir():
            return []
        return sorted(
            e.name
            for e in self.archive_dir.iterdir()
            if not (_SEGMENT_NAME.match(e.name) and e.is_file())
        )


class _Walk:
    """The running state of a verification, carried across segments."""

    def __init__(self, anchor: tuple[int, str] | None) -> None:
        self.anchor = anchor
        self.anchor_found = anchor is None
        self.prev = GENESIS_HASH
        self.count = 0
        self.head_seq: int | None = None
        self.running = LedgerSummary()

    @property
    def expected(self) -> int:
        return self.count

    def walk(self, lines: list[bytes], previous: tuple[Segment, str] | None) -> VerifyResult | None:
        """Check ``lines`` (one file) and move on. If this file does not start the ledger, its
        first record must be the link to ``previous``."""
        for position, raw in enumerate(lines):
            expected_seq = self.count
            where = (self.count, self.head_seq, self.prev)
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
            if record["prev_hash"] != self.prev:
                return _fail(*where, expected_seq, "prev_hash does not match previous record")
            if record["hash"] != compute_hash(expected_seq, record["prev_hash"], record["event"]):
                return _fail(*where, expected_seq, "hash mismatch: record was modified")
            try:
                Event.model_validate(record["event"])
            except ValidationError as exc:
                return _fail(
                    *where, expected_seq, f"event fails schema: {exc.error_count()} errors"
                )
            if position == 0 and expected_seq > 0:
                problem = _link_problem(record["event"], previous, self.prev, expected_seq)
                if problem is not None:
                    return _fail(*where, expected_seq, problem)
                if "summary" in (record["event"].get("data") or {}):
                    expected = self.running.to_data(
                        parse_moment(record["event"]["ts"]) or datetime.now(UTC)
                    )
                    stored = record["event"]["data"]["summary"]
                    if isinstance(stored, dict) and stored.get("v") == 1:
                        shown = as_version_1(expected)  # written before hosts and volumes existed
                    else:
                        shown = expected
                    if stored != shown:
                        return _fail(
                            *where,
                            expected_seq,
                            "the summary in the link record does not match the records before it",
                        )
                    self.running = LedgerSummary.from_data(expected) or LedgerSummary()
            self.running.fold([record])
            self.prev = record["hash"]
            self.head_seq = expected_seq
            self.count += 1
            if self.anchor is not None and expected_seq == self.anchor[0]:
                if record["hash"] != self.anchor[1]:
                    return _fail(
                        self.count,
                        self.head_seq,
                        self.prev,
                        expected_seq,
                        "anchor mismatch: history was rewritten",
                    )
                self.anchor_found = True
        return None


def _link_problem(
    event: dict[str, Any],
    previous: tuple[Segment, str] | None,
    prev_hash: str,
    seq: int,
) -> str | None:
    """Why the first record of a later segment is not a valid link to the segment before it."""
    if previous is None:
        return "a segment starts without the segment before it (archive missing?)"
    segment, sha = previous
    data = event.get("data") or {}
    missing = any(f not in data for f in _LINK_FIELDS)
    if event.get("kind") != EventKind.LEDGER_ROTATED.value or missing:
        return "a new segment does not start with a ledger_rotated record"
    if data["previous_segment"] != segment.path.name or data["previous_sha256"] != sha:
        return f"the archived segment {segment.path.name} is not the one the ledger recorded"
    if data["previous_last_seq"] != seq - 1 or data["previous_last_hash"] != prev_hash:
        return "the link record does not match the end of the previous segment"
    return None
