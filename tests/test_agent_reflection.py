"""EQP-12: reflection checks every draft before it is final (R-20 to R-23; PLAN.md D10, §8).

The deterministic checker compares what the draft says with what the tools
returned; the LLM critic covers what regexes cannot (paraphrases, and whether an
escalation rule applies to the request's wording). End-to-end runs, the single
revision and the seeded mode are in tests/test_agent_react.py.
"""

import json
from typing import Any

import pytest

from agent.decision import DecisionKind, Draft, Issue, IssueSource, Reflection
from agent.llm import LLMError
from agent.reflection import (
    CRITIC_PROMPT,
    SEEDED_DRAFT,
    CriticError,
    LLMCritic,
    check_draft,
    parse_critique,
    reflect,
    revision_request,
)
from agent.trace import Action
from server.codes import IssueCode
from tests.fakes import FakeCritic, ScriptedLLM, critique, text

EMPLOYEE = "E1002"
REQUEST = "I need a second monitor"
NEXT_DATE = "2029-02-26"


def _action(tool: str, arguments: dict[str, Any], payload: Any, **flags: Any) -> Action:
    return Action(
        tool_use_id="toolu_x",
        tool=tool,
        arguments=arguments,
        observation=payload,
        is_error=flags.get("is_error", False),
        executed=flags.get("executed", True),
        latency_ms=1.0,
    )


def employee_info() -> Action:
    payload = {
        "employee_id": EMPLOYEE,
        "name": "Ben Okafor",
        "role": "engineer",
        "hire_date": "2023-11-26",
        "tenure_months": 36,
        "equipment": [
            {"item": "laptop", "acquired_on": "2024-11-26"},
            {"item": "monitor", "acquired_on": "2026-02-26"},
        ],
    }
    return _action("get_employee_info", {"employee_id": EMPLOYEE}, payload)


def cooldown(**overrides: Any) -> Action:
    """Case B's real eligibility answer (COOLDOWN_NOT_ELAPSED)."""
    payload = {
        "employee_id": EMPLOYEE,
        "role": "engineer",
        "item": "monitor",
        "verdict": "INELIGIBLE",
        "reason_code": "COOLDOWN_NOT_ELAPSED",
        "explanation": (
            "Last monitor acquired on 2026-02-26; the engineer refresh period is 36 "
            f"months, so the next eligible date is {NEXT_DATE}."
        ),
        "tenure_months": 36,
        "min_tenure_months": 0,
        "refresh_months": 36,
        "last_acquired_on": "2026-02-26",
        "next_eligible_date": NEXT_DATE,
        "unit_cost_usd": 350,
        **overrides,
    }
    return _action(
        "check_request_eligibility", {"employee_id": EMPLOYEE, "item": "monitor"}, payload
    )


def ticket(ticket_id: str = "TKT-000007") -> Action:
    payload = {
        "ticket_id": ticket_id,
        "status": "PENDING_REVIEW",
        "employee_id": EMPLOYEE,
        "escalation_code": "EXCEPTION_CLAIMED",
    }
    arguments = {"employee_id": EMPLOYEE, "request": REQUEST, "reason": "EXCEPTION_CLAIMED: x"}
    return _action("flag_for_human_review", arguments, payload)


B_ACTIONS = [employee_info(), cooldown()]


def deny(response: str, item: str = "monitor") -> Draft:
    return Draft(decision=DecisionKind.DENY, item=item, response=response, reasons=["cooldown"])


def escalate(response: str, ticket_id: str | None = None) -> Draft:
    return Draft(
        decision=DecisionKind.ESCALATE, response=response, reasons=["x"], ticket_id=ticket_id
    )


def codes(issues: list[Issue]) -> list[IssueCode]:
    return [issue.code for issue in issues]


def _check(draft: Draft, actions: list[Action] = B_ACTIONS) -> list[Issue]:
    return check_draft(draft, actions, employee_id=EMPLOYEE, request=REQUEST)


# The PLAN.md §8 example: case B's flawed draft


