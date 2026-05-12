.DEFAULT_GOAL := help
.PHONY: help install run run-dashboard

help:  ## Show this help
	@awk 'BEGIN { FS = ":.*##" } /^[a-zA-Z_-]+:.*##/ { printf "  \033[1m%-15s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

install:  ## Install demo: bring up docker compose + pip install optio-core[redis] + this package
	docker compose up -d
	pip install -e ../optio-core[redis]
	pip install -e .

run:  ## Run the optio_demo Python entrypoint
	python -m optio_demo

run-dashboard:  ## Run the optio-dashboard against the demo Mongo + Redis
	MONGODB_URL=mongodb://localhost:27017/optio-demo REDIS_URL=redis://localhost:6379 npx optio-dashboard
