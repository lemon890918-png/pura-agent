"""AIAgentLoop — the core ReAct loop with typed Plan validation.

Design:
  - while (turn < max_turns):
      build CanonicalRequest
      provider.stream() -> consume events
      assemble CanonicalMessage(assistant, content, tool_calls)
      validate tool_calls.arguments via Tool.parameters_model
        - if invalid: inject error feedback, retry (max 3x)
        - if valid: execute tool, append tool result
      continue
      if no tool_calls and has text: return
  - circuit breaker: 3 consecutive all-invalid turns → stop

Error handling matrix (Phase 1):
  - prompt_too_long:     truncate head (Phase 3 implements properly)
  - max_output:          simple retry with bigger max_tokens
  - invalid_tool_args:   inject error feedback to LLM (max 3x)
  - tool_error:          feed error back to LLM
  - provider_error:      3x retry
  - circuit_breaker:     3x consecutive invalid tool args
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from pydantic import ValidationError
from pure_agent.model import (
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
    estimate_message_tokens,
)
from pure_agent.tools.base import Tool, ToolRegistry, ToolResult
from pure_agent.tools.filesystem import Sandbox
from pure_agent.hooks import HookContext, HookResult, HookRegistry, register_builtins


# typed feedback constants
_MAX_JSON_RETRY = 3
_CIRCUIT_BREAKER = 3
_MAX_INVALID_TURNS = 3


def _estimate(text: str) -> int:
    """Rough token estimate. Used for compact threshold checks."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def _assemble_assistant_message(
    text_parts: list[str],
    tool_calls: list[dict[str, Any]],
) -> CanonicalMessage:
    """Build a single CanonicalMessage from accumulated stream parts."""
    content: list = []
    full_text = "".join(text_parts)
    if full_text:
        content.append(TextBlock(text=full_text))
    for tc in tool_calls:
        content.append(
            ToolUseBlock(
                tool_call_id=tc["id"],
                name=tc["name"],
                arguments=tc.get("arguments", {}),
            )
        )
    return CanonicalMessage(role=Role.ASSISTANT, content=content)


def _assemble_request(
    system_prompt: str,
    messages: list[CanonicalMessage],
    tools: list[ToolSchema],
    model: str,
    max_output_tokens: int,
) -> CanonicalRequest:
    msgs: list[CanonicalMessage] = []
    if system_prompt:
        msgs.append(CanonicalMessage.from_text(Role.SYSTEM, system_prompt))
    msgs.extend(messages)
    return CanonicalRequest(
        model=model,
        messages=msgs,
        tools=tools,
        max_output_tokens=max_output_tokens,
    )


