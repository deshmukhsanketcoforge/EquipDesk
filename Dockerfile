# syntax=docker/dockerfile:1

# ---- base: Python + locked runtime dependencies -----------------------------
FROM python:3.12-slim AS base

COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /uvx /bin/

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# ---- test: adds dev tools; source is bind-mounted by compose ----------------
FROM base AS test

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

COPY . .

CMD ["sh", "scripts/check.sh"]

# ---- runtime: app code only, non-root ---------------------------------------
FROM base AS runtime

COPY server ./server
COPY agent ./agent
COPY evals ./evals
COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts/__init__.py scripts/smoke_client.py ./scripts/

RUN useradd --create-home --uid 10001 app
USER app

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]

CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8000"]
