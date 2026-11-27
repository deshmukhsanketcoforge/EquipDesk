"""Error results shared by every tool (docs/requirements.md R-24).

Tools never raise: failures come back as `{"error": {code, reason_code, message}}`.
"""

from pydantic import BaseModel

from server.codes import ErrorCode, ReasonCode


class ToolError(BaseModel):
    code: ErrorCode
    reason_code: ReasonCode | None = None
    """The domain reason when there is one, e.g. EMPLOYEE_NOT_FOUND."""
    message: str


class ErrorResult(BaseModel):
    error: ToolError


def error_result(
    code: ErrorCode, message: str, reason_code: ReasonCode | None = None
) -> ErrorResult:
    return ErrorResult(error=ToolError(code=code, reason_code=reason_code, message=message))
