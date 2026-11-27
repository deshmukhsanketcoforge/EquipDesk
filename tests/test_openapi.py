"""EQP-14: the committed OpenAPI schema is the API contract (PLAN.md §6).

`openapi.json` at the repo root is exported from the app. If a route, model or
code set changes without re-exporting it (`make openapi`), these tests fail.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from scripts.export_openapi import OPENAPI_FILE, main, schema_text
from server.codes import EscalationCode, ReasonCode, Verdict

ROOT = Path(__file__).resolve().parents[1]
FIX = "the API changed: run `make openapi` and commit openapi.json"


def test_the_committed_schema_matches_the_live_app(client: TestClient) -> None:
    assert ROOT / "openapi.json" == OPENAPI_FILE
    committed = json.loads(OPENAPI_FILE.read_text())

    assert client.get("/openapi.json").json() == committed, FIX


def test_the_committed_file_is_exactly_what_the_export_writes() -> None:
    assert OPENAPI_FILE.read_text() == schema_text(), FIX


def test_the_schema_has_the_four_tool_operations_and_health() -> None:
    paths = json.loads(OPENAPI_FILE.read_text())["paths"]

    assert set(paths) == {
        "/health",
        "/v1/employees/{employee_id}",
        "/v1/policies/{role}",
        "/v1/eligibility",
        "/v1/review-tickets",
    }
    assert set(paths["/v1/eligibility"]) == {"post"}
    assert set(paths["/v1/review-tickets"]) == {"post"}


def test_the_schema_lists_the_closed_code_sets() -> None:
    # D4: response models use the enums, so the contract lists the allowed values.
    schemas = json.loads(OPENAPI_FILE.read_text())["components"]["schemas"]
    enums = {tuple(sorted(s["enum"])) for s in schemas.values() if "enum" in s}

    for codes in (Verdict, ReasonCode, EscalationCode):
        assert tuple(sorted(code.value for code in codes)) in enums, codes.__name__


def test_the_export_writes_the_file_and_check_detects_drift(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "openapi.json"

    assert main(["--out", str(out)]) == 0
    assert out.read_text() == schema_text()
    assert main(["--check", "--out", str(out)]) == 0

    out.write_text(out.read_text().replace("EquipDesk API", "Drifted API"))
    assert main(["--check", "--out", str(out)]) == 1
    assert "make openapi" in capsys.readouterr().err


def test_check_reports_a_missing_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--check", "--out", str(tmp_path / "missing.json")]) == 1
    assert "make openapi" in capsys.readouterr().err
