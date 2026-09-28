# AUTO-520 / AUTO-530: generated inventory, deterministic render, and the
# Ansible-orchestrated safe change workflow (ADR 0019).

ANSIBLE_PLAYBOOK ?= $(UV) run --locked ansible-playbook
CHANGE_TAGS ?= all

.PHONY: render test-render change test-automation test-drift

render:
	$(PYTHON) automation/render/render_all.py

test-render:
	$(PYTHON) -m pytest -q -p no:cacheprovider --import-mode=importlib tests/unit/render
	$(PYTHON) automation/render/render_all.py --check

# Precheck, backup, diff, apply, and postcheck against the running lab.
change:
	$(ANSIBLE_PLAYBOOK) -i automation/inventory/hosts.yml automation/playbooks/change.yml --tags $(CHANGE_TAGS)

# Runs the change twice (the second must be a no-op) and exercises device and
# intent drift; needs the running lab.
test-automation:
	$(PYTHON) -m pytest -q -p no:cacheprovider --import-mode=importlib tests/unit/automation
	$(PYTHON) tests/integration/automation/acceptance.py

# Report-only drift check of the live lab against the last golden snapshot.
test-drift:
	$(PYTHON) automation/compliance/snapshot.py drift --golden artifacts/state/golden.json
