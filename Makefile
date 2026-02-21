.PHONY: all build up down clean lint

all: build

build:
	@echo "Building Docker images..."
	@docker compose build

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
