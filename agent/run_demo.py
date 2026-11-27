"""Run the demo or the live eval on the real model (PLAN.md §7, D12).

    python -m agent.run_demo                          # demo: A to F once each
    python -m agent.run_demo --case B --seed-flawed-draft
    python -m agent.run_demo --eval                   # every case k times, scored

Cases come from `evals/cases.yaml`. The demo writes `<case>.md|json` traces and
`summary.md` (each run's decision and response against the §7 table; one --case,
a seeded run or a demo that stopped partway only prints its rows, labelled SEEDED
when seeded). The eval
writes `traces/<case>-<run>.md|json`, `summary.json` and `report.md` (per-case
pass^k, failures, tokens and estimated cost).
"""

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

import anyio
import yaml
from pydantic import ValidationError

from agent.config import AgentSettings, ConfigError, load_settings
from agent.evals import (
    CASES_FILE,
    Case,
    EvalSuite,
    RunResult,
    demo_summary,
    eval_report,
    load_suite,
    miss_reason,
    run_result,
    score,
)
from agent.llm import FoundryLLMClient, LLMClient
from agent.mcp_client import EquipDeskMcp, McpUnavailable
from agent.react_agent import run
from agent.reflection import LLMCritic
from agent.trace import Trace, to_markdown, write_trace

TRACE_DIR = Path("artifacts/traces")
EVAL_DIR = Path("artifacts/eval")


class TraceNotWritten(Exception):
    """A trace could not be written; the run stops rather than spend more calls."""


async def _run_case(llm: LLMClient, url: str, model: str, case: Case, seed: bool) -> Trace:
    async with EquipDeskMcp(url) as mcp:
        return await run(
            llm,
            mcp,
            employee_id=case.employee_id,
            request=case.request,
            model=model,
            critic=LLMCritic(llm),
            seed_flawed_draft=seed,
        )


async def _run_all(
    llm: LLMClient,
    url: str,
    model: str,
    jobs: list[tuple[Case, int, str]],
    seed: bool,
    trace_dir: Path,
    show_trace: bool,
) -> tuple[list[RunResult], str | None]:
    """Run each (case, run number, trace name) in order, writing each trace as it
    finishes. If MCP becomes unreachable, stop and return the finished runs with
    the reason, so they are still reported; the missing runs fail the eval."""
    results: list[RunResult] = []
    for case, number, name in jobs:
        try:
            trace = await _run_case(llm, url, model, case, seed)
        except McpUnavailable as exc:
            return results, f"Stopped before case {case.id} run {number}: {exc}"
        try:
            write_trace(trace, trace_dir, name)
        except (OSError, ValueError) as exc:
            raise TraceNotWritten(f"Could not write the trace to {trace_dir}: {exc}") from exc
        result = run_result(case.id, number, trace)
        if show_trace:
            print(to_markdown(trace))
        miss = miss_reason(case, result)
        verdict = "as planned" if miss is None else f"NOT as planned: {miss}"
        print(f"Case {case.id} run {number}: {verdict}", file=sys.stderr)
        results.append(result)
    return results, None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the EquipDesk demo or live eval.")
    parser.add_argument("--eval", action="store_true", help="run every case k times and score")
    parser.add_argument("--case", help="run one case (any ID in the cases file)")
    parser.add_argument(
        "--seed-flawed-draft",
        action="store_true",
        help="replace the first draft with a known-bad one (labelled SEEDED)",
    )
    parser.add_argument("--cases-file", type=Path, default=CASES_FILE)
    parser.add_argument("--mcp-url", help="MCP endpoint (default: MCP_URL or localhost)")
    parser.add_argument("--trace-dir", type=Path, help=f"demo traces (default: {TRACE_DIR})")
    parser.add_argument(
        "--out", type=Path, default=EVAL_DIR, help="eval artifacts, traces in <out>/traces"
    )
    return parser


def _jobs(
    args: argparse.Namespace, suite: EvalSuite, parser: argparse.ArgumentParser
) -> list[tuple[Case, int, str]]:
    if args.eval:
        if args.case or args.seed_flawed_draft:
            parser.error("--eval runs every case unseeded; drop --case and --seed-flawed-draft")
        if args.trace_dir is not None:
            parser.error("--eval writes its traces to <out>/traces; use --out, not --trace-dir")
        return [
            (case, number, f"{case.id}-{number}")
            for case in suite.cases
            for number in range(1, suite.k + 1)
        ]
    if args.case:
        try:
            cases = [suite.case(args.case)]
        except KeyError as exc:
            parser.error(str(exc.args[0]))
    else:
        cases = [case for case in suite.cases if case.demo]
    suffix = "-seeded" if args.seed_flawed_draft else ""
    return [(case, 1, f"{case.id}{suffix}") for case in cases]


def main(
    argv: list[str] | None = None,
    *,
    llm_factory: Callable[[AgentSettings], LLMClient] = FoundryLLMClient,
    env_file: str | None = ".env",
) -> int:
    """Exit code: 0 when every run went as planned (demo) or the eval passed; 1 when
    not, when MCP failed partway or an output could not be written; 2 when the
    cases file or configuration is invalid, or MCP is unreachable from the start."""
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        suite = load_suite(args.cases_file)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        print(f"Invalid cases file {args.cases_file}: {exc}", file=sys.stderr)
        return 2
    jobs = _jobs(args, suite, parser)

    try:
        settings = load_settings(env_file)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2

    trace_dir = args.out / "traces" if args.eval else (args.trace_dir or TRACE_DIR)
    try:
        results, stopped = anyio.run(
            _run_all,
            llm_factory(settings),
            args.mcp_url or settings.mcp_url,
            settings.foundry_model_deployment,
            jobs,
            args.seed_flawed_draft,
            trace_dir,
            bool(args.case),
        )
    except TraceNotWritten as exc:
        print(exc, file=sys.stderr)
        return 1
    if stopped is not None:
        print(stopped, file=sys.stderr)
        if not results:
            return 2

    if args.eval:
        result = score(suite, results)  # runs that never happened fail it
        report = eval_report(result)
        print(report, end="")
        outputs = {"summary.json": result.model_dump_json(indent=2) + "\n", "report.md": report}
        ok = result.passed
    else:
        summary = demo_summary(suite, results)
        print(summary, end="")
        # summary.md is the evidence for the full, natural demo only: one case or a
        # seeded run must not replace it, nor may a demo that stopped partway.
        complete = not args.case and not args.seed_flawed_draft and stopped is None
        outputs = {"summary.md": summary} if complete else {}
        ok = stopped is None and all(miss_reason(suite.case(r.case_id), r) is None for r in results)
    out_dir = args.out if args.eval else trace_dir
    try:
        for name, content in outputs.items():
            (out_dir / name).write_text(content)
    except OSError as exc:
        print(f"Could not write {name} to {out_dir}: {exc}", file=sys.stderr)
        return 1
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
