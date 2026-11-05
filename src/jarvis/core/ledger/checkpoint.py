"""Create and check signed ledger checkpoints (task P2.9b, ADR 0010).

A checkpoint file is an append-only list of JSON lines, one ``Checkpoint`` each. It is kept
outside the ledger, by default in the per-user state directory, or wherever the person chooses
(a synced folder, or a file committed through a pull request for the strongest protection
against deletion).

Verification fails when a **signed** checkpoint contradicts the ledger: its signature does not
verify for a listed key, it belongs to another ledger, the ledger is shorter than it (truncated),
or the record it names has a different hash (history rewritten). A missing or stale checkpoint is
only a warning: nothing here can tell "never signed one" from "the file was deleted", which is the
limit of keeping it where an agent running as the same user can reach.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import ValidationError

from jarvis.core import signing
from jarvis.core.filelock import file_lock
from jarvis.core.schemas.checkpoint import Checkpoint
from jarvis.core.signing import NS_CHECKPOINT

from .ledger import Ledger

DEFAULT_MAX_AGE_DAYS = 7
MILESTONE_DIR = Path(".agents") / "checkpoints"


def canonical_payload(ledger_id: str, seq: int, hash_: str, created_at: datetime) -> bytes:
    return json.dumps(
        {
            "v": 1,
            "namespace": NS_CHECKPOINT,
            "ledger_id": ledger_id,
            "seq": seq,
            "hash": hash_,
            "created_at": created_at.astimezone(UTC).isoformat(),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def ledger_id(ledger: Ledger) -> str | None:
    """The hash of the first record, or ``None`` for an empty ledger."""
    for record in ledger.records():
        return record["hash"]
    return None


def default_checkpoint_path(state_dir: Path, ledger_identity: str) -> Path:
    return Path(state_dir) / "checkpoints" / f"{ledger_identity[:16]}.jsonl"


def milestone_checkpoint_path(project_dir: Path, ledger_identity: str) -> Path:
    """Where a checkpoint is kept inside the project, to be committed through a pull request."""
    return Path(project_dir) / MILESTONE_DIR / f"{ledger_identity[:16]}.jsonl"


def append_checkpoint(checkpoint: Checkpoint, checkpoint_file: Path) -> None:
    checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(checkpoint.model_dump(mode="json"), sort_keys=True) + "\n"
    with file_lock(checkpoint_file.with_name(checkpoint_file.name + ".lock"), timeout=5.0):
        with checkpoint_file.open("ab") as handle:
            handle.write(line.encode("utf-8"))


def read_checkpoint_files(*paths: Path | None) -> str:
    """The checkpoint lines of every file that exists, joined. A missing file adds nothing, and
    a line that appears in more than one file (the per-user copy and the committed one are the
    same signed line) counts once."""
    seen: set[str] = set()
    lines = []
    for path in paths:
        if path is not None and path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip() and line not in seen:
                    seen.add(line)
                    lines.append(line)
    return "".join(line + "\n" for line in lines)


def create_checkpoint(
    ledger: Ledger,
    key_path: str | Path,
    allowed_signers: str,
    checkpoint_file: Path | None = None,
    now: datetime | None = None,
    state_dir: Path | None = None,
    timeout: int = signing.SIGN_TIMEOUT_SECONDS,
    milestone_file: Path | None = None,
) -> tuple[Checkpoint, str, Path]:
    """Sign the ledger's current head and append it to the checkpoint file.

    With ``milestone_file`` the same signed line is also appended there (a file inside the
    project, committed through a pull request so a deletion shows in history).

    Returns ``(checkpoint, signer, path)``. Refuses a ledger that fails verification (a person
    must not sign a lie), an empty ledger, and a key that is not in the allowed-signers file.
    """
    result = ledger.verify()
    if not result.ok:
        raise ValueError(f"will not sign a ledger that fails verification: {result.error}")
    identity = ledger_id(ledger)
    if identity is None or result.head_seq is None:
        raise ValueError("the ledger is empty, so there is nothing to checkpoint")
    created = (now or datetime.now(UTC)).astimezone(UTC)
    payload = canonical_payload(identity, result.head_seq, result.head_hash, created)
    signature = signing.sign(payload, key_path, NS_CHECKPOINT, timeout)
    found = signing.verify(payload, signature, allowed_signers, NS_CHECKPOINT)
    if found is None:
        raise signing.SigningError("this key is not listed in the allowed-signers file")
    checkpoint = Checkpoint(
        ledger_id=identity,
        seq=result.head_seq,
        hash=result.head_hash,
        created_at=created,
        signature=signature,
    )
    if checkpoint_file is None:
        if state_dir is None:
            raise ValueError("no place to keep the checkpoint was given")
        checkpoint_file = default_checkpoint_path(state_dir, identity)
    append_checkpoint(checkpoint, checkpoint_file)
    if milestone_file is not None:
        append_checkpoint(checkpoint, milestone_file)
    return checkpoint, found.principal, checkpoint_file


@dataclass
class CheckpointReport:
    checked: int = 0
    good: int = 0
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    latest_seq: int | None = None
    latest_signer: str | None = None
    records_after_latest: int | None = None

    @property
    def ok(self) -> bool:
        return not self.failures


def check_checkpoints(
    ledger: Ledger,
    checkpoint_text: str,
    allowed_signers: str,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    now: datetime | None = None,
) -> CheckpointReport:
    """Compare signed checkpoints with the ledger. Failures are contradictions; absence and age
    are warnings."""
    report = CheckpointReport()
    now = (now or datetime.now(UTC)).astimezone(UTC)
    chain = ledger.verify()
    if not chain.ok:
        report.failures.append(
            f"the ledger fails verification: {chain.error} (seq {chain.error_seq})"
        )
        return report
    identity = ledger_id(ledger)
    by_seq = {r["seq"]: r["hash"] for r in ledger.records()}

    best: tuple[int, Checkpoint, str] | None = None
    for number, line in enumerate(checkpoint_text.splitlines(), start=1):
        if not line.strip():
            continue
        report.checked += 1
        try:
            checkpoint = Checkpoint.model_validate_json(line)
        except (ValidationError, ValueError):
            report.failures.append(f"checkpoint line {number} cannot be read")
            continue
        payload = canonical_payload(
            checkpoint.ledger_id, checkpoint.seq, checkpoint.hash, checkpoint.created_at
        )
        who = signing.verify(payload, checkpoint.signature, allowed_signers, NS_CHECKPOINT)
        if who is None:
            report.failures.append(
                f"checkpoint at seq {checkpoint.seq}: signature does not verify for a listed key"
            )
        elif checkpoint.ledger_id != identity:
            report.failures.append(
                f"checkpoint at seq {checkpoint.seq} (signed by {who.principal}) belongs to a "
                "different ledger, or this ledger was replaced"
            )
        elif checkpoint.seq not in by_seq:
            report.failures.append(
                f"checkpoint at seq {checkpoint.seq} (signed by {who.principal}): the ledger is "
                f"shorter ({chain.records} records), so it was truncated"
            )
        elif by_seq[checkpoint.seq] != checkpoint.hash:
            report.failures.append(
                f"checkpoint at seq {checkpoint.seq} (signed by {who.principal}): the record "
                "differs, so history was rewritten"
            )
        else:
            report.good += 1
            if best is None or checkpoint.seq > best[0]:
                best = (checkpoint.seq, checkpoint, who.principal)

    if best is not None:
        seq, checkpoint, signer = best
        report.latest_seq, report.latest_signer = seq, signer
        report.records_after_latest = (chain.head_seq or 0) - seq
        if now - checkpoint.created_at.astimezone(UTC) > timedelta(days=max_age_days):
            report.warnings.append(
                f"the newest checkpoint is older than {max_age_days} days; "
                f"{report.records_after_latest} records have been added since"
            )
    elif not report.failures:
        report.warnings.append(
            "no signed checkpoint found; truncation or a rewrite of the ledger would go unnoticed"
        )
    return report
