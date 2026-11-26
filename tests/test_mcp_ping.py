"""EQP-1: a trivial one-tool MCP server, mounted at /mcp, proven with a real client.

Brief, suggested approach step 1: get a one-tool server working with a client
before writing any business logic. This also de-risks mounting the MCP app
inside FastAPI (PLAN.md D1).
"""

import pytest
from mcp import Client
from mcp.types import TextContent

pytestmark = pytest.mark.anyio


async def test_client_lists_ping_tool(live_server_url: str) -> None:
    async with Client(f"{live_server_url}/mcp") as client:
        result = await client.list_tools()

    assert "ping" in [tool.name for tool in result.tools]


async def test_client_calls_ping_tool(live_server_url: str) -> None:
    async with Client(f"{live_server_url}/mcp") as client:
        result = await client.call_tool("ping", {})

    assert result.is_error is False
    texts = [block.text for block in result.content if isinstance(block, TextContent)]
    assert texts == ["pong"]
