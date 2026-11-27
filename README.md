# EquipDesk

An internal IT equipment request handler, built for the W7 CoForge FDE Training lab.

Employees send natural-language requests such as "I need a second monitor". A ReAct agent calls the tools of an MCP server to look up the employee and the equipment policy, then approves, denies or escalates the request to a human. A reflection step checks each draft response before it is final.

[PLAN.md](PLAN.md) holds the full design, decisions and ticket order.

## Status

Work is tracked as tickets EQP-1 to EQP-14 (Linear project EquipDesk).

| Done | Still to come |
|---|---|
| **EQP-1:** Docker setup, CI pipeline, FastAPI backend with Swagger, and an MCP server mounted at `/mcp` with a trivial `ping` tool<br>**EQP-2:** [requirements and policy rules](docs/requirements.md) and the fixed code sets in `server/codes.py` | Database and seed data (EQP-3), the four equipment tools (EQP-4 to EQP-8), the agent (EQP-9 to EQP-12), evals and demo (EQP-13) |

`make seed` and `make demo` exist already, but they exit with a "not implemented yet" message until their tickets land.

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
make up      # start Postgres and the API, wait until both are healthy
```

| URL | What it is |
|---|---|
| http://localhost:8000/docs | Swagger UI (OpenAPI docs) |
| http://localhost:8000/health | Health check, returns `{"status": "ok"}` |
| http://localhost:8000/mcp | MCP endpoint (Streamable HTTP), for MCP clients only |

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

Stop everything with `make down`.

## Running the tests

```sh
make test                    # or: docker compose run --rm test
```

This runs [scripts/check.sh](scripts/check.sh) in the `test` container: ruff lint, ruff format check, mypy, then pytest with a coverage gate (at least 90% on `server/`). CI runs exactly the same command.

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
| `make seed` | Seed the database (from EQP-3) |
| `make demo` | Run the agent against the real model (from EQP-9; needs `FOUNDRY_*` env vars) |

## Project layout

```
server/            FastAPI backend
  main.py          app factory: /health, Swagger, MCP mounted at /mcp
  mcp_tools.py     MCP tool definitions (currently: ping)
  config.py        settings from environment variables
  codes.py         fixed code sets: verdicts, reason, escalation and error codes
docs/
  requirements.md  requirements, policy rules (R-01 to R-26), codes, worked examples
agent/             ReAct agent (placeholder until EQP-9)
tests/             pytest suite (runs in Docker)
scripts/check.sh   the full lint + type + test check
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

Python 3.12, FastAPI, the official MCP Python SDK (2.x, `MCPServer`), PostgreSQL 16, uv, ruff, mypy, pytest, Docker Compose, GitHub Actions. Planned: SQLAlchemy 2 and Alembic (EQP-3), Claude Sonnet 5.5 via Azure AI Foundry (EQP-9).
