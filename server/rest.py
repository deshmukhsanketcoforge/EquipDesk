"""REST adapter (/v1/...): the same service functions as the MCP tools, documented
in Swagger. Error results keep the same body; the HTTP status follows the code."""

from typing import Any

from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from server.clock import Clock
from server.codes import ErrorCode
from server.repositories.base import RepositoryProvider
from server.services import eligibility, employees, policies
from server.services.eligibility import EligibilityResult
from server.services.employees import EmployeeInfo
from server.services.policies import PolicyLimits
from server.services.results import ApiModel, ErrorResult, call_with_repository, error_result

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


MAX_VALIDATION_ERRORS = 3


async def invalid_request_handler(_: Request, exc: Exception) -> JSONResponse:
    """A body or parameter that fails validation is INVALID_INPUT error data (R-24),
    not FastAPI's own 422 {"detail": [...]}. Names the field and the problem; never
    repeats the input, which is untrusted and would reach the agent's LLM."""
    assert isinstance(exc, RequestValidationError)
    problems = []
    for error in exc.errors()[:MAX_VALIDATION_ERRORS]:
        field = ".".join(str(part) for part in error["loc"] if part != "body") or "body"
        problems.append(f"{field}: {error['msg']}")
    result = error_result(ErrorCode.INVALID_INPUT, "Invalid request. " + "; ".join(problems))
    return JSONResponse(status_code=400, content=result.model_dump(mode="json"))


class EligibilityRequest(ApiModel):
    employee_id: str
    item: str
    """One catalog item name or alias, as the employee wrote it (e.g. "screen")."""


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

    @router.post(
        "/eligibility",
        operation_id="check_request_eligibility",
        response_model=EligibilityResult,
        responses={code: ERROR_RESPONSES[code] for code in (400, 500)},
        summary="Is this employee eligible for this item today?",
    )
    def check_request_eligibility(request: EligibilityRequest) -> EligibilityResult | JSONResponse:
        """Same operation as the `check_request_eligibility` MCP tool. Every verdict,
        including NEEDS_REVIEW, is a 200; only malformed input or a failure is an error."""
        return _respond(
            call_with_repository(
                repositories,
                lambda repo: eligibility.check_request_eligibility(
                    request.employee_id, request.item, repo=repo, clock=clock
                ),
            )
        )

    return router
