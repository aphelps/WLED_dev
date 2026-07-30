# WLED_dev — run the host unit tests across all submodules from one place.
#
#   make            # or `make test` — all pure-logic host tests (WLED + esp-now-router)
#   make test-wled  # just the WLED ampworks sensor-sync tests
#   make test-router# just the esp-now-router relay + leader-election tests
#   make test-ui    # WLED web-UI builder test (needs Node)
#   make test-all   # everything, including the UI test
#
# These are host-compiled (no device, no flashing). Run after `./setup.sh` so the submodules are
# checked out. ArduinoLibs (MPR121/Debug) has no host tests of its own.

CXX      ?= c++
CXXFLAGS ?= -std=c++11 -Wall -Wextra

.PHONY: test test-wled test-router test-ui test-all clean

test: test-wled test-router
	@echo ""
	@echo "OK — all submodule host tests passed."

# Guard: a submodule that was never initialized is a SETUP problem — fail loudly with the fix.
# A submodule that is checked out but carries no tests at its pinned revision is not an error:
# skip it with a note so `make test` stays usable while a submodule's tests are still in flight.
define require_submodule
	@if [ ! -e "$(1)/.git" ] && [ -z "$$(ls -A $(1) 2>/dev/null)" ]; then \
	  echo "ERROR: submodule '$(1)' is not initialized — run ./setup.sh (or: git submodule update --init --recursive)"; \
	  exit 1; \
	fi
endef

# WLED (ampworks): the SensorSync dispatch + SPSC ring host tests. Compiled from their own dir so
# the relative "../sensor_sync_*.h" includes resolve.
test-wled:
	@echo "== WLED ampworks host tests =="
	$(call require_submodule,WLED)
	@if [ ! -d WLED/usermods/ampworks/tests ]; then \
	  echo "-- skipped: no tests/ at the pinned WLED revision --"; \
	else \
	  cd WLED/usermods/ampworks/tests && for t in sensor_sync_test sensor_sync_ring_test; do \
	    echo "-- $$t --"; \
	    $(CXX) $(CXXFLAGS) -o /tmp/wled_$$t $$t.cpp && /tmp/wled_$$t || exit 1; \
	  done; \
	fi

# esp-now-router: the relay + leader-election host tests (delegates to that repo's own Makefile,
# which knows the -I paths into the WLED submodule). `pio test -e native` is the idiomatic
# alternative from inside the esp-now-router dir.
test-router:
	@echo "== esp-now-router host tests =="
	$(call require_submodule,esp-now-router)
	@if [ ! -f esp-now-router/tests/Makefile ]; then \
	  echo "-- skipped: no tests/ at the pinned esp-now-router revision --"; \
	else \
	  $(MAKE) --no-print-directory -C esp-now-router/tests test; \
	fi

# WLED web-UI builder test (Node/npm).
test-ui:
	@echo "== WLED web-UI builder test =="
	@cd WLED && npm test

test-all: test test-ui

clean:
	@$(MAKE) --no-print-directory -C esp-now-router/tests clean 2>/dev/null || true
	@rm -f /tmp/wled_sensor_sync_test /tmp/wled_sensor_sync_ring_test
