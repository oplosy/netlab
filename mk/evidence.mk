.PHONY: evidence-phase-1

# Full Phase 1 acceptance; the runner tears down only the project lab and
# records a timestamped bundle under the ignored artifacts/runs directory.
evidence-phase-1:
	python3 scripts/evidence/phase1.py
