"""EQP-5: scripts/smoke_client.py, the one-tool server/client proof (deliverable #2)."""

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent

from scripts.smoke_client import main, read_payload
from tests.conftest import serve


def test_smoke_client_lists_the_tool_and_gets_e1001(
    live_server_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["--url", f"{live_server_url}/mcp"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert f"Connected to {live_server_url}/mcp" in out
    assert "get_employee_info" in out
    assert '"employee_id": "E1001"' in out
    assert '"name": "Asha Rao"' in out


def test_smoke_client_fails_when_the_tool_returns_an_error(
    live_server_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["--url", f"{live_server_url}/mcp", "--employee-id", "E9999"])

    assert exit_code == 1
    assert "EMPLOYEE_NOT_FOUND" in capsys.readouterr().out


def test_smoke_client_fails_cleanly_when_the_server_is_down(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(["--url", "http://127.0.0.1:9/mcp"])

    assert exit_code == 2
    assert "Could not reach" in capsys.readouterr().err


def test_smoke_client_reports_a_text_error_from_a_reachable_server(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The server answers, but without the tool: that is a tool problem (exit 1),
    # not a connection problem (exit 2).
    mcp = MCPServer(name="no-tools-yet")

    @mcp.tool()
    def ping() -> str:
        return "pong"

    with serve(mcp.streamable_http_app()) as url:
        exit_code = main(["--url", f"{url}/mcp"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Unknown tool: get_employee_info" in captured.out
    assert "Could not" not in captured.err


def test_read_payload_parses_json_text() -> None:
    result = CallToolResult(content=[TextContent(type="text", text='{"a": 1}')])
    assert read_payload(result) == {"a": 1}


def test_read_payload_keeps_non_json_text_as_is() -> None:
    result = CallToolResult(content=[TextContent(type="text", text="Unknown tool: x")])
    assert read_payload(result) == "Unknown tool: x"


def test_read_payload_falls_back_to_structured_content() -> None:
    result = CallToolResult(content=[], structured_content={"result": {"employee_id": "E1001"}})
    assert read_payload(result) == {"employee_id": "E1001"}


def test_read_payload_with_nothing_returns_none() -> None:
    assert read_payload(CallToolResult(content=[])) is None
