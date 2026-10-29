"""Path and tool-id glob matching, with fail-closed path normalisation.

Globs: ``*`` matches within one path segment, ``**/`` matches any number of directories
(including none), ``**`` matches anything, ``?`` matches one character within a segment.
Matching is case-insensitive, because common file systems are, and a case-sensitive
deny rule (``**/.env``) would be bypassed by ``.ENV``.
"""

from __future__ import annotations

import re
from functools import lru_cache

_DRIVE = re.compile(r"^[A-Za-z]:")


def normalize_path(path: str) -> tuple[str, bool]:
    """Return ``(normalised, suspicious)``.

    Backslashes become slashes, ``./`` prefixes and duplicate slashes are removed. A path is
    *suspicious* when it is absolute, has a drive letter, contains a ``..`` segment, a NUL
    byte or is empty. Suspicious paths are never auto-approved by the engine.
    """
    suspicious = False
    p = path.replace("\\", "/")
    if "\x00" in p or not p.strip():
        suspicious = True
    if p.startswith("/") or _DRIVE.match(p):
        suspicious = True
    while "//" in p:
        p = p.replace("//", "/")
    while p.startswith("./"):
        p = p[2:]
    p = p.rstrip("/") if len(p) > 1 else p
    if any(segment == ".." for segment in p.split("/")):
        suspicious = True
    return p, suspicious


@lru_cache(maxsize=1024)
def _compile(glob: str) -> re.Pattern[str]:
    out: list[str] = []
    i = 0
    while i < len(glob):
        c = glob[i]
        if c == "*":
            if glob.startswith("**/", i):
                out.append("(?:.*/)?")
                i += 3
                continue
            if glob.startswith("**", i):
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("^" + "".join(out) + "$", re.IGNORECASE)


def glob_match(glob: str, value: str) -> bool:
    return _compile(glob).match(value) is not None


def matches_any(globs: list[str], value: str) -> bool:
    return any(glob_match(g, value) for g in globs)
