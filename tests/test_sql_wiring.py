"""EQP-5: the adapters on real Postgres, one session per request or tool call.

A session shared across calls would stay broken after a database error
(PendingRollbackError) and turn every later call into INTERNAL (SAN-32 note).
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from server.clock import FixedClock
from server.codes import ErrorCode
from server.config import Settings
from server.db.seed import seed
from server.main import create_app
from server.repositories.sql import SqlRepository, sql_repositories
from server.services.results import ErrorResult, call_with_repository, error_result
from tests.conftest import TODAY, provide


def test_each_scope_gets_a_fresh_session(db_engine: Engine) -> None:
    repositories = sql_repositories(db_engine)

    with repositories() as first, repositories() as second:
        assert isinstance(first, SqlRepository)
        assert isinstance(second, SqlRepository)
        assert first.session is not second.session


def test_a_database_error_does_not_break_the_next_call(db_engine: Engine) -> None:
    repositories = sql_repositories(db_engine)

    with repositories() as repo:
        assert isinstance(repo, SqlRepository)
        # The services catch repository errors (R-24), so the scope exits normally
        # with the session's transaction already failed.
        with pytest.raises(DBAPIError):
            repo.session.execute(text("select 1 / 0"))

    with repositories() as repo:
        assert repo.get_employee("E1001") is None  # usable: no PendingRollbackError


def test_scope_closes_its_session(db_engine: Engine) -> None:
    repositories = sql_repositories(db_engine)

    with repositories() as repo:
        assert isinstance(repo, SqlRepository)
        session = repo.session
        session.execute(text("select 1"))
        assert session.in_transaction()

    assert not session.in_transaction()


def test_default_app_reads_from_the_configured_database(db_engine: Engine) -> None:
    url = db_engine.url.render_as_string(hide_password=False)
    app = create_app(Settings(database_url=url), clock=FixedClock(TODAY))

    with TestClient(app) as client:
        response = client.get("/v1/employees/E9999")

    assert response.status_code == 404
    assert response.json()["error"]["reason_code"] == "EMPLOYEE_NOT_FOUND"


def test_rest_serves_seeded_postgres_data(db_session: Session) -> None:
    seed(db_session, TODAY)
    app = create_app(repositories=provide(SqlRepository(db_session)), clock=FixedClock(TODAY))

    with TestClient(app) as client:
        body = client.get("/v1/employees/E1001").json()

    assert body["name"] == "Asha Rao"
    assert [(e["item"], e["acquired_on"]) for e in body["equipment"]] == [
        ("monitor", "2022-03-26"),
        ("laptop", "2025-05-26"),
    ]


def test_app_closes_its_database_pool_on_shutdown(db_engine: Engine) -> None:
    url = db_engine.url.render_as_string(hide_password=False)
    app = create_app(Settings(database_url=url), clock=FixedClock(TODAY))
    closed: list[object] = []
    event.listen(app.state.engine, "close", lambda conn, record: closed.append(conn))

    with TestClient(app) as client:
        client.get("/v1/employees/E9999")
        assert closed == []

    assert len(closed) == 1  # the pooled connection was closed, not left idle


def test_app_with_injected_repositories_owns_no_engine(db_session: Session) -> None:
    app = create_app(repositories=provide(SqlRepository(db_session)), clock=FixedClock(TODAY))

    assert getattr(app.state, "engine", None) is None


def test_rest_serves_seeded_postgres_policies(db_session: Session) -> None:
    seed(db_session, TODAY)
    app = create_app(repositories=provide(SqlRepository(db_session)), clock=FixedClock(TODAY))

    with TestClient(app) as client:
        body = client.get("/v1/policies/Contractor").json()

    assert body["role"] == "contractor"
    entitled = {p["item"]: p["refresh_months"] for p in body["policies"] if p["entitled"]}
    assert entitled == {"headset": 24, "keyboard": 24, "mouse": 24}


def test_rest_checks_eligibility_against_seeded_postgres(db_session: Session) -> None:
    seed(db_session, TODAY)
    app = create_app(repositories=provide(SqlRepository(db_session)), clock=FixedClock(TODAY))

    with TestClient(app) as client:
        approve = client.post("/v1/eligibility", json={"employee_id": "E1001", "item": "Screen"})
        deny = client.post("/v1/eligibility", json={"employee_id": "E1002", "item": "monitor"})

    assert approve.json()["reason_code"] == "WITHIN_POLICY"
    assert approve.json()["item"] == "monitor"
    assert deny.json()["reason_code"] == "COOLDOWN_NOT_ELAPSED"
    assert deny.json()["next_eligible_date"] == "2029-02-26"


def test_scope_commits_a_ticket_so_a_later_session_sees_it(db_engine: Engine) -> None:
    # Tickets must survive the request that created them. This writes for real
    # (the scope commits), so it uses its own employee and cleans up after itself.
    repositories = sql_repositories(db_engine)
    app = create_app(repositories=repositories, clock=FixedClock(TODAY))
    body = {"employee_id": "E8888", "request": "commit check", "reason": "OTHER: test"}
    try:
        with TestClient(app) as client:
            created = client.post("/v1/review-tickets", json=body).json()
            replay = client.post("/v1/review-tickets", json=body).json()

        assert replay == created
        with db_engine.connect() as conn:
            rows = conn.execute(
                text(
                    "select status, escalation_code from review_tickets where employee_id = 'E8888'"
                )
            ).all()
        assert [tuple(row) for row in rows] == [("PENDING_REVIEW", "OTHER")]
    finally:
        with db_engine.begin() as conn:
            conn.execute(text("delete from review_tickets where employee_id = 'E8888'"))


def test_scope_rolls_back_when_the_call_fails(db_engine: Engine) -> None:
    repositories = sql_repositories(db_engine)
    with pytest.raises(RuntimeError), repositories() as repo:
        assert isinstance(repo, SqlRepository)
        repo.session.execute(
            text(
                "insert into review_tickets (employee_id, request_text, request_key, "
                "escalation_code, reason, status, created_at) values ('E7777', 'r', 'k', "
                "'OTHER', 'OTHER: r', 'PENDING_REVIEW', now())"
            )
        )
        raise RuntimeError("the call failed after writing")

    with db_engine.connect() as conn:
        count = conn.execute(
            text("select count(*) from review_tickets where employee_id = 'E7777'")
        ).scalar_one()
    assert count == 0


def test_nul_in_a_request_is_saved_on_postgres(db_engine: Engine) -> None:
    app = create_app(repositories=sql_repositories(db_engine), clock=FixedClock(TODAY))
    body = {"employee_id": "E8887", "request": "bad\u0000byte", "reason": "OTHER: nul\u0000"}
    try:
        with TestClient(app) as client:
            response = client.post("/v1/review-tickets", json=body)

        assert response.status_code == 200, response.json()
        with db_engine.connect() as conn:
            stored = conn.execute(
                text("select request_text from review_tickets where employee_id = 'E8887'")
            ).scalar_one()
        assert stored == "badbyte"
    finally:
        with db_engine.begin() as conn:
            conn.execute(text("delete from review_tickets where employee_id = 'E8887'"))


def test_an_error_result_commits_nothing(db_engine: Engine) -> None:
    # A row written before the service failed must not be saved while the caller
    # is told INTERNAL.
    def write_then_fail(repo: object) -> ErrorResult:
        assert isinstance(repo, SqlRepository)
        repo.session.execute(
            text(
                "insert into review_tickets (employee_id, request_text, request_key, "
                "escalation_code, reason, status, created_at) values ('E7776', 'r', 'k', "
                "'OTHER', 'OTHER: r', 'PENDING_REVIEW', now())"
            )
        )
        return error_result(ErrorCode.INTERNAL, "failed after writing")

    result = call_with_repository(sql_repositories(db_engine), write_then_fail)

    assert isinstance(result, ErrorResult)
    assert result.error.message == "failed after writing"
    with db_engine.connect() as conn:
        count = conn.execute(
            text("select count(*) from review_tickets where employee_id = 'E7776'")
        ).scalar_one()
    assert count == 0
