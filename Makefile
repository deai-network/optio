.DEFAULT_GOAL := help
.PHONY: help install check-tooling install-demo run-demo run-demo-dashboard build build-dashboard run-dashboard-api run-dashboard-dev codegen test test-interop lint lint-no-direct-writes clean clean-codegen clean-deep

PY_PACKAGES := optio-core optio-host optio-agents optio-opencode optio-codex optio-cursor optio-claudecode optio-grok optio-antigravity optio-kimicode

# Test parallelism (pytest-xdist). Tests marked `serial` (spawn-heavy or
# timing-fragile — real subprocess servers, docker containers, sub-second
# throttle/cancel windows) run in a final, non-parallel phase; everything else
# fans out across workers. Worker count defaults to HALF the CPUs on purpose:
# oversubscribing (e.g. -n auto on a many-core box) starves the subprocess- and
# timing-heavy tests and makes them flake. Override with `make test PYTEST_WORKERS=N`.
PYTEST_WORKERS ?= $(shell n=$$(nproc 2>/dev/null || echo 4); w=$$((n/2)); [ $$w -lt 2 ] && w=2; echo $$w)
PYTEST_XDIST   := -n $(PYTEST_WORKERS) --dist loadscope

# optio-core is run serially (its lifecycle tests are timing-sensitive); the
# rest fan out under xdist.
XDIST_PACKAGES := $(filter-out optio-core,$(PY_PACKAGES))

# Python toolchain — repo-local venv. Override PYTHON to pick a specific interpreter.
PYTHON ?= python3
VENV   := $(CURDIR)/.venv
PY     := $(VENV)/bin/python
PIP    := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest

$(VENV)/bin/python:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip

help:  ## Show this help
	@awk 'BEGIN { FS = ":.*##" } /^[a-zA-Z_-]+:.*##/ { printf "  \033[1m%-15s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

check-tooling:  ## Verify node + pnpm match repo pins; clear errors if missing
	@if [ ! -f .nvmrc ]; then echo "ERROR: .nvmrc missing"; exit 1; fi
	@expected_node=$$(cat .nvmrc | tr -d 'v'); \
	 if ! command -v node >/dev/null 2>&1; then \
	   echo "ERROR: node not installed."; \
	   echo "  Install nvm: https://github.com/nvm-sh/nvm#installing-and-updating"; \
	   echo "  Then run:    nvm install"; \
	   exit 1; \
	 fi; \
	 actual_node=$$(node --version | tr -d 'v'); \
	 if [ "$$actual_node" != "$$expected_node" ]; then \
	   echo "ERROR: node v$$actual_node does not match .nvmrc (v$$expected_node)."; \
	   echo "  Run: nvm install $$(cat .nvmrc) && nvm use"; \
	   echo "  (If nvm is missing: https://github.com/nvm-sh/nvm)"; \
	   exit 1; \
	 fi
	@if ! command -v corepack >/dev/null 2>&1; then \
	   echo "ERROR: corepack not on PATH (ships with Node 16.10+)."; \
	   echo "  After installing node, run once: corepack enable"; \
	   exit 1; \
	 fi
	@if ! command -v pnpm >/dev/null 2>&1; then \
	   echo "ERROR: pnpm not on PATH."; \
	   echo "  Run once: corepack enable"; \
	   exit 1; \
	 fi
	@echo "OK: node $$(node --version), pnpm $$(pnpm --version) (managed via corepack against package.json packageManager pin)"

install: check-tooling $(VENV)/bin/python  ## Install dependencies (TS workspace + Python packages)
	pnpm install
	for pkg in $(PY_PACKAGES); do \
	  (cd packages/$$pkg && $(PIP) install -e .[dev] 2>/dev/null || $(PIP) install -e .); \
	done

build: $(VENV)/bin/python  ## Build all packages
	pnpm -r build
	for pkg in $(PY_PACKAGES); do \
	  (cd packages/$$pkg && $(PY) -m build 2>/dev/null || true); \
	done

codegen:  ## Regenerate clamator RPC client/server stubs from optio-contracts source
	pnpm exec clamator-codegen \
	  --src packages/optio-contracts/src \
	  --out-ts packages/optio-api/src/_generated \
	  --out-py packages/optio-core/src/optio_core/_generated \
	  --ts-contract-import 'optio-contracts/optio-engine'
	@# Post-process: rename Python files with hyphens (invalid Python module
	@# identifiers) to underscored form. TS keeps hyphens (valid in TS).
	@if [ -f packages/optio-core/src/optio_core/_generated/optio-engine.py ]; then \
	  mv packages/optio-core/src/optio_core/_generated/optio-engine.py \
	     packages/optio-core/src/optio_core/_generated/optio_engine.py; \
	fi

test: $(VENV)/bin/python  ## Run all tests (TS + Python; per-package, no docker)
	pnpm -r test
	@rc=0; \
	echo ">> optio-core (serial — its lifecycle tests depend on real wall-clock timing and are unreliable under xdist)"; \
	(cd packages/optio-core && $(PYTEST)) || rc=1; \
	echo ">> Python phase 1/2: parallel ($(PYTEST_WORKERS) workers/pkg, -m 'not serial')"; \
	for pkg in $(XDIST_PACKAGES); do \
	  (cd packages/$$pkg && $(PYTEST) $(PYTEST_XDIST) -m "not serial") || rc=1; \
	done; \
	echo ">> Python phase 2/2: serial (spawn-heavy / timing-fragile, -m serial)"; \
	for pkg in $(XDIST_PACKAGES); do \
	  (cd packages/$$pkg && $(PYTEST) -m serial); s=$$?; \
	  [ $$s -eq 0 ] || [ $$s -eq 5 ] || rc=1; \
	done; \
	exit $$rc

