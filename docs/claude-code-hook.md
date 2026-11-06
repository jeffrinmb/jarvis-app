# Claude Code hook

`agentctl hook` connects Claude Code to the [policy gate](adr/0002-policy-evaluation-and-fail-closed-rules.md). Before each tool call it asks the gate whether the call is allowed, and it records what it decided in the audit ledger. The design and the facts about Claude Code's hook interface are in [ADR 0003](adr/0003-claude-code-hook-interface.md).

**Status.** Built and tested with realistic payloads, but not yet run inside a live Claude Code session (task P2.6c). Start in log-only mode and read what it records before you trust it.

## What it does

| Event | What the hook does |
|---|---|
| `PreToolUse` | Builds an action from the **real tool call** (tool, arguments, paths, edit size), asks the gate, records the decision, and in enforce mode turns it into a Claude Code decision |
| `PostToolUse`, `PostToolUseFailure` | Records that the call ran or failed, and updates what the session has been exposed to |
| `Stop`, `SubagentStop`, `SessionEnd` | Records the session's model calls (tokens, cache use, cost) from the transcript; never blocks |
| `SessionStart` | Starts a fresh session record (a resumed session keeps its exposure) |
| `ConfigChange` | Records edits to Claude Code settings, and in enforce mode blocks them so the hook cannot be removed mid-session |

## Modes

- **`log-only` (default).** Records every decision and **never blocks**. It always exits 0 and prints no decision, so a problem in the hook cannot stop your work. Use this first, to see how often the gate would have intervened.
- **`enforce`.** Blocks according to the gate, and fails closed. Needs Claude Code **2.1.295 or later**, because older versions let a crashed, timed-out or missing hook through. Check with `agentctl hook doctor --mode enforce`.

In enforce mode the gate's answer becomes:

| Gate says | Claude Code gets |
|---|---|
| allow | Nothing. Claude Code's own permission rules and prompts still apply. The hook never says "allow", because that would skip prompts the gate knows nothing about |
| council or human | `ask` (a person answers in the terminal), or `deny` when `non_interactive: true` is set. This is temporary until the approval flow exists (P2.7) |
| deny | `deny`, with the reason |

A gate `deny` holds in every Claude Code permission mode, including `bypassPermissions`. The hook never rewrites a tool's input.

## Set it up

1. **Install the command** so `agentctl` is on the `PATH` that Claude Code uses (for example `uv tool install .` from this repository), or use `--runner uv` below.
2. **Copy the starting files** into the project you want to watch:
   - `examples/claude-code/policy.yaml` to `.agents/policy.yaml`
   - `examples/claude-code/tool-registry.yaml` to `.agents/tool-registry.yaml`
   - optionally `examples/claude-code/claude-code.yaml` to `.agents/claude-code.yaml`
3. **Check the machine:**

   ```bash
   agentctl hook doctor
   ```

4. **Print the hooks block** and paste it into `.claude/settings.local.json` (or `~/.claude/settings.json`). The command does not edit any file, because changing hook configuration is itself a security-relevant act:

   ```bash
   agentctl hook snippet
   ```

   Useful options: `--runner venv` runs the project's `.venv` Python directly (fastest), `--runner uv` goes through `uv run --project`, `--minimal` registers only `PreToolUse`, `SessionStart` and `ConfigChange` (about half the cost), and `--mode enforce` is for later and needs `--policy-source git` or `directory` (see below). Hooks added to `.claude/settings.local.json` take effect on the next tool call, with no restart.
5. **Start a Claude Code session in the project**, work normally, then check the ledger:

   ```bash
   agentctl verify --ledger .agents/ledger/events.jsonl
   ```

## Review what it recorded

```bash
agentctl hook report
```

It verifies the ledger first, then summarises the decisions: how often the gate would have intervened, by tool and by rule, which Claude Code tools have no mapping, and a list of what would have been stopped with example paths, so you can judge whether each intervention is sensible. Add `--json` for a machine-readable form. Findings from the first round are in the [dogfood log](dogfood-log.md).

