# EquipDesk

An internal IT equipment request handler, built for the W7 CoForge FDE Training lab.

Employees send natural-language requests such as "I need a second monitor". A ReAct agent calls the tools of an MCP server to look up the employee and the equipment policy, then approves, denies or escalates the request to a human. A reflection step checks each draft response before it is final.

[PLAN.md](PLAN.md) holds the full design, decisions and ticket order.

## Status

Work is tracked as tickets EQP-1 to EQP-14 (Linear project EquipDesk).

| Done | Still to come |
|---|---|
| **EQP-1:** Docker setup, CI pipeline, FastAPI backend with Swagger, and an MCP server mounted at `/mcp` with a trivial `ping` tool<br>**EQP-2:** [requirements and policy rules](docs/requirements.md) and the fixed code sets in `server/codes.py`<br>**EQP-3:** PostgreSQL schema with Alembic migrations, idempotent demo seed, and repositories (Postgres + in-memory)<br>**EQP-4, EQP-5:** `get_employee_info` as a service, exposed over MCP and REST, with a smoke client<br>**EQP-6:** `get_policy_limits` (service, REST, MCP)<br>**EQP-7:** `check_request_eligibility` (service, REST, MCP): rules R-01 to R-09<br>**EQP-8:** `flag_for_human_review` (service, REST, MCP): idempotent review tickets<br>**EQP-9:** LLM client for Claude Opus 5.5 (fast) on Azure AI Foundry, the agent's MCP client, and a one-call proof (`make one-call`)<br>**EQP-10:** the ReAct loop (Thought → Action → Observation, at most 8 tool steps) with JSON and Markdown traces (`make ask`)<br>**EQP-11:** the decision JSON and the guardrails: an approve, deny or escalate the tool results don't support becomes a forced escalation<br>**EQP-12:** reflection on every draft (a deterministic claim-vs-observation checker plus an LLM critic, at most one revision) and `--seed-flawed-draft` to show a catch, labelled SEEDED<br>**EQP-13:** the demo and eval cases A–G in [evals/cases.yaml](evals/cases.yaml), the demo run (`make demo`) and the `live-eval` CI job, which runs every case 3 times against the real model and scores the runs<br>**EQP-14:** the evidence pack: the [decision record](docs/decisions.md), the committed API contract [openapi.json](openapi.json) with a drift test, and the [submission checklist](docs/submission-checklist.md) | Nothing: see [Next steps](#next-steps) for deliberately deferred work |

## Prerequisites

- Docker with Compose v2. Everything runs in containers.
- `make` (optional; each target is a short `docker compose` command).
- [uv](https://docs.astral.sh/uv/) (optional; for the local environment below).

## Local environment (optional)

You don't need a virtual environment to build, run or test the project. Everything runs in Docker, and each image has its own locked environment. A local one is still useful for editor support (autocomplete, type checking), the git pre-commit hooks, and running scripts such as the `ping` example below from your machine.

```sh
uv sync    # creates .venv with Python 3.12 and all locked dependencies, including dev tools
```

Then point your editor at `.venv/bin/python` (in VS Code: Command Palette → "Python: Select Interpreter"). Run tests through Docker anyway, because that is what CI runs.

## Quick start

From a fresh clone, with only Docker:

```sh
make up      # start Postgres and the API (applies migrations), wait until both are healthy
make seed    # load the catalog, policies and demo employees (dates relative to today)
```

Without `make`, the same two steps are:

```sh
docker compose up --build --detach --wait
docker compose exec api python -m server.db.seed
```

`docker compose up` without `--detach` works too, and keeps the logs in the terminal (seed from a second terminal). If port 8000 is taken, export another port for the whole shell session, so every later `docker compose` and `make` command uses it too: `export API_PORT=8001` (or add `API_PORT=8001` to `.env`). Setting it for one command only would let the next one recreate the API on 8000. No `.env` is needed for the API; only the agent commands below need the `FOUNDRY_*` values.

| URL | What it is |
|---|---|
| http://localhost:8000/docs | Swagger UI (OpenAPI docs) |
| http://localhost:8000/health | Health check, returns `{"status": "ok"}` |
| http://localhost:8000/mcp | MCP endpoint (Streamable HTTP), for MCP clients only |
| http://localhost:8000/v1/employees/E1001 | REST: same result as the `get_employee_info` MCP tool |
| http://localhost:8000/v1/policies/engineer | REST: same result as the `get_policy_limits` MCP tool |
| `POST /v1/eligibility` with `{"employee_id": "E1002", "item": "monitor"}` | REST: same result as the `check_request_eligibility` MCP tool (try it in Swagger) |
| `POST /v1/review-tickets` with `{"employee_id": "E1003", "request": "...", "reason": "EXCEPTION_CLAIMED: ..."}` | REST: same result as the `flag_for_human_review` MCP tool |

Prove the MCP server and a client work together (lists the tools, then calls `get_employee_info` for E1001):

```sh
make smoke                   # another employee: make smoke EMPLOYEE=E9999
```

It runs [scripts/smoke_client.py](scripts/smoke_client.py) inside the `api` container. From your machine (needs the local environment): `uv run python -m scripts.smoke_client`.

To call the MCP `ping` tool from Python (needs the local environment; run with `uv run python <file>`):

```python
import asyncio
from mcp import Client


async def main() -> None:
    async with Client("http://localhost:8000/mcp") as client:
        result = await client.call_tool("ping", {})
        print(result.content[0].text)  # pong


asyncio.run(main())
```

Send one hardcoded request to the real model (Claude Opus 5.5 fast on Azure AI Foundry). The model picks one tool and the agent calls it over MCP:

```sh
cp .env.example .env         # then fill in the three FOUNDRY_* values (.env is git-ignored)
make one-call                # E1001: "I need a second monitor" -> Thought, Action, Observation
```

If a `FOUNDRY_*` value is missing, it exits with a message naming the variable.

Run one request through the full ReAct loop and print its trace (Thought → Action → Observation for every step, then the outcome):

```sh
make ask ARGS='--employee-id E1002 --request "I need a second monitor"'
```

Run the demo: cases A to F from [evals/cases.yaml](evals/cases.yaml), once each. It writes each trace to `artifacts/traces/<case>.md` and `.json`, and `summary.md` with each final decision and response, checked against the expected result:

```sh
make seed
make demo                                       # A to F
make demo ARGS='--case B --seed-flawed-draft'   # B with a known-bad first draft (SEEDED): reflection catches it
```

`make eval` runs the live eval: every case (A to G) 3 times, scored as in [PLAN.md](PLAN.md) D12. It writes `artifacts/eval/report.md` (per-case pass^3, failures, tokens and an estimated cost), `summary.json` and the traces. Each run is billed, about 20,000 tokens, so CI runs it rather than every local change.

Stop everything with `make down`.

## Running the tests

```sh
make test                    # or: docker compose run --rm test
```

This runs [scripts/check.sh](scripts/check.sh) in the `test` container: ruff lint, ruff format check, mypy, then pytest with a coverage gate (at least 90% on `server/`). CI runs exactly the same command.

Database tests create a fresh `equipdesk_test` database on the compose Postgres, migrate it to the latest schema, and roll back each test's changes. Repository tests run against both the Postgres and the in-memory implementation.

Run a single test:

```sh
docker compose run --rm test pytest tests/test_mcp_ping.py::test_client_calls_ping_tool -q
```

After changing dependencies in `pyproject.toml`, run `uv lock` and rebuild the image (`make test` rebuilds automatically).

## Make targets

| Target | What it does |
|---|---|
| `make test` | Build the test image and run the full check |
| `make up` | Start `db` and `api` in the background |
| `make down` | Stop all containers and remove the database volume |
| `make logs` | Follow the API logs |
| `make build` | Build all images |
| `make seed` | Load demo data, starting `db` and `api` first if needed (the API applies migrations on startup). Safe to rerun: replaces demo data, keeps review tickets |
| `make smoke` | MCP client lists the tools and calls `get_employee_info` (after `make seed`; `EMPLOYEE=E1002` picks another employee) |
| `make one-call` | One hardcoded request, one tool call, on the real model (needs `FOUNDRY_*`, see below; `ARGS='--employee-id E1003 --request "..."'` changes the request) |
| `make ask` | One request through the full ReAct loop on the real model, printing the trace (`ARGS='--employee-id E1003 --request "..."'`) |
| `make demo` | Demo cases A to F on the real model, once each, with traces and a summary in `artifacts/traces/` (after `make seed`; `ARGS='--case B --seed-flawed-draft'` runs one case) |
| `make openapi` | Re-export [openapi.json](openapi.json) after an API change; `tests/test_openapi.py` fails until you do |
| `make eval` | Live eval: every case 3 times on the real model, scored, report in `artifacts/eval/` (after `make seed`; billed) |

## Project layout

```
server/            FastAPI backend
  main.py          app factory: /health, Swagger, REST routes, MCP mounted at /mcp
  mcp_tools.py     MCP tool definitions: the 4 tools (get_employee_info,
                   get_policy_limits, check_request_eligibility,
                   flag_for_human_review) and ping
  rest.py          REST routes (/v1/...), same service functions as the MCP tools
  services/        business logic: one plain function per tool, errors returned as data
  config.py        settings from environment variables
  codes.py         fixed code sets: verdicts, reason, escalation and error codes
  records.py       plain data records used by repositories and services
  clock.py         injected clock (real in the app, fixed in tests)
  db/              SQLAlchemy models, seed data and seed writer
  repositories/    data access: Postgres (sql.py) and in-memory fake (memory.py)
migrations/        Alembic migrations
docs/
  requirements.md  requirements, policy rules (R-01 to R-26), codes, worked examples
  decisions.md     architecture decisions (copy of PLAN.md §3)
  submission-checklist.md  the 9 deliverables and where each screenshot comes from
openapi.json       the REST API contract, exported from the app (make openapi)
agent/             the agent (talks to the backend only through MCP)
  config.py        FOUNDRY_* settings, with a clear error when any is missing
  llm.py           LLMClient interface and the Foundry client (Claude Opus 5.5 fast)
  mcp_client.py    MCP client: the 4 tools in LLM format, calls as observations
  one_call.py      one hardcoded request -> one tool call (make one-call)
  react_agent.py   the ReAct loop and system prompt (make ask)
  decision.py      the decision JSON the model ends with, and the final decision
  guardrails.py    checks a decision against the tool results (R-16 to R-18)
  reflection.py    reflection on each draft: deterministic checker + LLM critic (R-20 to R-23)
  trace.py         the trace of a run, rendered as JSON and Markdown
  evals.py         the eval cases, run results read from traces, and scoring (D12)
  run_demo.py      the demo and the live eval (make demo, make eval)
evals/
  cases.yaml       demo and eval cases A-G: expected results, k, thresholds, pricing
tests/             pytest suite (runs in Docker)
scripts/
  check.sh         the full lint + type + test check
  export_openapi.py  writes openapi.json (make openapi)
  smoke_client.py  one-tool MCP client proof (make smoke)
Dockerfile         multi-stage: base -> test -> runtime
docker-compose.yml services: db, api, agent (profile "demo"), test (profile "test")
.github/           CI workflow
PLAN.md            design, decisions, tickets, deliverables
```

## CI

GitHub Actions ([.github/workflows/ci.yml](.github/workflows/ci.yml)) runs the `test` job on pull requests into `main`, on pushes to `main` and on manual dispatch. It builds the test image, runs `docker compose run --rm test`, and uploads the coverage report. It needs no secrets and is the required merge gate.

The `live-eval` job runs after `test`. It starts `db` and `api`, seeds the demo data, and runs every case in [evals/cases.yaml](evals/cases.yaml) 3 times against the real model (`python -m agent.run_demo --eval`). It fails on any critical miss: A not approved or B not denied in every run, an escalate case approved or denied, or a decision the guardrails would reject. It also fails when a case has fewer than 2 of 3 full matches, or fewer than 90% of all runs fully match. It writes the report to the job summary and uploads the traces, `summary.json` and the report. It uses the `FOUNDRY_API_KEY`, `FOUNDRY_ENDPOINT` and `FOUNDRY_MODEL_DEPLOYMENT` secrets. Docs-only pull requests (Markdown or `docs/` only) and pull requests from forks skip it. A newer push cancels a running `test` job, but waits for a running `live-eval`: a cancelled live run would have spent its tokens with no score.

## Development workflow

- One ticket per branch and pull request. `main` is protected, and changes merge only with a green CI run.
- Test-driven: write failing tests first, then the implementation.
- Install the git pre-commit hooks once, after `uv sync` (ruff and basic file checks run on every commit):

  ```sh
  uv run pre-commit install
  ```

## Documentation

- [docs/requirements.md](docs/requirements.md): requirements and the testable policy rules, each with the tests that cover it.
- [docs/decisions.md](docs/decisions.md): the architecture decisions, with the alternatives rejected and why.
- [openapi.json](openapi.json): the REST API contract. Swagger UI at http://localhost:8000/docs renders the same schema.
- [docs/submission-checklist.md](docs/submission-checklist.md): the brief's 9 deliverables and where each screenshot comes from.
- [PLAN.md](PLAN.md): the full design, tickets and deliverable mapping.

## Next steps

Deliberately left out of this lab (PLAN.md §11), and what a production version would add:

- **Dependabot** or similar automated dependency updates. The project is not maintained after delivery.
- **Authentication** and authorization on the API. The employee ID is trusted as given by the calling system.
- **OpenTelemetry** traces and metrics beyond the per-step token and latency figures in the agent's traces.
- **Structured JSON logs** with a request ID. The API uses default uvicorn logging.
- **Rate limiting** on the API and the MCP endpoint.
- **Kubernetes** or Helm deployment. Docker Compose is the only runtime here.
- **Human-review UI** for the tickets `flag_for_human_review` creates.
- **Retries** and circuit breakers beyond the one retry for an invalid model answer.
- **Multi-tenant** policies: one company's catalog and rules only.

## Tech stack

Python 3.12, FastAPI, the official MCP Python SDK (2.x, `MCPServer`), PostgreSQL 16, SQLAlchemy 2, Alembic, uv, ruff, mypy, pytest, Docker Compose, GitHub Actions. Claude Opus 5.5 (fast mode deployment) via Azure AI Foundry (Anthropic Python SDK, `AnthropicFoundry`).
