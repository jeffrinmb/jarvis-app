# ADR 0009: The approval flow

- Status: Accepted
- Date: 2026-10-29
- Deciders: project owner (task P2.7). The four main choices below were made from options put to the owner on 2026-10-29.

## Context

The gate answers `human` or `council` for many calls, but until now nothing could answer. In enforce mode the hook turned the verdict into Claude Code's `ask`, whose answer was not recorded, not attributed to a person and not reusable, and an unattended run could only be denied. The charter (sections 4 and 4.1) wants decision cards, attributed approvals, no self-approval, four-eyes for no-go zones, and timeouts that never approve. The threat model (ASI09, RT-13) wants what a person sees to come from the gate and not from the agent's own account of what it is doing.

## Decision

**Records, not a new store.** Requests and approvals are ledger events (`approval_requested`, `approval_granted`, `approval_denied`), so they are hash-chained like everything else. The gate derives the current state from the ledger on every check (`jarvis.core.approvals`); there is no approval table to tamper with or to fall out of step.

**A request is built by the gate, from its own verdict** (P2.7c). When a call gets `human` or `council` and may be approved, the gate writes a request holding the tool, the paths, the size, the rules that fired, the taint flag and a *proposed scope*. Nothing in it is text the agent wrote. Its id is stable for the same task, call and reasons, so a retry reuses its card instead of creating another (approval fatigue). A request is a new, small schema; `DecisionCard` stays for agent-authored and council decisions (P7.1).

**Approval path: the Claude Code prompt, and the command line.**
- In enforce mode the gate's `ask` is Claude Code's terminal prompt. If the call then runs, the person said yes, and `PostToolUse` records an attributed `approval_granted` event (`via: claude-code-prompt`). That record is **evidence only**: it carries no scope and is never counted as a reusable approval.
- For everything else (unattended runs, later answers) a person runs `agentctl approvals approve <request>`. An unattended denial now tells the agent which request a person can answer.

**Scope: bounded, and it expires.** A grant names one task, exact tool ids, path globs, a cumulative size limit and an expiry. It covers a call only if **all** of these hold, and every path must match, not just one. Unknown size is never covered for a call that changes state. Globs that match everything (`**`) and tool globs are refused. The default lifetime is 4 hours and the policy caps it (`approvals.max_scope_hours`, default 24, at most 168). The proposed size leaves room for exactly one retry, because the held call already counts toward the task's cumulative total.

**What an approval can never do.**
- It never lifts a `deny`: unregistered tool, role or environment not allowed, a `never` rule, the kill switch, or a `default: deny` policy.
- It does not lift gates that describe the session or the world, which can change after a person said yes: the lethal trifecta, production, a path that escapes the repository, or a tainted session using an irreversible or outbound tool. Those stay with the terminal prompt every time.
- It does not cover `shell.run`. A shell command can do anything and its text is never written to the ledger, so a card could not show what would run. The command is on screen in the terminal prompt, and that is where it is decided.
- High-risk, irreversible and outbound tools accept only a **single-use approval of the exact call**, which the first allowed use spends.
- Only reasons that exist to make a person look are approvable: the registry's approval mode, `human_required` and `council_review` rules, no-go zones, and the budget, loop and anomaly pauses. For the pauses an approval is the way out, which makes them stay put until a person decides (the sticky pause that ADR 0006 lacked).

**Four-eyes and self-approval.** A call touching a no-go zone needs grants from two different people. An agent never counts as an approver: the approver's name may not share the agent id's prefix (`claude-code` and `claude-code:Explore:abc`). If the policy lists `approvers`, only those names count. The command line refuses these cases, and **the gate re-checks all of them** when it counts a grant (agent id of the writing process, expiry cap, approver list), so a grant written by hand outside the rules does not count. `agentctl log` refuses to write approval events.

## Consequences

- Approvals are as trustworthy as the names behind them. **Identity is still claimed, not authenticated** (G1, P2.9a). An agent that can run `agentctl approvals approve --by alice` could forge an approval. What stands in the way today: shell commands are human-gated in the example policy and `shell.run` is never auto-approved; the policy's `approvers` list limits who counts; and the `ConfigChange` hook protects the hook settings. None of that stops an agent with an unrestricted shell until the sandbox (P4.2) and authenticated identity (P2.9a) exist. Enforce mode (P2.10) should not be switched on before them.
- In enforce mode an approved call still reaches Claude Code's own permission rules, because the hook never emits `allow` (ADR 0003). The person may be asked once more by Claude Code itself.
- A card shows paths, sizes, rule ids and flags, not the diff text or a command. A person judging a change still has to look at the change. Showing the diff in a card is part of the notification work (P2.7b).
- Approvals add one ledger scan per check (the same scan the budgets already do). Request creation is deduplicated, so a retry loop does not flood the ledger.
- Parking a task and resuming it needs the orchestrator (P4.1). Until then "parked" means the gate keeps answering `human` for the call and the agent is told which request to wait for.
- Precedents (P2.7a), digests and notifications (P2.7b) and override-rate reporting are not built. The number of requests is visible in the ledger and in `agentctl approvals list --all`.

## Alternatives considered

- **Exact call only.** The simplest and hardest to abuse, but a long task would produce a card per call, which is the approval-fatigue failure the threat model names. Exact approval is kept for the dangerous tools.
- **Approve a whole task.** Least friction, and close to turning the gate off for that task. Rejected.
- **Command line only, or prompt only.** The first makes a person approve twice (once in the harness, once elsewhere); the second cannot approve later or in a batch and gives unattended runs no way forward.
- **Wait for identity before building four-eyes.** Nothing weak would be built, but no-go zones would have no second check meanwhile. The rules and tests are final and get stronger when P2.9a lands.
- **A separate approvals table.** Easier to query, but a second source of truth without the hash chain.
