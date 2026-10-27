"""Guards that keep the docs honest: links resolve, the changelog covers every schema,
and every test the threat model cites actually exists."""

import re
from pathlib import Path

import pytest

from jarvis.core.schemas import SCHEMA_VERSION, SCHEMAS

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

MARKDOWN_FILES = [
    ROOT / "README.md",
    ROOT / "CONTRIBUTING.md",
    ROOT / "SECURITY.md",
    *sorted(DOCS.rglob("*.md")),
]

_FENCE = re.compile(r"```.*?```", re.DOTALL)
_LINK = re.compile(r"\]\(([^)\s]+)\)")


@pytest.mark.parametrize("path", MARKDOWN_FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_relative_markdown_links_resolve(path):
    text = _FENCE.sub("", path.read_text(encoding="utf-8"))
    broken = []
    for target in _LINK.findall(text):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        file_part = target.split("#", 1)[0]
        if file_part and not (path.parent / file_part).exists():
            broken.append(target)
    assert not broken, f"broken links in {path.name}: {broken}"


def test_every_schema_is_at_the_documented_version_and_in_the_changelog():
    changelog = (DOCS / "schema-changelog.md").read_text(encoding="utf-8")
    for name, model in SCHEMAS.items():
        assert f"`{name}`" in changelog, f"schema {name!r} is missing from docs/schema-changelog.md"
        default = model.model_fields["schema_version"].default
        assert default == SCHEMA_VERSION
        assert re.search(rf"\|\s*`{re.escape(name)}`\s*\|\s*{default}\s*\|", changelog), (
            f"version table in docs/schema-changelog.md is wrong for {name!r}"
        )


def test_changelog_has_an_unreleased_section():
    assert "### Unreleased" in (DOCS / "schema-changelog.md").read_text(encoding="utf-8")


def test_tests_cited_by_the_threat_model_exist():
    path = DOCS / "threat-model.md"
    if not path.exists():
        pytest.skip("threat model not written yet")
    cited = re.findall(r"`(tests/[\w./]+\.py)::(\w+)`", path.read_text(encoding="utf-8"))
    assert cited, "the threat model should cite the tests that cover each control"
    missing = [
        f"{file}::{name}"
        for file, name in cited
        if not (ROOT / file).exists() or f"def {name}(" not in (ROOT / file).read_text("utf-8")
    ]
    assert not missing, f"threat model cites tests that do not exist: {missing}"
