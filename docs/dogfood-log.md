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

## Round 2: 2026-10-28, token capture (P2.2a)

Registered `Stop`, `SubagentStop` and `SessionEnd` in the git-ignored `.claude/settings.local.json` and ran two trivial Explore subagents during a normal session.

- **`Stop` and `SubagentStop` fire** in the desktop app and the hook captured usage on both. `SessionEnd` was not observed.
- **A real bug, found only by running it.** The first build read `transcript_path` on `SubagentStop`. That is the main session's transcript, so 454 main-session messages were recorded under the subagent's identity and the subagent's own calls were missed. The documentation (the `SubagentStop` section of the hooks reference) names `agent_transcript_path` for the subagent's own file. Fixed; the second subagent's two messages then matched its transcript file exactly (8,329 in, 25,269 cached, 177 out; then 710, 33,596, 51).
- **Dedupe confirmed on real data:** this session's transcript had 1,309 usage records for 434 distinct message ids when first read, so summing records would have been about three times too high.
- **Sidechain:** the main transcript held no `isSidechain` records; subagent records are only in the subagent files.
- **Cost:** unknown for every row, because no price table is configured here (`prices_path` unset). The report flags this.
- **Whole-file re-read:** not measured yet. The session transcript was about 17 MB at the time; the per-turn cost has not been timed.

Lesson: the unit tests had asserted the wrong behaviour for subagents, because I had written them from an assumption. Only a live run (and then the primary documentation) showed it.

## Round 3: 2026-11-04, hook latency (P2.6e)

Measured with the way Claude Code runs the hook (`.venv/Scripts/python.exe -m jarvis.cli hook run --mode log-only`), against a throwaway project, on the same Windows machine as round 1. Medians of 15 runs, before and after, back to back.

| Event | Before | After | Change |
|---|---|---|---|
| `python -c pass` (for scale) | 176 ms | 189 ms | noise |
| `PreToolUse` | 1272 ms | 1258 ms | -14 ms |
| `PostToolUse` | 1258 ms | 1248 ms | -10 ms |
| `SessionStart` | 1247 ms | 1198 ms | -49 ms |
| `Stop` (200 transcript messages) | 1301 ms | 1201 ms | -100 ms |

Run to run the spread is about 60 ms, so only the `Stop` and `SessionStart` changes are clearly real.

**Where the time goes** (fresh interpreter, medians): bare Python 192 ms; YAML adds about 60 ms; importing pydantic adds about 230 ms and **the first model class adds about 390 ms more**, because pydantic loads its core lazily on first use; all of our other 45 or so models together add about 80 ms; the rest is our own modules and the standard library pydantic pulls in (`importlib.metadata`, `zipfile`, `bz2`, `zoneinfo`). So about 1.1 s of a 1.2 s call is the Python start plus pydantic plus YAML.

**What was done:** the entry point was split so `hook` does not import the other commands, and the OTLP sender (`urllib`, `http`, `socket`, `ssl`, `email`), `subprocess`, `tempfile` and `secrets` load only when used. Before this the hook paid about 130 ms for the sender alone on every call.

**What did not help:** deferring pydantic's model construction (`defer_build`) gave no measurable gain, because the hook uses most of the models it imports. It was tried and reverted.

**Conclusion:** importing less is exhausted at a few percent. A per-call Python process cannot get under about one second here. Reaching the 200 ms target means not starting Python per call (P2.6f). Until then `--minimal` halves the number of calls.

Method note: the first `git stash` used to swap code for the "before" run hit a conflict on the renamed file when restoring. The working tree was verified by diff and tests afterwards and nothing was lost, but a `git worktree` is a safer way to compare.

## Round 4: 2026-11-04, a local hook server (P2.6f)

**A correction to round 3.** The absolute times in round 3 (about 1.25 s per call, bare Python 0.19 s) were taken while this machine was slow. Measured again the same afternoon with the same method, bare Python took 107 ms and a hook call about 0.47 to 0.50 s. The machine's speed varied by a factor of two or three between hours, so the *ratios* are meaningful and the absolute figures are not. Round 3's conclusion that "a per-call process cannot get under about one second" was true of that hour only. Its before/after comparison was run back to back and still stands (a few percent).

**Measured with the server (back to back, same session; medians of 11 runs):**

| Event | Without `--server` | With `--server` |
|---|---|---|
| bare Python (for scale) | 112 ms | 152 ms |
| `PreToolUse` | 479 ms | 227 ms |
| `PostToolUse` | 500 ms | 233 ms |
| `SessionStart` | 556 ms | 228 ms |
| `Stop` (200 messages) | 550 ms | 256 ms |

The warm handler itself takes 21 ms for `PreToolUse` and 9 ms for `PostToolUse` in-process, and about 51 ms over loopback HTTP with a naive client. The client's own cost over a bare interpreter fell from about 370 ms to about 75 ms (its imports were trimmed from about 80 ms to about 40 ms by dropping `hashlib`, `pathlib` and `typing` from its path).

