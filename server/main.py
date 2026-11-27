"""FastAPI application: REST endpoints, Swagger UI, and the MCP app mounted at /mcp."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy import Engine, create_engine

from server.clock import Clock, SystemClock
from server.config import Settings
from server.mcp_tools import build_mcp_server
from server.repositories.base import RepositoryProvider
from server.repositories.sql import sql_repositories
from server.rest import build_router


def create_app(
    settings: Settings | None = None,
    *,
    repositories: RepositoryProvider | None = None,
    clock: Clock | None = None,
) -> FastAPI:
    """Build the app. Tests inject an in-memory repository and a fixed clock;
    by default it uses Postgres from `settings` and the system clock."""
    settings = settings or Settings()
    engine: Engine | None = None
    if repositories is None:
        # Owned by this app: connects lazily, on the first request, and its pool
        # is closed at shutdown. Injected repositories are the caller's to clean up.
        engine = create_engine(settings.database_url, pool_pre_ping=True)
        repositories = sql_repositories(engine)
    clock = clock or SystemClock()

    mcp = build_mcp_server(repositories, clock)
    mcp_app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=settings.mcp_allowed_hosts,
            allowed_origins=settings.mcp_allowed_origins,
        ),
    )

    # Starlette does not run a mounted app's lifespan, so the MCP session
    # manager is started from FastAPI's own lifespan (PLAN.md D1).
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
        try:
            async with mcp.session_manager.run():
                yield
        finally:
            if engine is not None:
                engine.dispose()

    app = FastAPI(
        title="EquipDesk API",
        version="0.1.0",
        description="IT equipment request handling. The same operations are exposed "
        "as MCP tools at `/mcp`.",
        lifespan=lifespan,
    )
    app.state.engine = engine

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(build_router(repositories, clock))

    # Mounted last at the root so the MCP endpoint is exactly /mcp (no redirect)
    # while FastAPI's own routes (/health, /docs, /v1/...) still match first.
    app.mount("/", mcp_app)

    return app


app = create_app()
