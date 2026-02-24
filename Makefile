.PHONY: all build up down clean lint

# Enable PGO for local builds by default (set ENABLE_PGO=false to disable)
ENABLE_PGO ?= true

all: build

build:
	@echo "Building Docker images (ENABLE_PGO=$(ENABLE_PGO))..."
	@docker compose build --build-arg ENABLE_PGO=$(ENABLE_PGO)

up:
	@echo "Starting services..."
	@docker compose up -d

down:
	@echo "Stopping services..."
	@docker compose down

clean:
	@echo "Cleaning up..."
	@docker compose down -v

lint:
	@echo "Running linters..."
	@ruff check backend/ scripts/

format:
	@echo "Formatting code..."
	@ruff format backend/ scripts/