def test_the_seeded_case_b_draft_is_caught_with_the_exact_issue_codes() -> None:
    issues = _check(SEEDED_DRAFT)

    assert codes(issues) == [
        IssueCode.DECISION_MISMATCH,  # APPROVE, but the verdict was INELIGIBLE (R-20)
        IssueCode.UNSUPPORTED_NUMBER,  # "2 monitors": one is on file (R-21)
        IssueCode.UNSUPPORTED_DATE,  # 2029-02-14, but the tool said 2029-02-26 (R-21)
        IssueCode.UNBACKED_COMMITMENT,  # "will arrive by Friday" (R-22)
    ]
    assert all(issue.source is IssueSource.DETERMINISTIC for issue in issues)
    assert "2029-02-14" in issues[2].detail
    assert "arrive by Friday" in issues[3].detail


def test_the_seeded_draft_is_the_plan_example() -> None:
    assert SEEDED_DRAFT.decision is DecisionKind.APPROVE
    assert SEEDED_DRAFT.response == (
        "Approved! Your new monitor will arrive by Friday. "
        "You're allowed 2 monitors, next refresh 2029-02-14."
    )
    assert "SEEDED" in SEEDED_DRAFT.reasons[0]


def test_a_good_draft_is_confirmed() -> None:
    good = deny(
        f"Your last monitor was acquired on 2026-02-26 and the engineer refresh period "
        f"is 36 months, so you can request a new one on {NEXT_DATE}."
    )

    assert _check(good) == []


# R-20: decision vs verdict (the guardrail checks, as a chance to revise)


@pytest.mark.parametrize(
    ("draft", "actions"),
    [
        (deny("Denied.", item="laptop"), B_ACTIONS),  # another item than the one checked
        (Draft(decision=DecisionKind.APPROVE, item="monitor", response="Ok.", reasons=["x"]), []),
        (escalate("Sent to a reviewer."), B_ACTIONS),  # no ticket
    ],
    ids=["deny other item", "approve unchecked", "escalate without ticket"],
)
def test_a_decision_the_tools_do_not_support_is_a_mismatch(
    draft: Draft, actions: list[Action]
) -> None:
    assert codes(_check(draft, actions)) == [IssueCode.DECISION_MISMATCH]


# R-21: every date, amount, number and ticket ID must come from a tool


@pytest.mark.parametrize(
    "response",
    [
        "You can request one from February 26, 2029.",
        "You can request one from 26 February 2029.",
        "You can request one from Feb 26th, 2029.",
        "A monitor costs $350.",
        "A monitor costs $350.00.",
        "The refresh period is 36 months.",
        "The refresh period is 3 years.",  # 3 years = 36 months
        "The 36-month refresh period has not passed.",
        "You have 1 monitor on file.",
    ],
)
def test_facts_the_tools_returned_are_supported(response: str) -> None:
    assert _check(deny(response)) == []


@pytest.mark.parametrize(
    ("response", "code"),
    [
        ("You can request one from 2029-03-01.", IssueCode.UNSUPPORTED_DATE),
        ("You can request one from March 1, 2029.", IssueCode.UNSUPPORTED_DATE),
        ("You can request one from 2029-02-30.", IssueCode.UNSUPPORTED_DATE),  # no such day
        ("You can request one from February 30, 2029.", IssueCode.UNSUPPORTED_DATE),
        ("A monitor costs $1,200.", IssueCode.UNSUPPORTED_AMOUNT),
        ("The refresh period is 4 years.", IssueCode.UNSUPPORTED_NUMBER),
        ("The refresh period is 30 months.", IssueCode.UNSUPPORTED_NUMBER),
        ("You already have 3 monitors.", IssueCode.UNSUPPORTED_NUMBER),
        ("A review takes 5 days.", IssueCode.UNSUPPORTED_NUMBER),
        ("Your ticket is TKT-000099.", IssueCode.UNSUPPORTED_TICKET),
    ],
)
def test_facts_no_tool_returned_are_flagged(response: str, code: IssueCode) -> None:
    assert codes(_check(deny(response))) == [code]


