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
	tests/security/test_policy.py \
	tests/security/secure_edge \
	tests/security/suricata \
	tests/integration/netbox/test_projection.py

.PHONY: ci-static ci-lint ci-test ci-smoke

# AUTO-540 adds the render/inventory freshness check and Ansible checks; drift
# logic is covered offline by tests/unit/automation.
ci-static: validate-inventory ci-lint ci-test
	$(PYTHON) $(TOPOLOGY_RENDERER) --output $(TOPOLOGY_FILE) --check
	$(PYTHON) automation/render/render_all.py --check

ci-lint:
	$(UV) run --locked ruff check .
	$(UV) run --locked yamllint --strict .
	$(UV) run --locked ansible-playbook -i automation/inventory/hosts.yml automation/playbooks/change.yml --syntax-check
	$(UV) run --locked ansible-lint --offline automation/playbooks

ci-test:
	$(PYTHON) -m pytest -q -p no:cacheprovider --import-mode=importlib $(CI_PYTEST_TARGETS)

# AUTO-540 bounded topology smoke test (needs Docker, containerlab, and the
# network-node image; SMOKE_TIMEOUT bounds the run, default 300 s).
ci-smoke:
	bash scripts/ci/topology-smoke.sh
