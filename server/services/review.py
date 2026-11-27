"""flag_for_human_review: tool 4 of 4 (docs/requirements.md R-24, R-25).

Writes a review ticket. Idempotent on employee + normalized request text, so a
repeated or retried escalation returns the open ticket it already created. The
LLM-written reason is deliberately not part of that key: its wording varies
between runs and would create duplicates (PLAN.md §6).
"""

import hashlib
import logging
import re
from datetime import datetime
from typing import Final

from server.clock import Clock
from server.codes import ErrorCode, EscalationCode
from server.records import NewTicket
from server.repositories.base import Repository, normalize_name
from server.services.employees import parse_employee_id
from server.services.results import ApiModel, ErrorResult, echo, error_result

logger = logging.getLogger(__name__)

MAX_TEXT_LENGTH: Final = 2000
"""Longest request or reason accepted, in characters, after clean_text()."""

REASON_CODE = re.compile(r"\s*([A-Za-z_]+)\s*:")
"""The "<CODE>:" prefix of a reason (R-25)."""


class ReviewTicketResult(ApiModel):
    ticket_id: str
    """E.g. "TKT-000042". The same request always returns the same open ticket."""
    status: str
    """PENDING_REVIEW until a human picks the ticket up."""
    employee_id: str
    escalation_code: EscalationCode
    """Parsed from the reason when the ticket was created; OTHER if not recognized."""
    created_at: datetime


def request_key(request: str) -> str:
    """Idempotency key: SHA-256 of the request text, ignoring case and extra whitespace."""
    return hashlib.sha256(normalize_name(request).encode()).hexdigest()


def parse_escalation_code(reason: str) -> EscalationCode:
    """The EscalationCode a reason starts with ("<CODE>: ..."), else OTHER."""
    match = REASON_CODE.match(reason)
    if match is None:
        return EscalationCode.OTHER
    try:
        return EscalationCode(match.group(1).upper())
    except ValueError:
        return EscalationCode.OTHER


def clean_text(value: str) -> str:
    """The text as stored: NUL characters removed (Postgres cannot store them in a
    text column; rejecting them instead would make the request impossible to
    escalate) and surrounding whitespace stripped."""
    return value.replace("\x00", "").strip()


def _text_problem(name: str, value: object) -> str | None:
    if not isinstance(value, str) or not clean_text(value):
        return f"{name} must be a non-empty string."
    if len(clean_text(value)) > MAX_TEXT_LENGTH:
        return f"{name} is longer than {MAX_TEXT_LENGTH} characters."
    return None


def flag_for_human_review(
    employee_id: str, request: str, reason: str, *, repo: Repository, clock: Clock
) -> ReviewTicketResult | ErrorResult:
    """Create (or return the existing) review ticket for this request. Never raises."""
    canonical_id = parse_employee_id(employee_id)
    if canonical_id is None:
        return error_result(
            ErrorCode.INVALID_INPUT,
            f"Malformed employee ID {echo(employee_id)}: expected 'E' followed by 4 digits.",
        )
    for name, value in (("request", request), ("reason", reason)):
        problem = _text_problem(name, value)
        if problem:
            return error_result(ErrorCode.INVALID_INPUT, problem)

    request_text, reason_text = clean_text(request), clean_text(reason)
    try:
        ticket, _ = repo.get_or_create_open_ticket(
            NewTicket(
                employee_id=canonical_id,
                request_text=request_text,
                request_key=request_key(request_text),
                escalation_code=parse_escalation_code(reason_text),
                reason=reason_text,
                created_at=clock.now(),
            )
        )
    except Exception:
        logger.exception("flag_for_human_review failed for %s", canonical_id)
        return error_result(ErrorCode.INTERNAL, "Unexpected error while creating the ticket.")
    return ReviewTicketResult(
        ticket_id=ticket.ticket_id,
        status=ticket.status,
        employee_id=ticket.employee_id,
        escalation_code=ticket.escalation_code,
        created_at=ticket.created_at,
    )
