.PHONY: help install dev test lint type-check format clean docker-up docker-down migrate

PYTHON := python3
PIP := pip
PYTEST := pytest

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ─── Installation ─────────────────────────────────────────────
install: ## Install production dependencies
	$(PIP) install -e .

dev: ## Install development dependencies
	$(PIP) install -e ".[dev]"

# ─── Testing ──────────────────────────────────────────────────
test: ## Run all tests
	$(PYTEST) tests/ -v

test-unit: ## Run unit tests only (fast)
	$(PYTEST) tests/unit/ -v -m unit

test-contract: ## Run contract tests only
	$(PYTEST) tests/contract/ -v -m contract

test-integration: ## Run integration tests (requires DB, Redis)
	$(PYTEST) tests/integration/ -v -m integration

test-e2e: ## Run end-to-end tests (requires all services)
	$(PYTEST) tests/e2e/ -v -m e2e

test-cov: ## Run tests with coverage report
	$(PYTEST) tests/ -v --cov=prime_jennie --cov-report=html --cov-report=term-missing

# ─── Code Quality ─────────────────────────────────────────────
lint: ## Run linter (ruff)
	ruff check prime_jennie/ tests/

format: ## Auto-format code (ruff)
	ruff format prime_jennie/ tests/
	ruff check --fix prime_jennie/ tests/

type-check: ## Run type checker (mypy)
	mypy prime_jennie/

# ─── Database ─────────────────────────────────────────────────
migrate: ## Run database migrations
	alembic upgrade head

migrate-create: ## Create new migration (usage: make migrate-create MSG="add xyz")
	alembic revision --autogenerate -m "$(MSG)"

# ─── Docker ───────────────────────────────────────────────────
docker-up: ## Start all services
	docker compose up -d

docker-up-infra: ## Start infrastructure only (DB, Redis, vLLM)
	docker compose --profile infra up -d

docker-up-trading: ## Start trading services
	docker compose --profile trading up -d

docker-down: ## Stop all services
	docker compose down

docker-build: ## Build all service images
	DOCKER_BUILDKIT=1 docker compose build

docker-logs: ## Tail all service logs
	docker compose logs -f --tail=100

# ─── Data Migration ───────────────────────────────────────────
migrate-data: ## Migrate data from my-prime-jennie
	$(PYTHON) scripts/migrate_from_my_prime_jennie.py

migrate-data-dry: ## Dry-run data migration (no writes)
	$(PYTHON) scripts/migrate_from_my_prime_jennie.py --dry-run

# ─── Cleanup ──────────────────────────────────────────────────
clean: ## Clean build artifacts
	rm -rf build/ dist/ *.egg-info .pytest_cache .mypy_cache htmlcov/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
