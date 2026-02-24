.PHONY: all build up down clean lint

# Enable PGO and native arch optimization for local builds by default
ENABLE_PGO ?= true
ARCH_FLAGS ?= -march=native

all: build

build:
	@echo "Building Docker images (ENABLE_PGO=$(ENABLE_PGO), ARCH_FLAGS=$(ARCH_FLAGS))..."
	@docker compose build --build-arg ENABLE_PGO=$(ENABLE_PGO) --build-arg ARCH_FLAGS=$(ARCH_FLAGS)

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
