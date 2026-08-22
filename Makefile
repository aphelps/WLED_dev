# WLED_dev — run the host unit tests across all submodules from one place.
#
#   make            # or `make test` — all pure-logic host tests (WLED + esp-now-router)
#   make test-wled  # just the WLED ampworks sensor-sync tests
#   make test-bridge# just the WLED rs485_bridge wire-format/decision tests
#   make test-libs  # just the ArduinoLibs RS485 receive-path tests
#   make test-hmtl  # just the HMTL cross-ABI wire-layout sweep + its negative control
#   make test-router# just the esp-now-router relay + leader-election tests
#   make test-apjoin# just the scripts/wled_apjoin.py host tests (see scripts/README.md)
#   make test-readme# verify README.md's claims against the source files
#   make test-ui    # WLED web-UI builder test (needs Node)
#   make test-all   # everything, including the UI test
#
# These are host-compiled (no device, no flashing). Run after `./setup.sh` so the submodules are
# checked out.

CXX      ?= c++
CXXFLAGS ?= -std=c++11 -Wall -Wextra

.PHONY: test test-wled test-bridge test-libs test-hmtl test-router test-readme test-ui test-all clean test-sync test-apjoin

test: test-wled test-bridge test-libs test-hmtl test-router test-readme test-sync test-apjoin
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

# How a missing suite is reported. Unset (the default, for humans): a note, exit 0 — see the comment
# above. STRICT=1 (what CI passes): a failure. In CI the lenient default is actively harmful — a
# skipped suite reports the same green as a passing one, so a submodule pointer landing on a
# revision without tests silently drops that coverage. That is not hypothetical: main briefly
# pinned esp-now-router at its README-only commit and the router suites vanished from `make test`
# without any signal.
#
# Lenient only when STRICT is unset/empty or an explicit off-switch; ANY other value is strict.
# Deliberately asymmetric: a typo'd STRICT should fail loudly rather than silently drop coverage,
# which is the whole failure mode this exists to prevent. (`ifdef` was wrong here — it tests
# definedness, so `STRICT=0` would have been strict.)
#
# $(1) must not contain a comma: `$(call)` splits on commas and would truncate the message.
# The off-list carries the usual capitalisations because make's filter-out is case-sensitive: without
# them `STRICT=OFF` would be strict and report "ERROR: … (STRICT=OFF — a skipped suite is a failure)",
# which reads as a contradiction to whoever just typed OFF.
STRICT ?=
REQUIRE_TOOLCHAINS ?=
# STRICT implies "at least one real embedded toolchain must have run" — see test-hmtl below.
ifeq ($(filter-out 0 no No NO false False FALSE off Off OFF,$(strip $(STRICT))),)
require_any_real :=
else
require_any_real := 1
endif
ifeq ($(filter-out 0 no No NO false False FALSE off Off OFF,$(strip $(STRICT))),)
missing_tests = echo "-- skipped: $(1) --"
else
missing_tests = echo "ERROR: $(1) (STRICT=$(STRICT) — a skipped suite is a failure in CI)"; exit 1
endif

# WLED (ampworks): the SensorSync dispatch + SPSC ring host tests. Compiled from their own dir so
# the relative "../sensor_sync_*.h" includes resolve.
test-wled:
	@echo "== WLED ampworks host tests =="
	$(call require_submodule,WLED)
	@if [ ! -d WLED/usermods/ampworks/tests ]; then \
	  $(call missing_tests,no tests/ at the pinned WLED revision); \
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
	  $(call missing_tests,no rs485_bridge tests at the pinned WLED revision); \
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
	  $(call missing_tests,no test/ at the pinned ArduinoLibs revision); \
	else \
	  $(MAKE) --no-print-directory -C ArduinoLibs/test test; \
	fi

# HMTL: the cross-ABI layout sweep over the wire headers, plus the negative control that proves
# the asserts can fail. Delegates to HMTL/tests/layout/, which owns the toolchain discovery.
#
# This is here because nothing else reaches those asserts from CI. HMTL has its own `make test`,
# but this repo has never invoked it, and HMTL_Module's firmware envs resolve their libraries from
# a machine-local Arduino directory rather than from the HMTL checkout — verified, not assumed: a
# deliberately impossible static_assert in HMTL/Libraries/HMTLMessaging/HMTLPrograms.h leaves
# `make -C HMTL test-simavr` green.
#
# Without REQUIRE_TOOLCHAINS the sweep runs whatever compilers exist and prints a COVERAGE line
# naming what it skipped, so a host-only run cannot be read as a full one. On a runner with
# avr-g++ and xtensa-esp32-elf-g++ installed, pass REQUIRE_TOOLCHAINS=1 to demand all four ABIs.
#
# STRICT=1 additionally sets REQUIRE_ANY_REAL, which is the weaker guarantee that matters most: it
# does not demand a specific toolchain, only that the sweep touched at least ONE real embedded ABI.
# Without it, `make test STRICT=1` on a runner with neither toolchain would compile the host pair,
# report success, and have cross-checked no embedded ABI at all — the same "a skipped suite reports
# green" failure the STRICT machinery above exists to prevent. Today's CI runner has xtensa (the
# espressif32 platform install brings it), so this passes; only avr-g++ is missing.
# The sweep covers static_asserts — compile-time LAYOUT. It says nothing about the code that READS
# those structs, and HMTL's behavioural suite (platformio/HMTL_Test, `pio test -e native`) ran under
# no automation at all: HMTL has no .github/workflows, and this target reached only tests/layout/.
# So HMTL#9's program_color() clamp — the narrowing, the zero-length rule, the stale-invocation
# rejection — landed with tests that nothing invoked.
#
# Delegated to HMTL's own `make test-native` rather than spelled out here, so the env list stays in
# the repo that owns it. Which is why this comment does NOT promise which envs run: HMTL#9 makes it
# both pixel widths (an `#ifdef BIG_PIXELS` branch in a test body is dead source unless something
# compiles the flag), but an older HMTL pin runs whatever IT lists, and a promise made here would
# quietly become false. HMTL's test-native prints what it actually ran.
#
# PIO defaults to the venv setup.sh builds and falls back to PATH; if neither resolves this is a
# skipped suite like any other, which STRICT=1 turns into a failure.
#
# HMTL's `test-python` is deliberately NOT wired in: it needs pytest, which is not in
# WLED/requirements.txt, so adding it here would fail on a fresh CI venv. Adding pytest to the
# pinned toolchain is a separate decision about this repo's dependency set — filed, not smuggled in.
PIO ?= ./.venv/bin/pio

