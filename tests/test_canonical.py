"""Tests for canonical message model + role helpers."""

from __future__ import annotations

import json

import pytest

from pure_agent.model.canonical import (
    CanonicalMessage,
    Role,
    TextBlock,
    ToolResultBlock,
    ToolSchema,
    ToolUseBlock,
    Usage,
    safe_json_dumps,
    to_jsonable,
)


@pytest.mark.smoke
def test_from_text_basic() -> None:
    m = CanonicalMessage.from_text(Role.USER, "hi")
    assert m.role == Role.USER
    assert m.text() == "hi"
    assert m.tool_uses() == []


@pytest.mark.smoke
def test_tool_use_message() -> None:
    m = CanonicalMessage(
        role=Role.ASSISTANT,
        content=[
            TextBlock(text="thinking..."),
            ToolUseBlock(tool_call_id="c1", name="read_file", arguments={"path": "x"}),
        ],
    )
    assert m.text() == "thinking..."
    uses = m.tool_uses()
    assert len(uses) == 1
    assert uses[0].name == "read_file"


@pytest.mark.smoke
def test_tool_result_message() -> None:
    m = CanonicalMessage.from_tool_result("c1", "file contents here", is_error=False)
    assert m.role == Role.TOOL
    assert m.tool_call_id == "c1"


@pytest.mark.smoke
def test_serialization_roundtrip() -> None:
    m = CanonicalMessage(
        role=Role.ASSISTANT,
        content=[ToolUseBlock(tool_call_id="c1", name="bash", arguments={"cmd": "ls"})],
    )
    data = m.model_dump()
    json_str = json.dumps(data, ensure_ascii=False)
    m2 = CanonicalMessage.model_validate_json(json_str)
    assert m2.tool_uses()[0].name == "bash"
    assert m2.tool_uses()[0].arguments == {"cmd": "ls"}


@pytest.mark.smoke
def test_tool_schema_to_dict() -> None:
    s = ToolSchema(
        name="x",
        description="d",
        parameters={"type": "object", "properties": {"a": {"type": "string"}}},
    )
    d = s.to_dict()
    assert d["name"] == "x"
    assert "properties" in d["parameters"]


@pytest.mark.smoke
def test_to_jsonable_nested() -> None:
    class M:
        def model_dump(self):
            return {"a": 1, "b": [1, 2]}

    assert to_jsonable(M()) == {"a": 1, "b": [1, 2]}
    assert to_jsonable([M(), {"x": "y"}]) == [{"a": 1, "b": [1, 2]}, {"x": "y"}]


@pytest.mark.smoke
def test_safe_json_dumps_handles_datetime() -> None:
    from datetime import datetime

    s = safe_json_dumps({"ts": datetime(2026, 1, 1)})
    assert "2026" in s


@pytest.mark.smoke
def test_usage_default() -> None:
    u = Usage()
    assert u.prompt_tokens == 0
    assert u.total_tokens == 0
