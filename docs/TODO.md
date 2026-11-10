# TODO: phases and tasks

Derived from the [framework charter](framework-charter.md) (section 10 roadmap, section 12 open decisions). Check boxes as work completes. Task IDs are stable references (for example `P2.4`). Owners are roles, to be replaced with names in phase 0.

Rules for working through this list:
- Do not start phase N+1 work that depends on a phase N exit check before that check passes.
- Do not start the council, the learned router or the 3D world before the ledger and gates exist.
- Keep human gates strict at first. Loosen only from ledger data.
- Each phase ends with a demo, not a feature list.

Roles: **FO** framework owner, **PS** policy and security engineer, **ME** memory and evals engineer, **PL** platform engineer, **CO** compliance owner, **TL** pilot team lead.

---

## Phase 0: Decisions and setup

Goal: agree what we are building, who owns it, and where it is piloted.

- [ ] **P0.1** Name the framework owner, policy owner and security reviewer (FO)
- [ ] **P0.2** Review and approve the charter as v1.0 (FO, PS, CO)
- [x] **P0.3** Lock the core decisions. Decided in [ADR 0001](adr/0001-language-storage-and-layout.md): Python, uv, SQLite plus JSONL, the `src/jarvis` layout, Claude Code as the first harness. Decided in [ADR 0011](adr/0011-observability-and-vault.md): OpenTelemetry GenAI (pinned) for observability, and a vault per repository in `.agents/vault/` (FO, PL)
- [ ] **P0.4** Choose the pilot repos: one brownfield and one small greenfield, with their stacks, ticketing system and CI (FO, TL)
- [ ] **P0.5** Write the data policy: which models and endpoints may see which repos, retention terms (PS, CO)
- [ ] **P0.6** Scope compliance with the compliance owner: EU AI Act applicability, log retention (at least six months where it applies), confirm the Digital Omnibus dates on EUR-Lex (CO)
- [ ] **P0.7** Close the charter's unverified items: read the full OWASP agentic document, confirm OTel opt-in and `execute_tool` details, check Tessl, MetaGPT, CrewAI, AutoGen, LangGraph, the OpenAI Agents SDK and ISO/IEC 42001 (PS, ME)
- [ ] **P0.8** Capture baselines for both pilots: cycle time, defect rate, build time, cost, review time (TL)
- [ ] **P0.9** Run the DORA readiness checklist for each pilot team (clear AI stance, version control, small batches, internal data access, platform quality) (TL)
- [x] **P0.10** Repository hygiene: `main` is the default branch and protected by a ruleset (pull request required, `ci-ok` required so matrix changes never need ruleset edits, no force pushes or deletions); the repo is public; `.gitignore` (including `.obsidian/`), the Apache-2.0 licence, `NOTICE`, `CODEOWNERS`, `CONTRIBUTING.md` and `SECURITY.md` exist; secret scanning with push protection, private vulnerability reporting and approval for first-time contributors' workflow runs are switched on in GitHub settings (FO, PL)
- [ ] **P0.10a** Confirm who owns the copyright. _Done: the project owner reports it was built on personal time and equipment, and `NOTICE` names them as the copyright holder._ Still to do: read the employment contract's IP and outside-work clauses once and, if anything could relate to the employer's business, get written confirmation from the employer (FO)
- [ ] **P0.11** Decide whether OpenHands should be evaluated as a runtime or design reference before building our own orchestrator (FO, PL)
- [x] **P0.12** Start the framework decisions log: ADR 0001 (language, storage, layout) and ADR 0002 (policy rules) exist, with a template in `docs/adr/0000-template.md` (FO)
- [ ] **P0.13** Choose the final project name and check it: "Jarvis" is a common name that may clash with existing products or trademarks, the Python package name `jarvis-agentic` must be available if it will be published, and BMAD's trademarks must not be used (FO)
- [ ] **P0.14** Resolve the charter's remaining open decisions: where the event bus and visual layer are hosted (local or shared), which content pack ships first and whether importers are needed on day one, whether the pilot has a 2D timeline only, which channel delivers decision cards (email, chat or GitHub comments), and the stated reason for any second harness or model provider (FO, PL)
- [x] **P0.15** Decide how identity and signing work. Decided in [ADR 0010](adr/0010-identity-and-signing.md): approvers prove approvals with SSH signing keys (hardware where possible) listed in an allowed-signers file in the protected policy source; agent role is launcher-bound now and signed task credentials come with the orchestrator; approvals and ledger checkpoints are signed and routine events are not; the backend is `ssh-keygen` with no new dependency (FO, PS)
- [ ] **P0.16** Each approver creates an SSH signing key (a hardware security key for anyone who approves no-go zones or four-eyes) and adds the public key to the allowed-signers file by pull request. **Part done 2026-11-05:** one software key (jeffrinmb, 256 SHA256:o/gWVudpzkejteQ7e7cx21I83POHI2bqp7YQfs2VfWc) is listed in `.agents/allowed-signers`, in both namespaces. **Still open:** a hardware security key for anyone approving no-go zones, other approvers' keys, and the repository policy does not yet set `require_signatures`, so no approval is checked against the file. Needs your action, and a real hardware key to test the touch requirement (FO, PS)

