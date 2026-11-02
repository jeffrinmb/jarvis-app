"""A signed ledger checkpoint: "at record ``seq`` the hash was ``hash``" (task P2.9b, ADR 0010).

The ledger's own hash chain proves its records agree with each other, but someone who can write
the file can truncate it or rebuild it consistently. A checkpoint, signed with a person's key and
kept **outside** the ledger, is what they cannot forge: it records what the ledger looked like at
one moment, so a later ledger that is shorter, or whose history differs, is detected.

``ledger_id`` is the hash of the ledger's first record, so a checkpoint cannot be applied to a
different ledger. The signer is not stored: it is whoever the allowed-signers file names for the
key that made the signature (see ``jarvis.core.signing``).
"""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, Field

from .common import SCHEMA_VERSION, StrictModel

HEX64 = r"^[0-9a-f]{64}$"


class Checkpoint(StrictModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    ledger_id: str = Field(pattern=HEX64, description="Hash of the ledger's first record")
    seq: int = Field(ge=0, description="Sequence number of the last record covered")
    hash: str = Field(pattern=HEX64, description="Hash of that record")
    created_at: AwareDatetime
    signature: str = Field(
        max_length=8192,
        description="SSH signature, namespace jarvis-checkpoint, over the other fields",
    )