## Where the policy is read from

The hook reads the policy, tool registry, project file and its own adapter config from a **policy source** ([ADR 0004](adr/0004-trusted-policy-source.md)), because an agent that can edit those files can rewrite the rules it is judged by.

| `--policy-source` | Reads | Enforce mode |
|---|---|---|
| `worktree` (default) | the project directory, which the agent can write | refused |
| `git` | the files at a git ref or commit (`--policy-ref`, default `origin/main`) | allowed |
| `directory` | a directory outside the project (`--policy-dir`) | allowed |

Pin a **full commit SHA** with `--policy-ref`: a branch name can be moved by an agent that has a shell, until the sandbox (P4.2) stops it. The options can also come from `JARVIS_POLICY_SOURCE`, `JARVIS_POLICY_REF` and `JARVIS_POLICY_DIR`. They are deliberately not settable in the adapter config, which the agent could edit. `agentctl hook snippet --policy-source git --policy-ref <sha>` writes them into the settings block, and `agentctl hook doctor` shows which source and file hashes are in use.

## Running the hook through a local server

```bash
agentctl hook snippet --server --runner venv     # registers the hook with --server
agentctl hook serve --status                     # exit 0 if the server for this project is running
agentctl hook serve --stop                       # stop it (it also exits after 30 idle minutes)
```

With `--server` the registered command asks a small local server for the decision instead of loading everything itself, which cut the per-call time from about 480 to 570 ms to about 230 to 280 ms on the machine it was measured on ([ADR 0012](adr/0012-hook-server.md)). The first call starts the server in the background (one per project and settings, and it restarts when the code changes). The decisions and ledger records are the same, because the server runs the same code. A call is never decided twice: the client sends its headers first and the call itself only after the server says it will read it, so a server that is stopping and drops a request unread is simply replaced and the call asked again, while a server that took the call and then died makes the call fail (enforce mode blocks it; log-only lets it through with a note).

On Windows the server runs under `pythonw.exe`, so no console window opens. If the server is killed or the machine restarts, the next call notices the dead server, replaces it and carries on. It is safe to leave on: if the server cannot be reached the command decides for itself, as before, just slower; if a request was sent and no answer came back it never decides a second time (enforce mode blocks that call, log-only lets it through with a note). The server listens on `127.0.0.1` only, needs a secret kept in the per-user state directory, checks the `Host` header and accepts only JSON. **An agent running as you can read that secret and talk to the server or kill it**, the same limit as the kill switch until the sandbox (P4.2). It is not yet tested on Linux or macOS, and Claude Code's own `http` hooks, which would remove the process start entirely, are a follow-up (P2.6g).

## Approvals

When the gate answers `human` or `council` for something that may be approved, it writes an **approval request** from its own verdict ([ADR 0009](adr/0009-approval-flow.md)). A person answers it:

```bash
agentctl approvals list                      # what is waiting
agentctl approvals show req-0123456789ab     # the rules, paths, size and proposed scope
agentctl approvals approve req-0123456789ab --key ~/.ssh/id_ed25519 [--paths 'src/**'] [--max-diff-lines 100] [--hours 4]
agentctl approvals reject req-0123456789ab --by alice
```

