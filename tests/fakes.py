"""Test doubles for the agent: a scripted LLM and Messages API reply builders."""

from __future__ import annotations

import copy
import itertools
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from anthropic.types import MessageParam, ToolParam

from agent.llm import LLMResponse

_ids = itertools.count(1)


def message(
    content: list[dict[str, Any]],
    *,
    stop_reason: str = "end_turn",
    input_tokens: int = 1200,
    output_tokens: int = 85,
) -> dict[str, Any]:
    """A Messages API reply body, as Foundry returns it."""
    return {
        "id": f"msg_{next(_ids)}",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


def tool_use(name: str, arguments: dict[str, Any], *, thought: str = "") -> LLMResponse:
    """A reply that calls one tool, with an optional summarized-thinking Thought."""
    blocks: list[dict[str, Any]] = []
    if thought:
        blocks.append({"type": "thinking", "thinking": thought, "signature": "sig"})
    call_id = f"toolu_{next(_ids)}"
    blocks.append({"type": "tool_use", "id": call_id, "name": name, "input": arguments})
    return LLMResponse.from_message(message(blocks, stop_reason="tool_use"))


def text(answer: str) -> LLMResponse:
    """A final reply with no tool call."""
    return LLMResponse.from_message(message([{"type": "text", "text": answer}]))


@dataclass(frozen=True)
class RecordedRequest:
    system: str
    messages: list[MessageParam]
    tools: list[ToolParam]


class ScriptedLLM:
    """An LLMClient that replays scripted responses in order and records each request."""

    def __init__(self, responses: Iterable[LLMResponse]) -> None:
        self._responses = list(responses)
        self.requests: list[RecordedRequest] = []

    async def complete(
        self, *, system: str, messages: list[MessageParam], tools: list[ToolParam]
    ) -> LLMResponse:
        # Copies, so later appends by the caller don't rewrite what was sent.
        self.requests.append(RecordedRequest(system, copy.deepcopy(messages), copy.deepcopy(tools)))
        if not self._responses:
            raise AssertionError(f"no scripted response left for request {len(self.requests)}")
        return self._responses.pop(0)
