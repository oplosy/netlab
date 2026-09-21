# IMG-020 reproducible image targets. The root Makefile auto-includes mk/*.mk.
IMAGE_BUILD_SCRIPT ?= scripts/images/build.sh
IMAGE_VERIFY_SCRIPT ?= scripts/images/verify.sh
IMAGE_REPORT_SCRIPT ?= scripts/images/report.sh

.PHONY: images verify-images image-report

images:
	@bash $(IMAGE_BUILD_SCRIPT)

verify-images:
	@bash $(IMAGE_VERIFY_SCRIPT)

image-report:
	@bash $(IMAGE_REPORT_SCRIPT)
