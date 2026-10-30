# ADR 0010: Identity and signing

- Status: Accepted (decision; nothing is built yet, see "What is built and what is not")
- Date: 2026-10-29
- Deciders: project owner (task P0.15). Each choice below was made from options put to the owner on 2026-10-29.

## Context

Threat-model gaps G1 and G4 are the same problem seen from two sides: the gate trusts names. `role` and `agent_id` are claimed by whoever calls it, an approval is attributed to whatever name was typed after `--by` ([ADR 0009](0009-approval-flow.md)), and a ledger event can be appended by anyone who can write the file. The approval flow and enforce mode (P2.10) are only as strong as the identity behind them, which is why enforce mode waits for this work (P2.9a).

**What was checked, and how.**

- *Our own test on this machine (OpenSSH 10.6, Windows):* `ssh-keygen -Y sign` took about 200 ms and `-Y verify` about 260 ms; a tampered message failed verification; generating an `ed25519-sk` (security-key) key asked for a physical touch. `cryptography` is **not** a project dependency (the project depends only on `pydantic` and `pyyaml`).
- *Secondary sources:* OpenSSH added FIDO security keys in 8.2 with touch required by default, and PIN-per-use (`verify-required`) in [8.4](https://www.openssh.org/txt/release-8.4); see [Yubico's guide](https://developers.yubico.com/SSH/Securing_SSH_with_FIDO2.html). GitHub shows [SSH-signed commits as Verified](https://github.blog/changelog/2022-08-23-ssh-commit-verification-now-supported/) (Git 2.34 or later, key added as a signing key).
- *Not verified:* which OpenSSH release added `-Y sign` (the installed 10.6 has it); whether the touch flag is enforced when verifying an `sk` signature (it needs a real key to test); GitHub's "require signed commits" branch-protection behaviour; Windows security-key driver support beyond the prompt seen above.

**The limit that shapes the decision.** A key that the operating-system user can read or use can also be used by an agent with a shell running as that user. Only a hardware key that needs a physical touch makes a signature mean "a person did this". Until the sandbox (P4.2) exists, software keys prevent mistakes and impersonation by name, not a determined agent.

## Decision

1. **Humans prove an approval with SSH signing keys, hardware where possible.** Each approver has an SSH key listed in an *allowed-signers* file kept in the protected policy source ([ADR 0004](0004-trusted-policy-source.md)), so an agent cannot add itself. Approvals are signed and the gate verifies them. The policy can require a hardware-backed key (`sk-ssh-ed25519@openssh.com`, touch required) for four-eyes and no-go zones, and allow a passphrase-protected software key elsewhere. Signatures use a dedicated namespace (`jarvis-approval`) so a signature made for another purpose, such as a git commit, cannot be replayed as an approval.
2. **Agent role and identity are launcher-bound now.** For Claude Code, the role and agent name already come from the adapter config read from the protected source, so the agent cannot change them (ADR 0004), and enforce mode keeps that. Callers of `agentctl check` directly stay advisory until the orchestrator (P4.1a) issues short-lived, signed, task-scoped credentials, as the charter's section 8.4 describes. A credential is signed by a person's key in its own namespace (`jarvis-credential`) and names task, role and expiry.
3. **What is signed:** approvals, and ledger checkpoints (the existing `SEQ:HASH` anchor, signed in the namespace `jarvis-checkpoint`). Checkpoints are kept **outside** the ledger, because a ledger an attacker can truncate could drop its own checkpoints; where they live is settled in P2.9b. Routine events (decisions, tool calls, model calls) stay unsigned: a key the agent can read would not protect them yet.
4. **Backend: `ssh-keygen`, no new dependency.** The gate and the commands call the OpenSSH tool already present on Windows, macOS and Linux. It also gives hardware-key support and uses the same keys as git commit signing. Because verification costs about 260 ms, the gate verifies only grants that would actually cover the call and caches the result in the per-user state directory, which is outside the workspace. `agentctl hook doctor` will check that `ssh-keygen -Y` works. No change to `pyproject.toml` or `uv.lock`.

## What is built and what is not

Nothing here is implemented. The work is tracked as: **P0.16** (each approver creates a signing key and adds the public key by pull request), **P2.9a** (approver signatures, allowed-signers file, hardware-key requirement, doctor check), **P2.9b** (signed checkpoints) and **P2.9c** (signed task credentials, with the orchestrator). Until P2.9a is done, approvals are still attributed to claimed names, as ADR 0009 says.

## Consequences

- **Closed once built:** an agent cannot forge an approval without a signing key, and with a hardware key it cannot forge one without a person touching the device. Removing or truncating ledger records after a signed checkpoint is detected.
- **Still open:** an agent running as you with a software key it can use (unlocked in an agent, or without a passphrase) can sign as you, so the policy should require a hardware key for no-go zones and four-eyes before enforce mode is trusted with them. Routine ledger events can still be forged by someone who can write the file (G4, in part). Direct callers of the gate keep claiming roles until P2.9c (G1, in part). The sandbox (P4.2) is what finally separates the agent from the user's keys.
- Enforce mode (P2.10) needs P2.9a first, as before. It should also wait for at least one approver to have a hardware key, otherwise four-eyes is two passphrases.
- A person who loses a key needs it removed from the allowed-signers file by a reviewed change. Revocation is therefore a pull request, which is slow but visible; key lifetimes in the allowed-signers file (secondary sources say it supports them; not verified here) could limit a key's life.
- Approvers need OpenSSH. The minimum version is not verified.

## Alternatives considered

- **GitHub identity only.** Strong for policy changes through pull requests and verified commits, and nothing for runtime approvals, which happen in a terminal.
- **An external identity provider (SSO/OIDC).** Real organisational identity and central revocation, but a service we do not have, and network at check time. Reasonable for a larger team later; the interface (a principal plus a signature) does not rule it out.
- **The `cryptography` package.** Fast in-process Ed25519, but a new supply-chain dependency, no hardware-key support, and keys that are not the ones git and GitHub use.
- **Signing every event.** Detects a forged append from another identity, but costs about 200 ms each with `ssh-keygen` and protects nothing while the writer's key is readable by the agent.
- **Signed per-task credentials now.** Closes direct callers earlier, at the price of a step for every task, and it needs the orchestrator to be worth it.
- **Per-agent local keys.** An agent that can read its own key can use it, so the key proves the process, not the intent. It becomes useful with the sandbox and is a natural fit for task credentials later.
