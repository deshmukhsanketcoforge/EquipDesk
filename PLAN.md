# EquipDesk — Implementation Plan (rev 4)

## 1. Context

W7 lab (CoForge FDE Training): build, from scratch, an internal **IT equipment request handler**.
Employees send natural-language requests ("I need a second monitor"). A **ReAct agent** uses an **MCP server's tools** to look up the employee and policy, then **approves, denies, or escalates** (ambiguous → human review, never guess). A **reflection** step checks each draft before finalizing. Everything ships through a **CI pipeline** running unit tests. Repo currently has only README + .gitignore.

Working agreements (from the user):
- **Linear** tracks work (solo, simple tickets, no milestones).
- **CI + Docker exist from the first ticket**; CI guards every later change.
- **TDD for every ticket.**
- **Branch protection on `main`** (no direct pushes); CI runs on PRs into `main` and on pushes to `main` (post-merge).
- LLM = **Claude Sonnet 5.5 via Azure AI Foundry**; API key stored as a **GitHub secret** (cost accepted).
- Data in a **real database (enterprise style)**, not JSON files.
- Delivery window is **2 days** → keep the stack disciplined (§11).

Graded (100): requirements doc 8 · one-tool server/client proof 7 · four MCP tools 12 · unit tests 13 · agent + MCP client + ReAct 15 · approve/deny 10 · escalation w/ clear reasons 15 · reflection evidence 10 · passing pipeline 10. Deliverable: one PDF of 9 ordered screenshots (§10).

## 2. Architecture

```
            ┌─────────────────────── docker compose ───────────────────────┐
 request →  │  agent (ReAct + reflection)                                   │
 (text+id)  │      │  MCP client (Streamable HTTP)                          │
            │      ▼                                                        │
            │  api  (FastAPI)  ── /mcp  → MCP server (4 tools) ─┐           │
            │       ├─ /docs  Swagger UI (OpenAPI)              ├─► service │
            │       ├─ /v1/... REST endpoints (same 4 operations)┘  layer   │
            │       └─ /health                                      │       │
            │                                                       ▼       │
            │  db  (PostgreSQL)  ◄── SQLAlchemy 2 + Alembic migrations      │
            └───────────────────────────────────────────────────────────────┘
                 Foundry (Claude Sonnet 5.5) ◄── agent only (outbound HTTPS)
```

