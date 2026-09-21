# Linux-first command surface. Task fragments are optional so a partial phase
# checkout remains parseable while tasks are integrated independently.
SHELL := /usr/bin/env bash

MK_FRAGMENTS := $(wildcard mk/*.mk)
-include $(MK_FRAGMENTS)

.DEFAULT_GOAL := help
.PHONY: help

help:
	@printf '%s\n' 'netlab targets:'
	@printf '%s\n' '  make preflight  Read-only WSL2/toolchain/resource checks'
