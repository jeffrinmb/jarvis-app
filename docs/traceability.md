# Traceability: from requirements to tasks

This answers one question: **is everything we decided to build tracked by a task?** Each row names a requirement (from the conversations that shaped the [charter](framework-charter.md), the charter itself, or the [threat model](threat-model.md)), where it is defined, the tasks in the [TODO list](TODO.md) that deliver it, and where it stands.

Two tests keep this honest: every task id used here must exist in the TODO list, and every task in the TODO list must appear here. A new task therefore cannot be added without saying which requirement it serves. The tests check that the ids line up, not that the mapping is a good one; reviewers still have to judge that.

Status: **built** (code and tests exist), **partly built**, **planned**, **decision** (a person has to decide).

## Core requirements

| # | Requirement | Defined in | Tasks | Status |
|---|---|---|---|---|
| 1 | AI across the SDLC inside enforced boundaries (autonomy tiers) | Charter 4 | P2.4, P2.5, P4.1, P4.2, P4.15 | Partly built: the policy gate |
| 2 | Only major decisions reach a human | Charter 4.1 | P2.4, P2.7, P2.7a, P2.7b, P2.7c, P2.10, P6.10 | Partly built: the gate decides; approvals planned |
| 3 | Human in the loop: park and resume, attributed approvals, override and stop | Charter 4, 8.11 | P2.7, P2.8, P4.1, P7.7 | Planned |
| 4 | Council for high-risk decisions | Charter 4.2 | P7.1, P7.1a | Planned |
| 5 | Token optimisation and proper token usage | Charter 7.3, 8.1, 8.2 | P2.2, P2.8, P3.5, P4.4a, P4.7a, P6.3 | Planned |
| 6 | The right model for each task (routing) | Charter 8.1 | P4.3, P4.7, P4.7b, P7.2, P7.4 | Planned |
| 7 | Auditability | Charter 8.7 | P2.1, P2.1a, P2.1b, P2.3, P2.12, P3.5a, P4.14 | Partly built: the hash-chained ledger |
| 8 | Accountability: named humans, no self-approval | Charter 8.11, 9 | P0.1, P2.7, P2.9, P2.9a, P2.9b | Partly built: rules in the schemas |
| 9 | Learning from mistakes (a feedback loop, not weight updates) | Charter 5.9 | P3.6, P3.11, P6.5, P7.2 | Planned |
| 10 | Memory as an LLM-wiki on Markdown, Obsidian optional | Charter 7.1 | P3.1, P3.1a, P3.2, P3.3, P3.3a, P3.4, P3.10 | Planned |
| 11 | Graph memory and cheap retrieval | Charter 7.2 | P3.7, P3.8, P3.9 | Planned |
| 12 | Reusable on greenfield and brownfield projects | Charter 5.8, 6 | P0.4, P5.0, P5.1, P5.2, P5.3, P5.4, P5.5, P5.6, P5.7, P5.8, P5.9, P5.10, P7.8 | Planned |
| 13 | Multiple harnesses and models | Charter 8.12 | P2.6, P2.6a, P2.6c, P4.7, P7.3, P7.4, P7.14 | Partly built: the hook interface is documented in ADR 0003 |
| 14 | Governed tool and MCP access | Charter 8.3 | P4.4, P4.5, P4.5a, P4.6 | Planned |
| 15 | Prompt-injection resistance | Charter 8.5 | P2.5, P2.6b, P4.4b, P4.12, P4.12a, P6.4 | Partly built: taint and the trifecta rule |
| 16 | Software supply chain | Charter 8.6 | P1.5, P1.5a, P1.5b, P4.11, P4.11a, P7.12a | Partly built: pinned actions, lockfile, secret scan |
| 17 | Agent identity and short-lived credentials | Charter 8.4 | P2.9, P2.9a, P2.9b | Planned |
| 18 | Observability with OpenTelemetry and one trace id | Charter 8.7 | P2.3 | Planned |
| 19 | Evals, red team and regression tests for the framework itself | Charter 8.10 | P2.11, P6.1, P6.2, P6.3, P6.4, P6.5, P6.6, P6.7, P6.8 | Planned |
| 20 | Governance and regulatory alignment | Charter 8.11 | P0.6, P2.1b, P7.11 | Planned |
| 21 | Measure outcomes, not perception | Charter 8.11, 14 | P0.8, P0.9, P6.9 | Planned |
| 22 | Durable execution for long-running and resumable work | Charter 8.9 | P7.7 | Planned |
| 23 | Multi-agent discipline: start simple, justify each agent | Charter 8.8 | P7.13 | Planned |
| 24 | Visual layer: a 2D timeline first, then an optional 3D world | Charter 8.13 | P4.1a, P7.5, P7.6 | Planned |
| 25 | Relate to Spec Kit, Kiro, OpenSpec and BMAD without depending on them | Charter 13.1, 13.2 | P0.11, P0.14, P7.8, P7.9 | Planned |

