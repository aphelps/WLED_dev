---
name: wled-scan
description: Find every WLED device on the LAN and Tailscale and table their configuration — firmware build, LED bus type and pins, matrix, I2C, ESP-NOW. Use when you need to know what is on the network, which device is which, or which one has a suspect config.
---

```bash
wleds                          # LAN + Tailscale
wleds --lan                    # local subnets only
wleds --tailscale              # Tailscale peers only
wleds --subnet 192.168.1.0/24  # an explicit range (repeatable)
wleds --host wled.local        # specific hosts (repeatable)
wleds --json                   # machine-readable
wleds --timeout 2.0            # slow network / distant devices
```

`wleds` is a symlink onto `PATH` created by `skills/install.sh`; the implementation is
`skills/wled-scan/wled-scan.py` and can be run directly. Standard library only.

## Output

```
NAME        ADDRESS      VER     BUILD  LEDS  BUSES (type[pins]xN)  MATRIX  I2C    SYNC    UP
Trancender  10.1.10.227  16.0.1  -      1296  P9813[16,3]x1160      54x24   -1,-1  espnow  1d0h
```

| Column | Reading it |
|---|---|
| `BUILD` | `git@branch` from `set_build_info.py`. **`-` means stock WLED, not our firmware.** |
| `BUSES` | `type[pins]xLEDs` per bus. A trailing `!` flags a clocked type with fewer than two pins. |
| `MATRIX` | Panel geometry, or `-`. 2D effects need this, not merely the right LED count. |
| `I2C` | `hw.if.i2c-pin`. `-1,-1` means unset — MPR121 will report "not found". |
| `SYNC` | `espnow` = the radio (`nw.espnow`); `wled-sync` = WLED's own sync. Different things. |

## Why a sweep and not mDNS

WLED advertises `_wled._tcp`, so mDNS looks like the obvious discovery route. It is deliberately
not used: the macOS and Linux browse tools stream until interrupted with no portable one-shot mode,
and — the deciding reason — **Tailscale peers are not on the local multicast domain**, so mDNS
cannot see them at all. A concurrent sweep of `/json/info` treats both uniformly and finishes a /24
in a few seconds.

Local subnets come from `ifconfig`/`ip addr`, skipping loopback and the `100.` CGNAT range (those
come from `tailscale status --json` instead, so peers are enumerated rather than guessed).
Anything wider than a /22 is refused, so a misconfigured interface cannot start a 65k-host sweep.

## What it is good for

- **"Which device is which?"** — match by name, LED count and geometry before flashing anything.
  Confirming the target this way is much cheaper than recovering a device you flashed by mistake.
- **Spotting a bad LED config at a glance** — the `!` marker catches the common WS2801/APA102
  mistake where a clocked strip is wired across two single-pin buses instead of one two-pin bus.
- **Auditing a mesh** — `SYNC` shows which nodes actually have ESP-NOW on. Sensor sync needs it on
  **both** ends, and one-sided is indistinguishable from a wiring fault.
- **Finding stock devices** — `BUILD` of `-` is a device that has never had our firmware.

## Gotchas

- Only finds devices **on a network you can reach**. Nothing found usually means the wrong subnet,
  not absent devices — check `ipconfig getifaddr en0` / `ip -4 addr` first.
- A device in AP-fallback mode is on its own network and will not appear; see
  `scripts/wled-ap-hop.sh`.
- `LEDS` (`info.leds.count`) and the summed bus lengths can disagree; that gap is itself a finding.

Related: `check-device` for one device in depth, `configure-device` to fix what this turns up,
`flash` to update it.
