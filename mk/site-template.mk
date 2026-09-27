.PHONY: test-site-template

test-site-template:
	$(PYTHON) -m pytest -q -p no:cacheprovider tests/unit/site-template