## SDLC phase practices (charter section 5)

| Phase | Tasks |
|---|---|
| 5.1 Intake and requirements | P4.8, P4.8a |
| 5.2 Design and architecture | P4.3, P4.8b |
| 5.3 Build | P4.1, P4.2, P4.11, P3.5 |
| 5.4 Test and verification | P4.9 |
| 5.5 Code review | P4.10, P7.1 |
| 5.6 Integrate, release and deploy | P4.13, P4.5a, P4.11a |
| 5.7 Operate and respond | P6.8, P7.10 |
| 5.8 Maintain, modernise and brownfield | P5.0, P5.1, P5.2, P5.3, P5.4, P5.5, P5.6, P5.7, P5.8, P5.9, P5.10 |
| 5.9 Learn and improve | P3.6, P3.11 |

## Threat-model gaps and threats

Each gap in the [threat model](threat-model.md) has an owner task, so none is only a sentence in a document.

| Gap or threat | Tasks | Status |
|---|---|---|
| G1: `role` and `agent_id` are claimed, not authenticated (ASI03, RT-08) | P2.9, P2.9a | Planned; must precede enforcing mode |
| G2: policy, registry and project files are ordinary files (T-2, RT-15) | P2.4b, P4.2 | Planned |
| G4: ledger events are unsigned (ASI07, RT-12) | P2.9b | Planned |
| G5: no kill switch or anomaly detection (ASI08, ASI10, RT-14) | P2.8, P2.8a | Planned |
| T-1: ledger truncation or rewrite | P2.1a, P2.1b | Partly built: anchors exist, automation planned |
| T-3: the action is not the real tool call (RT-03) | P2.6, P2.6a, P2.6b | Planned |
| T-11: the hook is removed, disabled or bypassed (RT-18 to RT-20) | P2.6, P2.6c, P4.2, P4.15 | Planned |
| T-4: the gate is unavailable, or recovery skips the audit | P6.8a | Planned |
| T-5: secrets or personal data in the repo or the ledger | P1.5, P1.5b, P2.12 | Partly built: secret scan in CI |
| T-6, T-7: CI and dependency tampering | P1.5, P1.5a, P1.8 | Partly built |
| ASI09: persuasive explanations win approvals (RT-13) | P2.7c, P6.10 | Planned |

## Engineering and repository tasks

| Area | Tasks | Status |
|---|---|---|
| Schemas as the contract, with a changelog | P1.1, P1.2, P1.3, P1.4 | Built |
| Threat model | P1.7 | Built |
| Decisions log and ADRs | P0.3, P0.12, P1.6 | Partly built: ADRs 0001 and 0002 |
| Repository settings, licence and copyright | P0.10, P0.10a | Partly built |
| Project name and trademarks | P0.13 | Decision |
| Policy gate follow-ups: per-day limits, an index, path-derived risk | P2.4, P2.4a | Partly built |
| Ledger | P2.1 | Built |
| Charter approval and the claims that still need a primary source | P0.2, P0.7 | Planned |
| Data policy for which models see which repos (enforced by P4.7b) | P0.5 | Decision |
| Owners and roles | P0.1 | Decision |
| Release process for the framework | P7.12, P7.12a | Planned |
| Defence-in-depth static analysis of our own code | P1.8 | Planned |

## Deliberately not tracked

These are out of scope for the first version (charter section 1) and have no tasks on purpose:

- fine-tuning or reinforcement learning on model weights;
- replacing existing ticketing, CI or source control;
- a custom terminal UI (nebula covers that role);
- fully autonomous production deploys: production stays human-gated;
- a claim of being injection-proof: the aim is injection-resilient.

If one of these becomes a goal, add tasks here first.
