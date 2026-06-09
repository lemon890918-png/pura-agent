"""Tests for ContextBuilder + ContextSwitcher (Phase 6)."""

from __future__ import annotations

import time

import pytest

from pure_agent.memory import (
    ContextBuilder,
    ContextBudget,
    ContextSwitcher,
    L1Cache,
    extract_episodic,
    extract_facts,
)


# ─── ContextBuilder ──────────────────────────────────────────────────────


@pytest.mark.smoke
def test_context_builder_empty() -> None:
    cb = ContextBuilder()
    assert cb.build() == ""


@pytest.mark.smoke
def test_context_builder_l4() -> None:
    cb = ContextBuilder(l4_getter=lambda: [{"text": "prefers concise"}])
    out = cb.build()
    assert "User Preferences" in out
    assert "prefers concise" in out


@pytest.mark.smoke
def test_context_builder_l3() -> None:
    cb = ContextBuilder(l3_getter=lambda: [{"text": "uses Python 3.12"}])
    out = cb.build()
    assert "Project Facts" in out
    assert "Python 3.12" in out


@pytest.mark.smoke
def test_context_builder_l2() -> None:
    cb = ContextBuilder(l2_getter=lambda: [{"text": "added multiply function"}])
    out = cb.build()
    assert "Session Context" in out


@pytest.mark.smoke
def test_context_builder_l1() -> None:
    cb = ContextBuilder()
    cb.l1.put("file", "utils.py")
    out = cb.build()
    assert "Recent Items" in out
    assert "utils.py" in out


@pytest.mark.smoke
def test_context_builder_token_budget_truncates() -> None:
    cb = ContextBuilder(
        budget=ContextBudget(l3=5, total_cap=10),
        l3_getter=lambda: [{"text": "x" * 1000}],
    )
    out = cb.build()
    # should be truncated
    assert len(out) < 1000


@pytest.mark.smoke
def test_context_builder_caches() -> None:
    call_count = {"n": 0}

    def getter():
        call_count["n"] += 1
        return [{"text": "fact"}]

    cb = ContextBuilder(l3_getter=getter, cache_ttl_s=10.0)
    cb.build()
    cb.build()
    cb.build()
    assert call_count["n"] == 1


@pytest.mark.smoke
def test_context_builder_invalidate() -> None:
    call_count = {"n": 0}

    def getter():
        call_count["n"] += 1
        return [{"text": "fact"}]

    cb = ContextBuilder(l3_getter=getter, cache_ttl_s=10.0)
    cb.build()
    cb.invalidate()
    cb.build()
    assert call_count["n"] == 2


# ─── ContextSwitcher ─────────────────────────────────────────────────────


@pytest.mark.smoke
def test_context_switch_basic() -> None:
    cs = ContextSwitcher(current_session_id="s1")
    cs.current_l1.put("a", 1)
    cs.switch("s2")
    assert cs.current_session_id == "s2"
    cs.current_l1.put("b", 2)
    cs.switch("s1")
    # back to s1: should have "a"
    assert cs.current_l1.get("a") == 1
    assert cs.current_l1.get("b") is None


@pytest.mark.smoke
def test_context_switch_save_and_restore() -> None:
    cs = ContextSwitcher(current_session_id="s1")
    cs.current_l1.put("a", 1)
    cs.current_l1.put("b", 2)
    cs.switch("s2")
    cs.current_l1.put("c", 3)
    assert cs.snapshot_count() == 1
    cs.switch("s1")
    # the snapshot saved was used to restore the live s1 l1
    # so s1 l1 should still have a, b
    assert cs.current_l1.get("a") == 1
    assert cs.current_l1.get("b") == 2


@pytest.mark.smoke
def test_context_switch_fresh_session() -> None:
    cs = ContextSwitcher(current_session_id="s1")
    cs.current_l1.put("a", 1)
    new_l1 = cs.switch("brand_new")
    assert cs.current_session_id == "brand_new"
    assert new_l1.get("a") is None
    assert len(new_l1) == 0


@pytest.mark.smoke
def test_context_switch_same_id_noop() -> None:
    cs = ContextSwitcher(current_session_id="s1")
    cs.current_l1.put("a", 1)
    cs.switch("s1")  # same session
    assert cs.current_l1.get("a") == 1
    assert cs.snapshot_count() == 0


@pytest.mark.smoke
def test_context_switch_history() -> None:
    cs = ContextSwitcher(current_session_id="s1")
    cs.switch("s2")
    cs.switch("s3")
    assert cs.history() == ["s1", "s2", "s3"]


@pytest.mark.smoke
def test_context_switch_evict_expired() -> None:
    cs = ContextSwitcher(current_session_id="s1", snapshot_ttl_s=0.05)
    cs.current_l1.put("a", 1)
    cs.switch("s2")
    assert cs.snapshot_count() == 1
    time.sleep(0.1)
    n = cs.evict_expired()
    assert n == 1
    assert cs.snapshot_count() == 0


# ─── fact_extractor ─────────────────────────────────────────────────────


@pytest.mark.smoke
def test_extract_facts_project_uses() -> None:
    facts = extract_facts("The project uses Python 3.12 and uv for dependency management.")
    assert any("Python" in f for f in facts)


@pytest.mark.smoke
def test_extract_facts_user_prefers() -> None:
    facts = extract_facts("The user prefers concise responses in Chinese.")
    assert any("concise" in f or "Chinese" in f for f in facts)


@pytest.mark.smoke
def test_extract_facts_empty() -> None:
    assert extract_facts("") == []
    assert extract_facts("No facts here, just chatting.") == []


@pytest.mark.smoke
def test_extract_facts_dedup() -> None:
    text = "Project uses Python 3.12. Project uses Python 3.12."
    facts = extract_facts(text)
    # both "uses" patterns should match but dedup the result
    # the result list is deduped
    seen = set()
    for f in facts:
        assert f.lower() not in seen
        seen.add(f.lower())


@pytest.mark.smoke
def test_extract_episodic_from_action_text() -> None:
    text = "Verified the function works. Added a divide function. Found no bugs."
    facts = extract_episodic(text)
    assert len(facts) >= 1
