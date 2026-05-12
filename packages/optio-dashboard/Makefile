.DEFAULT_GOAL := help
.PHONY: help build run-api dev

ROOT := ../..

help:  ## Show this help
	@awk 'BEGIN { FS = ":.*##" } /^[a-zA-Z_-]+:.*##/ { printf "  \033[1m%-15s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

build:  ## Install dependencies and build all packages
	pnpm --dir $(ROOT) install
	pnpm --dir $(ROOT) --filter optio-contracts build
	pnpm --dir $(ROOT) --filter optio-api build
	pnpm --dir $(ROOT) --filter optio-ui build
	pnpm --dir $(ROOT) --filter optio-dashboard build

run-api:  ## Start the dashboard API server
	pnpm --dir $(ROOT) --filter optio-dashboard start

dev:  ## Start Vite dev server (proxies /api to localhost:3000; requires `make run-api`)
	pnpm --dir $(ROOT) --filter optio-dashboard dev
