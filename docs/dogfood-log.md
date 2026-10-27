# Dogfood log: the Claude Code hook on this repository

Task P2.6d. This log records what happened when the hook was run on the project that builds it, in **log-only mode** (it records what the gate would decide and never blocks). It feeds P2.10, the decision to switch to enforcing. Add a new section for each review round.

How to review a round:

```bash
uv run agentctl hook report
```

## Round 1: 2026-10-27, one development session

### Setup

- `.agents/policy.yaml`, `.agents/tool-registry.yaml` and `.agents/claude-code.yaml` are copies of the examples in `examples/claude-code/`, plus this repository's own additions (below). They are committed, so changes to them go through review (CODEOWNERS covers `/.agents/`).
- `.claude/settings.local.json` holds the hook wiring. It is per-machine and git-ignored. It was generated with `agentctl hook snippet --runner venv --minimal` (only `PreToolUse`, `SessionStart` and `ConfigChange`).
- The ledger is `.agents/ledger/events.jsonl` (git-ignored). The session state is kept outside the repository.

### What was measured

One continuous session of normal development work: reads, searches, edits, test runs, git commands, a web fetch and the task-list tools. **55 decisions in about six minutes.** The claude desktop app reported its permission mode as `auto` for every call.

| | Count |
|---|---|
| Allowed | 13 |
| Would be sent to a human | 39 (including the web fetch, which the council rule also matched) |
| Would be denied | 3 |
| Decisions made in a tainted session | 44 |

The gate would have intervened on **76%** of calls, which is far too high to enforce. Almost all of it comes from three causes, covered below.

### What worked

- **Hooks load mid-session.** Writing `.claude/settings.local.json` took effect on the next tool call, with no restart.
- **`ConfigChange` fires when the agent edits its own settings.** Editing `.claude/settings.local.json` with the Edit tool produced a `config_change` record for `local_settings`, with the project-relative file name. (In log-only mode it is recorded and not blocked; blocking in enforce mode is untested here.)
- **The ledger stayed valid** (`agentctl verify` passes) with 55 decisions and the notes.
- **The gate reacted as designed:** unregistered tools were denied, the web fetch went to the council, an edit to the settings file hit the "framework and Claude config" rule, and after the web fetch every later call was marked tainted.

### What was wrong, and is fixed

| Finding | Effect | Fix |
|---|---|---|
| Nearly every shell command starts with `cd <project> &&`, and Git Bash writes the path as `/d/...` | Every read-only command counted as a general shell call | A leading `cd` into the project is ignored when classifying (other `cd` targets, variables and `;` are not) |
| Commands end in `\| head -5`, `\| tail -1` or `2>&1` | Same | Those trailing output filters are ignored; anything else after a pipe still makes it a general shell call |
| The desktop app's own tools (task list, branch prompt) are MCP tools, which are untrusted by default | They tainted the session at once and were denied as unregistered | Registered in this repository's `.agents/` with output trust overridden to internal. The generic default (MCP output is untrusted) is unchanged |
| `ToolSearch`, `Skill`, `Task*` had no mapping | Denied as unregistered | Mapped to the internal bookkeeping tool |
| The report could not show MCP server details | Hard to review | `mcp_server` name and source are now recorded with each decision |

### What remains, and why it is a decision and not a bug

1. **Taint friction (the biggest source).** After one web fetch, 34 later calls (62% of all decisions) were flagged by the rule "writes after untrusted content", including every edit to `src/` and `tests/`. That rule does what it says. The trade-off is that fetching a web page is a normal part of development. Options: keep it strict; apply it only to riskier tools (for example `risk_at_least: medium`, which leaves ordinary edits alone but still covers shell commands and web access); or clear taint when a person approves. A hijacked agent writing to a branch is still stopped by pull request review, which is why relaxing it is defensible, but it removes a layer. **This is for the project owner to decide.**
2. **General shell commands: 24 decisions (44%).** They are real commands such as `git add && git commit && git push`, `cp`, `mkdir` and `uv run python - <<EOF` scripts. Treating them as a human decision is the safe default. If it is too noisy, add specific prefixes (for example `git add` and `git commit`) to the shell allow list in `claude-code.yaml`.
3. **Parallel tool calls.** When several calls run in one turn, one call's hook can taint the session before another call's hook runs. That is over-cautious, not unsafe.
4. **Session state is never cleared** except at the start of a new session. One genuine web fetch taints the rest of that session.

### Latency

Measured on this machine (Windows, Python 3.13), median of 6 to 8 runs:

| Step | Time |
|---|---|
| `python -c pass` | 121 ms |
| `import pydantic` | 308 ms |
| `import jarvis.core.schemas` | 833 ms |
| `import jarvis.hook_cli` | 898 ms |
| Whole hook, run with the venv Python | 931 ms |
| Whole hook, run as `agentctl.exe` | 971 ms |
| Whole hook, run through `uv run --project` | 1,091 ms |
| Whole hook, through `uv run --quiet --no-sync` | 1,244 ms |

About a second per hook run, dominated by importing pydantic and building the schema models. With all five events registered that is about two seconds per tool call, so the dogfood setup registers `PreToolUse` only (about one second per call). That is usable for observation but not for everyday enforcement. A local service or a much lighter import path is needed; tracked as P2.6e.

### Not tested yet

Enforce mode (it would have blocked this very session), how `ask` behaves in this app, `onFailure: block` on a newer Claude Code, the Windows Git Bash shell form, what happens when the hook crashes, and `bypassPermissions`.

### Next round

Keep the hook on for several days of normal work, then run the report again. Decide the taint question (item 1 above), decide which shell prefixes to allow, and record the new numbers here. P2.10 should not start until the intervention rate for ordinary work is low and every remaining intervention is one a person agrees with.
