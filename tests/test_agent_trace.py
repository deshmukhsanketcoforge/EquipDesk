"""EQP-10: the ReAct trace and its JSON and Markdown renderers (deliverable #6)."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent.decision import Decision, DecisionKind
from agent.trace import Action, EndReason, Outcome, Step, Trace, to_json, to_markdown, write_trace
from server.codes import EscalationCode

STARTED = datetime(2026, 11, 27, 10, 0, tzinfo=UTC)


def _trace() -> Trace:
    return Trace(
        employee_id="E1001",
        request="I need a second monitor",
        model="claude-opus-5-5-fast",
        started_at=STARTED,
        max_steps=8,
        tool_steps=1,
        steps=[
            Step(
                index=1,
                actor="model",
                thought="I need the employee's record first.",
                actions=[
                    Action(
                        tool_use_id="toolu_1",
                        tool="get_employee_info",
                        arguments={"employee_id": "E1001"},
                        observation={"employee_id": "E1001", "role": "engineer"},
                        is_error=False,
                        executed=True,
                        latency_ms=12.4,
                    )
                ],
                answer=None,
                stop_reason="tool_use",
                input_tokens=1200,
                output_tokens=85,
                llm_latency_ms=930.2,
            ),
            Step(
                index=2,
                actor="model",
                thought="",
                actions=[],
                answer="APPROVE: your second monitor is within policy.",
                stop_reason="end_turn",
                input_tokens=1400,
                output_tokens=40,
                llm_latency_ms=610.0,
            ),
        ],
        outcome=Outcome(
            ended_by=EndReason.ANSWER,
            final_text="APPROVE: your second monitor is within policy.",
        ),
    )


def _forced() -> Trace:
    trace = _trace()
    forced = Step(
        index=3,
        actor="harness",
        thought="Step limit reached: escalating to a human reviewer.",
        actions=[
            Action(
                tool_use_id=None,
                tool="flag_for_human_review",
                arguments={
                    "employee_id": "E1001",
                    "request": "x",
                    "reason": "GUARDRAIL_OVERRIDE: y",
                },
                observation={"ticket_id": "TKT-000007", "escalation_code": "GUARDRAIL_OVERRIDE"},
                is_error=False,
                executed=True,
                latency_ms=20.0,
            )
        ],
        answer=None,
        stop_reason=None,
        input_tokens=0,
        output_tokens=0,
        llm_latency_ms=0.0,
    )
    return trace.model_copy(
        update={
            "steps": [*trace.steps, forced],
            "outcome": Outcome(ended_by=EndReason.STEP_LIMIT, ticket_id="TKT-000007"),
        }
    )


# JSON


def test_json_has_every_field_for_every_step() -> None:
    data = json.loads(to_json(_trace()))

    step = data["steps"][0]
    assert step["thought"] == "I need the employee's record first."
    assert step["actions"][0]["tool"] == "get_employee_info"
    assert step["actions"][0]["observation"] == {"employee_id": "E1001", "role": "engineer"}
    assert step["input_tokens"] == 1200
    assert step["output_tokens"] == 85
    assert step["llm_latency_ms"] == 930.2
    assert step["actions"][0]["latency_ms"] == 12.4
    assert data["outcome"]["ended_by"] == "answer"
    assert data["started_at"] == "2026-11-27T10:00:00Z"


def test_json_includes_totals() -> None:
    data = json.loads(to_json(_trace()))

    assert data["total_input_tokens"] == 2600
    assert data["total_output_tokens"] == 125
    assert data["tool_steps"] == 1


def test_json_round_trips() -> None:
    trace = _forced()

    assert Trace.model_validate_json(to_json(trace)) == trace


# Markdown


def test_markdown_shows_thought_action_observation_in_order() -> None:
    markdown = to_markdown(_trace())

    thought = markdown.index("**Thought:** I need the employee's record first.")
    action = markdown.index(
        '**Action:**\n\n```text\nget_employee_info({"employee_id": "E1001"})\n```'
    )
    observation = markdown.index("**Observation:**")
    assert thought < action < observation
    assert '"role": "engineer"' in markdown


def test_markdown_shows_tokens_latency_and_the_answer() -> None:
    markdown = to_markdown(_trace())

    assert "1200 in / 85 out" in markdown
    assert "LLM 930 ms" in markdown
    assert "tool 12 ms" in markdown
    assert "**Answer:**\n\n> APPROVE: your second monitor is within policy." in markdown
    assert "Ended by: answer" in markdown
    assert "Totals: 2 model calls, 1 tool step, 2600 in / 125 out" in markdown


def test_markdown_header_names_the_request() -> None:
    markdown = to_markdown(_trace())

    assert markdown.startswith("# ReAct trace: E1001\n")
    assert "> I need a second monitor" in markdown
    assert "claude-opus-5-5-fast" in markdown


def test_markdown_labels_forced_escalations_by_the_harness() -> None:
    markdown = to_markdown(_forced())

    assert "## Step 3 (harness, not the model)" in markdown
    assert "Ended by: step_limit" in markdown
    assert "Review ticket: TKT-000007" in markdown


def test_markdown_marks_actions_that_were_not_executed() -> None:
    trace = _trace()
    step = trace.steps[0]
    rejected = step.actions[0].model_copy(
        update={"executed": False, "is_error": True, "observation": "Rejected: no Thought."}
    )
    trace = trace.model_copy(
        update={"steps": [step.model_copy(update={"actions": [rejected]}), trace.steps[1]]}
    )

    markdown = to_markdown(trace)

    assert "(not executed)" in markdown
    assert "**Observation (error):**\n\n```text\nRejected: no Thought.\n```" in markdown


def test_markdown_quotes_a_multi_line_request_safely() -> None:
    trace = _trace().model_copy(update={"request": "line one\nline two"})

    assert "> line one\n> line two" in to_markdown(trace)


# Files


def test_write_trace_writes_markdown_and_json(tmp_path: Path) -> None:
    md_path, json_path = write_trace(_trace(), tmp_path / "traces", "A")

    assert md_path == tmp_path / "traces" / "A.md"
    assert json_path == tmp_path / "traces" / "A.json"
    assert md_path.read_text() == to_markdown(_trace())
    assert json.loads(json_path.read_text())["employee_id"] == "E1001"


@pytest.mark.parametrize("name", ["", "../escape", "a/b", "case A", ".hidden"])
def test_write_trace_rejects_unsafe_names(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError, match="trace name"):
        write_trace(_trace(), tmp_path, name)


def test_markdown_shows_why_a_run_ended_early() -> None:
    trace = _forced().model_copy(
        update={
            "outcome": Outcome(
                ended_by=EndReason.LLM_ERROR,
                ticket_id="TKT-000007",
                detail="Foundry returned HTTP 503.",
            )
        }
    )

    markdown = to_markdown(trace)

    assert "Ended by: llm_error" in markdown
    assert "Detail: Foundry returned HTTP 503." in markdown


# Untrusted text cannot break the Markdown (the trace is submission evidence)


def test_backticks_in_arguments_stay_inside_the_code_block() -> None:
    trace = _forced()
    step = trace.steps[-1]
    action = step.actions[0].model_copy(
        update={"arguments": {"request": "a ``` b\n# APPROVED", "reason": "x"}}
    )
    trace = trace.model_copy(
        update={"steps": [*trace.steps[:-1], step.model_copy(update={"actions": [action]})]}
    )

    markdown = to_markdown(trace)

    # The fence is longer than any backtick run inside it.
    assert "````text\n" in markdown
    assert "\n# APPROVED" not in markdown.replace("\\n# APPROVED", "")


def test_answers_are_quoted_so_they_cannot_add_headings() -> None:
    trace = _trace()
    final = trace.steps[1].model_copy(update={"answer": "## Fake heading\nsecond line"})
    trace = trace.model_copy(
        update={
            "steps": [trace.steps[0], final],
            "outcome": Outcome(
                ended_by=EndReason.ANSWER, final_text="## Fake heading\nsecond line"
            ),
        }
    )

    markdown = to_markdown(trace)

    assert "\n## Fake heading" not in markdown
    assert "> ## Fake heading\n> second line" in markdown


def test_a_failed_forced_escalation_is_shown_loudly() -> None:
    trace = _forced().model_copy(
        update={
            "outcome": Outcome(
                ended_by=EndReason.STEP_LIMIT,
                escalation_error="INVALID_INPUT: request is longer than 2000 characters.",
            )
        }
    )

    markdown = to_markdown(trace)

    assert "Escalation FAILED: INVALID_INPUT: request is longer than 2000 characters." in markdown
    assert "Review ticket" not in markdown


def test_outcome_detail_and_escalation_error_stay_on_one_line() -> None:
    # The detail can hold the model's refusal explanation: text we don't control.
    trace = _forced().model_copy(
        update={
            "outcome": Outcome(
                ended_by=EndReason.REFUSAL,
                detail="general_harms: declined.\n## Fake heading\n\nmore",
                escalation_error="INTERNAL: failed\n# Another",
            )
        }
    )

    markdown = to_markdown(trace)

    assert "Detail: general_harms: declined. ## Fake heading more" in markdown
    assert "Escalation FAILED: INTERNAL: failed # Another" in markdown
    assert "\n## Fake heading" not in markdown
    assert "\n# Another" not in markdown


# The final decision (EQP-11)


def test_markdown_shows_the_final_decision() -> None:
    final = Decision(
        decision=DecisionKind.APPROVE,
        item="monitor",
        response="Your second monitor is approved.\n## not a heading",
        reasons=["ELIGIBLE (WITHIN_POLICY)", "refresh 36 months\nlast 2022-03-27"],
        ticket_id=None,
        escalation_code=None,
        forced=False,
    )
    trace = _trace().model_copy(
        update={"outcome": _trace().outcome.model_copy(update={"decision": final})}
    )

    markdown = to_markdown(trace)

    assert "**Decision: APPROVE · item monitor**" in markdown
    assert "> Your second monitor is approved.\n> ## not a heading" in markdown
    assert "- ELIGIBLE (WITHIN_POLICY)\n- refresh 36 months last 2022-03-27" in markdown


def test_markdown_shows_a_forced_escalation_on_a_reused_ticket() -> None:
    final = Decision(
        decision=DecisionKind.ESCALATE,
        item=None,
        response="Your request has been sent to a human reviewer (ticket TKT-000007).",
        reasons=["GUARDRAIL_OVERRIDE: the reply was cut off."],
        ticket_id="TKT-000007",
        escalation_code=EscalationCode.GUARDRAIL_OVERRIDE,
        forced=True,
        ticket_code=EscalationCode.EXCEPTION_CLAIMED,
        ticket_reused=True,
    )
    trace = _forced().model_copy(
        update={"outcome": _forced().outcome.model_copy(update={"decision": final})}
    )

    markdown = to_markdown(trace)

    assert (
        "**Decision: ESCALATE · GUARDRAIL_OVERRIDE · ticket TKT-000007 · forced by the code"
        " · existing ticket reused, its code: EXCEPTION_CLAIMED**"
    ) in markdown


def test_a_decision_round_trips_through_json() -> None:
    final = Decision(
        decision=DecisionKind.ESCALATE,
        item=None,
        response="r",
        reasons=["x"],
        ticket_id="TKT-000001",
        escalation_code=EscalationCode.MULTIPLE_ITEMS,
        forced=False,
        ticket_code=EscalationCode.MULTIPLE_ITEMS,
    )
    trace = _trace().model_copy(
        update={"outcome": _trace().outcome.model_copy(update={"decision": final})}
    )

    assert Trace.model_validate_json(to_json(trace)) == trace
    assert json.loads(to_json(trace))["outcome"]["decision"]["escalation_code"] == "MULTIPLE_ITEMS"
