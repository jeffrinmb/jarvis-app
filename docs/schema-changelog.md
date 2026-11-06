# Schema versioning policy and changelog

The schemas in `src/jarvis/core/schemas/` are the contract between every component and harness. The generated JSON Schemas in [`schemas/`](../schemas/) are derived from them and must not be edited by hand.

## Policy

Every top-level document carries an integer `schema_version`. All schemas are at version 1.

**Additive changes keep the version.** Record them in the changelog below.

- a new optional field with a safe default;
- a new schema;
- a new enum member (consumers must treat an unknown value as "not allowed", never as "allowed");
- a looser rule that does not weaken a safety property.

**Breaking changes bump the version of that schema.** They need an ADR in [`docs/adr/`](adr/0000-template.md) and a migration note in the changelog.

- removing or renaming a field, or changing its type or meaning;
- a new required field;
- anything that makes a previously valid document invalid, such as tightening a validation rule. (Rejecting a document that was unsafe is allowed at any time, but is still recorded here.)

**Safety rules are not loosened quietly.** Validations that enforce a safety property (for example "a policy default cannot be `allow`", "a done task needs a human", "agents never get the prod environment") may only be relaxed with an ADR and a review by the code owners.

**Unknown fields are rejected** (`extra="forbid"`), so a document written for a newer schema fails loudly on an older consumer instead of being misread.

**Pre-1.0.** The package is version 0.0.x, so stability is not yet promised. The log is kept anyway, because the audit ledger stores events and old records must stay readable.

## How to change a schema

1. Edit the model.
2. Regenerate the JSON Schemas: `uv run agentctl schema export --out schemas`.
3. Add an entry under **Unreleased** below, in the same pull request.
4. Run the tests. One fails if the JSON Schemas are out of date, and one fails if a schema is missing from this file.
5. For a breaking change, bump `schema_version` for that schema, add an ADR and a migration note.

## Current versions

| Schema | Version |
|---|---|
| `action` | 1 |
| `approval-grant` | 1 |
| `approval-request` | 1 |
| `decision-card` | 1 |
| `event` | 1 |
| `ledger-checkpoint` | 1 |
| `policy` | 1 |
| `price-table` | 1 |
| `project` | 1 |
| `task` | 1 |
| `tiers` | 1 |
| `tool-registry` | 1 |
| `verdict` | 1 |
| `wiki-frontmatter` | 1 |

## Changelog

### Unreleased

- `action`: new optional `command_prefixes`, the first one or two plain words of each command in a shell line (never arguments), filled by the adapter only when its configuration asks for it. Defaults to empty, so existing documents are unchanged (P2.10a).

- `action`: new optional `domains` (hosts named in the call's input, at most 20) and `data_bytes` (the input's size in bytes), both filled by the adapter's own scan and defaulting to empty and unset. The gate counts bytes only for `external-comms` tools. Existing documents are unchanged (P2.8c).

- `policy`: `anomalies` gains `new_domain` (default off), `new_domain_min_history` (default 20), `max_call_bytes` and `max_task_bytes` (both unset by default). Every detector is still off until set, so existing policies behave as before (P2.8c).

- Ledger summary (inside the `ledger_rotated` event's data) goes to version 2: it adds `domains_seen`, `domain_history` and per-task `out_bytes`. A version 1 summary is still accepted by `verify`, and the gate ignores it and reads the whole ledger (ADR 0014).

- `event`: new `kind` member `ledger_rotated`, the first record of a new ledger segment: it continues the hash chain and pins the archived segment (name, SHA-256, first and last sequence numbers, last hash) in `data`. An additive enum member; a consumer that does not know the kind must ignore it (ADR 0014, P2.1b).

- `action`: new optional `secrets_in_input`, the kinds of secret the adapter found in a call's input (never the secret). It is filled by the adapter's own scan, defaults to empty, and an agent cannot make the gate more lenient by setting it. Existing documents are unchanged.

- `policy`: new `secrets_in_input` (`human` or `alert`, default `human`): what a secret found in the input of a tool that writes, runs commands or sends data does. A person decides by default, so a policy that did not set it now stops such a call for a person; `alert` only records the kinds. It can never allow, and a read-only tool is never stopped by it (P2.12). This tightens the default, so it is recorded here.

- New schema `ledger-checkpoint` (version 1): a signed statement that at record `seq` the ledger's hash was `hash`, with the ledger id (the first record's hash), a time and an SSH signature in the namespace `jarvis-checkpoint`. The signer is not stored; it is whoever the allowed-signers file names for the key. One checkpoint per line in an append-only file kept outside the ledger (task P2.9b, [ADR 0010](adr/0010-identity-and-signing.md)).

