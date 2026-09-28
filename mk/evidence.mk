.PHONY: evidence-phase-1 evidence-phase-2 evidence-phase-3 evidence-phase-4

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

# EDGE-430: correlate firewall decisions, IDS events, packets, and IPFIX by run
# ID in the existing live lab (phase-3 applied); starts observability if needed.
evidence-phase-4:
	$(PYTHON) -m pytest -q -p no:cacheprovider --import-mode=importlib tests/unit/evidence
	$(PYTHON) scripts/evidence/phase4.py
