"""Canonical message model (L1 protocol).

Provider-agnostic representation of LLM conversation. Inspired by PilotDeck's
CanonicalMessage abstraction. The agent loop speaks CanonicalRequest/Event
internally; providers convert to/from their wire format at the boundary.
"""

from __future__ import annotations

import json
import time
import uuid
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


# ─── content blocks ───────────────────────────────────────────────────────────


class TextBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ToolUseBlock(BaseModel):
    type: Literal["tool_use"] = "tool_use"
    tool_call_id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResultBlock(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    tool_call_id: str
    content: str
    is_error: bool = False


ContentBlock = TextBlock | ToolUseBlock | ToolResultBlock


# ─── message ──────────────────────────────────────────────────────────────────


class CanonicalMessage(BaseModel):
    """A single message in the conversation, normalized across providers."""

    role: Role
    content: list[ContentBlock] = Field(default_factory=list)
    # for role=TOOL: which tool_call this is replying to
    tool_call_id: str | None = None
    name: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def text(self) -> str:
        """Concatenate all text content from TextBlocks and ToolResultBlocks."""
        parts: list[str] = []
        for b in self.content:
            if isinstance(b, TextBlock):
                parts.append(b.text)
            elif isinstance(b, ToolResultBlock):
                parts.append(b.content)
        return "".join(parts)

    def tool_uses(self) -> list[ToolUseBlock]:
        return [b for b in self.content if isinstance(b, ToolUseBlock)]

    @staticmethod
    def from_text(role: Role, text: str, **metadata: Any) -> "CanonicalMessage":
        return CanonicalMessage(
            role=role,
            content=[TextBlock(text=text)],
            metadata=metadata,
        )

    @staticmethod
    def from_tool_result(
        tool_call_id: str,
        content: str,
        is_error: bool = False,
    ) -> "CanonicalMessage":
        return CanonicalMessage(
            role=Role.TOOL,
            content=[ToolResultBlock(tool_call_id=tool_call_id, content=content, is_error=is_error)],
            tool_call_id=tool_call_id,
        )


# ─── request ──────────────────────────────────────────────────────────────────


class ToolSchema(BaseModel):
    """JSON Schema for a tool. ProviderAdapter.normalize_tool_schema converts."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema dict
    # Optional pydantic model for strong validation (Phase 1 typed Plan check)
    parameters_model: type[BaseModel] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


class CanonicalRequest(BaseModel):
    """What we send to a provider. Provider-specific fields live outside this."""

    model_config = {"arbitrary_types_allowed": True}

    model: str
    messages: list[CanonicalMessage]
    tools: list[ToolSchema] = Field(default_factory=list)
    max_output_tokens: int = 16384
    temperature: float = 1.0
    # Reserved for Phase 3
    stop: list[str] | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


# ─── streaming events ──────────────────────────────────────────────────────────


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ModelEvent(BaseModel):
    """One event from the provider stream."""

    type: Literal[
        "message_start",  # stream begins
        "text_delta",  # incremental text
        "tool_call_delta",  # incremental tool call (we may receive one full per delta)
        "message_end",  # stream ends
        "error",  # provider error
        "usage",  # token usage accounting
    ]
    text: str | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_arguments_delta: str | None = None  # raw JSON fragment
    finish_reason: str | None = None
    usage: Usage | None = None
    error: str | None = None
    timestamp_ms: int = Field(default_factory=lambda: int(time.time() * 1000))


# ─── result ───────────────────────────────────────────────────────────────────


class StopReason(str, Enum):
    COMPLETED = "completed"  # LLM returned text, no tool calls
    MAX_TURNS = "max_turns"  # hit max_turns
    ABORTED = "aborted"  # user aborted
    ERROR = "error"  # unrecoverable error
    CIRCUIT_BREAKER = "circuit_breaker"  # too many consecutive failures


class AgentRunResult(BaseModel):
    final_text: str
    turns: int
    total_usage: Usage
    stopped_reason: StopReason
    messages: list[CanonicalMessage] = Field(default_factory=list)
    error: str | None = None

    @field_validator("turns")
    @classmethod
    def _turns_nonneg(cls, v: int) -> int:
        if v < 0:
            raise ValueError("turns must be >= 0")
        return v


# ─── helpers ──────────────────────────────────────────────────────────────────


def new_id(prefix: str = "msg") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def to_jsonable(obj: Any) -> Any:
    """Convert pydantic models to JSON-safe dicts (for tool result rendering)."""
    from pydantic import BaseModel

    if isinstance(obj, BaseModel):
        return obj.model_dump()
    if isinstance(obj, list):
        return [to_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if hasattr(obj, "model_dump") and callable(obj.model_dump):
        try:
            return obj.model_dump()
        except Exception:
            pass
    return obj


__all__ = [
    "Role",
    "TextBlock",
    "ToolUseBlock",
    "ToolResultBlock",
    "ContentBlock",
    "CanonicalMessage",
    "ToolSchema",
    "CanonicalRequest",
    "Usage",
    "ModelEvent",
    "StopReason",
    "AgentRunResult",
    "new_id",
    "to_jsonable",
]


# JSON encode for tool results that may contain non-string keys
def safe_json_dumps(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return json.dumps(to_jsonable(obj), ensure_ascii=False, default=str)
