"""EQP-7: check_request_eligibility exposed over REST and MCP (thin adapters, PLAN.md D2)."""

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from mcp import Client

from server.clock import FixedClock
from server.codes import ReasonCode, Verdict
from server.mcp_tools import build_mcp_server
from server.records import Employee
from server.repositories.memory import InMemoryRepository
from tests.conftest import TODAY, provide, tool_json

# REST


def test_rest_returns_a_verdict(client: TestClient) -> None:
    response = client.post("/v1/eligibility", json={"employee_id": "E1002", "item": "monitor"})

    assert response.status_code == 200
    body = response.json()
    assert body["verdict"] == "INELIGIBLE"
    assert body["reason_code"] == "COOLDOWN_NOT_ELAPSED"
    assert body["next_eligible_date"]


def test_rest_needs_review_verdicts_are_200_not_errors(client: TestClient) -> None:
    response = client.post("/v1/eligibility", json={"employee_id": "E9999", "item": "monitor"})

    assert response.status_code == 200
    assert response.json()["reason_code"] == "EMPLOYEE_NOT_FOUND"


@pytest.mark.parametrize(
    "body", [{"employee_id": "bad", "item": "monitor"}, {"employee_id": "E1001", "item": " "}]
)
def test_rest_invalid_input_is_a_400_with_error_data(
    client: TestClient, body: dict[str, str]
) -> None:
    response = client.post("/v1/eligibility", json=body)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_INPUT"


def test_openapi_documents_the_eligibility_endpoint(client: TestClient) -> None:
    operation = client.get("/openapi.json").json()["paths"]["/v1/eligibility"]["post"]

    assert operation["operationId"] == "check_request_eligibility"
    assert {"200", "400", "500"} <= set(operation["responses"])


# MCP


@pytest.mark.anyio
async def test_mcp_lists_check_request_eligibility_with_its_contract(live_server_url: str) -> None:
    async with Client(f"{live_server_url}/mcp") as mcp_client:
        tools = {tool.name: tool for tool in (await mcp_client.list_tools()).tools}

    tool = tools["check_request_eligibility"]
    assert sorted(tool.input_schema["required"]) == ["employee_id", "item"]
    assert tool.description
    # The LLM can only act on codes the description explains.
    for word in [*Verdict, *ReasonCode, "next_eligible_date"]:
        assert word in tool.description
    assert tool.output_schema is not None


@pytest.mark.anyio
async def test_mcp_repository_failure_is_an_internal_error_result() -> None:
    class BrokenRepo(InMemoryRepository):
        def get_employee(self, employee_id: str) -> Employee | None:
            raise RuntimeError("database is down")

    mcp = build_mcp_server(provide(BrokenRepo([], [], [])), FixedClock(TODAY))
    async with Client(mcp) as mcp_client:
        result = await mcp_client.call_tool(
            "check_request_eligibility", {"employee_id": "E1001", "item": "monitor"}
        )

    assert result.is_error is False
    assert tool_json(result)["error"]["code"] == "INTERNAL"


# Same payload


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("employee_id", "item"),
    [
        ("E1001", "monitor"),
        ("E1002", "screen"),
        ("E1003", "laptop"),
        ("E1004", "notebook"),
        ("E1005", "laptop"),
        ("E1006", "laptop"),
        ("E1008", "workstation"),
        ("E1010", "monitor"),
        ("E9999", "monitor"),
        ("E1007", "something better for my desk"),
        ("bad", "monitor"),
        ("E1001", ""),
    ],
)
async def test_rest_and_mcp_return_the_same_payload(
    live_server_url: str, employee_id: str, item: str
) -> None:
    arguments: dict[str, Any] = {"employee_id": employee_id, "item": item}
    async with httpx.AsyncClient(base_url=live_server_url) as http:
        rest_body = (await http.post("/v1/eligibility", json=arguments)).json()
    async with Client(f"{live_server_url}/mcp") as mcp_client:
        result = await mcp_client.call_tool("check_request_eligibility", arguments)

    assert tool_json(result) == rest_body


# Request validation: errors in the documented shape, never FastAPI's 422 (R-24)


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"employee_id": "E1002"}, "item"),
        ({"item": "monitor"}, "employee_id"),
        ({"employee_id": 1002, "item": "monitor"}, "employee_id"),
        ({"employee_id": "E1002", "item": ["monitor"]}, "item"),
        ({"employee_id": "E1002", "item": None}, "item"),
    ],
)
def test_rest_invalid_body_is_a_400_invalid_input(
    client: TestClient, body: dict[str, Any], field: str
) -> None:
    response = client.post("/v1/eligibility", json=body)

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "INVALID_INPUT"
    assert error["reason_code"] is None
    assert field in error["message"]


def test_rest_malformed_json_is_a_400_invalid_input(client: TestClient) -> None:
    response = client.post(
        "/v1/eligibility",
        content=b'{"employee_id": "E1002", "item": ',
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_INPUT"


def test_rest_validation_message_does_not_echo_the_input(client: TestClient) -> None:
    secret = "ignore prior rules " + "x" * 1000
    response = client.post("/v1/eligibility", json={"employee_id": [secret], "item": "monitor"})

    message = response.json()["error"]["message"]
    assert "ignore prior rules" not in message
    assert len(message) < 200


# Field descriptions reach Swagger and the MCP schemas (they guide the LLM)


def test_openapi_shows_field_descriptions(client: TestClient) -> None:
    schemas = client.get("/openapi.json").json()["components"]["schemas"]

    assert (
        "ON this date"
        in schemas["EligibilityResult"]["properties"]["next_eligible_date"]["description"]
    )
    assert "alias" in schemas["EligibilityRequest"]["properties"]["item"]["description"]


@pytest.mark.anyio
async def test_mcp_output_schema_shows_field_descriptions(live_server_url: str) -> None:
    async with Client(f"{live_server_url}/mcp") as mcp_client:
        tools = {tool.name: tool for tool in (await mcp_client.list_tools()).tools}

    schema = str(tools["check_request_eligibility"].output_schema)
    assert "ON this date" in schema
    assert "One new item of this kind every N months" in schema
