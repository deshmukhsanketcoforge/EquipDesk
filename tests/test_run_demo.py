"""EQP-13: `python -m agent.run_demo`, the demo and the live eval (PLAN.md §7, D12).

A scripted model plays each case correctly (or not); the MCP side is the real app
over the seed data, so the observed reason codes are the tools' real answers and
the cases file is checked against the data it describes.
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
import yaml

import agent.__main__ as agent_main
import agent.run_demo as run_demo
from agent.config import AgentSettings
from agent.evals import CASES_FILE, Case, load_suite
from agent.llm import LLMClient, LLMResponse
from agent.mcp_client import McpUnavailable
from agent.run_demo import main
from tests.fakes import ScriptedLLM, critique, decision, tool_use

SUITE = load_suite()
URL_SUFFIX = "/mcp"


@pytest.fixture
def foundry_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FOUNDRY_API_KEY", "sk-test-not-a-real-key")
    monkeypatch.setenv("FOUNDRY_ENDPOINT", "https://example.services.ai.azure.com/anthropic/")
    monkeypatch.setenv("FOUNDRY_MODEL_DEPLOYMENT", "claude-opus-5-5-fast")


def _factory(llm: ScriptedLLM) -> Callable[[AgentSettings], LLMClient]:
    def build(settings: AgentSettings) -> LLMClient:
        return cast(LLMClient, llm)

    return build


def _eligibility(employee_id: str, item: str) -> LLMResponse:
    arguments = {"employee_id": employee_id, "item": item}
    return tool_use("check_request_eligibility", arguments, thought="Checking the policy.")


def _flag(case: Case, code: str) -> LLMResponse:
    arguments = {"employee_id": case.employee_id, "request": case.request, "reason": f"{code}: x"}
    return tool_use("flag_for_human_review", arguments, thought="Escalating to a reviewer.")


def _escalate(case: Case, code: str) -> list[LLMResponse]:
    return [_flag(case, code), decision("ESCALATE", response="A reviewer will look at this.")]


def played(case_id: str, *, wrong: bool = False) -> list[LLMResponse]:
    """One run of a case by a model that gets it right (or, with `wrong`,
    escalates it as OTHER), then the critic's pass."""
    case = SUITE.case(case_id)
    employee = case.employee_id
    if wrong:
        turns = _escalate(case, "OTHER")
    elif case_id == "A":
        turns = [_eligibility(employee, "monitor"), decision("APPROVE", "monitor")]
    elif case_id == "B":
        turns = [_eligibility(employee, "monitor"), decision("DENY", "monitor")]
    elif case_id == "C":
        turns = [_eligibility(employee, "laptop"), *_escalate(case, "EXCEPTION_CLAIMED")]
    elif case_id == "D":
        turns = _escalate(case, "AMBIGUOUS_ITEM")
    elif case_id == "E":
        turns = [_eligibility(employee, "laptop"), *_escalate(case, "TOOL_NEEDS_REVIEW")]
    elif case_id == "F":
        lookup = tool_use("get_employee_info", {"employee_id": employee}, thought="Who is this?")
        turns = [lookup, *_escalate(case, "TOOL_NEEDS_REVIEW")]
    else:
        turns = _escalate(case, "POLICY_OVERRIDE_ATTEMPT")
    return [*turns, critique()]


DEMO = ["A", "B", "C", "D", "E", "F"]


def _args(url: str, *extra: str) -> list[str]:
    return ["--mcp-url", f"{url}{URL_SUFFIX}", *extra]


def cases_file(directory: Path, ids: str, *, demo: bool = False) -> Path:
    """A cases file with only some cases, run once each (k = 1)."""
    data = SUITE.model_dump(mode="json")
    data["k"] = 1
    data["thresholds"]["min_full_matches"] = 1
    data["cases"] = [{**case, "demo": demo or case["demo"]} for case in data["cases"]]
    data["cases"] = [case for case in data["cases"] if case["id"] in ids]
    path = directory / "cases.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


SEEDED_B = [
    _eligibility("E1002", "monitor"),
    decision("DENY", "monitor"),  # replaced by the seeded draft
    critique(("UNBACKED_COMMITMENT", "promises a delivery day")),
    decision("DENY", "monitor"),
    critique(),
]


