"""MCP adapter: tool definitions exposed to the agent.

Tools stay thin; business logic lives in the service layer (PLAN.md D2).
"""

from mcp.server.mcpserver import MCPServer


def build_mcp_server() -> MCPServer:
    mcp = MCPServer(
        name="EquipDesk",
        instructions="Tools for handling IT equipment requests.",
    )

    @mcp.tool()
    def ping() -> str:
        """Check that the EquipDesk MCP server is reachable. Returns "pong"."""
        return "pong"

    return mcp
