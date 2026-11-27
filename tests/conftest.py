"""Shared test fixtures."""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from datetime import date
from typing import Any

import pytest
import uvicorn
from fastapi.testclient import TestClient
from mcp.types import CallToolResult, TextContent
from sqlalchemy import Engine, create_engine, make_url, text
from sqlalchemy.orm import Session
from starlette.types import ASGIApp

from server.clock import FixedClock
from server.config import Settings
from server.db.migrate import upgrade_to_head
from server.db.seed_data import build_seed_data
from server.main import create_app
from server.repositories.base import Repository, RepositoryProvider
from server.repositories.memory import InMemoryRepository

TEST_DATABASE = "equipdesk_test"
TODAY = date(2026, 11, 26)
"""The fixed "today" for adapter tests; seed data dates are relative to it."""


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


@pytest.fixture
def memory_repo() -> InMemoryRepository:
    return InMemoryRepository.from_seed(build_seed_data(TODAY))


def provide(repo: Repository) -> RepositoryProvider:
    """A repository provider that always hands out the same repository."""

    def scope() -> AbstractContextManager[Repository]:
        return nullcontext(repo)

    return scope


@pytest.fixture
def client(memory_repo: InMemoryRepository) -> Iterator[TestClient]:
    """The app over the in-memory seed data and a fixed clock, for REST tests."""
    app = create_app(repositories=provide(memory_repo), clock=FixedClock(TODAY))
    with TestClient(app) as test_client:
        yield test_client


def tool_json(result: CallToolResult) -> Any:
    """The JSON an MCP tool returned in its single text block."""
    texts = [block.text for block in result.content if isinstance(block, TextContent)]
    assert len(texts) == 1, result
    return json.loads(texts[0])


@contextmanager
def serve(app: ASGIApp) -> Iterator[str]:
    """Run an ASGI app (including its lifespan) with uvicorn on a free local port."""
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("uvicorn did not start within 10s")
        time.sleep(0.05)

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.fixture
def live_server_url(memory_repo: InMemoryRepository) -> Iterator[str]:
    """The real app on a free local port, backed by the in-memory seed data and a
    fixed clock.

    The MCP Streamable HTTP client talks real HTTP, so this proves the MCP app
    is mounted inside FastAPI and that its session manager lifespan runs.
    """
    with serve(create_app(repositories=provide(memory_repo), clock=FixedClock(TODAY))) as url:
        yield url


@pytest.fixture(scope="session")
def db_engine() -> Iterator[Engine]:
    """A fresh, empty test database (on the compose Postgres), migrated to head.

    Creating it from scratch on every run also proves that the migrations
    work on an empty database.
    """
    url = make_url(Settings().database_url)
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DATABASE}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{TEST_DATABASE}"'))
    admin.dispose()

    test_url = url.set(database=TEST_DATABASE)
    upgrade_to_head(test_url.render_as_string(hide_password=False))
    engine = create_engine(test_url)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine: Engine) -> Iterator[Session]:
    """A session whose work is rolled back after the test, commits included."""
    with db_engine.connect() as conn:
        transaction = conn.begin()
        session = Session(bind=conn, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            session.close()
            transaction.rollback()
