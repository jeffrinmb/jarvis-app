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
| `decision-card` | 1 |
| `event` | 1 |
| `policy` | 1 |
| `project` | 1 |
| `task` | 1 |
| `tiers` | 1 |
| `tool-registry` | 1 |
| `verdict` | 1 |
| `wiki-frontmatter` | 1 |

## Changelog

### Unreleased

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
