import importlib

import jarvis

CORE_PACKAGES = [
    "orchestrator",
    "policy",
    "council",
    "memory",
    "gates",
    "tools",
    "security",
    "evals",
]


def test_version_is_set():
    assert jarvis.__version__


def test_planned_packages_import():
    for name in CORE_PACKAGES:
        assert importlib.import_module(f"jarvis.core.{name}")
    assert importlib.import_module("jarvis.adapters")
