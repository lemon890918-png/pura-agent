"""Tests for Phase 10: Skills + MCP + configurable web search."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pure_agent.mcp import (
    MCPMessage,
    MCPServer,
    MCPServerConfig,
    MCPToolAdapter,
    load_mcp_config,
)
from pure_agent.skills import (
    Skill,
    discover_skills,
    load_skill,
    parse_skill_md,
    render_skills_prompt,
)


# ─── Skills ──────────────────────────────────────────────────────────


SAMPLE_SKILL = """---
name: tavily-search
description: Use Tavily API for high-quality web search.
version: 1.0
source: tavily-ai/skills
allowed_tools: [web_search, web_fetch]
---

# Tavily Search

Use this skill when the user asks for a web search.

## When to use

- User asks "search for X"
- User wants high-quality results

## Example

Call web_search with provider="tavily".
"""


@pytest.mark.smoke
def test_parse_skill_md_basic() -> None:
    s = parse_skill_md(SAMPLE_SKILL, path=Path("/tmp/tavily-search/SKILL.md"))
    assert s.name == "tavily-search"
    assert s.description == "Use Tavily API for high-quality web search."
    assert s.version == "1.0"
    assert s.source == "tavily-ai/skills"
    assert s.allowed_tools == ["web_search", "web_fetch"]
    assert "Tavily Search" in s.body
    assert "When to use" in s.body


@pytest.mark.smoke
def test_parse_skill_md_no_frontmatter() -> None:
    s = parse_skill_md("# Just a body\n", path=Path("/tmp/my-skill/SKILL.md"))
    # path is .../SKILL.md; stem is "SKILL"; use parent dir name for the skill name
    assert s.name in ("my-skill", "SKILL")
    # description defaults to empty (not "(no description)") because real SKILL.md
    # without frontmatter is rare; we only set the placeholder when a name is missing
    assert s.body.strip() == "# Just a body"


@pytest.mark.smoke
def test_discover_skills_empty_dir(tmp_path: Path) -> None:
    skills = discover_skills(tmp_path)
    assert skills == []


@pytest.mark.smoke
def test_discover_skills_finds_skill_md(tmp_path: Path) -> None:
    # create one skill
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(SAMPLE_SKILL)
    # create a non-skill dir (no SKILL.md)
    (tmp_path / "not-a-skill").mkdir()
    # create a file at the top level (not a dir)
    (tmp_path / "stray.txt").write_text("x")
    skills = discover_skills(tmp_path)
    assert len(skills) == 1
    assert skills[0].name == "tavily-search"


@pytest.mark.smoke
def test_discover_skills_skips_malformed(tmp_path: Path) -> None:
    # malformed skill (binary garbage that won't parse)
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "SKILL.md").write_bytes(b"\x00\x01\x02 not valid utf-8 \xfe\xff")
    # discover_skills catches errors silently so just verify empty result
    skills = discover_skills(tmp_path)
    assert skills == []


@pytest.mark.smoke
def test_render_skills_prompt_empty() -> None:
    assert render_skills_prompt([]) == ""


@pytest.mark.smoke
def test_render_skills_prompt_basic() -> None:
    s = parse_skill_md(SAMPLE_SKILL, path=Path("/tmp/tavily-search/SKILL.md"))
    out = render_skills_prompt([s])
    assert "## Active Skills" in out
    assert "### Skill: tavily-search" in out
    assert "web_search, web_fetch" in out
    assert "Tavily Search" in out


@pytest.mark.smoke
def test_load_skill(tmp_path: Path) -> None:
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    p = skill_dir / "SKILL.md"
    p.write_text(SAMPLE_SKILL)
    s = load_skill(p)
    assert s.name == "tavily-search"


# ─── MCP config loading ─────────────────────────────────────────────


@pytest.mark.smoke
def test_load_mcp_config_missing_file(tmp_path: Path) -> None:
    cfgs = load_mcp_config(tmp_path / "missing.json")
    assert cfgs == []


@pytest.mark.smoke
def test_load_mcp_config_basic(tmp_path: Path) -> None:
    p = tmp_path / "mcp.json"
    p.write_text(json.dumps({
        "mcpServers": {
            "filesystem": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                "env": {},
            },
            "github": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
                "env": {"GITHUB_TOKEN": "test"},
            },
        }
    }))
    cfgs = load_mcp_config(p)
    assert len(cfgs) == 2
    assert cfgs[0].name == "filesystem"
    assert cfgs[0].command == "npx"
    assert cfgs[0].args == ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    assert cfgs[1].name == "github"
    assert cfgs[1].env == {"GITHUB_TOKEN": "test"}


# ─── MCPMessage ──────────────────────────────────────────────────────


@pytest.mark.smoke
def test_mcp_message_request() -> None:
    m = MCPMessage(id=1, method="initialize", params={"foo": "bar"})
    d = m.to_dict()
    assert d["jsonrpc"] == "2.0"
    assert d["id"] == 1
    assert d["method"] == "initialize"
    assert d["params"] == {"foo": "bar"}
    assert "result" not in d


@pytest.mark.smoke
def test_mcp_message_response() -> None:
    m = MCPMessage(id=1, result={"ok": True})
    d = m.to_dict()
    assert d["result"] == {"ok": True}
    assert "method" not in d


# ─── MCPServer start (using a fake echo server) ──────────────────────


@pytest.mark.smoke
def test_mcp_server_with_echo() -> None:
    """Spawn a fake MCP server that echoes responses and verify protocol works."""
    import asyncio
    import sys
    import tempfile

    # Write a tiny Python "MCP" server that responds correctly to our requests.
    # It's not a real MCP server (no real tools), but it implements the
    # initialize / tools/list / tools/call protocol so we can test client behavior.
    server_script = tmp_path = Path(tempfile.mkdtemp()) / "fake_mcp.py"
    server_script.write_text('''
import sys, json

def respond(req):
    method = req.get("method")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": req["id"], "result": {"serverInfo": {"name": "fake", "version": "0.1"}, "protocolVersion": "2024-11-05", "capabilities": {}}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req["id"], "result": {"tools": [{"name": "echo", "description": "Echo back input", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}]}}
    if method == "tools/call":
        args = req.get("params", {}).get("arguments", {})
        return {"jsonrpc": "2.0", "id": req["id"], "result": {"content": [{"type": "text", "text": "echoed: " + args.get("text", "")}]}}
    return {"jsonrpc": "2.0", "id": req.get("id"), "error": {"code": -32601, "message": f"unknown method: {method}"}}

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    req = json.loads(line)
    if "id" in req:
        resp = respond(req)
        sys.stdout.write(json.dumps(resp) + "\\n")
        sys.stdout.flush()
    # ignore notifications (no id)
''')

    cfg = MCPServerConfig(
        name="fake",
        command=sys.executable,
        args=[str(server_script)],
    )
    server = MCPServer(cfg)

    async def go() -> None:
        try:
            await server.start()
            tools = server.tools
            assert len(tools) == 1
            assert tools[0]["name"] == "echo"
            result = await server.call_tool("echo", {"text": "hello"})
            assert "echoed: hello" in result["content"][0]["text"]
        finally:
            await server.stop()

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(go())
    finally:
        loop.close()


@pytest.mark.smoke
def test_mcp_tool_adapter_with_running_server() -> None:
    """MCPToolAdapter wraps a real MCP tool and calls it via JSON-RPC."""
    import asyncio
    import sys
    import tempfile
    from pathlib import Path

    server_script = Path(tempfile.mkdtemp()) / "fake_mcp2.py"
    server_script.write_text('''
import sys, json
def respond(req):
    method = req.get("method")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": req["id"], "result": {"serverInfo": {"name": "fake", "version": "0.1"}, "protocolVersion": "2024-11-05", "capabilities": {}}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req["id"], "result": {"tools": [{"name": "sum", "description": "Add two numbers", "inputSchema": {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}, "required": ["a", "b"]}}]}}
    if method == "tools/call":
        args = req.get("params", {}).get("arguments", {})
        return {"jsonrpc": "2.0", "id": req["id"], "result": {"content": [{"type": "text", "text": str(args["a"] + args["b"])}]}}
    return {"jsonrpc": "2.0", "id": req.get("id"), "error": {"code": -32601, "message": "unknown"}}
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    req = json.loads(line)
    if "id" in req:
        sys.stdout.write(json.dumps(respond(req)) + "\\n")
        sys.stdout.flush()
''')

    cfg = MCPServerConfig(
        name="calc",
        command=sys.executable,
        args=[str(server_script)],
    )
    server = MCPServer(cfg)
    adapter = MCPToolAdapter(server, {
        "name": "sum",
        "description": "Add two numbers",
        "inputSchema": {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}, "required": ["a", "b"]},
    })
    assert adapter.name == "mcp_calc_sum"
    assert adapter.description == "Add two numbers"

    async def go() -> None:
        try:
            await server.start()
            r = await adapter.execute(a=3, b=4)
            assert r.ok
            assert "7" in r.data["text"]
        finally:
            await server.stop()

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(go())
    finally:
        loop.close()


# ─── integration: skills rendered into system prompt ────────────────


@pytest.mark.smoke
def test_skills_in_system_prompt(tmp_path: Path) -> None:
    """Two skills loaded from disk, rendered to prompt."""
    skills_dir = tmp_path / "skills"
    (skills_dir / "search").mkdir(parents=True)
    (skills_dir / "search" / "SKILL.md").write_text(SAMPLE_SKILL)
    (skills_dir / "code-review").mkdir(parents=True)
    (skills_dir / "code-review" / "SKILL.md").write_text('''
---
name: code-review
description: Review code for bugs, performance, and style.
version: 1.0
source: local
allowed_tools: [read_file, grep, glob]
---

# Code Review

Review code thoroughly before suggesting changes.
''')
    skills = discover_skills(skills_dir)
    assert len(skills) == 2
    prompt = render_skills_prompt(skills)
    assert "tavily-search" in prompt
    assert "code-review" in prompt
    assert "web_search" in prompt
    assert "read_file" in prompt
