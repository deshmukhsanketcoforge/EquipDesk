"""EQP-11: the agent's final answer is a validated JSON decision (PLAN.md D8)."""

import json
from typing import Any

import pytest

from agent.decision import DecisionError, DecisionKind, parse_draft

APPROVE = {
    "decision": "APPROVE",
    "item": "monitor",
    "response": "Your second monitor is approved.",
    "reasons": ["check_request_eligibility returned ELIGIBLE (WITHIN_POLICY)."],
    "ticket_id": None,
}


def _text(**changes: Any) -> str:
    return json.dumps(APPROVE | changes)


def test_a_valid_decision_is_parsed() -> None:
    draft = parse_draft(_text())

    assert draft.decision is DecisionKind.APPROVE
    assert draft.item == "monitor"
    assert draft.response == "Your second monitor is approved."
    assert draft.reasons == ["check_request_eligibility returned ELIGIBLE (WITHIN_POLICY)."]
    assert draft.ticket_id is None


@pytest.mark.parametrize(
    "wrapped",
    [
        "```json\n{}\n```",
        "```\n{}\n```",
        "Here is my decision:\n{}",
        "  {}  \n",
    ],
)
def test_json_in_a_fence_or_after_a_preamble_is_accepted(wrapped: str) -> None:
    assert parse_draft(wrapped.replace("{}", _text())).decision is DecisionKind.APPROVE


def test_escalate_carries_the_ticket_and_needs_no_item() -> None:
    draft = parse_draft(_text(decision="ESCALATE", item=None, ticket_id="TKT-000004"))

    assert draft.decision is DecisionKind.ESCALATE
    assert draft.item is None
    assert draft.ticket_id == "TKT-000004"


def test_unknown_fields_are_ignored() -> None:
    assert parse_draft(_text(confidence="high")).decision is DecisionKind.APPROVE


def test_item_is_normalized() -> None:
    assert parse_draft(_text(item="  Monitor ")).item == "monitor"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("APPROVE: your monitor is approved.", "not a JSON object"),
        ("{not json}", "not valid JSON"),
        ("[1, 2]", "not a JSON object"),
        (_text(decision="MAYBE"), "decision"),
        (_text(decision="approve"), "decision"),  # the codes are exact
        (_text(response=""), "response"),
        (_text(response="   "), "response"),
        (_text(reasons=[]), "reasons"),
        (_text(reasons="ELIGIBLE"), "reasons"),
        (json.dumps({k: v for k, v in APPROVE.items() if k != "response"}), "response"),
        (_text(item=None), "item"),  # APPROVE must name its item
        (_text(decision="DENY", item=""), "item"),
    ],
)
def test_malformed_decisions_are_rejected_with_a_short_reason(text: str, message: str) -> None:
    with pytest.raises(DecisionError) as caught:
        parse_draft(text)

    assert message in str(caught.value)
    assert len(str(caught.value)) < 300  # it goes back to the model in the retry


def test_the_error_never_echoes_a_long_answer() -> None:
    with pytest.raises(DecisionError) as caught:
        parse_draft("APPROVE " + "x" * 5000)

    assert "x" * 100 not in str(caught.value)
