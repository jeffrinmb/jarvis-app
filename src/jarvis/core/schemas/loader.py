"""Load and validate framework files from disk."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel

from .tool_registry import ToolRegistry
from .wiki import WikiFrontmatter


def load_yaml_model[T: BaseModel](path: str | Path, model: type[T]) -> T:
    """Parse a YAML file and validate it against ``model``."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return model.model_validate(data)


def load_tool_registry(path: str | Path) -> ToolRegistry:
    """Tool registries may be a bare YAML list of entries or a full document."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, list):
        data = {"tools": data}
    return ToolRegistry.model_validate(data)


def split_frontmatter(text: str) -> tuple[dict, str]:
    """Split a Markdown note into (frontmatter dict, body). Raises ValueError if absent."""
    normalized = text.replace("\r\n", "\n")
    if not normalized.startswith("---\n"):
        raise ValueError("note must start with a '---' frontmatter block")
    end = normalized.find("\n---\n", 4)
    if end == -1:
        if normalized.endswith("\n---"):
            end = len(normalized) - 4
        else:
            raise ValueError("frontmatter block is not closed with '---'")
    raw = normalized[4:end]
    body = normalized[end + 5 :]
    data = yaml.safe_load(raw) or {}
    if not isinstance(data, dict):
        raise ValueError("frontmatter must be a mapping")
    return data, body


def parse_note(text: str) -> tuple[WikiFrontmatter, str]:
    """Validate a vault note's frontmatter and return it with the body."""
    data, body = split_frontmatter(text)
    return WikiFrontmatter.model_validate(data), body
