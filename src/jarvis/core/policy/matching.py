"""Path and glob matching. The implementation lives in ``jarvis.core.matching`` so lower layers
(the approval state) can use it without importing the policy package."""

from jarvis.core.matching import glob_match, matches_any, normalize_path

__all__ = ["glob_match", "matches_any", "normalize_path"]
