# ADR 0013: Redacting secrets and scanning tool input

- Status: Accepted
- Date: 2026-11-05
- Deciders: project owner (the three choices below were made in a design round), Claude Code (implementation)

## Context

Task P2.12 and threat-model item T-5: secrets or personal data must not reach the ledger or a decision card, and the hook should notice them in a tool call's input. Until now the answer was "the hook never records file contents, command text, tool output or error text", which keeps material out by omission. It does not help when something else writes free-form detail: an error message, a note, `agentctl log --data`, or a field a later change adds. The ledger's `data` field is free-form by design.

## Decision

1. **Redact at the ledger's one write point.** Every record, from every writer, passes through `Ledger._append_locked`. It replaces secrets and personal data in the event's `data` with `[REDACTED:<kind>]` before hashing, so the hash chain covers the stored text and a verified ledger never holds the original. Values under keys that name a signature or a digest (`signature`, `hash`, `*_sha256` and the like) are left alone so they stay valid; identity fields outside `data` are not touched.
2. **What is found:** well-known token formats (cloud access keys, GitHub, Slack, Anthropic and OpenAI style keys, Google and Stripe keys, JWTs, `Bearer` tokens, private key blocks), credentials in a URL, a `password=`-style assignment whose value contains a digit, **and** personal data: email addresses and card numbers that pass the Luhn check. **No entropy guessing**: it would mangle the hashes and signatures the ledger is full of.
3. **Scan tool input, record only the kinds.** The hook scans a call's input and puts the kinds found (for example `github-token`) on the `Action` as `secrets_in_input`. The input itself is never kept. The scan covers secrets only; emails and card numbers are redacted in the ledger but are not a reason to stop a call, because they are everywhere in code and documentation.
4. **A secret in the input of a tool that writes, runs commands or sends data needs a human** (`builtin:secret-in-input`, effect `human`), as the owner chose over "record only". A read-only tool is never stopped by it. The policy setting `secrets_in_input` is `human` (default) or `alert` (record only); it can never allow, and an `auto_approve` rule cannot lift the human gate, because a verdict takes the most restrictive match. The adapter fills the field from its own scan, never from anything the agent says.
5. **Scan cap.** Input is scanned up to 1,000,000 characters. Beyond that the result includes `input-not-fully-scanned`, which counts as a finding, so a secret placed after the cap cannot make a large input look clean.

## Consequences

- A secret that reaches a ledger record by accident is gone from the file, and a secret in a tool call's input is surfaced to a person without being recorded anywhere.
- The default is stricter: a policy that did not set `secrets_in_input` now stops such calls for a person. The schema changelog records this. In log-only mode (today's hook) it only records what would have been asked.
- **Expect false positives and misses.** The assignment pattern and the formats are heuristics. A secret in an unknown format, split across pieces, or encoded is not found, and a test file that contains a fake token will ask a human. An approval can cover such a call. This reduces the risk; it does not remove it, and it is not a substitute for the repository's secret scan (gitleaks in CI, push protection).
- Cost: about 2.7 ms for a fresh hook process (import plus compiling 15 patterns), and almost nothing on later calls through the hook server. A 1 MB input takes about 130 ms.
- The ledger now alters data on the way in. A signature or digest stored in `data` under a name that is not on the exempt list would be altered if it happened to contain a token-shaped substring; today none is.
- **Not covered:** tool *output* (the hook does not see it before the model does, and does not record it), text in `outcome` or other fields outside `data`, and secrets already in an existing ledger (redaction applies to new writes; an old record cannot be changed without breaking the chain).

## Alternatives considered

- **Redact only in the hook.** Leaves `agentctl log --data` and every other writer unprotected.
- **Entropy-based detection.** Catches unknown formats but would flag the hashes, signatures and ids that fill the ledger.
- **Record only, never ask a human** (the recommended option in the design round). Would not add prompts; the owner chose the stronger option, accepting more prompts and a stricter default.
- **Treat emails and card numbers as a reason to ask.** Too many everyday prompts for little gain.
