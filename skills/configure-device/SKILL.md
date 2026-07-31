---
name: configure-device
description: Configure a WLED device over the JSON API — LED bus type and pins (1-wire vs 2-wire), 2D matrix, I2C/MPR121, ESP-NOW sensor sync — plus the traps that make a correct-looking config silently not take effect.
---

Complements `flash` (build + upload) and `check-device` (report running build). This one is about
what to POST *after* the firmware is on, and why a change sometimes appears to be accepted and
then does nothing.

Set `DEV` once and paste the rest. Take it from an argument or `WLED_IP` rather than writing an
address into anything — these devices move between networks, and a baked-in IP is how a config
lands on the wrong device:

```bash
DEV="${1:-${WLED_IP:-wled.local}}"     # or: dns-sd -B _wled._tcp / avahi-browse -rt _wled._tcp
```

## There is no authentication on this API

Worth knowing before pointing any of the below at an address. From `wled00/wled_server.cpp`, the
JSON POST handler:

| Endpoint | Gate |
|---|---|
| `POST /json/state` — on/off, brightness, effect, colour, segments | **None, ever.** `deserializeState()` is called with no check. |
| `POST /json/cfg` — pins, WiFi, everything | Only `if (!correctPIN && strlen(settingsPIN)>0)`, i.e. only when a settings PIN is set. |
| `/update` (OTA) and the settings pages | `otaLock` / PIN. |

`otaLock` does **not** apply to the JSON API — `grep otaLock json.cpp` finds nothing. So anything
that can route to the device can drive its LEDs with one `curl`, and re-flash it too unless OTA is
locked.

Two consequences for this skill: confirm the address really is the device you mean before POSTing
(`wled-scan` lists them with names and geometry), and treat "it's on the LAN" as the entire access
control story — hardening means VLANs or firewall rules, not WLED settings.

## Before changing anything

Back up. A bad bus config can leave the device dark with no clue what it used to be, and presets
are not recoverable from the UI once overwritten.

```bash
curl -s -m 10 "http://$DEV/cfg.json"     -o "cfg-backup-$DEV.json"
curl -s -m 10 "http://$DEV/presets.json" -o "presets-backup-$DEV.json"
```

## The POST idiom

```bash
curl -s -H "Content-Type: application/json" -H "Expect:" \
  -X POST "http://$DEV/json/cfg" -d '{ ... }'
```

`-H "Expect:"` is **required**, not decoration. Without it curl sends `Expect: 100-continue` for
larger bodies, and WLED's server does not answer it — the request hangs or the body is dropped and
you get a success-looking response for a config that never arrived.

Read back what actually landed rather than trusting `{"success":true}` — it means "parsed", not
"applied as you intended":

```bash
curl -s "http://$DEV/json/cfg" | python3 -m json.tool | head -60
```

## Trap: persisted config beats compile-time defaults

**This is the one that wastes hours.** `cfg.json` on LittleFS overrides the `-D` defaults compiled
into the firmware. Re-flashing does **not** fix a bad persisted value; the new firmware boots and
immediately loads the old config over its own defaults.

Symptom: you fix a pin in `platformio.ini`, flash, and the device still misbehaves.
A real case: `hw.if.i2c-pin` persisted as `[-1,-1]` meant MPR121 stayed "not found" no matter how
many times the correct I2C pins were compiled in. The fix is a POST, not a rebuild:

```bash
curl -s -H "Content-Type: application/json" -H "Expect:" \
  -X POST "http://$DEV/json/cfg" -d '{"hw":{"if":{"i2c-pin":[19,22]}}}'
```

When a setting refuses to take, check what is persisted *before* rebuilding again.

## LED buses: 1-wire vs 2-wire

Bus types are in `WLED/wled00/const.h`. The distinction that matters:

| Type | Number | Wiring |
|---|---|---|
| WS2812 / SK6812 / most strips | 22, 30, … (≤ 39) | **1-wire**, clockless — `pin: [data]` |
| WS2801 | **50** | **2-wire**, clocked — `pin: [data, clock]` |
| APA102 / DotStar | **51** | 2-wire, clocked |
| LPD8806 / P9813 / LPD6803 | 52 / 53 / 54 | 2-wire, clocked |

Anything in `TYPE_2PIN_MIN`(48)–`TYPE_2PIN_MAX`(63) takes two pins; WLED derives the pin count from
the type (`is2Pin(type) + 1`).

**The classic mistake:** a clocked strip configured as *two separate 1-wire buses*, one per pin —
so the clock line is driven as a second data line and nothing works. Seen on a WS2801 device that
had pin 16 (30 LEDs) and pin 2 (10 LEDs) as two `type: 22` buses. Correct is **one** bus:

```bash
curl -s -H "Content-Type: application/json" -H "Expect:" \
  -X POST "http://$DEV/json/cfg" \
  -d '{"hw":{"led":{"total":40,"ins":[{"start":0,"len":40,"pin":[16,2],"order":0,"type":50,"rev":false,"skip":0,"ref":false,"rgbwm":0,"freq":0}]}}}'
```

