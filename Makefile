.PHONY: setup lint test up down logs load-poc pipeline features train forecast smoke smoke-infra load-test

COMPOSE = docker compose --env-file .env -f infra/docker-compose.yml
DATABASE_URL ?= postgresql://forecast:replace-with-a-strong-password@localhost:5432/sku_forecasting
MLFLOW_TRACKING_URI ?= http://localhost:5000

setup:
	uv sync

lint:
	uv run ruff check api scripts tests src dags

test:
	uv run pytest

up:
	test -f .env || cp .env.example .env
	$(COMPOSE) up --build -d

down:
	test -f .env || cp .env.example .env
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f

load-poc:
	$(COMPOSE) --profile poc run --rm forecast-loader

pipeline:
	PYTHONPATH=src DATABASE_URL="$(DATABASE_URL)" uv run python -m scripts.run_data_pipeline

features:
	PYTHONPATH=src DATABASE_URL="$(DATABASE_URL)" uv run python -m scripts.run_feature_pipeline

train:
	PYTHONPATH=src DATABASE_URL="$(DATABASE_URL)" MLFLOW_TRACKING_URI="$(MLFLOW_TRACKING_URI)" uv run python -m scripts.train_model

forecast:
	PYTHONPATH=src DATABASE_URL="$(DATABASE_URL)" MLFLOW_TRACKING_URI="$(MLFLOW_TRACKING_URI)" uv run python -m scripts.run_batch_forecast --sku-lookback-days 0 --lookback-days 200

smoke:
	curl --fail http://localhost:$${API_PORT:-8000}/health
	curl --fail http://localhost:$${API_PORT:-8000}/metrics >/dev/null

smoke-infra: smoke
	curl --fail http://localhost:$${MLFLOW_PORT:-5000}/health
	curl --fail http://localhost:$${AIRFLOW_PORT:-8080}/health
	curl --fail http://localhost:$${MINIO_PORT:-9000}/minio/health/live
	curl --fail http://localhost:$${PROMETHEUS_PORT:-9090}/-/ready
	curl --fail http://localhost:$${GRAFANA_PORT:-3000}/api/health

load-test:
	docker run --rm --network=host \
		-e API_URL=http://localhost:$${API_PORT:-8000} \
		-v $(CURDIR)/tests/load:/scripts \
		grafana/k6 run /scripts/forecast_k6.js
