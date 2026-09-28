.PHONY: test-ipsec

# Requires make lab-up and the VPN-150 runtime dependencies.
test-ipsec:
	$(PYTHON) -m pytest -q -p no:cacheprovider tests/integration/ipsec/test_plan.py
	$(PYTHON) tests/integration/ipsec/measure.py

.PHONY: test-ipsec-redundancy

# Requires a freshly deployed current topology: make lab-up. Applies current
# routing/security intent, tests idempotence, then runs bounded live failover.
test-ipsec-redundancy:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m pytest -q -p no:cacheprovider tests/integration/ipsec/test_plan.py
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m pytest -q -p no:cacheprovider tests/integration/ospf/test_plan.py
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m pytest -q -p no:cacheprovider tests/integration/bgp/test_policy.py tests/integration/bgp/test_multihoming.py
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m pytest -q -p no:cacheprovider tests/security/test_policy.py
	$(PYTHON) config/ipsec/apply.py
	$(PYTHON) config/gateway/apply.py
	bash automation/roles/ospf/apply.sh
	bash automation/roles/ospf/apply.sh
	$(PYTHON) config/security/apply.py
	$(PYTHON) tests/integration/ipsec/measure_redundancy.py --output evidence/specs/ipsec/wan-230-latest.json
