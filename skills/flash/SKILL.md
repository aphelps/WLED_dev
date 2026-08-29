---
name: flash
description: Build and upload firmware to a WLED device over HTTP OTA, then confirm the running build matches local git state. PlatformIO environment and target address are both parameters — no device or address baked in.
disable-model-invocation: true
---

Pick the environment for the hardware in hand rather than assuming:

| Env | Hardware |
|---|---|
| `display_only` | LEDs only — no MPR121, no RS485. The right default for a plain device. |
| `ampworks` | MPR121 touch + RS485 bridge |
| `apa102_mpr121` | APA102 + MPR121 board (HSPI LED bus, I2C on 19/22) |

```bash
ENV="${ENV:-display_only}"
DEV="${1:-${WLED_IP:-}}"     # host or IP; if empty, upload_wled.py uses its own default
```

1. **Record the expected build**, from the repo root:
   ```bash
   git -C WLED rev-parse --short HEAD
   ```
   Append `+dirty` if `git -C WLED status --porcelain` is non-empty — a dirty tree means the binary
   corresponds to no commit.

2. **Build and upload** from the `WLED/` directory (`platformio.ini` paths are relative to it).
   Two routes; `tools/upload_wled.py` picks by the target it is given.

   **Over the network** (device already on WiFi):
   ```bash
   WLED_IP="$DEV" pio run -e "$ENV" -t upload
   ```
   An HTTP POST to `/update`, not espota — macOS blocks the UDP espota needs.

   **Over the cable** — required for a FACTORY-FRESH board, which has no IP for OTA to reach:
   ```bash
   pio run -e "$ENV" -t upload --upload-port /dev/cu.usbserial-XXXX
   ```
   A serial target routes to the platform's esptool path instead. Identify the port first —
   names reshuffle between sessions:
   ```bash
   esptool.py --port /dev/cu.usbserial-XXXX chip_id     # confirm the MAC before flashing
   ```
   Step 3's verification needs the device's address, so for a fresh board it applies only after
   first boot and provisioning.

3. **Confirm the running build** matches step 1, polling while the device reboots (~6 × 5s):
   ```bash
   curl -s -m 8 "http://$DEV/json/info" | python3 -c "
   import json,sys; d=json.load(sys.stdin)
   for k in ('ver','git','branch','built'): print(f'{k}: {d.get(k,\"(missing)\")}')
   "
   ```
   Report the confirmed fields, or error on timeout. Never report success from the upload alone: a
   200 on `/update` means the image was accepted, not that it booted.

## Before flashing anything you cannot physically reach

Back it up first. A device that returns without its WiFi settings drops to its own AP and needs
hands on it:

```bash
curl -s -m 10 "http://$DEV/cfg.json"     -o "cfg-backup-$DEV.json"
curl -s -m 10 "http://$DEV/presets.json" -o "presets-backup-$DEV.json"
```

Most relevant across major versions (e.g. 0.14.x → 16.x), where the config format changes. See
`configure-device` for recovery and for setting the device up afterwards.

## If the build fails on a library member that plainly exists

PlatformIO *copies* path-based `lib_deps` out of the submodules into `.pio/libdeps/<env>/` and
builds the copy, without reliably re-syncing when the submodule pointer moves. A symptom like
`'RS485Socket' has no member named 'getFramingErrorCount'` — while the header clearly declares it —
means the copy is stale. Each env keeps its own, so clear them all:

```bash
rm -rf WLED/.pio/libdeps
```
