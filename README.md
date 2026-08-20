# WLED_dev

A working repository for custom [WLED](https://github.com/wled/WLED) LED-controller firmware, built
around a fork of WLED 16.0.1 with three additions:

- **Custom usermods and effects** — capacitive touch (MPR121), a sensor-sync layer that shares touch
  events between devices, and a handful of effects.
- **An ESP-NOW mesh** — devices relay sensor events and control commands to each other over a
  multi-hop backbone, so a touch on one panel can drive the whole installation.
- **An RS485 bridge** — talks the legacy HMTL wire format, so older hardware keeps working alongside
  the WLED devices.

**The firmware source is not at the root of this repo.** It lives in the `WLED/` submodule, at
`WLED/wled00/`, with the custom work under `WLED/usermods/`. Nothing at the top level is buildable
firmware — the root holds the build harness, host tests, and tooling. That layout is the single
most confusing thing here, so it is worth knowing before you start.

## Quick start

From a fresh clone to a running device.

```bash
git clone https://github.com/aphelps/WLED_dev.git
cd WLED_dev
./setup.sh
```

`setup.sh` checks prerequisites (`git`, `python3` + pip, and **Node.js/npm**), initialises the four
submodules, creates an isolated PlatformIO install in `.venv/`, and finishes with a smoke build to
prove the toolchain works. Pass `--no-verify` to skip that build if you are in a hurry.

Node is not optional: WLED's web-UI headers (`wled00/html_*.h`) are generated at build time and are
gitignored, so a build without npm fails in a way that does not obviously point at Node.

Then put `pio` on your PATH and build:

```bash
source .venv/bin/activate
cd WLED
pio run -e ampworks
```

**Always name the environment.** A bare `pio run` does *not* build our firmware — it builds
upstream WLED's entire release matrix (`default_envs` in `WLED/platformio.ini` lists 23 ESP8266 and
ESP32 targets, starting with `nodemcuv2`). It will take a long time and produce nothing you want.

### Flash it

Uploads go over HTTP rather than the usual OTA protocol, because macOS Sequoia blocks the UDP that
espota needs. The upload target is wired to `tools/upload_wled.py`, which POSTs the `.bin` to the
device's `/update` endpoint:

```bash
pio run -e ampworks -t upload                      # default device (192.168.1.55)
WLED_IP=192.168.1.99 pio run -e ampworks -t upload # any other device
```

### Verify what is actually running

Do this every time. It is the step that catches "I flashed it but nothing changed":

```bash
curl -s http://192.168.1.55/json/info | python3 -m json.tool
```

The `git`, `branch` and `built` fields are injected at build time by
`WLED/tools/set_build_info.py`. If they do not match the commit you just built, the upload did not
take — or you flashed a different device.

## Which environment for which board

There are **two separate PlatformIO projects** here, and conflating them is the usual mistake.

`WLED/platformio.ini` — 37 environments, most of them upstream's. Ours are:

| Env | Hardware |
|---|---|
| `ampworks` | ESP32 dev board: MPR121 touch + RS485 bridge on Serial2 |
| `apa102_mpr121` | APA102 LEDs on HSPI, MPR121 on I2C pins 19/22 |
| `display_only` | Sensor-sync + effects, with **no** local MPR121 |

Picking `ampworks` for an APA102+MPR121 board is why a touch sensor "isn't found" — the pins differ.
These three are exactly what CI compiles.

`esp-now-router/platformio.ini` — a different project, for the mesh backbone:

| Env | Purpose |
|---|---|
| `wt32-eth01` | The backbone router board (default) |
| `esp32dev` | Generic ESP32 router |
| `native` | Host tests, no hardware |

Building these from inside `WLED/` will not work.

## Testing

Everything here is host-compiled. No device, no flashing, no hardware.

```bash
make               # or `make test` — all host suites plus the README checks
make test-wled     # sensor-sync dispatch + the SPSC ring
make test-bridge   # HMTL wire format, run twice: native ABI and packed
make test-libs     # ArduinoLibs RS485 receive path
make test-router   # mesh relay + leader election
make test-readme   # verify this README's claims against the source files
make test-ui       # web-UI builder (needs Node)
make test-all      # everything, including the UI test
```

