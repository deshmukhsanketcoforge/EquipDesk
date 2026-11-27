"""EQP-8: flag_for_human_review service (R-24, R-25).

Idempotent on employee + normalized request text; the LLM-written reason is not
part of the key, because its wording varies between runs (PLAN.md §6).
"""

from datetime import UTC, date, datetime
from typing import Any

import pytest

from server.clock import FixedClock
from server.codes import ErrorCode, EscalationCode
from server.db.seed_data import build_seed_data
from server.records import NewTicket, ReviewTicket
from server.repositories.memory import InMemoryRepository
from server.services.results import ErrorResult
from server.services.review import (
    MAX_TEXT_LENGTH,
    ReviewTicketResult,
    flag_for_human_review,
    parse_escalation_code,
    request_key,
)

TODAY = date(2026, 11, 26)
CLOCK = FixedClock(TODAY)
SPILL = "My laptop died after I spilled coffee on it, I need a new one"


@pytest.fixture
def repo() -> InMemoryRepository:
    return InMemoryRepository.from_seed(build_seed_data(TODAY))


def _flag(
    repo: InMemoryRepository,
    reason: str = "EXCEPTION_CLAIMED: says coffee was spilled on the laptop",
    request: str = SPILL,
    employee_id: str = "E1003",
) -> ReviewTicketResult:
    result = flag_for_human_review(employee_id, request, reason, repo=repo, clock=CLOCK)
    assert isinstance(result, ReviewTicketResult), result
    return result


def _error(result: ReviewTicketResult | ErrorResult) -> ErrorResult:
    assert isinstance(result, ErrorResult), result
    return result


# --- A ticket is written -------------------------------------------------------


def test_creates_a_pending_review_ticket(repo: InMemoryRepository) -> None:
    result = _flag(repo)

    assert result.ticket_id == "TKT-000001"
    assert result.status == "PENDING_REVIEW"
    assert result.employee_id == "E1003"
    assert result.escalation_code is EscalationCode.EXCEPTION_CLAIMED
    assert result.created_at == datetime(2026, 11, 26, tzinfo=UTC)


def test_the_ticket_row_stores_request_reason_and_key(repo: InMemoryRepository) -> None:
    _flag(repo, reason="  EXCEPTION_CLAIMED: coffee spill  ", request=f"  {SPILL} ")

    stored, created = repo.get_or_create_open_ticket(
        NewTicket(
            employee_id="E1003",
            request_text="probe",
            request_key=request_key(SPILL),
            escalation_code=EscalationCode.OTHER,
            reason="probe",
            created_at=datetime(2027, 1, 1, tzinfo=UTC),
        )
    )
    assert created is False  # the service's ticket is the open one for this key
    assert stored.request_text == SPILL
    assert stored.reason == "EXCEPTION_CLAIMED: coffee spill"
    assert stored.escalation_code is EscalationCode.EXCEPTION_CLAIMED


def test_unknown_employee_can_still_be_escalated(repo: InMemoryRepository) -> None:
    # Case F: E9999 does not exist, and the escalation must still get a ticket.
    result = _flag(repo, "TOOL_NEEDS_REVIEW: EMPLOYEE_NOT_FOUND", "I need a monitor", "E9999")

    assert result.employee_id == "E9999"
    assert result.status == "PENDING_REVIEW"


def test_employee_id_is_normalized(repo: InMemoryRepository) -> None:
    assert _flag(repo, employee_id=" e1003 ").employee_id == "E1003"


def test_result_serializes_to_plain_json(repo: InMemoryRepository) -> None:
    payload = _flag(repo).model_dump(mode="json")

    assert payload == {
        "ticket_id": "TKT-000001",
        "status": "PENDING_REVIEW",
        "employee_id": "E1003",
        "escalation_code": "EXCEPTION_CLAIMED",
        "created_at": "2026-11-26T00:00:00Z",
    }