def test_numbers_inside_dates_amounts_and_tickets_are_not_quantities() -> None:
    # "350 monitor" inside "$350 monitor" is a price, not a count of monitors.
    assert _check(deny(f"A $350 monitor is next available on {NEXT_DATE}.")) == []


def test_the_ticket_must_be_the_one_flag_for_human_review_returned() -> None:
    actions = [*B_ACTIONS, ticket("TKT-000007")]

    assert _check(escalate("Ticket TKT-000007 is open.", "TKT-000007"), actions) == []
    assert codes(_check(escalate("See tkt-000008.", "TKT-000007"), actions)) == [
        IssueCode.UNSUPPORTED_TICKET
    ]


def test_dates_and_numbers_in_tool_explanations_and_error_messages_count() -> None:
    not_found = _action(
        "get_employee_info",
        {"employee_id": "E9999"},
        {"error": {"code": "NOT_FOUND", "reason_code": "EMPLOYEE_NOT_FOUND", "message": "x"}},
    )
    high_cost = cooldown(
        verdict="NEEDS_REVIEW",
        reason_code="HIGH_COST",
        explanation="A workstation costs $3,200, above the $2,500 review threshold.",
    )

    draft = escalate("A workstation costs $3,200, above the $2,500 threshold.", "TKT-000007")

    assert _check(draft, [not_found, high_cost, ticket()]) == []


def test_calls_that_never_ran_support_nothing() -> None:
    skipped = cooldown()
    skipped = skipped.model_copy(update={"executed": False})

    issues = _check(deny(f"Next date {NEXT_DATE}."), [skipped])

    assert IssueCode.UNSUPPORTED_DATE in codes(issues)


# R-22: no promise a tool did not confirm


@pytest.mark.parametrize(
    "phrase",
    [
        "It will arrive by Friday.",
        "It will be delivered on Monday.",
        "It will ship tomorrow.",
        "Delivery by the end of the week.",
        "It ships by 12/15.",
        "Expect it within 3 business days.",
        "Delivery is guaranteed.",
    ],
)
def test_commitments_are_flagged(phrase: str) -> None:
    issues = _check(escalate(phrase, "TKT-000007"), [*B_ACTIONS, ticket()])

    assert IssueCode.UNBACKED_COMMITMENT in codes(issues)


@pytest.mark.parametrize(
    "phrase",
    [
        "You are definitely not eligible yet.",
        "You'll be eligible by the end of the month.",
        "Please ask again by Friday if anything changes.",
    ],
)
def test_statements_that_promise_no_delivery_are_not_commitments(phrase: str) -> None:
    assert IssueCode.UNBACKED_COMMITMENT not in codes(_check(deny(phrase)))


def test_one_commitment_phrase_is_one_issue() -> None:
    issues = _check(deny(f"Approved on {NEXT_DATE}, it will arrive by Friday."))

    assert codes(issues) == [IssueCode.UNBACKED_COMMITMENT]


def test_issue_details_quote_model_text_briefly() -> None:
    issues = _check(deny("It will arrive by " + "x" * 5000))

    assert issues
    assert all(len(issue.detail) < 300 for issue in issues)


# The LLM critic (R-23 and paraphrases)


def test_parse_critique_reads_the_issue_list() -> None:
    reply = json.dumps(
        {"issues": [{"code": "MISSED_ESCALATION", "detail": "The request claims a spill."}]}
    )

    (issue,) = parse_critique(reply)

    assert issue.code is IssueCode.MISSED_ESCALATION
    assert issue.source is IssueSource.CRITIC
    assert issue.detail == "The request claims a spill."


def test_parse_critique_accepts_no_issues_and_a_code_fence() -> None:
    assert parse_critique('```json\n{"issues": []}\n```') == []


