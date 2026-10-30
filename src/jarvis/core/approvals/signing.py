"""Signed approvals: what is signed, how a grant is signed, and how the gate verifies one
(task P2.9a, ADR 0010).

The signed bytes are the canonical form of the request id, a random nonce and the scope (with
its expiry in UTC). They do **not** contain the approver's name: the name comes from the
allowed-signers file, from whichever listed key made the signature. A grant that claims another
name than its key's does not count.

The gate calls ``make_verifier`` only for grants that would cover the call, because each check
costs a few tens of milliseconds to a few hundred.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from jarvis.core import signing
from jarvis.core.schemas.approval import ApprovalGrant, ApprovalScope
from jarvis.core.signing import NS_APPROVAL, Verified

from .signing_payload import canonical_payload, new_nonce
from .state import Grant

Verifier = Callable[[Grant], Verified | None]


def make_verifier(allowed_signers: str | None, approvers: list[str]) -> Verifier:
    """A function that says who really signed a grant, or ``None``.

    Without an allowed-signers file nothing verifies (fail closed). The policy's ``approvers``
    list, when set, applies to the verified name, and an agent's name never counts.
    """

    def verify(grant: Grant) -> Verified | None:
        if not allowed_signers or not grant.signature or not grant.payload:
            return None
        found = signing.verify(grant.payload, grant.signature, allowed_signers, NS_APPROVAL)
        if found is None or found.principal != grant.approver:
            return None
        if approvers and found.principal not in approvers:
            return None
        return found

    return verify


def sign_grant(
    request_id: str,
    scope: ApprovalScope,
    key_path: str | Path,
    allowed_signers: str,
    timeout: int = signing.SIGN_TIMEOUT_SECONDS,
) -> ApprovalGrant:
    """Sign a scope with the person's key and name them from the allowed-signers file.

    Raises ``signing.SigningError`` if the key is not listed there, so a key nobody trusts
    never produces a grant.
    """
    nonce = new_nonce()
    signature = signing.sign(
        canonical_payload(request_id, nonce, scope), key_path, NS_APPROVAL, timeout
    )
    found = signing.verify(
        canonical_payload(request_id, nonce, scope), signature, allowed_signers, NS_APPROVAL
    )
    if found is None:
        raise signing.SigningError("this key is not listed in the allowed-signers file")
    return ApprovalGrant(
        request_id=request_id,
        approver=found.principal,
        via="cli",
        scope=scope,
        nonce=nonce,
        signature=signature,
    )