# --- Idempotency ----------------------------------------------------------------


def test_same_request_with_different_reason_wording_returns_the_same_ticket(
    repo: InMemoryRepository,
) -> None:
    first = _flag(repo, "EXCEPTION_CLAIMED: coffee spill on laptop")
    second = _flag(repo, "TOOL_NEEDS_REVIEW: COOLDOWN_NOT_ELAPSED, but spill claimed")

    assert second == first  # same ticket, first code kept


@pytest.mark.parametrize(
    "variant",
    [SPILL.upper(), f"  {SPILL}  ", SPILL.replace(" ", "   "), SPILL.replace(" ", "\n")],
)
def test_request_text_is_normalized_for_the_key(repo: InMemoryRepository, variant: str) -> None:
    assert _flag(repo, request=variant).ticket_id == _flag(repo).ticket_id


def test_a_different_request_creates_a_new_ticket(repo: InMemoryRepository) -> None:
    first = _flag(repo)
    second = _flag(repo, request="I also need a headset")

    assert second.ticket_id != first.ticket_id


def test_the_same_request_from_another_employee_creates_a_new_ticket(
    repo: InMemoryRepository,
) -> None:
    first = _flag(repo, employee_id="E1003")
    second = _flag(repo, employee_id="E1001")

    assert second.ticket_id != first.ticket_id


def test_request_key_is_a_stable_hash_of_the_normalized_text() -> None:
    key = request_key("I need  a Monitor ")

    assert key == request_key("i need a monitor")
    assert len(key) == 64
    assert key != request_key("i need a monitors")


# --- Reason parsing (R-25) -----------------------------------------------------------


@pytest.mark.parametrize("code", list(EscalationCode))
def test_every_escalation_code_is_parsed(code: EscalationCode) -> None:
    assert parse_escalation_code(f"{code.value}: explanation") is code


@pytest.mark.parametrize(
    ("reason", "code"),
    [
        ("exception_claimed: lower case", EscalationCode.EXCEPTION_CLAIMED),
        ("  AMBIGUOUS_ITEM :spaces around the colon", EscalationCode.AMBIGUOUS_ITEM),
        ("MULTIPLE_ITEMS:", EscalationCode.MULTIPLE_ITEMS),
        ("NOT_A_CODE: something", EscalationCode.OTHER),
        ("no code at all", EscalationCode.OTHER),
        ("EXCEPTION_CLAIMED - no colon", EscalationCode.OTHER),
        ("Note: EXCEPTION_CLAIMED later in the text", EscalationCode.OTHER),
    ],
)
def test_reason_parsing_edge_cases(reason: str, code: EscalationCode) -> None:
    assert parse_escalation_code(reason) is code


def test_unknown_code_is_stored_as_other(repo: InMemoryRepository) -> None:
    assert _flag(repo, "PLEASE_HELP: unsure").escalation_code is EscalationCode.OTHER


# --- Rejected input (R-24, R-25) ---------------------------------------------------


@pytest.mark.parametrize("reason", ["", "   ", "\n\t"])
def test_empty_reason_is_rejected(repo: InMemoryRepository, reason: str) -> None:
    error = _error(flag_for_human_review("E1003", SPILL, reason, repo=repo, clock=CLOCK))

    assert error.error.code is ErrorCode.INVALID_INPUT
    assert "reason" in error.error.message


@pytest.mark.parametrize("request_text", ["", "   "])
def test_empty_request_is_rejected(repo: InMemoryRepository, request_text: str) -> None:
    error = _error(flag_for_human_review("E1003", request_text, "OTHER: x", repo=repo, clock=CLOCK))

    assert error.error.code is ErrorCode.INVALID_INPUT
    assert "request" in error.error.message


