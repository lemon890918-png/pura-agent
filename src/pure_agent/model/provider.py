"""Provider adapter Protocol and base utilities."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol

from pure_agent.model.canonical import (
    CanonicalRequest,
    ModelEvent,
    ToolSchema,
)


class ProviderAdapter(Protocol):
    """Abstract interface for LLM providers.

    Implementations convert CanonicalRequest to provider-specific format
    and yield ModelEvent stream. The agent loop only ever sees ModelEvents.
    """

    def stream(
        self,
        request: CanonicalRequest,
    ) -> AsyncIterator[ModelEvent]:
        """Stream events for a single request."""
        ...

    def normalize_tool_schema(self, schema: ToolSchema) -> dict[str, Any]:
        """Convert a ToolSchema into the provider's expected wire format."""
        ...

    def max_context_tokens(self, model: str) -> int | None:
        """Return the model's context window size, or None if unknown."""
        ...

    async def aclose(self) -> None:
        """Close any underlying HTTP resources. Default: no-op."""
        return None


__all__ = ["ProviderAdapter"]
