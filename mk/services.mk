.PHONY: test-services

# Requires make lab-up, an active OSPF/L3 baseline, and the pinned images.
test-services:
	$(PYTHON) -m pytest -q -p no:cacheprovider tests/integration/services/test_plan.py tests/integration/l3/test_gateway_plan.py
	$(PYTHON) scripts/validate/validate_inventory.py
	$(PYTHON) scripts/lifecycle/render_topology.py --check
	$(PYTHON) automation/roles/services/apply.py
	$(PYTHON) automation/roles/services/apply.py
	$(PYTHON) config/gateway/apply.py
	$(PYTHON) config/gateway/apply.py
	$(PYTHON) tests/integration/services/acceptance.py
