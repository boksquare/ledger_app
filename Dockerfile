FROM python:3.12-slim

WORKDIR /opt/bills-app

# Claude Code CLI — used headlessly (claude -p) for statement parsing,
# authenticated with a Claude Pro/Max subscription token (no API key billing).
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL https://claude.ai/install.sh | bash \
    && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
ENV PATH="/root/.local/bin:$PATH"

# uv resolves and installs from pyproject.toml/uv.lock
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app

ENV PATH="/opt/bills-app/.venv/bin:/root/.local/bin:$PATH" \
    DATA_DIR=/data

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
