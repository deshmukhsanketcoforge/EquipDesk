"""REST adapter (/v1/...): the same service functions as the MCP tools, documented
in Swagger. Error results keep the same body; the HTTP status follows the code."""

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from server.clock import Clock
from server.codes import ErrorCode
from server.repositories.base import RepositoryProvider
from server.services import employees, policies
from server.services.employees import EmployeeInfo
from server.services.policies import PolicyLimits
from server.services.results import ErrorResult, call_with_repository

ERROR_STATUS: dict[ErrorCode, int] = {
    ErrorCode.INVALID_INPUT: 400,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.INTERNAL: 500,
}

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorResult, "description": "INVALID_INPUT: malformed input"},
    404: {"model": ErrorResult, "description": "NOT_FOUND: no such record"},
    500: {"model": ErrorResult, "description": "INTERNAL: unexpected failure"},
}


def _respond[T: BaseModel](result: T | ErrorResult) -> T | JSONResponse:
    if isinstance(result, ErrorResult):
        return JSONResponse(
            # A test keeps this map complete; 500 is only a safety net.
            status_code=ERROR_STATUS.get(result.error.code, 500),
            content=result.model_dump(mode="json"),
        )
    return result


def build_router(repositories: RepositoryProvider, clock: Clock) -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["equipment requests"])

    @router.get(
        # :path so "" and "a/b" reach the service, as over MCP (same error data).
        "/employees/{employee_id:path}",
        operation_id="get_employee_info",
        response_model=EmployeeInfo,
        responses=ERROR_RESPONSES,
        summary="Employee role, tenure and equipment on file",
    )
    def get_employee_info(employee_id: str) -> EmployeeInfo | JSONResponse:
        """Same operation as the `get_employee_info` MCP tool."""
        return _respond(
            call_with_repository(
                repositories,
                lambda repo: employees.get_employee_info(employee_id, repo=repo, clock=clock),
            )
        )

    @router.get(
        "/policies/{role:path}",
        operation_id="get_policy_limits",
        response_model=PolicyLimits,
        responses=ERROR_RESPONSES,
        summary="What a role is entitled to and how often",
    )
    def get_policy_limits(role: str) -> PolicyLimits | JSONResponse:
        """Same operation as the `get_policy_limits` MCP tool."""
        return _respond(
            call_with_repository(
                repositories, lambda repo: policies.get_policy_limits(role, repo=repo)
            )
        )

    return router
