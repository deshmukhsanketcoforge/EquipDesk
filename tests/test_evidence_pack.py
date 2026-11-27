"""EQP-14: the evidence pack stays true to the repo.

- docs/decisions.md is the PLAN.md §3 decision table.
- docs/submission-checklist.md maps the brief's 9 deliverables (in its order and
  wording) to sources that exist.
- The README's quick start works with plain `docker compose`, its links and make
  targets exist, and it lists the deferred work as next steps (PLAN.md §11).
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLAN = (ROOT / "PLAN.md").read_text()
README = (ROOT / "README.md").read_text()
DECISIONS = ROOT / "docs" / "decisions.md"
CHECKLIST = ROOT / "docs" / "submission-checklist.md"

BRIEF_DELIVERABLES = [
    "Requirements doc and policy rules you defined",
    "Terminal output confirming your minimal one-tool server and client connection worked",
    "Your MCP server code (all four tools)",
    "Your unit tests for each tool's underlying function, and terminal output showing them passing",
    "Your agent code, including the MCP client connection",
    "Full ReAct traces (Thought/Action/Observation) for all 4 test requests",
    "The final decision/response for each of the 4 requests, including escalation reasons",
    "Evidence of the reflection step catching or confirming at least one draft",
    "A passing pipeline run",
]
"""The brief's "Deliverable" list, in its order."""

DEFERRED = [
    "Dependabot",
    "Authentication",
    "OpenTelemetry",
    "Rate limiting",
    "Kubernetes",
    "Human-review UI",
    "Retries",
    "Multi-tenant",
    "Structured JSON logs",
]
"""PLAN.md §11 "Explicitly defer", as named in the README."""

DECISION_ROW = re.compile(r"^\| D\d+ \|.*\|$", re.MULTILINE)
DELIVERABLE_ROW = re.compile(r"^\| (\d|Bonus) \| ([^|]+) \| (.+) \|$", re.MULTILINE)
BACKTICKED = re.compile(r"`([^`\s]+)`")
LINK = re.compile(r"\]\((?!https?://|#)([^)#\s]+)")


def _section(text: str, heading: str) -> str:
    start = text.index(f"\n## {heading}")
    end = text.find("\n## ", start + 1)
    return text[start : end if end != -1 else len(text)]


ROOT_FILES = {"Makefile", "Dockerfile"}
FILE_NAME = re.compile(r"^[\w.*-]+\.\w+$")


def _looks_like_path(token: str) -> bool:
    """A repo file or glob: anything with a folder, a file extension, or a known
    extensionless root file. URLs and absolute paths are not repo files."""
    if token.startswith(("/", "http")):
        return False
    return "/" in token or token in ROOT_FILES or bool(FILE_NAME.match(token))


# docs/decisions.md


def test_the_decision_record_is_the_plan_decision_table() -> None:
    plan_rows = DECISION_ROW.findall(_section(PLAN, "3. Decision review"))
    doc_rows = DECISION_ROW.findall(DECISIONS.read_text())

    assert len(plan_rows) == 17
    assert doc_rows == plan_rows, "docs/decisions.md must copy PLAN.md §3 exactly"


def test_the_decision_record_points_back_to_the_plan() -> None:
    assert "PLAN.md" in DECISIONS.read_text()


# docs/submission-checklist.md


@pytest.fixture(scope="module")
def deliverables() -> list[tuple[str, str, str]]:
    return DELIVERABLE_ROW.findall(CHECKLIST.read_text())


def test_the_checklist_lists_the_nine_deliverables_in_the_briefs_order(
    deliverables: list[tuple[str, str, str]],
) -> None:
    numbered = [(number, text.strip()) for number, text, _ in deliverables if number != "Bonus"]

    assert numbered == [(str(n), text) for n, text in enumerate(BRIEF_DELIVERABLES, start=1)]


def test_every_deliverable_names_a_source_that_exists(
    deliverables: list[tuple[str, str, str]],
) -> None:
    for number, _, source in deliverables:
        paths = [token for token in BACKTICKED.findall(source) if _looks_like_path(token)]
        assert paths, f"deliverable {number} names no source file"
        for path in paths:
            assert list(ROOT.glob(path)), f"deliverable {number}: {path} does not exist"


@pytest.mark.parametrize(
    ("token", "is_path"),
    [
        ("Makefile", True),
        ("pyproject.toml", True),
        ("docker-compose.yl", True),  # a typo is still checked, and then fails
        ("server/services/*.py", True),
        ("http://localhost:8000/docs", False),
        ("/docs", False),
        ("SEEDED_DRAFT", False),
        ("--seed-flawed-draft", False),
    ],
)
def test_root_files_and_typos_are_checked_but_urls_and_names_are_not(
    token: str, is_path: bool
) -> None:
    assert _looks_like_path(token) is is_path


def test_the_checklist_includes_the_swagger_bonus(
    deliverables: list[tuple[str, str, str]],
) -> None:
    assert any(number == "Bonus" and "/docs" in source for number, _, source in deliverables)


# README


def test_the_quick_start_works_with_plain_docker_compose() -> None:
    quick_start = _section(README, "Quick start")

    assert "docker compose up" in quick_start
    assert "docker compose exec api python -m server.db.seed" in quick_start


def test_every_make_target_the_readme_names_exists() -> None:
    targets = set(re.findall(r"^([a-z-]+):", (ROOT / "Makefile").read_text(), re.MULTILINE))
    named = set(re.findall(r"`make ([a-z-]+)", README)) | set(
        re.findall(r"^make ([a-z-]+)", README, re.MULTILINE)
    )

    assert named
    assert named <= targets, named - targets


def test_every_relative_link_in_the_readme_and_docs_exists() -> None:
    for doc in [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]:
        for link in LINK.findall(doc.read_text()):
            assert (doc.parent / link).exists(), f"{doc.name}: broken link {link}"


def test_the_readme_lists_the_deferred_work_as_next_steps() -> None:
    next_steps = _section(README, "Next steps")

    for item in DEFERRED:
        assert item in next_steps, item


def test_the_readme_links_the_evidence_pack() -> None:
    for doc in ("docs/decisions.md", "docs/submission-checklist.md", "openapi.json"):
        assert f"]({doc})" in README, doc
