"""Token budget — track and enforce per-step / per-plan / per-session token usage."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from pure_agent.model import Usage
from pure_agent.persistence.db import Database


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str = "tok") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass
class BudgetExceeded(Exception):
    scope: str  # "step" | "plan" | "session"
    used: int
    limit: int

    def __str__(self) -> str:
        return f"token budget exceeded for {self.scope}: {self.used} > {self.limit}"


@dataclass
class TokenBudget:
    """Tracks and enforces per-scope token budgets.

    Usage:
        budget = TokenBudget(db, per_step=20_000, per_plan=100_000, per_session=1_000_000)
        with budget.step("step_1") as b:
            b.add(usage)
            ...
            if b.exceeded:
                raise BudgetExceeded(...)
    """

    db: Database
    per_step: int | None = None
    per_plan: int | None = None
    per_session: int | None = None

    _session_id: str = "default"
    _session_used: int = 0
    _plan_id: str | None = None
    _plan_used: int = 0

    def reset_plan(self) -> None:
        self._plan_id = None
        self._plan_used = 0

    @contextmanager
    def step(self, step_id: str, plan_id: str | None = None) -> Iterator["StepBudget"]:
        sb = StepBudget(self, step_id, plan_id=plan_id)
        try:
            yield sb
        finally:
            sb.flush()

    def _record(self, *, step_id: str | None, plan_id: str | None, model: str, usage: Usage) -> None:
        """Persist the token record to DB. Does NOT update session/plan counters
        (the StepBudget already does that in add() to keep counters live)."""
        self.db.conn.execute(
            "INSERT INTO token_usage (id, session_id, plan_id, step_id, model, "
            "prompt_tokens, completion_tokens, total_tokens, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _new_id(),
                self._session_id,
                plan_id,
                step_id,
                model,
                usage.prompt_tokens,
                usage.completion_tokens,
                usage.total_tokens,
                _now_iso(),
            ),
        )

    def check_step(self, used: int) -> None:
        if self.per_step is not None and used > self.per_step:
            raise BudgetExceeded("step", used, self.per_step)

    def check_plan(self) -> None:
        if self.per_plan is not None and self._plan_used > self.per_plan:
            raise BudgetExceeded("plan", self._plan_used, self.per_plan)

    def check_session(self) -> None:
        if self.per_session is not None and self._session_used > self.per_session:
            raise BudgetExceeded("session", self._session_used, self.per_session)

    def totals(self) -> dict[str, Any]:
        return {
            "session": self._session_used,
            "plan": self._plan_used,
        }


@dataclass
class StepBudget:
    """Per-step token budget accumulator."""

    parent: TokenBudget
    step_id: str
    plan_id: str | None
    used: int = 0
    prompt_used: int = 0
    completion_used: int = 0
    model: str = "unknown"

    def add(self, usage: Usage, *, model: str | None = None) -> None:
        self.used += usage.total_tokens
        self.prompt_used += usage.prompt_tokens
        self.completion_used += usage.completion_tokens
        if model:
            self.model = model
        # always accumulate session
        self.parent._session_used += usage.total_tokens
        # accumulate plan if plan_id set
        if self.plan_id is not None:
            self.parent._plan_used += usage.total_tokens
        # check this step first (most specific)
        self.parent.check_step(self.used)
        # check plan + session
        self.parent.check_plan()
        self.parent.check_session()

    def flush(self) -> None:
        if self.used == 0:
            return
        self.parent._record(
            step_id=self.step_id,
            plan_id=self.plan_id,
            model=self.model,
            usage=Usage(
                prompt_tokens=self.prompt_used,
                completion_tokens=self.completion_used,
                total_tokens=self.used,
            ),
        )


__all__ = ["TokenBudget", "StepBudget", "BudgetExceeded"]
