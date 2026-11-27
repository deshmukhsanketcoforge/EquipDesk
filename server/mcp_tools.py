"""MCP adapter: tool definitions exposed to the agent.

Tools stay thin; business logic lives in the service layer (PLAN.md D2). Tool
docstrings are the descriptions the LLM reads, so they say what comes back and
what the errors mean.
"""

from mcp.server.mcpserver import MCPServer

from server.clock import Clock
from server.repositories.base import RepositoryProvider
from server.services import employees
from server.services.employees import EmployeeInfo
from server.services.results import ErrorResult, call_with_repository


def build_mcp_server(repositories: RepositoryProvider, clock: Clock) -> MCPServer:
    mcp = MCPServer(
        name="EquipDesk",
        instructions="Tools for handling IT equipment requests.",
    )

    @mcp.tool()
    def ping() -> str:
        """Check that the EquipDesk MCP server is reachable. Returns "pong"."""
        return "pong"

    @mcp.tool()
    def get_employee_info(employee_id: str) -> EmployeeInfo | ErrorResult:
        """Look up an employee: role, hire date, tenure in whole months, and the
        equipment on file (item and acquired date, oldest first).

        `employee_id` is "E" followed by 4 digits, e.g. "E1001".

        Errors are returned as data, never raised: {"error": {"code", "reason_code",
        "message"}}. code NOT_FOUND with reason_code EMPLOYEE_NOT_FOUND means no such
        employee; INVALID_INPUT means a malformed ID; INTERNAL means the lookup failed.
        """
        return call_with_repository(
            repositories,
            lambda repo: employees.get_employee_info(employee_id, repo=repo, clock=clock),
        )

    return mcp
