# Submission checklist

The brief asks for a single PDF of screenshots, in this order. Each row names where the screenshot comes from. `tests/test_evidence_pack.py` checks that the list matches the brief and that every file named here exists.

| # | Deliverable | Source |
|---|---|---|
| 1 | Requirements doc and policy rules you defined | `docs/requirements.md`: the Decisions, Catalog, Policy, Eligibility rules, Escalation rules, Decision guardrails, Reflection rules, Tool contract rules, Codes and Worked examples sections (rules R-01 to R-26 and the closed code sets). The same catalog and policies are seeded into the database from `server/db/seed_data.py` |
| 2 | Terminal output confirming your minimal one-tool server and client connection worked | `make smoke` output (`scripts/smoke_client.py` lists the tools and calls `get_employee_info`), taken in EQP-5 before the full build |
| 3 | Your MCP server code (all four tools) | `server/mcp_tools.py` (the four tools) and `server/services/*.py` (the function behind each tool) |
| 4 | Your unit tests for each tool's underlying function, and terminal output showing them passing | `tests/test_employee_service.py`, `tests/test_policy_service.py`, `tests/test_eligibility_service.py`, `tests/test_review_service.py`, and the output of `docker compose run --rm test` (ruff, mypy, pytest with coverage) |
| 5 | Your agent code, including the MCP client connection | `agent/mcp_client.py` (the MCP client), `agent/react_agent.py` (system prompt and ReAct loop), `agent/decision.py`, `agent/guardrails.py`, `agent/reflection.py` |
| 6 | Full ReAct traces (Thought/Action/Observation) for all 4 test requests | `artifacts/traces/A.md` (approve), `artifacts/traces/B.md` (deny), `artifacts/traces/C.md` (escalate: exception claimed), `artifacts/traces/E.md` (escalate: conflicting records), from `make demo` |
| 7 | The final decision/response for each of the 4 requests, including escalation reasons | `artifacts/traces/summary.md` (every request's decision, ticket and response against the expected result), and the Outcome sections of `artifacts/traces/C.md` and `artifacts/traces/E.md` |
| 8 | Evidence of the reflection step catching or confirming at least one draft | Catch, labelled SEEDED: `artifacts/traces/B-seeded.md` (`make demo ARGS='--case B --seed-flawed-draft'`) with `SEEDED_DRAFT` in `agent/reflection.py`. Confirm, natural run: the "Draft 1 → Reflection: pass" step in `artifacts/traces/A.md` |
| 9 | A passing pipeline run | The GitHub Actions run of `.github/workflows/ci.yml` on `main` with `test` and `live-eval` green, and the `live-eval` job summary (per-case pass^3, from `evals/cases.yaml`) |
| Bonus | Swagger UI | `http://localhost:8000/docs` after `make up`, showing the four REST operations; the committed contract is `openapi.json` |

## Before assembling the PDF

- Retake a screenshot when its source changed after it was taken. Within the brief's order, `git log -1 -- <file>` shows the last change.
- **#1:** the rule tables must show the real test files, not "Planned: EQP-n", and should include every rule section: Decision guardrails (R-16 to R-19), Reflection rules (R-20 to R-23) and Tool contract rules (R-24 to R-26), plus the Codes section with the closed code sets.
- **#2:** keep the EQP-5 screenshot. It must predate the full build.
- **#4:** retake the full test run on the final `main` (the test count grows with every ticket).
- **#8:** caption the seeded catch as SEEDED: the harness injected that first draft; it is not a model error.
- **#9:** retake on the final `main` run, after the last pull request merges.
