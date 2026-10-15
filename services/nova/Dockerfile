# Nova on Linux, for Windows PCs where Smart App Control blocks PyAV's unsigned DLLs, so
# nothing that imports LiveKit runs natively (CLAUDE.md). Rehearsals, voice samples, tests
# and the evaluation run here as they would on any Linux machine. Built and run by
# compose.yaml; the image never holds services/nova/.env (.dockerignore).
FROM python:3.14.8-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /uvx /bin/

WORKDIR /app
ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONUNBUFFERED=1

# Dependencies first, so changing Nova's code doesn't reinstall them.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-install-project
COPY . .
RUN uv sync --locked

CMD ["uv", "run", "python", "-m", "nova.agent", "dev"]
