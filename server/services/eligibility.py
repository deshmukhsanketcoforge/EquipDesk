"""check_request_eligibility: tool 3 of 4 (docs/requirements.md R-01 to R-09, R-24).

Deterministic (PLAN.md D3): the rules run in the documented order and the first
one that fails decides. The result carries the facts behind the verdict, so the
agent's response and the reflection checker can quote real values.
"""

import logging
from datetime import date
from typing import Any, Final

from server.clock import Clock
from server.codes import REASON_VERDICT, ErrorCode, ReasonCode, Verdict
from server.dates import add_months, whole_months_between
from server.records import Employee
from server.repositories.base import Repository
from server.services.employees import parse_employee_id
from server.services.results import ApiModel, ErrorResult, echo, error_result

logger = logging.getLogger(__name__)

REVIEW_THRESHOLD_USD: Final = 2500
"""A unit cost above this needs human approval even when everything else passes (R-08)."""


class EligibilityResult(ApiModel):
    employee_id: str
    role: str | None
    """None when the employee was not found."""
    item: str | None
    """The catalog item the request resolved to; None if not reached or unknown."""
    verdict: Verdict
    reason_code: ReasonCode
    explanation: str
    tenure_months: int | None
    min_tenure_months: int | None
    refresh_months: int | None
    """One new item of this kind every N months; None when not entitled."""
    last_acquired_on: date | None
    """The most recent item of this kind on file, if any."""
    next_eligible_date: date | None
    """last_acquired_on + refresh_months; eligible ON this date (R-07)."""
    unit_cost_usd: int | None


def check_request_eligibility(
    employee_id: str, item: str, *, repo: Repository, clock: Clock
) -> EligibilityResult | ErrorResult:
    """Is this employee eligible for this item today? Never raises."""
    canonical_id = parse_employee_id(employee_id)
    if canonical_id is None:
        return error_result(
            ErrorCode.INVALID_INPUT,
            f"Malformed employee ID {echo(employee_id)}: expected 'E' followed by 4 digits.",
        )
    if not isinstance(item, str) or not item.strip():
        return error_result(ErrorCode.INVALID_INPUT, "Item must be a non-empty string.")
    try:
        return _evaluate(canonical_id, item, repo, clock.today())
    except Exception:
        logger.exception("check_request_eligibility failed for %s", canonical_id)
        return error_result(ErrorCode.INTERNAL, "Unexpected error while checking eligibility.")


def _evaluate(employee_id: str, requested: str, repo: Repository, today: date) -> EligibilityResult:
    facts: dict[str, Any] = {"employee_id": employee_id}

    def decide(reason: ReasonCode, explanation: str) -> EligibilityResult:
        return EligibilityResult.model_validate(
            {
                "role": None,
                "item": None,
                "tenure_months": None,
                "min_tenure_months": None,
                "refresh_months": None,
                "last_acquired_on": None,
                "next_eligible_date": None,
                "unit_cost_usd": None,
                **facts,
                "verdict": REASON_VERDICT[reason],
                "reason_code": reason,
                "explanation": explanation,
            }
        )

    # R-01
    employee = repo.get_employee(employee_id)
    if employee is None:
        return decide(ReasonCode.EMPLOYEE_NOT_FOUND, f"No employee with ID {employee_id}.")
    tenure = whole_months_between(employee.hire_date, today)
    facts |= {"role": employee.role, "tenure_months": tenure}

    # R-02
    policies = {p.item: p for p in repo.get_role_policies(employee.role)}
    if not policies:
        # The role comes from the database, so it is quoted plainly everywhere; only
        # caller text (the requested item) goes through echo().
        return decide(ReasonCode.UNKNOWN_ROLE, f"Role {employee.role} has no policy.")

    # R-03
    catalog_item = repo.find_catalog_item(requested)
    if catalog_item is None:
        return decide(
            ReasonCode.UNKNOWN_ITEM,
            f"{echo(requested)} is not a catalog item or alias.",
        )
    item = catalog_item.item
    facts |= {"item": item, "unit_cost_usd": catalog_item.unit_cost_usd}

    # R-04
    conflict = _conflict(employee, today)
    if conflict:
        return decide(ReasonCode.CONFLICTING_RECORDS, conflict)

    # R-05 (a known item with no policy row is a data gap, not a policy answer)
    policy = policies.get(item)
    if policy is None:
        return decide(
            ReasonCode.NO_ITEM_POLICY,
            f"Role {employee.role} has a policy, but none for the item {item}.",
        )
    last = max((e.acquired_on for e in employee.equipment if e.item == item), default=None)
    next_date = (
        add_months(last, policy.refresh_months)
        if last is not None and policy.refresh_months is not None
        else None
    )
    facts |= {
        "min_tenure_months": policy.min_tenure_months,
        "refresh_months": policy.refresh_months,
        "last_acquired_on": last,
        "next_eligible_date": next_date,
    }
    if not policy.entitled:
        return decide(
            ReasonCode.ROLE_NOT_ENTITLED, f"Role {employee.role} is not entitled to a {item}."
        )

    # R-06
    if tenure < policy.min_tenure_months:
        return decide(
            ReasonCode.TENURE_TOO_SHORT,
            f"Tenure is {tenure} months; a {item} needs at least "
            f"{policy.min_tenure_months} months.",
        )

    # R-07
    if next_date is not None and today < next_date:
        return decide(
            ReasonCode.COOLDOWN_NOT_ELAPSED,
            f"Last {item} acquired on {last}; the {employee.role} refresh period is "
            f"{policy.refresh_months} months, so the next eligible date is {next_date}.",
        )

    # R-08
    if catalog_item.unit_cost_usd > REVIEW_THRESHOLD_USD:
        return decide(
            ReasonCode.HIGH_COST,
            f"A {item} costs ${catalog_item.unit_cost_usd:,}, above the "
            f"${REVIEW_THRESHOLD_USD:,} review threshold.",
        )

    # R-09
    return decide(ReasonCode.WITHIN_POLICY, f"A {item} is within policy for this {employee.role}.")


def _conflict(employee: Employee, today: date) -> str | None:
    """R-04: why the records contradict each other, or None if they are consistent."""
    if employee.hire_date > today:
        return f"Hire date {employee.hire_date} is after today ({today})."
    for equipment in employee.equipment:
        if equipment.acquired_on < employee.hire_date:
            return (
                f"{equipment.item} on file is dated {equipment.acquired_on}, "
                f"before the hire date {employee.hire_date}."
            )
        if equipment.acquired_on > today:
            return (
                f"{equipment.item} on file is dated {equipment.acquired_on}, after today ({today})."
            )
    return None
