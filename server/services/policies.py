"""get_policy_limits: tool 2 of 4 (docs/requirements.md R-02, R-24)."""

import logging

from server.codes import ErrorCode, ReasonCode
from server.repositories.base import Repository, normalize_name
from server.services.results import ApiModel, ErrorResult, echo, error_result

logger = logging.getLogger(__name__)


class ItemPolicy(ApiModel):
    item: str
    entitled: bool
    refresh_months: int | None
    """One new item every this many months; None when the role is not entitled."""
    min_tenure_months: int
    """Whole months of tenure needed before the item can be granted."""


class PolicyLimits(ApiModel):
    role: str
    policies: list[ItemPolicy]
    """One entry per item that has a policy row for this role, in item order. The
    seed writes a row for every catalog item and role (tests/test_seed_data.py)."""


def get_policy_limits(role: str, *, repo: Repository) -> PolicyLimits | ErrorResult:
    """What a role is entitled to and how often. Never raises."""
    if not isinstance(role, str) or not role.strip():
        return error_result(ErrorCode.INVALID_INPUT, "Role must be a non-empty string.")
    key = normalize_name(role)
    try:
        policies = repo.get_role_policies(key)
    except Exception:
        logger.exception("get_policy_limits failed for %s", echo(key))
        return error_result(ErrorCode.INTERNAL, "Unexpected error while looking up the policy.")
    if not policies:
        return error_result(
            ErrorCode.NOT_FOUND, f"No policy for role {echo(key)}.", ReasonCode.UNKNOWN_ROLE
        )
    return PolicyLimits(
        role=key,
        policies=[
            ItemPolicy(
                item=p.item,
                entitled=p.entitled,
                refresh_months=p.refresh_months,
                min_tenure_months=p.min_tenure_months,
            )
            for p in policies
        ],
    )
