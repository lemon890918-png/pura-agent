"""Subagent: typed request/response, registry, lifecycle.

Phase 5 implementation. A subagent is a typed specialization of AIAgentLoop:
  - role: general_purpose / explore / plan / verify
  - tools: whitelist / blacklist filter
  - read_only: enforces no-write tools

SubagentRequest/SubagentResponse are pydantic models — typed wire protocol.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, ClassVar

from pydantic import BaseModel, Field

from pure_agent.model import Usage
from pure_agent.tools import ToolRegistry


class SubagentRole(str, Enum):
    GENERAL_PURPOSE = "general_purpose"
    EXPLORE = "explore"
    PLAN = "plan"
    VERIFY = "verify"


class SubagentStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class SubagentRequest(BaseModel):
    """Typed request to a subagent (the wire protocol)."""

    task_id: str
    role: SubagentRole
    prompt: str
    tools_allow: list[str] = Field(default_factory=list)
    tools_deny: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    max_turns: int = 10
    max_tokens: int | None = None
    timeout_s: float = 300.0
    read_only: bool = False
    system_prompt_override: str | None = None


class SubagentResponse(BaseModel):
    """Typed response from a subagent."""

    task_id: str
    role: SubagentRole
    status: SubagentStatus
    summary: str
    files_changed: list[str] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    turns: int = 0
    error: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    result: dict[str, Any] = Field(default_factory=dict)


# ─── registry ──────────────────────────────────────────────────────────────


@dataclass
class SubagentSpec:
    """Static config for a built-in subagent."""

    role: SubagentRole
    description: str
    system_prompt: str
    tools: list[str]  # which tools are allowed (whitelist)
    read_only: bool
    default_max_turns: int = 10
    default_timeout_s: float = 300.0


_SUBAGENT_SPECS: dict[SubagentRole, SubagentSpec] = {
    SubagentRole.GENERAL_PURPOSE: SubagentSpec(
        role=SubagentRole.GENERAL_PURPOSE,
        description="Can do anything: read, write, edit, search, verify.",
        system_prompt=(
            "You are a general-purpose subagent. You can read and write files, "
            "search the web, and run any tool. Complete the task at hand using "
            "the tools available to you."
        ),
        tools=["read_file", "write_file", "edit_file", "glob", "grep", "web_search"],
        read_only=False,
        default_max_turns=15,
    ),
    SubagentRole.EXPLORE: SubagentSpec(
        role=SubagentRole.EXPLORE,
        description="Read-only exploration. No writes.",
        system_prompt=(
            "You are an exploration subagent. You may only read files and search "
            "for information — you must NEVER write, edit, or modify any file. "
            "Your job is to gather facts and return a clear summary."
        ),
        tools=["read_file", "glob", "grep", "web_search"],
        read_only=True,
        default_max_turns=10,
    ),
    SubagentRole.PLAN: SubagentSpec(
        role=SubagentRole.PLAN,
        description="Decompose a goal into typed steps.",
        system_prompt=(
            "You are a planning subagent. Given a goal, return a structured JSON "
            "plan with steps. You may only read files (for context) — you must "
            "NEVER write or edit. Return the plan as ```json ... ``` at the end."
        ),
        tools=["read_file", "glob", "grep"],
        read_only=True,
        default_max_turns=5,
    ),
    SubagentRole.VERIFY: SubagentSpec(
        role=SubagentRole.VERIFY,
        description="Verify a step's outcome against its spec.",
        system_prompt=(
            "You are a verification subagent. You are given a step's intended "
            "action and its reported outcome. Read the actual file state and "
            "judge whether the outcome matches. Return verdict: pass | fail | "
            "needs_fix | skipped, plus a summary."
        ),
        tools=["read_file", "glob", "grep"],
        read_only=True,
        default_max_turns=8,
    ),
}


def get_spec(role: SubagentRole) -> SubagentSpec:
    return _SUBAGENT_SPECS[role]


def list_roles() -> list[SubagentRole]:
    return list(_SUBAGENT_SPECS.keys())


# ─── registry: build a SubagentRequest from a role + customization ───────


def build_request(
    role: SubagentRole,
    prompt: str,
    *,
    task_id: str | None = None,
    tools_allow: list[str] | None = None,
    tools_deny: list[str] | None = None,
    context: dict[str, Any] | None = None,
    max_turns: int | None = None,
    timeout_s: float | None = None,
    system_prompt_override: str | None = None,
) -> SubagentRequest:
    """Create a SubagentRequest with the spec's defaults filled in."""
    spec = get_spec(role)
    return SubagentRequest(
        task_id=task_id or f"sub_{role.value}_{datetime.now(timezone.utc).timestamp():.0f}",
        role=role,
        prompt=prompt,
        tools_allow=tools_allow or list(spec.tools),
        tools_deny=tools_deny or [],
        context=context or {},
        max_turns=max_turns if max_turns is not None else spec.default_max_turns,
        timeout_s=timeout_s if timeout_s is not None else spec.default_timeout_s,
        read_only=spec.read_only,
        system_prompt_override=system_prompt_override,
    )


