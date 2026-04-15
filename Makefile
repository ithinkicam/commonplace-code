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

worker-install: ## Install worker LaunchAgent (symlink plist and bootstrap)
	mkdir -p ~/Library/LaunchAgents
	ln -sf "$(CURDIR)/scripts/com.commonplace.worker.plist" ~/Library/LaunchAgents/com.commonplace.worker.plist
	launchctl bootstrap gui/$$UID ~/Library/LaunchAgents/com.commonplace.worker.plist

worker-uninstall: ## Remove worker LaunchAgent (bootout and unlink)
	launchctl bootout gui/$$UID ~/Library/LaunchAgents/com.commonplace.worker.plist || true
	rm -f ~/Library/LaunchAgents/com.commonplace.worker.plist

tailscale-serve: ## Expose commonplace-server at https://<host>:8443 via Tailscale (ADR-0004)
	~/.local/bin/tailscale serve --bg --https=8443 --set-path=/ http://127.0.0.1:8765

tailscale-unserve: ## Tear down the Tailscale serve mapping for :8443
	~/.local/bin/tailscale serve --https=8443 off

clean:          ## Remove build artifacts
	find . -type d -name __pycache__ -exec rm -rf {} +
	rm -rf .pytest_cache .mypy_cache .ruff_cache
