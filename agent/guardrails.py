"""Decision guardrails (docs/requirements.md R-16 to R-18; PLAN.md D9).

Enforced in code after the model drafts a decision, so "escalate, don't guess"
holds even if the model misbehaves or follows an injected instruction. They only
check facts code can see: the answers tools returned in this run, bound to the
requesting employee, the item the decision names and this request's text.
"""

from typing import Any

from agent.decision import DecisionKind, Draft
from agent.trace import Action
from server.codes import EscalationCode
from server.repositories.base import normalize_name
from server.services.review import clean_text, parse_escalation_code

ELIGIBILITY_TOOL = "check_request_eligibility"
ESCALATION_TOOL = "flag_for_human_review"

MAX_QUOTE = 40
"""Longest model-written value quoted in a violation (it becomes a ticket reason)."""


def canonical_id(employee_id: str) -> str:
    return employee_id.strip().upper()


def same_text(a: str, b: str) -> bool:
    """Compared exactly as flag_for_human_review keys tickets (server request_key):
    NULs removed, then case and whitespace ignored."""
    return normalize_name(clean_text(a)) == normalize_name(clean_text(b))


def quote(value: str | None) -> str:
    """repr() of a model-written value, shortened for a violation message."""
    text = repr(value)
    return text if len(text) <= MAX_QUOTE else text[: MAX_QUOTE - 3] + "..."


def ticket_code(payload: dict[str, Any]) -> EscalationCode | None:
    """The code stored on a ticket, or None if it is missing or unknown to this agent."""
    code = payload.get("escalation_code")
    if not isinstance(code, str):
        return None
    try:
        return EscalationCode(code)
    except ValueError:
        return None


def requested_code(action: Action) -> EscalationCode:
    """The code a flag_for_human_review call asked for, read as the server reads it."""
    reason = action.arguments.get("reason")
    return (
        parse_escalation_code(clean_text(reason))
        if isinstance(reason, str)
        else EscalationCode.OTHER
    )


def _answers(actions: list[Action], tool: str | None = None) -> list[dict[str, Any]]:
    """Tool answers (JSON objects, not errors) from calls that really ran."""
    return [
        action.observation
        for action in actions
        if action.executed
        and not action.is_error
        and isinstance(action.observation, dict)
        and "error" not in action.observation
        and (tool is None or action.tool == tool)
    ]


def _verdict(actions: list[Action], employee_id: str, item: str | None) -> str | None:
    """The latest eligibility verdict for this employee and item, if any."""
    verdicts = [
        answer.get("verdict")
        for answer in _answers(actions, ELIGIBILITY_TOOL)
        if answer.get("employee_id") == canonical_id(employee_id) and answer.get("item") == item
    ]
    return verdicts[-1] if verdicts else None


def _needs_review(actions: list[Action]) -> bool:
    """R-17/R-10: any NEEDS_REVIEW verdict, or a lookup error with a reason code."""
    for action in actions:
        payload = action.observation
        if not action.executed or not isinstance(payload, dict):
            continue
        if payload.get("verdict") == "NEEDS_REVIEW":
            return True
        error = payload.get("error")
        if isinstance(error, dict) and error.get("reason_code"):
            return True
    return False


def find_ticket(actions: list[Action], *, employee_id: str, request: str) -> Action | None:
    """The latest flag_for_human_review call that returned a ticket (with a known
    code) for this employee and this request text."""
    found = None
    for action in actions:
        if action.tool != ESCALATION_TOOL or not action.executed or action.is_error:
            continue
        answer = action.observation
        if (
            isinstance(answer, dict)
            and isinstance(answer.get("ticket_id"), str)
            and ticket_code(answer) is not None
            and answer.get("employee_id") == canonical_id(employee_id)
            and same_text(str(action.arguments.get("request", "")), request)
        ):
            found = action
    return found


def check(draft: Draft, actions: list[Action], *, employee_id: str, request: str) -> str | None:
    """Why the tool results do not support this draft decision, or None if they do."""
    who = canonical_id(employee_id)
    if draft.decision is DecisionKind.APPROVE:
        verdict = _verdict(actions, employee_id, draft.item)
        if verdict != "ELIGIBLE":
            return (
                f"R-16: APPROVE of {quote(draft.item)} needs the latest eligibility check for "
                f"{who} and that item to be ELIGIBLE; it was {verdict or 'never run'}."
            )
        return None
    if draft.decision is DecisionKind.DENY:
        verdict = _verdict(actions, employee_id, draft.item)
        if verdict != "INELIGIBLE":
            return (
                f"R-17: DENY of {quote(draft.item)} needs the latest eligibility check for "
                f"{who} and that item to be INELIGIBLE; it was {verdict or 'never run'}."
            )
        if _needs_review(actions):
            return "R-17: DENY is not allowed after a tool returned NEEDS_REVIEW in this run."
        return None
    flag = find_ticket(actions, employee_id=employee_id, request=request)
    if flag is None:
        return (
            f"R-18: ESCALATE needs a review ticket created by flag_for_human_review for "
            f"{who} and this request; there is none."
        )
    ticket_id = flag.observation["ticket_id"]
    if draft.ticket_id and draft.ticket_id != ticket_id:
        return (
            f"R-18: the decision cites ticket {quote(draft.ticket_id)}, but the ticket for "
            f"this request is {ticket_id}."
        )
    return None
