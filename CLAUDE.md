# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

WLED is an ESP8266/ESP32 firmware for controlling addressable LEDs (WS2812B, APA102, etc.) with 100+ effects, a web UI, and support for many control protocols (MQTT, E1.31, Art-Net, Alexa, HTTP JSON API, WebSockets, UDP, etc.). Version 0.15.3.

The main firmware source lives in `WLED/wled00/`. The `usermods/ampworks/` directory contains a custom usermod extending WLED.

## Build System

Uses [PlatformIO](https://platformio.org/). Configuration in `WLED/platformio.ini`.

### Quick start (fresh clone)

```bash
git clone <this-repo> && cd WLED_dev
./setup.sh                 # submodules + PlatformIO venv + verify build
source .venv/bin/activate  # put pio on PATH (or call ./.venv/bin/pio)
cd WLED && pio run -e ampworks
```

`setup.sh` initializes the submodules (WLED firmware + the `ArduinoLibs` submodule
providing MPR121/Debug/RS485Utils + the `HMTL` submodule providing the HMTL wire format —
vendored so there are **no machine-specific paths**), installs
PlatformIO into an isolated `.venv`, and runs a smoke build (`--no-verify` to skip).
Prerequisites it checks for: `git`, `python3`+venv/pip, and **Node.js/npm** (required by the
web-UI pre-build; it does not auto-install Node). The custom envs reference the sibling repos via
`${PROJECT_DIR}/../ArduinoLibs` and `${PROJECT_DIR}/../HMTL/Libraries`, so the build is
self-contained after `git submodule update --init`.

### Submodules

| Path | Repo | Role |
|------|------|------|
| `WLED/` | `aphelps/WLED` | The firmware itself (fork). Custom work branches from `main`, which is the WLED 16.0.1 line (it was `base-wled-16.0.1` until 2026-07-30; the pre-v16 history is kept on `main-pre-v16`). |
| `ArduinoLibs/` | `aphelps/ArduinoLibs` | Shared Arduino libraries — MPR121, Debug, `RS485Utils`/`Socket` (RS485 bus), and the vendored `RS485_non_blocking` (Nick Gammon, MIT). On the firmware build path via `lib_extra_dirs`. |
| `HMTL/` | `aphelps/HMTL` | Legacy HMTL module firmware + Python tooling, **and the source of truth for the HMTL wire format**. `HMTL/Libraries` is on the firmware build path via `lib_extra_dirs` (`[env:ampworks]`), but only `HMTLprotocol` is ever compiled in: `rs485_bridge` imports `HMTLprotocol/HMTLWireFormat.h` — a dependency-free header (`<stdint.h>` + `Socket.h`, never `Arduino.h`) holding `msg_hdr_t`, the `MSG_TYPE_*`/`MSG_FLAG_*` codes, the `msg_*` payload structs, `output_hdr_t`, `config_hdr_t` and the `HMTL_OUTPUT_*`/`HMTL_PROGRAM_*` codes. Nothing includes `HMTLTypes.h` or `HMTLMessaging.h`: their sources pull FastLED/`PixelUtil`, `EEPromUtils`, `MPR121` and `XBeeSocket`, which WLED 16 cannot build. There is no second copy of the wire format to keep in sync. |
| `esp-now-router/` | `aphelps/esp-now-router` | ESP-NOW multi-hop relay + leader election (added on `main` 2026-07-29, `7916e67`). Host tests run via the top-level `make test-router`; see `esp-now-router/tests/`. Not on the firmware build path. |

### Manual steps (what setup.sh automates)

```bash
# Install Python dependencies
pip install -r WLED/requirements.txt

# Install Node.js dependencies (for web UI building)
cd WLED && npm install

# Build web UI (runs before firmware compile automatically)
npm run build

# Compile firmware - default env is esp32dev (ampworks is super-repo-only; build it explicitly)
cd WLED && pio run

# Compile a specific environment
pio run -e ampworks
pio run -e esp32dev

# Flash to device
pio run -e ampworks --target upload

# Monitor serial output
pio device monitor -b 115200
```

The `ampworks` environment extends `esp32dev` with `-D USERMOD_AMPWORKS -D USERMOD_MPR121 -D USERMOD_SENSOR_SYNC -D USERMOD_RS485_BRIDGE -D RS485_HARDWARE_SERIAL=2`. Upload uses HTTP (not espota — macOS Sequoia blocks UDP):

```bash
# Flash to default device (192.168.1.55)
pio run -e ampworks -t upload

# Flash to a different device
WLED_IP=192.168.1.99 pio run -e ampworks -t upload

# Verify running build (shows git / branch / built fields)
curl -s http://192.168.1.55/json/info | python3 -m json.tool
```

For custom build flags, create `WLED/platformio_override.ini` (see `platformio_override.sample.ini`).

### Skills

Project skills live in `skills/` (see `skills/README.md`). Run `skills/install.sh` to symlink them
into `.claude/skills/` (Claude Code) and `.agents/skills/` (Codex). Those two directories are
gitignored and per-checkout, so the links do not travel with the repo.

Run it:
- after cloning,
- after adding or renaming a skill,
- **after any pull/submodule refresh that brings in new or renamed skills** — they will not load
  until they are linked, and a renamed one leaves a dangling link behind. The script is idempotent
  and repairs broken or stale links, so re-running it is always safe (`--dry-run` to preview).

## Running Tests

A top-level `Makefile` runs the host unit tests across all submodules (pure logic — no device):

```bash
make                # all host tests (WLED ampworks + esp-now-router)
make test-wled      # just the WLED SensorSync dispatch + SPSC ring tests
make test-router    # just the esp-now-router relay + leader-election tests
make test-ui        # WLED web-UI builder test (needs Node)
make test-all       # everything, incl. the UI test
```

Host C++ tests live in `WLED/usermods/ampworks/tests/` (SensorSync) and `esp-now-router/tests/`
(relay + leader election). The router tests also run idiomatically via `pio test -e native` from
inside `esp-now-router/`, and `make -C esp-now-router/tests coverage` gives gcov line coverage.

## Architecture

### Entry Point & Main Loop

`wled00/wled_main.cpp` → `setup()` and `loop()` → delegates to the `WLED` singleton class.

`setup()` sequence: `initConnection()` (WiFi/AP) → `initInterfaces()` (HTTP, mDNS, OTA) → `beginStrip()` (LED drivers) → `UsermodManager::setup()`

`loop()` sequence: time → IR → connection → serial (Adalight/TPM2) → UDP notifications → transitions → usermod loop → buttons/sensors → Alexa → LED strip update → yield

### Key Files

| File | Role |
|------|------|
| `wled.h` | WLED singleton class definition, all global variables, feature flag `#define`s |
| `wled.cpp` | Connection management, setup/loop implementation |
| `FX.cpp` / `FX.h` | All 100+ LED effects; `Segment` class |
| `bus_manager.cpp` / `bus_wrapper.h` | LED output hardware abstraction (multi-strip support) |
| `json.cpp` | JSON API serialization/deserialization (`/json` endpoints) |
| `wled_server.cpp` | HTTP and WebSocket request handlers |
| `cfg.cpp` | Config file I/O (LittleFS JSON) |
| `set.cpp` | Runtime settings processing |
| `udp.cpp` | UDP realtime, E1.31/Art-Net/DDP/TPM2.net handlers |
| `const.h` | Compile-time constants (max buses, effect counts, etc.) |
| `fcn_declare.h` | All global function forward declarations |
| `tools/set_build_info.py` | Pre-build script: injects `WLED_GIT_HASH/BRANCH/BUILD_TIME` → `/json/info` fields `git`/`branch`/`built` |
| `tools/upload_wled.py` | Custom upload: HTTP POST to `/update` via curl |

### Feature Flags (in `wled.h`)

Optional features can be disabled via `#define` or `-D` build flags:
- `WLED_DISABLE_OTA`, `WLED_DISABLE_ALEXA`, `WLED_DISABLE_MQTT`
- `WLED_DISABLE_INFRARED`, `WLED_DISABLE_WEBSOCKETS`, `WLED_DISABLE_HUESYNC`

### Usermods System

Usermods extend WLED by inheriting from `Usermod` (v2 API). They are registered in `wled00/usermods_list.cpp`.

Custom usermods in this repo:
- `usermods/ampworks/` — AMPWorks usermod; registers custom effects in `setup()`
- `usermods/mpr121/usermod_mpr121.h` — MPR121 capacitive touch sensor; `USERMOD_ID_MPR121 = 55`; library at `ArduinoLibs/MPR121/`
- `usermods/rs485_bridge/` — WiFi ↔ RS485 bridge for legacy HMTL modules; `USERMOD_ID_RS485_BRIDGE = 62`, `PinOwner::UM_RS485_BRIDGE`; needs `-D USERMOD_RS485_BRIDGE -D RS485_HARDWARE_SERIAL=2` (both, on ESP32) and `ArduinoLibs/{Socket,RS485Utils,RS485_non_blocking}` + `HMTL/Libraries/HMTLprotocol` (the wire format, imported not copied). Guarded so it compiles to an inert placeholder in the `usermods` wildcard/CI envs. See `usermods/rs485_bridge/readme.md`.

Custom effects (in `ampworks.cpp`): AMP AI, AMP AI Audio, AMP Moving SIN, HMTL Sparkle, Touch Ripple.

**Adding an effect:**
1. `uint16_t mode_name(void) { ... return FRAMETIME; }`
2. `static const char _data[] PROGMEM = "Name@Speed,Intensity,C1;Color1,Color2;Pal;flags;defaults";`
3. `strip.addEffect(255, &mode_name, _data);` in `AMPWorks::setup()`

**Cross-usermod data:** `UsermodManager::lookup(USERMOD_ID_xxx)` + typed cast + inline wrappers. Avoid `um_data_t` bitmask pattern.

### Data Persistence

Config and presets stored as JSON files on LittleFS. Config endpoints: `POST /json/cfg`. Up to 250 presets by ID.

### Web UI

The UI is built from source in `WLED/wled00/data/` and compiled by `tools/cdata.js` into `wled00/html_ui.h`, `html_settings.h`, and related `html_*.h` files. These generated headers are committed and should not be edited directly.

## Code Style

- **C++ files**: 2-space indentation
- **Web files** (`.html`/`.css`/`.js`): tab indentation
- Space between keywords and conditions: `if (a == b)` not `if(a==b)`
- No space between function name and parentheses: `doStuff(a)` not `doStuff (a)`
- Comments: space after `//` delimiter
