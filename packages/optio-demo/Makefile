.DEFAULT_GOAL := help
.PHONY: help install clean run run-dashboard check-venv

ROOT       := $(CURDIR)/../..
VENV       := $(ROOT)/.venv
PY         := $(VENV)/bin/python
PIP        := $(VENV)/bin/pip
WATCHFILES := $(VENV)/bin/watchfiles

# All locally-developed optio packages the demo depends on (transitively).
# `clean` purges their installed metadata; `install` reinstalls them editable
# in dependency order so a stale install can never pin an old optio-core.
LOCAL_PKGS := optio-core optio-host optio-agents optio-opencode optio-claudecode optio-demo

help:  ## Show this help
	@awk 'BEGIN { FS = ":.*##" } /^[a-zA-Z_-]+:.*##/ { printf "  \033[1m%-15s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

check-venv:
	@if [ ! -x $(PY) ]; then \
	  echo "ERROR: repo venv $(VENV) missing. Run \`make install\` from $(ROOT) first."; \
	  exit 1; \
	fi

install: check-venv  ## Install demo: docker compose + editable install of the full local optio stack
	docker compose up -d
	$(PIP) install -e ../optio-core[redis]
	$(PIP) install -e ../optio-host
	$(PIP) install -e ../optio-agents
	$(PIP) install -e ../optio-opencode
	$(PIP) install -e ../optio-claudecode
	$(PIP) install -e .

clean: check-venv  ## Uninstall all editable optio packages (purge stale install metadata; run `make clean install`)
	$(PIP) uninstall -y $(LOCAL_PKGS) || true

run: check-venv  ## Run optio_demo with auto-restart on Python source change (watches all locally-developed deps)
	$(WATCHFILES) "$(PY) -m optio_demo" \
	  src \
	  ../optio-core/src \
	  ../optio-host/src \
	  ../optio-opencode/src \
	  ../optio-claudecode/src

run-dashboard:  ## Run the optio-dashboard against the demo Mongo + Redis
	MONGODB_URL=mongodb://localhost:27017/optio-demo REDIS_URL=redis://localhost:6379 npx optio-dashboard
