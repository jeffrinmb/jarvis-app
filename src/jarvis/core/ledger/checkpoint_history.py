"""Check that the signed checkpoints committed to a project only ever grow (task P2.9f, ADR 0010).

Checkpoints kept in the project (``agentctl checkpoint --milestone``) are committed through pull
requests so that deleting or rewriting one shows in git history. A person reading a diff can
miss a deleted line, so this check says it plainly: compared with the base commit, every
existing file must still be there, every existing line must be unchanged and in the same place,
and every *new* line must be a well-formed checkpoint whose signature verifies for a key that
the **base** commit's allowed-signers file lists (so a pull request cannot add its own key and
sign with it in one step).

It cannot tell whether a new checkpoint matches the real ledger: the ledger is not in git. That
is checked where the ledger is, by ``agentctl verify --checkpoints``.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import PurePosixPath

from pydantic import ValidationError

from jarvis.core import signing
from jarvis.core.schemas.checkpoint import Checkpoint
from jarvis.core.signing import NS_CHECKPOINT

from .checkpoint import canonical_payload

CHECKPOINT_DIR = ".agents/checkpoints"


def _lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip()]


def check_history(
    base: Mapping[str, str], head: Mapping[str, str], allowed_signers: str | None
) -> list[str]:
    """Why ``head`` is not an append-only continuation of ``base``; empty when it is.

    Both map a file name (such as ``99febcdb51cef1fb.jsonl``) to its text. ``allowed_signers`` is
    the text of the allowed-signers file as of the base commit, or ``None`` if it had none.
    """
    failures: list[str] = []
    for name in sorted(base):
        if name not in head:
            failures.append(f"{name}: the checkpoint file was removed")
    for name in sorted(head):
        if not name.endswith(".jsonl") or "/" in name:
            failures.append(f"{name}: not a checkpoint file (only <ledger id>.jsonl belongs here)")
            continue
        old = _lines(base.get(name, ""))
        new = _lines(head[name])
        if new[: len(old)] != old:
            failures.append(f"{name}: an existing checkpoint was changed, reordered or removed")
            continue
        for number, line in enumerate(new[len(old) :], start=len(old) + 1):
            failures.extend(_check_new_line(name, number, line, allowed_signers))
    return failures


def _check_new_line(name: str, number: int, line: str, allowed_signers: str | None) -> list[str]:
    where = f"{name} line {number}"
    try:
        checkpoint = Checkpoint.model_validate_json(line)
    except (ValidationError, ValueError):
        return [f"{where}: not a readable checkpoint"]
    if PurePosixPath(name).stem != checkpoint.ledger_id[:16]:
        return [f"{where}: the checkpoint belongs to another ledger than the file name says"]
    if not allowed_signers or not allowed_signers.strip():
        return [f"{where}: the base commit has no allowed-signers file, so it cannot be verified"]
    payload = canonical_payload(
        checkpoint.ledger_id, checkpoint.seq, checkpoint.hash, checkpoint.created_at
    )
    if signing.verify(payload, checkpoint.signature, allowed_signers, NS_CHECKPOINT) is None:
        return [f"{where}: the signature does not verify for a key listed at the base commit"]
    return []
