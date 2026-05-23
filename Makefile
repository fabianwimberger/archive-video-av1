.PHONY: all build up down clean lint format cluster-build cluster-up cluster-down cluster-recreate node-build node-up node-down node-recreate

# Enable PGO, LTO, and native arch optimization for local builds by default
ENABLE_PGO ?= true
ENABLE_LTO ?= true
ARCH_FLAGS ?= -march=native
CLUSTER_COMPOSE_FILES := -f docker-compose.yml -f docker-compose.override.yml -f docker-compose.build.yml -f docker-compose.cluster.yml
SERVICE ?= convert-service

all: build

build:
	@echo "Building Docker images (ENABLE_PGO=$(ENABLE_PGO), ENABLE_LTO=$(ENABLE_LTO), ARCH_FLAGS=$(ARCH_FLAGS))..."
	@docker compose build --build-arg ENABLE_PGO=$(ENABLE_PGO) --build-arg ENABLE_LTO=$(ENABLE_LTO) --build-arg ARCH_FLAGS=$(ARCH_FLAGS)

up:
	@echo "Starting services..."
	@docker compose up -d

down:
	@echo "Stopping services..."
	@docker compose down

cluster-build:
	@echo "Building cluster service with override, build, and cluster compose files..."
	@docker compose $(CLUSTER_COMPOSE_FILES) build --build-arg ENABLE_PGO=$(ENABLE_PGO) --build-arg ENABLE_LTO=$(ENABLE_LTO) --build-arg ARCH_FLAGS=$(ARCH_FLAGS) $(SERVICE)

cluster-up:
	@echo "Starting cluster service with override, build, and cluster compose files..."
	@docker compose $(CLUSTER_COMPOSE_FILES) up -d $(SERVICE)

cluster-down:
	@echo "Stopping cluster stack with override, build, and cluster compose files..."
	@docker compose $(CLUSTER_COMPOSE_FILES) down

cluster-recreate:
	@echo "Recreating cluster service with override, build, and cluster compose files..."
	@docker compose $(CLUSTER_COMPOSE_FILES) up -d --force-recreate $(SERVICE)

node-build: cluster-build

node-up: cluster-up

node-down: cluster-down

node-recreate: cluster-recreate

clean:
	@echo "Cleaning up..."
	@docker compose down -v

lint:
	@echo "Running linters..."
	@ruff check backend/ scripts/

format:
	@echo "Formatting code..."
	@ruff format backend/ scripts/