- `approval-grant`: new optional `nonce` and `signature` (an SSH signature, namespace `jarvis-approval`, over the request id, nonce and scope; the approver is not signed because it comes from the allowed-signers file). A signature needs a nonce and a command-line grant (task P2.9a, [ADR 0010](adr/0010-identity-and-signing.md)).
- `policy`: `approvals` gains `require_signatures` (only signed approvals count, and the signer is the key's name) and `hardware_key_for_four_eyes` (no-go zone approvals need a security key). Both default to off, so existing policies behave as before; enforce mode refuses a policy without `require_signatures`.
- Adapter config (not a JSON Schema): `allowed_signers_path`, default `.agents/allowed-signers`, read through the policy source.

- New schemas `approval-request` and `approval-grant` (version 1): the `data` of `approval_requested` and `approval_granted` events. A request is built by the gate from its verdict and carries a proposed scope. A grant is one person's approval of a bounded, expiring scope; a prompt approval (`via: claude-code-prompt`) is a record of one call and has no scope. Scope globs that match everything, tool globs and a single-use scope without an exact call are rejected (task P2.7, [ADR 0009](adr/0009-approval-flow.md)).
- `policy`: new `approvals` block (`max_scope_hours`, default 24, between 1 and 168; `approvers`, a list of names allowed to approve). Both have defaults, so existing policies behave as before except that approvals now exist.
- `verdict`: new optional `approval_request` (the request a person can answer) and `approval_grant_ids` (the grants that let the call through), and a new rule category `approval`. Consumers must treat an unknown category as not allowed.
- Data convention: `approval_denied` events written by `agentctl approvals reject` revoke earlier grants for that request. `policy_decision` events carry `data.approval_grant_ids` and `data.approval_request`.

- New schema `price-table` (version 1): dollars per million tokens per model, with `as_of` and `source`, used to compute the cost of a model call when the provider did not report one (task P2.2, [ADR 0007](adr/0007-token-and-cost-ledger.md)).
- Data convention, no schema change: `model_call` events carry `data.cost_source` (`reported`, `computed` or `unknown`). `usage.tokens_in` means input tokens not served from a cache; `usage.tokens_cached` means those that were.

- `policy`: new optional `budgets` block (`max_calls_per_task`, `max_calls_per_day`, `loop_threshold`, `loop_window`, `on_exceed`). Unset means no limit, so existing policies behave as before. `on_exceed` can only be `human` or `deny`, never `allow`; `loop_threshold` and `loop_window` must be set together and the window cannot be smaller than the threshold (task P2.8).
- Data convention, no schema change: `policy_decision` events carry `data.fingerprint` (a hash of the tool and normalised paths, no content), and a tripped budget or loop also writes a `budget_exceeded` or `loop_detected` event. The `kill_switch` event kind already existed and is now written by `agentctl halt` and `agentctl resume`.

- `policy`: new optional `anomalies` block (`max_denials` with `denial_window`, `new_tool` with `new_tool_min_history` and `new_tool_risk_at_least`, `burst_calls` with `burst_seconds`, `on_anomaly`). Every detector is off until set. `on_anomaly` is `alert`, `human` or `deny`, never `allow`, and an `alert` adds no allow match (task P2.8a).
- `event`: new event kind `anomaly_detected` (additive; consumers must treat an unknown kind as not allowed, never as allowed).

Earlier in this period:

No schema changes. One data convention was added without changing any schema: `policy_decision` events now carry an optional `data.context` object with caller context (harness, mode, session id, permission mode, tool-use id, SHA-256 of the policy files). `data` is free-form, so the `event` schema is unchanged.

### 2026-10-27: policy gate (PR #5, commit 8bb0270)

Added

- `action`: the facts presented to the policy gate. It has no `risk` field on purpose: risk comes from the tool registry, so an agent cannot lower it.
- `verdict`: the gate's answer (decision, reason, matched rules, taint and trifecta flags, cumulative diff size).
- `policy` conditions: `risk_at_most` and `min_diff_lines`.

Tightened (recorded because previously valid documents can now fail)

- `policy`: an `auto_approve` rule must set at least one condition, so it can never match everything.
- `policy`: a condition is rejected when it can never match (`risk_at_least` above `risk_at_most`, or `min_diff_lines` above `max_diff_lines`).

Documentation

- Rule semantics are now described in the `policy` module docstring and in [ADR 0002](adr/0002-policy-evaluation-and-fail-closed-rules.md).

### 2026-10-27: initial schemas (PR #2, commit 7434307)

Added `decision-card`, `event`, `policy`, `project`, `task`, `tiers`, `tool-registry` and `wiki-frontmatter` at version 1, with these safety rules built in:

- `policy`: the default cannot be `allow`; rule ids are unique.
- `decision-card`: at least two options, a recommendation that is one of them, at least one piece of evidence, and a timeout that can only leave the task parked (never approve).
- `task`: a parked task needs a reason, and a done task needs a named human (`done_by`).
- `tool-registry`: irreversible, external-communication and critical-risk tools cannot be auto-approved; a tool usable in prod needs human approval.
- `project`: agents never get the prod environment; brownfield projects must list `no_go_paths`; the tiers file needs a model for every tier.
- `wiki-frontmatter`: a validated note needs trust of `test-verified` or `human-confirmed`.
- `event`: timestamps must carry a time zone; ids and trace ids must be well-formed hex.
