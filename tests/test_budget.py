"""Tests for TokenBudget."""

from __future__ import annotations

import pytest

from pure_agent.harness import BudgetExceeded, StepBudget, TokenBudget
from pure_agent.model import Usage
from pure_agent.persistence import Database


@pytest.fixture
def db(tmp_path) -> Database:
    return Database(path=tmp_path / "test.db")


@pytest.mark.smoke
def test_budget_no_limits_allows_unlimited(db) -> None:
    b = TokenBudget(db)
    with b.step("s1") as sb:
        for _ in range(100):
            sb.add(Usage(prompt_tokens=100, completion_tokens=100, total_tokens=200))
    assert sb.used == 20_000


@pytest.mark.smoke
def test_budget_step_exceeded_raises(db) -> None:
    b = TokenBudget(db, per_step=500)
    with pytest.raises(BudgetExceeded, match="step"):
        with b.step("s1") as sb:
            sb.add(Usage(prompt_tokens=300, completion_tokens=300, total_tokens=600))


@pytest.mark.smoke
def test_budget_plan_exceeded_raises(db) -> None:
    b = TokenBudget(db, per_plan=1000)
    b._plan_id = "p1"
    with pytest.raises(BudgetExceeded, match="plan"):
        with b.step("s1", plan_id="p1") as sb:
            sb.add(Usage(prompt_tokens=600, completion_tokens=600, total_tokens=1200))


@pytest.mark.smoke
def test_budget_session_exceeded_raises(db) -> None:
    b = TokenBudget(db, per_session=500)
    with pytest.raises(BudgetExceeded, match="session"):
        with b.step("s1") as sb:
            sb.add(Usage(prompt_tokens=300, completion_tokens=300, total_tokens=600))


@pytest.mark.smoke
def test_budget_persists_to_db(db) -> None:
    b = TokenBudget(db, per_session=100_000)
    with b.step("s1", plan_id="p1") as sb:
        sb.add(Usage(prompt_tokens=100, completion_tokens=200, total_tokens=300), model="test-model")
    row = db.conn.execute("SELECT * FROM token_usage").fetchone()
    assert row is not None
    assert row["total_tokens"] == 300
    assert row["model"] == "test-model"
    assert row["plan_id"] == "p1"


@pytest.mark.smoke
def test_budget_totals(db) -> None:
    b = TokenBudget(db)
    # step 1 with no plan
    with b.step("s1") as sb:
        sb.add(Usage(prompt_tokens=100, completion_tokens=100, total_tokens=200))
    assert b.totals()["session"] == 200
    # step 2 with plan
    with b.step("s2", plan_id="p1") as sb:
        sb.add(Usage(prompt_tokens=200, completion_tokens=200, total_tokens=400))
    assert b.totals()["plan"] == 400
    assert b.totals()["session"] == 600  # 200 + 400


@pytest.mark.smoke
def test_budget_reset_plan(db) -> None:
    b = TokenBudget(db)
    with b.step("s1", plan_id="p1") as sb:
        sb.add(Usage(total_tokens=500))
    assert b._plan_used == 500
    b.reset_plan()
    assert b._plan_used == 0
    # session still accumulates
    assert b._session_used == 500
