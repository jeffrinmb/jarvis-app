# ADR 0015: The taint rule, the edit cap and the shell prompts (P2.10a)

- Status: Accepted
- Date: 2026-11-06
- Deciders: project owner (all four choices below, made in a design round with the data in front of them), Claude Code (measurement and implementation)

## Context

Enforce mode (P2.10) needs a low intervention rate for ordinary work, and the first dogfood round found 62% of decisions flagged after one web fetch, which pointed at the taint rule. P2.10a asked how strict that rule should be and which shell prefixes to allow. Instead of deciding from that one short session, I measured the live ledger: 1,020 `PreToolUse` decisions over ten days (2026-10-27 to 2026-11-06), recorded in log-only mode, and for each option counted what it would have changed, using the matches each decision recorded. Treat these as one maintainer's real work, mostly one long session, not a general result.

| What | Decisions | Share |
|---|---|---|
| Would have asked a person (human or council) | 860 | 84% |
| Made in a session that was tainted | 986 | 97% (one long session, tainted early and never cleared) |
| Asked **only** because of the policy's taint rule | 14 | 1.4% |
| Would-ask rate with every taint rule removed | 846 | 83% |
| General shell commands (`shell.run`, always a person by design) | 598 | 59% |
| Edits to `src`, `tests` and `docs` asked only because the task's running diff total passed 400 lines | 103 | 10% |
| Writes to a path outside the project | 51 | 5% |
| Web fetches (council) | 35 | 3% |

The 62% figure was real for that short session, but it does not describe where prompts come from over time: the taint rule is cheap, and two other things are expensive.

## Decisions

1. **Keep the taint rule as it is.** It costs about 1% of decisions and still stops writes, shell and outbound calls after untrusted content. Relaxing it (only for medium risk and up, clearing it on approval, removing it) would change the would-ask rate by one point at most. A test pins the rule in this repository's policy so a quiet change fails and is reviewed.
2. **Raise the edit cap from 400 to 2,000 lines per session** in this repository's `.agents/policy.yaml`. The cap is cumulative per task, and the Claude Code hook treats a session as one task, so a long session passed 400 lines after 37 of 999 decisions (it ended at 17,343) and every later edit to an allowed folder asked. 2,000 still stops a session rewriting thousands of lines in small pieces. **The gain is modest, and I should say so:** from the recorded running totals, 2,000 would have let through 40 of the 106 edits that asked only because of the 400-line cap (about 4 points of the would-ask rate), because that one session wrote 17,343 lines and passes 2,000 at decision 183. A cap of any size prompts in a session that long; the lasting fix is a task that is a ticket, not a session. The orchestrator (P4.1) will make a task a ticket, where a cap fits naturally; this is a stopgap until then. A policy number, not a code change, so it is a reviewed edit of a protected file.
3. **Recognise safe chains of shell commands.** `a && b && c` is `read` or `test` only when **every** part is already a listed read or test command, with nothing else in it: no `;`, pipe, redirect, substitution, second `&` or empty part, no newline hidden as padding, at most eight parts. A leading `set -o pipefail &&` is ignored. `&&` only runs the next part if the previous one succeeded, so such a chain can do nothing its parts could not do one at a time, and `shell.run` itself is still never auto-approved. A randomised test checks that a chain containing anything unsafe is never classified as safe.
4. **Record a two-word command prefix, for evidence.** The ledger records no command text, so the shell prompts (59% of decisions) cannot be analysed and prefixes would be chosen blind. With `record_command_prefixes: true` in the adapter config (on in this repository's `.agents/claude-code.yaml`, off by default everywhere else) the hook records the first one or two plain words of each command (`git add`, `uv run`), never arguments: the second word only if it is a plain word (not a flag, a path or an assignment), the first word without its directory, `cd` without its target, anything else as `(other)`, at most ten per call. Only the first line of a command counts: the first version also split on newlines, and the first day of data showed lines of inline scripts (`assert old`, `mapping.py`) recorded as if they were commands, which is both noise and more of a script's content than was meant to be kept. `agentctl hook report` shows the counts. This narrows the rule that the ledger never records command text, so `AGENTS.md` now says so; it is meant to be turned off again once prefixes have been chosen (P2.10b).

## Consequences

- Enforce mode is still not practical for this workflow: shell commands are 59% of decisions. Chains (decision 3) should help, by how much is unknown until the next report, and the prefixes (decision 4) will say what else to allow. P2.10 stays blocked until the rate is measured again.
- Raising the cap loosens one safety setting for this repository by the owner's decision. The default for new users (the example policy) stays at 400, with a comment about what it counts.
- Paths outside the project (5%) are a separate cost. Some are session scratch folders. Not decided here.
- A prefix can still reveal what someone is doing (`ssh`, a tool name). It carries no arguments, paths or values, and the ledger's redaction (ADR 0013) applies as well, but it is a change to what is recorded and should be reviewed as one.

## Alternatives considered

- **Relax the taint rule** (risk threshold, clear on approval, remove): rejected on the data. Little to gain, and it weakens the defence against instructions smuggled in through fetched content.
- **Add prefixes now from memory:** rejected in favour of measuring first.
- **Keep the 400-line cap:** about 10% of decisions in a long session ask for no safety gain beyond what 2,000 gives.
- **Reset the cap when a person approves:** attractive, but it needs new state and an ADR, and the orchestrator will make the question moot.
