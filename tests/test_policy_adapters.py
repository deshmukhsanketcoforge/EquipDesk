"""EQP-6: get_policy_limits exposed over REST and MCP (thin adapters, PLAN.md D2)."""

import httpx
import pytest
from fastapi.testclient import TestClient
from mcp import Client

from server.clock import FixedClock
from server.mcp_tools import build_mcp_server
from server.records import RolePolicy
from server.repositories.memory import InMemoryRepository
from tests.conftest import TODAY, provide, tool_json

# REST


def test_rest_returns_the_role_policies(client: TestClient) -> None:
    response = client.get("/v1/policies/manager")

    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "manager"
    phone = next(p for p in body["policies"] if p["item"] == "phone")
    assert phone == {
        "item": "phone",
        "entitled": True,
        "refresh_months": 36,
        "min_tenure_months": 0,
    }


@pytest.mark.parametrize(
    ("role", "status", "code", "reason_code"),
    [
        ("intern", 404, "NOT_FOUND", "UNKNOWN_ROLE"),
        (" ", 400, "INVALID_INPUT", None),
    ],
)
def test_rest_returns_errors_as_data_with_matching_status(
    client: TestClient, role: str, status: int, code: str, reason_code: str | None
) -> None:
    response = client.get(f"/v1/policies/{role}")

    assert response.status_code == status
    error = response.json()["error"]
    assert error["code"] == code
    assert error["reason_code"] == reason_code


def test_openapi_documents_the_policy_endpoint(client: TestClient) -> None:
    operation = client.get("/openapi.json").json()["paths"]["/v1/policies/{role}"]["get"]

    assert operation["operationId"] == "get_policy_limits"
    assert {"200", "400", "404", "500"} <= set(operation["responses"])


# MCP


@pytest.mark.anyio
async def test_mcp_lists_get_policy_limits_with_its_contract(live_server_url: str) -> None:
    async with Client(f"{live_server_url}/mcp") as mcp_client:
        tools = {tool.name: tool for tool in (await mcp_client.list_tools()).tools}

    tool = tools["get_policy_limits"]
    assert tool.input_schema["required"] == ["role"]
    assert tool.description and "UNKNOWN_ROLE" in tool.description
    assert tool.output_schema is not None


@pytest.mark.anyio
async def test_mcp_unknown_role_is_data_not_a_tool_failure(live_server_url: str) -> None:
    async with Client(f"{live_server_url}/mcp") as mcp_client:
        result = await mcp_client.call_tool("get_policy_limits", {"role": "intern"})

    assert result.is_error is False
    assert tool_json(result)["error"]["reason_code"] == "UNKNOWN_ROLE"


@pytest.mark.anyio
async def test_mcp_repository_failure_is_an_internal_error_result() -> None:
    class BrokenRepo(InMemoryRepository):
        def get_role_policies(self, role: str) -> list[RolePolicy]:
            raise RuntimeError("database is down")

    mcp = build_mcp_server(provide(BrokenRepo([], [], [])), FixedClock(TODAY))
    async with Client(mcp) as mcp_client:
        result = await mcp_client.call_tool("get_policy_limits", {"role": "engineer"})

    assert result.is_error is False
    assert tool_json(result)["error"]["code"] == "INTERNAL"


# Same payload


@pytest.mark.anyio
@pytest.mark.parametrize(
    "role",
    ["standard", "engineer", "manager", "contractor", " Engineer ", "intern", " ", "", "a/b"],
)
async def test_rest_and_mcp_return_the_same_payload(live_server_url: str, role: str) -> None:
    async with httpx.AsyncClient(base_url=live_server_url) as http:
        rest_body = (await http.get(f"/v1/policies/{role}")).json()
    async with Client(f"{live_server_url}/mcp") as mcp_client:
        result = await mcp_client.call_tool("get_policy_limits", {"role": role})

    assert tool_json(result) == rest_body


@pytest.mark.parametrize(
    ("path", "status", "code"),
    [("/v1/policies/", 400, "INVALID_INPUT"), ("/v1/policies/a/b", 404, "NOT_FOUND")],
)
def test_rest_empty_or_slashed_role_reaches_the_service(
    client: TestClient, path: str, status: int, code: str
) -> None:
    # Not FastAPI's own {"detail": "Not Found"}: the same error data as over MCP.
    response = client.get(path)

    assert response.status_code == status
    assert response.json()["error"]["code"] == code
