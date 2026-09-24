.PHONY: test-ospf

# Requires make lab-up and the VPN-150 runtime dependencies.
test-ospf:
	$(PYTHON) -m pytest -q -p no:cacheprovider tests/integration/ospf/test_plan.py tests/integration/l3/test_gateway_plan.py
	bash automation/roles/ospf/apply.sh
	bash automation/roles/ospf/apply.sh
	$(PYTHON) config/gateway/apply.py
	NETLAB_OSPF_LIVE=1 $(PYTHON) tests/integration/ospf/measure.py --output evidence/specs/ospf/latest.json
