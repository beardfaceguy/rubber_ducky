"""Unified MCP server exposing both the code and plan review toolsets.

Registers the ``rubber_ducky_code_*`` (code) and ``rubber_ducky_plan_*`` (plan) tools on a
single server so one process serves both domains.
"""

from mcp.server.mcpserver import MCPServer

from rubber_ducky.code import mcp_server as code_tools
from rubber_ducky.plan import mcp_server as plan_tools

server = MCPServer(
    name="rubber-ducky",
    description="Durable, protocol-validated agent-to-agent code and plan review",
)
code_tools.register_tools(server)
plan_tools.register_tools(server)


def run() -> None:
    server.run(transport="stdio")
