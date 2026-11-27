# EquipDesk

An internal IT equipment request handler, built for the W7 CoForge FDE Training lab.

Employees send natural-language requests such as "I need a second monitor". A ReAct agent calls the tools of an MCP server to look up the employee and the equipment policy, then approves, denies or escalates the request to a human. A reflection step checks each draft response before it is final.

[PLAN.md](PLAN.md) holds the full design, decisions and ticket order.

## Status

Work is tracked as tickets EQP-1 to EQP-14 (Linear project EquipDesk).

| Done | Still to come |
|---|---|
| **EQP-1:** Docker setup, CI pipeline, FastAPI backend with Swagger, and an MCP server mounted at `/mcp` with a trivial `ping` tool<br>**EQP-2:** [requirements and policy rules](docs/requirements.md) and the fixed code sets in `server/codes.py`<br>**EQP-3:** PostgreSQL schema with Alembic migrations, idempotent demo seed, and repositories (Postgres + in-memory)<br>**EQP-4, EQP-5:** `get_employee_info` as a service, exposed over MCP and REST, with a smoke client<br>**EQP-6:** `get_policy_limits` (service, REST, MCP)<br>**EQP-7:** `check_request_eligibility` (service, REST, MCP): rules R-01 to R-09<br>**EQP-8:** `flag_for_human_review` (service, REST, MCP): idempotent review tickets<br>**EQP-9:** LLM client for Claude Opus 5.5 (fast) on Azure AI Foundry, the agent's MCP client, and a one-call proof (`make one-call`)<br>**EQP-10:** the ReAct loop (Thought → Action → Observation, at most 8 tool steps) with JSON and Markdown traces (`make ask`)<br>**EQP-11:** the decision JSON and the guardrails: an approve, deny or escalate the tool results don't support becomes a forced escalation | Reflection (EQP-12), evals and demo (EQP-13) |

`make demo` exists already, but it exits with a "not implemented yet" message until EQP-13.

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

```sh
make up      # start Postgres and the API (applies migrations), wait until both are healthy
make seed    # load the catalog, policies and demo employees (dates relative to today)
```

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
| `make demo` | Run the agent demo against the real model (from EQP-13; needs `FOUNDRY_*` env vars) |

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
agent/             the agent (talks to the backend only through MCP)
  config.py        FOUNDRY_* settings, with a clear error when any is missing
  llm.py           LLMClient interface and the Foundry client (Claude Opus 5.5 fast)
  mcp_client.py    MCP client: the 4 tools in LLM format, calls as observations
  one_call.py      one hardcoded request -> one tool call (make one-call)
  react_agent.py   the ReAct loop and system prompt (make ask)
  decision.py      the decision JSON the model ends with, and the final decision
  guardrails.py    checks a decision against the tool results (R-16 to R-18)
  trace.py         the trace of a run, rendered as JSON and Markdown
tests/             pytest suite (runs in Docker)
scripts/
  check.sh         the full lint + type + test check
  smoke_client.py  one-tool MCP client proof (make smoke)
Dockerfile         multi-stage: base -> test -> runtime
docker-compose.yml services: db, api, agent (profile "demo"), test (profile "test")
.github/           CI workflow
PLAN.md            design, decisions, tickets, deliverables
```

## CI

GitHub Actions ([.github/workflows/ci.yml](.github/workflows/ci.yml)) runs the `test` job on pull requests into `main`, on pushes to `main` and on manual dispatch. It builds the test image, runs `docker compose run --rm test`, and uploads the coverage report. It needs no secrets. A second job, `live-eval`, which runs the demo cases against the real model, is planned for EQP-13.

## Development workflow

- One ticket per branch and pull request. `main` is protected, and changes merge only with a green CI run.
- Test-driven: write failing tests first, then the implementation.
- Install the git pre-commit hooks once, after `uv sync` (ruff and basic file checks run on every commit):

  ```sh
  uv run pre-commit install
  ```

## Tech stack

Python 3.12, FastAPI, the official MCP Python SDK (2.x, `MCPServer`), PostgreSQL 16, SQLAlchemy 2, Alembic, uv, ruff, mypy, pytest, Docker Compose, GitHub Actions. Claude Opus 5.5 (fast mode deployment) via Azure AI Foundry (Anthropic Python SDK, `AnthropicFoundry`).
