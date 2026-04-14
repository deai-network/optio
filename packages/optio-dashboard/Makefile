.PHONY: build run

ROOT := ../..

build:
	pnpm --dir $(ROOT) install
	pnpm --dir $(ROOT) --filter optio-contracts build
	pnpm --dir $(ROOT) --filter optio-api build
	pnpm --dir $(ROOT) --filter optio-ui build
	pnpm --dir $(ROOT) --filter optio-dashboard build

run:
	pnpm --dir $(ROOT) --filter optio-dashboard start
