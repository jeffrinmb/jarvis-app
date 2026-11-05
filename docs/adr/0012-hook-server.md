# ADR 0012: A local server for the Claude Code hook

- Status: Accepted (the server and its client are built; the `http` hook route is not)
- Date: 2026-11-04
- Deciders: project owner (task P2.6f). The three main choices were made from options put to the owner on 2026-11-04.

## Context

The hook runs before every tool call, and with the events that matter registered it runs after each one too. A new Python process per call spends almost all of its time importing pydantic and our own modules ([dogfood log, rounds 3 and 4](../dogfood-log.md)). Importing less recovered only a few percent (task P2.6e), so the remaining cost needs a process that stays alive.

**What was checked, and how.** Claude Code's own `http` hook type is the obvious way to avoid starting a process. The [hooks reference](https://code.claude.com/docs/en/hooks) was read in full by a fetching agent (primary source):

- `http` hooks are supported for `PreToolUse`, `PostToolUse`, `ConfigChange`, `Stop`, `SubagentStop` and `SessionEnd`, but **not for `SessionStart`**, which is `command` or `mcp_tool` only.
- A connection failure, a timeout or a non-2xx answer is a *non-blocking* error: the action proceeds, unless `"onFailure": "block"` is set. That field needs Claude Code **2.1.295 or later** and has no effect on `Stop` or `SubagentStop`. This machine has 2.1.276.
- The page does **not** say which version introduced `http` hooks, whether `localhost` URLs are allowed, how redirects or proxies are handled, or give any security guidance for them. The default timeout is 600 seconds.

So the `http` route cannot be relied on for this machine without a live test, and on this version a missing server would let calls through. A client that is itself a `command` hook has none of those problems.

**Measured (our own tests, the same session, on a Windows machine whose speed varies by a factor of two or three between hours):**

| | per call |
|---|---|
| A new Python process per call, today | about 480 to 570 ms (bare Python about 110 ms) |
| The warm handler, in-process | 21 ms (`PreToolUse`), 9 ms (`PostToolUse`) |
| The tiny client plus a warm server | about 230 to 280 ms (bare Python about 150 ms in that run) |

The client's own cost over a bare interpreter fell from about 370 ms to about 75 ms.

## Decision

1. **A local server, `agentctl hook serve`, and a tiny client.** The server runs the same `hook_cli.execute` the in-process hook runs, so a decision and its ledger records are identical wherever it is made. The client is `agentctl hook run --server`, standard library only (`json`, `os`, `socket`, `time`, `zlib`): it does not import pydantic, YAML or the hook. It is opt-in per command line (or `JARVIS_HOOK_SERVER=1`), so existing settings keep working unchanged and nothing starts a background process until a person edits them (`agentctl hook snippet --server`).
2. **Never decided twice, never undecided.**
   - If the server cannot be reached, or rejects a request *before processing it* (bad secret, bad host, malformed body), the client decides **in-process**, with the same code, only slower.
   - If a request was *sent* and no valid answer came back, it may have been processed, so the client does not decide again: in **enforce mode it blocks the call**, and in log-only mode it lets it through with a note on stderr.
   - A server that fails to start is remembered for a minute, so calls do not each wait for it.