**What went wrong on the way:** my first client imported `hashlib` and `pathlib` and cost about 270 ms per call; profiling showed 37 ms in `jarvis.hook_paths` alone. The benchmark's own `--stop` omitted `--mode`, so it did not match its server's spec and left servers running; I stopped them by hand. On Windows each server appears as two processes (the venv launcher and its child).

**Not measured or tested:** Linux and macOS; a long session; Claude Code's `http` hooks (P2.6g).

**Live use, the same afternoon.** The maintainer's hook was switched to `--server`. Two defects appeared that the unit tests had not caught: a terminal window opened (the venv launcher's console child, see ADR 0012), and a server that was killed hard was never replaced because the client trusted stale discovery files. Both are fixed and were re-verified under the real hook: no window, survival across calls, and a replacement server started at once after a hard kill. One thing is not explained: an attempt to start the server with `CREATE_NO_WINDOW` alone was dead within about 30 seconds; I kept the flags that survived instead of chasing it.

Method lesson: my own cleanup commands (killing servers to reset state) left stale files and made a working change look broken, twice. Reset state with the product's own `hook serve --stop`, or expect stale files.

## Round 5: the first daily check and a missing subagent transcript

The scheduled check's first report (2026-11-05) flagged "no `model_call` rows since 11-04 11:46Z". That was a false alarm: the check ran before the morning's first turns finished, and the ledger had token rows from 03Z and 04Z that day. The quiet stretch on 11-04 was real idleness (one policy decision between 11Z and 03Z).

The report also noticed a recurring `error`: "transcript path is not a regular file", on `SubagentStop`, three times in a week. The hook was reading the subagent's own transcript (`agent_transcript_path`) and Claude Code had no file at that path, for three of the five subagents whose files I could compare. It is a gap in the token record, not a fault, so a missing subagent transcript is now a `note` (`usage_capture_skipped`) with no path in it, and `error` is kept for real failures, including a missing main transcript. Not established: why Claude Code has no file for some subagents (asynchronous writing or clean-up are both consistent with the evidence).

## Round 6: signing the first checkpoint

Two defects showed up the first time the maintainer signed a real checkpoint, both in code that its tests had passed.

- **`--key ~/.ssh/key` failed in PowerShell and cmd** with "signing key not found: ~/.ssh/key". Bash expands `~` before the program sees it; PowerShell and cmd pass it through, and the program used the text as a path. `signing.sign` now expands `~` itself, so every command that takes a key (`checkpoint`, `approvals approve`, the doctor) behaves the same in every shell. Checked for real in PowerShell, cmd and Git Bash, in both the `--key ~/...` and `--key=~/...` forms, with a throwaway key in a temporary home folder. Not covered: the `~user` form (Python does not support it on Windows).
- **One checkpoint was counted twice** ("2 of 2 good"). `--milestone` writes the same signed line to the per-user file and to the project, and the report read both. Identical lines are now counted once. The earlier tests had removed one copy before checking, so they never saw it.

A fixture lesson: Windows OpenSSH refuses a private key that other users can read ("Bad permissions"), and my first throwaway key inherited loose permissions, which looked like a failure of the fix until I ran `ssh-keygen` directly.

## Round 7: a ledger that slows every call

Measuring the hook against ledgers of growing size showed the cost per decision rising in a straight line: 19 ms at 100 records, 44 ms at 2,000, 166 ms at 10,000 and 631 ms at 40,000, while the live ledger gained about 1,000 records a day. The gate parsed and folded every record on every call. The fix (rotation with a carried summary, ADR 0014) brought it to a flat 35 to 46 ms. Two lessons: the first benchmark wrote every event at the same instant, so the gate correctly fell back to reading everything and the result looked like no improvement until I spaced the events out, and a mutation check found that a spent single-use approval could come back to life after a rotation, which the first version of the tests did not cover.

## Round 8: a call that reached a stopping server

The first scheduled check ended one benchmark call with "the answer was not HTTP; the call was allowed". It was easy to dismiss as noise, and in log-only mode it was harmless, but in enforce mode the same event blocks a legitimate call. I could reproduce it on demand (calls within about 0.1 s of a stop failed half the time) and the cause was in the design, not the timing: the client could not tell a request the server dropped unread from one it took and lost, and had to treat both as the second kind. The fix is a handshake that makes the difference visible. Two lessons. First, a suspicion I had about the same code (that a late shutdown deletes the replacement server's files) did not reproduce, and I said so and added the guard as hardening, not as a fix. Second, an existing test ("a server that accepts and closes without answering") had been modelling the wrong case; it had to be split into "dropped before reading" and "took the call and died", and a mutation check found that the reset-after-agreement case was untested.

