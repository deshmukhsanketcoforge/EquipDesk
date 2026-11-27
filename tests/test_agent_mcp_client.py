"""EQP-9: the agent's MCP client wrapper, against the real app over Streamable HTTP.

The agent talks only MCP (PLAN.md §2), so this wrapper is its whole view of the
backend: the tool list it offers the model and the observations it records.
"""

import json
from typing import cast

import pytest
from mcp.server.mcpserver import MCPServer

from agent.mcp_client import TOOL_NAMES, EquipDeskMcp, McpUnavailable, Observation
from server.codes import ReasonCode

BRIEF_TOOLS = [
    "check_request_eligibility",
    "flag_for_human_review",
    "get_employee_info",
    "get_policy_limits",
]


# Listing tools


@pytest.mark.anyio
async def test_lists_exactly_the_four_brief_tools_in_a_stable_order(live_server_url: str) -> None:
    async with EquipDeskMcp(f"{live_server_url}/mcp") as mcp:
        tools = await mcp.list_tools()

    # Sorted, so the tools part of every LLM request is byte-identical (cache-friendly).
    # The ping health check is not offered to the model.
    assert [tool["name"] for tool in tools] == BRIEF_TOOLS
    assert sorted(TOOL_NAMES) == BRIEF_TOOLS


@pytest.mark.anyio
async def test_tools_are_in_the_llm_tool_format(live_server_url: str) -> None:
    async with EquipDeskMcp(f"{live_server_url}/mcp") as mcp:
        tools = {tool["name"]: tool for tool in await mcp.list_tools()}

    tool = tools["check_request_eligibility"]
    assert set(tool) == {"name", "description", "input_schema"}
    assert "WITHIN_POLICY" in tool["description"]
    schema = dict(tool["input_schema"])
    assert schema["type"] == "object"
    assert set(cast(list[str], schema["required"])) == {"employee_id", "item"}


@pytest.mark.anyio
async def test_the_tool_list_is_fetched_once_per_session(live_server_url: str) -> None:
    # The same list object on every turn keeps the requests' tools identical.
    async with EquipDeskMcp(f"{live_server_url}/mcp") as mcp:
        assert await mcp.list_tools() is await mcp.list_tools()


@pytest.mark.anyio
async def test_a_missing_brief_tool_is_reported() -> None:
    # An in-process server (mcp Client accepts one directly) that lacks three tools.
    partial = MCPServer(name="partial")

    @partial.tool()
    def get_employee_info(employee_id: str) -> str:
        return employee_id

    async with EquipDeskMcp(partial) as mcp:
        with pytest.raises(McpUnavailable, match="check_request_eligibility"):
            await mcp.list_tools()


# Calling tools


@pytest.mark.anyio
async def test_call_returns_the_parsed_payload(live_server_url: str) -> None:
    async with EquipDeskMcp(f"{live_server_url}/mcp") as mcp:
        observation = await mcp.call_tool("get_employee_info", {"employee_id": "E1001"})

    assert observation == Observation(
        tool="get_employee_info",
        arguments={"employee_id": "E1001"},
        payload=observation.payload,
        is_error=False,
    )
    assert observation.payload["employee_id"] == "E1001"
    assert observation.payload["role"] == "engineer"


@pytest.mark.anyio
async def test_errors_as_data_are_observations_not_failures(live_server_url: str) -> None:
    async with EquipDeskMcp(f"{live_server_url}/mcp") as mcp:
        observation = await mcp.call_tool("get_employee_info", {"employee_id": "E9999"})

    # A tool answering "not found" is a valid answer the model must act on.
    assert observation.is_error is False
    assert observation.payload["error"]["reason_code"] == ReasonCode.EMPLOYEE_NOT_FOUND


@pytest.mark.anyio
async def test_eligibility_verdict_round_trips(live_server_url: str) -> None:
    async with EquipDeskMcp(f"{live_server_url}/mcp") as mcp:
        observation = await mcp.call_tool(
            "check_request_eligibility", {"employee_id": "E1002", "item": "monitor"}
        )

    assert observation.payload["reason_code"] == ReasonCode.COOLDOWN_NOT_ELAPSED


