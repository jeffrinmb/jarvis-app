# ADR 0006: Anomaly rules on the ledger

- Status: Accepted
- Date: 2026-10-28
- Deciders: project owner (task P2.8a)

## Context

An agent that keeps trying after being denied, reaches for a tool it has never used, or suddenly works much faster than usual is a signal worth acting on (OWASP ASI10, red-team case RT-14). The loop breaker from [ADR 0005](0005-budgets-loop-breaker-and-kill-switch.md) only catches an identical repeated call, so retries "with small variations" went unnoticed.

## Decision

The policy gets an optional `anomalies` block. Every detector is off until its fields are set, and every detector reads only earlier decisions in the ledger, counted inside the gate's transaction like the budgets.

1. **Repeated denials** (`max_denials` within `denial_window`): trips when that many of the task's last decisions were denials, however the attempts vary (different tool names, different paths). This is the RT-14 case.
2. **A tool never used before** (`new_tool`): trips when the tool has no earlier non-denied decision in the ledger, but only once the ledger has `new_tool_min_history` such decisions (default 50) and only for tools at or above `new_tool_risk_at_least` (default medium). The baseline stops a fresh ledger flagging everything. A denied attempt does not make a tool "seen", so retrying cannot launder it.
3. **A burst** (`burst_calls` within `burst_seconds`): trips when one task has already made that many calls in that window.
4. **`on_anomaly`** decides what a detection does: `alert` records an `anomaly_detected` event and leaves the decision alone, `human` (the default) pauses the call for a person, `deny` stops it. It can never be `allow`. A detection that is not `alert` is a built-in rule (`builtin:anomaly-denials`, `builtin:anomaly-new-tool`, `builtin:anomaly-burst`) combined by most-restrictive-wins, so an `auto_approve` rule cannot override it.
5. **`alert` is not an approval.** It adds no match at all, so it cannot make an action allowed that no rule allowed (`tests/test_anomaly_rules.py::test_alert_can_never_approve_something_no_rule_allows`).
6. Every detection writes an `anomaly_detected` event (new, additive event kind) with the rule id and a reason, in every mode, including `alert`. Nothing from tool input is recorded.

## Consequences

- RT-14 is covered: after the configured number of denials in a window, the next call of that task is paused or stopped.
- The pause lifts by itself when the denials leave the window. Parking the task until a human decides needs the approval flow (P2.7); until then `human` only asks for a decision on each call, and `deny` is the firm stop. The kill switch (ADR 0005) remains the manual stop.
- A new tool is only judged against the ledger's own history, so a long-lived ledger is a better baseline than a fresh one, and deleting or truncating the ledger resets it (G4).
- Detection happens when a call is checked. Nothing watches the ledger between calls, so an alert reaches a person only if something reads the events (for example `agentctl hook report`, or the alerting planned with OpenTelemetry in P2.3).
- **Built later (P2.8c, 2026-11-06):** calls to new hosts and unusual outbound volume, below. Per-call diff size can already be limited with `min_diff_lines` rules.

## New hosts and outbound volume (P2.8c)

Three more detectors in the same `anomalies` block, off until set, answered by the same `on_anomaly`, and combined by most-restrictive-wins like the others (`builtin:anomaly-new-domain`, `builtin:anomaly-call-bytes`, `builtin:anomaly-task-bytes`):

1. **A new host** (`new_domain`, `new_domain_min_history`, default 20): trips when a call names a host that no earlier decision that was not denied named, once that many earlier decisions have named a host. As with a new tool, the baseline stops a fresh ledger flagging every first call, and a denied attempt does not make a host "seen", so retrying cannot launder it.
2. **One large outbound call** (`max_call_bytes`): trips when a call to a tool that sends data outward carries more than that many bytes of input.
3. **A large outbound total** (`max_task_bytes`): trips when a task's calls to such tools, counting this one, carry more than that many bytes in all, so many small calls cannot add up unnoticed. Only a call that sends data is stopped; later local work is not.

**What the hook measures.** The adapter fills two new facts on the action from its own scan, never from anything the agent says: `domains`, the hosts named in the call (the host of a `WebFetch` URL, and the hosts of any URL in an MCP tool's input, lowercase, with no port, path, query or credentials, at most 20), and `data_bytes`, the size of the call's input. A web search names no host (the search engine is the tool). The gate counts bytes only for tools whose registry effect is `external-comms`, so writing a large file sends nothing and is not counted. The decision event records the hosts and the outbound bytes, never the input.

**Carried in the summary.** The hosts seen, how many decisions named a host, and each task's outbound bytes are part of the ledger summary (ADR 0014), so they survive rotation and `verify` recomputes them. That raised the summary version to 2. A link written under version 1 still verifies (compared as version 1 would have been), and the gate reads the whole ledger for it, as it does for any summary it cannot use; the next rotation writes a version 2 summary.

**Limits.** Only hosts written as URLs are found: not a host given to `curl` in a shell command (a shell command is already human-gated), not an IP address in a bare form, and not one split across fields. Input size is an upper bound on what a call can carry, not a measure of what actually left the machine, and a patient task can stay under both limits. A host is compared exactly, so `api.example.com` and `example.com` are different hosts. These rules add friction and a record; they are not a data-loss-prevention system.

## Alternatives considered

- **Count only identical repeats.** What the loop breaker already does. It misses the case this ADR is for.
- **Anomaly detection with statistics or a model.** More flexible, but harder to explain and to test, and the detector would itself be an attack surface. Simple counted rules first; revisit with real intervention data.
- **Make the pause sticky until a human clears it.** The right behaviour, but it needs task parking and approvals (P2.7).
