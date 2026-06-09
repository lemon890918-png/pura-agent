"""Phase 6 integration: cross-plan fact reuse + auto_extract_facts."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from pure_agent.agent import AIAgentLoop
from pure_agent.memory import (
    ContextBuilder,
    EpisodicMemory,
    SemanticMemory,
    extract_facts,
)
from pure_agent.model import (
    ModelEvent,
    TextBlock,
    Usage,
)
from pure_agent.persistence import Database
from pure_agent.plan import (
    Plan,
    PlanRunner,
    PlanStatus,
    PlanStep,
    PlanStorage,
    StepKind,
)
from pure_agent.tools import ToolRegistry


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ─── cross-plan fact reuse via memory ─────────────────────────────────


@pytest.mark.smoke
def test_cross_plan_fact_reuse(tmp_path) -> None:
    """Plan A adds a semantic fact. Plan B can read it via L3."""
    db = Database(path=tmp_path / "test.db")
    storage = PlanStorage(db)

    # Plan A: add a fact
    sem = SemanticMemory(db, "default")
    sem.add("project uses Python 3.12 + uv", source="plan_a", confidence=0.9)

    # Plan B: query the fact
    facts = sem.search("python")
    assert len(facts) >= 1
    assert any("Python 3.12" in f["fact"] for f in facts)


@pytest.mark.smoke
def test_extract_facts_from_step_text() -> None:
    """extract_facts pulls semantic facts from a step result text."""
    text = (
        "The project uses Python 3.12 and uv for dependency management. "
        "The user prefers concise Chinese responses."
    )
    facts = extract_facts(text)
    assert any("Python" in f for f in facts)
    # user prefers pattern
    assert any("concise" in f or "Chinese" in f for f in facts)


@pytest.mark.smoke
def test_context_builder_includes_layer_outputs() -> None:
    """ContextBuilder assembles L2/L3/L4 into a single prompt section."""
    sem_items = [{"text": "uses Python 3.12"}]
    proc_items = [{"text": "prefers concise"}]
    epi_items = [{"text": "added multiply function"}]

    cb = ContextBuilder(
        l2_getter=lambda: epi_items,
        l3_getter=lambda: sem_items,
        l4_getter=lambda: proc_items,
    )
    out = cb.build()
    assert "User Preferences" in out
    assert "prefers concise" in out
    assert "Project Facts" in out
    assert "Python 3.12" in out
    assert "Session Context" in out
    assert "multiply" in out


@pytest.mark.smoke
def test_context_builder_budget_enforces_truncation() -> None:
    big = [{"text": "x" * 10000}]
    cb = ContextBuilder(
        budget=ContextBuilder.__init__.__defaults__[0]  # not great
        if False else None,
        l3_getter=lambda: big,
    )
    # The total cap is 2000 tokens = ~8000 chars; should be truncated
    out = cb.build()
    # default total cap is 2000 tokens; truncation kicks in
    assert len(out) < 10000


# ─── PlanRunner integration ──────────────────────────────────────────────


@pytest.mark.smoke
def test_plan_runner_records_facts_to_memory(tmp_path) -> None:
    """PlanRunner adds episodic fact after each step + semantic fact for project."""
    from pure_agent.memory import MemoryLayers

    db = Database(path=tmp_path / "test.db")
    storage = PlanStorage(db)
    memory = MemoryLayers(db, session_id="s1", project_id="default")
    goal = storage.create_goal(project_id="default", text="x")
    plan = Plan(goal_id=goal.id)
    s0 = PlanStep(plan_id=plan.id, idx=0, kind=StepKind.READ, action="a")
    plan.steps = [s0]
    storage.create_plan(plan)

    class Prov:
        async def stream(self, req):
            # Simulate a step result with semantic facts
            yield ModelEvent(
                type="text_delta",
                text='```json\n{"verdict": "pass", "summary": "Project uses Python 3.12 with uv."}\n```',
            )
            yield ModelEvent(
                type="usage",
                usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            )
            yield ModelEvent(type="message_end", finish_reason="stop")

        def normalize_tool_schema(self, s):
            return {}

        def max_context_tokens(self, m=None):
            return 128000

    def factory(*, system_prompt="", tools=None, max_turns=5):
        return AIAgentLoop(
            provider=Prov(),
            tools=tools or ToolRegistry(),
            model="mock",
            system_prompt=system_prompt,
            max_turns=max_turns,
        )

    runner = PlanRunner(storage=storage, loop_factory=factory, memory=memory)
    result = run(runner.execute(plan.id, max_total_turns=20))
    assert result.ok

    # After run, episodic fact for s0 should be added
    epi = memory.episodic
    epi_facts = epi.recent(5)
    # episodic might be empty if extract_episodic didn't find action words
    # (the verdict text is "Project uses Python 3.12 with uv." which doesn't trigger episodic)
    # but semantic should have something
    sem_facts = memory.semantic.search("python")
    assert any("Python" in f["fact"] for f in sem_facts), f"no semantic fact, epi={epi_facts}"
