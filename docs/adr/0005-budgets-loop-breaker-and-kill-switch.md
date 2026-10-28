# ADR 0005: Budgets, loop breaker and kill switch

- Status: Accepted
- Date: 2026-10-28
- Deciders: project owner (task P2.8)

## Context

A runaway agent can make hundreds of calls, repeat one call forever, or keep going after a human has decided it should stop (threat model T-4, T-10, gap G5). The gate so far limited only the cumulative size of a change. There was no way to stop everything at once.

## Decision

**Budgets and the loop breaker are policy, counted from the ledger.**

1. The policy gets an optional `budgets` block: `max_calls_per_task`, `max_calls_per_day`, `loop_threshold` with `loop_window`, and `on_exceed` (`human` by default, or `deny`; never `allow`). Unset means no limit, so existing policies behave as before.
2. A "call" is a policy decision that did not end in `deny`, counted before the current call, so a limit of 3 allows three calls and stops the fourth. A denied call used nothing. The day is the UTC day of the event's timestamp, across all tasks in the ledger.
3. The counting runs in the same ledger transaction as the decision, like the cumulative diff limit, so concurrent checks cannot overshoot.
4. A **loop** is the same tool on the same normalised paths, `loop_threshold` times within the last `loop_window` calls of one task. Each decision stores a `fingerprint` (a short hash of tool and paths, never the paths themselves or any content).
5. A tripped limit is a built-in rule (`builtin:budget-task-calls`, `builtin:budget-day-calls`, `builtin:loop-detected`) that combines with the others by most-restrictive-wins, so an allow rule cannot override it. It also writes a `budget_exceeded` or `loop_detected` event so it can be searched and alerted on.

**The kill switch is a file outside the project.**

6. `agentctl halt` creates a `HALT` file in the per-user state directory (the one the hook uses for session state). While it exists the gate denies **every** action, reads and unregistered tools included, with `builtin:kill-switch`. `agentctl resume` removes it and `agentctl status` reports it (exit 1 when on).
7. The state directory is outside the workspace and a no-go path for the gate, so the agent is not given the means to clear it. It is still an ordinary file: an agent with a shell could delete it until the sandbox (P4.2) stops that, which is the same limit as the session state.
8. **Fail closed.** If the file exists but cannot be read or parsed, or its path cannot be inspected for a reason other than not existing, the switch counts as on. Setting and clearing record a `kill_switch` event in the ledger, best effort: the switch itself works even if the ledger does not.
9. In log-only mode the hook records the denial and never blocks, like every other verdict. In enforce mode it blocks every call.

## Consequences

- G5 is narrowed: a human can stop all gated actions with one command, and a runaway or looping task is caught by the gate. It only covers calls that go through the gate (the Claude Code hook today). A harness without an adapter is not stopped (P7.3), and an agent with a shell can still delete the file (P4.2).
- Looping on a legitimate repeated action (re-running a test suite on the same path) trips the breaker if the threshold is too low. The example policy uses a generous threshold and the default is off. Tune it from `agentctl hook report` data before enforcing.
- Per-stage budgets and token or cost budgets are not built: the action has no stage yet, and tokens are not recorded until the token ledger (P2.2). They are tracked as P2.8b.
- Detection of repeated *denied* attempts and unusual tool use stays with P2.8a.

## Alternatives considered

- **A `halted` flag in the ledger.** Auditable, but the ledger is in the project and agent-writable (G4), and a stuck ledger lock would stop the switch from being read.
- **A switch in the policy file.** It would need a commit and a merge to take effect, which is too slow for an emergency, and the file is not where a human reaches first.
- **A per-task switch.** Parking a single task belongs to the approval and parking flow (P2.7). One global switch is enough for an emergency.
- **Denying only writes while halted.** Reads can leak data and run up cost, and a "stop" that lets some calls through is hard to trust.
