# ADR 0004: Trusted policy source

- Status: Accepted
- Date: 2026-10-28
- Deciders: project owner (task P2.4b). Enforce mode itself stays a separate owner decision (P2.10).

## Context

The policy gate decides what an agent may do, but until now the hook read the policy, the tool registry, the project file and its own adapter config from the project directory. Those are ordinary files, and the agent works in that directory. An agent that can write `.agents/policy.yaml` can rewrite the rules it is judged by (threat model T-2, red-team case RT-15, gap G2). The same is true of `.agents/claude-code.yaml`, which can remap tools, mark paths trusted or lower the mode.

Hashes of these files were already recorded with every decision, which makes tampering visible after the fact. It does not stop it.

## Decision

The hook reads all four files from a **policy source**, and the source decides whether the files can be changed by the agent.

| Source | Reads | Agent-writable | Allowed in enforce mode |
|---|---|---|---|
| `worktree` (default) | files under the project directory | yes | **no** |
| `directory` | files under a directory outside the project | no, if the agent has no write access there | yes |
| `git` | the content of the files at a git ref or commit, with `git show` | no: working-tree edits and commits on the agent's branch do not change it | yes |

1. **Enforce mode refuses an agent-writable source.** The hook blocks with a message saying why, `agentctl hook snippet --mode enforce` refuses to print a settings block without a protected source, and `agentctl hook doctor --mode enforce` reports a problem. Log-only mode may use the working tree because it never blocks.
2. **The choice of source cannot come from a file the agent can write.** It is set by `--policy-source`, `--policy-ref` and `--policy-dir` in the hook command (so in the settings block, which `ConfigChange` protects), or by `JARVIS_POLICY_SOURCE`, `JARVIS_POLICY_REF` and `JARVIS_POLICY_DIR`. There is no setting for it in the adapter config.
3. **The adapter config is read from the same source.** With a protected source, `.agents/claude-code.yaml` comes from the protected content, and a missing file means the safe defaults. A copy in the working tree is ignored.
4. **A git ref is resolved once per hook run** to a full commit SHA, and every file is read from that commit, so the four files are always from one consistent version. The ref must look like a ref (no leading `-`, no `..`, no spaces) and `GIT_*` environment variables are removed before git runs, so neither can redirect the lookup.
5. **Missing, unreadable, non-UTF-8 or invalid files are errors,** never an empty policy. Relative paths cannot leave the source (`..`, absolute paths, drive letters, and links that resolve outside a directory source are refused).
6. **Every decision records where it was judged:** `policy_source` (for example `git:<ref>@<12 hex>` or `worktree`), `policy_source_protected`, and the SHA-256 of each file's exact bytes. Nothing from the files' content is recorded.

## Consequences

- Editing the policy in the working tree, or committing an edit on the agent's own branch, no longer changes what is enforced. Changing the rules means a reviewed merge (CODEOWNERS and the `ci-ok` rule already apply to `.agents/`), then moving the pinned commit.
- **Pin a full commit SHA for the strongest guarantee.** A branch name such as `origin/main` is a moving ref. An agent with a shell can move a local branch or remote-tracking ref (`git update-ref`, `git fetch` into it) until the sandbox of task P4.2 stops that. The default ref exists for convenience and the guide says so. This residual risk is why gap G2 is narrowed and not closed.
- A `directory` source is only as protected as that directory's permissions. It must not be inside the agent's writable area.
- The cost is one `git` call to resolve the ref plus two per file. On Windows this is noticeable (tens of milliseconds per call); it adds to the hook's per-call cost tracked in P2.6e.
- Changing policy now has a deliberate delay, which is the point: a human or a pull request sits between the agent and its own rules.
- Identity is still claimed and not authenticated (G1, P2.9a). A protected source stops the agent editing the rules; it does not stop it claiming a different role. Enforce mode still waits for P2.9a.

## Alternatives considered

- **Hash pinning only.** Record, and optionally require, an expected SHA-256 of each file. It detects tampering and fails closed, but the pinned value would itself live in a file the agent can edit unless it is also moved to the command line, and every legitimate policy change then needs two edits. A pinned commit gives the same guarantee and also the content.
- **Make the policy files read-only on disk.** Cheap and useful as defence in depth, but an agent with a shell can change permissions, and it is not portable between Windows and Linux. It belongs to the sandbox work (P4.2).
- **A signed policy bundle.** Strongest against a rewritten ref, but needs a key and a signing flow, which depend on the identity decision (P0.15, P2.9b). Revisit then.
- **Managed settings only.** Claude Code's managed policy settings cannot be edited from below, which is ideal for the hook configuration, but they do not hold our policy files and are not available on every machine. Recommended alongside this ADR, not instead of it.
