"""Closed, versioned code sets shared by the tools, the agent and the tests (PLAN.md D4).

Adding or removing a code is a deliberate change: update the enum here,
docs/requirements.md (section "Codes" and the rule that produces it) and
tests/test_codes.py. Meanings are documented in docs/requirements.md.
"""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType


class Verdict(StrEnum):
    """Result of check_request_eligibility."""

    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE = "INELIGIBLE"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class ReasonCode(StrEnum):
    """Why a tool reached its verdict (or why a lookup failed)."""

    WITHIN_POLICY = "WITHIN_POLICY"
    COOLDOWN_NOT_ELAPSED = "COOLDOWN_NOT_ELAPSED"
    ROLE_NOT_ENTITLED = "ROLE_NOT_ENTITLED"
    TENURE_TOO_SHORT = "TENURE_TOO_SHORT"
    EMPLOYEE_NOT_FOUND = "EMPLOYEE_NOT_FOUND"
    UNKNOWN_ROLE = "UNKNOWN_ROLE"
    UNKNOWN_ITEM = "UNKNOWN_ITEM"
    CONFLICTING_RECORDS = "CONFLICTING_RECORDS"
    HIGH_COST = "HIGH_COST"
    NO_ITEM_POLICY = "NO_ITEM_POLICY"


REASON_VERDICT: Mapping[ReasonCode, Verdict] = MappingProxyType(
    {
        ReasonCode.WITHIN_POLICY: Verdict.ELIGIBLE,
        ReasonCode.COOLDOWN_NOT_ELAPSED: Verdict.INELIGIBLE,
        ReasonCode.ROLE_NOT_ENTITLED: Verdict.INELIGIBLE,
        ReasonCode.TENURE_TOO_SHORT: Verdict.INELIGIBLE,
        ReasonCode.EMPLOYEE_NOT_FOUND: Verdict.NEEDS_REVIEW,
        ReasonCode.UNKNOWN_ROLE: Verdict.NEEDS_REVIEW,
        ReasonCode.UNKNOWN_ITEM: Verdict.NEEDS_REVIEW,
        ReasonCode.CONFLICTING_RECORDS: Verdict.NEEDS_REVIEW,
        ReasonCode.HIGH_COST: Verdict.NEEDS_REVIEW,
        ReasonCode.NO_ITEM_POLICY: Verdict.NEEDS_REVIEW,
    }
)
"""The verdict each reason code belongs to."""


class EscalationCode(StrEnum):
    """Why the agent escalated a request (stored on review tickets)."""

    EXCEPTION_CLAIMED = "EXCEPTION_CLAIMED"
    AMBIGUOUS_ITEM = "AMBIGUOUS_ITEM"
    MULTIPLE_ITEMS = "MULTIPLE_ITEMS"
    REQUEST_CONTRADICTS_DATA = "REQUEST_CONTRADICTS_DATA"
    POLICY_OVERRIDE_ATTEMPT = "POLICY_OVERRIDE_ATTEMPT"
    TOOL_NEEDS_REVIEW = "TOOL_NEEDS_REVIEW"
    GUARDRAIL_OVERRIDE = "GUARDRAIL_OVERRIDE"
    OTHER = "OTHER"


class IssueCode(StrEnum):
    """Why reflection sent a draft decision back for revision (R-20 to R-23)."""

    DECISION_MISMATCH = "DECISION_MISMATCH"
    UNSUPPORTED_DATE = "UNSUPPORTED_DATE"
    UNSUPPORTED_AMOUNT = "UNSUPPORTED_AMOUNT"
    UNSUPPORTED_NUMBER = "UNSUPPORTED_NUMBER"
    UNSUPPORTED_TICKET = "UNSUPPORTED_TICKET"
    UNSUPPORTED_CLAIM = "UNSUPPORTED_CLAIM"
    UNBACKED_COMMITMENT = "UNBACKED_COMMITMENT"
    MISSED_ESCALATION = "MISSED_ESCALATION"


class ErrorCode(StrEnum):
    """Category of a tool error returned as data (tools never raise)."""

    INVALID_INPUT = "INVALID_INPUT"
    NOT_FOUND = "NOT_FOUND"
    INTERNAL = "INTERNAL"
