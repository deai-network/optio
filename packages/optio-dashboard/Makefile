.PHONY: help build run dev

ROOT := ../..

help:
	@echo "Available targets:"
	@echo "  build  Install dependencies and build all packages"
	@echo "  run    Start the dashboard server"
	@echo "  dev    Start Vite dev server (proxies /api to localhost:3000, requires make run)"

build:
	pnpm --dir $(ROOT) install
	pnpm --dir $(ROOT) --filter optio-contracts build
	pnpm --dir $(ROOT) --filter optio-api build
	pnpm --dir $(ROOT) --filter optio-ui build
	pnpm --dir $(ROOT) --filter optio-dashboard build

run:
	pnpm --dir $(ROOT) --filter optio-dashboard start

dev:
	pnpm --dir $(ROOT) --filter optio-dashboard dev
