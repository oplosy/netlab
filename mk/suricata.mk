# EDGE-420: inline Suricata IPS on hq-fw-1 (ADR 0018). The live acceptance
# needs the running project lab with the firewall policy applied; it stops
# Suricata briefly to prove fail-closed behavior and restores it via the
# security apply.

.PHONY: test-suricata

test-suricata:
	$(PYTHON) -m pytest -q -p no:cacheprovider --import-mode=importlib tests/security/suricata
	$(PYTHON) tests/security/suricata/acceptance.py
