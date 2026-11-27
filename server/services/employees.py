"""get_employee_info: tool 1 of 4 (docs/requirements.md R-01, R-24)."""

import logging
import re
from datetime import date

from pydantic import BaseModel

from server.clock import Clock
from server.codes import ErrorCode, ReasonCode
from server.dates import whole_months_between
from server.repositories.base import Repository
from server.services.results import ErrorResult, error_result

logger = logging.getLogger(__name__)

EMPLOYEE_ID = re.compile(r"E[0-9]{4}")  # ASCII only: \d would accept any Unicode digit
MAX_ECHO = 32
"""Longest caller input quoted back in an error (the text reaches the agent's LLM)."""


class EquipmentOnFile(BaseModel):
    item: str
    acquired_on: date


class EmployeeInfo(BaseModel):
    employee_id: str
    name: str
    role: str
    hire_date: date
    tenure_months: int
    """Whole months from the hire date to today. Negative (or 0) when the hire
    date is in the future: the lookup reports records as they are, and
    check_request_eligibility turns that into CONFLICTING_RECORDS (R-04)."""
    equipment: list[EquipmentOnFile]
    """Oldest first."""


def _echo(value: object) -> str:
    text = repr(value)
    return text if len(text) <= MAX_ECHO else text[: MAX_ECHO - 3] + "..."


def parse_employee_id(employee_id: object) -> str | None:
    """The canonical ID ("E" + 4 digits), ignoring surrounding whitespace and case."""
    if not isinstance(employee_id, str):
        return None
    candidate = employee_id.strip().upper()
    return candidate if EMPLOYEE_ID.fullmatch(candidate) else None


def get_employee_info(
    employee_id: str, *, repo: Repository, clock: Clock
) -> EmployeeInfo | ErrorResult:
    """Role, tenure and equipment on file for one employee. Never raises."""
    canonical = parse_employee_id(employee_id)
    if canonical is None:
        return error_result(
            ErrorCode.INVALID_INPUT,
            f"Malformed employee ID {_echo(employee_id)}: expected 'E' followed by 4 digits.",
        )
    try:
        employee = repo.get_employee(canonical)
        if employee is None:
            return error_result(
                ErrorCode.NOT_FOUND,
                f"No employee with ID {canonical}.",
                ReasonCode.EMPLOYEE_NOT_FOUND,
            )
        return EmployeeInfo(
            employee_id=employee.employee_id,
            name=employee.name,
            role=employee.role,
            hire_date=employee.hire_date,
            tenure_months=whole_months_between(employee.hire_date, clock.today()),
            equipment=[
                EquipmentOnFile(item=e.item, acquired_on=e.acquired_on) for e in employee.equipment
            ],
        )
    except Exception:
        logger.exception("get_employee_info failed for %s", canonical)
        return error_result(ErrorCode.INTERNAL, "Unexpected error while looking up the employee.")
