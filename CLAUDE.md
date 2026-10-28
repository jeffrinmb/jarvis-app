# Claude Code instructions

Read [AGENTS.md](AGENTS.md): it holds everything shared by all coding agents, and it is the only copy.

@AGENTS.md

Claude Code specifics:

- The policy hook for this repository is configured per machine in `.claude/settings.local.json` (see [docs/claude-code-hook.md](docs/claude-code-hook.md)). It runs in log-only mode and never blocks. `uv run agentctl hook report` shows what it recorded.
- Add `Co-Authored-By` trailers to commits you write, as the repository's other commits do.
