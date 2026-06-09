"""Tests for L1Cache (Phase 6)."""

from __future__ import annotations

import time

import pytest

from pure_agent.memory import L1Cache, L1Item


@pytest.mark.smoke
def test_l1_put_get() -> None:
    c = L1Cache()
    c.put("a", 1)
    assert c.get("a") == 1
    assert c.get("missing") is None


@pytest.mark.smoke
def test_l1_lru_eviction() -> None:
    c = L1Cache(max_size=3)
    c.put("a", 1)
    c.put("b", 2)
    c.put("c", 3)
    c.put("d", 4)  # should evict "a"
    assert c.get("a") is None
    assert c.get("b") == 2
    assert c.get("d") == 4
    assert len(c) == 3


@pytest.mark.smoke
def test_l1_lru_access_refreshes() -> None:
    c = L1Cache(max_size=3)
    c.put("a", 1)
    c.put("b", 2)
    c.put("c", 3)
    c.get("a")  # "a" now most recently used
    c.put("d", 4)
    assert c.get("a") == 1
    assert c.get("b") is None  # "b" was LRU


@pytest.mark.smoke
def test_l1_ttl_expires() -> None:
    c = L1Cache(default_ttl_s=0.1)
    c.put("a", 1)
    assert c.get("a") == 1
    time.sleep(0.15)
    assert c.get("a") is None


@pytest.mark.smoke
def test_l1_per_item_ttl() -> None:
    c = L1Cache()
    c.put("a", 1, ttl_s=0.05)
    c.put("b", 2, ttl_s=10.0)
    time.sleep(0.1)
    assert c.get("a") is None
    assert c.get("b") == 2


@pytest.mark.smoke
def test_l1_importance_and_hits() -> None:
    c = L1Cache()
    c.put("a", 1, importance=0.9)
    c.put("b", 2, importance=0.1)
    c.get("b")  # hits=1
    c.get("b")  # hits=2
    top = c.top_by_hits(1)
    assert top[0].key == "b"
    top_imp = c.top_by_importance(1)
    assert top_imp[0].key == "a"


@pytest.mark.smoke
def test_l1_clear() -> None:
    c = L1Cache()
    c.put("a", 1)
    c.put("b", 2)
    n = c.clear()
    assert n == 2
    assert len(c) == 0


@pytest.mark.smoke
def test_l1_snapshot_restore() -> None:
    c1 = L1Cache()
    c1.put("a", 1)
    c1.put("b", 2)
    snap = c1.snapshot()
    c2 = L1Cache()
    c2.restore(snap)
    assert c2.get("a") == 1
    assert c2.get("b") == 2
