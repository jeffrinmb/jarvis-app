# ADR 0003: Claude Code hook interface and what it means for the policy gate

- Status: Accepted (findings and design direction for P2.6; open questions listed below must be tested)
- Date: 2026-10-27
- Deciders: project owner (task P2.6a)

## Context

Task P2.6 connects the policy gate (`agentctl check`) to Claude Code through hooks. The charter and the threat model (T-3, G1, G2) depend on what a hook can see and what it can stop, and Claude Code's hook interface changes between versions. This ADR records what the official documentation says, what was not verified, what the installed version means for us, and the design decisions that follow.

**Sources.** The official [hooks reference](https://code.claude.com/docs/en/hooks) and [hooks guide](https://code.claude.com/docs/en/hooks-guide), read on 2026-10-27. The reference is very long (about 280,000 characters) and was read through a summarising tool in three passes plus targeted lookups in the saved text, so wording below is a faithful summary and not a quotation. **Installed version here: Claude Code 2.1.276.** Nothing in this ADR has yet been tested by running a hook.

## What the documentation says

**What a hook sees.** Command hooks receive JSON on stdin. Common fields: `session_id`, `prompt_id`, `transcript_path`, `cwd`, `scratchpad_dir`, `permission_mode` (`default`, `plan`, `acceptEdits`, `auto`, `dontAsk` or `bypassPermissions`), `effort`, `hook_event_name`, and `agent_id` and `agent_type` inside subagents. Tool events add `tool_name`, `tool_input` and `tool_use_id`; MCP tools also carry `mcp_server` (with `name` and `source`, from v2.1.274). File paths in `tool_input.file_path` are always absolute, and use backslashes on Windows.

| Tool | `tool_input` fields documented |
|---|---|
| `Bash`, `PowerShell` | `command`, `description`, `timeout`, `run_in_background` |
| `Write` | `file_path`, `content` |
| `Edit` | `file_path`, `old_string`, `new_string`, `replace_all` |
| `Read` | `file_path`, `offset`, `limit` |
| `Glob` | `pattern`, `path` |
| `Grep` | `pattern`, `path`, `glob`, `output_mode`, `-i`, `multiline` |
| `WebFetch` | `url`, `prompt`, `offset` |
| `WebSearch` | `query`, `allowed_domains`, `blocked_domains` |
| `Agent` | `prompt`, `description`, `subagent_type`, `model` |

**When it fires.** `PreToolUse` runs before every tool call, "whether or not it needs permission". `PostToolUse` runs after a successful call and cannot undo it; it receives `tool_response`. `PostToolUseFailure` runs after a failed call and does not fire for permission denials. Other relevant events: `SessionStart` (with `source`), `UserPromptSubmit` (with `prompt`), `SubagentStart` and `SubagentStop`, and `ConfigChange`.

**What it can do to a tool call (`PreToolUse`).**

- Exit code **2** blocks the call, with stderr as the reason. Exit 0 with JSON `hookSpecificOutput.permissionDecision` of `allow`, `deny`, `ask` or `defer` (defer only with `-p`). Precedence across several hooks: `deny`, then `defer`, then `ask`, then `allow`.
- A `deny` blocks the call **in every permission mode**, including `bypassPermissions`, `--dangerously-skip-permissions` and `dontAsk`, because `PreToolUse` fires before any permission-mode check.
- `allow` skips the permission prompt (deny rules and some prompts are still evaluated). It means "approve", which is stronger than our gate's "no objection".
- `updatedInput` replaces the whole tool input, and permission rules are evaluated against the rewritten input, so it can approve a command the rules never saw. Several hooks rewriting the same input are not merged.

**What happens when a hook fails.** For a command hook, any exit code other than 0 or 2, a crash, a timeout or an executable that cannot start is a **non-blocking error and the action proceeds**. Invalid JSON output is also a failure. Setting `"onFailure": "block"` on a command or HTTP hook turns these into blocks, but that needs **Claude Code 2.1.295 or later**. The default command timeout is 10 minutes (600 seconds); set `timeout` explicitly. On Windows without Git Bash, a failed or timed-out `PreToolUse` command hook blocks the call and only exit 0 or 2 counts as a verdict.

**Configuration.** Hooks live in `~/.claude/settings.json`, `.claude/settings.json` (project, shareable), `.claude/settings.local.json`, managed policy settings, plugins, and skill or subagent frontmatter, and merge across levels. Matching hooks run in parallel. `disableAllHooks` turns hooks off, except managed hooks; `allowManagedHooksOnly` blocks all non-managed hooks. A command hook runs in **exec form** when `args` is set (no shell) and in shell form otherwise (`sh -c`, Git Bash on Windows, or PowerShell if Git Bash is absent; `"shell": "powershell"` is available). Hooks run with the user's full permissions and inherit the environment (minus `OTEL_*` exporter variables); `CLAUDE_PROJECT_DIR` is set.

**Trust.** In an interactive session hooks are held back until the folder is trusted. In `-p` and SDK sessions the folder is treated as trusted, so hooks committed in a repository's `.claude/settings.json` run even in a folder the user never trusted.

**Config changes.** A `ConfigChange` hook fires when user, project, local or managed settings, or a skill file, change during a session, and can block the change for every source except managed policy settings.

## Not verified

- The `MultiEdit` and `NotebookEdit` input schemas, the shape of an MCP tool's `tool_input`, `PostToolUse` `tool_response` shapes per tool, and `SessionEnd` input were not found in the text read.
- How `ask` behaves in a non-interactive (`-p`) session was not confirmed.
- Whether the agent's own edit tool triggers `ConfigChange` for project settings, and whether the block holds, is untested.
- Hook latency on this machine is unmeasured.

## Decision

These are the design decisions for P2.6, to be confirmed by the experiments in P2.6c.

1. **One command, several events.** `agentctl hook` reads the event from stdin and handles `PreToolUse` (ask the gate), `PostToolUse` and `PostToolUseFailure` (record the outcome and update session state), `SessionStart` (initialise state, record Claude Code version and permission mode) and `ConfigChange` (see 7).
2. **Log-only first.** In log-only mode the hook always exits 0 and prints no decision. It records the verdict and that it was log-only, so P2.10 can review what would have been blocked.
3. **Map verdicts conservatively.** Gate `allow` produces **no output** (never `allow`), so Claude Code's own permission rules and prompts still apply. `council` and `human` produce `ask` in an interactive session and `deny` in a non-interactive one, until the approval flow (P2.7) exists. `deny` produces `deny` with the verdict's reason. We never use `updatedInput`.
4. **Fail closed, and say when we cannot.** In enforcing mode every internal error exits 2, and the hook is registered with a short `timeout` and, where supported, `"onFailure": "block"`. **This machine runs 2.1.276, which predates `onFailure`**, so a crash, timeout or missing executable would let the action through. The installer must therefore refuse to enable enforcing mode below 2.1.295, and log-only mode must say so.
5. **Build the `Action` from the real call.** `tool_name` maps to a registry tool id through an explicit table, and anything unmapped is an unregistered tool (denied). Paths come from `tool_input.file_path`. `diff_lines` is computed from `Write` content or `Edit` strings; where it cannot be known it is `null`, which the gate already treats as unbounded. `Bash` can touch any path, so it maps to a generic shell tool that is never auto-approved. The role, agent id and `on_behalf_of` come from configuration and the hook input, never from the agent's text.
6. **Session state outside the workspace.** Taint and data-class exposure are kept per `session_id` and updated in `PostToolUse`: `WebFetch` and `WebSearch` output is untrusted; MCP output is untrusted unless the tool is registered otherwise, and trust is judged by `mcp_server.source`, never by the tool name; reads under configured untrusted paths are untrusted; `Bash` output is untrusted when the command uses network tools (a heuristic). The state directory is a no-go path for the gate. This is only as strong as filesystem permissions until the sandbox work (P4.2).
7. **Protect the hook itself.** `.claude/settings*.json`, the hook scripts and the state directory are no-go paths in the gate. A `ConfigChange` hook blocks changes to hook configuration in user, project and local settings and records them. Organisations should use managed settings, which cannot be blocked or disabled from below.
8. **Record the evidence.** Every decision event also records `permission_mode` (so a session running in `bypassPermissions` is visible), `session_id`, `tool_use_id`, `agent_id`, the `transcript_path` and a SHA-256 of the policy files in force. The hook input does not carry the Claude Code version, so the version is checked by `agentctl hook doctor` instead of being recorded per event.
9. **Performance.** Use exec form so there is no shell, and measure start-up cost per tool call. If it is too slow, move to an `http` hook talking to the orchestrator service (P4.1a).
10. **Headless sessions and unreviewed repositories.** Because `-p` trusts the folder, review `.claude/` in any brownfield repository before running Claude Code unattended on it (links to P4.15 and P5).

## Consequences

- The gate can be enforced through hooks even when a user switches to `bypassPermissions`, which is the strongest property the interface gives us.
- A hook that fails silently is fail-open on older Claude Code versions. The version check in decision 4 is not optional.
- The hook only knows what the call and its own state tell it. Shell commands remain opaque, so the generic shell tool must stay behind a human or council gate, and real containment needs the sandbox (P4.2).
- A project hook configuration can be edited by the agent. Decision 7 reduces that risk but a managed setting or a sandbox is the real fix (threat model T-11).
- Our own settings can conflict with other hooks a user has: precedence is deny over ask over allow, so we can add restrictions but cannot grant approval.

## Alternatives considered

- **Static permission rules only.** Simple and built in, but cannot express taint, cumulative limits or the lethal trifecta. Still worth using alongside the hook.
- **An `http` hook to a local service first.** Better latency and a place for durable state, but it needs the orchestrator service (P4.1a) before we can start.
- **The Agent SDK `canUseTool` callback.** More control, but it only covers SDK-driven runs and would be a second integration. A candidate for P7.3.
- **A sandbox only.** Necessary for containment (P4.2) but cannot see intent, so it complements and does not replace the gate.

## Implementation notes (P2.6)

The hook was built on 2026-10-27 as [docs/claude-code-hook.md](../claude-code-hook.md) describes. Where it differs from or adds to the decisions above:

- **Exposure is applied when a call is requested**, not only when `PostToolUse` arrives (decision 6 said "updated in PostToolUse"). A lost or failed post event therefore cannot hide an exposure. A call that the gate then blocks still counts, which is the conservative side. `PostToolUse` repeats the update, which is harmless because state only grows.
- **Shell commands** are classified by exact token prefixes plus a ban on shell metacharacters and a few dangerous flags, so `git status; curl ...` is a general shell call.
- **Missing or corrupt session state** is read as "everything was seen", so damage makes the gate stricter.
- **Hashes of the policy, registry and project files** are recorded with every decision. Loading them from a location the agent cannot write was added later, in [ADR 0004](0004-trusted-policy-source.md).
- **Verified only with synthetic payloads.** Nothing here has run inside a live Claude Code session. The open questions above are still open and are tracked as P2.6c; P2.6d runs the hook on this repository in log-only mode.
- **Dogfooding answered some of the open questions** ([docs/dogfood-log.md](../dogfood-log.md), round 1): hooks added to `.claude/settings.local.json` take effect mid-session on the next tool call; a `ConfigChange` hook fires when the agent edits that file with its own Edit tool; the desktop app reports `permission_mode` as `auto`; and a hook run costs about one second here, mostly import time (task P2.6e). Still open: exit codes on this version, `ask` behaviour, `bypassPermissions`, the remaining payload shapes, and a crashing hook.
- **`ask` for council and human** is a stopgap. Whether `ask` works in an unattended `-p` run is unconfirmed, so `non_interactive: true` makes the hook `deny` instead.
