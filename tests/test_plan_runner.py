"""Tests for plan/runner.py — execute a plan, skip done steps, fail/retry, abort."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from pure_agent.agent import AIAgentLoop
from pure_agent.model import (
    AgentRunResult,
    CanonicalMessage,
    ModelEvent,
    Role,
    StopReason,
    TextBlock,
    Usage,
)
from pure_agent.persistence import Database
from pure_agent.plan import (
    Goal,
    Plan,
    PlanRunner,
    PlanStatus,
    PlanStep,
    PlanStorage,
    StepKind,
    StepStatus,
    StepReport,
)


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ─── scripted loop factory ─────────────────────────────────────────────────


def make_loop_factory(scripts: list[dict]):
    """Build a factory that returns AIAgentLoop instances with scripted behavior.

    Each script item is a dict: { "text": "...", "verdict": "pass"|... }
    The factory pops items off the list in order.
    """
    queue = list(scripts)
    # also a pool for the same step (re-runs)
    counter = {"i": 0}

    def factory(*, system_prompt: str = ""):
        i = counter["i"]
        counter["i"] += 1
        if i >= len(queue):
            script = {"text": "ok", "verdict": "pass"}
        else:
            script = queue[i]
        return ScriptedLoop(script)

    return factory


class ScriptedLoop(AIAgentLoop):
    """AIAgentLoop that returns a fixed AgentRunResult without touching LLM/tools."""

    def __init__(self, script: dict) -> None:
        # skip super().__init__
        self.script = script
        self.system_prompt = ""

    async def run(
        self,
        user_message: str,
        *,
        max_turns: int | None = None,
        abort_signal=None,
    ):
        # build a fake assistant message
        text = self.script.get("text", "")
        msg = CanonicalMessage(role=Role.ASSISTANT, content=[TextBlock(text=text)])
        # maybe include a StepReport JSON at the end
        if "verdict" in self.script:
            report_json = json.dumps(
                {
                    "verdict": self.script["verdict"],
                    "summary": self.script.get("summary", "done"),
                    "files_changed": self.script.get("files_changed", []),
                }
            )
            msg.content.append(TextBlock(text=f"\n```json\n{report_json}\n```"))
        return AgentRunResult(
            final_text=text,
            turns=1,
            total_usage=Usage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
            stopped_reason=StopReason.COMPLETED,
            messages=[msg],
        )


# ─── fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def db(tmp_path) -> Database:
    db = Database(path=tmp_path / "test.db")
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    for pid in ("p1", "p2", "default"):
        db.conn.execute(
            "INSERT OR IGNORE INTO projects (id, name, hash, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (pid, pid, pid, now, now),
        )
    return db


@pytest.fixture
def storage(db) -> PlanStorage:
    return PlanStorage(db)


def make_goal_and_plan(
    storage: PlanStorage,
    step_specs: list[dict],
) -> tuple[Goal, Plan]:
    g = storage.create_goal(project_id="p1", text="x")
    p = Plan(goal_id=g.id, steps=[])
    for spec in step_specs:
        s = PlanStep(
            id=spec["id"],
            plan_id=p.id,
            idx=spec.get("idx", 0),
            kind=StepKind(spec.get("kind", "code")),
            action=spec.get("action", f"action {spec['id']}"),
            deps=spec.get("deps", []),
        )
        p.steps.append(s)
    storage.create_plan(p)
    return g, p


# ─── tests ────────────────────────────────────────────────────────────────


@pytest.mark.smoke
def test_run_simple_plan_all_pass(storage) -> None:
    _, p = make_goal_and_plan(
        storage,
        [
            {"id": "a", "idx": 0, "action": "first"},
            {"id": "b", "idx": 1, "deps": ["a"], "action": "second"},
        ],
    )
    factory = make_loop_factory(
        [
            {"text": "step a done", "verdict": "pass"},
            {"text": "step b done", "verdict": "pass"},
        ]
    )
    runner = PlanRunner(storage, factory)
    result = run(runner.execute(p.id))

    assert result.ok
    assert result.steps_completed == 2
    assert result.steps_failed == 0
    # verify persistence
    final = storage.get_plan(p.id)
    assert final.status == PlanStatus.DONE
    assert all(s.status == StepStatus.DONE for s in final.steps)


@pytest.mark.smoke
def test_run_resumes_skips_done(storage) -> None:
    """If a step is already DONE in DB, runner skips it."""
    _, p = make_goal_and_plan(
        storage,
        [
            {"id": "a", "idx": 0, "action": "first"},
            {"id": "b", "idx": 1, "deps": ["a"], "action": "second"},
        ],
    )
    # manually mark 'a' as done
    a = next(s for s in storage.get_plan(p.id).steps if s.id == "a")
    a.status = StepStatus.DONE
    a.step_report = StepReport(verdict="pass", summary="pre-done")
    storage.upsert_step(a)

    # only 1 script needed (b)
    factory = make_loop_factory(
        [
            {"text": "b done", "verdict": "pass"},
        ]
    )
    runner = PlanRunner(storage, factory)
    result = run(runner.execute(p.id))
    assert result.ok
    assert result.steps_completed == 2  # 1 pre-done + 1 freshly done


@pytest.mark.smoke
def test_run_blocks_step_with_unmet_deps(storage) -> None:
    _, p = make_goal_and_plan(
        storage,
        [
            {"id": "a", "idx": 0, "action": "first"},
            {"id": "b", "idx": 1, "deps": ["a"], "action": "second"},
        ],
    )
    # mark a as PENDING (so it would run, but make the script return a
    # non-pass verdict) — actually for this test we want a not done so b is blocked.
    # Force a to FAILED state by running first with a fail script
    factory = make_loop_factory(
        [
            {"text": "a failed", "verdict": "fail"},
        ]
    )
    runner = PlanRunner(storage, factory)
    result = run(runner.execute(p.id, max_total_turns=10))
    # a fails and b is blocked
    assert not result.ok
    final = storage.get_plan(p.id)
    assert any(s.id == "b" and s.status == StepStatus.BLOCKED for s in final.steps)


@pytest.mark.smoke
def test_run_abort_signal(storage) -> None:
    _, p = make_goal_and_plan(
        storage,
        [
            {"id": "a", "idx": 0, "action": "first"},
        ],
    )
    factory = make_loop_factory([{"text": "a done", "verdict": "pass"}])
    runner = PlanRunner(storage, factory)
    ev = asyncio.Event()
    ev.set()  # pre-abort
    result = run(runner.execute(p.id, abort_signal=ev))
    assert result.final_status == PlanStatus.ABANDONED


@pytest.mark.smoke
def test_run_emits_events(storage) -> None:
    _, p = make_goal_and_plan(
        storage,
        [
            {"id": "a", "idx": 0, "action": "x"},
        ],
    )
    events: list[tuple[str, dict]] = []

    def cb(t: str, payload: dict) -> None:
        events.append((t, payload))

    factory = make_loop_factory([{"text": "x done", "verdict": "pass"}])
    runner = PlanRunner(storage, factory, on_event=cb)
    run(runner.execute(p.id))
    types = [e[0] for e in events]
    assert "step_start" in types
    assert "step_end" in types


@pytest.mark.smoke
def test_run_unparseable_response_marks_failed(storage) -> None:
    _, p = make_goal_and_plan(
        storage,
        [{"id": "a", "idx": 0, "action": "x"}],
    )
    # no verdict, no report → parse fails
    factory = make_loop_factory([{"text": "no report here"}])
    runner = PlanRunner(storage, factory)
    result = run(runner.execute(p.id, max_total_turns=10))
    final = storage.get_plan(p.id)
    a = next(s for s in final.steps if s.id == "a")
    assert a.status == StepStatus.FAILED
    assert "parseable" in (a.last_error or "")


@pytest.mark.smoke
def test_run_max_total_turns_terminates(storage) -> None:
    """PlanRunner with max_total_turns stops when budget exhausted."""
    # 4 steps each returning pass with 1 turn; set max_total_turns=2
    _, p = make_goal_and_plan(
        storage,
        [
            {"id": "a", "idx": 0, "action": "x"},
            {"id": "b", "idx": 1, "action": "y"},
            {"id": "c", "idx": 2, "action": "z"},
            {"id": "d", "idx": 3, "action": "w"},
        ],
    )
    factory = make_loop_factory(
        [{"text": f"x", "verdict": "pass"} for _ in range(10)]
    )
    runner = PlanRunner(storage, factory)
    result = run(runner.execute(p.id, max_total_turns=2))
    assert not result.ok
    assert "max_total_turns" in (result.error or "")


@pytest.mark.smoke
def test_run_failure_after_max_attempts_aborts_plan(storage) -> None:
    """Step that fails marks step as FAILED with attempts=1, plan returns FAILED on first fail.

    Note: retry is at the *plan* level (resume re-runs failed steps), not within
    a single execute() call. The runner surfaces failure immediately and persists
    step state for resume.
    """
    _, p = make_goal_and_plan(
        storage,
        [
            {"id": "a", "idx": 0, "action": "x", "kind": "code"},
        ],
    )
    factory = make_loop_factory(
        [{"text": "bad", "verdict": "fail", "summary": "nope"}] * 5
    )
    runner = PlanRunner(storage, factory)
    result = run(runner.execute(p.id, max_total_turns=20))
    assert not result.ok
    # attempts should be 1 (one run inside this execute)
    final = storage.get_plan(p.id)
    a = next(s for s in final.steps if s.id == "a")
    assert a.attempts == 1
    assert a.status == StepStatus.FAILED
    # step is persisted so user can resume
    assert final.status == PlanStatus.FAILED


@pytest.mark.smoke
def test_run_not_found(storage) -> None:
    factory = make_loop_factory([])
    runner = PlanRunner(storage, factory)
    result = run(runner.execute("missing"))
    assert not result.ok
    assert "not found" in (result.error or "")


@pytest.mark.smoke
def test_run_skipped_step(storage) -> None:
    _, p = make_goal_and_plan(
        storage,
        [
            {"id": "a", "idx": 0, "action": "x"},
            {"id": "b", "idx": 1, "deps": ["a"], "action": "y"},
        ],
    )
    # mark b as skipped
    p_state = storage.get_plan(p.id)
    b = next(s for s in p_state.steps if s.id == "b")
    b.status = StepStatus.SKIPPED
    storage.upsert_step(b)

    factory = make_loop_factory([{"text": "a done", "verdict": "pass"}])
    runner = PlanRunner(storage, factory)
    result = run(runner.execute(p.id))
    # a done, b skipped → plan complete
    assert result.ok


@pytest.mark.smoke
def test_system_prompt_contains_prior_reports(storage) -> None:
    """When step b runs, system prompt should reference a's report."""
    _, p = make_goal_and_plan(
        storage,
        [
            {"id": "a", "idx": 0, "action": "first"},
            {"id": "b", "idx": 1, "deps": ["a"], "action": "second"},
        ],
    )

    captured: list[str] = []

    def factory(*, system_prompt: str = ""):
        captured.append(system_prompt)
        return ScriptedLoop(
            {"text": f"step done", "verdict": "pass", "summary": "prior report text"}
        )

    runner = PlanRunner(storage, factory)
    run(runner.execute(p.id))
    # second call (for step b) should mention "prior report text"
    assert any("prior report text" in s for s in captured)
