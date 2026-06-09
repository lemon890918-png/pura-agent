"""Tool base class + ToolRegistry.

A Tool is a typed JSON-Schema-described function that the agent can call.
Each Tool has a pydantic model for parameter validation (the "typed Plan"
check that distinguishes pure-agent from PilotDeck's free-form tool calls).
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from pydantic import BaseModel, ValidationError

from pure_agent.model.canonical import (
    ModelEvent,
    ToolSchema,
    safe_json_dumps,
)


# ─── result wrapper ───────────────────────────────────────────────────────────


class ToolResult(BaseModel):
    """Standardized tool output. Either ok with data, or error."""

    ok: bool
    data: Any = None
    error: str | None = None
    error_code: str | None = None  # machine-readable
    is_error: bool = False  # mirrors ToolResultBlock.is_error

    def to_content(self) -> str:
        """Render to a string for the LLM."""
        if self.ok:
            if isinstance(self.data, str):
                return self.data
            return safe_json_dumps(self.data)
        # error
        return f"ERROR [{self.error_code or 'unknown'}]: {self.error or 'unknown error'}"

    @staticmethod
    def ok_data(data: Any) -> "ToolResult":
        return ToolResult(ok=True, data=data, is_error=False)

    @staticmethod
    def fail(error: str, code: str | None = None) -> "ToolResult":
        return ToolResult(ok=False, error=error, error_code=code, is_error=True)


# ─── tool base ────────────────────────────────────────────────────────────────


class Tool(ABC):
    """Base class for all tools.

    Subclasses define:
      - name, description, parameters (JSON Schema)
      - parameters_model: pydantic model for strong validation
      - execute(**kwargs) -> ToolResult
    """

    name: ClassVar[str]
    description: ClassVar[str]
    parameters: ClassVar[dict]  # JSON Schema
    parameters_model: ClassVar[type[BaseModel]] | None = None

    @abstractmethod
    async def execute(self, **kwargs: Any) -> ToolResult: ...

    def to_schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            parameters_model=self.parameters_model,
        )

    def validate_args(self, raw: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
        """Validate raw tool args against parameters_model.

        Returns (validated_args, error_message). If error_message is not None,
        the tool should NOT be executed; instead the error must be fed back to LLM.
        """
        if self.parameters_model is None:
            return raw, None
        try:
            m = self.parameters_model.model_validate(raw)
            return m.model_dump(exclude_none=True), None
        except ValidationError as e:
            return raw, f"Invalid arguments for tool '{self.name}': {e}"


# ─── registry ─────────────────────────────────────────────────────────────────


class ToolRegistry:
    """Maps tool name -> Tool instance."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def all(self) -> list[Tool]:
        """Return a list of all registered tools."""
        return list(self._tools.values())
        return name in self._tools

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def schemas(self) -> list[ToolSchema]:
        return [t.to_schema() for t in self._tools.values()]

    def __len__(self) -> int:
        return len(self._tools)


__all__ = ["Tool", "ToolResult", "ToolRegistry"]
