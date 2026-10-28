"""Policy engine and gate (tasks P2.4 and P2.5)."""

from .check import decision_event, prior_diff_lines, run_check
from .engine import SEVERITY, combine, evaluate
from .matching import glob_match, matches_any, normalize_path
from .usage import Anomaly, Usage, compute_usage, detect_anomalies, fingerprint

__all__ = [
    "SEVERITY",
    "Anomaly",
    "Usage",
    "combine",
    "compute_usage",
    "decision_event",
    "detect_anomalies",
    "evaluate",
    "fingerprint",
    "glob_match",
    "matches_any",
    "normalize_path",
    "prior_diff_lines",
    "run_check",
]
