"""Where the hook reads its policy, tool registry and adapter config from (ADR 0004).

The policy decides what an agent may do, so it must not be changeable by that agent. A file in
the working tree is the agent's to edit. Three sources are supported:

* ``worktree``: files under the project directory. Convenient, but **agent-writable**. Fine for
  log-only observation, refused in enforce mode.
* ``directory``: a directory outside the project that the agent is not given write access to.
* ``git``: the content of the files at a git ref or commit. Edits in the working tree, and
  commits on the agent's own branch, do not change what is enforced. Pin a full commit SHA for
  the strongest guarantee; a branch name such as ``origin/main`` can be moved by an agent that
  has a shell (until the sandbox of task P4.2 stops that).

Every read returns the exact bytes' SHA-256, so each decision can record which policy it used.
A missing source, ref or file is an error, never an empty policy.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol

DEFAULT_REF = "origin/main"
SOURCE_KINDS = ("worktree", "directory", "git")
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@^~+-]{0,199}$")
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
GIT_TIMEOUT_SECONDS = 10


class SourceError(Exception):
    """The policy source cannot be read, so no decision can be made from it."""


@dataclass(frozen=True)
class SourceFile:
    text: str
    sha256: str


class TrustedSource(Protocol):
    kind: str
    protected: bool

    def describe(self) -> str: ...

    def read(self, relative: str) -> SourceFile | None:
        """The file's content, or ``None`` if it does not exist in this source."""


def _decode(data: bytes, where: str) -> SourceFile:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SourceError(f"{where} is not valid UTF-8") from exc
    return SourceFile(text, hashlib.sha256(data).hexdigest())


def _safe_relative(relative: str) -> str:
    """A relative POSIX path that stays inside its source, else ``SourceError``."""
    candidate = relative.replace("\\", "/")
    path = PurePosixPath(candidate)
    if path.is_absolute() or re.match(r"^[A-Za-z]:", candidate) or ".." in path.parts:
        raise SourceError(
            f"policy file path must be relative and stay inside the source: {relative}"
        )
    if not path.parts or str(path) == ".":
        raise SourceError("policy file path is empty")
    return path.as_posix()


class WorktreeSource:
    """Files under the project directory. Agent-writable, so never protected."""

    kind = "worktree"
    protected = False

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = Path(project_dir)

    def describe(self) -> str:
        return "worktree"

    def read(self, relative: str) -> SourceFile | None:
        path = Path(relative)
        if not path.is_absolute():
            path = self.project_dir / path
        try:
            return _decode(path.read_bytes(), str(relative))
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise SourceError(f"cannot read {relative}: {type(exc).__name__}") from exc


class DirectorySource:
    """A directory outside the project, which the agent is not given write access to."""

    kind = "directory"
    protected = True

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    def describe(self) -> str:
        return f"directory:{self.directory}"

    def read(self, relative: str) -> SourceFile | None:
        safe = _safe_relative(relative)
        base = os.path.realpath(self.directory)
        target = os.path.realpath(os.path.join(base, safe))
        if os.path.commonpath([base, target]) != base:
            raise SourceError(f"{relative} resolves outside the policy directory")
        try:
            with open(target, "rb") as fh:
                return _decode(fh.read(), str(relative))
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise SourceError(f"cannot read {relative}: {type(exc).__name__}") from exc


class GitSource:
    """File content at a git ref or commit, read with ``git show``."""

    kind = "git"
    protected = True

    def __init__(self, project_dir: Path, ref: str) -> None:
        if not _REF.match(ref) or ".." in ref:
            raise SourceError(f"not an acceptable git ref: {ref!r}")
        self.project_dir = Path(project_dir)
        self.ref = ref
        self._commit: str | None = None

    def _git(self, *args: str) -> subprocess.CompletedProcess[bytes]:
        # Variables such as GIT_DIR could point git at some other repository.
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        try:
            return subprocess.run(
                ["git", "-C", str(self.project_dir), *args],
                capture_output=True,
                timeout=GIT_TIMEOUT_SECONDS,
                check=False,
                env=env,
            )
        except FileNotFoundError as exc:
            raise SourceError("git is not installed or not on PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise SourceError("git did not answer in time") from exc

    def commit(self) -> str:
        """The full commit SHA the ref points to, resolved once and then used for every read."""
        if self._commit is None:
            done = self._git("rev-parse", "--verify", "--quiet", f"{self.ref}^{{commit}}")
            sha = done.stdout.decode("ascii", "replace").strip()
            if done.returncode != 0 or not _FULL_SHA.match(sha):
                raise SourceError(f"git ref {self.ref!r} was not found in {self.project_dir}")
            self._commit = sha
        return self._commit

    def describe(self) -> str:
        return f"git:{self.ref}@{self.commit()[:12]}"

    def read(self, relative: str) -> SourceFile | None:
        safe = _safe_relative(relative)
        spec = f"{self.commit()}:./{safe}"
        if self._git("cat-file", "-e", spec).returncode != 0:
            return None
        done = self._git("show", spec)
        if done.returncode != 0:
            raise SourceError(f"git could not show {relative} at {self.ref}")
        return _decode(done.stdout, f"{relative} at {self.ref}")


def is_full_sha(ref: str) -> bool:
    return bool(_FULL_SHA.match(ref))


def make_source(
    kind: str | None, project_dir: Path, ref: str | None = None, directory: str | None = None
) -> TrustedSource:
    """Build the source named by the hook command line or environment (never by a file the
    agent can edit)."""
    kind = kind or "worktree"
    if kind == "worktree":
        return WorktreeSource(project_dir)
    if kind == "directory":
        if not directory:
            raise SourceError("policy source 'directory' needs --policy-dir")
        return DirectorySource(Path(directory))
    if kind == "git":
        return GitSource(project_dir, ref or DEFAULT_REF)
    raise SourceError(f"unknown policy source {kind!r}; use one of {', '.join(SOURCE_KINDS)}")
