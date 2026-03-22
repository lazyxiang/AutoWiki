FROM python:3.12-slim
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml .
COPY shared/ ./shared/
COPY worker/ ./worker/
COPY api/ ./api/
COPY cli/ ./cli/
RUN pip install --no-cache-dir .
CMD ["python", "-m", "worker.main"]
