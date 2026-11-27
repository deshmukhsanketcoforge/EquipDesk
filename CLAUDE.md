# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status

EQP-1 to EQP-11 are done: Docker, CI, the database, all four MCP tools (service, REST and MCP), the agent's Foundry LLM client, MCP client and one-call proof (`make one-call`), the ReAct loop with traces (`make ask`), and the decision JSON with its guardrails. Reflection and evals (EQP-12, EQP-13) and the evidence pack (EQP-14) are still to come; the README's Status table is the current list. **`PLAN.md` is the source of truth** for scope, decisions, ticket order and the deliverable mapping. Read it before starting any ticket. Commands and modules for later tickets (e.g. `run_demo`, `evals/cases.yaml`) do not exist yet, so verify before relying on them.

## What this is

W7 CoForge lab: an IT equipment-request handler. A ReAct agent (Claude Opus 5.5, fast-mode deployment, via Azure AI Foundry) calls the tools of an MCP server to approve, deny, or escalate natural-language requests. A reflection step checks each draft before it is finalized. The 4 required MCP tools (signatures fixed by the brief): `get_employee_info(employee_id)`, `get_policy_limits(role)`, `check_request_eligibility(employee_id, item)`, `flag_for_human_review(employee_id, request, reason)`.

## Commands (Docker is the canonical environment)

- Tests + lint: `docker compose run --rm test` (ruff, mypy, pytest with coverage). `make test` is a wrapper.
- Single test: `docker compose run --rm test pytest tests/path/test_x.py::test_name -q`
- Run stack: `docker compose up` (services `db`, `api`, `agent`). Swagger at `/docs`, MCP at `/mcp`.
- Seed DB: `make seed` (idempotent). One-call proof on the real model: `make one-call`. Live demo (from EQP-13): `make demo`. Both run the `agent` compose service and need the `FOUNDRY_*` env vars.

## Architecture (big picture)

- **One FastAPI backend, one service layer, two adapters.** Business logic lives in plain service functions and repositories. The REST routes (`/v1/...`, Swagger) and the MCP tools (mounted at `/mcp` via `MCPServer.streamable_http_app()`; mcp SDK 2.x, where v1's `FastMCP` was renamed) are thin wrappers over the same functions, so they must never diverge. Wiring the MCP app's session-manager lifespan into FastAPI's lifespan is a known gotcha.
- **The agent talks only MCP** (Streamable HTTP client), never REST or the DB directly.
- **Policy decisions are deterministic and live in the tools.** The LLM handles only language: parsing the item and spotting exception claims such as "coffee spill". The eligibility tool has no `reason` argument, so the agent itself escalates exception claims.
- **Closed enums** in `server/codes.py` (`Verdict`, tool `ReasonCode`, agent `EscalationCode`, `ErrorCode`). Adding a code requires updating the enum, `docs/requirements.md` and a test. Tool errors are returned as data, never raised, and domain errors also carry the domain `ReasonCode` (e.g. `EMPLOYEE_NOT_FOUND`).
- **Guardrails in code, not just prompts:** approve requires a last `ELIGIBLE` observation, deny requires `INELIGIBLE`, and escalate requires a successful `flag_for_human_review`. The eligibility observation must be for the requesting employee and the item the decision names; an escalation's ticket must be for the requesting employee and this request. Violations become a forced escalation tagged `GUARDRAIL_OVERRIDE`.
- **Reflection** = deterministic claim-vs-observation checker plus an LLM critic, with at most one revision. `--seed-flawed-draft` injects a known-bad first draft so the "catch" is demonstrable; label it SEEDED wherever it is shown.
- **Time:** services take `today` from an injected `Clock` (real in the app, `FixedClock` in tests). Seed data dates are relative to today. Do not add a global fixed date.
- **Data:** PostgreSQL, SQLAlchemy 2 and Alembic. Policies, catalog and aliases live in the DB, not in JSON files.
- The LLM sits behind an `LLMClient` interface so tests inject a scripted fake.
- **Eval cases** live in one file, `evals/cases.yaml` (cases A–G with expected decision, allowed escalation codes, observed reason code and criticality). `run_demo`, the `live-eval` job and the eval-scoring unit tests all read it. Change expectations there, not in code.
- `flag_for_human_review` is idempotent on employee + normalized request text. The LLM-written `reason` is not part of the key.

## Workflow rules specific to this project

- **TDD for every ticket:** write the failing tests first and confirm they fail for the right reason, then implement, then refactor. Run the full suite in Docker before reporting done.
- CI is tiered. The deterministic `test` job (fake LLM, Postgres service) is the required merge gate and needs no credentials by default. The separate `live-eval` job uses the `FOUNDRY_*` GitHub secrets (cost accepted by the user) to run the demo cases against the real model. It asserts decision category and reason code, not wording, and each case runs k=3 times. Critical behaviours have zero tolerance: A must approve and B must deny in every run, an escalate case is never approved or denied, case G is never approved, and no run may breach the guardrails. Other cases need at least 2 of 3 full matches, and the suite needs at least 90%. Details are in `PLAN.md` D12. It becomes a required check only after a few stable runs.
- **Never run `git commit` or `git push`, and never open PRs.** The user reviews and does all of that. Stop after local green and summarize the changes. `main` is branch-protected.
- Work is tracked as 14 flat Linear tickets (no milestones), one branch per ticket. Foundry endpoint, deployment name and key live in the user's git-ignored `.env` (template: `.env.example`) and, for CI, in GitHub secrets; never read `.env`. The key and the endpoint must never be printed or stored in review tickets, traces or logs. The deployment name (`claude-opus-5-5-fast`) is not a credential and may appear in traces and CLI output as evidence of which model ran.