# ─── run: build filtered ToolRegistry, build system prompt, run loop ────


def make_subagent_system_prompt(req: SubagentRequest) -> str:
    """Build the system prompt for a subagent run."""
    spec = get_spec(req.role)
    if req.system_prompt_override:
        return req.system_prompt_override
    parts = [spec.system_prompt]
    if req.read_only:
        parts.append(
            "\nIMPORTANT: You are in READ-ONLY mode. "
            "Do NOT call write_file or edit_file. If the task requires writing, "
            "abort and report why."
        )
    if req.context:
        import json
        parts.append("\nContext:")
        parts.append(json.dumps(req.context, ensure_ascii=False, indent=2)[:2000])
    return "\n".join(parts)


def filter_registry(registry: ToolRegistry, req: SubagentRequest) -> ToolRegistry:
    """Return a new ToolRegistry with allow/deny applied."""
    new = ToolRegistry()
    for tool in registry.all():
        if req.tools_allow and tool.name not in req.tools_allow:
            continue
        if tool.name in req.tools_deny:
            continue
        new.register(tool)
    return new


# ─── runner: execute the request using a provided AIAgentLoop factory ──


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def run_subagent(
    req: SubagentRequest,
    *,
    loop_factory: Callable[..., Any],
    tool_registry: ToolRegistry,
    abort_signal: Any | None = None,
) -> SubagentResponse:
    """Run a subagent request through AIAgentLoop.

    The loop_factory is the same factory used by PlanRunner / chat CLI.
    It must accept at least: system_prompt, max_turns, tools.
    """
    import asyncio
    import time

    sys_prompt = make_subagent_system_prompt(req)
    tools = filter_registry(tool_registry, req)

    started_at = now_iso()
    t0 = time.monotonic()
    try:
        # timeout at the asyncio level
        loop = loop_factory(
            system_prompt=sys_prompt,
            tools=tools,
            max_turns=req.max_turns,
        )
        coro = loop.run(req.prompt, abort_signal=abort_signal)
        result = await asyncio.wait_for(coro, timeout=req.timeout_s)
        status = SubagentStatus.DONE
        error = None
        files_changed: list[str] = []
        summary = result.final_text or "(no text)"
        # try to extract files_changed from result
        for m in result.messages:
            from pure_agent.model import TextBlock, ToolResultBlock
            for b in m.content:
                if isinstance(b, TextBlock) and "files_changed" in b.text:
                    # crude scan
                    pass
        usage = result.total_usage
        turns = result.turns
    except asyncio.TimeoutError:
        status = SubagentStatus.TIMEOUT
        error = f"subagent timed out after {req.timeout_s}s"
        summary = ""
        files_changed = []
        usage = Usage()
        turns = 0
    except Exception as e:  # noqa: BLE001
        status = SubagentStatus.FAILED
        error = f"subagent error: {e}"
        summary = ""
        files_changed = []
        usage = Usage()
        turns = 0
    completed_at = now_iso()
    return SubagentResponse(
        task_id=req.task_id,
        role=req.role,
        status=status,
        summary=summary,
        files_changed=files_changed,
        usage=usage,
        turns=turns,
        error=error,
        started_at=started_at,
        completed_at=completed_at,
    )


__all__ = [
    "SubagentRole",
    "SubagentStatus",
    "SubagentRequest",
    "SubagentResponse",
    "SubagentSpec",
    "build_request",
    "filter_registry",
    "get_spec",
    "list_roles",
    "make_subagent_system_prompt",
    "run_subagent",
    "now_iso",
]
