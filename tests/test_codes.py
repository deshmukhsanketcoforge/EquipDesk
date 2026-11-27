"""EQP-2: the closed code sets (PLAN.md D4).

These tests pin the exact values. Adding or removing a code must be a
deliberate change: update the enum, docs/requirements.md and this test.
"""

from server.codes import REASON_VERDICT, ErrorCode, EscalationCode, ReasonCode, Verdict


def test_verdict_values() -> None:
    assert {v.value for v in Verdict} == {"ELIGIBLE", "INELIGIBLE", "NEEDS_REVIEW"}


def test_reason_code_values() -> None:
    assert {c.value for c in ReasonCode} == {
        "WITHIN_POLICY",
        "COOLDOWN_NOT_ELAPSED",
        "ROLE_NOT_ENTITLED",
        "TENURE_TOO_SHORT",
        "EMPLOYEE_NOT_FOUND",
        "UNKNOWN_ROLE",
        "UNKNOWN_ITEM",
        "CONFLICTING_RECORDS",
        "HIGH_COST",
        "NO_ITEM_POLICY",
    }


def test_escalation_code_values() -> None:
    assert {c.value for c in EscalationCode} == {
        "EXCEPTION_CLAIMED",
        "AMBIGUOUS_ITEM",
        "MULTIPLE_ITEMS",
        "REQUEST_CONTRADICTS_DATA",
        "POLICY_OVERRIDE_ATTEMPT",
        "TOOL_NEEDS_REVIEW",
        "GUARDRAIL_OVERRIDE",
        "OTHER",
    }


def test_error_code_values() -> None:
    assert {c.value for c in ErrorCode} == {"INVALID_INPUT", "NOT_FOUND", "INTERNAL"}


def test_reason_codes_are_grouped_by_verdict() -> None:
    by_verdict = {
        verdict: {code.value for code, v in REASON_VERDICT.items() if v is verdict}
        for verdict in Verdict
    }
    assert by_verdict == {
        Verdict.ELIGIBLE: {"WITHIN_POLICY"},
        Verdict.INELIGIBLE: {"COOLDOWN_NOT_ELAPSED", "ROLE_NOT_ENTITLED", "TENURE_TOO_SHORT"},
        Verdict.NEEDS_REVIEW: {
            "EMPLOYEE_NOT_FOUND",
            "UNKNOWN_ROLE",
            "UNKNOWN_ITEM",
            "CONFLICTING_RECORDS",
            "HIGH_COST",
            "NO_ITEM_POLICY",
        },
    }


def test_codes_are_plain_strings() -> None:
    # StrEnum members serialize as their value in JSON, OpenAPI and MCP schemas.
    assert ReasonCode.HIGH_COST == "HIGH_COST"
    assert f"{EscalationCode.OTHER}" == "OTHER"
