"""Tests for Checkpointer (Phase 4)."""

from __future__ import annotations

import pytest

from pure_agent.agent import Checkpointer
from pure_agent.model import CanonicalMessage, Role, TextBlock
from pure_agent.persistence import Database


@pytest.fixture
def db(tmp_path) -> Database:
    d = Database(path=tmp_path / "test.db")
    import datetime

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    d.conn.execute(
        "INSERT OR IGNORE INTO projects (id, name, hash, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("default", "Default", "default", now, now),
    )
    d.conn.execute(
        "INSERT OR IGNORE INTO sessions (id, project_id, name, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("s1", "default", "s1", now, now),
    )
    return d


def _msgs() -> list[CanonicalMessage]:
    return [
        CanonicalMessage.from_text(Role.SYSTEM, "sys"),
        CanonicalMessage.from_text(Role.USER, "hi"),
        CanonicalMessage.from_text(Role.ASSISTANT, "hello back"),
    ]


@pytest.mark.smoke
def test_save_and_load_latest(db) -> None:
    cp = Checkpointer(db, "s1")
    cid = cp.save(_msgs(), turn_id="t1", metadata={"turn": 1})
    assert cid.startswith("ckpt_")

    out = cp.load_latest()
    assert out is not None
    msgs, meta = out
    assert len(msgs) == 3
    assert msgs[0].text() == "sys"
    assert msgs[1].text() == "hi"
    assert msgs[2].text() == "hello back"
    assert meta == {"turn": 1}


@pytest.mark.smoke
def test_load_latest_empty(db) -> None:
    cp = Checkpointer(db, "s1")
    assert cp.load_latest() is None


@pytest.mark.smoke
def test_list_checkpoints(db) -> None:
    cp = Checkpointer(db, "s1")
    cp.save(_msgs(), turn_id="t1")
    cp.save(_msgs(), turn_id="t2")
    cp.save(_msgs(), turn_id="t3")
    out = cp.list_checkpoints()
    assert len(out) == 3
    assert {c["turn_id"] for c in out} == {"t1", "t2", "t3"}


@pytest.mark.smoke
def test_load_latest_returns_most_recent(db) -> None:
    cp = Checkpointer(db, "s1")
    cp.save(_msgs(), turn_id="t1")
    cp.save(
        [
            CanonicalMessage.from_text(Role.USER, "second message"),
        ],
        turn_id="t2",
    )
    msgs, _ = cp.load_latest()
    assert len(msgs) == 1
    assert msgs[0].text() == "second message"


@pytest.mark.smoke
def test_clean(db) -> None:
    cp = Checkpointer(db, "s1")
    cp.save(_msgs(), turn_id="t1")
    cp.save(_msgs(), turn_id="t2")
    deleted = cp.clean()
    assert deleted == 2
    assert cp.load_latest() is None


@pytest.mark.smoke
def test_messages_round_trip() -> None:
    """messages_to_json + messages_from_json round-trip preserves content."""
    from pure_agent.agent import messages_from_json, messages_to_json

    msgs = _msgs()
    text = messages_to_json(msgs)
    out = messages_from_json(text)
    assert len(out) == 3
    assert out[0].role == Role.SYSTEM
    assert out[0].text() == "sys"
    assert out[2].text() == "hello back"
