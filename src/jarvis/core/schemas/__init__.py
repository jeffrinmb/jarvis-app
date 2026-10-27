"""Versioned schemas that are the contract between every component and harness."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from .action import Action, SessionState
from .common import (
    SCHEMA_VERSION,
    Approval,
    DataClass,
    Decision,
    Effect,
    Environment,
    RiskClass,
    Tier,
    TrustLabel,
)
from .decision_card import DecisionCard
from .event import Event, EventKind
from .loader import load_tool_registry, load_yaml_model, parse_note, split_frontmatter
from .policy import Policy
from .project import ProjectConfig, TiersConfig
from .task import Task, TaskState
from .tool_registry import ToolEntry, ToolRegistry
from .verdict import RuleMatch, Verdict
from .wiki import WikiFrontmatter

SCHEMAS: dict[str, type[BaseModel]] = {
    "action": Action,
    "decision-card": DecisionCard,
    "event": Event,
    "policy": Policy,
    "project": ProjectConfig,
    "task": Task,
    "tiers": TiersConfig,
    "tool-registry": ToolRegistry,
    "verdict": Verdict,
    "wiki-frontmatter": WikiFrontmatter,
}


def json_schema(name: str) -> dict:
    return SCHEMAS[name].model_json_schema()


def export_json_schemas(out_dir: str | Path) -> list[Path]:
    """Write one ``<name>.schema.json`` per schema. Output is deterministic."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name in sorted(SCHEMAS):
        path = out / f"{name}.schema.json"
        text = json.dumps(json_schema(name), indent=2, sort_keys=True) + "\n"
        path.write_bytes(text.encode("utf-8"))
        written.append(path)
    return written


__all__ = [
    "SCHEMAS",
    "SCHEMA_VERSION",
    "Action",
    "Approval",
    "DataClass",
    "Decision",
    "DecisionCard",
    "Effect",
    "Environment",
    "Event",
    "EventKind",
    "Policy",
    "ProjectConfig",
    "RiskClass",
    "RuleMatch",
    "SessionState",
    "Task",
    "TaskState",
    "Tier",
    "TiersConfig",
    "ToolEntry",
    "ToolRegistry",
    "TrustLabel",
    "Verdict",
    "WikiFrontmatter",
    "export_json_schemas",
    "json_schema",
    "load_tool_registry",
    "load_yaml_model",
    "parse_note",
    "split_frontmatter",
]