- **One backend process (FastAPI)** owns the DB and a single **service layer** (plain Python functions — the TDD seam). Two thin adapters call it: **REST** (`/v1/...`, Swagger at `/docs`) and **MCP** (mounted at `/mcp`). So tools are exposed through the backend, and Swagger documents the same operations the MCP tools perform (MCP tools themselves aren't described by OpenAPI; the MCP tool schemas are self-describing via `list_tools`).
- Agent talks **only MCP** (that's the lab's point); REST/Swagger exists for humans, debugging, and enterprise-style API docs.

## 3. Decision review

| # | Decision | Alternatives rejected | Why / risk handled |
|---|---|---|---|
| D1 | Python 3.12, **FastAPI** backend, official `mcp` SDK `FastMCP` mounted via `streamable_http_app()` at `/mcp` | stdio-only server; separate MCP process calling REST | Needed for Swagger + shared logic with minimal moving parts. Risk: mounting the MCP app inside FastAPI requires wiring its session-manager lifespan into FastAPI's lifespan (known gotcha) → spike done in the first server ticket (EQP-5). Fallback if blocked: run MCP app as its own uvicorn port in the same image, same service layer. |
| D2 | Logic in **service layer** + repositories; adapters (REST, MCP) are thin | Logic in route/tool functions | Lab: plain-function-first. TDD seam. One behavior, two interfaces, no drift. |
| D3 | Policy tools **deterministic**; LLM only does language (parse item, spot exception claims) | LLM judges policy | Testable/auditable. Spec's `check_request_eligibility(employee_id, item)` has no `reason` arg, so exception claims can't reach it → the **agent** spots them in its Thought step and escalates; tool stays pure. |
| D4 | **Closed, versioned enums** for verdicts and reason codes (see §4) — `StrEnum` in one module `server/codes.py`; Pydantic response models use the enum type; OpenAPI/MCP schemas therefore list the allowed values; a test asserts the exact set; adding a code requires updating enum + requirements doc + a test | Free-text reasons | **Answer to Q1: yes, fixed.** Agents/tests/dashboards can switch on codes. Human-readable `explanation` accompanies each code. |
| D5 | **Deny** = clear violation, no claimed exception. **Escalate** = ambiguity, missing/conflicting data, violation + exception claim, high cost | Escalate every violation / deny everything | Rubric needs a clean denial and ≥2 distinct escalation reasons. |
| D6 | **Clock injection, not a global fixed date.** Service functions take `today` from a `Clock` dependency: real `date.today()` in the running app; a `FixedClock` in tests. Demo seed data dates are generated **relative to today** (e.g. "monitor bought 4.7 yrs ago") | Global `AS_OF` constant/env (rev 2) | **Answer to Q8:** eligibility depends on "now" (cooldown math). Hard-coding dates in tests/seed makes boundary tests silently flip as calendar time passes and makes results depend on when you run them. Injected clock = tests deterministic, production honest, demo works any day. (Rev 2's `AS_OF` is dropped.) |
| D7 | LLM via **Azure AI Foundry**, model Claude Sonnet 5.5, through the Anthropic SDK's Foundry client, behind our own `LLMClient` interface (`complete(messages, tools) -> response`). Config via env (`FOUNDRY_ENDPOINT`/resource, `FOUNDRY_API_KEY`, `FOUNDRY_MODEL_DEPLOYMENT`), loaded with pydantic-settings; temperature 0 | Direct Anthropic API; frameworks (LangChain) | **Answer to Q2.** Interface lets tests inject a scripted fake LLM. Native tool use + enforced `Thought:` text before each tool call gives the explicit ReAct trace. *Assumptions to verify in EQP-9 first step:* API-key auth (not Entra ID), exact Foundry client class/env names, deployment name — I'll confirm against current docs before coding, since you haven't given the endpoint details. |
| D8 | Final output = JSON `{decision, response, reasons[], ticket_id?}`, Pydantic-validated, retry once | Extra local `submit_decision` tool | Tool surface = exactly the server's tools. |
| D9 | **Guardrails in code**: `approve` only if last eligibility obs = `ELIGIBLE`; `deny` only if `INELIGIBLE`; `escalate` only after successful `flag_for_human_review` | Prompt-only | "Escalate, don't guess" holds even if the model misbehaves; mitigates prompt injection in request text. |
| D10 | Reflection = **deterministic checker + LLM critic**, ≤1 revision (detail/example in §8) | LLM-only | Deterministic part guarantees certain failures are always caught. |
| D11 | **PostgreSQL 16 + SQLAlchemy 2.0 + Alembic**, seeded by an idempotent `scripts/seed.py` (also run in demo/CI). Tables: `employees`, `equipment_assets` (history), `catalog_items` (+aliases, unit cost), `role_policies` (role, item, entitled, max_units, refresh_months), `review_tickets` (id, employee, request, reason_code, reason, status, created_at). Tests: service/rules unit tests on an in-memory fake repository (fast, no DB); repository + API integration tests on real Postgres (compose `db` service locally, GH Actions service container in CI), each test in a rolled-back transaction | JSON files (rev 2); SQLite | **Answer to Q7.** Policies in DB = editable without redeploy; tickets get real persistence/audit. Postgres matches "enterprise style" and our compose/CI already run containers. |
| D12 | **CI** (GitHub Actions): triggers `pull_request` → `main` and `push` → `main`. Job 1 `test` (no secrets): build image, ruff + mypy + pytest(+cov) against Postgres service. Job 2 `live-eval` (uses `FOUNDRY_*` GitHub secrets): brings up compose, runs the 6 demo requests against the real model, asserts **decision category + escalation reason code** per case (not wording), uploads traces as artifacts. Fork PRs can't see secrets → skipped (solo repo; irrelevant). `workflow_dispatch` enabled for re-runs | Live LLM in CI for *all* tests; no live eval | **Answer to Q3.** Deterministic suite stays the gate (fast, non-flaky); live-eval is the real end-to-end regression with the real model. Temperature 0 + category-only assertions limit flakiness; make `live-eval` a required check only after it's been stable for a few runs (otherwise a model hiccup blocks merges). Branch protection requires `test` (+ `live-eval` later). |
| D13 | **Docker from ticket 1**: multi-stage `Dockerfile` (`base` → `test` → `runtime`), `docker-compose.yml` (`db`, `api`, `agent`, `test`), healthchecks, `.dockerignore`. Secrets only via env/GitHub secret, never baked in | Venv-only | Same environment locally and in CI. |
| D14 | One ticket = one branch = one PR = one green CI run; branch name from Linear (`eqp-N-…`) | Direct commits | Regression guarantee; matches branch protection. |
| D15 | **TDD loop** (§9) for every ticket; red commit stays local, PR has tests+impl written test-first | Test-after | CI stays green on pushed branches. |
| D16 | Employee ID is a separate agent input (`--employee-id`) next to free-text request | Parse from prose | Simpler; unknown ID is still an escalation case. |

## 4. Requirements & policy (feeds docs/requirements.md)

**Request data:** `employee_id`, `role` (looked up), `tenure_months`, `item_requested` (normalized), `quantity` (derived from equipment on file), `reason` (free text), `submitted_at`.

**Catalog:** laptop, monitor, keyboard, mouse, headset, dock, phone; aliases in DB (screen→monitor, notebook→laptop…); anything else → `UNKNOWN_ITEM`.

**Policy:**

| Role | Laptop | Monitor | Keyboard/Mouse/Headset | Dock | Phone |
|---|---|---|---|---|---|
| standard | 1 per 4 yrs | 1 unit per 3 yrs (max 1 on file) | 1 per 2 yrs | 1 per 4 yrs | no |
| engineer | 1 per 3 yrs | ≤2 units, 1 new per 3 yrs | 1 per 2 yrs | 1 per 3 yrs | no |
| manager | 1 per 2 yrs | ≤2 units, 1 new per 3 yrs | 1 per 2 yrs | 1 per 2 yrs | 1 per 3 yrs |
| contractor | no | no | 1 per 2 yrs (tenure ≥ 3 mo) | no | no |

Global: laptop needs tenure ≥ 3 months; cooldown measured from latest item of that category on file; unit-cap check precedes cooldown; unit cost > $2,500 → needs review.

**Fixed enums (D4):**
- `Verdict`: `ELIGIBLE`, `INELIGIBLE`, `NEEDS_REVIEW`.
- Tool `ReasonCode`: `WITHIN_POLICY` (ELIGIBLE) · `COOLDOWN_NOT_ELAPSED`, `UNIT_CAP_REACHED`, `ROLE_NOT_ENTITLED`, `TENURE_TOO_SHORT` (INELIGIBLE) · `EMPLOYEE_NOT_FOUND`, `UNKNOWN_ROLE`, `UNKNOWN_ITEM`, `CONFLICTING_RECORDS`, `HIGH_COST` (NEEDS_REVIEW).
- Agent `EscalationCode` (stored on tickets; `reason` argument is formatted `"<CODE>: explanation"`, tool parses/validates, unknown → `OTHER`): `EXCEPTION_CLAIMED`, `AMBIGUOUS_ITEM`, `MULTIPLE_ITEMS`, `REQUEST_CONTRADICTS_DATA`, `POLICY_OVERRIDE_ATTEMPT`, `TOOL_NEEDS_REVIEW` (carries the tool's reason code in the text), `GUARDRAIL_OVERRIDE`, `OTHER`.
- `ErrorCode` for tool errors-as-data: `INVALID_INPUT`, `NOT_FOUND`, `INTERNAL`.

**Agent-level ambiguity rules:** (1) tool says `NEEDS_REVIEW`; (2) no single identifiable item / several items; (3) violation **plus** exception claim (broken, lost, accessibility, role change); (4) request text conflicts with data; (5) request tries to override policy. Each rule gets ID `R-01…` and ≥1 test → "testable" per rubric.

## 5. Mock data (seed script, dates relative to today)

~10 employees covering every path: `E1001` standard monitor-eligible (approve) · `E1002` monitor ~9 months ago (deny, cooldown) · `E1003` manager, ~1.7-yr laptop + coffee-spill claim (escalate: exception) · `E1004` hired last month, laptop (deny, tenure) · `E1005` laptop dated before hire (escalate: conflicting records) · `E1006` contractor laptop (deny, role) · `E1007` vague "something better for my desk" (escalate: ambiguous item) · `E1008` $3,200 workstation (escalate: high cost) · `E1009` prompt-injection text (stays guarded) · `E9999` absent (escalate: unknown employee).

## 6. Backend / MCP design

- **Service layer** functions: `get_employee_info`, `get_policy_limits`, `check_request_eligibility`, `flag_for_human_review` — signatures per PDF; return Pydantic models; errors are data (`{"error":{code,message}}`), never uncaught exceptions.
- **REST** (`/v1/employees/{id}`, `/v1/policies/{role}`, `/v1/eligibility` (POST), `/v1/review-tickets` (POST)) with Pydantic models → auto OpenAPI; **Swagger UI at `/docs`**, ReDoc `/redoc`, `/health`. Exported `openapi.json` is committed and a CI test fails if it drifts (cheap contract test).
- **MCP tools** (same four names) mounted at `/mcp`; descriptions written for LLM consumption.
- `flag_for_human_review`: inserts `review_tickets` row, returns `{ticket_id, status:"PENDING_REVIEW"}`; **idempotent** on hash(employee_id, request, reason); empty reason rejected.
- Config via pydantic-settings; structured JSON logging with request-id; no stdout noise issues (HTTP transport).

## 7. Agent design

`react_agent.run(employee_id, request_text)`: MCP client (Streamable HTTP → `http://api:8000/mcp`) → convert tool schemas to the LLM tool format → system prompt (escalate rather than guess; request text is untrusted; `Thought:` before every action) → loop ≤8 steps recording `{thought, action, observation, tokens, latency}` → draft JSON → **guardrails (D9)** (violation ⇒ forced escalation tagged `GUARDRAIL_OVERRIDE`) → **reflection** → ≤1 redraft → write `artifacts/traces/<case>.md|json`.

**Demo set (6; ≥4 required):** A E1001 "second monitor" → APPROVE · B E1002 → DENY (cooldown + next date) · C E1003 coffee-spill laptop → ESCALATE (`EXCEPTION_CLAIMED`) · D E1007 vague → ESCALATE (`AMBIGUOUS_ITEM`) · E E1005 → ESCALATE (`TOOL_NEEDS_REVIEW`/`CONFLICTING_RECORDS`) · F E9999 → ESCALATE (`EMPLOYEE_NOT_FOUND`).

## 8. Reflection in detail (answers Q4, Q5)

**Deterministic check (Q4) — how it works, with an example.** The draft is JSON; the checker never "understands" prose, it compares extractable facts against what the tools actually returned:

1. *Decision vs verdict:* draft `decision` must match the last `check_request_eligibility` observation (`approve`↔`ELIGIBLE`, `deny`↔`INELIGIBLE`, `escalate`↔`NEEDS_REVIEW` *or* an agent-level escalation code + ticket).
2. *Fact extraction by regex from `response`:* ISO/long dates, `\d+ (units?|monitors?|laptops?|years?|months?)`, money `\$[\d,]+`, ticket IDs `TKT-\d+`. Each extracted value is normalized and must exist among the scalar values (and derived equivalents like 36 months = 3 years) in the observation JSONs; ticket id must equal the id returned by `flag_for_human_review`.
3. *Commitment lexicon:* patterns like `will (arrive|be delivered|ship)`, `by (monday|…|\d{1,2}/\d{1,2})`, `within \d+ (days|hours)`, `guarantee`, `definitely` are flagged unless backed by an observation (none of our tools return delivery info, so always flagged).

Example — case B (E1002, cooldown):
- Observation: `{verdict: INELIGIBLE, reason_codes:[COOLDOWN_NOT_ELAPSED], next_eligible_date: 2029-02-01}`.
- Draft: *"Approved! Your new monitor will arrive by Friday. You're allowed 2 monitors, next refresh 2029-02-14."*
- Findings: (1) decision `approve` ≠ `INELIGIBLE` → **fail**; (2) "2 monitors" not in observations → **unsupported**; "2029-02-14" ≠ `2029-02-01` → **unsupported date**; (3) "will arrive by Friday" → **unbacked commitment**. Result `{verdict: revise, issues:[…]}` with machine-readable issue codes.
- Limit (honest): paraphrases the regexes can't extract slip through — that's what the **LLM critic** (second call, given draft + observations + rules) covers. Final verdict = revise if either check fails.

**Evidence strategy (Q5) — why a seeded draft, in detail.** The rubric wants evidence the reflection step "catches or confirms at least one draft." With a good prompt at temperature 0, the model's first drafts will be correct nearly every time, so natural runs would only ever show *confirm* — and we'd have no demonstration that reflection can actually *catch* a problem (the more convincing half). We can't honestly rely on the model making a mistake on demand. So:
- **Natural runs** (all 6 cases): reflection runs for real; trace shows "Draft 1 → Reflection: pass (checks listed)". That's the *confirm* evidence, unedited.
- **Seeded run** (`python -m agent.run_demo --case B --seed-flawed-draft`): the harness *replaces the model's first draft with a known-bad one* (the over-committing text above) and then runs reflection **for real** (both deterministic + LLM critic), then the model genuinely redrafts using the issues. The trace shows `Draft 1 [SEEDED, flawed]` → `Reflection: revise + issues` → `Draft 2` → guardrails pass. Labeled "SEEDED" in the trace and in the PDF caption so nothing is passed off as a natural model error.
- Also covered offline by unit tests (scripted fake LLM) so catching logic is regression-tested in CI. If a *natural* catch ever occurs in a live run, we keep that trace as a bonus.

## 9. TDD & CI/Docker workflow (every ticket)

1. Ticket → In Progress; branch `eqp-N-…`.
2. Write failing tests from the ticket's acceptance list; `docker compose run --rm test` → confirm red for the right reason.
3. Minimal implementation → green; refactor; `ruff` + `mypy` clean.
4. Run the **full** suite in Docker (regression).
5. Push; PR titled `EQP-N …`; Actions `test` (and `live-eval` once it exists) must be green; merge to `main`; post-merge run on `main` verifies again.

## 10. Linear tickets (flat, no milestones)

**Setup:** one Linear team (key assumed `EQP`), no projects/milestones/cycles. Workflow Todo → In Progress → In Review → Done. Labels: `infra`, `server`, `agent`, `docs`. Linear↔GitHub integration: PR open → In Review, merge → Done. Dependencies via "blocked by". Each ticket has acceptance criteria written as tests + the rubric/deliverable it feeds.

| ID | Title | Label | Blocked by | Acceptance (test-first) | Feeds |
|---|---|---|---|---|---|
| EQP-1 | Scaffold repo, Docker, compose, CI | infra | – | Smoke test green via `docker compose run test`; Actions on PR→main & push→main builds image, runs ruff+mypy+pytest with Postgres service; PR template; Makefile (`make test/up/seed`) | Pipeline #9 base |
| EQP-2 | Requirements doc, policy rules, enums | docs | 1 | `server/codes.py` enums + test asserting exact sets; doc lists every rule `R-xx` ↔ test mapping | Req. doc #1 |
| EQP-3 | DB schema, migrations, seed, repositories | server | 1, 2 | Alembic upgrade on empty DB works; seed idempotent (run twice = same rows); repo tests on real Postgres; seed dates relative to today; in-memory fake repo shares contract tests | Data layer |
| EQP-4 | Service: `get_employee_info` | server | 3 | Found / not found / malformed id / returns role, tenure, equipment on file | Tool 1 |
| EQP-5 | FastAPI app + Swagger + MCP mount with one tool + smoke client | server | 4 | `/health`, `/docs` 200; MCP `list_tools` shows `get_employee_info`; smoke client over HTTP returns E1001 (**capture output**); lifespan wiring verified | One-tool proof #2 |
| EQP-6 | `get_policy_limits` (service + REST + MCP) | server | 5 | Each role; unknown role → `UNKNOWN_ROLE`; normalization; REST+MCP return same payload | Tool 2 |
| EQP-7 | `check_request_eligibility` (service + REST + MCP) | server | 6 | One test per reason code; exact cooldown boundary (with `FixedClock`); unit cap; tenure; alias; unknown item/employee; conflicting records; high cost | Tool 3, tests #4 |
| EQP-8 | `flag_for_human_review` (service + REST + MCP) | server | 5 | Ticket row written; id returned; idempotent; empty reason rejected; reason code parsed/validated | Tool 4, tests #4 |
| EQP-9 | LLM client (Foundry) + MCP client wrapper | agent | 5 | Verify Foundry client/config against docs; `LLMClient` interface + fake; config tests; MCP wrapper lists/calls tools against test app; one hardcoded request → one tool call | Agent #5 |
| EQP-10 | ReAct loop + trace | agent | 8, 9 | Scripted fake-LLM tests: multi-step, step cap, malformed tool call, trace structure, markdown renderer | ReAct #5, #6 |
| EQP-11 | Decision schema + guardrails | agent | 10 | Approve w/o `ELIGIBLE` blocked; deny w/o `INELIGIBLE` blocked; escalate w/o ticket blocked; injection can't force approve; malformed JSON retry | Correctness #7 |
| EQP-12 | Reflection (deterministic + LLM critic) + seeded mode | agent | 11 | Example from §8 caught with exact issue codes; good draft confirmed; ≤1 revision; both drafts in trace; `--seed-flawed-draft` | Reflection #8 |
| EQP-13 | Demo run (6 cases), traces, live-eval CI job | agent/infra | 12 | `run_demo` outputs match case table; trace assertions (approve/deny preceded by eligibility obs, escalate by flag obs); `live-eval` job with Foundry secrets green on PR and main | Traces #6, #7, #9 |
| EQP-14 | Evidence pack: README, OpenAPI export + drift test, screenshot checklist | docs | 13 | README works from clean clone with `docker compose up`; checklist maps all 9 deliverables | PDF #1–9 |

**Why this shape:** one PR-sized, independently testable slice per ticket (a tool, a loop, a guardrail set) so each TDD red→green fits one sitting and a red CI points at one change; order mirrors the lab's own "one tool → prove plumbing → rest → agent → reflection last"; each ticket maps to a rubric line so progress = points; infra/data first because everything else depends on the safety net and fixtures; no milestones because 14 dependency-ordered tickets already tell the story for a solo 2-day effort; criteria-as-tests makes "done" unambiguous.

**2-day sequencing (not milestones, just order):** Day 1 = EQP-1…8 (infra → data → all four tools). Day 2 = EQP-9…14 (agent, guardrails, reflection, demo, live-eval, evidence). EQP-1–3 should be fast; if Day 1 slips, EQP-14's OpenAPI drift test and mypy strictness are the first things cut.

## 11. Industry-grade extras (kept deliberately small — Q10)

**Include (each ≈ minutes, not hours):**
- `uv` (or pip-tools) lockfile for reproducible installs; pinned versions.
- Pydantic v2 models everywhere + `pydantic-settings` config (12-factor env).
- Alembic migrations; idempotent seed.
- `ruff` (lint+format) + `mypy` (basic mode on `server/`, `agent/`); `pre-commit` running ruff.
- `/health` + compose healthchecks; structured JSON logs with request-id.
- Coverage gate (≥90% on `server/`) in CI; test artifacts + traces uploaded.
- Makefile (`make test`, `make up`, `make demo`), PR template, Dependabot for pip/Docker/Actions.
- Token/latency capture per LLM step in traces (cheap cost visibility).
- Short ADR note = §3 decision table copied into `docs/decisions.md`.

**Explicitly defer (mention in README as "next steps"):** authN/Z on the API, OpenTelemetry/metrics stack, rate limiting, K8s/Helm, human-review UI, retries/circuit breakers beyond one LLM retry, multi-tenant policies.

## 12. Deliverable mapping (single PDF, ordered)

| # | Screenshot | Source |
|---|---|---|
| 1 | Requirements doc + policy rules | `docs/requirements.md`, seeded `role_policies` (EQP-2/3) |
| 2 | One-tool server/client output | smoke client run (EQP-5) |
| 3 | MCP server code (4 tools) | `server/mcp_tools.py`, `server/services/` |
| 4 | Unit tests + passing output | `tests/`, `docker compose run test` |
| 5 | Agent code incl. MCP client | `agent/react_agent.py`, `agent/mcp_client.py` |
| 6 | Full ReAct traces ×4+ | `artifacts/traces/*.md` (EQP-13) |
| 7 | Final decision + escalation reasons | trace tails / summary table |
| 8 | Reflection catching/confirming | Draft1/Reflection/Draft2 sections (EQP-12/13) |
| 9 | Passing pipeline | GitHub Actions run (`test` + `live-eval`) |
Bonus screenshot: Swagger UI at `/docs`.

## 13. Open items — resolved

1. **Foundry details:** the user will supply endpoint, deployment name and key when EQP-9 needs them. Until then the code ships the config scaffolding only (`pydantic-settings` fields, `.env.example` with placeholders, GitHub secret names `FOUNDRY_API_KEY`, `FOUNDRY_ENDPOINT`, `FOUNDRY_MODEL_DEPLOYMENT`) and a clear startup error if they are missing. Everything before EQP-9 and all CI `test` jobs run without them.
2. **Branch protection on `main`:** already configured by the user.
3. **Linear:** MCP server added and verified by the user in a new session. Tickets EQP-1…14 are created there from §10 once the tools are visible to Claude. Team key is read from Linear (the `EQP` prefix in this plan is a placeholder).
4. **Git:** Claude never commits or pushes. The user reviews each ticket's work and does commit/push/PR. Claude stops after local green (tests + lint in Docker) and hands over a summary of the changes.
5. **Agreed:** the rest of the plan as written.
