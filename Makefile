.DEFAULT_GOAL := help

.PHONY: help install test test-all lint format-check run consumer up down scan-gitleaks scan-pip-audit scan-npm-audit scan-trivy scan-checkov scan-terraform scan-all

help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

install: ## Install dependencies with uv
	uv sync

test: ## Run server test suite with coverage
	uv run pytest tests/ --cov=app --cov-report=term-missing -q

test-all: ## Run server and client test suites
	uv run pytest tests/ --cov=app --cov-report=term-missing -q
	cd client && pnpm install --frozen-lockfile && pnpm test

lint: ## Run ruff lint checks (requires uvx)
	command -v uvx >/dev/null 2>&1 || (echo "error: 'uvx' not found. Install uv (https://docs.astral.sh/uv/) to run lint." >&2; exit 1)
	uvx ruff check .

format-check: ## Check formatting with ruff (requires uvx)
	command -v uvx >/dev/null 2>&1 || (echo "error: 'uvx' not found. Install uv (https://docs.astral.sh/uv/) to run format-check." >&2; exit 1)
	uvx ruff format --check .

scan-gitleaks: ## Run gitleaks secret scan (requires gitleaks)
	command -v gitleaks >/dev/null 2>&1 || (echo "error: 'gitleaks' not found. Install: brew install gitleaks" >&2; exit 1)
	gitleaks detect --source . --redact --report-format json --report-path gitleaks-report.json

scan-pip-audit: ## Run pip-audit on Python dependencies (requires uvx)
	command -v uvx >/dev/null 2>&1 || (echo "error: 'uvx' not found. Install uv (https://docs.astral.sh/uv/)." >&2; exit 1)
	uvx pip-audit

scan-npm-audit: ## Run npm audit on client dependencies (requires pnpm)
	cd client && pnpm audit

scan-trivy: ## Run Trivy filesystem scan (requires trivy)
	command -v trivy >/dev/null 2>&1 || (echo "error: 'trivy' not found. Install: brew install trivy" >&2; exit 1)
	trivy fs --severity CRITICAL,HIGH --format json --output trivy-report.json .

scan-checkov: ## Run Checkov on Terraform IaC (requires checkov)
	command -v checkov >/dev/null 2>&1 || (echo "error: 'checkov' not found. Install: pip install checkov" >&2; exit 1)
	checkov -d terraform/ --framework terraform --soft-fail

scan-terraform: ## Run Terraform fmt/validate (requires terraform)
	command -v terraform >/dev/null 2>&1 || (echo "error: 'terraform' not found. Install: brew install terraform" >&2; exit 1)
	cd terraform && terraform fmt -check -recursive && terraform init -backend=false && terraform validate

scan-all: ## Run all security scans
	$(MAKE) scan-gitleaks
	$(MAKE) scan-pip-audit
	$(MAKE) scan-npm-audit
	$(MAKE) scan-trivy
	$(MAKE) scan-checkov
	$(MAKE) scan-terraform

run: ## Run the Flask dev server (uses .env via python-dotenv)
	uv run python run.py

consumer: ## Run the Kafka consumer locally (CONSUMER_TYPE defaults to logs in consumer/config.py)
	uv run python consumer/app.py

up: ## Start all services with Docker Compose (reads .env via compose env_file)
	docker compose up --build

down: ## Stop all Docker Compose services
	docker compose down
