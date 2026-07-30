# WLED_dev — run the host unit tests across all submodules from one place.
#
#   make            # or `make test` — all pure-logic host tests (WLED + esp-now-router)
#   make test-wled  # just the WLED ampworks sensor-sync tests
#   make test-bridge# just the WLED rs485_bridge wire-format/decision tests
#   make test-libs  # just the ArduinoLibs RS485 receive-path tests
#   make test-router# just the esp-now-router relay + leader-election tests
#   make test-ui    # WLED web-UI builder test (needs Node)
#   make test-all   # everything, including the UI test
#
# These are host-compiled (no device, no flashing). Run after `./setup.sh` so the submodules are
# checked out.

CXX      ?= c++
CXXFLAGS ?= -std=c++11 -Wall -Wextra

.PHONY: test test-wled test-bridge test-libs test-router test-ui test-all clean

test: test-wled test-bridge test-libs test-router
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

# WLED (rs485_bridge): the HMTL wire-format + receive-decision tests. Run BOTH natively and under
# -fpack-struct=1: the second is the AVR-like ABI, and the point of most of those assertions is that
# the two agree. Previously these had to be run by hand, so `make test` reported success without ever
# touching them.
test-bridge:
	@echo "== WLED rs485_bridge host tests =="
	$(call require_submodule,WLED)
	$(call require_submodule,HMTL)
	$(call require_submodule,ArduinoLibs)
	@if [ ! -f WLED/usermods/rs485_bridge/tests/rs485_bridge_test.cpp ]; then \
	  echo "-- skipped: no rs485_bridge tests at the pinned WLED revision --"; \
	else \
	  echo "-- rs485_bridge_test (native ABI) --"; \
	  $(CXX) $(CXXFLAGS) -I HMTL/Libraries/HMTLprotocol -I ArduinoLibs/Socket \
	    -o /tmp/wled_rs485_bridge_test WLED/usermods/rs485_bridge/tests/rs485_bridge_test.cpp \
	    && /tmp/wled_rs485_bridge_test || exit 1; \
	  echo "-- rs485_bridge_test (-fpack-struct=1, AVR-like ABI) --"; \
	  $(CXX) $(CXXFLAGS) -fpack-struct=1 -I HMTL/Libraries/HMTLprotocol -I ArduinoLibs/Socket \
	    -o /tmp/wled_rs485_bridge_test_avr WLED/usermods/rs485_bridge/tests/rs485_bridge_test.cpp \
	    && /tmp/wled_rs485_bridge_test_avr || exit 1; \
	fi

# ArduinoLibs: the RS485 receive-path state-machine tests (stale-packet re-delivery, packet timeout,
# socket-layer length validation, allocation failure). Delegates to that repo's own test Makefile,
# which owns the Arduino.h shim and the -DRS485_HARDWARE_SERIAL that the library requires.
test-libs:
	@echo "== ArduinoLibs host tests =="
	$(call require_submodule,ArduinoLibs)
	@if [ ! -f ArduinoLibs/test/Makefile ]; then \
	  echo "-- skipped: no test/ at the pinned ArduinoLibs revision --"; \
	else \
	  $(MAKE) --no-print-directory -C ArduinoLibs/test test; \
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
	@$(MAKE) --no-print-directory -C ArduinoLibs/test clean 2>/dev/null || true
	@rm -f /tmp/wled_sensor_sync_test /tmp/wled_sensor_sync_ring_test
	@rm -f /tmp/wled_rs485_bridge_test /tmp/wled_rs485_bridge_test_avr
