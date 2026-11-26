"""Backend configuration, read from environment variables (12-factor)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Host headers the MCP endpoint accepts (DNS-rebinding protection).
    # `api` is the compose service name the agent uses to reach the backend.
    mcp_allowed_hosts: list[str] = ["127.0.0.1:*", "localhost:*", "api:*"]
    mcp_allowed_origins: list[str] = ["http://127.0.0.1:*", "http://localhost:*"]
