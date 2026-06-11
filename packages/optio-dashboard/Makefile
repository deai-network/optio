.DEFAULT_GOAL := help
.PHONY: help build compile run-api dev

ROOT := ../..

help:  ## Show this help
	@awk 'BEGIN { FS = ":.*##" } /^[a-zA-Z_-]+:.*##/ { printf "  \033[1m%-15s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

compile:  ## Recompile all workspace packages (no install)
	pnpm --dir $(ROOT) --filter optio-contracts build
	pnpm --dir $(ROOT) --filter optio-api build
	pnpm --dir $(ROOT) --filter optio-ui build
	pnpm --dir $(ROOT) --filter optio-dashboard build

build:  ## Install dependencies and build all packages
	pnpm --dir $(ROOT) install
	$(MAKE) compile

run-api: compile  ## Recompile, then start the dashboard API server (OPTIO_PASSWORD defaults to optio-dev if unset)
	OPTIO_PASSWORD="$${OPTIO_PASSWORD:-optio-dev}" pnpm --dir $(ROOT) --filter optio-dashboard start

dev:  ## Start Vite dev server (proxies /api to localhost:3000; requires `make run-api`)
	pnpm --dir $(ROOT) --filter optio-dashboard dev
