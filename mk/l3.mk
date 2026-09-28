.PHONY: test-l3

# Requires an active Phase 1 lab created by make lab-up.
test-l3:
	$(PYTHON) -m pytest -q -p no:cacheprovider tests/integration/l3/test_gateway_plan.py
	$(PYTHON) config/gateway/apply.py
	$(PYTHON) config/gateway/apply.py
	$(PYTHON) tests/integration/l3/measure.py --site all
