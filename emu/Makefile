# LM-1 Emulator — Makefile
#
# make test              Run all tests (background), print results when done
# make test-batch B=phase1  Run one batch
# make test-status       Peek at the running/last test run
# make test-kill         Kill a hanging test run
# make test-list         Show available batches and tests
# make test-results      Show results of the last completed run
# make build             Build the C++ accelerator extension
# make clean             Clean build artifacts

SHELL   := /bin/bash
PYTHON  ?= python3
EMU_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
RUNNER  := $(PYTHON) -m lm1.testing.runner --test-dir $(EMU_DIR)tests

# ── Tests ─────────────────────────────────────────────────────────────────

.PHONY: test test-batch test-status test-kill test-list test-results

## Run full test suite in a background process; tail the results.
test:
	@echo "=== LM-1 test run (all batches) ==="
	@cd $(EMU_DIR) && $(RUNNER) run & \
		TEST_PID=$$!; \
		wait $$TEST_PID; \
		EXIT=$$?; \
		exit $$EXIT

## Run a single batch.  Usage: make test-batch B=phase1
test-batch:
ifndef B
	@echo "Usage: make test-batch B=<batch-name>"
	@echo "Available batches:"; cd $(EMU_DIR) && $(RUNNER) list
	@exit 1
endif
	@echo "=== LM-1 test run (batch: $(B)) ==="
	@cd $(EMU_DIR) && $(RUNNER) run --batch $(B) & \
		TEST_PID=$$!; \
		wait $$TEST_PID; \
		EXIT=$$?; \
		exit $$EXIT

## Peek at a running (or last completed) test run.
test-status:
	@cd $(EMU_DIR) && $(RUNNER) status

## Kill a hanging test run.
test-kill:
	@cd $(EMU_DIR) && $(RUNNER) kill

## List available test batches and individual tests.
test-list:
	@cd $(EMU_DIR) && $(RUNNER) list

## Show results of the last completed test run.
test-results:
	@cd $(EMU_DIR) && $(RUNNER) status

# ── Build ─────────────────────────────────────────────────────────────────

.PHONY: build clean install

## Build the C++ accelerator extension in-place.
build:
	@cd $(EMU_DIR) && $(PYTHON) setup.py build_ext --inplace

## Editable install (includes C++ extension build).
install:
	@cd $(EMU_DIR) && pip install -e .

## Clean build artifacts.
clean:
	@cd $(EMU_DIR) && rm -rf build/ dist/ *.egg-info lm1/*.so lm1/_accel_ext*.so
	@find $(EMU_DIR) -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
	@find $(EMU_DIR) -name '*.pyc' -delete 2>/dev/null || true
