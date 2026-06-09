"""OpenAI Chat Completions adapter (streaming).

Reference implementation for OpenAI-protocol providers. Subclassed by
MinimaxAdapter with just a different base_url.

Stream shape:
  - text_delta:  incremental text content
  - tool_call_complete: a single, fully-assembled tool call (id, name, args).
                        Yielded once per call after message_end.
  - message_end:  stream end
  - usage:       token usage (may come at start or end)
  - error:       unrecoverable error

The agent loop should buffer all events and finalize the assistant message
after message_end.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from typing import Any

import httpx

from pure_agent.model.canonical import (
    CanonicalMessage,
    CanonicalRequest,
    ModelEvent,
    Role,
    TextBlock,
    ToolResultBlock,
    ToolSchema,
    ToolUseBlock,
    Usage,
)
from pure_agent.model.provider import ProviderAdapter


class OpenAIAdapter:
    """OpenAI Chat Completions API with SSE streaming.

    Tested with:
      - OpenAI (api.openai.com/v1)
      - Minimax (api.minimaxi.com/v1) — same protocol
    """

    DEFAULT_MAX_CONTEXT = 128_000
    DEFAULT_TIMEOUT_S = 300.0

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4o",
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_retries: int = 3,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self._client = client
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout_s, connect=10.0),
            )
        return self._client

    # ─── protocol impl ─────────────────────────────────────────────────────

    def max_context_tokens(self, model: str | None = None) -> int:
        m = (model or self.model).lower()
        if "mini" in m or "m2" in m or "haiku" in m:
            return 200_000
        if "gpt-4o" in m or "gpt-4-turbo" in m or "claude" in m or "minimax" in m or "m3" in m:
            return 128_000
        if "gpt-3.5" in m or "gpt-4-32k" in m:
            return 32_000
        return self.DEFAULT_MAX_CONTEXT

    def normalize_tool_schema(self, schema: ToolSchema) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": schema.name,
                "description": schema.description,
                "parameters": schema.parameters,
            },
        }

    # ─── wire conversion ───────────────────────────────────────────────────

    @staticmethod
    def _content_to_openai(msg: CanonicalMessage) -> dict[str, Any]:
        if msg.role == Role.TOOL:
            assert msg.tool_call_id, "tool message must have tool_call_id"
            assert msg.content, "tool message must have content"
            tr = msg.content[0]
            assert isinstance(tr, ToolResultBlock)
            return {
                "role": "tool",
                "tool_call_id": tr.tool_call_id,
                "content": tr.content,
            }
        if msg.role == Role.ASSISTANT:
            text_parts: list[str] = []
            for b in msg.content:
                if isinstance(b, TextBlock):
                    text_parts.append(b.text)
            out: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts)}
            tool_calls_payload: list[dict[str, Any]] = []
            for b in msg.content:
                if isinstance(b, ToolUseBlock):
                    tool_calls_payload.append(
                        {
                            "id": b.tool_call_id,
                            "type": "function",
                            "function": {
                                "name": b.name,
                                "arguments": json.dumps(b.arguments, ensure_ascii=False),
                            },
                        }
                    )
            if tool_calls_payload:
                out["tool_calls"] = tool_calls_payload
            return out
        text = "".join(b.text for b in msg.content if isinstance(b, TextBlock))
        return {"role": msg.role.value, "content": text}

    def _build_request_body(self, req: CanonicalRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": req.model,
            "messages": [self._content_to_openai(m) for m in req.messages],
            "stream": True,
            "max_tokens": req.max_output_tokens,
            "temperature": req.temperature,
        }
        if req.tools:
            body["tools"] = [self.normalize_tool_schema(t) for t in req.tools]
        if req.stop:
            body["stop"] = req.stop
        if req.extra:
            body.update(req.extra)
        return body

    # ─── stream ────────────────────────────────────────────────────────────

    async def stream(
        self,
        request: CanonicalRequest,
    ) -> AsyncIterator[ModelEvent]:
        body = self._build_request_body(request)
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        yield ModelEvent(type="message_start")

        last_err: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                client = self._get_client()
                async with client.stream("POST", url, json=body, headers=headers) as resp:
                    if resp.status_code >= 400:
                        err_body = await resp.aread()
                        err_text = err_body.decode("utf-8", errors="replace")[:500]
                        if 400 <= resp.status_code < 500 and resp.status_code != 429:
                            yield ModelEvent(
                                type="error",
                                error=f"HTTP {resp.status_code}: {err_text}",
                            )
                            return
                        last_err = RuntimeError(f"HTTP {resp.status_code}: {err_text}")
                        await asyncio.sleep(2 ** attempt)
                        continue

                    tool_calls: dict[int, dict[str, Any]] = {}
                    finish_reason: str | None = None
                    usage_payload: dict[str, Any] | None = None
                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        if line.startswith(":"):
                            continue
                        if not line.startswith("data:"):
                            continue
                        payload = line[len("data:"):].strip()
                        if payload == "[DONE]":
                            break
                        try:
                            obj = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        choices = obj.get("choices") or []
                        for choice in choices:
                            delta = choice.get("delta") or {}
                            finish_reason = choice.get("finish_reason") or finish_reason
                            text_chunk = delta.get("content")
                            if text_chunk:
                                yield ModelEvent(type="text_delta", text=text_chunk)
                            for tc in delta.get("tool_calls") or []:
                                idx = tc.get("index", 0)
                                slot = tool_calls.setdefault(
                                    idx,
                                    {"id": "", "name": "", "arguments": ""},
                                )
                                if tc.get("id"):
                                    slot["id"] = tc["id"]
                                fn = tc.get("function") or {}
                                if fn.get("name"):
                                    slot["name"] = fn["name"]
                                if fn.get("arguments"):
                                    slot["arguments"] += fn["arguments"]
                        if obj.get("usage"):
                            usage_payload = obj["usage"]

                    # Finalize tool calls. We synthesize the final
                    # assistant message into a usage-bearing ModelEvent so
                    # the agent loop can pick up tool calls easily:
                    # we use the existing 'text' field to hold JSON-encoded
                    # tool calls when type == 'message_end'.
                    if tool_calls:
                        calls_payload: list[dict[str, Any]] = []
                        for _idx, slot in tool_calls.items():
                            try:
                                args = json.loads(slot["arguments"]) if slot["arguments"] else {}
                            except json.JSONDecodeError:
                                args = {"_raw": slot["arguments"]}
                            calls_payload.append(
                                {
                                    "id": slot["id"],
                                    "name": slot["name"],
                                    "arguments": args,
                                }
                            )
                        # Emit a synthetic event the agent loop can recognize
                        # We use the existing ModelEvent but type="message_end"
                        # with finish_reason set; the agent loop will look at
                        # ALL events to collect tool calls via a known
                        # convention. To keep this simple, we stash the calls
                        # in finish_reason extension via metadata field.
                        # But ModelEvent doesn't have metadata. So we use
                        # the convention: a separate event TYPE that's a
                        # valid Literal. Pydantic Literal in ModelEvent is:
                        #   message_start, text_delta, tool_call_delta,
                        #   message_end, error, usage
                        # We use tool_call_delta with full args. Agent loop
                        # will see tool_call_delta events and accumulate.
                        for call in calls_payload:
                            # Serialize args back to JSON; the agent loop
                            # parses on receipt.
                            yield ModelEvent(
                                type="tool_call_delta",
                                tool_call_id=call["id"],
                                tool_name=call["name"],
                                tool_arguments_delta=json.dumps(
                                    call["arguments"], ensure_ascii=False
                                ),
                            )

                    if usage_payload:
                        yield ModelEvent(
                            type="usage",
                            usage=Usage(
                                prompt_tokens=usage_payload.get("prompt_tokens", 0),
                                completion_tokens=usage_payload.get("completion_tokens", 0),
                                total_tokens=usage_payload.get("total_tokens", 0),
                            ),
                        )
                    yield ModelEvent(
                        type="message_end",
                        finish_reason=finish_reason,
                    )
                    return

            except (httpx.TimeoutException, httpx.NetworkError) as e:
                last_err = e
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                yield ModelEvent(type="error", error=f"network error: {e}")
                return

        if last_err:
            yield ModelEvent(type="error", error=f"after {self.max_retries} retries: {last_err}")


__all__ = ["OpenAIAdapter"]


def api_key_from_env(env_var: str = "OPENAI_API_KEY") -> str | None:
    return os.environ.get(env_var)
