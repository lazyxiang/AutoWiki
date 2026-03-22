FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
COPY shared/ ./shared/
COPY api/ ./api/
COPY cli/ ./cli/
COPY worker/ ./worker/
RUN pip install --no-cache-dir .
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "3001"]
