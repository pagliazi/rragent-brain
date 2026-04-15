"""
MCP Server — expose RRAgent tools via Model Context Protocol.

Allows Claude Desktop, Cursor, and other MCP clients to use RRAgent tools.
Runs as a stdio MCP server launched by the host application.

Usage:
    python -m rragent.tools.mcp.server --backend pyagent
    python -m rragent.tools.mcp.server --backend hermes
    python -m rragent.tools.mcp.server --backend all
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any

logger = logging.getLogger("rragent.tools.mcp.server")


class RRClawMCPServer:
    """
    MCP Server exposing RRAgent tools.

    Two modes:
    1. PyAgent backend: exposes market/dev/backtest tools via Redis
    2. Hermes backend: exposes Hermes agent tools
    3. All: exposes both

    Tool schemas are built from the ToolRegistry at startup.
    Tool calls are routed to the appropriate backend.
    """

    def __init__(self, backend: str = "pyagent"):
        self.backend = backend
        self._tools: dict[str, dict] = {}
        self._pyagent: Any = None
        self._hermes: Any = None

    async def initialize(self):
        """Initialize backends and build tool catalog."""
        if self.backend in ("pyagent", "all"):
            await self._init_pyagent()

        if self.backend in ("hermes", "all"):
            self._init_hermes()

        logger.info(f"MCP server initialized with {len(self._tools)} tools")

    async def _init_pyagent(self):
        """Initialize PyAgent backend."""
        import os
        from rragent.tools.pyagent.bridge import PyAgentBridge, PYAGENT_COMMANDS

        redis_url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
        self._pyagent = PyAgentBridge(redis_url=redis_url)
        await self._pyagent.connect()

        for cmd in PYAGENT_COMMANDS:
            tool_name = f"pyagent_{cmd['command']}"
            self._tools[tool_name] = {
                "name": tool_name,
                "description": cmd["description"],
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "args": {
                            "type": "string",
                            "description": "Command arguments",
                        },
                    },
                },
                "agent": cmd["agent"],
                "command": cmd["command"],
                "backend": "pyagent",
            }

    def _init_hermes(self):
        """Initialize Hermes backend."""
        import os
        from rragent.tools.hermes.runtime import HermesNativeRuntime

        hermes_path = os.environ.get("HERMES_AGENT_PATH", "/opt/hermes-agent")
        self._hermes = HermesNativeRuntime(hermes_path=hermes_path)

        if self._hermes.available:
            for tool in self._hermes.list_tools():
                tool_name = f"hermes_{tool['name']}"
                self._tools[tool_name] = {
                    "name": tool_name,
                    "description": tool["description"],
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "args": {
                                "type": "string",
                                "description": "Tool arguments",
                            },
                        },
                    },
                    "backend": "hermes",
                }

    async def handle_request(self, request: dict) -> dict:
        """Handle a single MCP JSON-RPC request."""
        method = request.get("method", "")
        req_id = request.get("id")
        params = request.get("params", {})

        if method == "initialize":
            return self._response(req_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {"listChanged": False},
                },
                "serverInfo": {
                    "name": "rragent",
                    "version": "0.1.0",
                },
            })

        elif method == "tools/list":
            tools = [
                {
                    "name": t["name"],
                    "description": t["description"],
                    "inputSchema": t["inputSchema"],
                }
                for t in self._tools.values()
            ]
            return self._response(req_id, {"tools": tools})

        elif method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            result = await self._call_tool(tool_name, arguments)
            return self._response(req_id, result)

        elif method == "notifications/initialized":
            return None  # Notification, no response needed

        else:
            return self._error(req_id, -32601, f"Method not found: {method}")

    async def _call_tool(self, tool_name: str, arguments: dict) -> dict:
        """Route a tool call to the appropriate backend."""
        tool = self._tools.get(tool_name)
        if not tool:
            return {
                "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
                "isError": True,
            }

        backend = tool.get("backend", "")
        args_str = arguments.get("args", "")

        try:
            if backend == "pyagent" and self._pyagent:
                result = await self._pyagent.call_agent(
                    agent_name=tool["agent"],
                    command=tool["command"],
                    args={"input": args_str},
                )
                return {
                    "content": [{"type": "text", "text": str(result)}],
                }

            elif backend == "hermes" and self._hermes:
                result = await self._hermes.run_task(
                    prompt=args_str,
                    toolsets=["core"],
                    max_iterations=10,
                )
                return {
                    "content": [{"type": "text", "text": result.output}],
                    "isError": not result.success,
                }

            else:
                return {
                    "content": [{"type": "text", "text": f"Backend not available: {backend}"}],
                    "isError": True,
                }
        except Exception as e:
            return {
                "content": [{"type": "text", "text": f"Error: {e}"}],
                "isError": True,
            }

    def _response(self, req_id: Any, result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    def _error(self, req_id: Any, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}

    async def run_stdio(self):
        """Run as stdio MCP server (launched by host application)."""
        await self.initialize()

        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await asyncio.get_event_loop().connect_read_pipe(lambda: protocol, sys.stdin)

        writer_transport, writer_protocol = await asyncio.get_event_loop().connect_write_pipe(
            asyncio.streams.FlowControlMixin, sys.stdout
        )
        writer = asyncio.StreamWriter(writer_transport, writer_protocol, reader, asyncio.get_event_loop())

        while True:
            try:
                line = await reader.readline()
                if not line:
                    break

                request = json.loads(line.decode())
                response = await self.handle_request(request)

                if response:
                    writer.write((json.dumps(response) + "\n").encode())
                    await writer.drain()

            except json.JSONDecodeError:
                continue
            except Exception as e:
                logger.error(f"MCP server error: {e}")
                break


async def main():
    """Entry point for MCP server."""
    import argparse
    parser = argparse.ArgumentParser(description="RRAgent MCP Server")
    parser.add_argument("--backend", default="pyagent", choices=["pyagent", "hermes", "all"])
    args = parser.parse_args()

    server = RRClawMCPServer(backend=args.backend)
    await server.run_stdio()


if __name__ == "__main__":
    asyncio.run(main())