One behaviour worth knowing: if a submodule is checked out at a revision that carries no tests, its
suite is **skipped with a note and still exits 0**. That is deliberate for local work, but it means
a green `make test` does not by itself prove every suite ran. CI passes `STRICT=1`, which turns a
skipped suite into a failure.

## The submodules

| Path | Repo | Why it exists |
|---|---|---|
| `WLED/` | [aphelps/WLED](https://github.com/aphelps/WLED) | The firmware fork. All custom work lives here. |
| `ArduinoLibs/` | [aphelps/ArduinoLibs](https://github.com/aphelps/ArduinoLibs) | Shared Arduino libraries: MPR121, Debug, and the RS485 socket layer. On the firmware build path. |
| `HMTL/` | [aphelps/HMTL](https://github.com/aphelps/HMTL) | The source of truth for the HMTL wire format. **Only `HMTLprotocol` is ever compiled** — the rest pulls in dependencies WLED 16 cannot build. Vendored rather than copied so there is no second copy of the wire format to keep in sync. |
| `esp-now-router/` | [aphelps/esp-now-router](https://github.com/aphelps/esp-now-router) | The mesh backbone: multi-hop relay and leader election. **Not** on the firmware build path — it builds as its own project. |

They are pinned to specific commits. After pulling, run `git submodule update --init` (or
`./setup.sh` again) or you will build against stale submodule code — a confusing class of failure
where the source you are reading is not the source being compiled.

## Repo layout

| Path | What |
|---|---|
| `WLED/` | The firmware submodule. Source at `wled00/`, custom work at `usermods/`. |
| `ArduinoLibs/`, `HMTL/`, `esp-now-router/` | The other submodules (above). |
| `Makefile` | Runs the host test suites across all submodules. |
| `setup.sh` | One-shot setup for a fresh clone. |
| `skills/` | Task-focused notes that AI assistants load on demand. Readable on their own. |
| `tools/` | Host-side probes — `hmtl_cli.py`, `rs485_bridge_probe.py`. **Not** `WLED/tools/`, which is build scripting; the name collision is confusing. |
| `docs/` | Longer-form guides, e.g. the effect-development guide. |
| `configs/` | Saved device configurations. |
| `.github/workflows/` | CI: host tests under `STRICT=1`, then firmware builds. |

## When it goes wrong

- **`setup.sh` fails and Node is not mentioned.** Confirm `node --version` and `npm --version`
  work. The web-UI pre-build needs them and `setup.sh` will not install them for you.
- **You changed a submodule's source but the build did not change.** Each PlatformIO environment
  keeps its own copy of library dependencies, and a stale copy shadows your edit — the symptom is a
  compile error naming a function that plainly exists in the header you are looking at. Delete
  `WLED/.pio/libdeps` and rebuild.
- **The device answers but reports the wrong build.** Compare `git`/`branch`/`built` from
  `/json/info` against what you built. Usually the upload went to a different device — check
  `WLED_IP`.
- **A touch sensor "isn't found".** Almost always the wrong environment for the board; see the
  table above.
- **`make test` is green but a suite you care about did not run.** Re-run with `make test STRICT=1`,
  which fails instead of skipping.

## The mesh, in one paragraph

Devices form an ESP-NOW mesh so that a sensor event — a touch — reaches every device rather than
only the one that was touched. Edge nodes attach to a router; routers relay multi-hop, elect a
leader, and heal around a node that drops out. A control plane rides the same transport so a phone
attached to any one node can drive the whole installation. The design, including the go/no-go
criteria and the failure modes it is built against, is written up in
`esp-now-router/BACKBONE_ROUTER.md`, and the wire format in
`WLED/usermods/ampworks/SENSOR_SYNC.md`. Both live inside submodules, so open them from your own
checkout — they are not browsable from this repo's GitHub page.

## Further reading

- [`skills/README.md`](skills/README.md) — the agent skills, and how to install them.
- [`docs/effect-development-guide.md`](docs/effect-development-guide.md) — writing a new LED effect.
- `WLED/usermods/rs485_bridge/readme.md` — the RS485 bridge (in a submodule; open locally).
- `CLAUDE.md` is what AI coding assistants read in this repo. It is written for them rather than for
  you, and parts of it have drifted from the code — prefer this README and the source files.
