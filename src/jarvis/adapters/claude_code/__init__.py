"""Claude Code adapter: the hook that feeds the policy gate (task P2.6, ADR 0003)."""

from .config import HookConfig, HookMode, load_config
from .handler import HookContext, HookResult, handle
from .session import SessionStore, default_state_dir
from .snippet import build_settings, check_environment
from .versions import MIN_ON_FAILURE, parse_version, supports_on_failure

__all__ = [
    "MIN_ON_FAILURE",
    "HookConfig",
    "HookContext",
    "HookMode",
    "HookResult",
    "SessionStore",
    "build_settings",
    "check_environment",
    "default_state_dir",
    "handle",
    "load_config",
    "parse_version",
    "supports_on_failure",
]
