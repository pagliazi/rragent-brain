"""
MCP Client — connect to external MCP servers for tool discovery.

Connects to third-party MCP servers (ClawHub, community tools)
and makes their tools available as Tier 2 (on-demand) tools.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("rragent.tools.mcp.client")


@dataclass
class MCPServerConfig:
    """Configuration for an external MCP server."""

    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class MCPTool:
    """A tool discovered from an MCP server."""

    name: str
    description: str
    input_schema: dict
    server_name: str


class MCPClient:
    """
    Connect to external MCP servers and discover their tools.

    Manages stdio subprocesses for each configured MCP server.
    Tools are exposed as Tier 2 (discoverable via tool_search).
    """

    def __init__(self):
        self._servers: dict[str, MCPServerConfig] = {}
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._readers: dict[str, asyncio.StreamReader] = {}
        self._writers: dict[str, asyncio.StreamWriter] = {}
        self._tools: dict[str, MCPTool] = {}
        self._request_id = 0

    def add_server(self, config: MCPServerConfig):
        """Register an MCP server configuration."""
        self._servers[config.name] = config

    async def connect_all(self):
        """Connect to all configured MCP servers."""
        for name, config in self._servers.items():
            try:
                await self._connect(name, config)
            except Exception as e:
                logger.warning(f"Failed to connect MCP server {name}: {e}")

    async def _connect(self, name: str, config: MCPServerConfig):
        """Connect to a single MCP server."""
        env = {**dict(__import__("os").environ), **config.env}

        process = await asyncio.create_subprocess_exec(
            config.command, *config.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        self._processes[name] = process

        # Initialize
        response = await self._send_request(name, "initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "rragent", "version": "0.1.0"},
        })

        if response:
            # Send initialized notification
            await self._send_notification(name, "notifications/initialized", {})

            # List tools
            tools_response = await self._send_request(name, "tools/list", {})
            if tools_response and "tools" in tools_response.get("result", {}):
                for tool_data in tools_response["result"]["tools"]:
                    tool = MCPTool(
                        name=f"mcp_{name}_{tool_data['name']}",
                        description=tool_data.get("description", ""),
                        input_schema=tool_data.get("inputSchema", {}),
                        server_name=name,
                    )
                    self._tools[tool.name] = tool

                logger.info(
                    f"MCP server {name}: {len(tools_response['result']['tools'])} tools discovered"
                )

    async def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """Call a tool on its MCP server."""
        tool = self._tools.get(tool_name)
        if not tool:
            return {"error": f"Unknown MCP tool: {tool_name}"}

        # Strip the mcp_{server}_ prefix to get original name
        original_name = tool_name
        prefix = f"mcp_{tool.server_name}_"
        if tool_name.startswith(prefix):
            original_name = tool_name[len(prefix):]

        response = await self._send_request(tool.server_name, "tools/call", {
            "name": original_name,
            "arguments": arguments,
        })

        if response and "result" in response:
            return response["result"]
        elif response and "error" in response:
            return {"error": response["error"]}
        return {"error": "No response from MCP server"}

    async def _send_request(self, server: str, method: str, params: dict) -> dict | None:
        """Send a JSON-RPC request to an MCP server."""
        process = self._processes.get(server)
        if not process or not process.stdin or not process.stdout:
            return None

        self._request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params,
        }

        try:
            process.stdin.write((json.dumps(request) + "\n").encode())
            await process.stdin.drain()

            line = await asyncio.wait_for(
                process.stdout.readline(),
                timeout=30,
            )
            if line:
                return json.loads(line.decode())
        except Exception as e:
            logger.warning(f"MCP request to {server} failed: {e}")

        return None

    async def _send_notification(self, server: str, method: str, params: dict):
        """Send a JSON-RPC notification (no response expected)."""
        process = self._processes.get(server)
        if not process or not process.stdin:
            return

        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }

        try:
            process.stdin.write((json.dumps(notification) + "\n").encode())
            await process.stdin.drain()
        except Exception:
            pass

    @property
    def discovered_tools(self) -> dict[str, MCPTool]:
        return dict(self._tools)

    async def disconnect_all(self):
        """Disconnect all MCP servers."""
        for name, process in self._processes.items():
            try:
                process.terminate()
                await asyncio.wait_for(process.wait(), timeout=5)
            except Exception:
                process.kill()

        self._processes.clear()
        logger.info("All MCP servers disconnected")
