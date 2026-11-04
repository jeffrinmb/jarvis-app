"""`agentctl`: the entry point.

Kept deliberately small. Claude Code runs ``agentctl hook run`` (or ``python -m jarvis.cli hook
run``) before every tool call, and importing the whole command set costs hundreds of milliseconds
that a hook call does not need. So ``hook`` goes straight to ``jarvis.hook_cli`` and every other
command loads ``jarvis.commands``, which holds the full set. Behaviour and exit codes are the same
either way (task P2.6e).
"""

from __future__ import annotations

import os
import sys


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args[:2] == ["hook", "run"] and (
        "--server" in args or os.environ.get("JARVIS_HOOK_SERVER") == "1"
    ):
        from jarvis.hook_client import main_client

        return main_client(args)
    if args[:1] == ["hook"]:
        from jarvis.hook_cli import main_hook

        return main_hook(args)
    from jarvis.commands import main as run

    return run(args)


if __name__ == "__main__":
    sys.exit(main())
