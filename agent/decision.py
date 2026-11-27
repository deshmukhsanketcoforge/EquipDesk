"""The agent's decision: the model's JSON draft and the final, guardrail-checked record.

PLAN.md D8. The model ends a run with one JSON object; `parse_draft` validates it.
The final `Decision` is what the run actually did, after the guardrails (EQP-11):
the model's draft when the tool results support it, otherwise a forced escalation.
"""

import json
from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from server.codes import EscalationCode


class DecisionKind(StrEnum):
    APPROVE = "APPROVE"
    DENY = "DENY"
    ESCALATE = "ESCALATE"


class DecisionError(ValueError):
    """The final answer is not a usable decision. The message is short and goes
    back to the model in the one retry."""


class Draft(BaseModel):
    """The model's final answer, as the system prompt asks for it."""

    model_config = ConfigDict(frozen=True, extra="ignore", str_strip_whitespace=True)

    decision: DecisionKind
    item: str | None = None
    """The catalog item the decision is about (as check_request_eligibility named it)."""
    response: str = Field(min_length=1)
    """The message to the employee."""
    reasons: list[str] = Field(min_length=1)
    ticket_id: str | None = None
    """The ticket flag_for_human_review returned (ESCALATE only)."""

    @field_validator("item")
    @classmethod
    def _normalize_item(cls, value: str | None) -> str | None:
        return " ".join(value.split()).lower() if value else None

    @model_validator(mode="after")
    def _item_for_approve_or_deny(self) -> Self:
        if self.decision is not DecisionKind.ESCALATE and not self.item:
            raise ValueError(f"item is required for {self.decision.value}")
        return self


class Decision(BaseModel):
    """What the run decided, after the guardrails. Every completed run has one."""

    model_config = ConfigDict(frozen=True)

    decision: DecisionKind
    item: str | None
    response: str
    reasons: list[str]
    ticket_id: str | None
    escalation_code: EscalationCode | None
    """ESCALATE only: the code the model asked for, or GUARDRAIL_OVERRIDE when the
    code forced the escalation."""
    forced: bool
    """True when the code escalated (guardrail, step cap, refusal, ...), not the model."""
    ticket_code: EscalationCode | None = None
    """The code stored on the ticket (differs from escalation_code if it was reused)."""
    ticket_reused: bool = False
    """The ticket already existed with another code (tickets are idempotent per
    employee and request), e.g. a forced escalation got back the model's ticket
    (SAN-38) or the model got back an earlier run's GUARDRAIL_OVERRIDE ticket."""


def _json_text(text: str) -> str:
    """The JSON object in the answer, tolerating a code fence or a short preamble."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end < start:
        raise DecisionError("The answer is not a JSON object.")
    return text[start : end + 1]


def _describe(error: ValidationError) -> str:
    parts = []
    for item in error.errors()[:3]:
        where = ".".join(str(part) for part in item["loc"]) or "decision"
        parts.append(f"{where}: {item['msg']}")
    return "; ".join(parts)


def parse_draft(text: str) -> Draft:
    """The model's final answer as a Draft, or DecisionError saying what is wrong."""
    try:
        data: Any = json.loads(_json_text(text))
    except json.JSONDecodeError as exc:
        raise DecisionError(f"The answer is not valid JSON ({exc.msg}).") from None
    if not isinstance(data, dict):  # pragma: no cover - _json_text found braces
        raise DecisionError("The answer is not a JSON object.")
    try:
        return Draft.model_validate(data)
    except ValidationError as exc:
        raise DecisionError(f"The decision JSON is invalid: {_describe(exc)}.") from None