@pytest.mark.anyio
@pytest.mark.parametrize("name", ["ping", "delete_all_tickets", "Get_Employee_Info"])
async def test_tools_not_offered_to_the_model_are_never_called(
    live_server_url: str, name: str
) -> None:
    # ping exists on the server and would succeed, so is_error proves it was not called.
    async with EquipDeskMcp(f"{live_server_url}/mcp") as mcp:
        observation = await mcp.call_tool(name, {})

    assert observation.is_error is True
    assert observation.payload == f"Unknown tool: {name}. Available tools: {', '.join(BRIEF_TOOLS)}"


@pytest.mark.anyio
async def test_invalid_arguments_are_an_error_observation(live_server_url: str) -> None:
    async with EquipDeskMcp(f"{live_server_url}/mcp") as mcp:
        observation = await mcp.call_tool("get_employee_info", {"wrong": "E1001"})

    assert observation.is_error is True
    assert isinstance(observation.payload, str)


# Observations become tool results for the next LLM turn


def test_observation_becomes_a_tool_result_block() -> None:
    observation = Observation(
        tool="get_employee_info",
        arguments={"employee_id": "E1001"},
        payload={"employee_id": "E1001"},
        is_error=False,
    )

    block = observation.tool_result("toolu_1")

    assert block == {
        "type": "tool_result",
        "tool_use_id": "toolu_1",
        "content": json.dumps({"employee_id": "E1001"}),
        "is_error": False,
    }


def test_text_observation_is_sent_as_is() -> None:
    observation = Observation(tool="x", arguments={}, payload="Unknown tool: x", is_error=True)

    assert observation.tool_result("toolu_2")["content"] == "Unknown tool: x"


# Exceptions raised by the caller inside the session


@pytest.mark.anyio
async def test_caller_exceptions_leave_the_session_unwrapped(live_server_url: str) -> None:
    # The MCP client's task group would wrap them in an ExceptionGroup; the agent
    # loop must be able to catch its own errors (e.g. LLMError) directly.
    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        async with EquipDeskMcp(f"{live_server_url}/mcp") as mcp:
            await mcp.list_tools()
            raise Boom


# Failures once the session is open become McpUnavailable (exit 2 in the CLI)


@pytest.mark.anyio
@pytest.mark.parametrize("method", ["list_tools", "call_tool"])
async def test_mid_session_failures_become_mcp_unavailable(
    live_server_url: str, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    async def dropped(*_: object, **__: object) -> None:
        raise ExceptionGroup("transport", [ConnectionResetError("api restarted")])

    async with EquipDeskMcp(f"{live_server_url}/mcp") as mcp:
        monkeypatch.setattr(mcp._client, method, dropped)
        with pytest.raises(McpUnavailable, match="api restarted"):
            if method == "list_tools":
                await mcp.list_tools()
            else:
                await mcp.call_tool("get_employee_info", {"employee_id": "E1001"})


@pytest.mark.anyio
async def test_a_failing_session_close_becomes_mcp_unavailable(
    live_server_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp = EquipDeskMcp(f"{live_server_url}/mcp")
    with pytest.raises(McpUnavailable, match="closing"):
        async with mcp:
            real_exit = mcp._client.__aexit__

            async def failing_exit(*args: object) -> None:
                await real_exit(*args)  # type: ignore[arg-type]
                raise ExceptionGroup("close", [OSError("closing the stream failed")])

            monkeypatch.setattr(mcp._client, "__aexit__", failing_exit)


@pytest.mark.anyio
async def test_an_invalid_target_is_reported_as_mcp_unavailable() -> None:
    with pytest.raises(McpUnavailable):
        async with EquipDeskMcp(42):  # type: ignore[arg-type]
            pass  # pragma: no cover - never entered


# Connection failures


@pytest.mark.anyio
async def test_unreachable_server_raises_mcp_unavailable() -> None:
    with pytest.raises(McpUnavailable, match=r"127\.0\.0\.1:9"):
        async with EquipDeskMcp("http://127.0.0.1:9/mcp"):
            pass  # pragma: no cover - never entered
