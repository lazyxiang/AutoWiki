FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Enable bytecode compilation and use the uv cache mount
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

# Install dependencies first (leverage Docker layer caching)
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system -r pyproject.toml

# Copy source code after dependencies are installed
COPY shared/ ./shared/
COPY api/ ./api/
COPY cli/ ./cli/
COPY worker/ ./worker/

# Final install of the local package (fast since deps are already there)
RUN uv pip install --system --no-deps .

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "3001"]
