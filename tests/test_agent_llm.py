"""EQP-9: the Foundry LLM client behind the LLMClient interface (PLAN.md D7).

The real client is exercised through a mock HTTP transport: these tests check the
exact request the Anthropic SDK sends to Foundry and how the reply is parsed,
with no network and no credentials.
"""

import json
from collections.abc import Callable
from typing import Any

import anthropic
import httpx2
import pytest
from anthropic import DefaultAsyncHttpxClient
from anthropic.types import ToolParam
from pydantic import SecretStr

from agent.config import AgentSettings
from agent.llm import FoundryLLMClient, LLMClient, LLMError, LLMResponse, ToolCall
from tests.fakes import ScriptedLLM, message

SECRET = "sk-test-not-a-real-key-123"
ENDPOINT = "https://example-resource.services.ai.azure.com/anthropic/"
TOOLS: list[ToolParam] = [
    {
        "name": "get_employee_info",
        "description": "Look up an employee.",
        "input_schema": {
            "type": "object",
            "properties": {"employee_id": {"type": "string"}},
            "required": ["employee_id"],
        },
    }
]
REPLY = message(
    [
        {"type": "thinking", "thinking": "Look up the employee first.", "signature": "sig-abc"},
        {"type": "text", "text": "Checking the employee record."},
        {
            "type": "tool_use",
            "id": "toolu_1",
            "name": "get_employee_info",
            "input": {"employee_id": "E1001"},
        },
    ],
    stop_reason="tool_use",
)


def _settings(**overrides: Any) -> AgentSettings:
    values: dict[str, Any] = {
        "foundry_api_key": SecretStr(SECRET),
        "foundry_endpoint": ENDPOINT,
        "foundry_model_deployment": "my-opus-deployment",
    }
    return AgentSettings(**(values | overrides))


def _client(
    handler: Callable[[httpx2.Request], httpx2.Response], **overrides: Any
) -> FoundryLLMClient:
    http = DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler))
    return FoundryLLMClient(_settings(**overrides), http_client=http, max_retries=0)


class Recorder:
    """Answers every request with a fixed reply and keeps the requests."""

    def __init__(self, status: int = 200, body: Any = REPLY) -> None:
        self.requests: list[httpx2.Request] = []
        self.status = status
        self.body = body

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return httpx2.Response(self.status, json=self.body)

    @property
    def body_sent(self) -> dict[str, Any]:
        sent: dict[str, Any] = json.loads(self.requests[-1].content)
        return sent


async def _complete(client: LLMClient) -> LLMResponse:
    return await client.complete(
        system="You handle IT equipment requests.",
        messages=[{"role": "user", "content": "E1001: I need a second monitor"}],
        tools=TOOLS,
    )


# The request


@pytest.mark.anyio
async def test_request_goes_to_the_foundry_messages_endpoint_with_the_key() -> None:
    recorder = Recorder()
    await _complete(_client(recorder))

    request = recorder.requests[0]
    assert str(request.url) == ENDPOINT + "v1/messages"
    assert request.headers["x-api-key"] == SECRET
    assert request.headers["anthropic-version"]


@pytest.mark.anyio
async def test_request_body_uses_the_deployment_tools_and_explicit_effort() -> None:
    recorder = Recorder()
    await _complete(_client(recorder, foundry_effort="high", foundry_max_tokens=4000))

    body = recorder.body_sent
    assert body["model"] == "my-opus-deployment"
    assert body["max_tokens"] == 4000
    assert body["system"] == "You handle IT equipment requests."
    assert body["messages"] == [{"role": "user", "content": "E1001: I need a second monitor"}]
    assert body["tools"] == TOOLS
    assert body["output_config"] == {"effort": "high"}
    # Summarized thinking is the visible Thought before each action (Opus 5.5
    # returns text between tool calls as thinking blocks, empty by default).
    assert body["thinking"] == {"type": "adaptive", "display": "summarized"}


@pytest.mark.anyio
async def test_a_call_without_tools_sends_no_tools_field() -> None:
    # The reflection critic (EQP-12) calls the model without tools.
    recorder = Recorder()
    await _client(recorder).complete(
        system="You review drafts.", messages=[{"role": "user", "content": "x"}], tools=[]
    )

    assert "tools" not in recorder.body_sent


@pytest.mark.anyio
async def test_request_sends_no_sampling_parameters_forced_tool_choice_or_speed() -> None:
    # Opus 5.5 rejects non-default temperature/top_p/top_k and forced tool_choice
    # (both 400), so determinism comes from the deterministic tools, not sampling.
    # On Foundry fast mode is the -fast deployment; sending `speed` is an error.
    recorder = Recorder()
    await _complete(_client(recorder))

    body = recorder.body_sent
    for field in ("temperature", "top_p", "top_k", "tool_choice", "speed"):
        assert field not in body


# The reply