@pytest.mark.parametrize(
    "reply",
    [
        "Looks fine to me.",
        "[]",
        '{"issues": [}',
        '{"issues": "none"}',
        '{"issues": [{"detail": "no code"}]}',
    ],
    ids=["prose", "list", "bad json", "not a list", "no code"],
)
def test_an_unusable_critique_is_an_error(reply: str) -> None:
    with pytest.raises(CriticError):
        parse_critique(reply)


@pytest.mark.parametrize(
    ("code", "detail", "expected_code", "expected_detail"),
    [
        # A code that exists but is the checker's, or no code at all, is still an issue.
        ("UNSUPPORTED_DATE", "Wrong date.", IssueCode.UNSUPPORTED_CLAIM, "UNSUPPORTED_DATE"),
        ("NOT_A_CODE", "Odd.", IssueCode.UNSUPPORTED_CLAIM, "NOT_A_CODE"),
        (" missed_escalation ", "Spill.", IssueCode.MISSED_ESCALATION, "Spill."),
        ("MISSED_ESCALATION", "  ", IssueCode.MISSED_ESCALATION, "no detail given"),
    ],
    ids=["checker code", "unknown code", "lowercase", "empty detail"],
)
def test_one_odd_critic_entry_is_kept_as_an_issue_not_a_failure(
    code: str, detail: str, expected_code: IssueCode, expected_detail: str
) -> None:
    # A critique is only unusable as a whole when it is not the JSON asked for;
    # one odd entry still sends the draft back once (no immediate escalation).
    reply = json.dumps({"issues": [{"code": code, "detail": detail}]})

    (issue,) = parse_critique(reply)

    assert issue.code is expected_code
    assert issue.source is IssueSource.CRITIC
    assert expected_detail in issue.detail


def test_critic_details_are_capped() -> None:
    reply = json.dumps({"issues": [{"code": "UNSUPPORTED_CLAIM", "detail": "y" * 5000}]})

    (issue,) = parse_critique(reply)

    assert len(issue.detail) <= 300


@pytest.mark.anyio
async def test_the_llm_critic_sends_the_rules_the_request_tools_and_draft() -> None:
    llm = ScriptedLLM([critique()])
    draft = deny(f"Next date {NEXT_DATE}.")

    report = await LLMCritic(llm).review(
        employee_id=EMPLOYEE, request=REQUEST, draft=draft, actions=B_ACTIONS
    )

    assert report.issues == []
    (sent,) = llm.requests
    assert sent.system == CRITIC_PROMPT
    assert sent.tools == []
    (message,) = sent.messages
    content = message["content"]
    assert isinstance(content, str)
    assert f"<request>\n{REQUEST}\n</request>" in content
    assert NEXT_DATE in content and "COOLDOWN_NOT_ELAPSED" in content
    assert draft.model_dump_json() in content
    assert report.input_tokens > 0 and report.latency_ms > 0


def test_the_critic_prompt_names_every_code_it_may_use() -> None:
    for code in ("DECISION_MISMATCH", "UNSUPPORTED_CLAIM", "UNBACKED_COMMITMENT"):
        assert code in CRITIC_PROMPT
    assert "MISSED_ESCALATION" in CRITIC_PROMPT
    assert "untrusted" in CRITIC_PROMPT


@pytest.mark.anyio
async def test_the_llm_critic_flags_a_denial_that_ignores_a_claimed_exception() -> None:
    # R-23: the tools say INELIGIBLE, but the request claims the monitor broke.
    llm = ScriptedLLM(
        [
            critique(
                ("MISSED_ESCALATION", "The request says the monitor is broken: EXCEPTION_CLAIMED.")
            )
        ]
    )

    report = await LLMCritic(llm).review(
        employee_id=EMPLOYEE,
        request="My monitor broke, I need a new one",
        draft=deny(f"You can request one on {NEXT_DATE}."),
        actions=B_ACTIONS,
    )

    assert codes(report.issues) == [IssueCode.MISSED_ESCALATION]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (LLMError("Foundry returned HTTP 529."), "HTTP 529"),
        (text("", stop_reason="refusal"), "refusal"),
        (text('{"issues": [', stop_reason="max_tokens"), "max_tokens"),
        (text("no json here"), "not a JSON object"),
    ],
    ids=["llm error", "refusal", "cut off", "malformed"],
)
async def test_a_critic_that_cannot_answer_raises_critic_error(reply: Any, expected: str) -> None:
    critic = LLMCritic(ScriptedLLM([reply]))

    with pytest.raises(CriticError, match=expected):
        await critic.review(
            employee_id=EMPLOYEE, request=REQUEST, draft=deny("x"), actions=B_ACTIONS
        )


