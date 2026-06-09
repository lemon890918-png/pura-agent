"""Tests for memory layers (episodic / semantic / procedural + facade)."""

from __future__ import annotations

import pytest

from pure_agent.memory import (
    EpisodicMemory,
    MemoryLayers,
    ProceduralMemory,
    SemanticMemory,
    ShortTermMemory,
)
from pure_agent.persistence import Database


@pytest.fixture
def db(tmp_path) -> Database:
    d = Database(path=tmp_path / "test.db")
    # pre-create default project
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    d.conn.execute(
        "INSERT OR IGNORE INTO projects (id, name, hash, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        ("default", "Default", "default", now, now),
    )
    # pre-create default session
    d.conn.execute(
        "INSERT OR IGNORE INTO sessions (id, project_id, created_at) VALUES (?, ?, ?)",
        ("s1", "default", now),
    )
    return d


# ─── L1 short-term ─────────────────────────────────────────────────────────


@pytest.mark.smoke
def test_short_term_add_and_clear() -> None:
    st = ShortTermMemory("s1")
    st.add("note", "hello")
    st.add("note", "world")
    assert len(st.items) == 2
    section = st.as_prompt_section()
    assert "hello" in section and "world" in section
    st.clear()
    assert st.items == []


# ─── L2 episodic ───────────────────────────────────────────────────────────


@pytest.mark.smoke
def test_episodic_add_and_recent(db) -> None:
    m = EpisodicMemory(db, "s1")
    m.add("user_question", {"text": "add multiply"})
    m.add("tool_call", {"name": "read_file", "path": "utils.py"})
    recent = m.recent(limit=10)
    assert len(recent) == 2
    assert recent[0]["event"] in ("user_question", "tool_call")


@pytest.mark.smoke
def test_episodic_prompt_section(db) -> None:
    m = EpisodicMemory(db, "s1")
    m.add("user_question", {"text": "add multiply function"})
    section = m.as_prompt_section()
    assert "multiply" in section


@pytest.mark.smoke
def test_episodic_purge_older_than(db) -> None:
    """`purge_older_than(days=0)` purges everything because cutoff = now > everything created."""
    m = EpisodicMemory(db, "s1")
    m.add("test", {"x": 1})
    # days=0 means cutoff == now, all items older than 0 seconds are purged
    purged = m.purge_older_than(days=0)
    assert purged == 1
    assert m.recent() == []


# ─── L3 semantic ───────────────────────────────────────────────────────────


@pytest.mark.smoke
def test_semantic_add_and_search(db) -> None:
    m = SemanticMemory(db, "default")
    m.add("project uses Python 3.12", source="auto-detect")
    m.add("build tool is uv", source="auto-detect")
    m.add("uses typer for CLI", source="user")

    results = m.search("Python")
    assert any("Python" in r["fact"] for r in results)

    results = m.search("uv")
    assert any("uv" in r["fact"] for r in results)


@pytest.mark.smoke
def test_semantic_all_facts(db) -> None:
    m = SemanticMemory(db, "default")
    m.add("fact 1")
    m.add("fact 2", confidence=0.3)
    facts = m.all_facts()
    assert len(facts) == 2
    # high confidence first
    assert facts[0]["confidence"] >= facts[1]["confidence"]


@pytest.mark.smoke
def test_semantic_prompt_section(db) -> None:
    m = SemanticMemory(db, "default")
    m.add("uses Python 3.12")
    m.add("uses uv")
    section = m.as_prompt_section()
    assert "Python 3.12" in section
    assert "uv" in section


# ─── L4 procedural ─────────────────────────────────────────────────────────


@pytest.mark.smoke
def test_procedural_add_and_list(db) -> None:
    m = ProceduralMemory(db, "user1")
    m.add("language", "prefer Chinese", weight=1.0)
    m.add("tool", "use typer not click", weight=0.8)
    prefs = m.all_prefs()
    assert len(prefs) == 2
    # higher weight first
    assert prefs[0]["weight"] >= prefs[1]["weight"]


@pytest.mark.smoke
def test_procedural_prompt_section(db) -> None:
    m = ProceduralMemory(db, "user1")
    m.add("language", "Chinese")
    section = m.as_prompt_section()
    assert "Chinese" in section


# ─── facade ────────────────────────────────────────────────────────────────


@pytest.mark.smoke
def test_memory_layers_combined_prompt(db) -> None:
    ml = MemoryLayers(db, session_id="s1", project_id="default", user_id="user1")
    ml.procedural.add("language", "Chinese")
    ml.semantic.add("uses Python 3.12")
    ml.episodic.add("user_question", {"text": "add multiply"})

    section = ml.as_prompt_sections()
    assert "Chinese" in section
    assert "Python 3.12" in section
    assert "add multiply" in section


@pytest.mark.smoke
def test_memory_layers_empty_prompt(db) -> None:
    ml = MemoryLayers(db, session_id="s1", project_id="default")
    section = ml.as_prompt_sections()
    assert section == ""
