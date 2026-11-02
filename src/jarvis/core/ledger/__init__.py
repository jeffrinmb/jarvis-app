"""Append-only, hash-chained audit ledger (task P2.1)."""

from .checkpoint import (
    CheckpointReport,
    check_checkpoints,
    create_checkpoint,
    default_checkpoint_path,
    ledger_id,
)
from .ledger import (
    GENESIS_HASH,
    Ledger,
    LedgerCorruptError,
    LedgerError,
    LedgerLockError,
    VerifyResult,
    canonical_json,
    compute_hash,
)

__all__ = [
    "GENESIS_HASH",
    "CheckpointReport",
    "Ledger",
    "LedgerCorruptError",
    "LedgerError",
    "LedgerLockError",
    "VerifyResult",
    "canonical_json",
    "check_checkpoints",
    "compute_hash",
    "create_checkpoint",
    "default_checkpoint_path",
    "ledger_id",
]
