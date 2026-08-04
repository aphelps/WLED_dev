# scripts

Fleet-level tools that operate on WLED devices over the network, rather than on the firmware source.
They're plain Python 3 (standard library only) plus one small Swift helper, so there's nothing to
install — but they do drive the machine's Wi-Fi radio, so read the safety notes before the first
real run.

| Script | For |
|---|---|
| `wled_apjoin.py` | Find devices sitting in AP-fallback mode (`WLED-AP`) and put them on your network, without connecting to each one by hand. |
| `wled_scan_ap.swift` | Helper for the above: lists visible Wi-Fi networks as JSON. Read-only; safe to run on its own. |

Related: `skills/wled-scan/` finds devices that are **already** on your LAN and tables their config
(`wleds`). `wled_apjoin.py` is the step before that — getting them onto the LAN in the first place.

---

## `wled_apjoin.py` — adopt devices broadcasting their setup AP

A WLED device that can't reach a known network falls back to its own access point, `WLED-AP`
(password `wled1234`, device at `http://4.3.2.1`). Adopting one by hand means: join its AP from your
laptop, open the settings page, type the network name and password, save, reboot, and rejoin your
own network. This does that loop for you, for every such device in range.

### Before the first run

**This takes your machine off its own Wi-Fi network while it works.** Each device it adopts means
associating to that device's AP, so anything depending on your connection — SSH sessions, a VPN, a
remote desktop, a long download — will drop. Run it from the physical machine, not over the network
you're about to leave.

Requirements:

- **macOS.** It shells out to `networksetup`, `ipconfig` and `swift`. Linux/Raspberry Pi is a later
  task; see `PORTING_NOTES` at the bottom of the script for the `nmcli` equivalents.
- **A 2.4 GHz target network.** An ESP32 has no 5 GHz radio. Pointing devices at a 5 GHz-only SSID
  produces devices that store the credentials and never connect.
- **Location Services access for scanning.** macOS withholds SSIDs from unauthorised processes. Grant
  it to your terminal (and to `swift`) under *System Settings → Privacy & Security → Location
  Services*. Without it the tool stops with an explicit error rather than reporting "no devices
  found" — but you still have to grant it before anything works.

Check that scanning works at all first. This is read-only and changes nothing:

```bash
swift scripts/wled_scan_ap.swift            # every visible network, as JSON
swift scripts/wled_scan_ap.swift WLED-AP    # targeted probe for one SSID
```

If you get `{"ok":true,"networks":[]}` with a non-zero `scanned` count, or `ssids_withheld: true`,
that's the Location Services grant missing.

### Running it

Always start with `--dry-run`. It scans, identifies candidates and prints what it *would* do,
without joining anything or touching your network connection:

```bash
scripts/wled_apjoin.py --ssid MyNetwork --password 'secret' --dry-run
```

When the candidate list looks right, drop the flag:

```bash
scripts/wled_apjoin.py --ssid MyNetwork --password 'secret'
```

It asks for confirmation before joining anything. `--yes` skips the prompt (needed for unattended
runs — without a TTY the tool exits rather than assuming yes).

If the machine you're running from should end up on a *different* network than the devices:

```bash
scripts/wled_apjoin.py --ssid IoT-2G --password 'iotpass' \
                       --home-ssid MyNetwork --home-password 'homepass'
```

`--home-password` is only needed when the network isn't already in your keychain — normally it is,
and macOS rejoins without being told the passphrase.

It can be run from anywhere; paths are resolved relative to the script, not the working directory.

> **The target network's password is passed on the command line**, so it is visible in `ps` for the
> life of the run and lands in your shell history. There is no env-var or file alternative yet. In
> the meantime: quote it, and prefix the command with a space if your shell is configured with
> `HISTCONTROL=ignorespace`. The detached watchdog does *not* expose it — that one is passed over a
> pipe specifically to keep it out of the process table.

### What it does, in order

1. **Scans your current LAN first** (via `skills/wled-scan/`), and records the MAC of every device
   already on it. This is not just reporting: a device found here is *left alone*, so a working
   device is never needlessly rewritten.
