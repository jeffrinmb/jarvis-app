"""Shared enums and the strict base model for all framework schemas.

Versioning policy (task P1.4): every top-level document carries ``schema_version``.
Additive changes (new optional fields, new enum values) keep the version. Removing or
renaming a field, or changing its meaning, bumps the version and needs an ADR.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

SCHEMA_VERSION = 1


class StrictModel(BaseModel):
    """Base model: unknown fields are rejected so typos never pass silently."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TrustLabel(StrEnum):
    TRUSTED = "trusted"
    INTERNAL = "internal"
    UNTRUSTED = "untrusted"


class RiskClass(StrEnum):
    """Ordered from least to most risky; use ``rank`` to compare."""

    READ = "read"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return list(RiskClass).index(self)


class Effect(StrEnum):
    READ_ONLY = "read-only"
    WRITE_REVERSIBLE = "write-reversible"
    WRITE_IRREVERSIBLE = "write-irreversible"
    EXTERNAL_COMMS = "external-comms"


class DataClass(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class Tier(StrEnum):
    """Model tier requested by a role; ``tiers.yaml`` maps tiers to concrete models."""

    CHEAP = "cheap"
    STANDARD = "standard"
    STRONG = "strong"


class Decision(StrEnum):
    """Outcome of a policy check."""

    ALLOW = "allow"
    COUNCIL = "council"
    HUMAN = "human"
    DENY = "deny"


class Approval(StrEnum):
    AUTO = "auto"
    COUNCIL = "council"
    HUMAN = "human"


class Environment(StrEnum):
    DEV = "dev"
    STAGING = "staging"
    PROD = "prod"
