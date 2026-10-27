# ADR 0002: Policy evaluation order and fail-closed rules

- Status: Accepted
- Date: 2026-10-27
- Deciders: project owner (design proposed during `feat/policy-gate`)

## Context

Tasks P2.4 and P2.5 build the policy gate: given an action, return `allow`, `council`, `human` or `deny` with a reason. The charter (sections 2, 4 and 4.1) requires that boundaries be enforced in code, that the gate fail closed, that risk come from evidence and not from the agent's own claim, that cumulative limits not be defeated by splitting a change, and that untrusted content and the lethal trifecta tighten the policy. Several details were left open.

## Decision

**Inputs.** The gate evaluates an `Action` (facts: agent, role, tool, environment, touched paths, diff size, trust of inputs, what the session has already seen) against a `Policy`, a `ToolRegistry` and optionally a `ProjectConfig`. An action has no risk field: risk is the registry's risk class for the tool.

**Order.** The most restrictive applicable outcome wins: `deny > human > council > allow`. `allow` requires at least one matching `auto_approve` rule and nothing restrictive. If nothing matches, the policy's `default` applies, and a policy whose default is `allow` is invalid.

**Built-in guardrails** apply whatever the policy file says:

| Guardrail | Outcome |
|---|---|
| Tool not in the registry | deny |
| Role not in the tool's allowed roles | deny |
| Environment not enabled for the tool, or for the project | deny |
| Project given and tool not in `tools_enabled` (an empty list enables nothing) | deny |
| Production environment | human |
| Absolute, empty, drive-letter or `..` path | human |
| Path in the project's `no_go_paths` | human |
| Lethal trifecta: private data + untrusted content + an outbound tool | human |
| Tainted session using an irreversible or outbound tool | human |
| Registry approval mode `human` or `council` | at least that level (a floor) |

**Rule semantics.** A rule matches when every set condition matches. For `auto_approve` rules all touched paths must match the globs and at least one path must be touched; for `council_review`, `human_required` and `never` any matching path is enough. `max_diff_lines` needs a known size at or below the limit; `min_diff_lines` also matches when the size is unknown. An unknown diff size is tolerated only for read-only tools. An `auto_approve` rule must set at least one condition. Globs are case-insensitive and paths are normalised (separators, `./`, duplicate slashes) before matching.

**Cumulative limits.** Diff sizes are summed per task from earlier `policy_decision` events that did not end in `deny`, including pending council and human decisions. The read of history and the append of the new decision happen under one ledger lock (`Ledger.transaction`), so concurrent checks cannot jointly exceed a limit.

**Audit before answer.** The verdict is returned only after it is recorded. If the ledger cannot be read, verified at its tail, locked or written, the answer is `deny`. `agentctl check` also denies on any unexpected internal error.

**Exit codes.** `agentctl check` exits 0 for allow, 10 for council, 11 for human, 12 for deny and 2 for invalid input. Callers must treat every non-zero code as "not allowed".

**Comparisons.** Guardrails compare enums with `==`, not `is`, so an entry built without validation (plain strings) still triggers them. A test pins this.

## Consequences

- A permissive or mistaken policy file cannot remove the guardrails, and a missing or corrupt audit log stops actions instead of letting them through.
- Callers (the Claude Code hook, P2.6) are responsible for supplying truthful `session` and `input_trust` data; the gate cannot see what the harness does not tell it.
- Reading the whole ledger on every check costs time as it grows. Acceptable for now; an index (SQLite) is a follow-up (P2.4a).
- Only per-task cumulative limits exist. Per-day limits and budgets belong with P2.8.
- Risk is only as good as the registry. Path-derived risk (for example raising risk for `auth/` paths) is expressed today through `human_required` rules, not computed.
- `council` means "convene the council"; the council itself, and its fallback to a human on a split, come in P7.1.

## Alternatives considered

- **First matching rule wins (firewall style).** Easier to read, but ordering mistakes silently weaken a policy. Most-restrictive-wins makes mistakes fail toward asking a human.
- **Let the action declare its risk.** Simpler, but an agent could lower it. Rejected by the charter's "risk comes from evidence".
- **Fail open on audit errors.** Keeps work flowing during a logging outage, but breaks the premise that every action is auditable. Rejected.
- **Match restrictive and allow rules with the same path semantics.** Simpler, but then one allowed file could carry a forbidden one through an auto-approve rule.
