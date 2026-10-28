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
- **Not built:** calls to new domains and unusual data volumes. The hook does not yet extract domains from tool input, and no data volume is recorded per call. Both are tracked as P2.8c. Per-call diff size can already be limited with `min_diff_lines` rules.

## Alternatives considered

- **Count only identical repeats.** What the loop breaker already does. It misses the case this ADR is for.
- **Anomaly detection with statistics or a model.** More flexible, but harder to explain and to test, and the detector would itself be an attack surface. Simple counted rules first; revisit with real intervention data.
- **Make the pause sticky until a human clears it.** The right behaviour, but it needs task parking and approvals (P2.7).
