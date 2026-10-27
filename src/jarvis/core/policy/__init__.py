"""Policy engine and gate (tasks P2.4 and P2.5)."""

from .check import decision_event, prior_diff_lines, run_check
from .engine import SEVERITY, combine, evaluate
from .matching import glob_match, matches_any, normalize_path

__all__ = [
    "SEVERITY",
    "combine",
    "decision_event",
    "evaluate",
    "glob_match",
    "matches_any",
    "normalize_path",
    "prior_diff_lines",
    "run_check",
]
