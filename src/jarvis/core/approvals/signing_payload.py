"""What is signed for an approval: the request id, a random nonce and the scope, in canonical
form (task P2.9a, ADR 0010).

The approver's name is deliberately **not** part of it. The name comes from the allowed-signers
file, from whichever listed key made the signature.
"""

from __future__ import annotations

import json
import secrets
from datetime import UTC

from jarvis.core.schemas.approval import ApprovalGrant, ApprovalScope
from jarvis.core.signing import NS_APPROVAL


def canonical_payload(request_id: str, nonce: str, scope: ApprovalScope) -> bytes:
    body = scope.model_dump(mode="json")
    body["expires_at"] = scope.expires_at.astimezone(UTC).isoformat()
    return json.dumps(
        {"v": 1, "namespace": NS_APPROVAL, "request_id": request_id, "nonce": nonce, "scope": body},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def grant_payload(grant: ApprovalGrant) -> bytes:
    if grant.scope is None or grant.request_id is None or grant.nonce is None:
        raise ValueError("only a signed command-line grant has a payload")
    return canonical_payload(grant.request_id, grant.nonce, grant.scope)


def new_nonce() -> str:
    return secrets.token_hex(16)
