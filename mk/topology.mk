# TOP-040 Phase 1 topology and project-scoped lifecycle commands.
TOPOLOGY_FILE ?= lab/phase-1.clab.yml
TOPOLOGY_RENDERER ?= scripts/lifecycle/render_topology.py
TOPOLOGY_SMOKE ?= tests/smoke/topology/check_topology.py
TOPOLOGY_PYTHON ?= $(PYTHON)

.PHONY: topology topology-check lab-up lab-inspect lab-down smoke verify-clean

topology:
	$(TOPOLOGY_PYTHON) $(TOPOLOGY_RENDERER) --output $(TOPOLOGY_FILE)

topology-check: topology
	$(TOPOLOGY_PYTHON) $(TOPOLOGY_RENDERER) --output $(TOPOLOGY_FILE) --check

lab-up: topology
	@TOPOLOGY=$(TOPOLOGY_FILE) bash scripts/lifecycle/lab-up.sh
	@bash automation/roles/bgp/apply.sh

lab-inspect:
	@TOPOLOGY=$(TOPOLOGY_FILE) bash scripts/lifecycle/lab-inspect.sh

lab-down:
	@TOPOLOGY=$(TOPOLOGY_FILE) bash scripts/lifecycle/lab-down.sh

smoke: topology
	$(TOPOLOGY_PYTHON) $(TOPOLOGY_SMOKE)

verify-clean:
	@bash scripts/lifecycle/verify-clean.sh