Exit check: owners named, decisions and pilots recorded, baselines captured.

---

## Phase 1: Schemas and repository skeleton

Goal: stable contracts before any component is built.

- [x] **P1.1** Create the skeleton: `src/jarvis/` (core and adapters stubs), `packs/`, `viz/`, `docs/`; layout decided in ADR 0001 (PL)
- [x] **P1.2** Define schemas with validation: event, task, decision card, policy rule, tool registry entry, wiki frontmatter, trust label, `project.yaml`, `tiers.yaml` (PL, PS)
- [x] **P1.3** Schema tests with fixtures, including invalid cases; example files are validated by the tests (PL)
- [x] **P1.4** Schema versioning policy and changelog: [docs/schema-changelog.md](schema-changelog.md); tests fail if a schema is missing from it or the version table is wrong (PL)
- [x] **P1.5** CI on every change: lint, format check, tests and the schema drift test on Linux and Windows; a `secret-scan` job (gitleaks over the full history, version and SHA-256 pinned); every action pinned to a commit SHA; Dependabot for actions and Python packages; static tests guard all of this (`tests/test_ci_workflow.py`). _The secret-scan job has passed in CI since its first run._ (PL)
- [ ] **P1.5a** Dependency allowlist and existence check, as a defence against invented or typo-squatted packages. Overlaps P4.11 (PS)
- [x] **P1.5b** Verify the first real runs of the P1.5 work. _Done: the `ci` workflow, which includes the `secret-scan` job, succeeded on its runs for pull requests 6, 7 and 8; both Dependabot update runs (`uv` and `github_actions`) succeeded, so both entries are accepted; GitHub secret scanning with push protection and private vulnerability reporting are on. No Dependabot pull request has been opened yet; when one appears it must pass CI like any other._ (FO, PS)
- [ ] **P1.6** ADR template and the first ADRs (storage, schema versioning, trace conventions). _Template and ADR 0001 done; schema versioning and trace conventions ADRs still to do._ (FO)
- [x] **P1.7** Map the OWASP agentic risks (ASI01 to ASI10) to controls and red-team cases as a threat-model document: [docs/threat-model.md](threat-model.md), with 20 red-team cases and a test that checks every cited test exists (PS)

- [x] **P1.8** Static analysis of the framework's own code in CI. Built with Bandit 1.9.4 (pinned): a `static-analysis` job runs `bandit -c bandit.yaml -r src`, any finding fails it, and `ci-ok` waits for it. First run: 14 findings, all Low severity and High confidence and all about running subprocesses. Triage: seven `B404` ("imports subprocess") skipped in `bandit.yaml` because every call is checked individually; five `B603`/`B607` calls (`git`, `ssh-keygen`, `claude --version`, our own server process) suppressed on the line with `# nosec <id>` and a `# Reason:` comment; and one **real finding**: the new `checkpoint-history` command passed its `--base` to `git` without the validation the git policy source has, so a base such as `--base=--output=x` could be read as an option. It now rejects anything that does not start with a letter or digit, and scrubs `GIT_*` variables. A test keeps every suppression named and explained and the configuration limited to one skip (PS)
- [ ] **P1.8a** CodeQL (or a similar data-flow analysis) as a second analyser, if code scanning is enabled for the repository. Bandit matches known-risky patterns; CodeQL follows data between functions and would catch what it cannot. Needs a repository setting and pinned action versions (PS)

Exit check: schemas validate, CI is green, threat model reviewed.

---

## Phase 2: The spine (ledger, policy gate, approvals)

Goal: every action is logged, risky actions are stopped, humans can approve with attribution.

