"""PlanRunner — executes a Plan step-by-step using AIAgentLoop."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ValidationError

from pure_agent.agent import AIAgentLoop
from pure_agent.model import (
    CanonicalMessage,
    Role,
    TextBlock,
    ToolSchema,
    Usage,
)
from pure_agent.plan.models import (
    Plan,
    PlanStatus,
    PlanStep,
    StepReport,
    StepStatus,
    all_deps_done,
    can_transition_step,
    is_plan_complete,
    now_iso,
    topo_sort,
)
from pure_agent.plan.storage import PlanStorage
from pure_agent.tools import ToolRegistry
from pure_agent.memory import FileTracker

def _summarize_step(s: PlanStep) -> str:
    rep = s.step_report
    if rep is None:
        return f"[{s.status.value}]"
    return f"[{rep.verdict}] {rep.summary}"


def _build_step_system_prompt(plan: Plan, step: PlanStep, goal_text: str) -> str:
    """Build the system prompt for executing one plan step."""
    done_steps = [s for s in plan.steps if s.status == StepStatus.DONE]
    failed_steps = [s for s in plan.steps if s.status == StepStatus.FAILED]
    skipped = [s for s in plan.steps if s.status == StepStatus.SKIPPED]

    lines = [
        "You are executing a step in a pure-agent plan.",
        "",
        f"Plan id: {plan.id} (v{plan.version})",
        f"Step: {step.id} (idx={step.idx}, kind={step.kind.value})",
        f"Action: {step.action}",
        f"Attempts so far: {step.attempts}/{step.max_attempts}",
        "",
        f"Goal: {goal_text}",
    ]
    if done_steps:
        lines.append("")
        lines.append("Completed steps (their reports):")
        for s in done_steps:
            lines.append(f"  {s.id} {s.action} → {_summarize_step(s)}")
    if failed_steps:
        lines.append("")
        lines.append("Previously failed steps:")
        for s in failed_steps:
            lines.append(f"  {s.id} {s.action} → ERROR: {s.last_error or 'unknown'}")
    if skipped:
        lines.append("")
        lines.append("Skipped steps:")
        for s in skipped:
            lines.append(f"  {s.id} {s.action}")

    lines.append("")
    lines.append("When you finish, your final response should be a JSON object with:")
    lines.append("  verdict: 'pass' | 'fail' | 'needs_fix' | 'skipped'")
    lines.append("  summary: <one-sentence description of what you did>")
    lines.append("  files_changed: <list of file paths you modified>")
    lines.append("  notes: <any caveats or context>")

    return "\n".join(lines)


class PlanRunResult(BaseModel):
    plan_id: str
    final_status: PlanStatus
    steps_completed: int
    steps_failed: int
    total_usage: Usage
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.final_status == PlanStatus.DONE


class PlanRunner:
    """Executes a Plan step-by-step.

    Design:
      - one AIAgentLoop per step
      - typed Plan validates each step's report
      - persistent: every status change goes to SQLite
      - interruptible: abort_signal checked at safe points
    """

    def __init__(
        self,
        storage: PlanStorage,
        loop_factory: Callable[..., AIAgentLoop],
        *,
        on_event: Callable[[str, dict], None] | None = None,
        memory: Any | None = None,  # MemoryLayers
    ) -> None:
        self.storage = storage
        self._loop_factory = loop_factory
        self._on_event = on_event or (lambda t, p: None)
        self._memory = memory

    def _emit(self, t: str, **p: Any) -> None:
        try:
            self._on_event(t, p)
        except Exception:
            pass

    async def execute(
        self,
        plan_id: str,
        *,
        abort_signal: asyncio.Event | None = None,
        max_total_turns: int = 200,
    ) -> PlanRunResult:
        plan = self.storage.get_plan(plan_id)
        if plan is None:
            return PlanRunResult(
                plan_id=plan_id,
                final_status=PlanStatus.FAILED,
                steps_completed=0,
                steps_failed=0,
                total_usage=Usage(),
                error=f"plan not found: {plan_id}",
            )
        if plan.status in (PlanStatus.DONE, PlanStatus.ABANDONED):
            return PlanRunResult(
                plan_id=plan_id,
                final_status=plan.status,
                steps_completed=sum(1 for s in plan.steps if s.status == StepStatus.DONE),
                steps_failed=sum(1 for s in plan.steps if s.status == StepStatus.FAILED),
                total_usage=Usage(),
                error=f"plan already {plan.status.value}",
            )

        # mark plan in_progress
        plan.status = PlanStatus.IN_PROGRESS
        self.storage.update_plan_status(plan_id, plan.status)

        goal_text = self._goal_text(plan)

        total_usage = Usage()
        steps_by_id = {s.id: s for s in plan.steps}
        total_turns_used = 0
        steps_completed = 0
        steps_failed = 0

        # iterate in topo order
        for step in topo_sort(plan.steps):
            if abort_signal is not None and abort_signal.is_set():
                self._emit("plan_aborted", plan_id=plan_id)
                return PlanRunResult(
                    plan_id=plan_id,
                    final_status=PlanStatus.ABANDONED,
                    steps_completed=steps_completed,
                    steps_failed=steps_failed,
                    total_usage=total_usage,
                    error="aborted",
                )

            # skip already-done (resumed)
            if step.status == StepStatus.DONE:
                steps_completed += 1
                continue

            # check deps
            if not all_deps_done(step, steps_by_id):
                step.status = StepStatus.BLOCKED
                self.storage.upsert_step(step)
                self._emit("step_blocked", step_id=step.id, reason="deps not met")
                continue

            # skip if blocked/skipped
            if step.status in (StepStatus.BLOCKED, StepStatus.SKIPPED):
                continue

            # run
            self._emit("step_start", plan_id=plan_id, step_id=step.id, action=step.action)
            result = await self._run_step(plan, step, goal_text, abort_signal)
            total_usage.prompt_tokens += result.total_usage.prompt_tokens
            total_usage.completion_tokens += result.total_usage.completion_tokens
            total_usage.total_tokens += result.total_usage.total_tokens
            total_turns_used += result.turns

            # update step
            self.storage.upsert_step(step)
            # Phase 6: extract facts from step result and store in memory
            if self._memory is not None and result.final_text:
                self._store_step_facts(step, result)
            self._emit(
                "step_end",
                plan_id=plan_id,
                step_id=step.id,
                status=step.status.value,
            )
            if step.status == StepStatus.DONE:
                steps_completed += 1
            elif step.status == StepStatus.FAILED:
                steps_failed += 1
                if step.attempts >= step.max_attempts:
                    self._emit("plan_failed", plan_id=plan_id, step_id=step.id)
                    plan.status = PlanStatus.FAILED
                    self.storage.update_plan_status(plan_id, plan.status)
                    return PlanRunResult(
                        plan_id=plan_id,
                        final_status=PlanStatus.FAILED,
                        steps_completed=steps_completed,
                        steps_failed=steps_failed,
                        total_usage=total_usage,
                        error=f"step {step.id} failed after {step.attempts} attempts: {step.last_error}",
                    )

            if total_turns_used >= max_total_turns:
                plan.status = PlanStatus.FAILED
                self.storage.update_plan_status(plan_id, plan.status)
                return PlanRunResult(
                    plan_id=plan_id,
                    final_status=PlanStatus.FAILED,
                    steps_completed=steps_completed,
                    steps_failed=steps_failed,
                    total_usage=total_usage,
                    error=f"max_total_turns reached ({max_total_turns})",
                )

        # determine final status
        if is_plan_complete(plan.steps):
            plan.status = PlanStatus.DONE
        elif steps_failed > 0:
            plan.status = PlanStatus.FAILED
        self.storage.update_plan_status(plan_id, plan.status)
        return PlanRunResult(
            plan_id=plan_id,
            final_status=plan.status,
            steps_completed=steps_completed,
            steps_failed=steps_failed,
            total_usage=total_usage,
        )

    async def resume(self, plan_id: str, **kwargs: Any) -> PlanRunResult:
        """Resume a plan. Equivalent to execute() — done steps are skipped."""
        return await self.execute(plan_id, **kwargs)

    def _goal_text(self, plan: Plan) -> str:
        row = self.storage.db.conn.execute(
            "SELECT text FROM goals WHERE id = ?", (plan.goal_id,)
        ).fetchone()
        return row["text"] if row else ""

    async def _run_step(
        self,
        plan: Plan,
        step: PlanStep,
        goal_text: str,
        abort_signal: asyncio.Event | None,
    ) -> Any:
        # mark in_progress
        if step.status == StepStatus.PENDING and can_transition_step(StepStatus.PENDING, StepStatus.IN_PROGRESS):
            step.status = StepStatus.IN_PROGRESS
        step.attempts += 1
        step.started_at = now_iso()
        self.storage.upsert_step(step)

        sys_prompt = _build_step_system_prompt(plan, step, goal_text)
        loop = self._loop_factory(system_prompt=sys_prompt)
        result = await loop.run(step.action, max_turns=20, abort_signal=abort_signal)

        # parse final assistant text as StepReport
        report = self._parse_step_report(result, step)
        if report is None:
            # couldn't parse → fail with retry
            step.status = StepStatus.FAILED
            step.last_error = "agent did not return a parseable StepReport"
            step.completed_at = now_iso()
            return result

        step.step_report = report
        step.completed_at = now_iso()
        # map verdict to step status
        if report.verdict == "pass":
            step.status = StepStatus.DONE
        elif report.verdict == "skipped":
            step.status = StepStatus.SKIPPED
        elif report.verdict in ("fail", "needs_fix"):
            step.status = StepStatus.FAILED
            step.last_error = report.summary
        return result

    @staticmethod
    def _parse_step_report(result: Any, step: PlanStep) -> StepReport | None:
        """Try to extract a StepReport from the agent's final message.

        Looks for ```json ... ``` block; if not found, tries the raw text.
        On failure, returns None.
        """
        text = ""
        for m in reversed(result.messages):
            if m.role == Role.ASSISTANT:
                text = "".join(b.text for b in m.content if isinstance(b, TextBlock))
                if text:
                    break
        if not text:
            return None

        # try fenced json
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if m:
            try:
                obj = json.loads(m.group(1))
                return StepReport.model_validate(obj)
            except (json.JSONDecodeError, ValidationError):
                pass
        # try bare object
        m = re.search(r"(\{[^{}]*\"verdict\"[^{}]*\})", text, re.DOTALL)
        if m:
            try:
                obj = json.loads(m.group(1))
                return StepReport.model_validate(obj)
            except (json.JSONDecodeError, ValidationError):
                pass
        return None


    def _store_step_facts(self, step: PlanStep, result: Any) -> None:
        """Phase 6: extract facts from a step result and store in memory layers."""
        from pure_agent.memory import extract_episodic, extract_facts

        text = result.final_text or ""
        if not text:
            return
        # episodic: every step
        epi_facts = extract_episodic(text, max_facts=2)
        for f in epi_facts:
            try:
                self._memory.episodic.add(
                    f,
                    metadata={"step_id": step.id, "plan_id": step.plan_id},
                )
            except Exception:
                pass
        # semantic: only if step is "done" (passed verification)
        if step.status.value == "done":
            sem_facts = extract_facts(text, max_facts=2)
            for f in sem_facts:
                try:
                    self._memory.semantic.add(
                        f,
                        source="plan_runner",
                        confidence=0.7,
                    )
                except Exception:
                    pass


__all__ = ["PlanRunner", "PlanRunResult"]
