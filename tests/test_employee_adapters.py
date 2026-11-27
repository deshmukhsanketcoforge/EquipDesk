"""EQP-5: get_employee_info exposed over REST and MCP (thin adapters, PLAN.md D2).

Both adapters call the same service function, so for every input the REST body
and the MCP tool result must be the same JSON (errors included, R-24).
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from mcp import Client
from mcp.types import CallToolResult, TextContent

from server.clock import FixedClock
from server.codes import ErrorCode
from server.main import create_app
from server.mcp_tools import build_mcp_server
from server.records import Employee
from server.repositories.base import Repository, RepositoryProvider
from server.repositories.memory import InMemoryRepository
from server.rest import ERROR_STATUS
from tests.conftest import TODAY, provide


@pytest.fixture
def client(memory_repo: InMemoryRepository) -> Iterator[TestClient]:
    app = create_app(repositories=provide(memory_repo), clock=FixedClock(TODAY))
    with TestClient(app) as test_client:
        yield test_client


def _tool_json(result: CallToolResult) -> Any:
    texts = [block.text for block in result.content if isinstance(block, TextContent)]
    assert len(texts) == 1, result
    return json.loads(texts[0])


# REST


def test_rest_returns_the_employee(client: TestClient) -> None:
    response = client.get("/v1/employees/E1001")

    assert response.status_code == 200
    body = response.json()
    assert body["employee_id"] == "E1001"
    assert body["role"] == "engineer"
    assert body["tenure_months"] == 72
    assert [e["item"] for e in body["equipment"]] == ["monitor", "laptop"]


@pytest.mark.parametrize(
    ("employee_id", "status", "code", "reason_code"),
    [
        ("E9999", 404, "NOT_FOUND", "EMPLOYEE_NOT_FOUND"),
        ("not-an-id", 400, "INVALID_INPUT", None),
    ],
)
def test_rest_returns_errors_as_data_with_matching_status(
    client: TestClient, employee_id: str, status: int, code: str, reason_code: str | None
) -> None:
    response = client.get(f"/v1/employees/{employee_id}")

    assert response.status_code == status
    error = response.json()["error"]
    assert error["code"] == code
    assert error["reason_code"] == reason_code
    assert error["message"]


def test_rest_internal_error_is_a_500_with_error_data() -> None:
    class BrokenRepo(InMemoryRepository):
        def get_employee(self, employee_id: str) -> Employee | None:
            raise RuntimeError("database is down")

    app = create_app(repositories=provide(BrokenRepo([], [], [])), clock=FixedClock(TODAY))
    with TestClient(app) as test_client:
        response = test_client.get("/v1/employees/E1001")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL"


def test_openapi_documents_the_employee_endpoint(client: TestClient) -> None:
    operation = client.get("/openapi.json").json()["paths"]["/v1/employees/{employee_id}"]["get"]

    assert operation["operationId"] == "get_employee_info"
    assert {"200", "400", "404", "500"} <= set(operation["responses"])


# MCP


@pytest.mark.anyio
async def test_mcp_lists_get_employee_info_with_its_contract(live_server_url: str) -> None:
    async with Client(f"{live_server_url}/mcp") as mcp_client:
        tools = {tool.name: tool for tool in (await mcp_client.list_tools()).tools}

    tool = tools["get_employee_info"]
    assert tool.input_schema["required"] == ["employee_id"]
    assert tool.description and "employee" in tool.description.lower()
    assert tool.output_schema is not None


@pytest.mark.anyio
async def test_mcp_tool_returns_the_employee(live_server_url: str) -> None:
    async with Client(f"{live_server_url}/mcp") as mcp_client:
        result = await mcp_client.call_tool("get_employee_info", {"employee_id": "E1001"})

    assert result.is_error is False
    payload = _tool_json(result)
    assert payload["employee_id"] == "E1001"
    assert result.structured_content == {"result": payload}


@pytest.mark.anyio
async def test_mcp_domain_errors_are_data_not_tool_failures(live_server_url: str) -> None:
    async with Client(f"{live_server_url}/mcp") as mcp_client:
        result = await mcp_client.call_tool("get_employee_info", {"employee_id": "E9999"})

    assert result.is_error is False
    assert _tool_json(result)["error"]["reason_code"] == "EMPLOYEE_NOT_FOUND"


# Same payload


@pytest.mark.anyio
@pytest.mark.parametrize("employee_id", ["E1001", "E1004", "E1005", "e1002 ", "E9999", "bad-id"])
async def test_rest_and_mcp_return_the_same_payload(live_server_url: str, employee_id: str) -> None:
    async with httpx.AsyncClient(base_url=live_server_url) as http:
        rest_body = (await http.get(f"/v1/employees/{employee_id}")).json()
    async with Client(f"{live_server_url}/mcp") as mcp_client:
        result = await mcp_client.call_tool("get_employee_info", {"employee_id": employee_id})

    assert _tool_json(result) == rest_body


# Never raises, even when opening or closing the repository scope fails (R-24)


class ScopeFailure(RuntimeError):
    pass


def _failing_provider(memory_repo: InMemoryRepository, *, on: str) -> RepositoryProvider:
    @contextmanager
    def scope() -> Iterator[Repository]:
        if on == "enter":
            raise ScopeFailure("cannot connect: password=hunter2")
        yield memory_repo
        raise ScopeFailure("rollback failed: password=hunter2")

    return scope


@pytest.mark.parametrize("on", ["enter", "exit"])
def test_rest_scope_failure_is_an_internal_error_result(
    memory_repo: InMemoryRepository, on: str
) -> None:
    provider = _failing_provider(memory_repo, on=on)
    with TestClient(create_app(repositories=provider, clock=FixedClock(TODAY))) as test_client:
        response = test_client.get("/v1/employees/E1001")

    assert response.status_code == 500
    error = response.json()["error"]
    assert error["code"] == "INTERNAL"
    assert "hunter2" not in error["message"]


@pytest.mark.anyio
@pytest.mark.parametrize("on", ["enter", "exit"])
async def test_mcp_scope_failure_is_an_internal_error_result(
    memory_repo: InMemoryRepository, on: str
) -> None:
    mcp = build_mcp_server(_failing_provider(memory_repo, on=on), FixedClock(TODAY))
    async with Client(mcp) as mcp_client:
        result = await mcp_client.call_tool("get_employee_info", {"employee_id": "E1001"})

    assert result.is_error is False
    error = _tool_json(result)["error"]
    assert error["code"] == "INTERNAL"
    assert "hunter2" not in error["message"]


def test_every_error_code_has_an_http_status() -> None:
    # ErrorCode is a closed set (D4); adding a code must also give it a status.
    assert set(ERROR_STATUS) == set(ErrorCode)