@pytest.mark.parametrize("field", ["request", "reason"])
def test_overlong_text_is_rejected(repo: InMemoryRepository, field: str) -> None:
    texts = {"request": SPILL, "reason": "OTHER: x"} | {field: "x" * (MAX_TEXT_LENGTH + 1)}
    result = flag_for_human_review(
        "E1003", texts["request"], texts["reason"], repo=repo, clock=CLOCK
    )

    error = _error(result).error
    assert error.code is ErrorCode.INVALID_INPUT
    assert field in error.message
    assert len(error.message) < 200


def test_text_at_the_length_limit_is_accepted(repo: InMemoryRepository) -> None:
    _flag(repo, request="x" * MAX_TEXT_LENGTH, reason="OTHER: " + "y" * (MAX_TEXT_LENGTH - 7))


@pytest.mark.parametrize(
    ("employee_id", "request_text", "reason"),
    [
        ("bad", SPILL, "OTHER: x"),
        (None, SPILL, "OTHER: x"),
        ("E1003", None, "OTHER: x"),
        ("E1003", SPILL, None),
        ("E1003", SPILL, ["OTHER: x"]),
    ],
)
def test_malformed_or_non_string_input_is_rejected(
    repo: InMemoryRepository, employee_id: Any, request_text: Any, reason: Any
) -> None:
    result = flag_for_human_review(employee_id, request_text, reason, repo=repo, clock=CLOCK)
    assert _error(result).error.code is ErrorCode.INVALID_INPUT


def test_rejected_input_writes_no_ticket(repo: InMemoryRepository) -> None:
    flag_for_human_review("E1003", SPILL, "", repo=repo, clock=CLOCK)

    assert _flag(repo).ticket_id == "TKT-000001"


# --- Never raises (R-24) -----------------------------------------------------------


def test_repository_failure_becomes_internal_error_without_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class BrokenRepo(InMemoryRepository):
        def get_or_create_open_ticket(self, new: NewTicket) -> tuple[ReviewTicket, bool]:
            raise RuntimeError("connection refused: password=hunter2")

    result = flag_for_human_review(
        "E1003", SPILL, "OTHER: x", repo=BrokenRepo([], [], []), clock=CLOCK
    )
    error = _error(result).error

    assert error.code is ErrorCode.INTERNAL
    assert "hunter2" not in error.message
    assert "hunter2" in caplog.text


# --- Text that Postgres cannot store, and padding ------------------------------------


def test_nul_characters_are_removed_so_the_escalation_still_succeeds(
    repo: InMemoryRepository,
) -> None:
    # Postgres rejects NUL in text columns; rejecting the input instead would make
    # the request impossible to escalate, since the agent would resend the same text.
    with_nul = _flag(repo, reason="OTHER: odd\x00text", request=f"{SPILL}\x00")
    without_nul = _flag(repo, reason="OTHER: odd text", request=SPILL)

    assert with_nul.ticket_id == without_nul.ticket_id  # same key once NUL is removed
    stored, _ = repo.get_or_create_open_ticket(
        NewTicket(
            employee_id="E1003",
            request_text="probe",
            request_key=request_key(SPILL),
            escalation_code=EscalationCode.OTHER,
            reason="probe",
            created_at=datetime(2027, 1, 1, tzinfo=UTC),
        )
    )
    assert "\x00" not in stored.request_text
    assert "\x00" not in stored.reason


@pytest.mark.parametrize("text", ["\x00", " \x00 \x00 "])
def test_request_of_only_nul_and_spaces_is_empty(repo: InMemoryRepository, text: str) -> None:
    result = flag_for_human_review("E1003", text, "OTHER: x", repo=repo, clock=CLOCK)
    assert _error(result).error.code is ErrorCode.INVALID_INPUT


def test_length_limit_applies_to_the_stored_text_not_the_padding(
    repo: InMemoryRepository,
) -> None:
    padded = "\n " + "x" * MAX_TEXT_LENGTH + " \n\n   "
    _flag(repo, request=padded, reason="OTHER: x" + " " * 50)
