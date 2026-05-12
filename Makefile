.DEFAULT_GOAL := help
.PHONY: help install install-demo run-demo run-demo-dashboard build build-dashboard run-dashboard-api run-dashboard-dev codegen test test-interop lint lint-no-direct-writes clean clean-codegen clean-deep

PY_PACKAGES := optio-core optio-host optio-opencode

help:  ## Show this help
	@awk 'BEGIN { FS = ":.*##" } /^[a-zA-Z_-]+:.*##/ { printf "  \033[1m%-15s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

install:  ## Install dependencies (TS workspace + Python packages)
	pnpm install
	for pkg in $(PY_PACKAGES); do \
	  (cd packages/$$pkg && pip install -e .[dev] 2>/dev/null || pip install -e .); \
	done

build:  ## Build all packages
	pnpm -r build
	for pkg in $(PY_PACKAGES); do \
	  (cd packages/$$pkg && python -m build 2>/dev/null || true); \
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

test:  ## Run all tests (TS + Python; per-package, no docker)
	pnpm -r test
	for pkg in $(PY_PACKAGES); do \
	  (cd packages/$$pkg && pytest); \
	done

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

clean:  ## Remove build artifacts and dependency caches (KEEPS committed _generated/)
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