class AIAgentLoop:
    """Stateless-per-call agent loop.

    Call `run(user_message)` to get a final AgentRunResult.
    Tool execution is inline (no async queue) for Phase 1 simplicity.
    """

    def __init__(
        self,
        *,
        provider: Any,  # ProviderAdapter
        tools: ToolRegistry,
        model: str,
        max_turns: int = 30,
        max_output_tokens: int = 16384,
        system_prompt: str = "",
        inject_debug_sop: bool = True,
        on_event: Callable[[str, dict], None] | None = None,
        on_tool_call: Callable[[str, dict, ToolResult], None] | None = None,
        # Phase 4 additions:
        compactor: Any | None = None,  # Compactor
        compact_threshold_tokens: int = 80_000,  # trigger if prompt > this
        steer_queue: Any | None = None,  # SteerQueue
        checkpointer: Any | None = None,  # Checkpointer
        budget: Any | None = None,  # StepBudget (from TokenBudget)
        tool_timeout_s: float = 120.0,  # per-tool timeout (watchdog)
        # Phase 12 additions:
        hooks: HookRegistry | None = None,
        project_root: Path | None = None,
    ) -> None:
        self.provider = provider
        self.tools = tools
        self.model = model
        self.max_turns = max_turns
        self.max_output_tokens = max_output_tokens
        # Codex-style debug SOP injection (default ON; user can disable)
        if inject_debug_sop and "codex-debug-sop" not in system_prompt.lower():
            try:
                from pure_agent.skills.codex_debug_sop import PROMPT as CODEX_SOP
                system_prompt = (
                    (system_prompt + "\n\n" + CODEX_SOP) if system_prompt else CODEX_SOP
                )
            except ImportError:
                pass
        self.system_prompt = system_prompt
        self._on_event = on_event or (lambda t, p: None)
        self._on_tool_call = on_tool_call or (lambda *a, **k: None)
        # Phase 4
        self.compactor = compactor
        self.compact_threshold_tokens = compact_threshold_tokens
        self.steer_queue = steer_queue
        self.checkpointer = checkpointer
        self.budget = budget
        self.tool_timeout_s = tool_timeout_s
        # track compaction count
        self.compacted_count = 0
        # Phase 12: hook system
        if hooks is None:
            hooks = HookRegistry()
            register_builtins(hooks)
        self.hooks = hooks
        self.project_root = project_root

    def _emit(self, event_type: str, **payload: Any) -> None:
        try:
            self._on_event(event_type, payload)
        except Exception:
            pass

    def _suggest_tool(self, name: str) -> str | None:
        """Best guess for a misnamed tool. Returns None if no good match."""
        names = list(self.tools._tools.keys())
        if not names:
            return None
        # Exact prefix / suffix match
        for n in names:
            if name in n or n in name:
                return n
        # Substring overlap
        name_lower = name.lower()
        best = None
        best_score = 0
        for n in names:
            n_lower = n.lower()
            score = sum(1 for a, b in zip(name_lower, n_lower) if a == b)
            if score > best_score:
                best_score = score
                best = n
        return best if best_score >= 2 else None

    async def _stream_one(
        self,
        messages: list[CanonicalMessage],
    ) -> tuple[CanonicalMessage, Usage, str | None, str | None]:
        """Run one model turn. Returns (assistant_message, usage, finish_reason, error)."""
        req = _assemble_request(
            self.system_prompt,
            messages,
            self.tools.schemas(),
            self.model,
            self.max_output_tokens,
        )

        text_parts: list[str] = []
        tool_calls_map: dict[int, dict[str, Any]] = {}
        usage = Usage()
        finish_reason: str | None = None
        error: str | None = None

        try:
            async for ev in self.provider.stream(req):
                if ev.type == "text_delta" and ev.text:
                    text_parts.append(ev.text)
                    self._emit("text_delta", text=ev.text)
                elif ev.type == "tool_call_delta":
                    # final assembled tool call; use index 0 for single-call cases
                    idx = 0
                    if ev.tool_call_id:
                        # find or assign slot
                        slot = None
                        for k, v in tool_calls_map.items():
                            if v.get("id") == ev.tool_call_id:
                                slot = v
                                idx = k
                                break
                        if slot is None:
                            slot = {"id": "", "name": "", "arguments": ""}
                            idx = len(tool_calls_map)
                            tool_calls_map[idx] = slot
                        slot["id"] = ev.tool_call_id
                        if ev.tool_name:
                            slot["name"] = ev.tool_name
                        if ev.tool_arguments_delta is not None:
                            # full args as JSON
                            try:
                                slot["arguments"] = json.loads(ev.tool_arguments_delta)
                            except json.JSONDecodeError:
                                slot["arguments"] = {"_raw": ev.tool_arguments_delta}
                elif ev.type == "usage" and ev.usage:
                    usage = ev.usage
                elif ev.type == "message_end":
                    finish_reason = ev.finish_reason
                elif ev.type == "error":
                    error = ev.error
                    break
        except Exception as e:
            error = f"stream exception: {e}"

        assistant = _assemble_assistant_message(
            text_parts,
            [tool_calls_map[k] for k in sorted(tool_calls_map.keys())],
        )
        return assistant, usage, finish_reason, error

    async def _execute_tool_call(self, call: ToolUseBlock) -> ToolResult:
        tool = self.tools.get(call.name)
        if tool is None:
            return ToolResult.fail(f"unknown tool: {call.name}", code="unknown_tool")
        # typed Plan validation
        args, err = tool.validate_args(call.arguments)
        if err is not None:
            return ToolResult.fail(err, code="invalid_arguments")

        # Phase 12: PreToolUse hooks — can deny or modify the call
        pre_ctx = HookContext(
            event="PreToolUse",
            tool_name=call.name,
            tool_args=args,
            project_root=self.project_root,
        )
        try:
            pre = self.hooks.run("PreToolUse", pre_ctx)
            if pre.action == "deny":
                self._emit("hook_denied", tool=call.name, reason=pre.deny_reason)
                return ToolResult.fail(
                    f"tool denied by hook: {pre.deny_reason}",
                    code="denied_by_hook",
                )
            if pre.action == "modify" and pre.modified_args is not None:
                args, err = tool.validate_args(pre.modified_args)
                if err is not None:
                    return ToolResult.fail(
                        f"hook modified args invalid: {err}",
                        code="hook_invalid_args",
                    )
                call.arguments = pre.modified_args
        except Exception as e:  # noqa: BLE001
            # Hook errors are non-fatal — proceed with original args
            self._emit("hook_error", event="PreToolUse", error=str(e))

        # Phase 4: watchdog per-tool timeout
        try:
            from pure_agent.agent.watchdog import run_with_timeout

            result = await run_with_timeout(
                tool.execute(**args),
                timeout_s=self.tool_timeout_s,
                scope=f"tool:{call.name}",
            )
        except Exception as e:  # noqa: BLE001
            from pure_agent.agent.watchdog import WatchdogTimeout

            if isinstance(e, WatchdogTimeout):
                self._emit("tool_timeout", tool=call.name, timeout_s=self.tool_timeout_s)
                result = ToolResult.fail(str(e), code="tool_timeout")
            else:
                result = ToolResult.fail(f"tool execution error: {e}", code="tool_error")

        # Phase 12: PostToolUse / PostToolUseFailure hooks
        post_event = "PostToolUse" if result.ok else "PostToolUseFailure"
        post_ctx = HookContext(
            event=post_event,
            tool_name=call.name,
            tool_args=args,
            tool_result=result,
            project_root=self.project_root,
            extra=pre_ctx.extra,
        )
        try:
            post = self.hooks.run(post_event, post_ctx)
            if post.message:
                self._emit("hook_feedback", tool=call.name, message=post.message)
        except Exception as e:  # noqa: BLE001
            self._emit("hook_error", event=post_event, error=str(e))

        return result

    async def run(
        self,
        user_message: str,
        *,
        max_turns: int | None = None,
        abort_signal: asyncio.Event | None = None,
    ) -> AgentRunResult:
        messages: list[CanonicalMessage] = [
            CanonicalMessage.from_text(Role.USER, user_message)
        ]
        cap = max_turns if max_turns is not None else self.max_turns
        total_usage = Usage()
        consecutive_invalid_turns = 0
        last_assistant_text = ""

        for turn in range(1, cap + 1):
            if abort_signal is not None and abort_signal.is_set():
                return AgentRunResult(
                    final_text=last_assistant_text,
                    turns=turn - 1,
                    total_usage=total_usage,
                    stopped_reason=StopReason.ABORTED,
                    messages=messages,
                )

            # Reset write-call tracker each turn — only counts the *current* turn's calls
            # (handled below in tool-call tracking)

            # Phase 4: drain steer queue and inject into messages
            if self.steer_queue is not None:
                injected = self.steer_queue.drain()
                for m in injected:
                    messages.append(m)
                    self._emit("steered", text=m.text()[:120])

            # Phase 4: auto-compact if context too large
            if self.compactor is not None:
                # Check both before stream (existing context) and after appending
                # assistant (which may have produced a large response).
                est = sum(_estimate(m.text()) for m in messages)
                if est > self.compact_threshold_tokens and self.compactor.call_count < self.compactor.max_compactions:
                    try:
                        result = await self.compactor.compact(messages, keep_last=4)
                        messages = result.new_messages
                        self.compacted_count += 1
                        self._emit(
                            "compacted",
                            original_tokens=result.original_tokens,
                            compacted_tokens=result.compacted_tokens,
                            count=self.compacted_count,
                        )
                        total_usage.prompt_tokens += result.usage.prompt_tokens
                        total_usage.completion_tokens += result.usage.completion_tokens
                        total_usage.total_tokens += result.usage.total_tokens
                    except RuntimeError as e:
                        # hit max compactions — log and continue
                        self._emit("compact_skipped", reason=str(e))

            self._emit("turn_start", turn=turn)
            assistant, usage, finish_reason, error = await self._stream_one(messages)

            # Phase 4: budget accounting
            if self.budget is not None:
                try:
                    self.budget.add(usage, model=self.model)
                except Exception as e:
                    self._emit("budget_exceeded", error=str(e))
                    return AgentRunResult(
                        final_text=last_assistant_text,
                        turns=turn,
                        total_usage=total_usage,
                        stopped_reason=StopReason.ERROR,
                        messages=messages,
                        error=f"budget exceeded: {e}",
                    )

            total_usage.prompt_tokens += usage.prompt_tokens
            total_usage.completion_tokens += usage.completion_tokens
            total_usage.total_tokens += usage.total_tokens
            self._emit("assistant_message", turn=turn, message=assistant)

            if error:
                return AgentRunResult(
                    final_text=last_assistant_text,
                    turns=turn,
                    total_usage=total_usage,
                    stopped_reason=StopReason.ERROR,
                    messages=messages,
                    error=error,
                )

            messages.append(assistant)
            last_assistant_text = assistant.text()
            tool_uses = assistant.tool_uses()

            # Phase 4: post-turn compact (assistant response may have been large)
            if self.compactor is not None:
                est = sum(_estimate(m.text()) for m in messages)
                if est > self.compact_threshold_tokens and self.compactor.call_count < self.compactor.max_compactions:
                    try:
                        result = await self.compactor.compact(messages, keep_last=4)
                        messages = result.new_messages
                        self.compacted_count += 1
                        self._emit(
                            "compacted_post",
                            original_tokens=result.original_tokens,
                            compacted_tokens=result.compacted_tokens,
                            count=self.compacted_count,
                        )
                        total_usage.prompt_tokens += result.usage.prompt_tokens
                        total_usage.completion_tokens += result.usage.completion_tokens
                        total_usage.total_tokens += result.usage.total_tokens
                    except RuntimeError:
                        pass

            # Phase 4: save checkpoint after every turn
            if self.checkpointer is not None:
                try:
                    cid = self.checkpointer.save(
                        messages,
                        turn_id=f"turn_{turn}",
                        metadata={"turn": turn, "compacted": self.compacted_count},
                    )
                    self._emit("checkpoint_saved", id=cid, turn=turn)
                except Exception as e:  # noqa: BLE001
                    self._emit("checkpoint_failed", error=str(e))

            if not tool_uses:
                # LLM returned text without tool calls
                # Anti-hallucination guard: detect prose-only output that
                # describes tool calls in code blocks / function-call syntax
                # (e.g. ```typescript\nfunctions.write_file({...})```)
                # instead of actually emitting the tool call.
                #
                # Step A — try to EXTRACT a tool call from the prose and
                # execute it. This rescues the common pattern where the
                # model writes `functions.write_file({...})` in a code
                # block instead of using the protocol's tool_calls channel.
                from pure_agent.agent._tool_extractor import extract_tool_calls
                extracted = extract_tool_calls(last_assistant_text)
                if extracted:
                    first_name, first_args = extracted[0]
                    tu_block = ToolUseBlock(
                        tool_call_id="extracted_" + str(turn),
                        name=first_name,
                        arguments=first_args,
                    )
                    synth_assistant = CanonicalMessage(
                        role=Role.ASSISTANT,
                        content=None,
                        tool_calls=[tu_block],
                    )
                    messages.append(synth_assistant)
                    self._emit("tool_call_start", call=tu_block)
                    result = await self._execute_tool_call(tu_block)
                    self._on_tool_call(tu_block.name, tu_block.arguments, result)
                    if tu_block.name in ("write_file", "edit_file", "create_file"):
                        self._write_called = True
                    self._emit("tool_call_end", name=tu_block.name, result=result)
                    messages.append(CanonicalMessage(
                        role=Role.TOOL,
                        content=[ToolResultBlock(
                            tool_call_id=tu_block.id,
                            content=str(result.data) if result.is_success else str(result.error),
                            is_error=not result.is_success,
                        )],
                    ))
                    self._emit("extracted_tool_call", name=tu_block.name, turn=turn)
                    messages.append(CanonicalMessage.from_text(
                        Role.USER,
                        f"\n[System] The {tu_block.name} tool was executed successfully "
                        f"(extracted from your prose since you described it as text). "
                        f"Result: {result.data if result.is_success else result.error}. "
                        f"You can continue, or reply NO_ACTION_NEEDED if done."
                    ))
                    continue
                _js_call_patterns = ("functions.write_file", "functions.edit_file",
                                     "functions.read_file", "tools.write_file",
                                     "tools.edit_file", "```typescript",
                                     "```python\nwrite_file", "```python\nedit_file",
                                     "writing the file", "save the file", "save the optimized",
                                     "save the corrected", "Here is the updated",
                                     "Here is the optimized", "Here are the tool calls",
                                     "These calls will create", "final tool calls",
                                     "the corrected file", "the fixed file",
                                     "I have updated", "I have written", "the new app.py")
                _write_verbs = _js_call_patterns + (
                    "wrote", "saved", "created the file", "modified", "updated the file",
                    "refactor", "fix the bug", "fixed the bug", "saved the file",
                    "implement the changes", "implementation", "the file has been",
                    "write_file", "edit_file", "calling", "call write_file",
                    "use write_file", "use edit_file", "should call")
                text_lower = last_assistant_text.lower()
                wrote_claim = any(v in text_lower for v in _write_verbs)
                wrote_called = getattr(self, "_write_called", False)
                any_file_action = any(
                    getattr(m, 'tool_calls', None) for m in messages
                    if hasattr(m, 'tool_calls') and m.tool_calls
                )
                # After 2 turns of "describe tool call but don't emit", we know
                # the model is stuck — break the loop with a hard warning.
                stuck_turns = getattr(self, "_stuck_turns", 0) + 1 if (wrote_claim and not wrote_called) else 0
                self._stuck_turns = stuck_turns
                if turn < cap:
                    if wrote_claim and not wrote_called:
                        reminder = (
                            "\n\n[System] STOP. You described tool calls in code blocks "
                            "(e.g. ```typescript\nfunctions.write_file({...})```) but the "
                            "write_file/edit_file tool was NOT actually invoked. The OpenAI "
                            "tool-calling protocol is NOT text — you must use the tool_calls "
                            "channel. Stop pasting JSON in code blocks. Instead, when you have "
                            "file content to write, emit a tool call with name='write_file' "
                            "and arguments={'path': '...', 'content': '...'}. If you are truly "
                            "stuck, reply with exactly NO_ACTION_NEEDED and stop."
                        )
                    elif turn >= 2 and not any_file_action:
                        reminder = (
                            "\n\n[System] You keep responding with prose but no tool calls. "
                            "If the user asked you to create, modify, or fix a file, you MUST "
                            "call read_file → write_file (or edit_file). Pass the COMPLETE file "
                            "content as the 'content' parameter, starting at column 0. If no "
                            "action is needed, reply with exactly NO_ACTION_NEEDED."
                        )
                    else:
                        reminder = None
                    if reminder:
                        messages.append(CanonicalMessage.from_text(Role.USER, reminder))
                        self._emit("hallucination_guard", turn=turn)
                        continue
                self._emit("turn_end", turn=turn, stopped_reason="completed")
                return AgentRunResult(
                    final_text=last_assistant_text,
                    turns=turn,
                    total_usage=total_usage,
                    stopped_reason=StopReason.COMPLETED,
                    messages=messages,
                )

            # execute each tool call, append results
            all_invalid = True
            for tu in tool_uses:
                self._emit("tool_call_start", call=tu)
                result = await self._execute_tool_call(tu)
                self._on_tool_call(tu.name, tu.arguments, result)
                # Track write-style calls for hallucination guard
                if tu.name in ("write_file", "edit_file", "create_file"):
                    self._write_called = True
                self._emit(
                    "tool_call_end",
                    call=tu,
                    result_ok=result.ok,
                    result=result,
                )
                # append tool result message
                content = result.to_content()
                messages.append(
                    CanonicalMessage(
                        role=Role.TOOL,
                        content=[
                            ToolResultBlock(
                                tool_call_id=tu.tool_call_id,
                                content=content,
                                is_error=not result.ok,
                            )
                        ],
                        tool_call_id=tu.tool_call_id,
                    )
                )
                if result.ok:
                    all_invalid = False
                elif result.error_code == "invalid_arguments":
                    # self-correction feedback: inject explanation for LLM
                    messages.append(
                        CanonicalMessage.from_text(
                            Role.USER,
                            f"Tool '{tu.name}' rejected your arguments with: {result.error}. "
                            f"Please retry with corrected arguments that match the JSON schema. "
                            f"Available tools: {sorted(self.tools._tools.keys())}",
                            synthetic=True,
                        )
                    )
                elif result.error_code == "unknown_tool":
                    # Phase 12: smart retry — name was wrong, suggest closest match
                    suggestion = self._suggest_tool(tu.name)
                    msg = f"Tool '{tu.name}' is not registered."
                    if suggestion:
                        msg += f" Did you mean '{suggestion}'?"
                    msg += f" Available: {sorted(self.tools._tools.keys())}"
                    messages.append(
                        CanonicalMessage.from_text(
                            Role.USER,
                            msg,
                            synthetic=True,
                        )
                    )
                elif result.error_code in ("hunk_apply", "patch_parse", "old_string_not_found", "ambiguous_match"):
                    # Apply-patch / edit-file specific: tell the model the
                    # exact issue so it can adjust the hunk and retry.
                    messages.append(
                        CanonicalMessage.from_text(
                            Role.USER,
                            f"Tool '{tu.name}' failed: {result.error}. "
                            f"Read the file again with read_file, then either fix the "
                            f"hunk context (add more surrounding lines) or switch to "
                            f"write_file to send the complete file content.",
                            synthetic=True,
                        )
                    )
                elif result.error_code == "denied_by_hook":
                    messages.append(
                        CanonicalMessage.from_text(
                            Role.USER,
                            f"Tool '{tu.name}' was denied by a hook: {result.error}. "
                            f"Adjust your call and retry, or take a different approach.",
                            synthetic=True,
                        )
                    )

            if all_invalid:
                consecutive_invalid_turns += 1
                if consecutive_invalid_turns >= _CIRCUIT_BREAKER:
                    return AgentRunResult(
                        final_text=last_assistant_text,
                        turns=turn,
                        total_usage=total_usage,
                        stopped_reason=StopReason.CIRCUIT_BREAKER,
                        messages=messages,
                        error="too many consecutive invalid tool calls",
                    )
            else:
                consecutive_invalid_turns = 0

        # hit max_turns
        return AgentRunResult(
            final_text=last_assistant_text,
            turns=cap,
            total_usage=total_usage,
            stopped_reason=StopReason.MAX_TURNS,
            messages=messages,
        )


__all__ = ["AIAgentLoop"]
