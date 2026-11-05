"""``agentctl checkpoint-history``: fail when committed checkpoints were removed or rewritten
(task P2.9f, ADR 0010). Run it in CI against the commit a pull request is based on.

It reads the base commit with ``git`` and the current files from the working tree, then applies
``jarvis.core.ledger.checkpoint_history.check_history``. Anything it cannot read is a failure
(exit 2), not a pass: an error in CI must not look like a clean result.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from jarvis.core.ledger.checkpoint_history import CHECKPOINT_DIR, check_history

ALLOWED_SIGNERS = ".agents/allowed-signers"


class GitError(Exception):
    pass


def add_checkpoint_history_parser(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(
        "checkpoint-history",
        help="fail if committed checkpoints were removed or changed since a base commit",
    )
    parser.add_argument(
        "--base",
        required=True,
        help="the commit to compare with (in CI: the pull request's base commit); an empty or "
        "all-zero value means there is nothing to compare and passes",
    )
    parser.add_argument("--dir", default=CHECKPOINT_DIR, help="where checkpoints are committed")
    parser.add_argument("--allowed-signers", default=ALLOWED_SIGNERS)


def _git(*args: str) -> bytes:
    import subprocess  # not at module level: slow to import, and only this command needs it

    git = shutil.which("git")
    if git is None:
        raise GitError("git was not found on PATH")
    try:
        done = subprocess.run([git, *args], capture_output=True, check=False, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitError(f"git could not run: {type(exc).__name__}") from exc
    if done.returncode != 0:
        raise GitError(f"git {args[0]} failed: {done.stderr.decode('utf-8', 'replace').strip()}")
    return done.stdout


def _base_files(base: str, directory: str) -> dict[str, str]:
    names = _git("ls-tree", "-r", "--name-only", base, "--", directory).decode().splitlines()
    files: dict[str, str] = {}
    for path in names:
        text = _git("show", f"{base}:{path}").decode("utf-8", "replace")
        files[path.removeprefix(directory.rstrip("/") + "/")] = text
    return files


def _head_files(directory: str) -> dict[str, str]:
    root = Path(directory)
    if not root.is_dir():
        return {}
    return {
        str(path.relative_to(root).as_posix()): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _base_allowed(base: str, allowed_path: str) -> str | None:
    try:
        return _git("show", f"{base}:{allowed_path}").decode("utf-8", "replace")
    except GitError:
        return None  # the base commit has no such file


def run_checkpoint_history_command(args: argparse.Namespace) -> int:
    base = args.base.strip()
    if not base or set(base) == {"0"}:
        print("checkpoint-history: no base commit to compare with, nothing to check")
        return 0
    try:
        _git("rev-parse", "--verify", f"{base}^{{commit}}")
        before = _base_files(base, args.dir)
        allowed = _base_allowed(base, args.allowed_signers)
        after = _head_files(args.dir)
    except (GitError, OSError, UnicodeDecodeError) as exc:
        print(f"checkpoint-history: cannot compare ({exc})", file=sys.stderr)
        return 2
    failures = check_history(before, after, allowed)
    for failure in failures:
        print(f"FAILED: {failure}", file=sys.stderr)
    if failures:
        return 1
    added = sum(
        max(0, len(after[n].splitlines()) - len(before.get(n, "").splitlines())) for n in after
    )
    print(f"checkpoint-history: OK ({len(after)} file(s), {added} new line(s) since {base[:12]})")
    return 0