@pytest.mark.anyio
async def test_reply_is_parsed_into_thought_tool_calls_and_usage() -> None:
    response = await _complete(_client(Recorder()))

    assert response.thought == "Look up the employee first.\nChecking the employee record."
    assert response.thinking == "Look up the employee first."
    assert response.text == "Checking the employee record."
    assert response.tool_calls == [
        ToolCall(id="toolu_1", name="get_employee_info", arguments={"employee_id": "E1001"})
    ]
    assert response.stop_reason == "tool_use"
    assert response.usage.input_tokens == 1200
    assert response.usage.output_tokens == 85
    assert response.latency_ms >= 0


@pytest.mark.anyio
async def test_reply_content_is_kept_unchanged_for_the_next_turn() -> None:
    # Thinking blocks must go back exactly as received (signature included), or
    # the next request fails the preserved-thinking check.
    response = await _complete(_client(Recorder()))

    assert response.content == REPLY["content"]
    assert response.assistant_message() == {"role": "assistant", "content": REPLY["content"]}


@pytest.mark.anyio
async def test_empty_thinking_blocks_add_nothing_to_the_thought() -> None:
    reply = message(
        [
            {"type": "thinking", "thinking": "", "signature": "sig"},
            {"type": "text", "text": "Approved."},
        ]
    )
    response = await _complete(_client(Recorder(body=reply)))

    assert response.thought == "Approved."
    assert response.tool_calls == []
    assert response.stop_reason == "end_turn"


@pytest.mark.anyio
async def test_a_refusal_is_returned_not_raised() -> None:
    # The agent turns a refusal into an escalation (EQP-10/11); Foundry has no
    # server-side fallback.
    details = {"type": "refusal", "category": "cyber", "explanation": "Declined."}
    reply = message([], stop_reason="refusal", stop_details=details)
    response = await _complete(_client(Recorder(body=reply)))

    assert response.stop_reason == "refusal"
    assert response.tool_calls == []
    # The category says which safety classifier declined: kept for the trace.
    assert response.refusal == "cyber: Declined."


@pytest.mark.anyio
async def test_a_refusal_without_details_still_says_so() -> None:
    response = await _complete(_client(Recorder(body=message([], stop_reason="refusal"))))

    assert response.refusal == "no category given"


@pytest.mark.anyio
async def test_other_replies_have_no_refusal() -> None:
    assert (await _complete(_client(Recorder()))).refusal is None


# Errors


@pytest.mark.anyio
async def test_http_errors_become_llm_errors_without_the_key() -> None:
    error = {"type": "error", "error": {"type": "authentication_error", "message": "bad key"}}
    with pytest.raises(LLMError) as caught:
        await _complete(_client(Recorder(status=401, body=error)))

    message_text = str(caught.value)
    assert "401" in message_text
    assert SECRET not in message_text


@pytest.mark.anyio
async def test_connection_failures_become_llm_errors() -> None:
    def unreachable(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused", request=request)

    with pytest.raises(LLMError, match="reach") as caught:
        await _complete(_client(unreachable))

    # The message ends up in tickets and traces: no resource URL in it.
    assert ENDPOINT not in str(caught.value)
    assert "services.ai.azure.com" not in str(caught.value)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("body", "expected"),
    [
        # Azure gateways do not always use the Anthropic error shape.
        ({"error": "invalid key"}, "Foundry returned HTTP 401."),
        ({"error": {"code": "401", "message": "Denied"}}, "Foundry returned HTTP 401 (401)."),
        ({"message": "Unauthorized"}, "Foundry returned HTTP 401."),
        ("Unauthorized", "Foundry returned HTTP 401."),
    ],
)
async def test_unusual_error_bodies_still_become_llm_errors(body: object, expected: str) -> None:
    with pytest.raises(LLMError) as caught:
        await _complete(_client(Recorder(status=401, body=body)))

    assert str(caught.value) == expected


@pytest.mark.anyio
async def test_any_other_sdk_error_becomes_an_llm_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(Recorder())

    async def broken(**_: object) -> None:
        raise anthropic.AnthropicError("response did not match the schema")

    monkeypatch.setattr(client._client.messages, "create", broken)

    with pytest.raises(LLMError, match="model call failed"):
        await _complete(client)


# The scripted fake implements the same interface


@pytest.mark.anyio
async def test_scripted_fake_replays_responses_and_records_requests() -> None:
    fake = ScriptedLLM([LLMResponse.from_message(REPLY)])

    response = await _complete(fake)

    assert response.tool_calls[0].name == "get_employee_info"
    assert fake.requests[0].tools == TOOLS
    assert fake.requests[0].messages[-1]["content"] == "E1001: I need a second monitor"


@pytest.mark.anyio
async def test_scripted_fake_fails_loudly_when_the_script_runs_out() -> None:
    with pytest.raises(AssertionError, match="no scripted response"):
        await _complete(ScriptedLLM([]))
