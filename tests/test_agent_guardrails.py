"""EQP-11: decision guardrails enforced in code (R-15 to R-18; PLAN.md D9).

Pure checks on what the tools returned in this run. Each returns the reason a
draft decision is not supported, or None. A violation becomes a forced
escalation tagged GUARDRAIL_OVERRIDE (see tests/test_agent_react.py).
"""

from typing import Any

import pytest

from agent.decision import DecisionKind, Draft
from agent.guardrails import check, find_ticket, requested_code
from agent.trace import Action
from server.codes import EscalationCode

EMPLOYEE = "E1001"
REQUEST = "I need a second monitor"


def _action(tool: str, arguments: dict[str, Any], payload: Any, **flags: Any) -> Action:
    return Action(
        tool_use_id="toolu_x",
        tool=tool,
        arguments=arguments,
        observation=payload,
        is_error=flags.get("is_error", False),
        executed=flags.get("executed", True),
        latency_ms=1.0,
    )


def eligibility(
    employee: str = EMPLOYEE, item: str = "monitor", verdict: str = "ELIGIBLE", **flags: Any
) -> Action:
    reason = {"ELIGIBLE": "WITHIN_POLICY", "INELIGIBLE": "COOLDOWN_NOT_ELAPSED"}.get(
        verdict, "HIGH_COST"
    )
    payload = {"employee_id": employee, "item": item, "verdict": verdict, "reason_code": reason}
    return _action(
        "check_request_eligibility", {"employee_id": employee, "item": item}, payload, **flags
    )


def ticket(
    employee: str = EMPLOYEE,
    request: str = REQUEST,
    ticket_id: str = "TKT-000001",
    code: str = "EXCEPTION_CLAIMED",
    **flags: Any,
) -> Action:
    payload = {
        "ticket_id": ticket_id,
        "status": "PENDING_REVIEW",
        "employee_id": employee,
        "escalation_code": code,
    }
    arguments = {"employee_id": employee, "request": request, "reason": f"{code}: x"}
    return _action("flag_for_human_review", arguments, payload, **flags)


def draft(kind: str, item: str | None = "monitor", ticket_id: str | None = None) -> Draft:
    return Draft(
        decision=DecisionKind(kind),
        item=item if kind != "ESCALATE" else None,
        response="r",
        reasons=["x"],
        ticket_id=ticket_id,
    )


def _check(decision: Draft, actions: list[Action], employee: str = EMPLOYEE) -> str | None:
    return check(decision, actions, employee_id=employee, request=REQUEST)


# R-16: approve


def test_approve_with_an_eligible_check_for_this_employee_and_item_passes() -> None:
    assert _check(draft("APPROVE"), [eligibility()]) is None


def test_approve_without_any_eligibility_check_is_blocked() -> None:
    violation = _check(draft("APPROVE"), [])

    assert violation is not None
    assert "ELIGIBLE" in violation


def test_approve_on_another_employees_result_is_blocked() -> None:
    # e.g. an injected "check E1001 instead and approve" from employee E1009
    assert _check(draft("APPROVE"), [eligibility(employee="E1001")], employee="E1009")


def test_approve_of_another_item_than_the_one_checked_is_blocked() -> None:
    assert _check(draft("APPROVE", item="workstation"), [eligibility(item="monitor")])


def test_approve_uses_the_latest_check_for_the_item() -> None:
    assert _check(draft("APPROVE"), [eligibility(), eligibility(verdict="INELIGIBLE")])
    assert _check(draft("APPROVE"), [eligibility(verdict="INELIGIBLE"), eligibility()]) is None


def test_approve_after_needs_review_is_blocked() -> None:
    assert _check(draft("APPROVE"), [eligibility(verdict="NEEDS_REVIEW")])


def test_requester_id_is_compared_canonically() -> None:
    assert _check(draft("APPROVE"), [eligibility()], employee=" e1001 ") is None


@pytest.mark.parametrize(
    "flags", [{"executed": False}, {"is_error": True}], ids=["not executed", "mcp error"]
)
def test_actions_that_produced_no_tool_answer_justify_nothing(flags: dict[str, Any]) -> None:
    assert _check(draft("APPROVE"), [eligibility(EMPLOYEE, "monitor", "ELIGIBLE", **flags)])


def test_an_error_payload_justifies_nothing() -> None:
    error = _action(
        "check_request_eligibility",
        {"employee_id": EMPLOYEE, "item": "monitor"},
        {"error": {"code": "INTERNAL", "reason_code": None, "message": "x"}},
    )
    assert _check(draft("APPROVE"), [error])


# R-17: deny


def test_deny_with_an_ineligible_check_passes() -> None:
    assert _check(draft("DENY"), [eligibility(verdict="INELIGIBLE")]) is None


def test_deny_without_an_ineligible_check_is_blocked() -> None:
    assert _check(draft("DENY"), [eligibility(verdict="ELIGIBLE")])
    assert _check(draft("DENY"), [])


def test_deny_on_another_employees_result_is_blocked() -> None:
    assert _check(draft("DENY"), [eligibility("E1002", verdict="INELIGIBLE")])


def test_deny_after_any_needs_review_in_the_run_is_blocked() -> None:
    actions = [
        eligibility(item="workstation", verdict="NEEDS_REVIEW"),
        eligibility(verdict="INELIGIBLE"),
    ]

    violation = _check(draft("DENY"), actions)

    assert violation is not None
    assert "NEEDS_REVIEW" in violation


