.PHONY: evidence-phase-1 evidence-phase-2 evidence-phase-3

# Full Phase 1 acceptance; the runner tears down only the project lab and
# records a timestamped bundle under the ignored artifacts/runs directory.
evidence-phase-1:
	python3 scripts/evidence/phase1.py

# Measures provider, edge, and encrypted tunnel failover in the existing live lab.
evidence-phase-2:
	$(PYTHON) scripts/evidence/phase2.py

# Reconfigure the project lab and prove Branch 2 routing, isolation, and regression acceptance.
evidence-phase-3:
	$(PYTHON) scripts/evidence/phase3.py
