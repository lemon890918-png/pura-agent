"""Tests for Harness (Phase 5): RetryPolicy, TimeoutPolicy, Tracer, with_retry."""

from __future__ import annotations

import asyncio

import pytest

from pure_agent.harness import (
    RetryPolicy,
    RetryStrategy,
    Span,
    TimeoutPolicy,
    Trace,
    Tracer,
    with_retry,
)


# ─── RetryPolicy ────────────────────────────────────────────────────────


@pytest.mark.smoke
def test_retry_policy_exponential_backoff() -> None:
    p = RetryPolicy(max_attempts=5, strategy=RetryStrategy.EXPONENTIAL, initial_backoff_s=1.0)
    assert p.backoff(1) == 1.0
    assert p.backoff(2) == 2.0
    assert p.backoff(3) == 4.0
    assert p.backoff(4) == 8.0
    assert p.backoff(5) == 16.0


@pytest.mark.smoke
def test_retry_policy_max_backoff_capped() -> None:
    p = RetryPolicy(max_attempts=10, strategy=RetryStrategy.EXPONENTIAL, initial_backoff_s=1.0, max_backoff_s=10.0)
    assert p.backoff(5) == 10.0  # would be 16, capped at 10


@pytest.mark.smoke
def test_retry_policy_linear_backoff() -> None:
    p = RetryPolicy(max_attempts=5, strategy=RetryStrategy.LINEAR, initial_backoff_s=2.0)
    assert p.backoff(1) == 2.0
    assert p.backoff(3) == 6.0
    assert p.backoff(5) == 10.0


@pytest.mark.smoke
def test_retry_policy_should_retry_stops_at_max() -> None:
    p = RetryPolicy(max_attempts=3)
    # After 1st failure: should we go into attempt 2? yes
    assert p.should_retry(2, "tool_error") is True
    # After 2nd failure: should we go into attempt 3? yes
    assert p.should_retry(3, "tool_error") is True
    # After 3rd failure: should we go into attempt 4? no — exceeds max
    assert p.should_retry(4, "tool_error") is False


@pytest.mark.smoke
def test_retry_policy_should_retry_filters_errors() -> None:
    p = RetryPolicy(max_attempts=5, retryable_errors=["tool_error"])
    assert p.should_retry(2, "tool_error") is True
    assert p.should_retry(2, "ValueError") is False


# ─── with_retry ─────────────────────────────────────────────────────────


@pytest.mark.smoke
def test_with_retry_succeeds_first_try() -> None:
    async def ok() -> str:
        return "yes"

    async def go() -> str:
        return await with_retry(ok, policy=RetryPolicy(max_attempts=3))

    assert asyncio.get_event_loop().run_until_complete(go()) == "yes"


@pytest.mark.smoke
def test_with_retry_succeeds_after_failures() -> None:
    calls = {"n": 0}

    async def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise ValueError("tool_error")
        return "ok"

    async def go() -> str:
        return await with_retry(
            flaky,
            policy=RetryPolicy(
                max_attempts=5,
                strategy=RetryStrategy.FIXED,
                initial_backoff_s=0.001,
            ),
        )

    assert asyncio.get_event_loop().run_until_complete(go()) == "ok"
    assert calls["n"] == 3


@pytest.mark.smoke
def test_with_retry_exhausts_attempts() -> None:
    calls = {"n": 0}

    async def always_fail() -> str:
        calls["n"] += 1
        raise ValueError("tool_error")

    async def go() -> None:
        await with_retry(
            always_fail,
            policy=RetryPolicy(
                max_attempts=3,
                strategy=RetryStrategy.FIXED,
                initial_backoff_s=0.001,
            ),
        )

    with pytest.raises(ValueError, match="tool_error"):
        asyncio.get_event_loop().run_until_complete(go())
    assert calls["n"] == 3


# ─── TimeoutPolicy ──────────────────────────────────────────────────────


@pytest.mark.smoke
def test_timeout_policy_under_limit_passes() -> None:
    p = TimeoutPolicy(per_total_s=10.0)
    p.check(elapsed=5.0)  # no raise


@pytest.mark.smoke
def test_timeout_policy_over_limit_raises() -> None:
    from pure_agent.agent import WatchdogTimeout

    p = TimeoutPolicy(per_total_s=1.0)
    with pytest.raises(WatchdogTimeout, match="harness_total"):
        p.check(elapsed=2.0)


@pytest.mark.smoke
def test_timeout_policy_no_total_limit() -> None:
    p = TimeoutPolicy(per_total_s=None)
    p.check(elapsed=999_999.0)  # no raise


# ─── Tracer + Span ──────────────────────────────────────────────────────


@pytest.mark.smoke
def test_tracer_records_span() -> None:
    t = Tracer()
    with t.span("my_op", foo="bar") as sp:
        x = 1 + 1
    assert isinstance(sp, Span)
    assert sp.trace.duration_ms >= 0
    assert len(t.all()) == 1
    assert t.all()[0].event_type == "my_op"
    assert t.all()[0].payload["foo"] == "bar"


@pytest.mark.smoke
def test_tracer_records_to_db(tmp_path) -> None:
    from pure_agent.persistence import Database

    db = Database(path=tmp_path / "test.db")
    t = Tracer(session_id="s1", db=db)
    with t.span("op1", k=1):
        pass
    rows = db.conn.execute("SELECT * FROM traces").fetchall()
    assert len(rows) == 1
    assert rows[0]["event_type"] == "op1"


@pytest.mark.smoke
def test_tracer_clear() -> None:
    t = Tracer()
    with t.span("op"):
        pass
    assert len(t.all()) == 1
    t.clear()
    assert len(t.all()) == 0


@pytest.mark.smoke
def test_tracer_parent_child() -> None:
    t = Tracer()
    with t.span("parent", p="P") as parent:
        with t.span("child", parent_id=parent.trace.id, c="C"):
            pass
    assert len(t.all()) == 2
    children = [tr for tr in t.all() if tr.parent_id == parent.trace.id]
    assert len(children) == 1
    assert children[0].event_type == "child"
