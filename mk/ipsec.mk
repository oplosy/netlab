.PHONY: test-ipsec

# Requires make lab-up and the VPN-150 runtime dependencies.
test-ipsec:
	$(PYTHON) -m pytest -q -p no:cacheprovider tests/integration/ipsec/test_plan.py
	$(PYTHON) tests/integration/ipsec/measure.py
