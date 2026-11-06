"""SSH signatures through ``ssh-keygen -Y`` (task P2.9a, ADR 0010).

The OpenSSH tool is already on the machines this runs on, it supports hardware security keys,
and it uses the same keys as git commit signing, so no dependency is added. Signatures are bound
to a *namespace*, so one made for an approval cannot be replayed as something else.

Who signed is read from the **allowed-signers file** (a trusted file, kept in the protected
policy source), never from a name inside the signed data. A signature from a key that is not in
that file is simply "nobody".

Every call runs ``ssh-keygen`` with an argument list (no shell), fixed time limits, and the
public material in a private temporary directory that is removed afterwards. A missing tool, a
timeout or any unexpected output is "not verified"; this module never guesses.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import subprocess

NS_APPROVAL = "jarvis-approval"
NS_CHECKPOINT = "jarvis-checkpoint"
NS_CREDENTIAL = "jarvis-credential"

VERIFY_TIMEOUT_SECONDS = 20
SIGN_TIMEOUT_SECONDS = 120  # a hardware key waits for a person to touch it
MAX_SIGNATURE_BYTES = 8192

_PRINCIPAL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@+-]{0,99}$")
_GOOD = re.compile(r'^Good "([^"]+)" signature for (\S+) with (\S+) key (\S+)\s*$')


class SigningError(Exception):
    """Signing could not be done (no tool, no key, a timeout). Verification never raises this:
    it answers ``None``."""


@dataclass(frozen=True)
class Verified:
    principal: str  # the name the allowed-signers file gives this key
    key_type: str  # for example ED25519 or ED25519-SK
    fingerprint: str

    @property
    def hardware(self) -> bool:
        """A security-key type. Whether the touch requirement was enforced when verifying is
        not established here (it needs a real key to test)."""
        return self.key_type.upper().endswith("-SK")


def ssh_keygen() -> str:
    found = shutil.which("ssh-keygen")
    if not found:
        raise SigningError("ssh-keygen was not found on PATH; install the OpenSSH client")
    return found


def _run(args: list[str], data: bytes | None, timeout: int) -> subprocess.CompletedProcess[bytes]:
    import subprocess  # not at module level: it is slow to import and only needed here

    try:
        # Reason: ssh-keygen from shutil.which, fixed flags and an argument list (no shell).
        return subprocess.run(  # nosec B603
            args, input=data, capture_output=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise SigningError("ssh-keygen did not finish in time") from exc
    except OSError as exc:
        raise SigningError(f"ssh-keygen could not run: {type(exc).__name__}") from exc


def sign(
    data: bytes, key_path: str | Path, namespace: str, timeout: int = SIGN_TIMEOUT_SECONDS
) -> str:
    """The armoured SSH signature of ``data``. The key may be a security-key handle, in which
    case ssh-keygen waits for a touch (and asks for a passphrase on the terminal if it has one).
    """
    # PowerShell and cmd pass "~" through unexpanded, so expand it here, in every shell alike.
    key = Path(key_path).expanduser()
    if not key.is_file():
        raise SigningError(f"signing key not found: {key_path}")
    done = _run([ssh_keygen(), "-Y", "sign", "-f", str(key), "-n", namespace], data, timeout)
    signature = done.stdout.decode("utf-8", "replace")
    if done.returncode != 0 or "BEGIN SSH SIGNATURE" not in signature:
        raise SigningError("ssh-keygen could not sign (wrong passphrase, no device, or bad key)")
    return signature


def _principals(signature_file: Path, allowed_file: Path) -> list[str]:
    done = _run(
        [ssh_keygen(), "-Y", "find-principals", "-f", str(allowed_file), "-s", str(signature_file)],
        None,
        VERIFY_TIMEOUT_SECONDS,
    )
    if done.returncode != 0:
        return []
    names = [ln.strip() for ln in done.stdout.decode("utf-8", "replace").splitlines()]
    return [n for n in names if _PRINCIPAL.match(n)]


def verify(data: bytes, signature: str, allowed_signers: str, namespace: str) -> Verified | None:
    """Who signed ``data``, or ``None`` if the signature does not verify for a listed key in
    ``namespace``. ``allowed_signers`` is the text of the allowed-signers file."""
    if not signature or len(signature.encode()) > MAX_SIGNATURE_BYTES:
        return None
    if "BEGIN SSH SIGNATURE" not in signature or not allowed_signers.strip():
        return None
    import tempfile

    try:
        with tempfile.TemporaryDirectory(prefix="jarvis-sig-") as tmp:
            allowed = Path(tmp) / "allowed"
            sig = Path(tmp) / "data.sig"
            allowed.write_text(allowed_signers, encoding="utf-8", newline="\n")
            sig.write_text(signature, encoding="utf-8", newline="\n")
            for principal in _principals(sig, allowed):
                done = _run(
                    [
                        ssh_keygen(),
                        "-Y",
                        "verify",
                        "-f",
                        str(allowed),
                        "-I",
                        principal,
                        "-n",
                        namespace,
                        "-s",
                        str(sig),
                    ],
                    data,
                    VERIFY_TIMEOUT_SECONDS,
                )
                match = _GOOD.match(done.stdout.decode("utf-8", "replace").strip())
                if (
                    done.returncode == 0
                    and match
                    and match.group(1) == namespace
                    and (match.group(2) == principal)
                ):
                    return Verified(principal, match.group(3), match.group(4))
    except (SigningError, OSError):
        return None
    return None


def self_test() -> str:
    """A real sign-and-verify round trip with a throwaway key, for ``hook doctor``. Returns a
    short description or raises ``SigningError``."""
    import tempfile

    tool = ssh_keygen()
    with tempfile.TemporaryDirectory(prefix="jarvis-selftest-") as tmp:
        key = Path(tmp) / "k"
        made = _run(
            [tool, "-q", "-t", "ed25519", "-N", "", "-C", "selftest", "-f", str(key)], None, 30
        )
        if made.returncode != 0:
            raise SigningError("ssh-keygen could not create a test key")
        parts = key.with_suffix(".pub").read_text(encoding="utf-8").split()
        allowed = f'selftest namespaces="{NS_APPROVAL}" {parts[0]} {parts[1]}\n'
        payload = b"jarvis self-test"
        signature = sign(payload, key, NS_APPROVAL, timeout=30)
        good = verify(payload, signature, allowed, NS_APPROVAL)
        bad = verify(payload + b"!", signature, allowed, NS_APPROVAL)
        if good is None or bad is not None:
            raise SigningError("ssh-keygen -Y did not verify as expected")
    return "ssh-keygen -Y signs and verifies"
