"""The LLM behind the agent: an interface, and Claude Opus 5.5 on Azure AI Foundry.

PLAN.md D7. The agent depends only on `LLMClient`, so tests inject a scripted
fake (tests/fakes.py) and never need credentials or network.

Checked against the current Claude docs (Foundry page, Opus 5.5 notes):
- `AsyncAnthropicFoundry(api_key=..., base_url=...)`: API-key auth; base_url is the
  deployment's Target URI; `model` is the deployment name.
- No temperature/top_p/top_k: Opus 5.5 rejects non-default sampling (400), and
  the SDK's API version no longer takes them. Effort is set explicitly instead.
- No forced tool_choice (400 on Opus 5.5): `auto`, the default, is used.
- No `speed`: on Foundry fast mode is the `-fast` deployment itself, and sending
  `speed` is an error.
- Text between tool calls comes back in `thinking` blocks (empty by default), so
  thinking display is "summarized": that summary is the visible Thought.
- Thinking blocks must be sent back unchanged (preserved thinking), so the raw
  reply blocks are kept for the next turn.
- Foundry has no server-side fallback; a refusal (`stop_reason == "refusal"`) is
  returned for the agent to escalate.
"""

import time
from typing import Any, Protocol, cast

import anthropic
import httpx2
from anthropic import AsyncAnthropicFoundry
from anthropic.types import Message, MessageParam, ToolParam
from pydantic import BaseModel, ConfigDict

from agent.config import AgentSettings

REQUEST_TIMEOUT_S = 120.0


class LLMError(Exception):
    """The model call failed (HTTP error, timeout, unreachable). Never holds the key."""


class ToolCall(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    arguments: dict[str, Any]


class Usage(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_tokens: int
    output_tokens: int


class LLMResponse(BaseModel):
    """One model reply, normalized for the agent loop and its trace."""

    model_config = ConfigDict(frozen=True)

    content: list[dict[str, Any]]
    """The reply's content blocks exactly as received, for the next request."""
    thought: str
    """Non-empty thinking summaries and text, in order: the trace's Thought."""
    thinking: str = ""
    """Only the thinking summaries (the Thought of a final answer, without the answer)."""
    text: str
    """Only the text blocks: the model's answer when it calls no tool."""
    tool_calls: list[ToolCall]
    stop_reason: str | None
    refusal: str | None = None
    """For stop_reason "refusal": the safety category and explanation, if given."""
    usage: Usage
    latency_ms: float = 0.0

    @classmethod
    def from_message(
        cls, reply: Message | dict[str, Any], latency_ms: float = 0.0
    ) -> "LLMResponse":
        message = reply if isinstance(reply, Message) else Message.model_validate(reply)
        thoughts: list[str] = []
        thinking: list[str] = []
        texts: list[str] = []
        calls: list[ToolCall] = []
        for block in message.content:
            if block.type == "thinking" and block.thinking.strip():
                thoughts.append(block.thinking.strip())
                thinking.append(block.thinking.strip())
            elif block.type == "text" and block.text.strip():
                thoughts.append(block.text.strip())
                texts.append(block.text.strip())
            elif block.type == "tool_use":
                calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(block.input)))
        refusal = None
        if message.stop_reason == "refusal":
            details = message.stop_details
            parts = [details.category, details.explanation] if details else []
            refusal = ": ".join(str(part) for part in parts if part) or "no category given"
        return cls(
            content=[block.to_dict() for block in message.content],
            thought="\n".join(thoughts),
            thinking="\n".join(thinking),
            text="\n".join(texts),
            tool_calls=calls,
            stop_reason=message.stop_reason,
            refusal=refusal,
            usage=Usage(
                input_tokens=message.usage.input_tokens,
                output_tokens=message.usage.output_tokens,
            ),
            latency_ms=latency_ms,
        )

    def assistant_message(self) -> MessageParam:
        """This reply as the assistant turn to append to the conversation."""
        return cast(MessageParam, {"role": "assistant", "content": self.content})


def _error_kind(body: object) -> str | None:
    """The error type (Anthropic shape) or code (Azure gateway shape), if any.
    Error bodies vary, so nothing here may assume a shape."""
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        kind = error.get("type") or error.get("code")
        return str(kind) if kind else None
    return None


class LLMClient(Protocol):
    async def complete(
        self, *, system: str, messages: list[MessageParam], tools: list[ToolParam]
    ) -> LLMResponse:
        """One model call. Raises LLMError if the call itself fails."""
        ...


class FoundryLLMClient:
    """Claude on Azure AI Foundry through the Anthropic SDK's Foundry client."""

    def __init__(
        self,
        settings: AgentSettings,
        *,
        http_client: httpx2.AsyncClient | None = None,
        max_retries: int = 2,
    ) -> None:
        self._settings = settings
        self._client = AsyncAnthropicFoundry(
            api_key=settings.foundry_api_key.get_secret_value(),
            base_url=settings.foundry_endpoint,
            timeout=REQUEST_TIMEOUT_S,
            max_retries=max_retries,  # the SDK retries 408/409/429/5xx and connection errors
            http_client=http_client,
        )

    async def complete(
        self, *, system: str, messages: list[MessageParam], tools: list[ToolParam]
    ) -> LLMResponse:
        started = time.perf_counter()
        try:
            reply = await self._client.messages.create(
                model=self._settings.foundry_model_deployment,
                max_tokens=self._settings.foundry_max_tokens,
                system=system,
                messages=messages,
                tools=tools,
                thinking={"type": "adaptive", "display": "summarized"},
                output_config={"effort": self._settings.foundry_effort},
            )
        except anthropic.APIStatusError as exc:
            kind = _error_kind(exc.body)
            raise LLMError(
                f"Foundry returned HTTP {exc.status_code}" + (f" ({kind})." if kind else ".")
            ) from None
        except anthropic.APIConnectionError:
            # Also covers timeouts (APITimeoutError is a subclass).
            # No URL in the message: it ends up in review tickets and trace files.
            raise LLMError(
                "Could not reach the Foundry endpoint (check FOUNDRY_ENDPOINT)."
            ) from None
        except anthropic.AnthropicError as exc:  # e.g. a reply the SDK cannot parse
            raise LLMError(f"The model call failed ({type(exc).__name__}).") from None
        return LLMResponse.from_message(reply, (time.perf_counter() - started) * 1000)
