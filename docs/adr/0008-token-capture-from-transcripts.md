# ADR 0008: Capturing token usage from Claude Code transcripts

- Status: Accepted
- Date: 2026-10-28
- Deciders: project owner (task P2.2a)

## Context

The token ledger ([ADR 0007](0007-token-and-cost-ledger.md)) needs one row per model call, but the Claude Code hook never sees token counts: no hook input carries usage. Every hook input does carry `transcript_path`, and Claude Code's session transcript has per-message usage.

**What was verified, and how.**

- *Primary (Claude Code hooks reference, read 2026-10-28):* `transcript_path` is "path to conversation JSON", the file "is written asynchronously and may lag the in-memory conversation", and hooks that need the last assistant text should use `last_assistant_message` on `Stop` and `SubagentStop`. The content format of the transcript is **not documented**, and no hook input carries token usage in the part of the page read (about the first 100,000 of 282,000 characters; the `Stop` and `SessionEnd` sections were not read; the `SubagentStop` section, read afterwards, documents `agent_transcript_path`).
- *Secondary (third-party descriptions found by search):* one JSON object per line, assistant records with `message.usage` holding `input_tokens`, `output_tokens`, `cache_creation_input_tokens` and `cache_read_input_tokens`, and a warning to de-duplicate by message id.
- *Our own observation (Claude Code 2.1.x on this machine, one session of 1309 usage records):* the same four fields, plus `message.id` and `message.model`; **1309 records for only 434 distinct message ids**, because one API response is written as several records that repeat the same usage. Summing every record would count about three times too much. Records of one id were identical, and the last was never smaller than an earlier one.

Nothing here is a contract Anthropic has promised, so the reader must fail toward fewer rows, never toward invented numbers.

## Decision

1. **Capture on `Stop`, `SubagentStop` and `SessionEnd`.** The hook reads the transcript at `transcript_path` and appends a `model_call` row for each message id that has no row yet. These events are non-blocking: capture never blocks in any mode, and a failure is recorded as an `error` event.
2. **De-duplicate by message id**, keeping the most complete record (the largest output count, later on a tie). Idempotency is also enforced against the ledger: a row is written only if no row exists for that session and message id (`data.session_id`, `data.message_id`). A late event, a repeated event, or a transcript that lagged behind the last turn simply catches up on the next event.
3. **Read only numbers, a model name and a message id.** Message content is never parsed into a row or stored. A record whose counts are not plausible integers, or whose id or model does not match a strict pattern (this drops `<synthetic>` placeholders), is skipped and counted, not repaired.
4. **Bound the read.** The path must end in `.jsonl`, be a regular file, and be at most 256 MiB; a line over 8 MiB is skipped; at most 20,000 new rows are written per event. The path comes from Claude Code's hook input, but only counts are taken from it, so a wrong path cannot leak content.
5. **Field mapping.** `tokens_in` is `input_tokens` plus `cache_creation_input_tokens`, `tokens_cached` is `cache_read_input_tokens`, `tokens_out` is `output_tokens`. Cache-creation tokens are also kept in `data.cache_creation_tokens`. Folding writes into `tokens_in` understates their price a little (ADR 0007); a `reported` cost would be exact but the transcript carries none.
6. **Cost comes from a price table, read through the policy source.** `prices_path` in the adapter config names it. An agent-writable table could be edited to understate cost and so defeat cost budgets, so in enforce mode an unprotected source is not used. A missing, broken or unusable table means the cost is `unknown`, which the report flags; `data.prices_sha256` records which table priced a row.
7. **The row's time is the message's time**, unless that is in the future, so a report grouped by day is right even when capture happens at session end.
8. **A subagent's calls are in its own file.** `SubagentStop` carries `agent_transcript_path` (the subagent's own transcript, in a `subagents/` folder next to the session's) and `transcript_path` (the main session's). On `SubagentStop` the hook reads **only** `agent_transcript_path` and records under the subagent's identity (`<agent>:<type>:<id>`); on `Stop` and `SessionEnd` it reads `transcript_path` and records under the session's agent. A row also records `data.hook_event` and the transcript's file name (not its directory).
9. **`--minimal` snippets do not capture usage**, because `Stop`, `SubagentStop` and `SessionEnd` are registered only in the full set.

## Consequences

- Token and cost reports now fill in on their own for Claude Code sessions. The cost is unknown until a price table is configured.
- If Claude Code changes the transcript format, capture degrades to fewer rows. Today nothing alerts when a transcript yields none; `agentctl tokens report` simply shows less. A check against the installed Claude Code version belongs with P2.6c.
- Each event re-reads the whole transcript (one pass, a cheap string filter before JSON parsing). It runs once per turn, not per tool call. A long session makes this slower; an offset kept in session state is the fix if it matters (P2.6e).
- **Verified live (2026-10-28, Claude Code 2.1.276 in the desktop app):** `Stop` and `SubagentStop` both fire and capture; a subagent's two messages in `subagents/agent-<id>.jsonl` were recorded with exactly the file's counts under the subagent's identity; the main transcript contains no `isSidechain` records, so nothing is double counted. `SessionEnd` has not been observed firing yet.
- **A bug found by that run:** the first build read `transcript_path` on `SubagentStop`, which is the *main* transcript, so it recorded the whole main session under the subagent's identity and missed the subagent's own calls. Counts were right, labels were wrong. Fixed by decision 8; tests assert that the main transcript is never attributed to a subagent. The local dogfood ledger still holds those 454 rows (a ledger is append-only); a `note` event marks them.
- Usage reported by the transcript is whatever Claude Code wrote, not an invoice. Reasoning tokens, batch discounts and regional prices are not modelled (ADR 0007).

## Alternatives considered

- **Parse the transcript on demand from `agentctl tokens report`.** No hook change, but the transcript can be deleted by Claude Code's retention setting before it is read, and the rows would not be in the tamper-evident ledger.
- **A statusline or API proxy.** A proxy sees exact usage for any harness, but it is a new component with credentials in its path. Worth revisiting with the orchestrator (P4.1a) and the router (P4.7), not for one harness.
- **Count every record without de-duplicating.** Simple, and about three times too high on observed data.
