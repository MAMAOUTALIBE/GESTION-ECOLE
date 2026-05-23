# GESTION-EE Backend — Makefile (Module 0)
#
# Toutes les cibles supposent qu'on est dans Backend/ et qu'`uv` est installe.
# `make help` liste les cibles disponibles.

.DEFAULT_GOAL := help

VENV          := .venv
PY            := $(VENV)/bin/python
PYTEST        := $(VENV)/bin/pytest
RUFF          := $(VENV)/bin/ruff
MYPY          := $(VENV)/bin/mypy
LOCUST        := $(VENV)/bin/locust

PG_HOST       ?= 127.0.0.1
PG_PORT       ?= 5433
PG_USER       ?= gestionee
PG_PASSWORD   ?= gestionee_dev_2026
TEST_DB       ?= gestionee_test

# --- meta -------------------------------------------------------------------
.PHONY: help
help: ## Affiche cette aide
	@awk 'BEGIN {FS = ":.*?## "; printf "\nUsage:\n  make \033[36m<target>\033[0m\n\nCibles:\n"} \
	  /^[a-zA-Z0-9_.-]+:.*?## / { printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2 }' \
	  $(MAKEFILE_LIST)

# --- bootstrap --------------------------------------------------------------
.PHONY: install
install: ## Installe / synchronise les dependances (uv sync)
	uv sync

# --- tests ------------------------------------------------------------------
.PHONY: test
test: ## Lance TOUS les tests (unit + integration)
	$(PYTEST) tests/ -q

.PHONY: test-unit
test-unit: ## Lance uniquement les tests unitaires racine (les 240 historiques)
	$(PYTEST) tests/ -q --ignore=tests/integration --ignore=tests/load

.PHONY: test-integration
test-integration: ## Lance la suite d'integration (DB + Redis reels)
	$(PYTEST) tests/integration/ -q

.PHONY: test-integration-parallel
test-integration-parallel: ## Idem mais en parallele via pytest-xdist
	$(PYTEST) tests/integration/ -q -n auto

.PHONY: test-load
test-load: ## Lance Locust en mode UI (http://localhost:8089)
	$(LOCUST) -f tests/load/locustfile.py --host http://localhost:8000

# --- qualite ---------------------------------------------------------------
.PHONY: lint
lint: ## Lint ruff + mypy strict
	$(RUFF) check app tests
	$(MYPY) app

.PHONY: format
format: ## Format ruff (in-place)
	$(RUFF) format app tests
	$(RUFF) check app tests --fix

# --- couverture ------------------------------------------------------------
.PHONY: coverage
coverage: ## Couverture en mode term-missing
	$(PYTEST) tests/ --cov=app --cov-report=term-missing

.PHONY: coverage-html
coverage-html: ## Genere un rapport HTML et l'ouvre
	$(PYTEST) tests/ --cov=app --cov-report=html
	@command -v open >/dev/null && open htmlcov/index.html || \
	  echo "Rapport disponible dans htmlcov/index.html"

# --- DB de test ------------------------------------------------------------
.PHONY: db-test-create
db-test-create: ## Cree la base gestionee_test (idempotent, active PostGIS si dispo)
	PGHOST=$(PG_HOST) PGPORT=$(PG_PORT) PGUSER=$(PG_USER) \
	  PGPASSWORD=$(PG_PASSWORD) TEST_DB_NAME=$(TEST_DB) \
	  bash scripts/create_test_db.sh

.PHONY: db-test-recreate
db-test-recreate: ## Drop + cree la base gestionee_test (utile apres bug fixture)
	PGHOST=$(PG_HOST) PGPORT=$(PG_PORT) PGUSER=$(PG_USER) \
	  PGPASSWORD=$(PG_PASSWORD) TEST_DB_NAME=$(TEST_DB) DROP=1 \
	  bash scripts/create_test_db.sh

.PHONY: db-test-drop
db-test-drop: ## Drop la base gestionee_test (garde anti-prod via scripts/drop_test_db.sh)
	PGHOST=$(PG_HOST) PGPORT=$(PG_PORT) PGUSER=$(PG_USER) \
	  PGPASSWORD=$(PG_PASSWORD) TEST_DB_NAME=$(TEST_DB) \
	  bash scripts/drop_test_db.sh

# --- divers ----------------------------------------------------------------
.PHONY: clean
clean: ## Supprime caches pytest, mypy, ruff, coverage
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage coverage.xml
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
