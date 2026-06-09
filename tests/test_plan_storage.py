"""Tests for plan/storage.py — SQLite CRUD."""

from __future__ import annotations

import pytest

from pure_agent.persistence import Database
from pure_agent.plan import (
    Goal,
    GoalConstraints,
    Plan,
    PlanStatus,
    PlanStep,
    StepKind,
    StepStatus,
    StepReport,
)
from pure_agent.plan.storage import PlanStorage


@pytest.fixture
def db(tmp_path) -> Database:
    db = Database(path=tmp_path / "test.db")
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    # pre-create the project ids used by tests
    for pid in ("p1", "p2", "default"):
        db.conn.execute(
            "INSERT OR IGNORE INTO projects (id, name, hash, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (pid, pid, pid, now, now),
        )
    return db


@pytest.fixture
def storage(db) -> PlanStorage:
    return PlanStorage(db)


@pytest.mark.smoke
def test_create_and_get_goal(storage) -> None:
    g = storage.create_goal(project_id="p1", text="x")
    fetched = storage.get_goal(g.id)
    assert fetched is not None
    assert fetched.text == "x"
    assert fetched.id == g.id


@pytest.mark.smoke
def test_list_goals(storage) -> None:
    g1 = storage.create_goal(project_id="p1", text="a")
    g2 = storage.create_goal(project_id="p1", text="b")
    out = storage.list_goals(project_id="p1")
    ids = [g.id for g in out]
    assert g1.id in ids
    assert g2.id in ids


@pytest.mark.smoke
def test_create_plan_with_steps(storage) -> None:
    g = storage.create_goal(project_id="p1", text="x")
    p = Plan(
        goal_id=g.id,
        steps=[
            PlanStep(id="a", plan_id="", idx=0, kind=StepKind.READ, action="r"),
            PlanStep(id="b", plan_id="", idx=1, kind=StepKind.CODE, action="c", deps=["a"]),
        ],
    )
    for s in p.steps:
        s.plan_id = p.id
    storage.create_plan(p)

    fetched = storage.get_plan(p.id)
    assert fetched is not None
    assert len(fetched.steps) == 2
    assert fetched.steps[0].id == "a"
    assert fetched.steps[1].deps == ["a"]


@pytest.mark.smoke
def test_upsert_step_updates_status(storage) -> None:
    g = storage.create_goal(project_id="p1", text="x")
    p = Plan(
        goal_id=g.id,
        steps=[PlanStep(id="a", plan_id="", idx=0, kind=StepKind.READ, action="r")],
    )
    for s in p.steps:
        s.plan_id = p.id
    storage.create_plan(p)

    s = p.steps[0]
    s.status = StepStatus.IN_PROGRESS
    s.attempts = 1
    storage.upsert_step(s)

    fetched = storage.get_plan(p.id)
    assert fetched.steps[0].status == StepStatus.IN_PROGRESS
    assert fetched.steps[0].attempts == 1


@pytest.mark.smoke
def test_step_report_persisted(storage) -> None:
    g = storage.create_goal(project_id="p1", text="x")
    p = Plan(
        goal_id=g.id,
        steps=[PlanStep(id="a", plan_id="", idx=0, kind=StepKind.READ, action="r")],
    )
    for s in p.steps:
        s.plan_id = p.id
    storage.create_plan(p)
    s = p.steps[0]
    s.step_report = StepReport(verdict="pass", summary="ok", files_changed=["x.py"])
    s.status = StepStatus.DONE
    storage.upsert_step(s)

    fetched = storage.get_plan(p.id)
    assert fetched.steps[0].step_report is not None
    assert fetched.steps[0].step_report.verdict == "pass"
    assert fetched.steps[0].step_report.files_changed == ["x.py"]


@pytest.mark.smoke
def test_list_plans(storage) -> None:
    g = storage.create_goal(project_id="p1", text="x")
    p1 = Plan(goal_id=g.id, steps=[PlanStep(id="a", plan_id="", idx=0, kind=StepKind.READ, action="r")])
    for s in p1.steps:
        s.plan_id = p1.id
    storage.create_plan(p1)
    p2 = Plan(goal_id=g.id, steps=[])
    storage.create_plan(p2)

    out = storage.list_plans(goal_id=g.id)
    assert len(out) == 2


@pytest.mark.smoke
def test_update_plan_status(storage) -> None:
    g = storage.create_goal(project_id="p1", text="x")
    p = Plan(goal_id=g.id, steps=[])
    storage.create_plan(p)
    storage.update_plan_status(p.id, PlanStatus.IN_PROGRESS)
    fetched = storage.get_plan(p.id)
    assert fetched.status == PlanStatus.IN_PROGRESS


@pytest.mark.smoke
def test_increment_plan_version(storage) -> None:
    g = storage.create_goal(project_id="p1", text="x")
    p = Plan(goal_id=g.id, steps=[])
    storage.create_plan(p)
    v1 = storage.increment_plan_version(p.id)
    assert v1 == 2
    v2 = storage.increment_plan_version(p.id)
    assert v2 == 3
