"""MCP (Model Context Protocol) client: spawn MCP servers via stdio JSON-RPC.

Phase 10. Implements the minimum subset of MCP needed to discover and call tools:
  - initialize
  - tools/list
  - tools/call
Each MCP tool is wrapped as a pure_agent.tools.Tool so it slots into the
existing ToolRegistry.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from pure_agent.tools.base import Tool, ToolResult


# ─── protocol message types ─────────────────────────────────────────


@dataclass
class MCPMessage:
    """A JSON-RPC 2.0 message."""

    jsonrpc: str = "2.0"
    id: str | int | None = None
    method: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"jsonrpc": self.jsonrpc}
        if self.id is not None:
            d["id"] = self.id
        if self.method is not None:
            d["method"] = self.method
        if self.params:
            d["params"] = self.params
        if self.result is not None:
            d["result"] = self.result
        if self.error is not None:
            d["error"] = self.error
        return d


@dataclass
class MCPServerConfig:
    """Configuration for an MCP server."""

    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    startup_timeout_s: float = 10.0


class MCPServer:
    """Spawned MCP server process with stdio JSON-RPC."""

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self._proc: asyncio.subprocess.Process | None = None
        self._next_id = 1
        self._tools: list[dict[str, Any]] = []
        self._server_info: dict[str, Any] = {}

    async def start(self) -> None:
        """Start the subprocess and initialize the protocol."""
        if not shutil.which(self.config.command.split()[0]):
            raise FileNotFoundError(
                f"MCP server command not found: {self.config.command!r}"
            )
        env = os.environ.copy()
        env.update(self.config.env)
        cwd = self.config.cwd or os.getcwd()

        self._proc = await asyncio.create_subprocess_exec(
            self.config.command,
            *self.config.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=cwd,
        )
        # initialize
        init_result = await self._request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "pure-agent", "version": "0.1.0"},
            },
        )
        self._server_info = init_result.get("serverInfo", {})
        # send initialized notification
        await self._send_notification("notifications/initialized", {})
        # list tools
        tools_result = await self._request("tools/list", {})
        self._tools = tools_result.get("tools", [])

    async def stop(self) -> None:
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=2.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    self._proc.kill()
                except ProcessLookupError:
                    pass
        self._proc = None

    @property
    def tools(self) -> list[dict[str, Any]]:
        return list(self._tools)

    @property
    def server_info(self) -> dict[str, Any]:
        return dict(self._server_info)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return await self._request("tools/call", {"name": name, "arguments": arguments})

    # ─── JSON-RPC plumbing ───────────────────────────────────────────

    def _new_id(self) -> int:
        i = self._next_id
        self._next_id += 1
        return i

    async def _send(self, msg: MCPMessage) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("MCP server not started")
        data = json.dumps(msg.to_dict()) + "\n"
        self._proc.stdin.write(data.encode("utf-8"))
        await self._proc.stdin.drain()

    async def _read_one(self) -> dict[str, Any]:
        if self._proc is None or self._proc.stdout is None:
            raise RuntimeError("MCP server not started")
        line = await self._proc.stdout.readline()
        if not line:
            stderr = ""
            if self._proc.stderr is not None:
                try:
                    stderr_bytes = await asyncio.wait_for(
                        self._proc.stderr.read(), timeout=0.5
                    )
                    stderr = stderr_bytes.decode("utf-8", errors="replace")
                except asyncio.TimeoutError:
                    pass
            raise RuntimeError(
                f"MCP server closed unexpectedly. stderr: {stderr[-500:]}"
            )
        return json.loads(line.decode("utf-8"))

    async def _request(
        self, method: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        rid = self._new_id()
        await self._send(MCPMessage(id=rid, method=method, params=params))
        # loop: skip notifications (no id)
        while True:
            raw = await asyncio.wait_for(
                self._read_one(),
                timeout=self.config.startup_timeout_s,
            )
            if raw.get("id") == rid:
                if raw.get("error"):
                    raise RuntimeError(
                        f"MCP error: {raw['error'].get('message', raw['error'])}"
                    )
                return raw.get("result", {})
            # else: notification, ignore

    async def _send_notification(
        self, method: str, params: dict[str, Any]
    ) -> None:
        await self._send(MCPMessage(method=method, params=params))


# ─── Pure-agent Tool adapter ──────────────────────────────────────────


class MCPToolAdapter(Tool):
    """Wraps an MCP tool as a pure_agent Tool."""

    def __init__(self, server: MCPServer, tool_def: dict[str, Any]) -> None:
        self._server = server
        self._tool_def = tool_def
        # Build a pydantic model from the tool's inputSchema
        self._input_schema = tool_def.get("inputSchema", {"type": "object", "properties": {}})
        self._ParamsModel = self._build_params_model(
            f"{server.config.name}_{tool_def['name']}_Params", self._input_schema
        )
        # required by Tool ABC
        self.name = f"mcp_{server.config.name}_{tool_def['name']}"
        self.description = tool_def.get("description", "")
        self.parameters = self._input_schema
        self.parameters_model = self._ParamsModel

    @staticmethod
    def _build_params_model(name: str, schema: dict[str, Any]) -> type[BaseModel]:
        """Build a pydantic model from JSON Schema.

        For Phase 10 we use a permissive approach: all properties are optional
        with their declared type or string fallback.
        """
        props = schema.get("properties", {})
        required = set(schema.get("required", []))
        type_map: dict[str, type] = {
            "string": str,
            "integer": int,
            "number": float,
            "boolean": bool,
            "array": list,
            "object": dict,
        }
        fields: dict[str, Any] = {}
        for pname, pschema in props.items():
            ptype = type_map.get(pschema.get("type", "string"), str)
            default = ... if pname in required else None
            fields[pname] = (ptype | None, default)
        if not fields:
            return BaseModel  # type: ignore[return-value]
        return pydantic_create_model(name, **fields)  # type: ignore[arg-type]

    async def execute(self, **kwargs: Any) -> ToolResult:
        try:
            result = await self._server.call_tool(
                self._tool_def["name"], kwargs
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult.fail(str(e), code="mcp_error")
        content = result.get("content", [])
        # render content to text
        parts: list[str] = []
        for c in content:
            if c.get("type") == "text":
                parts.append(c.get("text", ""))
            elif c.get("type") == "resource":
                parts.append(f"[resource: {c.get('uri')}]")
            else:
                parts.append(json.dumps(c))
        text = "\n".join(parts)
        if result.get("isError"):
            return ToolResult.fail(text, code="mcp_tool_error")
        return ToolResult.ok_data({"text": text, "raw": result})


# Need this import after the class def so the pydantic_create_model helper
# is available. Imported at runtime to avoid circular import issues.
from pydantic import create_model as pydantic_create_model  # noqa: E402


# ─── config loading ───────────────────────────────────────────────────


def load_mcp_config(path: Path) -> list[MCPServerConfig]:
    """Load MCP server configs from mcp.json.

    Format (matches Claude Desktop's mcp.json):
    {
      "mcpServers": {
        "name": {
          "command": "...",
          "args": [...],
          "env": {...}
        }
      }
    }
    """
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    servers: list[MCPServerConfig] = []
    for name, cfg in data.get("mcpServers", {}).items():
        servers.append(
            MCPServerConfig(
                name=name,
                command=cfg.get("command", ""),
                args=cfg.get("args", []),
                env=cfg.get("env", {}),
                cwd=cfg.get("cwd"),
            )
        )
    return servers


__all__ = [
    "MCPMessage",
    "MCPServer",
    "MCPServerConfig",
    "MCPToolAdapter",
    "load_mcp_config",
]
