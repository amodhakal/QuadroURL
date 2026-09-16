.DEFAULT_GOAL := help

.PHONY: help install test lint format-check run consumer up down

help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

install: ## Install dependencies with uv
	uv sync

test: ## Run test suite with coverage
	uv run pytest tests/ --cov=app --cov-report=term-missing -q

lint: ## Run ruff lint checks (requires uvx)
	command -v uvx >/dev/null 2>&1 || (echo "error: 'uvx' not found. Install uv (https://docs.astral.sh/uv/) to run lint." >&2; exit 1)
	uvx ruff check .

format-check: ## Check formatting with ruff (requires uvx)
	command -v uvx >/dev/null 2>&1 || (echo "error: 'uvx' not found. Install uv (https://docs.astral.sh/uv/) to run format-check." >&2; exit 1)
	uvx ruff format --check .

run: ## Run the Flask dev server (uses .env via python-dotenv)
	uv run python run.py

consumer: ## Run the Kafka consumer locally (CONSUMER_TYPE defaults to logs in consumer/config.py)
	uv run python consumer/app.py

up: ## Start all services with Docker Compose (reads .env via compose env_file)
	docker compose up --build

down: ## Stop all Docker Compose services
	docker compose down