# reflect(): both checks, one record


@pytest.mark.anyio
async def test_reflect_runs_both_checks_and_records_them() -> None:
    critic = FakeCritic([("UNSUPPORTED_CLAIM", "Says 'top priority'; no tool said so.")])

    step = await reflect(
        SEEDED_DRAFT,
        B_ACTIONS,
        critic,
        index=3,
        number=1,
        seeded=True,
        employee_id=EMPLOYEE,
        request=REQUEST,
    )

    assert step.actor == "reflection"
    assert step.index == 3
    reflection = step.reflection
    assert reflection is not None
    assert reflection.verdict == "revise"
    assert reflection.seeded and reflection.draft_number == 1
    assert reflection.draft == SEEDED_DRAFT
    assert codes(reflection.issues)[-1] is IssueCode.UNSUPPORTED_CLAIM
    assert reflection.issues[-1].source is IssueSource.CRITIC
    assert critic.calls == 1


@pytest.mark.anyio
async def test_reflect_passes_a_good_draft() -> None:
    step = await reflect(
        deny(f"Next date {NEXT_DATE}."),
        B_ACTIONS,
        FakeCritic(),
        index=3,
        number=1,
        seeded=False,
        employee_id=EMPLOYEE,
        request=REQUEST,
    )

    assert step.reflection is not None
    assert step.reflection.verdict == "pass"
    assert step.reflection.issues == []


@pytest.mark.anyio
async def test_reflect_records_a_critic_failure() -> None:
    step = await reflect(
        deny(f"Next date {NEXT_DATE}."),
        B_ACTIONS,
        FakeCritic(error="the critic call failed"),
        index=3,
        number=1,
        seeded=False,
        employee_id=EMPLOYEE,
        request=REQUEST,
    )

    assert step.reflection is not None
    assert step.reflection.verdict == "failed"
    assert step.reflection.critic_error == "the critic call failed"


def test_revision_request_lists_the_issues_and_shows_a_seeded_draft() -> None:
    issues = _check(SEEDED_DRAFT)
    seeded = Reflection(draft_number=1, seeded=True, draft=SEEDED_DRAFT, issues=issues)

    message = revision_request(seeded)

    assert "SEEDED" in message
    assert SEEDED_DRAFT.response in message
    for issue in issues:
        assert issue.code.value in message
    assert "flag_for_human_review" in message
    natural = revision_request(seeded.model_copy(update={"seeded": False}))
    assert "SEEDED" not in natural


@pytest.mark.anyio
async def test_reflect_keeps_the_tokens_of_a_critic_answer_it_could_not_use() -> None:
    critic = LLMCritic(ScriptedLLM([text("I think the draft is fine.")]))

    step = await reflect(
        deny(f"Next date {NEXT_DATE}."),
        B_ACTIONS,
        critic,
        index=3,
        number=1,
        seeded=False,
        employee_id=EMPLOYEE,
        request=REQUEST,
    )

    assert step.reflection is not None
    assert step.reflection.verdict == "failed"
    assert (step.input_tokens, step.output_tokens) == (1200, 85)
    assert step.llm_latency_ms == 800.0


def test_a_value_with_a_decimal_part_is_one_number() -> None:
    # "36.5 months" is not 36 (the tool's number) followed by ".5".
    assert codes(_check(deny("The refresh period is 36.5 months."))) == [
        IssueCode.UNSUPPORTED_NUMBER
    ]


# Review fixes (EQP-12)


