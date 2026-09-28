# EDGE-410: HQ SecureEdge firewall tier (ADR 0017). The live acceptance needs
# the running project lab with gateway, OSPF, IPsec, security, and services
# applied; it pauses hq-fw-1 briefly to prove fail-closed behavior.

.PHONY: test-secure-edge

test-secure-edge:
	$(PYTHON) -m pytest -q -p no:cacheprovider --import-mode=importlib tests/security/secure_edge
	$(PYTHON) tests/security/secure_edge/acceptance.py
