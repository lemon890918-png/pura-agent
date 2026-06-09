"""Token counter — best-effort estimate using tiktoken (cl100k_base).

If tiktoken is not available, fall back to a heuristic (~4 chars / token).
"""

from __future__ import annotations

from pure_agent.model.canonical import CanonicalMessage, ToolSchema


def _try_tiktoken():
    try:
        import tiktoken  # type: ignore

        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        return None


_ENCODER = _try_tiktoken()


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    if _ENCODER is not None:
        return len(_ENCODER.encode(text))
    # heuristic: ~4 chars per token for English, ~1.5 for CJK
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    other = len(text) - cjk
    return max(1, cjk // 2 + other // 4)


def estimate_message_tokens(msg: CanonicalMessage) -> int:
    """Estimate tokens for a single message (text content)."""
    n = 0
    for block in msg.content:
        if hasattr(block, "text"):
            n += estimate_tokens(block.text) + 4  # +4 for role/format overhead
        else:
            # tool calls/result: rough estimate
            try:
                n += estimate_tokens(str(block.model_dump_json())) + 8
            except Exception:
                pass
    return n


def estimate_request_tokens(
    messages: list[CanonicalMessage],
    tools: list[ToolSchema] | None = None,
) -> int:
    """Estimate total tokens for a request, including tool schemas."""
    n = 16  # base overhead
    for m in messages:
        n += estimate_message_tokens(m)
    if tools:
        for t in tools:
            try:
                n += estimate_tokens(str(t.parameters)) + 20
            except Exception:
                n += 100
    return n


__all__ = [
    "estimate_tokens",
    "estimate_message_tokens",
    "estimate_request_tokens",
]
