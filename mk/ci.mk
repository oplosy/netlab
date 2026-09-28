# CI-050: static quality gate. Runs without Docker, Containerlab, or a lab
# runtime; live tests stay skipped because their NETLAB_*_LIVE flags are unset.

CI_PYTEST_TARGETS ?= \
	tests/unit \
	tests/integration/l2/test_switching_plan.py \
	tests/integration/l3/test_gateway_plan.py \
	tests/integration/ospf/test_plan.py \
	tests/integration/services/test_plan.py \
	tests/integration/observability/test_plan.py \
	tests/integration/ipsec/test_plan.py \
	tests/integration/bgp \
	tests/integration/br2 \
	tests/security/test_policy.py

.PHONY: ci-static ci-lint ci-test

ci-static: validate-inventory ci-lint ci-test
	$(PYTHON) $(TOPOLOGY_RENDERER) --output $(TOPOLOGY_FILE) --check

ci-lint:
	$(UV) run --locked ruff check .
	$(UV) run --locked yamllint --strict .

ci-test:
	$(PYTHON) -m pytest -q -p no:cacheprovider --import-mode=importlib $(CI_PYTEST_TARGETS)
