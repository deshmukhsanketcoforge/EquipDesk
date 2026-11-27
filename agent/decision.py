"""The agent's decision: the model's JSON draft, its reflection and the final record.

PLAN.md D8. The model ends a run with one JSON object; `parse_draft` validates it.
Each draft is reflected on (EQP-12, R-20 to R-23) and may be revised once. The
final `Decision` is what the run actually did, after the guardrails (EQP-11): the
model's draft when the tool results support it, otherwise a forced escalation.
"""

import json
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    computed_field,
    field_validator,
    model_validator,
)

from server.codes import EscalationCode, IssueCode


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


class IssueSource(StrEnum):
    DETERMINISTIC = "deterministic"
    """The claim-vs-observation checker (R-20 to R-22)."""
    CRITIC = "critic"
    """The LLM critic (R-23, and facts the regexes cannot extract)."""


class Issue(BaseModel):
    """One reason reflection sent a draft back."""

    model_config = ConfigDict(frozen=True)

    code: IssueCode
    source: IssueSource
    detail: str


class Reflection(BaseModel):
    """Reflection on one draft: the deterministic checks plus the LLM critic."""

    model_config = ConfigDict(frozen=True)

    draft_number: int
    """1 for the first draft, 2 for the one revision."""
    seeded: bool = False
    """The harness replaced the model's draft with a known-bad one
    (--seed-flawed-draft); not a model error."""
    draft: Draft
    issues: list[Issue]
    critic_error: str | None = None
    """Set when the critic gave no usable answer: the draft could not be checked."""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def verdict(self) -> Literal["pass", "revise", "failed"]:
        if self.critic_error is not None:
            return "failed"
        return "revise" if self.issues else "pass"


def json_text(text: str) -> str:
    """The JSON object in a model answer, tolerating a code fence or a short preamble."""
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
        data: Any = json.loads(json_text(text))
    except json.JSONDecodeError as exc:
        raise DecisionError(f"The answer is not valid JSON ({exc.msg}).") from None
    if not isinstance(data, dict):  # pragma: no cover - json_text found braces
        raise DecisionError("The answer is not a JSON object.")
    try:
        return Draft.model_validate(data)
    except ValidationError as exc:
        raise DecisionError(f"The decision JSON is invalid: {_describe(exc)}.") from None
