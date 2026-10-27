# Security policy

This project is a framework for governing AI agents, so security reports are taken seriously, including reports about the framework's own safeguards.

## Supported versions

The project is in early development (version 0.0.x). Only the latest commit on `main` is supported. There are no maintained release branches yet.

## Reporting a vulnerability

Please **do not open a public issue** for a security problem.

Use GitHub's private vulnerability reporting: open the repository's **Security** tab, then **Advisories**, and choose **Report a vulnerability**. This sends the report privately to the maintainers.

If that option is not available, open a public issue that says only that you have a security concern and asks for a private contact. Do not include details in it.

Please include:

- what you found and which part of the project it affects (file, command or schema);
- steps or a minimal example that reproduces it;
- what an attacker could achieve (impact);
- the commit or version you tested.

Do not include real secrets, credentials or personal data in a report.

## What counts as a security issue here

Beyond the usual (code execution, secret exposure, unsafe file handling), these are in scope because they defeat the framework's purpose:

- a way to **bypass or weaken a policy gate**, an approval, a budget or the kill switch;
- a way to **forge, alter, delete or reorder audit records** without verification detecting it (note the documented limit: truncation and consistent rewrites need an external anchor);
- **prompt-injection paths** that let untrusted content change what an agent is allowed to do, or write trusted memory;
- **privilege or credential issues** in the tool gateway, MCP handling or identity design;
- schemas or defaults that are **unsafe by default** (for example, something that fails open).

Known gaps are listed in the [threat model](docs/threat-model.md), the [charter](docs/framework-charter.md) (section 16) and the [TODO list](docs/TODO.md). A report about something already listed there is still welcome, but may already be tracked.

## What to expect

This is a small project without a dedicated security team, so there is **no guaranteed response time**. The maintainers will try to acknowledge a report promptly, keep you informed, and credit you in the fix if you wish. Please allow a reasonable period to fix a problem before disclosing it publicly.

## Safe harbour

Good-faith research that follows this policy, avoids harming other people's data or systems, and gives us reasonable time to respond will not be pursued by the maintainers.
