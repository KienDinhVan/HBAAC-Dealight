FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH="/app/src:/app" \
    PATH="/app/.venv/bin:${PATH}"

WORKDIR /app

# LightGBM (mlflow model load) needs the GNU OpenMP runtime at inference time.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv==0.10.2

COPY pyproject.toml uv.lock README.md .python-version ./
COPY src ./src
RUN uv sync --frozen --no-dev

COPY api ./api
COPY scripts ./scripts
COPY datasets ./datasets
COPY data/artifacts ./data/artifacts

EXPOSE 8000

CMD ["uvicorn", "api.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
