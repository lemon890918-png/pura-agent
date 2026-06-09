"""Model layer: canonical messages, provider adapters, token counting, router."""

from pure_agent.model.canonical import (
    AgentRunResult,
    CanonicalMessage,
    CanonicalRequest,
    ContentBlock,
    ModelEvent,
    Role,
    StopReason,
    TextBlock,
    ToolResultBlock,
    ToolSchema,
    ToolUseBlock,
    Usage,
    new_id,
    safe_json_dumps,
    to_jsonable,
)
from pure_agent.model.minimax_adapter import MinimaxAdapter
from pure_agent.model.openai_adapter import OpenAIAdapter
from pure_agent.model.provider import ProviderAdapter
from pure_agent.model.router import (
    DEFAULT_TIER_CONFIG,
    TIER_DEFAULTS,
    ModelRouter,
    TierConfig,
)
from pure_agent.model.token_counter import (
    estimate_message_tokens,
    estimate_request_tokens,
    estimate_tokens,
)

__all__ = [
    "AgentRunResult",
    "CanonicalMessage",
    "CanonicalRequest",
    "ContentBlock",
    "DEFAULT_TIER_CONFIG",
    "MinimaxAdapter",
    "ModelEvent",
    "ModelRouter",
    "OpenAIAdapter",
    "ProviderAdapter",
    "Role",
    "StopReason",
    "TIER_DEFAULTS",
    "TextBlock",
    "TierConfig",
    "ToolResultBlock",
    "ToolSchema",
    "ToolUseBlock",
    "Usage",
    "estimate_message_tokens",
    "estimate_request_tokens",
    "estimate_tokens",
    "new_id",
    "safe_json_dumps",
    "to_jsonable",
]