# The demo (A to F, once each)


def test_the_demo_runs_a_to_f_and_matches_the_plan_table(
    live_server_url: str, foundry_env: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    llm = ScriptedLLM([turn for case_id in DEMO for turn in played(case_id)])

    code = main(
        _args(live_server_url, "--trace-dir", str(tmp_path)),
        llm_factory=_factory(llm),
        env_file=None,
    )

    assert code == 0
    out = capsys.readouterr().out
    for case_id in DEMO:
        md = (tmp_path / f"{case_id}.md").read_text()
        assert "Draft 1 → Reflection: pass" in md  # a natural confirm (deliverable #8)
        record = json.loads((tmp_path / f"{case_id}.json").read_text())
        assert record["employee_id"] == SUITE.case(case_id).employee_id
    assert not (tmp_path / "G.md").exists()
    summary = (tmp_path / "summary.md").read_text()
    assert summary in out
    rows = [line for line in summary.splitlines() if line.startswith("| ") and "Case" not in line]
    assert [row.split("|")[1].strip() for row in rows] == DEMO
    assert all(row.rstrip().endswith("| yes |") for row in rows)
    assert "| C | E1003 | ESCALATE · EXCEPTION_CLAIMED |" in summary
    assert "| E | E1005 | ESCALATE · TOOL_NEEDS_REVIEW |" in summary


def test_the_demo_exits_1_when_a_case_does_not_match_the_plan(
    live_server_url: str, foundry_env: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    script = [*played("A", wrong=True)]
    script += [turn for case_id in DEMO[1:] for turn in played(case_id)]
    llm = ScriptedLLM(script)

    code = main(
        _args(live_server_url, "--trace-dir", str(tmp_path)),
        llm_factory=_factory(llm),
        env_file=None,
    )

    assert code == 1
    assert "expected APPROVE" in capsys.readouterr().out


def test_one_case_prints_its_trace(
    live_server_url: str, foundry_env: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    llm = ScriptedLLM(played("G"))

    code = main(
        _args(live_server_url, "--case", "G", "--trace-dir", str(tmp_path)),
        llm_factory=_factory(llm),
        env_file=None,
    )

    assert code == 0
    out = capsys.readouterr().out
    assert "# ReAct trace: E1009" in out
    assert "| G | E1009 | ESCALATE · POLICY_OVERRIDE_ATTEMPT |" in out
    assert (tmp_path / "G.md").exists()


def test_the_seeded_run_is_written_as_case_seeded(
    live_server_url: str, foundry_env: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    llm = ScriptedLLM(SEEDED_B)

    code = main(
        _args(live_server_url, "--case", "B", "--seed-flawed-draft", "--trace-dir", str(tmp_path)),
        llm_factory=_factory(llm),
        env_file=None,
    )

    assert code == 0
    md = (tmp_path / "B-seeded.md").read_text()
    assert "Draft 1 [SEEDED, flawed] → Reflection: revise" in md
    assert "Draft 2 → Reflection: pass" in md
    assert not (tmp_path / "B.md").exists()
    assert "| B (SEEDED) | E1002 | DENY | monitor |" in capsys.readouterr().out
    assert not (tmp_path / "summary.md").exists()


def test_a_seeded_demo_never_replaces_the_natural_summary(
    live_server_url: str, foundry_env: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    traces = tmp_path / "traces"
    traces.mkdir()
    (traces / "summary.md").write_text("the natural demo")
    only_b = cases_file(tmp_path, "B")

    code = main(
        _args(
            live_server_url,
            "--cases-file",
            str(only_b),
            "--seed-flawed-draft",
            "--trace-dir",
            str(traces),
        ),
        llm_factory=_factory(ScriptedLLM(SEEDED_B)),
        env_file=None,
    )

    assert code == 0
    assert (traces / "summary.md").read_text() == "the natural demo"
    assert (traces / "B-seeded.md").exists()
    assert "| B (SEEDED) |" in capsys.readouterr().out


def test_an_unknown_case_is_a_usage_error(foundry_env: None) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--case", "Z"], env_file=None)
    assert exit_info.value.code == 2


# The live eval (A to G, k times each)


def test_the_eval_runs_every_case_k_times_and_writes_the_artifacts(
    live_server_url: str, foundry_env: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    script = [turn for case in SUITE.cases for _ in range(SUITE.k) for turn in played(case.id)]
    llm = ScriptedLLM(script)

    code = main(
        _args(live_server_url, "--eval", "--out", str(tmp_path)),
        llm_factory=_factory(llm),
        env_file=None,
    )

    assert code == 0
    traces = sorted(path.name for path in (tmp_path / "traces").glob("*.md"))
    assert traces == sorted(f"{case.id}-{n}.md" for case in SUITE.cases for n in (1, 2, 3))
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["passed"] is True
    assert summary["total_runs"] == summary["full_matches"] == 21
    assert {case["case_id"]: case["pass_k"] for case in summary["cases"]} == dict.fromkeys(
        "ABCDEFG", True
    )
    assert summary["input_tokens"] > 0
    report = (tmp_path / "report.md").read_text()
    assert "PASSED" in report
    assert report in capsys.readouterr().out


def test_the_eval_exits_1_when_a_critical_case_fails(
    live_server_url: str, foundry_env: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    script: list[LLMResponse] = []
    for case in SUITE.cases:
        for run in range(1, SUITE.k + 1):
            script += played(case.id, wrong=case.id == "A" and run == 3)
    llm = ScriptedLLM(script)

    code = main(
        _args(live_server_url, "--eval", "--out", str(tmp_path)),
        llm_factory=_factory(llm),
        env_file=None,
    )

    assert code == 1
    assert json.loads((tmp_path / "summary.json").read_text())["passed"] is False
    assert "FAILED" in capsys.readouterr().out


def test_the_eval_reads_k_from_the_cases_file(
    live_server_url: str, foundry_env: None, tmp_path: Path
) -> None:
    cases = tmp_path / "cases.yaml"
    text = (
        CASES_FILE.read_text()
        .replace("k: 3", "k: 1")
        .replace("min_full_matches: 2", "min_full_matches: 1")
    )
    cases.write_text(text)
    llm = ScriptedLLM([turn for case in SUITE.cases for turn in played(case.id)])

    code = main(
        _args(live_server_url, "--eval", "--cases-file", str(cases), "--out", str(tmp_path / "o")),
        llm_factory=_factory(llm),
        env_file=None,
    )

    assert code == 0
    assert len(list((tmp_path / "o" / "traces").glob("*.json"))) == 7


@pytest.mark.parametrize(
    "extra",
    [["--seed-flawed-draft"], ["--case", "A"], ["--trace-dir", "x"]],
    ids=["seeded", "one case", "trace dir"],
)
def test_the_eval_runs_whole_cases_unseeded_only(foundry_env: None, extra: list[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--eval", *extra], env_file=None)
    assert exit_info.value.code == 2


# Errors


@pytest.mark.parametrize(
    "content",
    [
        None,
        "k: [unclosed",
        "k: 3\n",
        CASES_FILE.read_text().replace("[EXCEPTION_CLAIMED]", "[GUARDRAIL_OVERRIDE]"),
    ],
    ids=["missing", "not yaml", "incomplete", "guardrail override allowed"],
)
def test_an_invalid_cases_file_exits_2_with_its_path(
    foundry_env: None, tmp_path: Path, capsys: pytest.CaptureFixture[str], content: str | None
) -> None:
    path = tmp_path / "cases.yaml"
    if content is not None:
        path.write_text(content)

    assert main(["--cases-file", str(path)], env_file=None) == 2
    err = capsys.readouterr().err
    assert f"Invalid cases file {path}" in err
    assert "Traceback" not in err


def _stop_mcp_after(monkeypatch: pytest.MonkeyPatch, runs: int) -> None:
    """MCP becomes unreachable after `runs` runs (e.g. the api restarted)."""
    real = run_demo._run_case
    done = 0

    async def flaky(*args: object) -> object:
        nonlocal done
        if done == runs:
            raise McpUnavailable("Cannot reach the MCP server: connection refused")
        done += 1
        return await real(*args)  # type: ignore[arg-type]

    monkeypatch.setattr(run_demo, "_run_case", flaky)


def test_an_eval_that_loses_mcp_partway_still_reports_the_finished_runs(
    live_server_url: str,
    foundry_env: None,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stop_mcp_after(monkeypatch, 2)
    llm = ScriptedLLM([*played("A"), *played("B")])
    out = tmp_path / "out"

    code = main(
        _args(
            live_server_url,
            "--eval",
            "--cases-file",
            str(cases_file(tmp_path, "ABC")),
            "--out",
            str(out),
        ),
        llm_factory=_factory(llm),
        env_file=None,
    )

    assert code == 1
    summary = json.loads((out / "summary.json").read_text())
    assert summary["passed"] is False
    assert summary["total_runs"] == summary["full_matches"] == 2
    assert any("C: 0 runs" in failure for failure in summary["failures"])
    captured = capsys.readouterr()
    assert "Stopped before case C run 1" in captured.err
    assert "FAILED" in captured.out
    assert (out / "report.md").exists()


def test_a_demo_that_loses_mcp_partway_writes_no_summary(
    live_server_url: str,
    foundry_env: None,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stop_mcp_after(monkeypatch, 1)

    code = main(
        _args(live_server_url, "--trace-dir", str(tmp_path)),
        llm_factory=_factory(ScriptedLLM(played("A"))),
        env_file=None,
    )

    assert code == 1
    assert not (tmp_path / "summary.md").exists()
    assert (tmp_path / "A.md").exists()
    captured = capsys.readouterr()
    assert "| A | E1001 | APPROVE |" in captured.out
    assert "Stopped before case B run 1" in captured.err


def test_an_unwritable_eval_summary_exits_1_after_printing_the_report(
    live_server_url: str, foundry_env: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    (out / "summary.json").mkdir(parents=True)  # a directory where the file goes

    code = main(
        _args(
            live_server_url,
            "--eval",
            "--cases-file",
            str(cases_file(tmp_path, "G")),
            "--out",
            str(out),
        ),
        llm_factory=_factory(ScriptedLLM(played("G"))),
        env_file=None,
    )

    assert code == 1
    captured = capsys.readouterr()
    assert "PASSED" in captured.out
    assert "Could not write summary.json" in captured.err


def test_an_unwritable_demo_summary_exits_1(
    live_server_url: str, foundry_env: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "summary.md").mkdir()

    code = main(
        _args(
            live_server_url,
            "--cases-file",
            str(cases_file(tmp_path, "G", demo=True)),
            "--trace-dir",
            str(tmp_path),
        ),
        llm_factory=_factory(ScriptedLLM(played("G"))),
        env_file=None,
    )

    assert code == 1
    assert "Could not write summary.md" in capsys.readouterr().err


def test_missing_configuration_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("FOUNDRY_API_KEY", "FOUNDRY_ENDPOINT", "FOUNDRY_MODEL_DEPLOYMENT"):
        monkeypatch.delenv(name, raising=False)

    assert main([], env_file=None) == 2
    assert "FOUNDRY_API_KEY" in capsys.readouterr().err


def test_an_unreachable_server_exits_2(
    foundry_env: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        ["--mcp-url", "http://127.0.0.1:9/mcp", "--trace-dir", str(tmp_path)],
        llm_factory=_factory(ScriptedLLM([])),
        env_file=None,
    )

    assert code == 2
    assert capsys.readouterr().err


def test_an_unwritable_trace_dir_exits_1(
    live_server_url: str, foundry_env: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")

    code = main(
        _args(live_server_url, "--case", "A", "--trace-dir", str(blocker / "traces")),
        llm_factory=_factory(ScriptedLLM(played("A"))),
        env_file=None,
    )

    assert code == 1
    assert "Could not write" in capsys.readouterr().err


# `make demo` (the agent compose service) runs the demo


def test_the_agent_entry_point_runs_the_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str] | None] = []

    def fake_main(argv: list[str] | None = None) -> int:
        calls.append(argv)
        return 7

    monkeypatch.setattr(agent_main, "run_demo_main", fake_main)

    assert agent_main.main() == 7
    assert calls == [None]