If someone reports "it won't accept pin 2", check whether they are adding a second bus rather than
a clock pin to the existing one.

After setting a bus, sanity-check two things the config cannot tell you:

- **LED count** — an odd split (30+10) usually means someone was fighting the pin layout, not that
  there are really two runs.
- **Colour order** (`order`, 0 = GRB). WS2801 is commonly RGB. Drive solid white and look: a tint,
  or red/green swapped, means `order` is wrong, not the wiring.

```bash
curl -s -H "Content-Type: application/json" -H "Expect:" -X POST "http://$DEV/json/state" \
  -d '{"on":true,"bri":128,"seg":[{"id":0,"fx":0,"col":[[255,255,255]]}]}'
```

## 2D matrix

2D effects need a matrix defined (`hw.led.matrix`), not just the right LED count — without it they
render as a 1D strip. Find a grid device by LED count: 25 = 5×5, 64 = 8×8.

```bash
curl -s -H "Content-Type: application/json" -H "Expect:" -X POST "http://$DEV/json/cfg" \
  -d '{"hw":{"led":{"matrix":{"mpc":1,"panels":[{"b":false,"r":false,"v":false,"s":false,"p":false,"h":8,"w":8,"x":0,"y":0}]}}}}'
```

## APA102 + MPR121 boards (GPIO19 conflict)

On these the LED bus and the I2C touch sensor fight over GPIO19: the default VSPI reserves it as
MISO, while MPR121 wants it for SDA. Needs all three:

- build with `-D AMPWORKS_DOTSTAR_HSPI` (env `apa102_mpr121`) so the LED bus uses HSPI
- `hw.if.i2c-pin` = `[19,22]`
- `ap.behav` = `0`

Flash the `apa102_mpr121` env, **not** plain `ampworks`, or MPR121 reports "not found".

If I2C pins get stuck at `[-1,-1]` and a POST won't take, temporarily define a clockless bus to
release the pin reservation, set the I2C pins, then restore the APA102 bus.

## ESP-NOW sensor sync

Two different keys — both matter, and they are easy to confuse:

- `nw.espnow` — the **radio**. Starts quickEspNow and the RX callback. Sensor sync does nothing
  without it.
- `if.sync.espnow` — WLED's *own* state sync over ESP-NOW. Independent of the sensor bus.

Set on **both** nodes; enabling one end looks like a wiring fault.

```bash
curl -s -H "Content-Type: application/json" -H "Expect:" \
  -X POST "http://$DEV/json/cfg" -d '{"nw":{"espnow":true}}'
```

Nodes must share a WiFi channel — ESP-NOW is channel-based, so an AP that moves channels splits
the mesh.

## Firmware envs

| Env | Use |
|---|---|
| `display_only` | LEDs only, no MPR121, no RS485. The right default for a plain device. |
| `ampworks` | MPR121 touch + RS485 bridge (needs `RS485_HARDWARE_SERIAL=2`). |
| `apa102_mpr121` | APA102 + MPR121 boards — HSPI LED bus, I2C on 19/22. |

## Verify

Confirm the *running* build, not the one you meant to send:

```bash
curl -s "http://$DEV/json/info" | python3 -c "
import json,sys; d=json.load(sys.stdin)
for k in ('ver','git','branch','built'): print(f'{k}: {d.get(k,\"(missing)\")}')
print('leds:', d.get('leds'))
"
```

Then check it is *stable* — a bad bus config can boot-loop, which looks like a working flash if you
only poll once. Uptime should climb between two reads 20s apart:

```bash
for i in 1 2; do curl -s "http://$DEV/json/info" | python3 -c "
import json,sys; d=json.load(sys.stdin); print('uptime:', d.get('uptime'), 'heap:', d.get('freeheap'))"; sleep 20; done
```

## When the device stops responding

Before assuming the config broke it, rule out your own machine — a laptop that has roamed to
another AP or VPN produces exactly the same symptom as a bricked device, and the panic is avoidable:

```bash
# Are you still on the device's subnet?
ipconfig getifaddr en0 2>/dev/null || ip -4 addr show scope global | awk '/inet /{print $2}'
# Is the gateway reachable at all?
ping -c2 "$(netstat -rn -f inet 2>/dev/null | awk '/^default/{print $2; exit}' \
            || ip route | awk '/^default/{print $3; exit}')"
```

If **several** devices are unreachable at once — including ones you never touched — it is almost
certainly the network, not the device. If only the one you just configured is gone, suspect a
boot loop from a bad bus config, and recover over serial or via `scripts/wled-ap-hop.sh`.

## Upgrading across major versions

Going from e.g. 0.14.x to 16.x is a large config-format jump. WiFi settings normally migrate, but
if they don't the device falls back to its own AP and needs **physical access** — so don't OTA a
remote or hard-to-reach device without a way to recover it. Back up first (above), and after the
reboot confirm it rejoined at the expected IP before assuming success.

Related: `scripts/wled-ap-hop.sh` for talking to a device that has dropped to AP mode.