- [x] **P2.1** Hash-chained JSONL event log with `agentctl log`, `verify` and `head`; single-writer lock file; anchors for truncation and rewrite detection (PL)
- [ ] **P2.1a** Record ledger anchors outside the ledger (for example commit `agentctl head` output to Git at milestones or on a schedule) and verify against them in CI. _Signed checkpoints (P2.9b) now do the recording and checking; the committed checkpoints are now checked in CI (P2.9f); signing stays manual (P2.9e)._ (PL, PS)
- [ ] **P2.1b** Ledger retention and archival: rotate or archive without breaking the chain, keep records for at least six months where regulation applies, and control who can read or write them. **Part done 2026-11-06 ([ADR 0014](adr/0014-ledger-segments.md)):** `agentctl ledger rotate` and `status`, and a size-triggered rotation in `Ledger`, move the active file into `<name>.archive/` and continue one hash chain; `verify`, `head`, records and signed checkpoints work across segments, and nothing is ever deleted. **Still open:** a retention and deletion policy, compression, copies off the machine, and access control on the archive (PL, PS, CO)
- [x] **P2.2** Token and cost ledger: one `model_call` row per call in the hash-chained ledger with model, tokens in and out, cached, cost and its source, outcome; a dated price table (`price-table` schema); `agentctl tokens record` and `report` by model, task, agent, stage or day ([ADR 0007](adr/0007-token-and-cost-ledger.md)). Nothing records rows automatically yet: that is P2.2a (PL)
- [x] **P2.2a** Record model calls automatically from Claude Code: on `Stop`, `SubagentStop` and `SessionEnd` the hook reads per-message `usage` from the session transcript and writes `model_call` rows, de-duplicated by message id and idempotent against the ledger ([ADR 0008](adr/0008-token-capture-from-transcripts.md)). The transcript format is not documented; the reader is defensive and was checked on a real session (PL)
- [ ] **P2.2b** Time a long session's per-turn re-read, and check the transcript reader against each new Claude Code version and warn when a session that ran model calls yields no rows (belongs with P2.6c); keep an offset so long sessions are not re-read in full (P2.6e) (PL)
- [x] **P2.3** OpenTelemetry GenAI export, as decided in [ADR 0011](adr/0011-observability-and-vault.md): `agentctl otel export --out FILE` writes the verified ledger as OTLP/JSON from one module that pins a commit of the conventions, maps `tokens_in` plus `tokens_cached` to `gen_ai.usage.input_tokens`, and never exports prompt, answer, path or reason text. Hand-written OTLP, so no new dependency. Events without ids get them derived and marked `jarvis.ids_derived`; the hook sets real ones in P2.3a (PL)
- [x] **P2.3a** Real trace and span ids from the Claude Code hook: one trace per session; a tool call's gate decision and its `PostToolUse` outcome share a span id from `tool_use_id` and are exported as one `execute_tool` span; model calls from the transcript join the session trace. Ids are derived from the payload, so nothing is stored between calls, and older rows without ids still join the same trace (PL)
- [x] **P2.3b** `agentctl otel export --endpoint URL` sends the export to an OTLP/HTTP collector (JSON, `/v1/traces`), with or without `--out`. Fail closed: https except for localhost, no credentials in the URL or sent, redirects refused, the response body never shown, exit 1 if the collector is unreachable, refuses, or reports rejected spans; proxies named in the environment are ignored. The export's structure was also checked once against the official OTLP protobuf definitions (PL, PS)
- [ ] **P2.3c** Run the export against a real collector or viewer (only a local stand-in server and the protobuf definitions have been used); support an authentication header, kept out of the command line and the ledger; keep a cursor so a second send does not repeat spans; and re-check the pinned conventions commit when they move (PL, PS)
- [x] **P2.4** Policy engine and `agentctl check` returning allow, council, human or deny with a reason; risk comes from the registry, never from the action; fail closed; decisions logged to the ledger; thresholds aggregated per task. Rules and exit codes are in ADR 0002 (PS)
- [x] **P2.4a** An index so checks do not scan the whole ledger, built as ledger rotation with a summary carried in the new segment ([ADR 0014](adr/0014-ledger-segments.md)). Measured per decision: 41 ms at 10,000 records and 46 ms at 40,000 with rotation, against 166 ms and 631 ms before. The summary never changes a decision, only how much is read (the gate reads the whole ledger when a task's windows do not fit), `verify` recomputes it, and the same random sequence of real decisions on a rotating and a non-rotating ledger gives identical verdicts. The hook rotates at 1,000,000 bytes (`ledger_rotate_bytes`). **Not covered:** whole-history readers still read every segment; compacting very old tasks (PL, PS)
- [ ] **P2.4c** Per-day aggregation of decisions and costs for reports (split from P2.4a) (PL)
- [ ] **P2.4d** Path-derived risk: raise an action's risk from the paths it touches, for example auth or deployment files. It changes verdicts, so it needs its own review (split from P2.4a) (PS, PL)
- [x] **P2.4b** Policy integrity (threat model gap G2): record a SHA-256 of the policy, registry and project files in every decision event, and have the hook load them (and its own adapter config) from a source the agent cannot write: a git commit or a directory outside the project, chosen on the command line or by environment variable. Enforce mode refuses the working tree. [ADR 0004](adr/0004-trusted-policy-source.md). _Narrows G2; it is not closed until the sandbox stops an agent with a shell moving a git ref (P4.2), so pin a full commit SHA._ (PS)
- [x] **P2.5** Trust labels, taint tracking and the lethal-trifecta check in the gate. _The gate takes session state as input; the Claude Code hook (P2.6) must supply it truthfully._ (PS)
- [x] **P2.6a** Before building the hook, check Claude Code's current hook interface against its documentation (events, payload fields, whether a hook can block a call) and record what it can and cannot do in an ADR: [ADR 0003](adr/0003-claude-code-hook-interface.md). Key findings: a hook `deny` holds in every permission mode, but a crashed or timed-out hook lets the action through unless `onFailure: block` is set, which needs Claude Code 2.1.295 and this machine has 2.1.276 (PL)
- [x] **P2.6** _Built: `agentctl hook run|snippet|doctor`, see [docs/claude-code-hook.md](claude-code-hook.md). Tested with realistic payloads, not yet in a live session (P2.6c)._ Claude Code hooks that emit events and call `agentctl check` before risky tools, in **log-only mode** first. The hook builds the `Action` from the real tool call and tracks what the session has seen (untrusted content, private data). Design decisions are in [ADR 0003](adr/0003-claude-code-hook-interface.md), including a `ConfigChange` hook that protects the hook configuration (PL)
- [x] **P2.6b** End-to-end test that a lie in the agent's own description of an action cannot change the decision, and that a missing exposure report is caught (threat model T-3, RT-03). _Done at the handler level with realistic payloads (`tests/test_claude_code_hook.py`); the live-session confirmation is part of P2.6c._ (PS)
- [ ] **P2.6c** Run the open-question experiments listed in ADR 0003 on the installed Claude Code and update the ADR: exit-code behaviour on this version, `ask` in a `-p` session, the `MultiEdit`, `NotebookEdit` and MCP payloads, whether `ConfigChange` blocks the agent's own edits to hook settings, whether `deny` holds under `bypassPermissions`, hook start-up latency, and the Windows shell (threat model T-11, RT-18 to RT-20). _Answered in round 1 of the dogfood log: hooks load mid-session, `ConfigChange` fires on the agent's own settings edit, latency is about 1 s per call. Answered on 2026-11-10 in the desktop app (`auto` mode): `ask` raises a confirmation for a write and `deny` holds for shell calls. Still open, and blocked on signing the CLI in (`claude auth login`) for the headless runs: exit-code behaviour on this version, `ask` in `-p`, `bypassPermissions`, `MultiEdit` and `NotebookEdit` payloads, the Windows shell form, a crashing hook, and `ask` in `default` mode._ (PL, PS)
- [x] **P2.6d** Dogfood the hook on this repository in log-only mode. _Round 1 done: installed, 55 decisions from one session reviewed, four false-positive causes fixed (leading `cd`, trailing output filters, trusted app tools, internal bookkeeping tools). See [docs/dogfood-log.md](dogfood-log.md). Further rounds belong to P2.10._ (PS, PL)
- [x] **P2.6e** Reduce the hook's cost by importing less ([dogfood log, round 3](dogfood-log.md)): `jarvis.cli` is now a thin entry point so `hook` never loads the other commands, and the exporter, the OTLP sender, `subprocess` and `tempfile` load only where they are used. A test guards it (`tests/test_hook_imports.py`). **The gain is small: about 1% to 8% on a Windows machine, measured back to back (the absolute times were taken while the machine was slow; see round 4 of the dogfood log).** What is left is the Python start (0.19 s), pydantic (about 0.6 s, of which the first model alone is about 0.4 s) and YAML, which no amount of lazy importing removes. Going well under 200 ms needs a long-lived process: P2.6f (PL)
- [x] **P2.6f** A local server for the hook and a tiny client ([ADR 0012](adr/0012-hook-server.md)): `agentctl hook serve` runs the same decision code as the in-process hook, and `agentctl hook run --server` (or `JARVIS_HOOK_SERVER=1`, or `hook snippet --server`) asks it through a standard-library-only client that never imports pydantic. Auto-starts on first use and exits after 30 idle minutes; the endpoint is loopback only with a per-user secret, a Host check and a JSON-only check; if the server cannot be reached the client decides in-process, and if a request was sent without an answer it never decides again (enforce blocks). **About 2 times faster per call here (about 480 to 570 ms down to about 230 to 280 ms); the Python start is still paid.** Not tested on Linux or macOS (PL, PS)
- [x] **P2.6h** The first-call startup race found in the first daily check: a call that reached a server that was stopping was dropped unread, and the client (which must never decide twice) failed it, which in enforce mode would have blocked a legitimate call. Reproduced (about half of the calls made within 0.1 s after a stop failed; 1 of 40 real enforce-mode hook calls blocked) and fixed with an `Expect: 100-continue` handshake that tells an unread request from one the server took, plus a clean server shutdown ([ADR 0012](adr/0012-hook-server.md)). 0 failures in 60 library calls and 0 of 40 blocked in enforce mode afterwards (PL)
- [ ] **P2.6g** `http` hooks for the tool events, so there is no process start at all (about 50 ms): first a live test that a Claude Code with `http` hooks accepts a `localhost` URL on this machine (the documentation is silent on both), then a fixed port and a secret in the URL path of the git-ignored local settings. `SessionStart` stays a command hook, and enforce mode needs Claude Code 2.1.295 or later for `onFailure: "block"` because that route has no in-process fallback. Also run the server tests on Linux and macOS (PL, PS)
- [x] **P2.10a** Decided by the owner with measurements from the live ledger ([ADR 0015](adr/0015-prompt-rate-decisions.md)): of 1,020 decisions, 84% would have asked a person, but only 1.4% because of the taint rule alone (removing every taint rule moves 84% to 83%), 59% were general shell commands and 10% were edits blocked only by the cumulative 400-line cap. So: **the taint rule is kept**; **the edit cap goes from 400 to 2,000 lines per session** in this repository's policy (a modest gain: it would have let through 40 of the 106 edits blocked only by the cap, about 4 points, because the measured session ran to 17,343 lines); **safe `&&` chains** of listed read or test commands are recognised (every part must already be safe, nothing else in the line); and a **two-word command prefix is recorded** (`record_command_prefixes`, on here, never arguments) so the shell prefixes can be chosen from evidence. `agentctl hook report` shows the prefix counts (PS, FO)
- [ ] **P2.10b** After a few days of recorded prefixes, run `agentctl hook report`, choose which command prefixes may be allowed (as read or test classes, never `shell.run` itself), measure the would-ask rate again, and turn `record_command_prefixes` off. Also decide about writes to paths outside the project (5% of decisions, some are session scratch folders) (PS, FO)
- [x] **P2.7** Approval flow: the gate writes an approval request from its own verdict, a person answers with `agentctl approvals approve` or in Claude Code's prompt, and approvals are bounded in scope and expire. Four-eyes for no-go zones, no self-approval, single-use approval for dangerous tools, and an approval can never lift a deny or a session-state gate ([ADR 0009](adr/0009-approval-flow.md)). Park and resume of a task is P2.7d (PL, PS)
- [ ] **P2.7d** Park and resume: the orchestrator parks a task on a pending approval request and resumes it when approved, so the agent waits instead of retrying. Needs the orchestrator (P4.1) (PL)
- [ ] **P2.7e** Show the real change in a card (a diff summary from the worktree) and report approval fatigue: requests per day, time to answer, rejected share. Belongs with notifications (P2.7b) (PL)
- [ ] **P2.7a** Precedents: record every human decision, and when the same decision type is approved repeatedly (start with three) propose a policy change through a normal policy pull request. Agents never edit policy (PS, ME)
- [ ] **P2.7b** Notification channels for decision cards: a twice-daily digest for non-urgent items, push for urgent ones, and an "ask me later" action, all through the same gate API (PL)
- [x] **P2.7c** Request evidence comes from the gate's verdict (matched rules, taint flag, size) and the real call's paths, never from text the agent wrote (ASI09, RT-13). Diff text in a card is P2.7e (PS)
- [x] **P2.8** Budgets per task and day, loop breaker, kill switch: policy `budgets` counted from the ledger in the gate's transaction, and `agentctl halt`, `resume` and `status` for a kill switch that denies everything ([ADR 0005](adr/0005-budgets-loop-breaker-and-kill-switch.md)). Per-stage and token or cost budgets are P2.8b (PL)
- [ ] **P2.8b** Budgets per stage and per token or cost: needs a stage on the action (P4.1) and the token and cost ledger (P2.2). Reuses the `budgets` block and `on_exceed` (PL)
- [x] **P2.8a** Anomaly rules on the ledger: repeated denied actions (however the retries vary), a tool never used before, and bursts of calls. Each detection is an `anomaly_detected` event and can alert, pause for a human or deny ([ADR 0006](adr/0006-anomaly-rules.md), ASI10, RT-14). Calls to new domains and unusual data volumes are P2.8c (PS)
- [x] **P2.8c** Anomaly rules for calls to new hosts and unusual outbound volume ([ADR 0006](adr/0006-anomaly-rules.md)): the hook extracts the host of a `WebFetch` URL and the hosts of URLs in MCP input (a web search names none) and measures each call's input; the gate flags a host no earlier call named (after a baseline), one call carrying too many bytes outward, and a task whose outward total grows too big, each as `builtin:anomaly-*` answered by `on_anomaly` (human, deny or alert) and off until set. Decisions record the hosts and outbound bytes, never the input; the carried ledger summary holds them (summary version 2; a version 1 link still verifies). **Limits:** only hosts written as URLs are found (not a host given to `curl`), input size is an upper bound and not what actually left, and a patient task can stay under the limits (PS)
- [ ] **P2.9** Agent identities and "on behalf of" fields, and the credential broker with short-lived, task-scoped credentials issued only for an approved task. Decided in [ADR 0010](adr/0010-identity-and-signing.md): credentials are signed by a person's key; built with the orchestrator, see P2.9c (PS)
- [x] **P2.9a** Authenticated approvals (threat model gap G1, RT-08), built as [ADR 0010](adr/0010-identity-and-signing.md) decided: `agentctl approvals approve --key` signs with the approver's SSH key (`ssh-keygen -Y`, namespace `jarvis-approval`); with `approvals.require_signatures` the gate counts a grant only if its signature verifies against the allowed-signers file from the protected source, and the approver is whoever that file names for the key; `hardware_key_for_four_eyes` can demand a security key for no-go zones; a rejected signed grant cannot be revived by copying it; enforce mode refuses a policy that does not require signatures; `agentctl hook doctor` runs a real sign-and-verify. **Not tested with a real hardware key** (P0.16, P2.9d) (PS)
- [ ] **P2.9d** Test the touch requirement with a real hardware security key (needs P0.16), and decide whether to cache verified approvals in the per-user state directory: verification costs tens to a few hundred milliseconds for each covering grant on every check, and a cache an agent could write would let it skip verification (PS, PL)
- [x] **P2.9b** Signed ledger checkpoints (threat model gap G4), built as [ADR 0010](adr/0010-identity-and-signing.md) decided and as the owner chose (a path outside the project; contradictions fail, absence only warns): `agentctl checkpoint --key` signs the ledger's head (namespace `jarvis-checkpoint`, covering the ledger id, seq, hash and time) and appends it to a file kept outside the ledger, by default in the per-user state directory; `agentctl verify --checkpoints` fails on a bad signature, a different ledger, truncation or a rewritten history, and warns on none or a stale one. **Routine events are still unsigned, so a forged append is not detected (RT-12 stays open)** and nothing is protected after the newest checkpoint (PS)
- [x] **P2.9e** Milestone checkpoints and a stale warning, built as decided with the owner (signing stays manual: a scheduled signer needs a key without a passphrase, which anything running as the same user could use): `checkpoint --milestone` also writes the signed line to `.agents/checkpoints/` to commit through a pull request, `verify --checkpoints` reads both files, and `hook report` shows the checkpoint status, warns when there is none or it is older than 7 days, and fails on a contradiction. **Not built:** a scheduled signer, and checking the committed checkpoints in CI (P2.9f) (PL, PS)
- [x] **P2.9f** Check the committed checkpoints in CI: `agentctl checkpoint-history --base SHA` fails when, compared with the base commit, a checkpoint file was removed, an existing line was changed, reordered or removed, a file name does not match its ledger, or a new line is not a well-formed checkpoint signed by a key that the **base** commit's allowed-signers lists (so a pull request cannot bless its own key). It runs as the `checkpoint-history` CI job, which `ci-ok` waits for. **Limits:** it cannot tell whether a new checkpoint matches the real ledger (the ledger is not in git; `verify --checkpoints` does that), and a pull request can edit the check itself, which is why changes under `.github/` and `src/jarvis/core/ledger/` need a code owner (PL, PS)
- [ ] **P2.9c** Signed per-task credentials for direct callers of the gate and for MCP: a person signs a short-lived credential naming task, role and expiry, and the gate requires it. Built with the orchestrator (P4.1a) (PS)
- [ ] **P2.10** Review the log-only data (how often would the gate have blocked, how many overrides), tune thresholds, then switch to enforcing. Enforcing mode needs Claude Code 2.1.295 or later for `onFailure: block`, and P2.9a must be done first (PS, FO)
- [ ] **P2.11** Start the small red-team suite and the eval skeleton (formalised in phase 6) (PS, ME)

- [x] **P2.12** Redact secrets and personal data, and scan tool input, built as [ADR 0013](adr/0013-redaction-and-secret-scanning.md) decided with the owner: the ledger's one write point replaces secrets (well-known token formats, URL credentials, `password=` assignments, private key blocks) and personal data (emails, Luhn-valid card numbers) in an event's `data` with `[REDACTED:kind]` before hashing, leaving signatures and digests alone; the hook scans a call's input and, for a tool that writes, runs commands or sends data, a secret in it needs a human (`builtin:secret-in-input`; policy `secrets_in_input` is `human` by default or `alert`) with only the kinds recorded. **Not covered:** tool output, text outside `data`, secrets in unknown formats, and records written before this. Heuristic: expect false positives and misses (PS, PL)

Exit check: a risky action is blocked and approved by a named human, the hash chain verifies, and a budget breach parks a task.

---

## Phase 3: Memory

Goal: knowledge compiled once, retrieved cheaply, validated before it is trusted.

- [ ] **P3.1** Vault template: folders, `AGENTS.md` schema, `index.md`, `log.md`, note templates (ME)
- [ ] **P3.1a** Project-context block in `AGENTS.md`: setup, adopt, refresh and audit operations, each line stamped with the commit it was verified against, kept small. _A hand-written first version exists (`AGENTS.md` and `docs/HANDOFF.md`, checked by tests); the tooling that creates and refreshes it is still to do._ (ME)
- [ ] **P3.2** Inbox write gate, promotion command, trust levels, supersede links (ME, PS)
- [ ] **P3.3** Ingest job: a merged PR or retrospective updates affected pages and appends to `log.md` (ME)
- [ ] **P3.3a** Query operation: read `index.md` and the relevant pages; good answers are filed back as new inbox pages so knowledge compounds (ME)
- [ ] **P3.4** Lint job: stale, contradictory, orphan and unvalidated items (ME)
- [ ] **P3.5** Context assembler: hard token cap, tiered loading, tool-result clearing, recall-first compaction (ME)
- [ ] **P3.5a** Record the retrieved context ids and a memory snapshot reference in decision events, so any decision can be explained and replayed (ME, PL)
- [ ] **P3.6** `record` command: turn a mistake into a CI check, hook, policy rule, playbook or wiki note, in that order of preference (ME, PS)
- [ ] **P3.7** Graph index in SQLite built from wikilinks and frontmatter; 1 to 2 hop retrieval with a cap (ME)
- [ ] **P3.8** Memory tools exposed through the framework's MCP server: `wiki_search`, `wiki_read`, `wiki_propose` (PL)
- [ ] **P3.9** Memory metrics: context size per call, retrieval precision, repeat-mistake rate (ME)
- [ ] **P3.10** Optional: document opening the vault in Obsidian, plugin vetting and licence check (ME)

- [ ] **P3.11** Reflection job (cheap model, batched): after a failure or a human override, write a short post-mortem and propose a lesson. Reward signals are objective: CI and test results, human decisions, rework, incidents, reverts and cost. Lessons are validated before they are trusted (ME)

Exit check: a repeated mistake is captured once, validated by a human, and not repeated; context tokens per task drop against the baseline.

---

## Phase 4: Pipeline end to end

Goal: a small real ticket goes from intake to a reviewed PR with humans only at gates.

- [ ] **P4.1** Task state machine, with "nothing is done without a human" enforced in code; records the baseline revision before any change (PL)
- [ ] **P4.1a** Orchestrator service (daemon) with an event stream and the gate API, so the CLI, web and visual clients attach to one source of truth and agents keep running when a client closes (PL)
- [ ] **P4.2** One git worktree per task; sandbox permissions; edit, test and publish as separate permission levels (PL, PS)
- [ ] **P4.3** Role charters: analyst, builder, reviewer, QA, read-only ops; each with a model tier and a tool allowlist (FO, PL)
- [ ] **P4.4** Tool registry and gateway with 5 to 8 tools (Git, CI, test runner, file search, ticket read, memory tools) and risk classes (PL, PS)
- [ ] **P4.4a** Keep tool token costs low: expose only the role's tools, load schemas on demand when there are many, truncate and paginate results, cache idempotent reads, prefer scripts for bulk work (PL)
- [ ] **P4.4b** Plan-then-execute: the gateway rejects actions outside the approved plan (PS)
- [ ] **P4.5** MCP vetting process: allowlist, pinned versions and checksums, sandboxing, audience validation, no token passthrough, hashed tool definitions (PS)
- [ ] **P4.5a** Quarantine a tool or MCP server whose schema or scope changed after approval, until its owner re-approves (PS)
- [ ] **P4.6** The framework's own MCP server: `policy_check`, `request_approval`, `task_state`, `budget_status`, `wiki_*` (PL)
- [ ] **P4.7** Model gateway: `tiers.yaml`, fallbacks, different models for author and reviewer (PL)
- [ ] **P4.7a** Cost levers: prompt caching with byte-stable prefixes, batch APIs for non-urgent work, reasoning-token caps and prompt trimming, each measured separately; track the cache-hit rate (PL, ME)
- [ ] **P4.7b** Enforce `data_policy.allowed_models` in the gateway, so a repo's data only reaches approved endpoints (PS, PL)
- [ ] **P4.8** Spec template with testable acceptance criteria and non-goals; spec review gate (FO, TL)
- [ ] **P4.8a** Scale-adaptive intake: a small change skips the full spec path, a larger one gets the full path, chosen by rules and not by the agent (FO, PL)
- [ ] **P4.8b** Per-feature threat modelling step in design: a short checklist against the threat model and the OWASP list, with the result stored in the vault (PS)
- [ ] **P4.9** Verify stage: tests, SAST, secret and dependency scans, mutation testing on changed code, human-owned golden tests (PL)
- [ ] **P4.10** Review stage: AI reviewer with severity tuning; track the acted-upon rate (PL)
- [ ] **P4.11** Supply chain: dependency existence and allowlist checks, install scripts off in sandboxes, AI commit trailers, SBOM, agent and model inventory (PS)
- [ ] **P4.11a** SLSA-style provenance that links an agent session to the signed release it produced (PS, PL)
- [ ] **P4.12** Injection basics: egress allowlist, quarantined reader agent, output scanning, untrusted content to the inbox only (PS)
- [ ] **P4.12a** Canary tokens planted in sensitive data, to detect exfiltration (PS)
- [ ] **P4.13** Progressive delivery hooks: feature flags, staged exposure, rollback; production deploy stays human-gated (PL)
- [ ] **P4.14** End-to-end demo on a real ticket, with the audit log replaying every decision (all)

- [ ] **P4.15** Unattended runs: the orchestrator launches harnesses in non-interactive mode, never with confirmations disabled, and enforces limits outside the harness as well (sandbox permissions, scoped credentials, branch protection, CI) (PL, PS)

Exit check: demo ticket reaches a reviewed PR with two human gates, and the audit trail explains each step.

---

## Phase 5: Brownfield

Goal: onboard a real existing repository safely.

- [ ] **P5.0** Triage first: decide whether a system is worth modernising before investing in it, retire systems with high debt and little value, and list what an agent cannot recover (undocumented business rules, architecture decisions) so a human owns it (TL, FO)
- [ ] **P5.1** Deterministic code graph from imports, symbols, build files and dependencies (PL)
- [ ] **P5.2** Profiler: git hotspots, churn, ownership, coverage, CI health, dependency age (PL)
- [ ] **P5.3** Cheap-model module summaries into the inbox as unverified (ME)
- [ ] **P5.4** Human confirmation of the 10 to 20 most critical modules (TL)
- [ ] **P5.5** No-go zones in policy for fragile, undocumented or regulated areas (PS, TL)
- [ ] **P5.6** Characterization test generator, plus a "break it on purpose" check; golden tests human-owned (PL)
- [ ] **P5.7** Archive old greenfield planning docs out of agent reach; project-context block in `AGENTS.md` (ME)
- [ ] **P5.8** Brownfield pack: `init brownfield`, read-only tool set at start (PL)
- [ ] **P5.9** Strangler slice template: one intent per slice, parity tests, staged traffic shifts (FO, PL)
- [ ] **P5.10** Baseline metrics report, then pilot on the chosen repo and a retrospective (TL)

Exit check: one real repo onboarded, a characterized module changed through a slice, and metrics compared with the baseline.

---

## Phase 6: Evals and red team (formalise)

Goal: prompts, policies and agents are treated as code and regression-tested.

- [ ] **P6.1** Clean-room eval harness: temporary workspace, separate grader, 3 or more runs per case (ME)
- [ ] **P6.2** Regression sets per role; human-labelled judge calibration set, rechecked when the judge or rubric changes (ME)
- [ ] **P6.3** CI gates on success, latency and cost for every prompt, model or tool change (PL)
- [ ] **P6.4** Injection suite of 20 to 30 payloads across tickets, READMEs, web pages and tool output; attack success rate tracked; blast radius per role (PS)
- [ ] **P6.5** Production failure becomes a regression test, plus an online check for recurrence (ME)
- [ ] **P6.6** Per-model prompt overrides and per-model evals (ME)
- [ ] **P6.7** Retire any skill or agent that no longer beats the bare-model baseline (ME)
- [ ] **P6.8** Incident playbook and kill-switch drill (PS)
- [ ] **P6.8a** Operator runbook for a stuck lock or a corrupt ledger that restores service without skipping the audit (threat model T-4) (PS, PL)
- [ ] **P6.9** Metrics report: lead time, change-failure rate, time to restore, cycle time, cost per merged PR, rework rate, acted-upon rate of review comments, escalation and override rates, repeat-mistake rate, attack success rate and human review time, compared with the baselines from P0.8 (ME, PL)
- [ ] **P6.10** Approval-fatigue monitoring: decision-card volume, time to decision and override rate, with an alert when thresholds are crossed (PS)

Exit check: evals gate every change, attack success rate is tracked, and a kill-switch drill is completed.

---

## Phase 7: Council, router, second harness, visuals, packaging

Goal: widen the framework once the data shows it is needed.

- [ ] **P7.1** Council for two or three high-risk gates (role pattern first), vote schema, quorum, one round; evaluate it against a single reviewer and retire it where it does not help (FO, ME)
- [ ] **P7.1a** Model council (independent answers, anonymised peer ranking, chair synthesis) and debate patterns, once the role council has proved useful (ME)
- [ ] **P7.2** Learned router from ledger data (bandit-style), with routing reasons logged (ME)
- [ ] **P7.3** Second harness adapter with a conformance test (loads context, hits the gate, emits events) (PL)
- [ ] **P7.4** Second model provider behind the gateway (PL)
- [ ] **P7.5** 2D timeline and list view of events, with the same gate API for approvals (PL)
- [ ] **P7.6** 3D world, if the pilot shows value: scene mapping, human gate as a door, token meters (PL)
- [ ] **P7.7** Durable execution decision: own store, LangGraph-style checkpoints or Temporal; test crash recovery and approval resume separately (PL)
- [ ] **P7.8** Greenfield pack and stack packs (`init greenfield`) (PL)
- [ ] **P7.9** Importers for BMAD, Spec Kit and OpenSpec artifacts; `AGENTS.md` exporter (PL)
- [ ] **P7.10** Ops assistant, read-only first, with machine-readable runbooks (PL)
- [ ] **P7.11** Compliance mapping document: controls to EU AI Act, NIST AI RMF and ISO/IEC 42001 (CO, PS)
- [ ] **P7.12** Packaging, versioning, changelog and user documentation (FO, PL)
- [ ] **P7.12a** Signed releases and an SBOM for the framework itself, with a documented release process (FO, PS)
- [ ] **P7.13** Typed agent-to-agent message schemas and scoped shared memory, only if a multi-agent design is justified by evidence (charter 8.8) (PL)
- [ ] **P7.14** Generate per-harness instruction files (for example a `CLAUDE.md` that points at the canonical `AGENTS.md`) instead of maintaining several copies (PL)

Exit check: pilot metrics reviewed against baselines; keep or retire each optional component on the evidence.

---

## Continuous track (every phase)

- [ ] Security review of each new tool, MCP server and policy change
- [ ] Access review: which roles use which tools; remove unused permissions
- [ ] Dependency and model inventory updates
- [ ] Eval and red-team refresh with new payloads and harder cases
- [ ] Memory pruning and lint reports reviewed
- [ ] Metrics review: lead time, change-failure rate, time to restore, cost per merged PR, rework, acted-upon rate, override rate, repeat-mistake rate, attack success rate
- [ ] Keep the charter, ADRs, the [traceability matrix](traceability.md) and this list in sync
- [ ] Review the threat model when the hook ships (P2.6), when enforcing mode is enabled (P2.10) and before the first pilot; add a red-team case for every incident and near miss
- [ ] Re-verify a charter claim against its primary source whenever it becomes a hard requirement (charter section 16)

## Suggested first two weeks

1. P0.1, P0.3, P0.4, P0.10 (owners, decisions, pilots, repo hygiene)
2. P1.1, P1.2, P1.5 (skeleton, schemas, CI)
3. P2.1 and P2.4 (event log and the policy gate), then P2.6 in log-only mode
4. P0.8 baselines in parallel
