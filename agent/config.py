"""Agent configuration, read from environment variables (PLAN.md D7, §13 item 1).

The Foundry values come from the shell, a local `.env` (git-ignored; see
`.env.example`) or, in CI, GitHub secrets. They are never baked into an image.
"""

from typing import Literal
from urllib.parse import urlsplit

from pydantic import SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

type Effort = Literal["low", "medium", "high", "xhigh", "max"]

REQUIRED = ("FOUNDRY_API_KEY", "FOUNDRY_ENDPOINT", "FOUNDRY_MODEL_DEPLOYMENT")
_TARGET_PATHS: tuple[list[str], ...] = ([], ["v1"], ["v1", "messages"])
"""What may follow /anthropic in the Target URI as pasted from the portal."""
_BLANK = "must not be empty"


class ConfigError(Exception):
    """The agent cannot start: configuration is missing or invalid. Never holds values."""


class AgentSettings(BaseSettings):
    # No env_file here: only load_settings() (the CLI path) reads `.env`, so tests
    # and CI never pick up a developer's real credentials by accident.
    model_config = SettingsConfigDict(extra="ignore", str_strip_whitespace=True)

    foundry_api_key: SecretStr
    """The Key from the Foundry deployment's Details tab (API-key auth)."""
    foundry_endpoint: str
    """The deployment's Target URI: https://<resource>.services.ai.azure.com/anthropic/."""
    foundry_model_deployment: str
    """The Foundry deployment name, sent as `model` (here claude-opus-5-5-fast)."""
    foundry_effort: Effort = "medium"
    """Claude Opus 5.5's default, set explicitly. There is no
    temperature setting: the model rejects non-default sampling parameters."""
    foundry_max_tokens: int = 16000
    """Per model call, thinking included (non-streaming requests)."""
    mcp_url: str = "http://localhost:8000/mcp"
    """The EquipDesk MCP endpoint; compose sets http://api:8000/mcp."""

    @field_validator(
        "foundry_api_key", "foundry_endpoint", "foundry_model_deployment", mode="before"
    )
    @classmethod
    def _not_blank(cls, value: object) -> object:
        # docker compose passes an unset ${FOUNDRY_API_KEY:-} as an empty string.
        if isinstance(value, str) and not value.strip():
            raise ValueError(_BLANK)
        return value

    @field_validator("foundry_endpoint")
    @classmethod
    def _base_url(cls, value: str) -> str:
        """https://<host>/anthropic/, whatever the pasted Target URI adds after
        /anthropic (v1, v1/messages, a query string); the SDK appends v1/messages."""
        parts = urlsplit(value)
        segments = [segment for segment in parts.path.split("/") if segment]
        if (
            parts.scheme != "https"
            or not parts.hostname
            or segments[:1] != ["anthropic"]
            or segments[1:] not in _TARGET_PATHS
        ):
            raise ValueError(
                "must be the deployment's Target URI, "
                "https://<resource>.services.ai.azure.com/anthropic/"
            )
        return f"https://{parts.netloc}/anthropic/"


def load_settings(env_file: str | None = ".env") -> AgentSettings:
    """The agent's settings, or a ConfigError naming each missing or invalid variable."""
    try:
        return AgentSettings(_env_file=env_file)  # type: ignore[call-arg]
    except ValidationError as exc:
        missing: list[str] = []
        invalid: list[str] = []
        for error in exc.errors():
            name = str(error["loc"][0]).upper()
            if error["type"] == "missing" or str(error["msg"]).endswith(_BLANK):
                missing.append(name)
            else:
                # The message only, never error["input"]: it could be the key.
                invalid.append(f"{name} ({error['msg']})")
        parts = []
        if missing:
            parts.append("missing " + ", ".join(missing))
        if invalid:
            parts.append("invalid " + ", ".join(invalid))
        raise ConfigError(
            f"Agent configuration: {'; '.join(parts)}. Set them in the shell or in a "
            ".env file (copy .env.example)."
        ) from None
