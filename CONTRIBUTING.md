# Contributing

Thanks for helping. This project is early, so the most useful contributions are small, tested and tied to a task in the [TODO list](docs/TODO.md).

If you are an AI coding agent, or are setting one up, read [AGENTS.md](AGENTS.md) and [docs/HANDOFF.md](docs/HANDOFF.md) first.

Please read the [charter](docs/framework-charter.md) sections 2 (principles) and 4 (autonomy tiers) first. The rules in them shape what gets accepted. In particular:

- enforce boundaries in code, not in prompts;
- fail closed: when in doubt, ask a human or deny;
- prefer a deterministic check over a model call;
- add concepts from other tools only through the adoption test in section 2.1.

## Setup

You need Python 3.12 or newer and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

## Before you open a pull request

Run the same checks CI runs. Each is one command:

```bash
uv run ruff check .
```

```bash
uv run ruff format --check .
```

```bash
uv run pytest
```

CI also scans the full git history for secrets with [gitleaks](https://github.com/gitleaks/gitleaks), so never commit a key or token, even in a commit you later remove. If you have gitleaks installed you can run the same scan first:

```bash
gitleaks git --redact --verbose --config .gitleaks.toml .
```

If you changed a schema in `src/jarvis/core/schemas/`, regenerate the JSON Schemas and commit the result. A test fails if they are out of date:

```bash
uv run agentctl schema export --out schemas
```

## Workflow

1. Pick a task from [docs/TODO.md](docs/TODO.md) (for example `P2.4`), or open an issue to discuss something new first.
2. Branch from `main` with a short name such as `feat/policy-gate` or `fix/ledger-lock`.
3. Make focused commits. Write the subject in the imperative ("Add policy engine"), and explain why in the body when it is not obvious.
4. Open a pull request into `main`. CI must pass; `main` does not accept direct pushes.
5. Update the docs in the same pull request: tick the TODO task, and record significant decisions as an ADR in `docs/adr/` (copy `0000-template.md`).

## What a good change includes

- **Tests.** New behaviour needs tests, including the failure cases. Tests cover deterministic code only; do not test the output of a language model.
- **Safe defaults.** New configuration must fail closed. Add a test that proves it.
- **No secrets or personal data.** Never commit keys, tokens, real customer data or private URLs. Examples must use made-up values.
- **Small scope.** One intent per pull request. Do not mix a refactor with a behaviour change.

## AI-assisted contributions

AI tools are welcome, and this project is built to govern them. You are responsible for anything you submit:

- read and understand the change before submitting it;
- add a `Co-Authored-By` trailer to commits that an AI tool materially wrote;
- never paste output you have not run, and never weaken or delete a test to make it pass.

## Licence

By contributing you agree that your contribution is licensed under the [Apache License 2.0](LICENSE), the same as the project, unless you state otherwise when you submit it.

## Security issues

Do not report vulnerabilities in public issues. See [SECURITY.md](SECURITY.md).
