# ADR 0012: A local gate service for the Claude Code hook

- Status: Accepted (design only; nothing is built yet, task P2.6f builds it)
- Date: 2026-11-22
- Deciders: project owner (task P2.6e), who accepted the design on 2026-11-22. Written by Claude from the measurements below.

## Context

Every Claude Code hook call starts a new Python process. Round 2 of the P2.6e measurements (Windows, warm cache, median of 9 runs) found:

| What | Time |
|---|---|
| Bare Python start | 70 ms |
| `from pydantic import BaseModel` | about 370 ms |
| `import jarvis.core.schemas` | about 610 ms |
| A whole `agentctl hook run` | about 700 ms |
| A stdlib-only client sending one line to a loopback server that answers at once | 99 to 120 ms |

The gate's own work is about 10 ms, so the call is almost all import time. Lazy imports of `subprocess`, `urllib` and `tempfile` would save about 30 ms, which is inside the noise (about 30 ms), and pydantic's `defer_build` saved nothing measurable. The hook path validates the policy, config and events with pydantic, so no amount of lighter importing gets near the target of well under 200 ms. At 700 ms per call, registering every event and enforcing are both impractical; `--minimal` registration only halves the cost.

[ADR 0003](0003-claude-code-hook-interface.md) decision 9 already said: if the hook is too slow, move to an `http` hook talking to the orchestrator service (P4.1a). The orchestrator does not exist. This ADR designs the smallest service that removes the start-up cost and that the orchestrator can later absorb.

**One fact checked here:** on this machine (Python 3.14.7, Windows) `socket.AF_UNIX` is not available, so the transport cannot be a Unix socket on Windows. Everything else below is a design, not a measurement.

## Decision

1. **One service, the same code.** `agentctl gate serve` runs one long-lived process per user. For each request it calls the same `handle(payload, HookContext)` that `agentctl hook run` calls today, and returns its `HookResult` (exit code, stdout, stderr). It contains no decision logic of its own, so the verdicts cannot differ. Imports happen once. A test sends the same payloads through both paths and compares exit code, output and the recorded event (ids and timestamps aside).
2. **The service keeps code, not state.** Policy, registry, adapter config, the halt file, session state and the ledger are read from disk on every request, exactly as now, through the trusted policy source of [ADR 0004](0004-trusted-policy-source.md). So `agentctl halt`, a changed policy at a moving ref and a new session all take effect on the next call, with no restart. The service may later cache parsed policy keyed by the SHA-256 of the file text, but only if measured to matter. Cross-process safety stays with the existing file locks (the lock file is created with `O_EXCL`, so it works between threads and between the service and a fallback process).
3. **A tiny client in place of the full command.** The hook command becomes one stdlib-only file, run as `python -I -S <path>`, that reads stdin, forwards it with the hook's flags (`--mode`, `--policy-source`, `--policy-ref`, `--policy-dir`, `--config`, `--state-dir`) and the environment variables the hook reads (`CLAUDE_PROJECT_DIR`, `JARVIS_*`), prints what comes back and exits with the returned code. Expected cost is the measured floor plus the gate's 10 to 20 ms: about 120 to 140 ms.
4. **Transport: loopback TCP.** The service binds `127.0.0.1` on an ephemeral port, never another address, and drops any peer that is not loopback. It writes an endpoint file in the state directory (the directory the gate already treats as a no-go path) with port, pid, protocol number, `jarvis.__version__` and a random 256-bit token. Each request carries the token; a wrong or missing token closes the connection without a reply and without reading further. The token and the request are never written to the ledger or to logs. Windows named pipes stay a candidate if loopback TCP proves awkward; they are not chosen because the standard library has no simple client for them.
5. **Fail closed, with one rule about when fallback is allowed.**
   - *Nothing was sent* (no endpoint file, connection refused, stale pid): the client runs the full in-process path, as today. The answer is correct and slow, and in enforce mode a service that is down never turns into "allow".
   - *The request was sent and no valid answer came back* (timeout, connection closed, malformed reply, protocol or version mismatch, an exit code other than 0 or 2): in enforce mode exit 2 with a short reason; in log-only mode exit 0 with a note on stderr. **There is no fallback after sending**, because the service may already have appended to the ledger or counted against a budget, and running again would count the call twice.
   - *The client itself fails*: in enforce mode exit 2. On Claude Code before 2.1.295 a crash still lets the call through, which ADR 0003 decision 4 already covers by refusing enforce mode below that version.
   - The service's own errors are handled by `handle`, which already blocks in enforce mode. The client accepts only exit codes 0 and 2 and ignores everything else in the reply, so it can never relay an `allow` or an `updatedInput`.
