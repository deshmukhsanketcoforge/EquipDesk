"""The agent's MCP client: its only way to reach the backend (PLAN.md §2, §7).

Wraps the MCP SDK's Streamable HTTP client. It offers the model exactly the four
brief tools in the LLM tool format, and turns each call into an Observation the
agent loop, the guardrails and the trace all read.
"""

import json
from types import TracebackType
from typing import Any, Self

from anthropic.types import ToolParam, ToolResultBlockParam
from mcp import Client
from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel, ConfigDict

TOOL_NAMES = frozenset(
    {
        "get_employee_info",
        "get_policy_limits",
        "check_request_eligibility",
        "flag_for_human_review",
    }
)
"""The brief's four tools: the model's whole tool surface (D8). `ping` is a health
check and is not offered."""


class McpUnavailable(Exception):
    """The MCP server could not be reached, or does not offer the four tools."""


class Observation(BaseModel):
    """What a tool returned. Errors-as-data (e.g. EMPLOYEE_NOT_FOUND) are normal
    observations; is_error marks only calls that never produced a tool answer."""

    model_config = ConfigDict(frozen=True)

    tool: str
    arguments: dict[str, Any]
    payload: Any
    """The tool's JSON answer, or a text message for an MCP-level error."""
    is_error: bool

    def tool_result(self, tool_use_id: str) -> ToolResultBlockParam:
        """This observation as the tool_result block for the next LLM turn."""
        content = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": content,
            "is_error": self.is_error,
        }


def read_payload(result: CallToolResult) -> Any:
    """Parsed JSON text, else the raw text (MCP-level errors), else structured content."""
    texts = [block.text for block in result.content if isinstance(block, TextContent)]
    if texts:
        try:
            return json.loads(texts[0])
        except ValueError:
            return texts[0]
    if result.structured_content is not None:  # pragma: no cover - our tools always send text
        return result.structured_content.get("result", result.structured_content)
    return None  # pragma: no cover


class EquipDeskMcp:
    """An open MCP session with the EquipDesk server.

        async with EquipDeskMcp("http://api:8000/mcp") as mcp:
            tools = await mcp.list_tools()
            observation = await mcp.call_tool("get_employee_info", {"employee_id": "E1001"})

    `target` is the server's URL, or an MCPServer for in-process use.
    """

    def __init__(self, target: str | MCPServer) -> None:
        self._target = target
        self._client: Client  # created on entry, where a failure becomes McpUnavailable
        self._tools: list[ToolParam] | None = None

    async def __aenter__(self) -> Self:
        try:
            self._client = Client(self._target)
            await self._client.__aenter__()
        except Exception as exc:  # connection refused, DNS, HTTP or session failures
            raise McpUnavailable(
                f"Could not open an MCP session with {self._target}: {exc!r}"
            ) from exc
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            await self._client.__aexit__(exc_type, exc, tb)
        except Exception as error:
            # The client's task group wraps an exception raised in the `async with`
            # body; the caller gets its own exception back, which matters more than
            # anything that also failed while closing.
            if exc is not None:
                raise exc from None
            raise McpUnavailable(f"The MCP session failed while closing: {error!r}") from error

    async def list_tools(self) -> list[ToolParam]:
        """The four tools in the LLM tool format, sorted by name so every request's
        tool list is byte-identical (prompt-cache friendly)."""
        if self._tools is None:
            try:
                listed = {tool.name: tool for tool in (await self._client.list_tools()).tools}
            except Exception as exc:  # session dropped, server restarted, protocol error
                raise McpUnavailable(f"Listing the MCP tools failed: {exc!r}") from exc
            missing = sorted(TOOL_NAMES - set(listed))
            if missing:
                raise McpUnavailable(f"The MCP server does not offer: {', '.join(missing)}")
            self._tools = [
                {
                    "name": name,
                    "description": listed[name].description or "",
                    "input_schema": listed[name].input_schema,
                }
                for name in sorted(TOOL_NAMES)
            ]
        return self._tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Observation:
        """Call one of the four tools. A name the model was not offered is never sent
        to the server; the observation names the valid tools so the model can retry."""
        if name not in TOOL_NAMES:
            available = ", ".join(sorted(TOOL_NAMES))
            return Observation(
                tool=name,
                arguments=arguments,
                payload=f"Unknown tool: {name}. Available tools: {available}",
                is_error=True,
            )
        try:
            result = await self._client.call_tool(name, arguments)
        except Exception as exc:  # session dropped, server restarted, protocol error
            raise McpUnavailable(f"Calling {name} over MCP failed: {exc!r}") from exc
        return Observation(
            tool=name, arguments=arguments, payload=read_payload(result), is_error=result.is_error
        )
