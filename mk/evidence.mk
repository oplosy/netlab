.PHONY: evidence-phase-1 evidence-phase-2

# Full Phase 1 acceptance; the runner tears down only the project lab and
# records a timestamped bundle under the ignored artifacts/runs directory.
evidence-phase-1:
	python3 scripts/evidence/phase1.py

# Measures provider, edge, and encrypted tunnel failover in the existing live lab.
evidence-phase-2:
	$(PYTHON) scripts/evidence/phase2.py
