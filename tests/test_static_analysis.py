"""Static analysis of our own code stays honest (P1.8): the Bandit job is wired in, nothing is
suppressed without a reason, the configuration cannot quietly grow a skip, and the one real
finding it led to (an option-like git base) stays fixed."""

import re
from pathlib import Path

import pytest
import yaml

from jarvis.cli import main

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


@pytest.fixture(scope="module")
def workflow():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_the_bandit_job_runs_on_the_source_with_our_config_and_a_pinned_version(workflow):
    job = workflow["jobs"]["static-analysis"]
    steps = " ".join(str(s.get("run", "")) for s in job["steps"])
    assert re.search(r"pip install bandit==\d+\.\d+\.\d+\b", steps), (
        "pin Bandit to an exact version"
    )
    assert "bandit -c bandit.yaml -r src" in steps
    checkout = next(
        s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout")
    )
    assert checkout["with"]["persist-credentials"] is False


def test_ci_ok_waits_for_the_static_analysis_job(workflow):
    ci_ok = workflow["jobs"]["ci-ok"]
    assert "static-analysis" in ci_ok["needs"]
    assert "needs.static-analysis.result" in str(ci_ok["steps"][0]["env"])


def test_the_configuration_skips_only_the_import_warning():
    config = yaml.safe_load((ROOT / "bandit.yaml").read_text(encoding="utf-8"))
    assert config == {"skips": ["B404"]}, "a new skip needs a reason and a review"


def test_every_suppression_names_a_test_and_gives_a_reason():
    found = 0
    for path in sorted(SRC.rglob("*.py")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines):
            if "# nosec" not in line:
                continue
            found += 1
            where = f"{path.relative_to(ROOT)}:{number + 1}"
            assert re.search(r"# nosec B\d{3}\b", line), (
                f"{where}: name the test id, not a blanket nosec"
            )
            above = lines[number - 1].strip() if number else ""
            assert above.startswith("# Reason:"), f"{where}: put `# Reason: ...` on the line above"
    assert found >= 5  # the subprocess calls were triaged, not deleted from the check


# ---------- the finding the first run led to ----------


@pytest.mark.parametrize("base", ["--output=stolen.txt", "-x", "a..b", "bad base", "x" * 300])
def test_checkpoint_history_refuses_a_base_git_could_read_as_an_option(base, capsys):
    assert main(["checkpoint-history", f"--base={base}"]) == 2  # the form argparse lets through
    assert "not an acceptable base commit" in capsys.readouterr().err


def test_checkpoint_history_still_accepts_a_sha_a_branch_and_a_remote_ref():
    from jarvis.checkpoint_history_cli import _BASE

    for ok in ("a" * 40, "main", "origin/main", "refs/heads/feat/x", "HEAD~3", "v1.2.3"):
        assert _BASE.match(ok), ok
