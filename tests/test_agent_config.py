"""EQP-9: agent configuration from FOUNDRY_* environment variables (PLAN.md D7, §13 item 1)."""

from pathlib import Path

import pytest

from agent.config import AgentSettings, ConfigError, load_settings

SECRET = "sk-test-not-a-real-key-123"
ENDPOINT = "https://example-resource.services.ai.azure.com/anthropic/"
REQUIRED = ("FOUNDRY_API_KEY", "FOUNDRY_ENDPOINT", "FOUNDRY_MODEL_DEPLOYMENT")


@pytest.fixture
def foundry_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.setenv("FOUNDRY_API_KEY", SECRET)
    monkeypatch.setenv("FOUNDRY_ENDPOINT", ENDPOINT)
    monkeypatch.setenv("FOUNDRY_MODEL_DEPLOYMENT", "claude-opus-5-5-fast")
    for name in ("FOUNDRY_EFFORT", "FOUNDRY_MAX_TOKENS", "MCP_URL"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.fixture
def no_foundry_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in REQUIRED:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_settings_load_from_the_environment(foundry_env: pytest.MonkeyPatch) -> None:
    settings = load_settings(env_file=None)

    assert settings.foundry_api_key.get_secret_value() == SECRET
    assert settings.foundry_endpoint == ENDPOINT
    assert settings.foundry_model_deployment == "claude-opus-5-5-fast"


def test_defaults(foundry_env: pytest.MonkeyPatch) -> None:
    settings = load_settings(env_file=None)

    # Opus 5.5's default effort, set explicitly; no temperature (400 on this model).
    assert settings.foundry_effort == "medium"
    assert settings.foundry_max_tokens == 16000
    assert settings.mcp_url == "http://localhost:8000/mcp"


def test_missing_variables_raise_a_clear_error_naming_each(
    no_foundry_env: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ConfigError) as caught:
        load_settings(env_file=None)

    message = str(caught.value)
    for name in REQUIRED:
        assert name in message
    assert ".env.example" in message


@pytest.mark.parametrize("name", REQUIRED)
def test_each_required_variable_is_reported_when_missing(
    foundry_env: pytest.MonkeyPatch, name: str
) -> None:
    foundry_env.delenv(name)

    with pytest.raises(ConfigError) as caught:
        load_settings(env_file=None)

    assert name in str(caught.value)
    assert [n for n in REQUIRED if n in str(caught.value)] == [name]


@pytest.mark.parametrize("value", ["", "   "])
def test_empty_values_count_as_missing(foundry_env: pytest.MonkeyPatch, value: str) -> None:
    # docker compose passes ${FOUNDRY_API_KEY:-}, i.e. an empty string, when unset.
    foundry_env.setenv("FOUNDRY_API_KEY", value)

    with pytest.raises(ConfigError, match="FOUNDRY_API_KEY"):
        load_settings(env_file=None)


def test_the_key_never_appears_in_errors_or_repr(foundry_env: pytest.MonkeyPatch) -> None:
    assert SECRET not in repr(load_settings(env_file=None))

    foundry_env.setenv("FOUNDRY_EFFORT", "turbo")
    with pytest.raises(ConfigError) as caught:
        load_settings(env_file=None)

    assert "FOUNDRY_EFFORT" in str(caught.value)
    assert SECRET not in str(caught.value)


@pytest.mark.parametrize(
    "value",
    [
        "https://example-resource.services.ai.azure.com/anthropic",
        "https://example-resource.services.ai.azure.com/anthropic/",
        " https://example-resource.services.ai.azure.com/anthropic/ ",
        # The portal's Target URI may include the Messages path.
        "https://example-resource.services.ai.azure.com/anthropic/v1/messages",
        "https://example-resource.services.ai.azure.com/anthropic/v1/messages/",
    ],
)
def test_endpoint_is_normalized_to_the_base_url(
    foundry_env: pytest.MonkeyPatch, value: str
) -> None:
    foundry_env.setenv("FOUNDRY_ENDPOINT", value)

    assert load_settings(env_file=None).foundry_endpoint == ENDPOINT


@pytest.mark.parametrize(
    "value",
    [
        "https://example-resource.services.ai.azure.com/anthropic/v1",
        "https://example-resource.services.ai.azure.com/anthropic/v1/",
        "https://example-resource.services.ai.azure.com/anthropic/?api-version=2026-10-21",
        "https://example-resource.services.ai.azure.com/anthropic/v1/messages?api-version=1",
    ],
)
def test_endpoint_variants_of_the_target_uri_are_normalized(
    foundry_env: pytest.MonkeyPatch, value: str
) -> None:
    # Otherwise the SDK appends v1/messages again: /anthropic/v1/v1/messages -> 404.
    foundry_env.setenv("FOUNDRY_ENDPOINT", value)

    assert load_settings(env_file=None).foundry_endpoint == ENDPOINT


@pytest.mark.parametrize(
    "value",
    [
        "https://example-resource.services.ai.azure.com/",
        "https://example-resource.services.ai.azure.com/openai/v1",
        "https://example-resource.services.ai.azure.com/anthropicx/",
        "https:///anthropic/",
    ],
)
def test_endpoint_must_be_a_foundry_anthropic_url(
    foundry_env: pytest.MonkeyPatch, value: str
) -> None:
    foundry_env.setenv("FOUNDRY_ENDPOINT", value)

    with pytest.raises(ConfigError, match="FOUNDRY_ENDPOINT"):
        load_settings(env_file=None)


def test_an_unedited_copy_of_env_example_reports_every_variable_as_missing(
    no_foundry_env: pytest.MonkeyPatch,
) -> None:
    # `cp .env.example .env` without filling it in must name the variables, not
    # send a request to a placeholder endpoint.
    example = Path(__file__).resolve().parent.parent / ".env.example"

    with pytest.raises(ConfigError) as caught:
        load_settings(env_file=str(example))

    for name in REQUIRED:
        assert name in str(caught.value)


@pytest.mark.parametrize("value", ["example-resource", "http://example.com/anthropic/", "ftp://x"])
def test_endpoint_must_be_an_https_url(foundry_env: pytest.MonkeyPatch, value: str) -> None:
    foundry_env.setenv("FOUNDRY_ENDPOINT", value)

    with pytest.raises(ConfigError, match="FOUNDRY_ENDPOINT"):
        load_settings(env_file=None)


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max"])
def test_every_effort_level_is_accepted(foundry_env: pytest.MonkeyPatch, effort: str) -> None:
    foundry_env.setenv("FOUNDRY_EFFORT", effort)

    assert load_settings(env_file=None).foundry_effort == effort


def test_env_file_is_read(no_foundry_env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"FOUNDRY_API_KEY={SECRET}\nFOUNDRY_ENDPOINT={ENDPOINT}\nFOUNDRY_MODEL_DEPLOYMENT=opus\n"
    )

    assert load_settings(env_file=str(env_file)).foundry_model_deployment == "opus"


def test_settings_class_reads_no_env_file_by_default() -> None:
    # Tests and CI must never pick up a developer's real .env by accident: only
    # load_settings() (the CLI path) reads it.
    assert AgentSettings.model_config.get("env_file") is None
