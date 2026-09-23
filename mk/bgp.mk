.PHONY: test-bgp

test-bgp:
	NETLAB_BGP_LIVE=1 $(PYTHON) -m pytest -q -p no:cacheprovider tests/integration/bgp/test_policy.py
