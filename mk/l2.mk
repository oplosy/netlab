.PHONY: test-l2 test-l2-peer

# Requires an active Phase 1 lab created by make lab-up.
test-l2:
	$(PYTHON) -m pytest -q -p no:cacheprovider tests/integration/l2/test_switching_plan.py
	$(PYTHON) tests/integration/l2/measure.py --site all

# Requires make lab-up; checks both directions on all four site VLANs.
test-l2-peer:
	$(PYTHON) tests/integration/l2/peer_adjacency.py --site all
