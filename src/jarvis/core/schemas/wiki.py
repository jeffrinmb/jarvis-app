"""Frontmatter for vault notes (LLM-wiki). Agents write only to the inbox."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from .common import SCHEMA_VERSION, StrictModel


class NoteType(StrEnum):
    LESSON = "lesson"
    DECISION = "decision"
    COMPONENT = "component"
    PLAYBOOK = "playbook"
    INCIDENT = "incident"
    PREFERENCE = "preference"


class NoteStatus(StrEnum):
    INBOX = "inbox"
    VALIDATED = "validated"
    DEPRECATED = "deprecated"


class NoteTrust(StrEnum):
    """Ordered from least to most trusted."""

    AGENT_INFERRED = "agent-inferred"
    TEST_VERIFIED = "test-verified"
    HUMAN_CONFIRMED = "human-confirmed"


class WikiFrontmatter(StrictModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    type: NoteType
    status: NoteStatus = NoteStatus.INBOX
    trust: NoteTrust = NoteTrust.AGENT_INFERRED
    confidence: float = Field(ge=0, le=1)
    created: date
    supersedes: list[str] = Field(default_factory=list)
    source: str | None = Field(default=None, description="Event id, PR or path it came from")
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validated_needs_trust(self) -> WikiFrontmatter:
        if self.status is NoteStatus.VALIDATED and self.trust is NoteTrust.AGENT_INFERRED:
            raise ValueError("a validated note needs trust test-verified or human-confirmed")
        return self
