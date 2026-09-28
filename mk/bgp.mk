.PHONY: test-bgp test-bgp-multihoming

test-bgp:
	NETLAB_BGP_LIVE=1 $(PYTHON) -m pytest -q -p no:cacheprovider tests/integration/bgp/test_policy.py

test-bgp-multihoming:
	NETLAB_BGP_LIVE=1 NETLAB_BGP_MULTIHOMING_LIVE=1 $(PYTHON) -m pytest -q -p no:cacheprovider tests/integration/bgp
