.PHONY: preflight
.PHONY: preflight-self-check

preflight:
	@bash scripts/preflight/check.sh

preflight-self-check:
	@bash scripts/preflight/self-check-modinfo.sh
