"""Tests for plan/models.py — Goal/Plan/PlanStep + state machine + DAG."""

from __future__ import annotations

import pytest

from pure_agent.plan.models import (
    Goal,
    GoalConstraints,
    GoalStatus,
    Plan,
    PlanStatus,
    PlanStep,
    StepKind,
    StepReport,
    StepStatus,
    all_deps_done,
    can_transition_goal,
    can_transition_plan,
    can_transition_step,
    is_plan_complete,
    topo_sort,
)


def step(id_: str, idx: int, deps: list[str] | None = None, kind: StepKind = StepKind.CODE) -> PlanStep:
    return PlanStep(
        id=id_,
        plan_id="p1",
        idx=idx,
        kind=kind,
        action=f"action {id_}",
        deps=deps or [],
    )


# ─── state machine ──────────────────────────────────────────────────────────


@pytest.mark.smoke
def test_goal_valid_transitions() -> None:
    assert can_transition_goal(GoalStatus.PENDING, GoalStatus.PLANNING)
    assert can_transition_goal(GoalStatus.PLANNING, GoalStatus.RUNNING)
    assert can_transition_goal(GoalStatus.RUNNING, GoalStatus.DONE)
    assert can_transition_goal(GoalStatus.RUNNING, GoalStatus.FAILED)
    assert not can_transition_goal(GoalStatus.DONE, GoalStatus.RUNNING)
    assert not can_transition_goal(GoalStatus.FAILED, GoalStatus.RUNNING)


@pytest.mark.smoke
def test_plan_valid_transitions() -> None:
    assert can_transition_plan(PlanStatus.PENDING, PlanStatus.IN_PROGRESS)
    assert can_transition_plan(PlanStatus.IN_PROGRESS, PlanStatus.DONE)
    assert not can_transition_plan(PlanStatus.DONE, PlanStatus.IN_PROGRESS)


@pytest.mark.smoke
def test_step_valid_transitions() -> None:
    assert can_transition_step(StepStatus.PENDING, StepStatus.IN_PROGRESS)
    assert can_transition_step(StepStatus.IN_PROGRESS, StepStatus.DONE)
    assert can_transition_step(StepStatus.IN_PROGRESS, StepStatus.FAILED)
    # retry
    assert can_transition_step(StepStatus.FAILED, StepStatus.IN_PROGRESS)
    # blocked + skip
    assert can_transition_step(StepStatus.PENDING, StepStatus.BLOCKED)
    assert can_transition_step(StepStatus.PENDING, StepStatus.SKIPPED)
    # terminal
    assert not can_transition_step(StepStatus.DONE, StepStatus.IN_PROGRESS)
    assert not can_transition_step(StepStatus.SKIPPED, StepStatus.PENDING)


# ─── DAG validation ────────────────────────────────────────────────────────


@pytest.mark.smoke
def test_plan_valid_dag() -> None:
    p = Plan(
        goal_id="g1",
        steps=[
            step("a", 0),
            step("b", 1, deps=["a"]),
            step("c", 2, deps=["a", "b"]),
        ],
    )
    assert len(p.steps) == 3


@pytest.mark.smoke
def test_plan_missing_dep_rejected() -> None:
    with pytest.raises(ValueError, match="dep"):
        Plan(goal_id="g1", steps=[step("a", 0, deps=["missing"])])


@pytest.mark.smoke
def test_plan_self_dep_rejected() -> None:
    with pytest.raises(ValueError, match="depend on itself"):
        Plan(goal_id="g1", steps=[step("a", 0, deps=["a"])])


@pytest.mark.smoke
def test_plan_cycle_rejected() -> None:
    with pytest.raises(ValueError, match="cycle"):
        Plan(
            goal_id="g1",
            steps=[
                step("a", 0, deps=["b"]),
                step("b", 1, deps=["a"]),
            ],
        )


@pytest.mark.smoke
def test_plan_duplicate_ids_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate step ids"):
        Plan(
            goal_id="g1",
            steps=[step("a", 0), step("a", 1)],
        )


@pytest.mark.smoke
def test_plan_duplicate_idx_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate idx"):
        Plan(
            goal_id="g1",
            steps=[step("a", 0), step("b", 0)],
        )


# ─── topo sort ──────────────────────────────────────────────────────────────


@pytest.mark.smoke
def test_topo_sort_respects_deps() -> None:
    p = Plan(
        goal_id="g1",
        steps=[
            step("c", 2, deps=["a", "b"]),
            step("a", 0),
            step("b", 1, deps=["a"]),
        ],
    )
    order = [s.id for s in topo_sort(p.steps)]
    assert order.index("a") < order.index("b") < order.index("c")


@pytest.mark.smoke
def test_all_deps_done() -> None:
    p = Plan(
        goal_id="g1",
        steps=[step("a", 0), step("b", 1, deps=["a"])],
    )
    by_id = {s.id: s for s in p.steps}
    assert all_deps_done(by_id["b"], by_id) is False
    by_id["a"].status = StepStatus.DONE
    assert all_deps_done(by_id["b"], by_id) is True


@pytest.mark.smoke
def test_is_plan_complete() -> None:
    p = Plan(
        goal_id="g1",
        steps=[
            step("a", 0),
            step("b", 1, deps=["a"], kind=StepKind.CODE),
            step("c", 2, deps=["b"], kind=StepKind.VERIFY),
        ],
    )
    assert not is_plan_complete(p.steps)
    p.steps[0].status = StepStatus.DONE
    p.steps[1].status = StepStatus.DONE
    p.steps[2].status = StepStatus.SKIPPED  # skipped counts as complete
    assert is_plan_complete(p.steps)


# ─── Goal/Plan basic ────────────────────────────────────────────────────────


@pytest.mark.smoke
def test_goal_defaults() -> None:
    g = Goal(project_id="p1", text="build a thing")
    assert g.status == GoalStatus.PENDING
    assert g.id.startswith("goal_")
    assert g.constraints.max_token_budget is None


@pytest.mark.smoke
def test_goal_with_constraints() -> None:
    g = Goal(
        project_id="p1",
        text="x",
        constraints=GoalConstraints(max_token_budget=1000, scope_paths=["src/"]),
    )
    assert g.constraints.max_token_budget == 1000


@pytest.mark.smoke
def test_step_report_typed() -> None:
    r = StepReport(
        verdict="pass", summary="done", files_changed=["x.py"], notes="ok"
    )
    assert r.verdict == "pass"
    assert "x.py" in r.files_changed


@pytest.mark.smoke
def test_step_attempts_default() -> None:
    s = step("a", 0)
    assert s.attempts == 0
    assert s.max_attempts == 3


@pytest.mark.smoke
def test_step_deps_unique() -> None:
    with pytest.raises(ValueError, match="unique"):
        PlanStep(
            id="a", plan_id="p1", idx=0, kind=StepKind.CODE, action="x", deps=["b", "b"]
        )