3. **Auto-start on first use, idle shutdown** (the owner's choice). The first call starts a detached server for its spec, with a lock so concurrent calls start one, and waits up to six seconds. It exits after 30 idle minutes (`JARVIS_HOOK_SERVER_IDLE_MINUTES`), and `agentctl hook serve --stop` ends it; `--status` reports it.
4. **One server per spec.** A spec is the resolved project directory, mode, adapter config path, state directory, policy source and ref, accountable user and a **fingerprint of the code**. The client compares the spec stored in the server's discovery file with its own exactly, so a different mode or project never reaches another's server, and editing the code starts a new one instead of using a stale one. The file names use a cheap checksum, which is not a security boundary.
5. **The endpoint is closed to everyone but the client.** It binds `127.0.0.1` on a port the operating system picks. Every request needs a random 256-bit secret as a bearer token, kept in a per-user file next to the discovery file (outside the project), compared in constant time (the owner's choice). The `Host` header must be the server's own address (against DNS rebinding), the body must be `application/json` (a web page cannot send that to another origin without a preflight the server never answers), the body is capped at 8 MiB, and requests are never logged. A request is rejected before anything is processed.
6. **No policy cache.** Each request re-reads the policy, registry and adapter config from the policy source, exactly like a fresh process, so a policy change is seen on the next call. That costs about 20 ms warm, which is not worth a staleness question.

## Consequences

- About 2 times faster per call on this machine, with the overhead above bare Python cut about five times. It does **not** reach the 200 ms target on its own: the Python start (about 110 to 150 ms here) is still paid on every call. Only the `http` route removes that.
- **A same-user limit that stays.** The secret is a file the same user can read, so an agent running as that user can read it and talk to the server, or kill it. This stops other programs, web pages and other users, not an agent with a shell, the same limit as the kill switch and the approval flow until the sandbox (P4.2). In enforce mode a killed server means the client falls back to in-process, which still decides and logs, so killing it gains the agent nothing.
- **A forged event needs the secret.** Anyone holding it could post hook events (adding bogus decisions or outcomes to the ledger). It is the same class of access as writing the ledger file (G4), and signed checkpoints (ADR 0010) still detect rewrites.
- On Windows a venv's `pythonw.exe` is a launcher, so one server shows as two `pythonw.exe` processes. Harmless.
- A server that dies mid-request in enforce mode blocks that one call (by design) and the next call falls back.
- The client stays standard-library only; a test checks that a call that finds a server loads neither pydantic nor the hook.

## Found in live use (2026-11-04), and fixed

The server was switched on for the maintainer's real Claude Code hook on the same day. Two things the tests had not caught:

1. **A terminal window opened on Windows.** The server was started with `DETACHED_PROCESS`, which leaves it with no console. A venv's `python.exe` is a launcher that starts the real interpreter, a console program, and with no console to inherit Windows opened a new Windows Terminal window for it (title: the venv's `python.exe`). Fix: on Windows the server runs under **`pythonw.exe`**, the windowed interpreter, which never creates a console, with the same creation flags as before. An attempt to start it with `CREATE_NO_WINDOW` alone (without `DETACHED_PROCESS`) was found dead within about 30 seconds under the real hook, and the reason was not established, so the flags that were observed to survive were kept and a test now pins them. Verified live: the server starts, survives and no window opens.
2. **A killed server left the client stuck on the slow path, silently.** The discovery and secret files outlive the process. After a server was killed (not stopped), crashed, or the machine restarted, the client trusted the stale files, got "connection refused" from the dead port, decided in-process and **never started a replacement**, on every call afterwards. The earlier recovery test had used a graceful `--stop`, which cleans up, so it never showed this. Fix: a connection that never reaches a server marks the files stale; the client removes them, starts a new server and tries once more (the request had not been processed, so this cannot decide twice). Tests start from stale files, with and without the ability to start a server, and one mutation check confirms they fail without the fix. Verified live by killing the running server hard and making a call: a new server started at once.

Neither was a safety problem (the client's fallback decided every call correctly), which is the design working, but the first was visible and the second would have quietly removed the whole benefit.

## Not built, and tracked

- **P2.6g, `http` hooks** for the tool events (about 50 ms per call, no process start): needs a live test on a Claude Code that supports them, a fixed port and a secret in the URL path of the git-ignored local settings, `SessionStart` kept as a command hook, and **2.1.295 or later** for `onFailure: "block"` in enforce mode (there is no in-process fallback on that route).
- Not tested on Linux or macOS: the detached start (`start_new_session`), owner-only file permissions (`0600`) and the temp directories. CI runs the tests on Linux, which is the first check.
- Not measured over a long session: memory growth, and many parallel calls beyond eight in a test.
- A bound port could be refused by a firewall prompt on some machines; none appeared here (loopback).

## Alternatives considered

- **`http` hooks first.** Fastest, but untested on this Claude Code version, with no in-process fallback and no blocking on failure below 2.1.295. Kept as the follow-up.
- **A bearer token from an environment variable.** Cleaner for `http` hooks, but it works only if Claude Code (including the desktop app) passes that variable, and it adds nothing for the client route.
- **No secret.** Any program on this machine could post fake events.
- **Explicit start only.** Nothing hidden, but a forgotten server means every call quietly takes the slow path.
- **A named pipe or Unix socket with the standard library's `multiprocessing.connection`.** Not reachable from a browser at all, but its import cost is higher and the same endpoint could not serve a future `http` hook, which needs TCP.
- **Rewriting the hook without pydantic.** Large, and it would fork the validation the whole framework relies on.
