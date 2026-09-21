# DAT-030: authoritative inventory checks. The phase Makefile includes this
# fragment after ENV-010 provides the repository Python environment.

UV ?= uv
PYTHON ?= $(UV) run --locked python
INVENTORY_VALIDATOR ?= scripts/validate/validate_inventory.py
INVENTORY_FILE ?= inventory/inventory.yaml
INVENTORY_SCHEMA ?= schemas/inventory.schema.json

.PHONY: validate-inventory test-unit

validate-inventory:
	$(PYTHON) $(INVENTORY_VALIDATOR) --inventory $(INVENTORY_FILE) --schema $(INVENTORY_SCHEMA)

test-unit:
	$(PYTHON) -m pytest tests/unit
