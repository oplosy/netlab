.PHONY: observability-prepare observability-up observability-down test-observability

OBS_COMPOSE := docker compose -f observability/compose.yaml -f artifacts/observability/compose.agents.yaml
OBS_RUNTIME_PYTHON ?= python3

observability-prepare:
	$(PYTHON) automation/roles/observability/prepare.py

observability-up: observability-prepare
	set -a; source versions.env; source artifacts/observability/runtime.env; set +a; $(OBS_COMPOSE) up -d --build
	$(PYTHON) automation/roles/observability/apply.py

observability-down: observability-prepare
	set -a; source versions.env; source artifacts/observability/runtime.env; set +a; $(OBS_COMPOSE) down

test-observability:
	$(PYTHON) -m pytest -q -p no:cacheprovider tests/integration/observability/test_plan.py
	$(OBS_RUNTIME_PYTHON) tests/integration/observability/acceptance.py

lab-down: observability-down
