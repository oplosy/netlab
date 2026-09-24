# SEC-170 policy validation and live acceptance.
.PHONY: test-security

test-security:
	$(PYTHON) -m pytest -q -p no:cacheprovider tests/security/test_policy.py
	$(PYTHON) scripts/validate/validate_inventory.py
	$(PYTHON) config/security/apply.py
	$(PYTHON) config/security/apply.py
	$(PYTHON) tests/security/acceptance.py
