# AUTO-510: NetBox as a rebuildable projection of Git intent (ADR 0019).
# NetBox runs as the netlab-netbox compose project on 127.0.0.1:18080.

NETBOX_COMPOSE := docker compose -f services/netbox/compose.yaml

.PHONY: netbox-prepare netbox-up netbox-down netbox-reset netbox-sync netbox-check test-netbox-sync

netbox-prepare:
	$(PYTHON) automation/netbox/prepare.py

netbox-up: netbox-prepare
	set -a; source versions.env; set +a; $(NETBOX_COMPOSE) up -d
	$(PYTHON) automation/netbox/wait_ready.py

netbox-down:
	set -a; source versions.env; set +a; $(NETBOX_COMPOSE) down

# Destroys the NetBox database volumes; the projection is rebuilt from Git.
netbox-reset:
	set -a; source versions.env; set +a; $(NETBOX_COMPOSE) down --volumes

netbox-sync:
	$(PYTHON) automation/netbox/sync.py

netbox-check:
	$(PYTHON) automation/netbox/sync.py --check

test-netbox-sync:
	$(PYTHON) -m pytest -q -p no:cacheprovider --import-mode=importlib tests/integration/netbox
	$(PYTHON) tests/integration/netbox/acceptance.py
	$(PYTHON) tests/integration/netbox/inventory_parity.py
