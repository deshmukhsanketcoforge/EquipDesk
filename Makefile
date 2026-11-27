.PHONY: help test build up down logs seed demo

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-8s %s\n", $$1, $$2}'

test: ## Lint, type-check and run tests in Docker (same as CI)
	docker compose build test
	docker compose run --rm test

build: ## Build all images
	docker compose --profile test --profile demo build

up: ## Start db + api (Swagger at http://localhost:8000/docs)
	docker compose up --build --detach --wait

down: ## Stop everything and remove volumes
	docker compose --profile test --profile demo down --volumes

logs: ## Follow api logs
	docker compose logs --follow api

seed: ## (Re)load demo data; idempotent, keeps review tickets. Starts db + api if needed
	docker compose up --build --detach --wait api
	docker compose exec api python -m server.db.seed

demo: ## Run the agent demo against the real model (needs FOUNDRY_* env)
	docker compose --profile demo run --rm --build agent
