.PHONY: test-l2

# Requires an active Phase 1 lab created by make lab-up.
test-l2:
	$(PYTHON) -m pytest -q -p no:cacheprovider tests/integration/l2/test_switching_plan.py
	$(PYTHON) tests/integration/l2/measure.py --site all
