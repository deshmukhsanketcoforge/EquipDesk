"""The ReAct trace: every Thought -> Action -> Observation of one run, with tokens
and latency, rendered as JSON (for machines: evals, reflection) and Markdown (for
people: the submission's deliverable #6). PLAN.md §7.
"""

import json
import re
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, computed_field

_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


class EndReason(StrEnum):
    ANSWER = "answer"
    """The model gave its final answer without calling a tool."""
    STEP_LIMIT = "step_limit"
    """No answer within the step cap (R-19): forced escalation."""
    REFUSAL = "refusal"
    """The model declined the request: forced escalation."""
    MAX_TOKENS = "max_tokens"
    """The model's reply was cut off: forced escalation."""
    LLM_ERROR = "llm_error"
    """The model call failed: forced escalation."""
    NO_ANSWER = "no_answer"
    """The model stopped without a complete, non-empty answer: forced escalation."""
    MCP_ERROR = "mcp_error"
    """The MCP session failed mid-run: forced escalation (if the server can still take it)."""


class Action(BaseModel):
    model_config = ConfigDict(frozen=True)

    tool_use_id: str | None
    """The model's tool_use id; None for an action the harness took itself."""
    tool: str
    arguments: dict[str, Any]
    observation: Any
    is_error: bool
    executed: bool
    """False when the call was refused before reaching the server."""
    latency_ms: float


class Step(BaseModel):
    model_config = ConfigDict(frozen=True)

    index: int
    actor: Literal["model", "harness"]
    """`harness` marks a forced escalation the code made, not the model."""
    thought: str
    actions: list[Action]
    answer: str | None
    """The model's text when it called no tool."""
    stop_reason: str | None
    input_tokens: int
    output_tokens: int
    llm_latency_ms: float


class Outcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    ended_by: EndReason
    final_text: str | None = None
    """The model's final answer (EndReason.ANSWER only)."""
    ticket_id: str | None = None
    """The review ticket of a forced escalation."""
    detail: str | None = None
    """Why a run ended early, e.g. the LLM error message."""
    escalation_error: str | None = None
    """Set when the forced escalation itself failed: no review ticket exists."""


class Trace(BaseModel):
    model_config = ConfigDict(frozen=True)

    employee_id: str
    request: str
    model: str
    started_at: datetime
    max_steps: int
    tool_steps: int
    """Tool-using model turns the loop counted against the step cap (R-19). A turn the
    cap stopped before it ran is not counted; a turn rejected for a missing Thought is."""
    steps: list[Step]
    outcome: Outcome

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_input_tokens(self) -> int:
        return sum(step.input_tokens for step in self.steps)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_output_tokens(self) -> int:
        return sum(step.output_tokens for step in self.steps)


def to_json(trace: Trace) -> str:
    return trace.model_dump_json(indent=2) + "\n"


def _call(action: Action) -> str:
    return f"{action.tool}({json.dumps(action.arguments)})"


def _fenced(text: str, language: str = "text") -> list[str]:
    """A code block whose fence is longer than any backtick run in the text, so
    untrusted text (the employee's request, tool echoes) cannot close it."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return [f"{fence}{language}", text, fence]


def _one_line(text: str) -> str:
    """Whitespace collapsed: text we don't control (model or tool messages) stays on
    one line, so it cannot add headings or break the layout."""
    return " ".join(text.split())


def _quoted(text: str) -> list[str]:
    """A block quote: model text cannot add headings or other structure."""
    return [f"> {line}" for line in text.splitlines() or [""]]


def _observation(action: Action) -> list[str]:
    label = "**Observation (error):**" if action.is_error else "**Observation:**"
    if isinstance(action.observation, str):
        return [label, "", *_fenced(action.observation)]
    return [label, "", *_fenced(json.dumps(action.observation, indent=2), "json")]


def to_markdown(trace: Trace) -> str:
    lines = [
        f"# ReAct trace: {trace.employee_id}",
        "",
        *_quoted(trace.request),
        "",
        f"Model: {trace.model} · started {trace.started_at.isoformat()} · "
        f"step cap {trace.max_steps}",
    ]
    for step in trace.steps:
        title = f"## Step {step.index}"
        if step.actor == "harness":
            title += " (harness, not the model)"
        thought = _one_line(step.thought) or "(none)"
        lines += ["", title, "", f"**Thought:** {thought}"]
        for action in step.actions:
            suffix = " (not executed)" if not action.executed else ""
            lines += ["", f"**Action:**{suffix}", "", *_fenced(_call(action))]
            lines += ["", *_observation(action)]
        if step.answer is not None:
            lines += ["", "**Answer:**", "", *_quoted(step.answer)]
        tool_ms = sum(action.latency_ms for action in step.actions)
        lines += [
            "",
            f"_Tokens {step.input_tokens} in / {step.output_tokens} out · "
            f"LLM {step.llm_latency_ms:.0f} ms · tool {tool_ms:.0f} ms · "
            f"stop_reason {step.stop_reason or '-'}_",
        ]
    model_calls = sum(1 for step in trace.steps if step.actor == "model")
    noun = "step" if trace.tool_steps == 1 else "steps"
    lines += ["", "## Outcome", "", f"Ended by: {trace.outcome.ended_by.value}"]
    if trace.outcome.final_text is not None:
        lines += ["Final answer:", "", *_quoted(trace.outcome.final_text), ""]
    if trace.outcome.escalation_error:
        lines.append(f"Escalation FAILED: {_one_line(trace.outcome.escalation_error)}")
    elif trace.outcome.ticket_id:
        lines.append(f"Review ticket: {trace.outcome.ticket_id}")
    if trace.outcome.detail:
        lines.append(f"Detail: {_one_line(trace.outcome.detail)}")
    lines.append(
        f"Totals: {model_calls} model calls, {trace.tool_steps} tool {noun}, "
        f"{trace.total_input_tokens} in / {trace.total_output_tokens} out"
    )
    return "\n".join(lines) + "\n"


def write_trace(trace: Trace, directory: Path, name: str) -> tuple[Path, Path]:
    """Write <directory>/<name>.md and .json; `name` is a plain file stem (e.g. a case ID)."""
    if not _SAFE_NAME.fullmatch(name):
        raise ValueError(f"Invalid trace name {name!r}: use letters, digits, '-' and '_'.")
    directory.mkdir(parents=True, exist_ok=True)
    md_path, json_path = directory / f"{name}.md", directory / f"{name}.json"
    md_path.write_text(to_markdown(trace))
    json_path.write_text(to_json(trace))
    return md_path, json_path
