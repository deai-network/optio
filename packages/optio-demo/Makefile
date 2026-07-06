.DEFAULT_GOAL := help
.PHONY: help install clean run run-dashboard check-venv deps-up deps-down

ROOT       := $(CURDIR)/../..
VENV       := $(ROOT)/.venv
PY         := $(VENV)/bin/python
PIP        := $(VENV)/bin/pip
WATCHFILES := $(VENV)/bin/watchfiles

# All locally-developed optio packages the demo depends on (transitively).
# `clean` purges their installed metadata; `install` reinstalls them editable
# in dependency order so a stale install can never pin an old optio-core.
LOCAL_PKGS := optio-core optio-host optio-agents optio-opencode optio-claudecode optio-grok optio-codex optio-cursor optio-kimicode optio-antigravity optio-demo

help:  ## Show this help
	@awk 'BEGIN { FS = ":.*##" } /^[a-zA-Z_-]+:.*##/ { printf "  \033[1m%-15s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

check-venv:
	@if [ ! -x $(PY) ]; then \
	  echo "ERROR: repo venv $(VENV) missing. Run \`make install\` from $(ROOT) first."; \
	  exit 1; \
	fi

deps-up:  ## Start the demo's Docker dependencies (Mongo + Redis)
	docker compose up -d

deps-down:  ## Stop the demo's Docker dependencies (keeps data volumes)
	docker compose down

install: check-venv deps-up  ## Install demo: docker compose + editable install of the full local optio stack
	$(PIP) install -e ../optio-core[redis]
	$(PIP) install -e ../optio-host
	$(PIP) install -e ../optio-agents
	$(PIP) install -e ../optio-opencode
	$(PIP) install -e ../optio-claudecode
	$(PIP) install -e ../optio-grok
	$(PIP) install -e ../optio-codex
	$(PIP) install -e ../optio-cursor
	$(PIP) install -e ../optio-kimicode
	$(PIP) install -e ../optio-antigravity
	$(PIP) install -e .

clean: check-venv  ## Uninstall all editable optio packages (purge stale install metadata; run `make clean install`)
	$(PIP) uninstall -y $(LOCAL_PKGS) || true

run: check-venv  ## Run optio_demo with auto-restart on Python source change (watches all locally-developed deps)
	# --sigint-timeout 15: on a file change watchfiles SIGINTs optio_demo then
	# SIGKILLs after this timeout (default 5s). The optio runloop's graceful
	# cooperative-cancel shutdown can take up to ~10s; 15s lets an in-flight
	# task finish before the SIGKILL. Idle restarts stay instant.
	$(WATCHFILES) --sigint-timeout 15 "$(PY) -m optio_demo" \
	  src \
	  ../optio-core/src \
	  ../optio-host/src \
	  ../optio-agents/src \
	  ../optio-opencode/src \
	  ../optio-claudecode/src \
	  ../optio-grok/src \
	  ../optio-codex/src \
	  ../optio-cursor/src \
	  ../optio-kimicode/src \
	  ../optio-antigravity/src

run-dashboard:  ## Run the optio-dashboard against the demo Mongo + Redis
	MONGODB_URL=mongodb://localhost:27017/optio-demo REDIS_URL=redis://localhost:6379 npx optio-dashboard