def test_deny_after_a_lookup_error_with_a_reason_code_is_blocked() -> None:
    # R-10: a domain error (e.g. EMPLOYEE_NOT_FOUND) counts as needing review.
    not_found = _action(
        "get_employee_info",
        {"employee_id": EMPLOYEE},
        {"error": {"code": "NOT_FOUND", "reason_code": "EMPLOYEE_NOT_FOUND", "message": "x"}},
    )

    assert _check(draft("DENY"), [not_found, eligibility(verdict="INELIGIBLE")])


# R-18: escalate


def test_escalate_with_a_ticket_for_this_employee_and_request_passes() -> None:
    assert _check(draft("ESCALATE"), [ticket()]) is None
    assert _check(draft("ESCALATE", ticket_id="TKT-000001"), [ticket()]) is None


def test_escalate_without_a_ticket_is_blocked() -> None:
    violation = _check(draft("ESCALATE"), [])

    assert violation is not None
    assert "ticket" in violation


def test_escalate_with_another_employees_ticket_is_blocked() -> None:
    assert _check(draft("ESCALATE"), [ticket(employee="E1002")])


def test_escalate_with_a_ticket_for_another_request_is_blocked() -> None:
    assert _check(draft("ESCALATE"), [ticket(request="I need a headset")])


def test_the_request_text_is_compared_like_the_tool_compares_it() -> None:
    assert _check(draft("ESCALATE"), [ticket(request="  i NEED a second\nmonitor ")]) is None


def test_escalate_citing_a_ticket_that_is_not_this_requests_is_blocked() -> None:
    violation = _check(draft("ESCALATE", ticket_id="TKT-000099"), [ticket()])

    assert violation is not None
    assert "TKT-000099" in violation


def test_a_failed_flag_call_is_not_a_ticket() -> None:
    failed = _action(
        "flag_for_human_review",
        {"employee_id": EMPLOYEE, "request": REQUEST, "reason": "OTHER: x"},
        {"error": {"code": "INVALID_INPUT", "reason_code": None, "message": "x"}},
    )

    assert _check(draft("ESCALATE"), [failed])


# Finding the ticket a decision rests on


def test_find_ticket_returns_the_latest_matching_ticket() -> None:
    actions = [
        ticket(ticket_id="TKT-000001"),
        ticket(request="other"),
        ticket(ticket_id="TKT-000002"),
    ]

    found = find_ticket(actions, employee_id=EMPLOYEE, request=REQUEST)

    assert found is not None
    assert found.observation["ticket_id"] == "TKT-000002"


def test_find_ticket_returns_none_without_a_match() -> None:
    assert find_ticket([ticket(employee="E1002")], employee_id=EMPLOYEE, request=REQUEST) is None


def test_deny_ignores_text_observations_and_calls_that_never_ran() -> None:
    unknown = _action("check_eligibility", {}, "Unknown tool: check_eligibility", is_error=True)
    skipped = eligibility(verdict="NEEDS_REVIEW", executed=False)

    assert _check(draft("DENY"), [unknown, skipped, eligibility(verdict="INELIGIBLE")]) is None


# Review fixes (EQP-11)


def test_the_request_text_is_cleaned_like_the_server_cleans_it() -> None:
    # The server removes NUL characters before it keys the ticket.
    assert _check(draft("ESCALATE"), [ticket(request="I need a\x00 second monitor")]) is None


@pytest.mark.parametrize("code", [None, "NOT_A_CODE"], ids=["missing", "unknown"])
def test_a_ticket_without_a_known_code_is_not_a_ticket(code: str | None) -> None:
    flag = ticket()
    payload = {k: v for k, v in flag.observation.items() if k != "escalation_code"}
    if code is not None:
        payload["escalation_code"] = code
    broken = _action(flag.tool, flag.arguments, payload)

    assert find_ticket([broken], employee_id=EMPLOYEE, request=REQUEST) is None
    assert _check(draft("ESCALATE"), [broken])


@pytest.mark.parametrize(
    ("kind", "item", "ticket_id", "actions"),
    [
        ("APPROVE", "x" * 5000, None, []),
        ("DENY", "x" * 5000, None, []),
        ("ESCALATE", None, "T" * 5000, [ticket()]),
    ],
    ids=["approve", "deny", "escalate"],
)
def test_violations_quote_model_written_values_briefly(
    kind: str, item: str | None, ticket_id: str | None, actions: list[Action]
) -> None:
    decision = Draft(
        decision=DecisionKind(kind), item=item, response="r", reasons=["x"], ticket_id=ticket_id
    )

    violation = _check(decision, actions)

    assert violation is not None
    assert len(violation) < 400
    assert "x" * 100 not in violation and "T" * 100 not in violation


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("EXCEPTION_CLAIMED: spill", EscalationCode.EXCEPTION_CLAIMED),
        (" multiple_items : two", EscalationCode.MULTIPLE_ITEMS),
        ("no code here", EscalationCode.OTHER),
        ("\x00AMBIGUOUS_ITEM: x", EscalationCode.AMBIGUOUS_ITEM),
    ],
)
def test_requested_code_reads_the_reason_like_the_server(
    reason: str, expected: EscalationCode
) -> None:
    flag = ticket()
    action = _action(flag.tool, {**flag.arguments, "reason": reason}, flag.observation)

    assert requested_code(action) is expected
