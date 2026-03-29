.PHONY: install run run-dashboard

install:
	docker compose up -d
	pip install -e ../optio-core[redis]
	pip install -e .

run:
	python -m optio_demo

run-dashboard:
	MONGODB_URL=mongodb://localhost:27017/optio-demo REDIS_URL=redis://localhost:6379 npx optio-dashboard
