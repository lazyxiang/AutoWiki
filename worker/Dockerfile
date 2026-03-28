FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system -r pyproject.toml

COPY shared/ ./shared/
COPY worker/ ./worker/
COPY api/ ./api/
COPY cli/ ./cli/

RUN uv pip install --system --no-deps .

CMD ["python", "-m", "worker.main"]
