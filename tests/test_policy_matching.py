import pytest

from jarvis.core.policy import glob_match, matches_any, normalize_path


@pytest.mark.parametrize(
    ("glob", "value", "expected"),
    [
        ("docs/**", "docs/a.md", True),
        ("docs/**", "docs/x/y/z.md", True),
        ("docs/**", "docs", False),
        ("docs/**", "src/docs/a.md", False),
        ("**/.env", ".env", True),
        ("**/.env", "a/b/.env", True),
        ("**/.env", "a/b/.envrc", False),
        ("*.md", "README.md", True),
        ("*.md", "docs/README.md", False),
        ("src/*/x.py", "src/a/x.py", True),
        ("src/*/x.py", "src/a/b/x.py", False),
        ("src/?.py", "src/a.py", True),
        ("src/?.py", "src/ab.py", False),
        ("git.*", "git.read", True),
        ("git.*", "github.create_pr", False),
        ("a+b/(c)", "a+b/(c)", True),  # regex characters are literal
        ("**", "anything/at/all", True),
    ],
)
def test_glob_match(glob, value, expected):
    assert glob_match(glob, value) is expected


def test_matching_is_case_insensitive():
    assert glob_match("**/.env", ".ENV")
    assert glob_match("docs/**", "DOCS/A.md")


def test_matches_any():
    assert matches_any(["a/**", "b/**"], "b/x")
    assert not matches_any(["a/**"], "b/x")
    assert not matches_any([], "b/x")


@pytest.mark.parametrize(
    ("raw", "normalised", "suspicious"),
    [
        ("docs/a.md", "docs/a.md", False),
        ("./docs/a.md", "docs/a.md", False),
        ("docs\\sub\\a.md", "docs/sub/a.md", False),
        ("docs//a.md", "docs/a.md", False),
        ("docs/", "docs", False),
        ("../secrets.txt", "../secrets.txt", True),
        ("docs/../../x", "docs/../../x", True),
        ("/etc/passwd", "/etc/passwd", True),
        ("C:\\Windows\\x", "C:/Windows/x", True),
        ("", "", True),
        ("   ", "   ", True),
        ("a\x00b", "a\x00b", True),
    ],
)
def test_normalize_path(raw, normalised, suspicious):
    assert normalize_path(raw) == (normalised, suspicious)
