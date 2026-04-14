.PHONY: help build run

ROOT := ../..

help:
	@echo "Available targets:"
	@echo "  build  Install dependencies and build all packages"
	@echo "  run    Start the dashboard server"

build:
	pnpm --dir $(ROOT) install
	pnpm --dir $(ROOT) --filter optio-contracts build
	pnpm --dir $(ROOT) --filter optio-api build
	pnpm --dir $(ROOT) --filter optio-ui build
	pnpm --dir $(ROOT) --filter optio-dashboard build

run:
	pnpm --dir $(ROOT) --filter optio-dashboard start
