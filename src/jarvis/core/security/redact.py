"""Find and redact secrets and personal data (task P2.12, threat model T-5).

Two jobs, one set of patterns:

* ``redact_value`` is what the ledger applies to an event's free-form ``data`` before it is
  written, so a secret that reaches a record by mistake (an error message, a note, a
  ``--data`` argument) is replaced by ``[REDACTED:<kind>]`` and the hash chain covers the
  redacted text.
* ``find_secrets`` is what the hook applies to a tool call's input. It returns only the *kinds*
  found, never the text, so the gate can ask a human without the secret being recorded anywhere.

**What this is not.** It matches well-known token formats and a few obvious shapes (a
``password=`` assignment, credentials in a URL, a private key block, an email address, a card
number that passes the Luhn check). There is deliberately no entropy guessing: it would
mangle the hashes and signatures the ledger is full of. A secret in an unknown format, or one
split across pieces, is not found, so this reduces the risk and does not remove it. Scanning
is capped (``MAX_SCAN_CHARS``); input beyond the cap is reported as not fully scanned instead
of being waved through.

Only the Python standard library is used, and the patterns are compiled on first use.
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED:{kind}]"
MAX_SCAN_CHARS = 1_000_000
NOT_FULLY_SCANNED = "input-not-fully-scanned"

# Keys whose values are signatures or digests, never prose: left alone so they stay valid.
_EXEMPT_EXACT = frozenset({"signature", "hash", "prev_hash", "digest"})
_EXEMPT_SUFFIXES = ("_sha256", "_hash", "_digest", "_signature")

# (kind, pattern, is_secret). Personal data (is_secret False) is redacted in the ledger but is
# not a reason to stop a tool call: emails are everywhere in code and documentation.
_PATTERN_SOURCES: list[tuple[str, str, bool]] = [
    (
        "private-key",
        r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----(?:.*?-----END (?:[A-Z0-9]+ )*"
        r"PRIVATE KEY(?: BLOCK)?-----|.*\Z)",
        True,
    ),
    ("aws-access-key", r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA)[0-9A-Z]{16}\b", True),
    ("github-token", r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})\b", True),
    ("slack-token", r"\bxox[abposr]-[A-Za-z0-9-]{10,}", True),
    ("anthropic-key", r"\bsk-ant-[A-Za-z0-9_-]{20,}", True),
    ("openai-key", r"\bsk-(?!ant-)(?:proj-)?[A-Za-z0-9_-]{32,}", True),
    ("google-api-key", r"\bAIza[0-9A-Za-z_-]{35}\b", True),
    ("stripe-key", r"\b[sr]k_live_[0-9A-Za-z]{20,}", True),
    ("jwt", r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}", True),
    ("bearer-token", r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{20,}", True),
    ("url-credentials", r"(?<=://)[^\s/:@]+:[^\s/@]{3,}(?=@)", True),
    (
        "credential-assignment",
        r"(?i)\b(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|client[_-]?secret)"
        r"\b[\"']?\s*[:=]\s*[\"']?(?=[^\s\"',;]*\d)[^\s\"',;]{6,}",
        True,
    ),
    ("email", r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b", False),
    ("card-number", r"\b(?:\d[ -]?){12,18}\d\b", False),
]

_compiled: list[tuple[str, re.Pattern[str], bool]] | None = None


def _patterns() -> list[tuple[str, re.Pattern[str], bool]]:
    global _compiled
    if _compiled is None:
        _compiled = [(k, re.compile(p, re.DOTALL), s) for k, p, s in _PATTERN_SOURCES]
    return _compiled


def _luhn_ok(digits: str) -> bool:
    if not 13 <= len(digits) <= 19 or len(set(digits)) == 1:
        return False
    total = 0
    for index, char in enumerate(reversed(digits)):
        n = int(char)
        if index % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _real(kind: str, match: re.Match[str]) -> bool:
    if kind == "card-number":
        return _luhn_ok(re.sub(r"[ -]", "", match.group(0)))
    return True


def find_secrets(text: str) -> list[str]:
    """The kinds of secret in ``text`` (sorted, no duplicates, never the text itself).

    Personal data is not reported here. Text beyond ``MAX_SCAN_CHARS`` is not scanned, and the
    result then also contains ``input-not-fully-scanned``.
    """
    kinds: set[str] = set()
    scanned = text[:MAX_SCAN_CHARS]
    for kind, pattern, is_secret in _patterns():
        if is_secret and any(_real(kind, m) for m in pattern.finditer(scanned)):
            kinds.add(kind)
    if len(text) > MAX_SCAN_CHARS:
        kinds.add(NOT_FULLY_SCANNED)
    return sorted(kinds)


def redact_text(text: str) -> str:
    """``text`` with every secret and piece of personal data replaced by ``[REDACTED:kind]``."""
    for kind, pattern, _ in _patterns():
        text = pattern.sub(
            lambda m, kind=kind: REDACTED.format(kind=kind) if _real(kind, m) else m.group(0),
            text,
        )
    return text


def _exempt(key: object) -> bool:
    return isinstance(key, str) and (key in _EXEMPT_EXACT or key.endswith(_EXEMPT_SUFFIXES))


def redact_value(value: Any) -> Any:
    """``value`` with every string in it redacted, recursing into dicts and lists.

    Values under a key that names a signature or a digest are kept as they are. Numbers,
    booleans and ``None`` are never touched.
    """
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {k: v if _exempt(k) else redact_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_value(v) for v in value]
    return value


def find_secrets_in(value: Any) -> list[str]:
    """``find_secrets`` over every string in a nested value, with one shared scan budget."""
    kinds: set[str] = set()
    budget = MAX_SCAN_CHARS
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            if budget <= 0:
                kinds.add(NOT_FULLY_SCANNED)
                continue
            kinds.update(find_secrets(item[:budget]))
            if len(item) > budget:
                kinds.add(NOT_FULLY_SCANNED)
            budget -= len(item)
        elif isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return sorted(kinds)