2. **Scans for Wi-Fi networks**, both a broadcast sweep and a targeted probe per literal pattern —
   a sweep has been observed to miss an AP that a targeted probe finds.
3. **Picks candidates**: anything matching `WLED-AP`, `WLED-*`, `WLED_*` (plus your own
   `--ssid-pattern`s), and — because a *renamed* device is indistinguishable from a stranger by name
   — any open 2.4 GHz AP. Your own `--ssid` and `--home-ssid` are always excluded.
4. **Arms a watchdog**, a detached process that force-rejoins your home network after
   `--safety-timeout` seconds (default 180) no matter what happens to the main process — including
   being killed outright.
5. **For each candidate**: joins it, asks `http://4.3.2.1/json/info` what it is, and pushes
   credentials **only** if it answers `brand: WLED` *and* its MAC belongs to Espressif. Anything
   else is left immediately, having been sent nothing. Then it forgets the AP, so macOS doesn't
   auto-join it later.
6. **Restores your network** — on success, failure, exception or Ctrl-C — and only disarms the
   watchdog once it can prove it's back on a real address (a WLED AP hands out `4.3.2.x`, which
   doesn't count).
7. **Verifies**, by re-scanning the LAN for up to `--verify-deadline` seconds, that each device it
   pushed to actually turned up. This has to happen after leaving the AP: WLED won't complete its
   own join while an AP client is still associated.

### Reading the output

```
SSID                          RESULT          DETAIL
------------------------------------------------------------------------------
WLED-AP                       pushed          a020a6123456 — rebooting
WLED-AP                       joined          a020a6123456
Basement speaker              not-wled        not WLED (brand=None)
```

| Result | Means |
|---|---|
| `pushed` | Credentials accepted; device rebooting. |
| `joined` | Confirmed back on your LAN afterwards. This is the success case. |
| `never-appeared` | Credentials were accepted but the device never showed up. Usually a wrong password, or a 5 GHz target network. |
| `skip` | Already on this LAN — deliberately left alone. |
| `would-push` | `--dry-run` only. |
| `not-wled` | Something else answered; nothing was sent to it. |
| `inspected` | `--inspect` only: its config, reported. Nothing was sent. |
| `join-failed` | Couldn't get onto that AP. The detail says which way it failed — the AP was gone by the time we tried, the association never produced an address, or `networksetup` claimed success while leaving us on the network we started on. |
| `push-failed` | Either the POST itself failed, or the config never read back — in which case the device is deliberately **not** rebooted, since rebooting on an unsaved config is what loses the credentials. |
| `give-up` | Retry cap hit. |

Exit status: `0` everything worked · `1` something failed, or the rejoin couldn't be confirmed ·
`2` scanning failed (usually the Location Services grant) · `130` interrupted.

### If it leaves you off the network

It shouldn't — that's what the watchdog is for, and it stays armed precisely when the restore
*couldn't* be confirmed. Wait out `--safety-timeout` (default 3 minutes) and it will rejoin you.

To fix it immediately:

```bash
networksetup -setairportnetwork en0 MyNetwork 'homepass'
```

And if a `WLED-AP` is left in your preferred-networks list (macOS would then auto-join it):

```bash
networksetup -removepreferredwirelessnetwork en0 WLED-AP
```

### Options

