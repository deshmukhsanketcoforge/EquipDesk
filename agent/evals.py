"""The demo and eval cases (`evals/cases.yaml`) and how runs are scored.

PLAN.md §7 (the case table) and D12 (the live-eval rules). A run of a case is
reduced to a `RunResult`, read from its trace; `score` applies D12 to k runs of
every case. Critical failures (zero tolerance): a case that must APPROVE or DENY
ends otherwise; an escalate case is APPROVEd or DENYed; a final decision breaks
the guardrails (D9). Otherwise a run fully matches when its decision, escalation
code and observed tool reason code all match; each case needs a minimum of full
matches and the suite a minimum rate.
"""

from collections.abc import Iterator
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agent.decision import DecisionKind, Draft
from agent.guardrails import canonical_id, check
from agent.trace import Trace
from server.codes import EscalationCode, ReasonCode

CASES_FILE = Path(__file__).resolve().parent.parent / "evals" / "cases.yaml"

_REASON_CODES = frozenset(code.value for code in ReasonCode)
_AUTO_DECISIONS = (DecisionKind.APPROVE, DecisionKind.DENY)
_RESPONSE_CHARS = 200
"""How much of a response the demo summary shows; the trace has all of it."""


class Critical(StrEnum):
    EXACT = "exact"
    """Every run must end in the expected decision (A approves, B denies)."""
    NO_AUTO_DECISION = "no_auto_decision"
    """No run may APPROVE or DENY: a wrong automatic decision is the dangerous
    error, a wrong escalation code only costs a reviewer minutes."""


class AllowedCode(BaseModel):
    """An escalation code that counts as a full match."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: EscalationCode
    when_observed: ReasonCode | None = None
    """Accept the code only if this tool reason code appeared in the run."""

    @model_validator(mode="before")
    @classmethod
    def _from_plain_code(cls, value: Any) -> Any:
        return {"code": value} if isinstance(value, str) else value


class Case(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    """Also the trace file name."""
    employee_id: str
    request: str = Field(min_length=1)
    decision: DecisionKind
    allowed_codes: list[AllowedCode] = []
    observed_reason: ReasonCode | None = None
    """A tool reason code that must appear in some observation of the run."""
    critical: Critical
    demo: bool = False
    """One of the brief's demo requests (run by the demo, not only the eval)."""

    @field_validator("allowed_codes")
    @classmethod
    def _never_guardrail_override(cls, value: list[AllowedCode]) -> list[AllowedCode]:
        if any(allowed.code is EscalationCode.GUARDRAIL_OVERRIDE for allowed in value):
            raise ValueError("GUARDRAIL_OVERRIDE is a safe catch, never a full match")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.decision is DecisionKind.ESCALATE:
            if not self.allowed_codes:
                raise ValueError(f"case {self.id}: an escalate case needs allowed_codes")
            if self.critical is not Critical.NO_AUTO_DECISION:
                raise ValueError(f"case {self.id}: an escalate case is no_auto_decision")
        else:
            if self.allowed_codes:
                raise ValueError(f"case {self.id}: allowed_codes are for escalate cases")
            if self.critical is not Critical.EXACT:
                raise ValueError(f"case {self.id}: an approve or deny case is exact")
        return self


