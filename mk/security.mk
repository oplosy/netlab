# SEC-170 policy validation and live acceptance.
SEC_RUNTIME_PYTHON ?= python3
.PHONY: test-security

test-security:
	$(PYTHON) -m pytest -q -p no:cacheprovider tests/security/test_policy.py
	$(PYTHON) scripts/validate/validate_inventory.py
	$(SEC_RUNTIME_PYTHON) config/security/apply.py
	$(SEC_RUNTIME_PYTHON) config/security/apply.py
	$(SEC_RUNTIME_PYTHON) tests/security/acceptance.py
