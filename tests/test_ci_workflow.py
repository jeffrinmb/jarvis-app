"""Static checks on the CI workflow, so its safety properties cannot drift unnoticed."""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


@pytest.fixture(scope="module")
def workflow():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def all_steps(workflow):
    for job_name, job in workflow["jobs"].items():
        for step in job.get("steps", []):
            yield job_name, step


def test_every_action_is_pinned_to_a_full_commit_sha(workflow):
    unpinned = [
        f"{job}: {step['uses']}"
        for job, step in all_steps(workflow)
        if "uses" in step and not re.search(r"@[0-9a-f]{40}$", step["uses"])
    ]
    assert not unpinned, f"pin these to a commit SHA (with the version in a comment): {unpinned}"


def test_workflow_has_read_only_token_permissions(workflow):
    assert workflow["permissions"] == {"contents": "read"}


def test_every_job_has_a_timeout(workflow):
    missing = [name for name, job in workflow["jobs"].items() if "timeout-minutes" not in job]
    assert not missing, f"jobs without timeout-minutes: {missing}"


def test_checkout_does_not_persist_credentials(workflow):
    risky = [
        job
        for job, step in all_steps(workflow)
        if str(step.get("uses", "")).startswith("actions/checkout")
        and step.get("with", {}).get("persist-credentials") is not False
    ]
    assert not risky, f"checkout steps that leave the token in git config: {risky}"


def test_ci_ok_waits_for_every_other_job(workflow):
    others = {name for name in workflow["jobs"] if name != "ci-ok"}
    ci_ok = workflow["jobs"]["ci-ok"]
    assert set(ci_ok["needs"]) == others, "add new jobs to ci-ok's needs"
    assert ci_ok["if"] == "always()"
    script = ci_ok["steps"][0]["run"]
    for name in others:
        assert f"needs.{name}.result" in str(ci_ok["steps"][0]["env"]), f"ci-ok ignores {name}"
    assert '!= "success"' in script


def test_secret_scan_checks_full_history_with_a_pinned_checksummed_tool(workflow):
    job = workflow["jobs"]["secret-scan"]
    checkout = next(
        s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout")
    )
    assert checkout["with"]["fetch-depth"] == 0
    install = next(s for s in job["steps"] if s.get("name") == "Install gitleaks")
    assert re.fullmatch(r"\d+\.\d+\.\d+", install["env"]["GITLEAKS_VERSION"])
    assert re.fullmatch(r"[0-9a-f]{64}", install["env"]["GITLEAKS_SHA256"])
    assert "sha256sum -c" in install["run"]
    scan = next(s for s in job["steps"] if s.get("name") == "Scan history for secrets")
    assert " git " in scan["run"] and "--redact" in scan["run"]


def test_gitleaks_config_extends_the_default_rules():
    text = (ROOT / ".gitleaks.toml").read_text(encoding="utf-8")
    assert re.search(r"^useDefault\s*=\s*true", text, re.MULTILINE)


def test_dependabot_keeps_actions_and_python_dependencies_current():
    config = yaml.safe_load((ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
    ecosystems = {u["package-ecosystem"] for u in config["updates"]}
    assert {"github-actions", "uv"} <= ecosystems
