"""FastAPI application: REST endpoints, Swagger UI, and the MCP app mounted at /mcp."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from mcp.server.transport_security import TransportSecuritySettings

from server.config import Settings
from server.mcp_tools import build_mcp_server


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    mcp = build_mcp_server()
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
        async with mcp.session_manager.run():
            yield

    app = FastAPI(
        title="EquipDesk API",
        version="0.1.0",
        description="IT equipment request handling. The same operations are exposed "
        "as MCP tools at `/mcp`.",
        lifespan=lifespan,
    )

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    # Mounted last at the root so the MCP endpoint is exactly /mcp (no redirect)
    # while FastAPI's own routes (/health, /docs, /v1/...) still match first.
    app.mount("/", mcp_app)

    return app


app = create_app()
