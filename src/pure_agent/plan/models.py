"""Goal/Plan/PlanStep data models.

Typed pydantic models that power pure-agent's long-running task decomposition.
The DAG and state machine are validated at construction time so callers can
trust the in-memory representation.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str = "id") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ─── enums ────────────────────────────────────────────────────────────────────


class GoalStatus(str, Enum):
    PENDING = "pending"
    PLANNING = "planning"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    ABANDONED = "abandoned"


class PlanStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"
    ABANDONED = "abandoned"


class StepStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


class StepKind(str, Enum):
    READ = "read"
    CODE = "code"
    SEARCH = "search"
    VERIFY = "verify"
    DELIVER = "deliver"
    PLAN = "plan"


# ─── step result ─────────────────────────────────────────────────────────────


class StepReport(BaseModel):
    """Structured result returned by a completed step (typed protocol)."""

    verdict: Literal["pass", "fail", "needs_fix", "skipped"] = "pass"
    summary: str = Field(..., min_length=1, description="One-paragraph summary")
    files_changed: list[str] = Field(default_factory=list)
    notes: str | None = None
    artifacts: dict[str, Any] = Field(default_factory=dict)


# ─── goal ─────────────────────────────────────────────────────────────────────


class GoalConstraints(BaseModel):
    deadline: str | None = None
    max_token_budget: int | None = Field(default=None, ge=0)
    scope_paths: list[str] = Field(default_factory=list)


class Goal(BaseModel):
    id: str = Field(default_factory=lambda: _new_id("goal"))
    project_id: str
    text: str
    constraints: GoalConstraints = Field(default_factory=GoalConstraints)
    status: GoalStatus = GoalStatus.PENDING
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


# ─── plan ─────────────────────────────────────────────────────────────────────


class PlanStep(BaseModel):
    id: str = Field(default_factory=lambda: _new_id("step"))
    plan_id: str
    idx: int = Field(..., ge=0)
    kind: StepKind
    action: str = Field(..., min_length=1)
    deps: list[str] = Field(default_factory=list)
    status: StepStatus = StepStatus.PENDING
    assigned_subagent: str | None = None
    attempts: int = 0
    max_attempts: int = 3
    last_error: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    step_report: StepReport | None = None

    @field_validator("deps")
    @classmethod
    def _deps_unique(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("deps must be unique")
        return v


class Plan(BaseModel):
    id: str = Field(default_factory=lambda: _new_id("plan"))
    goal_id: str
    version: int = 1
    status: PlanStatus = PlanStatus.PENDING
    steps: list[PlanStep] = Field(default_factory=list)
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)

    @model_validator(mode="after")
    def _validate_dag(self) -> "Plan":
        # unique ids
        ids = [s.id for s in self.steps]
        if len(set(ids)) != len(ids):
            raise ValueError("plan has duplicate step ids")
        # idx uniqueness
        idxs = [s.idx for s in self.steps]
        if len(set(idxs)) != len(idxs):
            raise ValueError("plan has duplicate idx values")
        # deps must reference existing steps
        id_set = set(ids)
        for s in self.steps:
            for d in s.deps:
                if d not in id_set:
                    raise ValueError(f"step {s.id} dep {d} not in plan")
            if s.id in s.deps:
                raise ValueError(f"step {s.id} cannot depend on itself")
        # cycle check (Kahn's algorithm)
        in_degree: dict[str, int] = {s.id: 0 for s in self.steps}
        edges: dict[str, list[str]] = {s.id: [] for s in self.steps}
        for s in self.steps:
            for d in s.deps:
                in_degree[s.id] += 1
                edges[d].append(s.id)
        # find initial nodes
        from collections import deque

        q = deque([sid for sid, deg in in_degree.items() if deg == 0])
        visited = 0
        while q:
            cur = q.popleft()
            visited += 1
            for nxt in edges[cur]:
                in_degree[nxt] -= 1
                if in_degree[nxt] == 0:
                    q.append(nxt)
        if visited != len(self.steps):
            raise ValueError("plan has cycle in deps")
        return self


# ─── state machine helpers ──────────────────────────────────────────────────


_ALLOWED_GOAL_TRANSITIONS: dict[GoalStatus, set[GoalStatus]] = {
    GoalStatus.PENDING: {GoalStatus.PLANNING, GoalStatus.ABANDONED},
    GoalStatus.PLANNING: {GoalStatus.RUNNING, GoalStatus.FAILED, GoalStatus.ABANDONED},
    GoalStatus.RUNNING: {GoalStatus.DONE, GoalStatus.FAILED, GoalStatus.ABANDONED},
    GoalStatus.DONE: set(),
    GoalStatus.FAILED: set(),
    GoalStatus.ABANDONED: set(),
}


_ALLOWED_PLAN_TRANSITIONS: dict[PlanStatus, set[PlanStatus]] = {
    PlanStatus.PENDING: {PlanStatus.IN_PROGRESS, PlanStatus.ABANDONED},
    PlanStatus.IN_PROGRESS: {PlanStatus.DONE, PlanStatus.FAILED, PlanStatus.ABANDONED},
    PlanStatus.DONE: set(),
    PlanStatus.FAILED: set(),
    PlanStatus.ABANDONED: set(),
}


_ALLOWED_STEP_TRANSITIONS: dict[StepStatus, set[StepStatus]] = {
    StepStatus.PENDING: {StepStatus.IN_PROGRESS, StepStatus.BLOCKED, StepStatus.SKIPPED},
    StepStatus.IN_PROGRESS: {StepStatus.DONE, StepStatus.FAILED},
    StepStatus.DONE: set(),
    StepStatus.FAILED: {StepStatus.IN_PROGRESS, StepStatus.SKIPPED},  # retry
    StepStatus.BLOCKED: {StepStatus.PENDING, StepStatus.SKIPPED},
    StepStatus.SKIPPED: set(),
}


def can_transition_goal(from_: GoalStatus, to: GoalStatus) -> bool:
    return to in _ALLOWED_GOAL_TRANSITIONS.get(from_, set())


def can_transition_plan(from_: PlanStatus, to: PlanStatus) -> bool:
    return to in _ALLOWED_PLAN_TRANSITIONS.get(from_, set())


def can_transition_step(from_: StepStatus, to: StepStatus) -> bool:
    return to in _ALLOWED_STEP_TRANSITIONS.get(from_, set())


# ─── plan operations ────────────────────────────────────────────────────────


def topo_sort(steps: list[PlanStep]) -> list[PlanStep]:
    """Return steps in dependency order (deps before dependents)."""
    by_id = {s.id: s for s in steps}
    in_degree: dict[str, int] = {s.id: len(s.deps) for s in steps}
    children: dict[str, list[str]] = {s.id: [] for s in steps}
    for s in steps:
        for d in s.deps:
            children[d].append(s.id)
    from collections import deque

    q = deque(sorted([sid for sid, deg in in_degree.items() if deg == 0]))
    out: list[PlanStep] = []
    while q:
        cur = q.popleft()
        out.append(by_id[cur])
        for nxt in sorted(children[cur]):
            in_degree[nxt] -= 1
            if in_degree[nxt] == 0:
                q.append(nxt)
    return out


def is_plan_complete(steps: list[PlanStep]) -> bool:
    """Plan is complete when all non-blocked, non-skipped steps are done."""
    for s in steps:
        if s.status in (StepStatus.SKIPPED, StepStatus.BLOCKED):
            continue
        if s.status != StepStatus.DONE:
            return False
    return True


def all_deps_done(step: PlanStep, steps_by_id: dict[str, PlanStep]) -> bool:
    for d in step.deps:
        if d not in steps_by_id:
            return False
        if steps_by_id[d].status != StepStatus.DONE:
            return False
    return True


__all__ = [
    "GoalStatus",
    "PlanStatus",
    "StepStatus",
    "StepKind",
    "StepReport",
    "GoalConstraints",
    "Goal",
    "PlanStep",
    "Plan",
    "can_transition_goal",
    "can_transition_plan",
    "can_transition_step",
    "topo_sort",
    "is_plan_complete",
    "all_deps_done",
    "new_id",
    "now_iso",
]


def now_iso() -> str:
    return _now_iso()
