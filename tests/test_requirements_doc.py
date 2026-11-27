"""EQP-2: docs/requirements.md stays in sync with the code.

- Every code in server/codes.py is documented, and the doc lists no unknown code.
- Rule IDs (R-01, R-02, ...) are unique and sequential.
- Every rule names the tests that cover it; any test file it names exists.
- Every reason and escalation code is produced by at least one rule.
"""

import re
from pathlib import Path

import pytest

from server.codes import ErrorCode, EscalationCode, ReasonCode, Verdict

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "requirements.md"

ALL_CODES = {c.value for enum in (Verdict, ReasonCode, EscalationCode, ErrorCode) for c in enum}
RULE_ROW = re.compile(r"^\| (R-\d{2}) \|(.+)\|\s*$", re.MULTILINE)
CODE_ROW = re.compile(r"^\| `([A-Z][A-Z_]+)` \|", re.MULTILINE)
TEST_PATH = re.compile(r"tests/[\w/]+\.py")


@pytest.fixture(scope="module")
def doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _section(doc: str, heading: str) -> str:
    start = doc.index(f"\n## {heading}")
    end = doc.find("\n## ", start + 1)
    return doc[start : end if end != -1 else len(doc)]


def _rules(doc: str) -> list[tuple[str, list[str]]]:
    return [
        (rule_id, [cell.strip() for cell in rest.split("|")])
        for rule_id, rest in RULE_ROW.findall(doc)
    ]


def test_codes_section_documents_exactly_the_enum_values(doc: str) -> None:
    documented = set(CODE_ROW.findall(_section(doc, "Codes")))
    assert documented == ALL_CODES


def test_rule_ids_are_unique_and_sequential(doc: str) -> None:
    ids = [rule_id for rule_id, _ in _rules(doc)]
    assert ids, "no rules found"
    assert ids == [f"R-{n:02d}" for n in range(1, len(ids) + 1)]


def test_every_rule_names_its_tests(doc: str) -> None:
    for rule_id, cells in _rules(doc):
        tests_cell = cells[-1]
        assert tests_cell, f"{rule_id} has no test mapping"
        for path in TEST_PATH.findall(tests_cell):
            assert (ROOT / path).is_file(), f"{rule_id} names missing test file {path}"


@pytest.mark.parametrize(
    "code", sorted({c.value for c in ReasonCode} | {c.value for c in EscalationCode})
)
def test_every_reason_and_escalation_code_comes_from_a_rule(doc: str, code: str) -> None:
    rule_text = " ".join(" ".join(cells) for _, cells in _rules(doc))
    assert f"`{code}`" in rule_text, f"no rule produces {code}"
