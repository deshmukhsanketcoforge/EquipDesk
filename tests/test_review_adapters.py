"""EQP-8: flag_for_human_review exposed over REST and MCP (thin adapters, PLAN.md D2)."""

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from mcp import Client

from server.clock import FixedClock
from server.codes import EscalationCode
from server.mcp_tools import build_mcp_server
from server.records import NewTicket, ReviewTicket
from server.repositories.memory import InMemoryRepository
from tests.conftest import TODAY, provide, tool_json

SPILL = {
    "employee_id": "E1003",
    "request": "I spilled coffee on my laptop and need a new one",
    "reason": "EXCEPTION_CLAIMED: coffee spill on the laptop",
}

# REST


def test_rest_creates_a_ticket(client: TestClient) -> None:
    response = client.post("/v1/review-tickets", json=SPILL)

    assert response.status_code == 200
    body = response.json()
    assert body["ticket_id"].startswith("TKT-")
    assert body["status"] == "PENDING_REVIEW"
    assert body["escalation_code"] == "EXCEPTION_CLAIMED"


def test_rest_is_idempotent(client: TestClient) -> None:
    first = client.post("/v1/review-tickets", json=SPILL).json()
    second = client.post(
        "/v1/review-tickets", json=SPILL | {"reason": "OTHER: reworded by the model"}
    ).json()

    assert second == first


@pytest.mark.parametrize(
    "body",
    [
        SPILL | {"reason": " "},
        SPILL | {"request": ""},
        SPILL | {"employee_id": "bad"},
        {"employee_id": "E1003", "request": "x"},  # missing reason: validation handler
    ],
)
def test_rest_invalid_input_is_a_400_with_error_data(
    client: TestClient, body: dict[str, Any]
) -> None:
    response = client.post("/v1/review-tickets", json=body)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_INPUT"


def test_openapi_documents_the_review_ticket_endpoint(client: TestClient) -> None:
    operation = client.get("/openapi.json").json()["paths"]["/v1/review-tickets"]["post"]

    assert operation["operationId"] == "flag_for_human_review"
    assert {"200", "400", "500"} <= set(operation["responses"])


# MCP


@pytest.mark.anyio
async def test_mcp_lists_flag_for_human_review_with_its_contract(live_server_url: str) -> None:
    async with Client(f"{live_server_url}/mcp") as mcp_client:
        tools = {tool.name: tool for tool in (await mcp_client.list_tools()).tools}

    tool = tools["flag_for_human_review"]
    assert sorted(tool.input_schema["required"]) == ["employee_id", "reason", "request"]
    assert tool.description
    # The LLM can only use codes the description explains.
    for word in [*EscalationCode, "PENDING_REVIEW", "<CODE>: "]:
        assert word in tool.description
    assert tool.output_schema is not None


@pytest.mark.anyio
async def test_mcp_repository_failure_is_an_internal_error_result() -> None:
    class BrokenRepo(InMemoryRepository):
        def get_or_create_open_ticket(self, new: NewTicket) -> tuple[ReviewTicket, bool]:
            raise RuntimeError("database is down")

    mcp = build_mcp_server(provide(BrokenRepo([], [], [])), FixedClock(TODAY))
    async with Client(mcp) as mcp_client:
        result = await mcp_client.call_tool("flag_for_human_review", SPILL)

    assert result.is_error is False
    assert tool_json(result)["error"]["code"] == "INTERNAL"


# Same payload: a ticket created over REST is returned unchanged over MCP, and the
# reverse, because the call is idempotent.


@pytest.mark.anyio
@pytest.mark.parametrize(
    "arguments",
    [
        SPILL,
        {"employee_id": "E9999", "request": "I need a monitor", "reason": "TOOL_NEEDS_REVIEW: x"},
        {"employee_id": "E1007", "request": "something better", "reason": "AMBIGUOUS_ITEM: y"},
        SPILL | {"reason": ""},
        SPILL | {"employee_id": "bad"},
    ],
)
async def test_rest_and_mcp_return_the_same_payload(
    live_server_url: str, arguments: dict[str, str]
) -> None:
    async with httpx.AsyncClient(base_url=live_server_url) as http:
        rest_body = (await http.post("/v1/review-tickets", json=arguments)).json()
    async with Client(f"{live_server_url}/mcp") as mcp_client:
        result = await mcp_client.call_tool("flag_for_human_review", arguments)

    assert tool_json(result) == rest_body


@pytest.mark.anyio
async def test_mcp_first_then_rest_returns_the_same_ticket(live_server_url: str) -> None:
    async with Client(f"{live_server_url}/mcp") as mcp_client:
        mcp_body = tool_json(await mcp_client.call_tool("flag_for_human_review", SPILL))
    async with httpx.AsyncClient(base_url=live_server_url) as http:
        rest_body = (await http.post("/v1/review-tickets", json=SPILL)).json()

    assert rest_body == mcp_body
