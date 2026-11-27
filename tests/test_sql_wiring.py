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
from server.config import Settings
from server.db.seed import seed
from server.main import create_app
from server.repositories.sql import SqlRepository, sql_repositories
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
