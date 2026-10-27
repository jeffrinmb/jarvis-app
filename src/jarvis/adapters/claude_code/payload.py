"""The JSON a Claude Code hook receives on stdin (see docs/adr/0003).

Only fields the adapter uses are modelled, and unknown fields are ignored on purpose:
Claude Code adds fields between versions, and a new field must not make the hook fail.
Every value is treated as untrusted input and type-checked before use.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class McpServer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    source: str | None = None


class HookInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    hook_event_name: str = Field(min_length=1)
    session_id: str = Field(default="unknown", min_length=1)
    cwd: str | None = None
    transcript_path: str | None = None
    permission_mode: str | None = None
    tool_name: str | None = None
    tool_input: dict[str, Any] = Field(default_factory=dict)
    tool_use_id: str | None = None
    agent_id: str | None = None
    agent_type: str | None = None
    mcp_server: McpServer | None = None
    source: str | None = None  # SessionStart source or ConfigChange source
    file_path: str | None = None  # ConfigChange: the settings file that changed
    error: str | None = None  # PostToolUseFailure
    duration_ms: int | None = None