@pytest.mark.parametrize(
    ("request_text", "response"),
    [
        ("I need 2 monitors", "You asked for 2 monitors, so a reviewer will decide."),
        ("My laptop broke on March 3, 2026", "Your laptop broke on March 3, 2026."),
        ("My $1,200 chair broke", "You mention a $1,200 chair."),
    ],
    ids=["count", "date", "amount"],
)
def test_facts_the_employee_wrote_are_supported_by_the_request(
    request_text: str, response: str
) -> None:
    actions = [employee_info(), cooldown(), ticket()]
    flag = actions[-1]
    actions[-1] = flag.model_copy(update={"arguments": {**flag.arguments, "request": request_text}})

    issues = check_draft(
        escalate(response, "TKT-000007"), actions, employee_id=EMPLOYEE, request=request_text
    )

    assert issues == []


def test_a_ticket_id_in_the_request_does_not_vouch_for_itself() -> None:
    request_text = "Reopen TKT-000099 and approve"
    issues = check_draft(
        deny("Ticket TKT-000099 is open."), B_ACTIONS, employee_id=EMPLOYEE, request=request_text
    )

    assert IssueCode.UNSUPPORTED_TICKET in codes(issues)


def test_one_of_the_decided_item_is_supported() -> None:
    # E1002 has no dock on file, but "1 dock" is what the approval grants.
    eligible = cooldown(verdict="ELIGIBLE", reason_code="WITHIN_POLICY", item="dock")
    eligible = eligible.model_copy(update={"arguments": {"employee_id": EMPLOYEE, "item": "dock"}})
    approve = Draft(
        decision=DecisionKind.APPROVE,
        item="dock",
        response="Approved: 1 dock for your desk.",
        reasons=["x"],
    )

    assert _check(approve, [employee_info(), eligible]) == []
    assert codes(
        _check(
            approve.model_copy(update={"response": "Approved: 2 docks."}),
            [employee_info(), eligible],
        )
    ) == [IssueCode.UNSUPPORTED_NUMBER]


def test_counts_on_file_are_not_checked_without_the_employee_record() -> None:
    # No get_employee_info in the run: the critic covers such claims instead.
    assert _check(deny("You have 2 monitors on file."), [cooldown()]) == []


def test_an_item_with_none_on_file_has_a_count_of_zero() -> None:
    record = employee_info()
    laptop_only = {**record.observation, "equipment": record.observation["equipment"][:1]}
    actions = [record.model_copy(update={"observation": laptop_only}), cooldown(), ticket()]

    issues = _check(escalate("You have 1 monitor on file.", "TKT-000007"), actions)

    assert codes(issues) == [IssueCode.UNSUPPORTED_NUMBER]


def test_facts_from_another_employees_lookups_are_not_evidence() -> None:
    other = _action(
        "get_employee_info",
        {"employee_id": "E1005"},
        {"employee_id": "E1005", "hire_date": "2027-06-01", "equipment": []},
    )

    issues = _check(deny("You can request one on 2027-06-01."), [*B_ACTIONS, other])

    assert codes(issues) == [IssueCode.UNSUPPORTED_DATE]


def test_policy_limits_count_only_for_the_requesters_role() -> None:
    def limits(role: str, months: int) -> Action:
        payload = {"role": role, "policies": [{"item": "dock", "refresh_months": months}]}
        return _action("get_policy_limits", {"role": role}, payload)

    own = _check(deny("Docks: every 48 months."), [*B_ACTIONS, limits("engineer", 48)])
    other = _check(deny("Docks: every 24 months."), [*B_ACTIONS, limits("manager", 24)])

    assert own == []
    assert codes(other) == [IssueCode.UNSUPPORTED_NUMBER]


def test_lookups_are_bound_to_the_requester_canonically() -> None:
    lower = cooldown()
    lower = lower.model_copy(update={"arguments": {"employee_id": " e1002 ", "item": "monitor"}})

    assert _check(deny(f"Next date {NEXT_DATE}."), [lower]) == []