test-hmtl:
	@echo "== HMTL cross-ABI wire layout sweep =="
	$(call require_submodule,HMTL)
	@if [ ! -f HMTL/tests/layout/Makefile ]; then \
	  $(call missing_tests,no tests/layout/ at the pinned HMTL revision); \
	else \
	  $(MAKE) --no-print-directory -C HMTL/tests/layout \
	    REQUIRE_TOOLCHAINS=$(REQUIRE_TOOLCHAINS) REQUIRE_ANY_REAL=$(require_any_real) && \
	  $(MAKE) --no-print-directory -C HMTL/tests/layout packed-access && \
	  $(MAKE) --no-print-directory -C HMTL/tests/layout negative || exit 1; \
	fi
	@# No "(both pixel widths)" here: the env list lives in HMTL's own Makefile, so an older HMTL
	@# pin can run one env while this banner claims two. HMTL's test-native prints what it ran.
	@echo "== HMTL native behaviour tests =="
	@pio_bin=""; \
	if [ -x "$(PIO)" ]; then pio_bin="$(abspath $(PIO))"; \
	else pio_bin="$$(command -v $(notdir $(PIO)) 2>/dev/null || true)"; fi; \
	if [ ! -f HMTL/platformio/HMTL_Test/platformio.ini ]; then \
	  $(call missing_tests,no HMTL_Test/ at the pinned HMTL revision); \
	elif [ -z "$$pio_bin" ]; then \
	  $(call missing_tests,pio not found at $(PIO) nor on PATH — run ./setup.sh); \
	else \
	  $(MAKE) --no-print-directory -C HMTL test-native PIO="$$pio_bin" || exit 1; \
	fi

# esp-now-router: the relay + leader-election host tests (delegates to that repo's own Makefile,
# which knows the -I paths into the WLED submodule). `pio test -e native` is the idiomatic
# alternative from inside the esp-now-router dir.
test-router:
	@echo "== esp-now-router host tests =="
	$(call require_submodule,esp-now-router)
	@if [ ! -f esp-now-router/tests/Makefile ]; then \
	  $(call missing_tests,no tests/ at the pinned esp-now-router revision); \
	else \
	  $(MAKE) --no-print-directory -C esp-now-router/tests test; \
	fi

# Fleet sync tool (top-level, not a submodule): name resolution, request-body construction and a
# cross-version check against local stub servers. Pure Python, no hardware, no submodule needed.
test-sync:
	@echo "== wled_sync host tests =="
	@if [ ! -f tests/test_wled_sync.py ]; then \
	  $(call missing_tests,no tests/test_wled_sync.py); \
	else \
	  python3 tests/test_wled_sync.py; \
	fi

# AP auto-join tool: candidate selection, identity gating and the safety properties (never push to
# an unidentified AP; always forget the AP we joined). Pure Python against a fake platform — no
# radio is touched.
test-apjoin:
	@echo "== wled_apjoin host tests =="
	@if [ ! -f tests/test_wled_apjoin.py ]; then \
	  $(call missing_tests,no tests/test_wled_apjoin.py); \
	else \
	  python3 tests/test_wled_apjoin.py; \
	fi
# README claim-checker. The README is what a remote collaborator sets up from with nobody to ask,
# so its claims are verified against platformio.ini / Makefile / .gitmodules rather than trusted.
test-readme:
	@echo "== README claim checks =="
	$(call require_submodule,WLED)
	$(call require_submodule,esp-now-router)
	@python3 tools/check_readme.py

# WLED web-UI builder test (Node/npm).
test-ui:
	@echo "== WLED web-UI builder test =="
	@cd WLED && npm test

test-all: test test-ui

clean:
	@$(MAKE) --no-print-directory -C esp-now-router/tests clean 2>/dev/null || true
	@$(MAKE) --no-print-directory -C ArduinoLibs/test clean 2>/dev/null || true
	@$(MAKE) --no-print-directory -C HMTL/tests/layout clean 2>/dev/null || true
	@rm -f /tmp/wled_sensor_sync_test /tmp/wled_sensor_sync_ring_test
	@rm -f /tmp/wled_rs485_bridge_test /tmp/wled_rs485_bridge_test_avr
