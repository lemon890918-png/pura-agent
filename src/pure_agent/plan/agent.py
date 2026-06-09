"""PlanAgent — uses LLM to decompose a Goal into a typed Plan."""

from __future__ import annotations

import json
import re
from typing import Any

from pure_agent.model import (
    CanonicalMessage,
    CanonicalRequest,
    ModelEvent,
    Role,
    TextBlock,
    Usage,
)
from pure_agent.model.canonical import ToolSchema
from pure_agent.model.provider import ProviderAdapter
from pure_agent.plan.models import (
    Goal,
    Plan,
    PlanStep,
    StepKind,
    now_iso,
)


_PLAN_PROMPT = """You are a planning agent for pure-agent. Your job is to decompose
a user goal into a structured plan of executable steps.

Output a JSON object matching this schema:

{
  "steps": [
    {
      "kind": "read" | "code" | "search" | "verify" | "deliver" | "plan",
      "action": "<one-sentence imperative task>",
      "deps": ["<step id>", ...]   // list of step ids this depends on; first steps have []
    },
    ...
  ]
}

Rules:
- Each step must be a single, atomic action an agent can complete in 1-5 turns.
- Use kind=read for exploration (grep, read files), kind=code for writes/edits,
  kind=search for web_search, kind=verify for running tests/checks,
  kind=deliver for git commit / report / final hand-off, kind=plan for sub-plans.
- Deps reference the id of the step (auto-assigned as s1, s2, s3, ... in order).
- Aim for 3-8 steps. Fewer is better than more, but don't over-aggregate.
- Be specific in 'action' — the agent will execute it literally.

Output ONLY the JSON object, wrapped in ```json ... ``` fences.
Do NOT include explanations, comments, or markdown outside the JSON block.
"""


class PlanAgent:
    """Decomposes a Goal into a Plan using an LLM.

    Approach:
      1. Ask LLM to produce JSON matching Plan schema.
      2. Extract JSON from markdown fence.
      3. Validate via pydantic.
      4. On failure, retry with error feedback (up to 3 times).
    """

    def __init__(self, provider: ProviderAdapter, model: str) -> None:
        self.provider = provider
        self.model = model

    async def decompose(
        self,
        goal: Goal,
        *,
        project_context: str = "",
        max_attempts: int = 3,
    ) -> tuple[Plan, Usage]:
        """Return (validated Plan, total Usage)."""
        messages: list[CanonicalMessage] = []
        messages.append(
            CanonicalMessage.from_text(Role.SYSTEM, _PLAN_PROMPT)
        )
        user_prompt = self._build_user_prompt(goal, project_context)
        messages.append(CanonicalMessage.from_text(Role.USER, user_prompt))

        total_usage = Usage()
        last_error: str | None = None

        for attempt in range(1, max_attempts + 1):
            req = CanonicalRequest(
                model=self.model,
                messages=messages,
                tools=[],  # no tool calls
                max_output_tokens=8192,
                temperature=0.2,
            )

            text, usage = await self._complete(req)
            total_usage.prompt_tokens += usage.prompt_tokens
            total_usage.completion_tokens += usage.completion_tokens
            total_usage.total_tokens += usage.total_tokens

            try:
                plan = self._parse_and_validate(text, goal.id)
                return plan, total_usage
            except (ValueError, json.JSONDecodeError) as e:
                last_error = str(e)
                # inject feedback for next attempt
                messages.append(CanonicalMessage.from_text(Role.ASSISTANT, text))
                messages.append(
                    CanonicalMessage.from_text(
                        Role.USER,
                        f"Your previous response failed validation: {e}\n\n"
                        "Please retry with a valid JSON object matching the schema.",
                        synthetic=True,
                    )
                )
                continue

        raise RuntimeError(
            f"PlanAgent failed after {max_attempts} attempts; last error: {last_error}"
        )

    def _build_user_prompt(self, goal: Goal, project_context: str) -> str:
        ctx = ""
        if project_context:
            ctx = f"\n\nProject context:\n{project_context}"
        constraints = ""
        if goal.constraints.deadline:
            constraints += f"\nDeadline: {goal.constraints.deadline}"
        if goal.constraints.max_token_budget:
            constraints += f"\nMax token budget: {goal.constraints.max_token_budget}"
        if goal.constraints.scope_paths:
            constraints += f"\nScope paths: {', '.join(goal.constraints.scope_paths)}"

        return f"Goal: {goal.text}{ctx}{constraints}"

    async def _complete(self, req: CanonicalRequest) -> tuple[str, Usage]:
        """Run a non-streaming completion. Returns (text, usage)."""
        text_parts: list[str] = []
        usage = Usage()
        error: str | None = None
        async for ev in self.provider.stream(req):
            if ev.type == "text_delta" and ev.text:
                text_parts.append(ev.text)
            elif ev.type == "usage" and ev.usage:
                usage = ev.usage
            elif ev.type == "error":
                error = ev.error
                break
        if error:
            raise RuntimeError(f"PlanAgent LLM error: {error}")
        return "".join(text_parts), usage

    def _parse_and_validate(self, text: str, goal_id: str) -> Plan:
        try:
            json_text = PlanAgent._extract_json(text)
            obj = json.loads(json_text)
            if not isinstance(obj, dict) or "steps" not in obj:
                raise ValueError("expected {'steps': [...]}")
            steps_in = obj["steps"]
            if not isinstance(steps_in, list) or not steps_in:
                raise ValueError("'steps' must be a non-empty list")
            if len(steps_in) > 20:
                raise ValueError("too many steps (>20) — keep plans small")

            steps: list[PlanStep] = []
            id_by_idx: dict[int, str] = {}
            for i, raw in enumerate(steps_in, start=1):
                sid = f"s{i}"
                id_by_idx[i] = sid
                kind_raw = raw.get("kind", "code")
                try:
                    kind = StepKind(kind_raw)
                except ValueError:
                    raise ValueError(f"unknown step kind: {kind_raw}")
                action = raw.get("action", "").strip()
                if not action:
                    raise ValueError(f"step {i} missing 'action'")
                deps_raw = raw.get("deps", []) or []
                deps: list[str] = []
                for d in deps_raw:
                    if isinstance(d, int):
                        if d < 1 or d >= i:
                            raise ValueError(f"step {i} dep {d} invalid (must be 1..{i-1})")
                        deps.append(id_by_idx[d])
                    elif isinstance(d, str):
                        if d not in id_by_idx.values():
                            raise ValueError(f"step {i} dep '{d}' not yet defined")
                        deps.append(d)
                    else:
                        raise ValueError(f"step {i} dep must be int or str, got {type(d)}")
                step = PlanStep(
                    id=sid,
                    plan_id="",
                    idx=i - 1,
                    kind=kind,
                    action=action,
                    deps=deps,
                )
                steps.append(step)

            return Plan(goal_id=goal_id, steps=steps)
        except (ValueError, json.JSONDecodeError) as e:
            raise e

    @staticmethod
    def _extract_json(text: str) -> str:
        """Extract JSON from ```json ... ``` fences, or use the full text."""
        # try fenced
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if m:
            return m.group(1)
        # try raw object
        m = re.search(r"(\{.*\})", text, re.DOTALL)
        if m:
            return m.group(1)
        raise ValueError("no JSON object found in LLM response")


__all__ = ["PlanAgent"]