6. **Concurrency.** Requests are handled on threads, with the call to `handle` serialised by one lock at first. Claude Code runs parallel tool calls' hooks in parallel, and at 10 to 20 ms a call the queue stays short; this is checked by a test with many simultaneous clients and a cumulative budget (the T-9 case), not assumed.
7. **Lifecycle.** `agentctl gate start` (detached), `serve` (foreground), `status` and `stop`. The service exits after an idle period (default 30 minutes) so it does not linger. Before answering, it stats its own source files and exits if they changed, so a `git pull` or an agent edit to the gate's code cannot leave an old version enforcing; the next call falls back to the in-process path until it is started again. `agentctl hook doctor` pings the service and reports its version, uptime and the policy hashes it sees. Whether `SessionStart` should start it on its own is the owner's call (see below).
8. **Every event goes through the service**, so with the cost gone all hook events can be registered, which was the point of P2.6e.
9. **Out of scope here:** an `http` hook (Claude Code posting straight to the service, with no client process, so the cost would be near the gate's own 10 to 20 ms). It is recorded as the next step, P2.6g, because it needs documentation checks listed below. It also corrects a sentence added to the P2.6e task on 2026-11-22: ADR 0003 says `"onFailure": "block"` applies to HTTP hooks as well as command hooks (Claude Code 2.1.295 or later), so an `http` hook can fail closed on a connection failure there. What it cannot do is fall back to the in-process path.

## Consequences

- **Faster, with the same verdicts.** The gate path drops from about 700 ms to roughly 120 to 140 ms, and the code that decides is untouched.
- **A new process and a new bypass to reason about.** An agent running as the same user can read the token, stop the service, or start an impostor on another port and rewrite the endpoint file. An impostor that answers "exit 0, no output" looks the same as a real "no objection", so in enforce mode it could silence the gate. This is the same trust level as today (the agent can already edit anything the user can unless the gate stops it: G1, G2, ADR 0003 decision 7, threat model T-11), and it is no worse in kind, but it is a new place to attack. The controls are: the endpoint file and the service code are no-go paths for the gate; every real reply carries the policy hashes and the ledger sequence number it appended, which `hook doctor` and `hook report` can spot-check; and the real fix remains the sandbox (P4.2) or managed settings. Recorded as T-13 in the threat model.
- **Stale code is bounded, not eliminated**, by the source stat check and the version in the endpoint file.
- **More to operate.** A stuck or crashed service shows up as slow calls (fallback) rather than wrong ones, but someone must notice. `hook doctor` and `hook report` should say when calls are taking the slow path.
- **The orchestrator inherits it.** When P4.1a arrives, `gate serve` becomes one endpoint of that daemon; the client and the protocol stay.
- **Windows file permissions** on the endpoint file are not verified to be user-only beyond the profile directory's own permissions. Treat the token as readable by anything running as the user.

## Not verified (must be checked before or while building)

- That a Windows loopback client really stays near 120 ms against the real service, rather than the stand-in used here, and what Windows Defender does to a long-lived listening process.
- That the existing file locks behave under many threads in one process (they are lock files, so they should, but nothing has tested it).
- Claude Code's `http` hook details for P2.6g: whether loopback URLs are allowed, how a header can carry a secret without putting it in settings, what a non-2xx reply or a refused connection does on 2.1.295 with `onFailure: block`, and the exact JSON that blocks a call.
- Whether Claude Code kills child processes that outlive a hook (it matters for starting the service from `SessionStart`).
- That `python -I -S` can run the client file from a project `.venv` the same way on Linux and macOS.

## Alternatives considered

- **Validate the hook path without pydantic.** Fast, but it duplicates the safety validation that [AGENTS.md](../../AGENTS.md) says not to weaken, and the two copies would drift. Rejected.
- **Go straight to the `http` hook.** Lowest latency and no client process, but its documentation is unverified here and it has no in-process fallback, so a service that is down blocks everything on 2.1.295 and fails open before it. Better as a second step on top of the same service (P2.6g).
- **A client in a compiled language** (Go or Rust, a few milliseconds to start). It would beat 100 ms, but it adds a second language and toolchain that [ADR 0001](0001-language-storage-and-layout.md) chose not to have. Revisit only if 120 ms proves too slow.
- **Smaller changes to the current command.** Lazy imports (about 30 ms) and `--minimal` registration (about half the calls) are worth doing anyway but cannot reach the target.
- **Fall back after a failed send.** Simpler for the user, but it can double-count ledger events and budgets. Rejected in decision 5.
- **A Unix socket or a named pipe.** Better access control than TCP with a token on Linux and macOS, but `AF_UNIX` is missing on Windows here, which is where the project is developed. Could be added per platform later behind the same client.
- **Do nothing and accept 700 ms.** Cheap, and it keeps `--minimal` registration and log-only mode working, but enforce mode on every tool call would add several seconds to a normal session.

## Decisions by the owner

1. **The design is accepted** (2026-11-22), which lets P2.6f start.
2. **Whether `SessionStart` starts the service by itself is still open.** Until the owner answers, P2.6f builds only the manual `agentctl gate start`, and the service is never started by a hook.
3. **Idle timeout:** the 30-minute default stands unless the owner changes it.
