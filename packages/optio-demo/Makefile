.DEFAULT_GOAL := help
.PHONY: help install run run-dashboard check-venv

ROOT       := $(CURDIR)/../..
VENV       := $(ROOT)/.venv
PY         := $(VENV)/bin/python
PIP        := $(VENV)/bin/pip
WATCHFILES := $(VENV)/bin/watchfiles

help:  ## Show this help
	@awk 'BEGIN { FS = ":.*##" } /^[a-zA-Z_-]+:.*##/ { printf "  \033[1m%-15s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

check-venv:
	@if [ ! -x $(PY) ]; then \
	  echo "ERROR: repo venv $(VENV) missing. Run \`make install\` from $(ROOT) first."; \
	  exit 1; \
	fi

install: check-venv  ## Install demo: bring up docker compose + pip install optio-core[redis] + this package
	docker compose up -d
	$(PIP) install -e ../optio-core[redis]
	$(PIP) install -e .

run: check-venv  ## Run optio_demo with auto-restart on Python source change (watches all locally-developed deps)
	$(WATCHFILES) "$(PY) -m optio_demo" \
	  src \
	  ../optio-core/src \
	  ../optio-host/src \
	  ../optio-opencode/src \
	  ../optio-claudecode/src

run-dashboard:  ## Run the optio-dashboard against the demo Mongo + Redis
	MONGODB_URL=mongodb://localhost:27017/optio-demo REDIS_URL=redis://localhost:6379 npx optio-dashboard
