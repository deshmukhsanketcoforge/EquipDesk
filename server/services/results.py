"""Error results shared by every tool (docs/requirements.md R-24).

Tools never raise: failures come back as `{"error": {code, reason_code, message}}`.
"""

import logging
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict

from server.codes import ErrorCode, ReasonCode
from server.repositories.base import Repository, RepositoryProvider

logger = logging.getLogger(__name__)

MAX_ECHO = 32
"""Longest caller input quoted back in an error (the text reaches the agent's LLM)."""


def echo(value: object) -> str:
    """repr() of caller input for an error message, truncated to MAX_ECHO."""
    text = repr(value)
    return text if len(text) <= MAX_ECHO else text[: MAX_ECHO - 3] + "..."


class ApiModel(BaseModel):
    """Base for every tool result and request body. Field docstrings become schema
    descriptions in Swagger and in the MCP tool schemas, where they guide the LLM."""

    model_config = ConfigDict(use_attribute_docstrings=True)


class ToolError(ApiModel):
    code: ErrorCode
    reason_code: ReasonCode | None = None
    """The domain reason when there is one, e.g. EMPLOYEE_NOT_FOUND."""
    message: str


class ErrorResult(ApiModel):
    error: ToolError


def error_result(
    code: ErrorCode, message: str, reason_code: ReasonCode | None = None
) -> ErrorResult:
    return ErrorResult(error=ToolError(code=code, reason_code=reason_code, message=message))


class _Discard(Exception):
    """Raised through the repository scope to roll it back and keep the error."""

    def __init__(self, result: ErrorResult) -> None:
        super().__init__(result.error.message)
        self.result = result


def call_with_repository[T](
    repositories: RepositoryProvider, call: Callable[[Repository], T]
) -> T | ErrorResult:
    """Open a repository scope, run one service call in it, and close it.

    The scope is committed only when the call returns a successful result; an
    ErrorResult rolls it back.

    The services already turn their own failures into error results; this also
    covers failures while opening or closing the scope (e.g. the database is
    down, or the rollback fails), so the adapters never raise either (R-24).
    """
    try:
        with repositories() as repo:
            result = call(repo)
            if isinstance(result, ErrorResult):
                # End the scope as a failure so nothing written before the error
                # is committed while the caller is told it failed.
                raise _Discard(result)
            return result
    except _Discard as discard:
        return discard.result
    except Exception:
        logger.exception("repository scope failed")
        return error_result(ErrorCode.INTERNAL, "Unexpected error while accessing the data.")
