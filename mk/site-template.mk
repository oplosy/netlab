.PHONY: test-site-template

test-site-template:
	$(PYTHON) -m pytest -q -p no:cacheprovider tests/unit/site-template

.PHONY: test-branch-2

test-branch-2:
	$(PYTHON) -m pytest -q -p no:cacheprovider tests/integration/br2