An approval names one task, exact tools, path globs, a size limit and an expiry, and the gate re-checks all of it. It never lifts a refusal, the lethal-trifecta, production, escaping-path or tainted-session gates, or a shell command (those stay with Claude Code's prompt, where the command is on screen). A no-go zone needs two different people; high-risk and irreversible tools take only a single-use approval of the exact call. Set `approvals.approvers` in the policy so only named people count. In enforce mode, a call a person allows in Claude Code's own prompt is recorded as an attributed approval, as evidence only. An unattended denial names the request a person can answer.

**Signed approvals** ([ADR 0010](adr/0010-identity-and-signing.md)). With `approvals: require_signatures: true` in the policy, `approve` signs with your SSH key (`--key`) and the gate counts the approval only if the signature verifies against `.agents/allowed-signers`, read from the protected policy source (see `examples/claude-code/allowed-signers` for the format). The approver is whoever that file names for your key, not a name you typed. `hardware_key_for_four_eyes: true` additionally demands a security key for no-go zones. Enforce mode refuses a policy that does not require signatures, and `agentctl hook doctor` runs a real sign-and-verify. **A software key that an agent running as you can use can sign as you**; use a hardware key for anything that matters, and note that the touch requirement has not been tested with a real key yet (P2.9d).

## Stopping everything, and limits

```bash
agentctl halt      # deny every action until `resume`
agentctl status    # exit 1 while the kill switch is on
agentctl resume
```

The kill switch is a `HALT` file in the same per-user state directory the hook uses, outside the project ([ADR 0005](adr/0005-budgets-loop-breaker-and-kill-switch.md)). While it exists the hook's gate denies every call, reads included; in log-only mode it records that and still never blocks. If the file cannot be read it counts as on.

The `anomalies` block can also flag a call that names a host no earlier call named, one call that carries too many bytes outward, and a task whose outward total grows too big (`new_domain`, `max_call_bytes`, `max_task_bytes`; see `examples/claude-code/policy.yaml` and [ADR 0006](adr/0006-anomaly-rules.md)). The hook finds hosts only when they are written as URLs in a `WebFetch` or MCP call, and records the host and the call's input size, never the input.

The policy can also carry a `budgets` block (see `examples/claude-code/policy.yaml`): calls per task and per day, and a loop breaker for the same call repeated. A tripped limit sends the call to a human (or denies, with `on_exceed: deny`). An `anomalies` block adds detectors for repeated denials (however the retries vary), a tool never used before and bursts of calls, each recorded as an `anomaly_detected` event and set to alert, pause for a human or deny ([ADR 0006](adr/0006-anomaly-rules.md)). Counting from the ledger means a budget or detector only holds while the ledger is intact (G4). An agent with a shell could delete the `HALT` file until the sandbox (P4.2) is in place.

## Token and cost ledger

The hook does not see token counts in its input, but every event carries `transcript_path`, so on `Stop`, `SubagentStop` and `SessionEnd` it reads the usage in Claude Code's session transcript and writes one `model_call` row per message ([ADR 0008](adr/0008-token-capture-from-transcripts.md)). The transcript format is **not documented by Anthropic**: the reader takes only numbers, a model name and a message id, de-duplicates by message id (one response is written as several records), and skips anything it cannot trust, so a format change gives fewer rows and never wrong ones. It is safe to run repeatedly. Set `prices_path` in the adapter config to a price table (read through the policy source) to compute costs; otherwise costs are recorded as unknown, which the report flags. `--minimal` snippets do not capture usage. You can also write rows by hand: `agentctl tokens record --agent A --on-behalf-of H --model M --tokens-in N --tokens-out N [--tokens-cached N] [--cost-usd X | --prices examples/prices.yaml]` writes one row, and `agentctl tokens report [--by model|task|agent|stage|day]` summarises them, flags calls whose cost is unknown and shows the cache hit rate. See [ADR 0007](adr/0007-token-and-cost-ledger.md). The example price table is illustrative, not real prices.

## A long ledger

The gate counts usage, diff totals and approvals from earlier records, and reading a long ledger on every call used to make each decision slower and slower (about 166 ms at 10,000 records and 631 ms at 40,000). The hook now rotates the ledger: when a write finds the active file at `ledger_rotate_bytes` (default 1,000,000, in `.agents/claude-code.yaml`; unset turns it off), the file is moved whole into `events.archive/` and a new one starts with a `ledger_rotated` record that continues the same hash chain and carries a summary of everything before it. A decision then reads only the active file, and costs about the same at 40,000 records as at 100. Nothing is deleted or rewritten, and `agentctl verify`, the report, `tokens report`, the OpenTelemetry export and signed checkpoints read every segment as one ledger. `agentctl ledger status` lists the segments and `agentctl ledger rotate` rotates by hand. The summary never changes a decision, only how much is read: when a task's loop, denial or burst window does not fit inside it, the gate reads the whole ledger instead. See [ADR 0014](adr/0014-ledger-segments.md).

## Signed ledger checkpoints

`agentctl checkpoint --key ~/.ssh/id_ed25519` signs the ledger's current head and appends it to a file outside the ledger (by default in the per-user state directory; `--checkpoints FILE` chooses another place, such as a synced folder or a file you commit). `agentctl verify --checkpoints` then fails if a signed checkpoint contradicts the ledger (truncated, rewritten, another ledger, or a bad or unlisted signature) and only warns when there is none or the newest is older than 7 days. See [ADR 0010](adr/0010-identity-and-signing.md). It does not detect a forged routine event, and nothing is protected after the newest checkpoint.

**Milestones and staleness (P2.9e).** `agentctl checkpoint --key KEY --milestone` also writes the same signed line to `.agents/checkpoints/<ledger id>.jsonl` in the project. It holds only the ledger id, the head's sequence number and hash, a time and a signature, never event content, so it is safe to commit. Commit it through a pull request, so that deleting it later shows in git history; `verify --checkpoints` reads both the per-user file and this one. `agentctl hook report` now ends with a `Checkpoints:` line (and a `checkpoints` key with `--json`): how many are good, the newest one and how many records came after it, a warning when there is none or the newest is older than 7 days, and a failure (exit 1) when a signed checkpoint contradicts the ledger. It says `not checked` when the project has no `.agents/allowed-signers`. **CI guards the committed file (P2.9f):** `agentctl checkpoint-history --base SHA` (run by the `checkpoint-history` job on every pull request) fails if a checkpoint file was removed, an existing line was changed, reordered or removed, or a new line is unreadable or not signed by a key the base commit lists. Run it yourself with `--base origin/main`. It cannot compare a new line with your real ledger; `verify --checkpoints` does that.

**Signing stays manual, on purpose:** an unattended job would need a key without a passphrase, which anything running as you could also use, so the report reminds you and you sign when it asks.

## What is recorded

Every decision records: the tool and its registry id, the project-relative paths, the verdict and the rule ids that produced it, the session id, the permission mode, the tool-use id, whether the session was tainted, a fingerprint of the tool and paths (for the loop breaker), the policy source (for example `git:<ref>@<commit prefix>`) and whether it is protected, and a SHA-256 of the policy, registry and project files in force. The ledger is hash-chained, so edits to it are detected.

**Never recorded:** file contents, command arguments, tool output or error text. One optional exception: with `record_command_prefixes: true` in the adapter config (off by default; on in this repository while P2.10b is open) the first one or two plain words of each shell command are recorded, such as `git add`, so the shell prefixes that may be allowed can be chosen from evidence; `agentctl hook report` counts them. Never a flag, a path, a value or a directory ([ADR 0015](adr/0015-prompt-rate-decisions.md)). As a second layer ([ADR 0013](adr/0013-redaction-and-secret-scanning.md), P2.12), the ledger replaces secrets and personal data (emails, card numbers) in an event's free-form `data` with `[REDACTED:kind]` before it is written, and the hook scans each call's input for secrets: when one is found in the input of a tool that writes, runs commands or sends data, a person decides (`builtin:secret-in-input`), and the ledger records only the kind, such as `github-token`. Set `secrets_in_input: alert` in the policy to record without stopping. It matches known formats, so it reduces the risk and does not remove it, and a file that contains a fake token will ask a person.

## How a call is classified

- **Tools.** `Read`, `Glob`, `Grep` map to `fs.read`; `Write`, `Edit`, `MultiEdit`, `NotebookEdit` to `fs.write`; `WebFetch`, `WebSearch` to `web.fetch`; `Agent` to `agent.spawn`. Any other tool, and any MCP tool that is not in your registry, is **denied**. MCP trust is judged by the tool you register, never by its name.
- **Shell.** A command is a read (`shell.read`) or a test run (`shell.test`) only if it is a single plain command that starts with an allowed prefix, such as `git status` or `uv run pytest`. Anything with chaining, pipes, redirects, substitution or unusual arguments is a general shell call (`shell.run`), which needs a human. Two very common habits are understood: a leading `cd <dir> &&` into the project is ignored, and trailing `2>&1`, `| head -N`, `| tail -N` and `| wc -l` are ignored, because they cannot change what the command does. A `cd` anywhere else, a variable, or any other pipe target keeps it a general shell call. The `description` field the agent writes is ignored.
- **Bookkeeping tools.** Claude Code's own `ToolSearch`, `Skill`, `Task*`, `TodoWrite`, plan-mode and question tools map to an internal read-only tool. Other MCP tools must be registered, and their output is untrusted unless you list them under `trust_overrides`.
- **Paths.** Absolute paths inside the project become relative. A path outside the project, or one that climbs out with `..`, stays absolute and goes to a human.
- **Shell chains.** A chain `a && b && c` counts as a read or test command only when every part already is one and the line has nothing else in it (no `;`, pipe, redirect, substitution, second `&` or empty part, at most eight parts); a leading `set -o pipefail &&` is ignored. Anything else is a general shell command and goes to a person.
- **Edit size.** Counted from the real arguments as an upper bound. The size limit in an `auto_approve` rule is cumulative per task, and the hook treats a whole session as one task, so a long session passes it (this repository's cap is 2,000 lines, decided in ADR 0015; the example policy keeps 400). Unknown sizes (for example a shell command) never help a call get approved.
- **Exposure.** A session becomes *tainted* when a call that brings in untrusted content is **requested** (web fetches and searches, MCP tools, reads under third-party paths, shell commands that look like they fetch from the network), and it is marked as having seen *restricted data* when it reads a secret-looking file. This is applied at request time, not when the result arrives, so a lost `PostToolUse` cannot hide it. The gate then tightens: for example, edits after reading untrusted content need a human, and untrusted content plus restricted data plus an outbound call is the lethal trifecta.

## Limits to know about

- The hook only knows what each call and its own state tell it. A shell command is opaque, so keep `shell.run` behind a human. Real containment needs the sandbox (P4.2).
- Session state lives in a user-level directory outside the project, but it is an ordinary file. An agent with shell access could in principle edit it. The gate treats the framework's own files as human-gated paths in the example policy, and the sandbox will close this properly.
- In `enforce` mode, **blocking `ConfigChange`** also stops you changing your own Claude Code settings during a session. Turn it off with `block_config_changes: false` if that gets in the way.
- Parallel tool calls in one turn may run their hooks at the same time, so exposure from one call may not yet be visible to another in the same batch.
- **Cost per call.** Each hook run starts a Python process, and almost all of its time is importing (the Python start, pydantic, YAML). On the Windows machine it was measured on that was about **0.5 to 1.2 seconds**, depending on how busy the machine was (see [the dogfood log](dogfood-log.md), rounds 3 and 4). Registering more events multiplies it. Use `--minimal` (three events instead of seven, so about half the calls) and `--runner venv`, and see the next section for the local server, which roughly halves it again. `tests/test_hook_imports.py` stops new work from adding to the in-process cost by accident.
- Unattended `claude -p` runs trust the folder, so a repository's own `.claude/settings.json` hooks run without a prompt. Review `.claude/` before running unattended on a repository you did not write.

## Configuration

`.agents/claude-code.yaml` (see `examples/claude-code/claude-code.yaml`). Everything has a safe default: the mode, the role the session acts in, the environment (never production), who is accountable (`on_behalf_of`, then `$JARVIS_ON_BEHALF_OF`, then the OS user), which paths are untrusted or sensitive, which vetted tools' output you trust, and the shell prefixes treated as read or test.

Environment variables: `JARVIS_HOOK_MODE`, `JARVIS_ON_BEHALF_OF`, `JARVIS_TASK_ID` (cumulative limits are per task; the default is one task per Claude Code session), `JARVIS_STATE_DIR`, and `CLAUDE_PROJECT_DIR` (set by Claude Code).
