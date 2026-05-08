.DEFAULT_GOAL := help
.PHONY: help install build codegen test lint clean clean-codegen clean-deep

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
	  --ts-contract-import 'optio-contracts/engine-to-api'

test:  ## Run all tests (TS + Python; per-package, no docker)
	pnpm -r test
	for pkg in $(PY_PACKAGES); do \
	  (cd packages/$$pkg && pytest); \
	done

lint:  ## Lint all packages
	pnpm -r lint 2>/dev/null || true
	for pkg in $(PY_PACKAGES); do \
	  (cd packages/$$pkg && ruff check . 2>/dev/null || true); \
	done

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
