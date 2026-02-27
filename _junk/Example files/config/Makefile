# Trading Bot Makefile
# Centralizes common build, test, and run commands

.PHONY: help install test lint run validate start-services agent-status generate-tests clean

help: ## Show this help message
	@echo "Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-20s %s\n", $$1, $$2}'

install: ## Install Python and Node.js dependencies
	conda env create -f environment.yml --force
	npm install

test: ## Run all tests with pytest
	pytest

test-single: ## Run a single test file (usage: make test-single FILE=tests/test_file.py)
	pytest $(FILE)

test-name: ## Run tests by name pattern (usage: make test-name PATTERN="test_name")
	pytest -k "$(PATTERN)"

lint: ## Run linting tools (flake8, mypy, black)
	flake8
	mypy .
	black --check .
	npm run check

lint-fix: ## Auto-fix linting issues where possible
	black .
	# Add other auto-fixers if available

run: ## Run the main trading bot
	python main.py

run-api: ## Run the API server
	uvicorn api_server:app --host 0.0.0.0 --port 8000

run-analyze: ## Run analysis (if npm script exists)
	npm run analyze

validate: ## Run all validation agents
	./scripts/validate_all.sh

start-services: ## Start all services with Docker Compose
	./start_services.sh

agent-status: ## Check agent system status
	./scripts/agent_status.sh

generate-tests: ## Generate tests for uncovered modules
	./scripts/generate_tests.sh

clean: ## Clean up generated files and caches
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type d -name ".mypy_cache" -exec rm -rf {} +
	rm -rf reports/
	npm run clean 2>/dev/null || true

# Development safeguards
setup-dev: ## Set up development environment with pre-commit hooks
	./scripts/setup-dev-environment.sh

dev-server: ## Start development server with safeguards
	./scripts/dev-server.sh

check-ready: ## Check if code is ready for commit
	./scripts/check-ready-for-commit.sh

critical-tests: ## Run critical path tests only
	./scripts/run-critical-tests.sh

test-parallel: ## Run tests in parallel
	./scripts/test-parallel.sh

# Pre-commit hooks
pre-commit-install: ## Install pre-commit hooks
	pre-commit install

pre-commit-run: ## Run pre-commit on all files
	pre-commit run --all-files

pre-commit-update: ## Update pre-commit hooks
	pre-commit autoupdate