class Thresholds(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    min_full_matches: int = Field(ge=0)
    """Per case, out of k."""
    suite_min_rate: Decimal = Field(ge=0, le=1)
    """Full matches over all case-runs."""


class Pricing(BaseModel):
    """List price per million tokens, for the cost estimate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_usd_per_mtok: Decimal = Field(ge=0)
    output_usd_per_mtok: Decimal = Field(ge=0)


class EvalSuite(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    k: int = Field(ge=1)
    thresholds: Thresholds
    pricing: Pricing
    cases: list[Case] = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        ids = [case.id for case in self.cases]
        duplicates = sorted({case_id for case_id in ids if ids.count(case_id) > 1})
        if duplicates:
            raise ValueError(f"duplicate case ids: {', '.join(duplicates)}")
        if self.thresholds.min_full_matches > self.k:
            raise ValueError("min_full_matches cannot exceed k")
        return self

    def case(self, case_id: str) -> Case:
        for case in self.cases:
            if case.id == case_id:
                return case
        raise KeyError(f"No case {case_id!r} in the cases file.")


def load_suite(path: Path = CASES_FILE) -> EvalSuite:
    return EvalSuite.model_validate(yaml.safe_load(path.read_text()))


class RunResult(BaseModel):
    """One run of one case, as the scoring sees it."""

    model_config = ConfigDict(frozen=True)

    case_id: str
    run: int
    decision: DecisionKind | None
    """None when the run ended without a decision: its forced escalation failed, or
    the run could not be completed at all."""
    no_decision: str | None = None
    """Why there is no decision. Not a guardrail breach: nothing was approved or
    denied. A miss on an escalate case, critical on an exact one."""
    seeded: bool = False
    """The first draft was a known-bad one injected by --seed-flawed-draft."""
    item: str | None = None
    escalation_code: EscalationCode | None = None
    forced: bool = False
    ticket_id: str | None = None
    response: str | None = None
    observed_reasons: list[ReasonCode] = []
    """The tool reason codes that executed calls about the requesting employee
    returned (by the call's employee_id, so error payloads count too)."""
    breach: str | None = None
    """Why the final decision is not backed by the observations before it (D9)."""
    input_tokens: int = 0
    output_tokens: int = 0
    model_calls: int = 0
    critic_calls: int = 0


def _reason_codes(value: Any) -> Iterator[ReasonCode]:
    """The `reason_code` values anywhere in an observation, error payloads included."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "reason_code" and item in _REASON_CODES:
                yield ReasonCode(item)
            else:
                yield from _reason_codes(item)
    elif isinstance(value, list):
        for item in value:
            yield from _reason_codes(item)


def run_result(case_id: str, run: int, trace: Trace) -> RunResult:
    """Read a run from its trace, checking the trace assertion: an approve or deny
    is preceded by a matching eligibility observation, an escalate by a flag
    observation (the guardrails' own check, D9)."""
    actions = [action for step in trace.steps for action in step.actions]
    requester = canonical_id(trace.employee_id)
    observed = {
        code
        for action in actions
        if action.executed
        and canonical_id(str(action.arguments.get("employee_id", ""))) == requester
        for code in _reason_codes(action.observation)
    }
    decision = trace.outcome.decision
    breach: str | None = None
    no_decision: str | None = None
    if decision is None:
        why = trace.outcome.escalation_error or trace.outcome.ended_by.value
        no_decision = f"the forced escalation failed ({why})"
    else:
        draft = Draft(
            decision=decision.decision,
            item=decision.item,
            response=decision.response,
            reasons=decision.reasons,
            ticket_id=decision.ticket_id,
        )
        breach = check(draft, actions, employee_id=trace.employee_id, request=trace.request)
    return RunResult(
        case_id=case_id,
        run=run,
        decision=decision.decision if decision else None,
        item=decision.item if decision else None,
        escalation_code=decision.escalation_code if decision else None,
        forced=decision.forced if decision else False,
        ticket_id=decision.ticket_id if decision else None,
        response=decision.response if decision else None,
        no_decision=no_decision,
        seeded=trace.seeded,
        observed_reasons=sorted(observed),
        breach=breach,
        input_tokens=trace.total_input_tokens,
        output_tokens=trace.total_output_tokens,
        model_calls=sum(1 for step in trace.steps if step.actor == "model"),
        critic_calls=sum(1 for step in trace.steps if step.actor == "reflection"),
    )


def describe(result: RunResult) -> str:
    """The decision as one short label, e.g. `ESCALATE · EXCEPTION_CLAIMED`."""
    if result.decision is None:
        return "no decision"
    if result.escalation_code is None:
        return result.decision.value
    return f"{result.decision.value} · {result.escalation_code.value}"


def _expected(case: Case) -> str:
    if not case.allowed_codes:
        return case.decision.value
    codes = []
    for allowed in case.allowed_codes:
        condition = f" (after {allowed.when_observed.value})" if allowed.when_observed else ""
        codes.append(f"{allowed.code.value}{condition}")
    return f"{case.decision.value} · {' or '.join(codes)}"


def critical_failure(case: Case, result: RunResult) -> str | None:
    """Why this run fails the whole eval on its own, or None."""
    if result.breach is not None:
        return f"guardrail breach: {result.breach}"
    if case.critical is Critical.EXACT and result.decision is not case.decision:
        why = f" ({result.no_decision})" if result.no_decision else ""
        return f"ended {describe(result)}{why}, expected {case.decision.value}"
    if case.critical is Critical.NO_AUTO_DECISION and result.decision in _AUTO_DECISIONS:
        return f"{describe(result)} on a case that must escalate"
    return None


def miss_reason(case: Case, result: RunResult) -> str | None:
    """Why this run is not a full match, or None when it is."""
    critical = critical_failure(case, result)
    if critical is not None:
        return critical
    if result.decision is None:  # an escalate case; on an exact case it is critical
        return f"ended with no decision: {result.no_decision or 'unknown reason'}"
    # Past these checks, an exact case ended in its decision and an escalate case
    # escalated: what is left to match is the code and the observed reason.
    if case.decision is DecisionKind.ESCALATE and not any(
        allowed.code is result.escalation_code
        and (allowed.when_observed is None or allowed.when_observed in result.observed_reasons)
        for allowed in case.allowed_codes
    ):
        forced = " (forced by the code)" if result.forced else ""
        return f"ended {describe(result)}{forced}, expected {_expected(case)}"
    if case.observed_reason is not None and case.observed_reason not in result.observed_reasons:
        return f"no observation returned {case.observed_reason.value}"
    return None


class CaseScore(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    expected: str
    runs: list[RunResult]
    full_matches: int
    pass_k: bool
    """Every run fully matched (pass^k)."""
    misses: list[str]
    critical_failures: list[str]


class Score(BaseModel):
    model_config = ConfigDict(frozen=True)

    passed: bool
    failures: list[str]
    k: int
    thresholds: Thresholds
    cases: list[CaseScore]
    full_matches: int
    total_runs: int
    rate: Decimal
    input_tokens: int
    output_tokens: int
    pricing: Pricing
    cost_usd: Decimal
    """An estimate at list price (`pricing`), not the bill."""


def _score_case(suite: EvalSuite, case: Case, runs: list[RunResult]) -> CaseScore:
    misses, critical = [], []
    for result in runs:
        failure = critical_failure(case, result)
        if failure is not None:
            critical.append(f"run {result.run}: {failure}")
        miss = miss_reason(case, result)
        if miss is not None:
            misses.append(f"run {result.run}: {miss}")
    full = len(runs) - len(misses)
    return CaseScore(
        case_id=case.id,
        expected=_expected(case),
        runs=runs,
        full_matches=full,
        pass_k=bool(runs) and full == len(runs),
        misses=misses,
        critical_failures=critical,
    )


def score(suite: EvalSuite, results: list[RunResult]) -> Score:
    """Apply D12 to k runs of every case."""
    known = {case.id for case in suite.cases}
    unknown = sorted({result.case_id for result in results} - known)
    if unknown:
        raise ValueError(f"Results for cases not in the suite: {', '.join(unknown)}")

    cases = [
        _score_case(suite, case, [result for result in results if result.case_id == case.id])
        for case in suite.cases
    ]
    failures = [
        f"CRITICAL {case.case_id} {failure}" for case in cases for failure in case.critical_failures
    ]
    for case in cases:
        if len(case.runs) != suite.k:
            failures.append(f"{case.case_id}: {len(case.runs)} runs, expected {suite.k}")
        if case.full_matches < suite.thresholds.min_full_matches:
            failures.append(
                f"{case.case_id}: {case.full_matches}/{suite.k} full matches, "
                f"needs {suite.thresholds.min_full_matches}"
            )
    full = sum(case.full_matches for case in cases)
    total = sum(len(case.runs) for case in cases)
    rate = Decimal(full) / Decimal(total) if total else Decimal(0)
    if rate < suite.thresholds.suite_min_rate:
        failures.append(
            f"suite: {_percent(rate)} of runs fully match ({full}/{total}), "
            f"needs {_percent(suite.thresholds.suite_min_rate)}"
        )
    input_tokens = sum(result.input_tokens for result in results)
    output_tokens = sum(result.output_tokens for result in results)
    cost = (
        Decimal(input_tokens) * suite.pricing.input_usd_per_mtok
        + Decimal(output_tokens) * suite.pricing.output_usd_per_mtok
    ) / Decimal(1_000_000)
    return Score(
        passed=not failures,
        failures=failures,
        k=suite.k,
        thresholds=suite.thresholds,
        cases=cases,
        full_matches=full,
        total_runs=total,
        rate=rate,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        pricing=suite.pricing,
        cost_usd=cost,
    )


def _percent(rate: Decimal) -> str:
    return f"{rate * 100:.0f}%"


def _cell(text: str | None, limit: int | None = None) -> str:
    """Text we don't control as one Markdown table cell."""
    if not text:
        return "-"
    flat = " ".join(text.split()).replace("|", "\\|")
    if limit is not None and len(flat) > limit:
        flat = flat[: limit - 1].rstrip() + "…"
    return flat


def eval_report(result: Score) -> str:
    """The live-eval report: per-case pass^k, failures, misses, tokens and cost."""
    verdict = "PASSED" if result.passed else "FAILED"
    lines = [
        f"# Live eval: {verdict}",
        "",
        f"{result.k} runs per case · {result.full_matches}/{result.total_runs} runs fully "
        f"match ({_percent(result.rate)}; the suite needs "
        f"{_percent(result.thresholds.suite_min_rate)}, each case "
        f"{result.thresholds.min_full_matches}/{result.k})",
        "",
        f"| Case | Expected | Full matches | pass^{result.k} | Runs | Critical |",
        "|---|---|---|---|---|---|",
    ]
    for case in result.cases:
        runs = ", ".join(describe(run) for run in case.runs) or "-"
        critical = "FAILED" if case.critical_failures else "ok"
        pass_k = "yes" if case.pass_k else "no"
        lines.append(
            f"| {case.case_id} | {case.expected} | {case.full_matches}/{len(case.runs)} "
            f"| {pass_k} | {runs} | {critical} |"
        )
    if result.failures:
        lines += ["", "## Failures", "", *(f"- {_cell(failure)}" for failure in result.failures)]
    misses = [f"{case.case_id} {miss}" for case in result.cases for miss in case.misses]
    if misses:
        lines += ["", "## Misses", "", *(f"- {_cell(miss)}" for miss in misses)]
    lines += [
        "",
        f"Tokens: {result.input_tokens:,} in / {result.output_tokens:,} out · estimated cost "
        f"${result.cost_usd:.2f} at list price (${result.pricing.input_usd_per_mtok} / "
        f"${result.pricing.output_usd_per_mtok} per MTok)",
    ]
    return "\n".join(lines) + "\n"


def demo_summary(suite: EvalSuite, results: list[RunResult]) -> str:
    """One row per demo run: the final decision and response (deliverable #7),
    checked against the PLAN.md §7 table. A seeded run is labelled SEEDED."""
    lines = [
        "| Case | Employee | Decision | Item | Ticket | Response | Expected | Matches the plan |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for result in results:
        case = suite.case(result.case_id)
        miss = miss_reason(case, result)
        lines.append(
            f"| {case.id}{' (SEEDED)' if result.seeded else ''} | {case.employee_id} "
            f"| {describe(result)} | {_cell(result.item)} "
            f"| {_cell(result.ticket_id)} | {_cell(result.response, _RESPONSE_CHARS)} "
            f"| {_expected(case)} | {'yes' if miss is None else _cell(f'no: {miss}')} |"
        )
    return "\n".join(lines) + "\n"
