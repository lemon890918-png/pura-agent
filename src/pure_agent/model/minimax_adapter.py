"""MinimaxAdapter — convenience wrapper over OpenAIAdapter.

Minimax exposes the OpenAI Chat Completions API at
https://api.minimaxi.com/v1. Default model: MiniMax-M3.
"""

from __future__ import annotations

import os

from pure_agent.model.openai_adapter import OpenAIAdapter


class MinimaxAdapter(OpenAIAdapter):
    """Minimax LLM via OpenAI protocol.

    Default model: MiniMax-M3
    API key: MINIMAX_API_KEY env var
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = "MiniMax-M3",
        base_url: str = "https://api.minimaxi.com/v1",
        timeout_s: float = 300.0,
        max_retries: int = 3,
        client=None,
    ) -> None:
        key = api_key or os.environ.get("MINIMAX_API_KEY", "")
        if not key:
            raise ValueError(
                "Minimax API key not set. Pass api_key= or set MINIMAX_API_KEY env."
            )
        super().__init__(
            api_key=key,
            base_url=base_url,
            model=model,
            timeout_s=timeout_s,
            max_retries=max_retries,
            client=client,
        )


__all__ = ["MinimaxAdapter"]