| Flag | Default | Notes |
|---|---|---|
| `--ssid` | *required* | Network to put the devices on. Must be 2.4 GHz. |
| `--password` | *required* | Password for `--ssid`. See the warning above. |
| `--home-ssid` | `--ssid` | Network *this machine* returns to. |
| `--home-password` | — | Only if it isn't already in the keychain. |
| `--ap-password` | `wled1234` | For secured WLED APs. This is WLED's published default, not a secret. |
| `--pin` | — | Device settings PIN, if you've set one. |
| `--ssid-pattern GLOB` | — | Extra name pattern; repeatable. |
| `--no-open-probe` | off | Don't try open 2.4 GHz APs. Safer around strangers, but misses renamed devices. |
| `--yes` | off | Skip the confirmation prompt. |
| `--dry-run` | off | Identify only; join nothing, push nothing. Uses no radio at all. |
| `--inspect` | off | Join each device and report its config, pushing nothing. Unlike `--dry-run` this *does* use the radio. See below. |
| `--adopt-deadline` | 0 | Keep re-scanning and retrying for this long. **Set this** if the APs are flapping — see below. 0 = single pass. |
| `--rescan-interval` | 15 | Seconds between re-scans while waiting for an AP to reappear. |
| `--iface` | `en0` | Wi-Fi interface. |
| `--connect-timeout` | 30 | Seconds to wait for an association. |
| `--ap-settle-timeout` | 20 | Seconds to wait for DHCP after associating, before speaking IP. |
| `--push-confirm-timeout` | 12 | Seconds to wait for the pushed config to read back before rebooting the device. |
| `--safety-timeout` | 180 | Watchdog force-rejoin delay. Raised automatically (out loud) if `--adopt-deadline` would outlive it. |
| `--max-attempts` | 2 | Retry cap per device. |
| `--verify-deadline` | 90 | Seconds to wait for pushed devices to reappear. |

### When the APs come and go

A device in AP-fallback drops its SoftAP every time it retries its own connection, so its AP
appears and vanishes on a cycle of a minute or two. A single pass loses that race constantly: the
AP is there during the scan and gone by the time the join runs (`join-failed  Could not find
network …`), or it simply isn't up when you start.

`--adopt-deadline` is the answer — it keeps re-scanning for that long:

```bash
scripts/wled_apjoin.py --ssid MyNetwork --password 'secret' --adopt-deadline 420
```

Verdicts about the *device* (`pushed`, `not-wled`, `skip`) are final, so nothing is ever pushed
twice. Failures of the *radio* (`join-failed`) stay eligible, so a device is never written off for
being mid-retry. The deadline also covers the opening scan, because finding nothing at t=0 is a
normal starting state rather than a reason to quit.

### Diagnosing a device that won't join

If a device takes the credentials and still never appears, `--inspect` joins it and reports what it
actually has stored, without sending anything:

```bash
scripts/wled_apjoin.py --ssid MyNetwork --password 'secret' --inspect --adopt-deadline 300
```

```
SSID                          RESULT      DETAIL
WLED-TOUCH-MATRIX             inspected   2cbcbbdaca60 ver=16.0.1 ap.behav=0 ap.chan=1 nw.ssid='STARLINK' nw.pskl=0
```

That line is a real diagnosis: the device is configured for a network called `STARLINK` with
`pskl=0` — no stored passphrase — so it can never authenticate and falls back to its AP forever.
`pskl` is the passphrase *length*; WLED never echoes the passphrase itself, and `0` against a
non-empty SSID is the signature of credentials that didn't save.

### Known limitations

- **Two devices broadcasting the same SSID are seen as one.** Candidates are deduplicated by name,
  so if two stock devices are both advertising `WLED-AP`, only one is adopted per sweep. With
  `--adopt-deadline` set the second is usually picked up on a later pass — once the first has
  rebooted onto your network its AP is gone, and the name resolves to the remaining device. Without
  the deadline, run the tool again.
- **Association can simply fail, repeatedly.** With a short-lived AP and a marginal signal,
  `networksetup` may not get on at all — the run reports honest `join-failed` rows rather than
  guessing. Moving the machine closer is usually the fix; a device whose config you already know is
  often faster to correct directly at `http://4.3.2.1` while its AP is up.
- **macOS only.** See `PORTING_NOTES` in the script.
- **Not for an ESP32 to run.** A single-radio ESP32 shares one channel between its AP and STA
  interfaces, so hopping onto a device's AP drags its own SoftAP to that channel and drops its
  uplink — which would break an ESP-NOW backbone for the duration of every hop. Mesh-side adoption
  is `esp-now-router`'s problem, not a port of this.

---

## Tests

Pure logic and a fake platform — no radio, no network, no device:

```bash
make test-apjoin      # just these
make test             # everything in the repo
```

The parts worth knowing are tested as safety properties rather than behaviours: that credentials are
never sent to an AP that didn't identify itself as WLED-on-Espressif, that every path forgets the AP
it joined, and that a 5 GHz or unknown-band network is never a candidate.
