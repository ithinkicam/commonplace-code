.PHONY: help test test-live smoke lint format safe-mode new-skill clean storygraph-import storygraph-dry library-scan library-import library-watch-install library-watch-uninstall bluesky-backfill bluesky-dry kindle-dry kindle-backfill kindle-cookies-install kindle-cookies-refresh notion-therapy-watch notion-therapy-dry notion-therapy-watch-install notion-therapy-watch-uninstall mcp-token-init mcp-token-rotate seed-feasts seed-feasts-dry logs log-rotate log-rotate-install log-rotate-uninstall db-backup db-restore

help:           ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-20s %s\n", $$1, $$2}'

test:           ## Run all tests (excludes live tests; see test-live)
	.venv/bin/python -m pytest tests/ -v

test-live:      ## Run live tests (Ollama + claude CLI + live DB; burns session budget)
	.venv/bin/python -m pytest tests/ -v -m live

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

library-scan:   ## Dry-run: list books that would be enqueued (non-destructive)
	.venv/bin/python scripts/library_scan.py --dry-run

library-import: ## Enqueue ingest_library jobs for all un-ingested books
	.venv/bin/python scripts/library_scan.py

library-watch-install: ## Install library-watch LaunchAgent (runs scan every 15 min)
	mkdir -p ~/Library/LaunchAgents
	ln -sf "$(CURDIR)/scripts/com.commonplace.library-watch.plist" ~/Library/LaunchAgents/com.commonplace.library-watch.plist
	launchctl bootstrap gui/$$UID ~/Library/LaunchAgents/com.commonplace.library-watch.plist

library-watch-uninstall: ## Remove library-watch LaunchAgent
	launchctl bootout gui/$$UID ~/Library/LaunchAgents/com.commonplace.library-watch.plist || true
	rm -f ~/Library/LaunchAgents/com.commonplace.library-watch.plist

storygraph-import: ## Import StoryGraph CSV into library DB (usage: make storygraph-import CSV=<path>)
	.venv/bin/python scripts/import_storygraph.py $(CSV)

storygraph-dry: ## Dry-run StoryGraph CSV import — reports counts, writes nothing (usage: make storygraph-dry CSV=<path>)
	.venv/bin/python scripts/import_storygraph.py $(CSV) --dry-run

bluesky-backfill: ## Ingest all Bluesky posts into the DB (real run)
	.venv/bin/python scripts/bluesky_backfill.py

bluesky-dry:    ## Dry-run: count Bluesky posts without ingesting
	.venv/bin/python scripts/bluesky_backfill.py --dry-run

kindle-dry:     ## Dry-run Kindle backfill — counts books + highlights using live cookies
	.venv/bin/python scripts/kindle_backfill.py --dry-run

kindle-backfill: ## Import all Kindle highlights into the DB (real run)
	.venv/bin/python scripts/kindle_backfill.py

kindle-cookies-refresh: ## Read live Amazon cookies from Chrome and store in Keychain (no manual export)
	.venv/bin/python scripts/kindle_cookies_from_chrome.py

kindle-cookies-install: ## Install Kindle session cookies from JSON file into Keychain (usage: make kindle-cookies-install COOKIES=<path>)
	@if [ -z "$(COOKIES)" ]; then echo "Usage: make kindle-cookies-install COOKIES=~/Downloads/amazon-cookies.json"; exit 1; fi
	@python3 -c "\
import json, subprocess, sys, os; \
p = os.path.expanduser('$(COOKIES)'); \
data = open(p).read(); \
json.loads(data); \
subprocess.run(['security', 'add-generic-password', '-U', '-a', 'commonplace', '-s', 'commonplace-kindle/session-cookies', '-w', data], check=True); \
os.unlink(p); \
print('Cookies installed in Keychain and source file deleted.')"

notion-therapy-watch: ## Run Notion Therapy watcher and enqueue changed pages
	.venv/bin/python scripts/notion_therapy_watch.py

notion-therapy-dry: ## Dry-run Notion Therapy watcher (lists pages, no enqueue)
	.venv/bin/python scripts/notion_therapy_watch.py --dry-run

notion-therapy-watch-install: ## Install Notion Therapy watcher LaunchAgent
	mkdir -p ~/Library/LaunchAgents
	ln -sf "$(CURDIR)/scripts/com.commonplace.notion-therapy-watch.plist" ~/Library/LaunchAgents/com.commonplace.notion-therapy-watch.plist
	launchctl bootstrap gui/$$UID ~/Library/LaunchAgents/com.commonplace.notion-therapy-watch.plist

notion-therapy-watch-uninstall: ## Remove Notion Therapy watcher LaunchAgent
	launchctl bootout gui/$$UID ~/Library/LaunchAgents/com.commonplace.notion-therapy-watch.plist || true
	rm -f ~/Library/LaunchAgents/com.commonplace.notion-therapy-watch.plist

mcp-token-init: ## Generate MCP URL-path token in keychain + write .mcp.json (idempotent)
	.venv/bin/python scripts/init_mcp_token.py

mcp-token-rotate: ## Rotate MCP token, rewrite .mcp.json, kick launchd service
	.venv/bin/python scripts/rotate_mcp_token.py

seed-feasts:    ## Import feasts.yaml into the feast table (idempotent)
	.venv/bin/python scripts/feast_import.py $(ARGS)

seed-feasts-dry: ## Dry-run feast import — validate + report counts, no DB writes
	.venv/bin/python scripts/feast_import.py --dry-run $(ARGS)

logs:           ## Tail all commonplace launchd logs
	tail -f ~/Library/Logs/commonplace-*.log

log-rotate:     ## Rotate commonplace logs now (ad-hoc, same policy as the cron)
	bash scripts/rotate_commonplace_logs.sh

log-rotate-install: ## Install daily 03:30 log-rotation LaunchAgent
	mkdir -p ~/Library/LaunchAgents
	ln -sf "$(CURDIR)/scripts/com.commonplace.log-rotate.plist" ~/Library/LaunchAgents/com.commonplace.log-rotate.plist
	launchctl bootstrap gui/$$UID ~/Library/LaunchAgents/com.commonplace.log-rotate.plist

log-rotate-uninstall: ## Remove log-rotation LaunchAgent
	launchctl bootout gui/$$UID ~/Library/LaunchAgents/com.commonplace.log-rotate.plist || true
	rm -f ~/Library/LaunchAgents/com.commonplace.log-rotate.plist

db-backup:      ## Run library DB backup now (integrity-checked, gzipped)
	bash scripts/library_db_backup.sh

db-restore:     ## Restore latest library DB backup (usage: make db-restore [FILE=...])
	bash scripts/library_db_restore.sh $(FILE)

clean:          ## Remove build artifacts
	find . -type d -name __pycache__ -exec rm -rf {} +
	rm -rf .pytest_cache .mypy_cache .ruff_cache
