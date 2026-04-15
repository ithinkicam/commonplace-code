.PHONY: help test smoke lint format safe-mode new-skill clean

help:           ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-20s %s\n", $$1, $$2}'

test:           ## Run all tests
	pytest tests/ -v

smoke:          ## Run smoke tests against running services
	bash scripts/smoke-test.sh

lint:           ## Run linters
	ruff check commonplace_server commonplace_worker tests
	mypy commonplace_server commonplace_worker

format:         ## Format code
	ruff format commonplace_server commonplace_worker tests

safe-mode:      ## Stop services, take snapshot, drop to safe shell
	bash scripts/safe-mode.sh

new-skill:      ## Scaffold a new skill file (usage: make new-skill name=foo)
	bash scripts/new-skill.sh $(name)

clean:          ## Remove build artifacts
	find . -type d -name __pycache__ -exec rm -rf {} +
	rm -rf .pytest_cache .mypy_cache .ruff_cache
