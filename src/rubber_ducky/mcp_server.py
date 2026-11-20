"""Unified MCP server exposing code, plan, and ADR review toolsets.

Registers all review-mode tools on one process.
"""

from mcp.server.mcpserver import MCPServer

from rubber_ducky.adr import mcp_server as adr_tools
from rubber_ducky.code import mcp_server as code_tools
from rubber_ducky.plan import mcp_server as plan_tools

server = MCPServer(
    name="rubber-ducky",
    description="Durable, protocol-validated code, plan, and ADR review",
)
code_tools.register_tools(server)
plan_tools.register_tools(server)
adr_tools.register_tools(server)


def run() -> None:
    server.run(transport="stdio")