test-interop:  ## End-to-end test: TS clamator client ↔ Py engine over real redis (clamator wire verification). INTEROP_DEBUG=1 enables verbose mode + increased timeouts (slow CI). INTEROP_KEEP=1 skips cleanup on failure for postmortem.
	timeout 120 bash packages/optio-demo/run-interop.sh

lint: lint-no-direct-writes  ## Lint all packages
	pnpm -r lint 2>/dev/null || true
	for pkg in $(PY_PACKAGES); do \
	  (cd packages/$$pkg && ruff check . 2>/dev/null || true); \
	done

lint-no-direct-writes:  ## Fail if any direct Mongo write call appears in packages/optio-api/src/
	@echo "Scanning packages/optio-api/src/ for direct Mongo writes..."
	@if grep -rEn --include='*.ts' --exclude-dir=__tests__ \
	    '\.(insertOne|insertMany|updateOne|updateMany|deleteOne|deleteMany|replaceOne|findOneAndUpdate|findOneAndReplace|findOneAndDelete|bulkWrite)\(' \
	    packages/optio-api/src/ ; then \
	  echo "ERROR: direct Mongo writes found in packages/optio-api/src/. The API server must only call engine RPCs for mutations." >&2 ; \
	  exit 1 ; \
	fi
	@echo "OK: no direct Mongo writes in packages/optio-api/src/"

clean:  ## Remove build artifacts and dependency caches (KEEPS committed _generated/ and $(VENV)/)
	pnpm -r clean 2>/dev/null || true
	rm -rf node_modules packages/*/node_modules packages/*/dist
	for pkg in $(PY_PACKAGES); do \
	  (cd packages/$$pkg && rm -rf build dist *.egg-info .pytest_cache); \
	done
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name .ruff_cache -prune -exec rm -rf {} +

clean-codegen:  ## Remove generated clamator stubs (require make codegen to rebuild)
	rm -rf packages/optio-api/src/_generated
	rm -rf packages/optio-core/src/optio_core/_generated

clean-deep: clean clean-codegen  ## clean + clean-codegen (full reset)

install-demo:  ## Install optio-demo (docker compose + pip)
	$(MAKE) -C packages/optio-demo install

run-demo:  ## Run the optio-demo Python entrypoint
	$(MAKE) -C packages/optio-demo run

run-demo-dashboard:  ## Run the dashboard against the demo Mongo + Redis
	$(MAKE) -C packages/optio-demo run-dashboard

build-dashboard:  ## Build optio-dashboard (and its package deps)
	$(MAKE) -C packages/optio-dashboard build

run-dashboard-api:  ## Start the dashboard API server
	$(MAKE) -C packages/optio-dashboard run-api

run-dashboard-dev:  ## Start the dashboard Vite dev server (requires run-dashboard-api)
	$(MAKE) -C packages/optio-dashboard dev

# -----------------------------------------------------------------------------
# Release targets
#
# Per-package releases via Python orchestrator. Each target takes BUMP=...
# Wire-locked optio-contracts and optio-core release together via release-wire.
# See docs/2026-05-18-release-infrastructure-design.md for design.

RELEASABLE_TS      := filtrum-core filtrum-mongo optio-ui optio-api optio-conversation-ui optio-dashboard
RELEASABLE_PY      := optio-host optio-agents optio-opencode optio-claudecode optio-grok optio-codex optio-cursor optio-kimicode optio-antigravity optio-demo
RELEASE_INDIVIDUAL := $(RELEASABLE_TS) $(RELEASABLE_PY)
WIRE_LOCKED        := optio-contracts optio-core

# Single dispatcher target: delegates to the Python orchestrator.
# Requires BUMP=<level> on the command line.
.PHONY: $(addprefix release-, $(RELEASE_INDIVIDUAL))
$(addprefix release-, $(RELEASE_INDIVIDUAL)): release-%: $(VENV)/bin/python
	@if [ -z "$(BUMP)" ]; then \
	  echo "ERROR: BUMP is required (patch | minor | none | promote-to-1.0)" >&2; \
	  exit 1; \
	fi
	$(PY) scripts/release/run.py per-package $* "$(BUMP)"

# Wire-locked packages: print a helpful message and exit.
.PHONY: $(addprefix release-, $(WIRE_LOCKED))
$(addprefix release-, $(WIRE_LOCKED)):
	@echo "wire-locked: use 'make release-wire BUMP=...' to release optio-contracts + optio-core together." >&2
	@exit 1

.PHONY: release-wire
release-wire: $(VENV)/bin/python  ## Release optio-contracts + optio-core in lockstep (requires BUMP=...)
	@if [ -z "$(BUMP)" ]; then \
	  echo "ERROR: BUMP is required (patch | minor | none | promote-to-1.0)" >&2; \
	  exit 1; \
	fi
	$(PY) scripts/release/run.py wire "$(BUMP)"

.PHONY: release-all
release-all: $(VENV)/bin/python  ## Release every package whose source > registry
	$(PY) scripts/release/run.py all

.PHONY: $(addprefix resume-release-, $(RELEASE_INDIVIDUAL))
$(addprefix resume-release-, $(RELEASE_INDIVIDUAL)): resume-release-%: $(VENV)/bin/python
	$(PY) scripts/release/run.py resume $*

.PHONY: $(addprefix clean-dist-, $(RELEASABLE_PY) $(RELEASABLE_TS) $(WIRE_LOCKED))
$(addprefix clean-dist-, $(RELEASABLE_PY) $(RELEASABLE_TS) $(WIRE_LOCKED)): clean-dist-%:
	rm -rf packages/$*/dist
