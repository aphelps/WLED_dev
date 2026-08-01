#!/usr/bin/env python3
"""wled_apjoin — find WLED devices sitting in AP-fallback mode and put them on your network.

macOS only for now (see PORTING NOTES at the bottom). Standard library plus a small Swift helper
for scanning, because CoreWLAN is the only interface on macOS 26 that still returns SSIDs.

The dangerous part is not the HTTP: joining a device's AP takes this machine off its own network,
so every path must put it back. That is why the run is wrapped in a restore that executes on
success, failure, exception and signal, plus a detached watchdog that force-restores even if this
process is killed outright.

Usage:
  scripts/wled_apjoin.py --ssid MyNetwork --password secret --home-ssid MyNetwork
  scripts/wled_apjoin.py --ssid MyNetwork --password secret --home-ssid MyNetwork --dry-run
  scripts/wled_apjoin.py ... --yes            # do not confirm each device
  scripts/wled_apjoin.py ... --ssid-pattern 'Studio*'
"""

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SWIFT_SCAN = os.path.join(REPO_ROOT, "scripts", "wled_scan_ap.swift")
SCAN_PATH = os.path.join(REPO_ROOT, "skills", "wled-scan", "wled-scan.py")

DEVICE_IP = "4.3.2.1"          # WLED's SoftAP address (wled.cpp: WiFi.softAPConfig(4,3,2,1))
DEFAULT_AP_PASS = "wled1234"   # const.h: DEFAULT_AP_PASS — published, so not a secret
DEFAULT_PATTERNS = ["WLED-AP", "WLED-*", "WLED_*"]

# Espressif OUIs. Checked on info.mac AFTER associating — BSSIDs are withheld at scan time, so
# this cannot be a scan filter, but it can gate the credential push.
ESPRESSIF_OUIS = {
    "244cab", "24a160", "240ac4", "2462ab", "3c71bf", "483fda", "4c11ae", "500291",
    "5ccf7f", "600194", "68c63a", "7cdfa1", "807d3a", "840d8e", "8caab5", "94b97e",
    "9c9c1f", "a020a6", "a4cf12", "a8032a", "ac67b2", "b4e62d", "bcddc2", "c44f33",
    "cc50e3", "d8a01d", "d8bfc0", "dc4f22", "e09806", "e8db84", "ecfabc", "f008d1",
    "f4cfa2", "fcf5c4",
}


# --- pure logic (host-tested; no network, no platform calls) -----------------------------------
def matches_pattern(ssid, patterns):
    """Case-insensitive glob match against any pattern."""
    s = (ssid or "").lower()
    for p in patterns:
        rx = "^" + re.escape(p.lower()).replace(r"\*", ".*").replace(r"\?", ".") + "$"
        if re.match(rx, s):
            return True
    return False


def is_candidate(net, patterns, probe_open, exclude=()):
    """Should this scan result be tried? Returns (bool, reason).

    Three sources, because each misses what the others catch (see the plan):
      - name matches a WLED pattern
      - an open AP, which is how *renamed* devices are found — they are otherwise
        indistinguishable from strangers by name alone
    Open APs are restricted to 2.4 GHz: an ESP32 has no 5 GHz radio, so a 5 GHz open network
    cannot be a WLED device and there is no reason to touch it. That single check excludes most
    public hotspots (observed: `xfinitywifi` on channel 44).
    """
    ssid = net.get("ssid") or ""
    if not ssid:
        return False, "no ssid"
    # Never touch our own networks. Without this a run joins the home network, fails to find a
    # WLED device on it, and then FORGETS it — removing it from the preferred list on the way past.
    if any(ssid.lower() == (e or "").lower() for e in exclude):
        return False, "this is one of our own networks"
    if matches_pattern(ssid, patterns):
        return True, "name matches a WLED pattern"
    if probe_open and net.get("open"):
        # Require 2.4GHz positively. Testing `!= 5` fails OPEN when the band is missing or the
        # channel was unknown (-1), which would let 5/6GHz strangers through.
        if net.get("band") != 2:
            return False, f"open but band={net.get('band')!r} — an ESP32 is 2.4GHz only"
        return True, "open 2.4GHz AP — may be a renamed device"
    return False, "no match"


def is_espressif(mac):
    """True if the MAC's OUI belongs to Espressif. `mac` as WLED reports it: 12 hex chars."""
    if not mac:
        return False
    clean = re.sub(r"[^0-9a-fA-F]", "", str(mac)).lower()
    return len(clean) >= 6 and clean[:6] in ESPRESSIF_OUIS


def identify(info):
    """Is this a WLED device we should touch? Returns (ok, mac, reason).

    Gates the credential push: the target network's plaintext PSK goes over a link whose passphrase
    is published in WLED's own source (or is absent entirely), so we send it only to something
    confirmed to be a WLED device on Espressif silicon.
    """
    if not isinstance(info, dict):
        return False, None, "no /json/info response"
    mac = info.get("mac")
    if info.get("brand") != "WLED":
        return False, mac, f"not WLED (brand={info.get('brand')!r})"
    if not is_espressif(mac):
        return False, mac, f"MAC {mac!r} is not Espressif"
    return True, mac, "WLED device"


def decide_action(mac, lan_macs, attempts, max_attempts):
    """What to do with an identified device. Returns (action, reason).

    Adam's rule: skip only if it is on the CURRENT LAN; a device on a *different* network gets
    re-pointed onto this one. `lan_macs` therefore has to be a complete scan of the current LAN —
    if it is incomplete, working devices get needlessly rewritten.
    """
    if mac and mac.lower() in {m.lower() for m in lan_macs}:
        return "skip", "already on this LAN"
    if attempts.get(mac, 0) >= max_attempts:
        return "give-up", f"already tried {max_attempts}x without it appearing"
    return "push", "not on this LAN"


def build_wifi_cfg(ssid, password, pin=None):
    """Config body for POST /json/cfg.

    Deliberately NOT the `POST /settings/wifi` form: that handler reads every field
    unconditionally and a missing one arrives as "", so a credentials-only POST also empties
    apSSID (killing AP-fallback recovery), forces apBehavior=0, clamps TX power, and disables
    ESP-NOW while clearing linked_remotes. /json/cfg preserves absent keys (cfg.cpp: CJSON).
    """
    body = {"nw": {"ins": [{"ssid": ssid, "psk": password}]}}
    if pin:
        body["pin"] = pin
    return body


# --- macOS platform layer -----------------------------------------------------------------------
class MacPlatform:
    """Kept behind a narrow surface so a Linux/ESP32 implementation is additive, not surgery."""

    name = "macos"

    def __init__(self, iface="en0"):
        self.iface = iface

    def scan(self, ssid=None):
        cmd = ["swift", SWIFT_SCAN] + ([ssid] if ssid else [])
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        except (OSError, subprocess.SubprocessError) as e:
            return [], f"scan failed: {e}"
        if out.returncode != 0 or not out.stdout.strip():
            return [], f"scan failed: {out.stderr.strip() or 'no output'}"
        try:
            d = json.loads(out.stdout)
        except json.JSONDecodeError:
            return [], "scan returned non-JSON"
        if not d.get("ok"):
            return [], d.get("error", "scan error")
        if d.get("ssids_withheld"):
            # Never report an empty list here: it is indistinguishable from "no devices" and would
            # send someone hunting for a hardware fault.
            return [], ("macOS is withholding SSIDs — grant Location Services access to the "
                        "terminal/swift, or scanning will always look empty")
        return d.get("networks", []), None

    def join(self, ssid, password=None, timeout=30):
        cmd = ["networksetup", "-setairportnetwork", self.iface, ssid]
        if password:
            cmd.append(password)
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.SubprocessError) as e:
            return False, str(e)
        # networksetup reports failure in stdout, not the exit status.
        msg = (out.stdout or "").strip()
        if msg and "not" in msg.lower():
            return False, msg
        return True, None

    def forget(self, ssid):
        """Joining is not stateless: macOS stores the passphrase in the keychain and adds the SSID
        to the preferred list, which the OS then auto-joins later. Left alone, every run makes this
        machine more likely to silently hop onto a WLED AP on its own."""
        subprocess.run(["networksetup", "-removepreferredwirelessnetwork", self.iface, ssid],
                       capture_output=True, text=True)

    def is_connected(self):
        """Back on a real network — NOT merely holding an address.

        A WLED SoftAP hands out 4.3.2.x, so `ipconfig getifaddr` succeeds while we are still on the
        device's AP. Treating that as success is how a run reports "back on home Wi-Fi", disarms the
        watchdog, and exits 0 on a stranded host. Exclude the SoftAP subnet explicitly."""
        try:
            ip = subprocess.run(["ipconfig", "getifaddr", self.iface],
                                capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return False
        addr = (ip.stdout or "").strip()
        if not addr:
            return False
        if addr.startswith("4.3.2."):
            return False          # still on a WLED SoftAP
        if addr.startswith("169.254."):
            return False          # link-local: associated but no DHCP
        return True

    def wait_online(self, deadline_s):
        end = time.time() + deadline_s
        while time.time() < end:
            if self.is_connected():
                return True
            time.sleep(1.0)
        return False

    def watchdog(self, ssid, password, after_s):
        """Detached force-rejoin. Must outlive this process group: a terminal SIGINT reaches a
        plain background child, which is how the shell version could be left with nothing to
        recover the host after a second Ctrl-C.

        No shell, deliberately. An earlier version built a `/bin/sh -c` string with
        subprocess.list2cmdline — which is the WINDOWS quoting rule — so an SSID or password
        containing a space, quote, backtick or `$` was either corrupted or executed: `pa$$word`
        became the shell's PID, `a;id` ran `id`, and `p'w` killed the watchdog at launch with an
        unexpected-EOF while `dog` still looked alive. It also put the network PSK into a
        ps-visible argv for the whole timeout window.

        Instead: a python child that sleeps then execs the tool directly with an argv list, and
        takes the password on stdin so it never appears in the process table."""
        helper = (
            "import os,subprocess,sys,time\n"
            "t=float(sys.argv[1]); iface=sys.argv[2]; ssid=sys.argv[3]\n"
            "pw=sys.stdin.read()\n"
            "time.sleep(t)\n"
            "cmd=['networksetup','-setairportnetwork',iface,ssid]+([pw] if pw else [])\n"
            "subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", helper, str(int(after_s)), self.iface, ssid],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        proc.stdin.write((password or "").encode())
        proc.stdin.close()
        return proc


# --- HTTP ---------------------------------------------------------------------------------------
def get_json(host, path, timeout=5):
    try:
        with urllib.request.urlopen(f"http://{host}{path}", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


def post_json(host, path, body, timeout=10):
    data = json.dumps(body).encode()
    req = urllib.request.Request(f"http://{host}{path}", data=data, method="POST",
                                 headers={"Content-Type": "application/json", "Expect": ""})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace"), None
    except Exception as e:
        return None, str(e)


def lan_device_macs(timeout=2.0):
    """MACs currently on this LAN, via the existing scanner. This is a correctness input, not just
    verification: a device found here is left alone."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("wled_scan", SCAN_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    targets = []
    for net in mod.local_subnets():
        targets += [str(ip) for ip in net.hosts()]
    import concurrent.futures
    macs = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=64) as ex:
        for info in ex.map(lambda h: mod.probe(h, timeout), targets):
            if info and info.get("mac"):
                macs[info["mac"].lower()] = info.get("name") or info["_host"]
    return macs


PORTING_NOTES = """
Linux / Raspberry Pi (later task):
  scan     nmcli -t -f SSID,SIGNAL,CHAN,SECURITY dev wifi list  (or iw dev <if> scan)
  join     nmcli dev wifi connect <ssid> password <pw> ifname <if>
  forget   nmcli con delete <name>   — nmcli creates an AUTOCONNECT profile, so this is required
           or NetworkManager will re-associate to a WLED AP on its own later
  online   nmcli -t -f STATE general
  A second radio (USB dongle) removes the disconnection problem entirely: one interface holds the
  home network while the other hops. That is why `iface` is threaded through even though macOS
  only ever uses en0.

ESP32 (handled separately by esp-now-router, NOT by porting this):
  A single-radio ESP32 shares one channel between AP and STA, so associating to a WLED-AP on
  channel 1 drags its own SoftAP there and drops its STA link. ESP-NOW rides the current channel,
  so a mesh node doing this breaks the backbone for the duration of every hop
  (see esp-now-router/BACKBONE_ROUTER.md).
"""


# --- orchestration ------------------------------------------------------------------------------
def provision_one(plat, net, args, lan_macs, attempts, report):
    """Join one AP, identify it, maybe push credentials, leave. Returns after restoring nothing —
    the caller owns restoration, so a failure here can never skip it."""
    ssid = net["ssid"]
    ok, err = plat.join(ssid, None if net.get("open") else args.ap_password, args.connect_timeout)
    try:
        if not ok:
            # Inside the try so the finally still forgets it: a failed join can still have added
            # the SSID to the preferred list, which the OS would then auto-join later.
            report.append((ssid, "join-failed", err or "?"))
            return
        info = get_json(DEVICE_IP, "/json/info")
        good, mac, reason = identify(info)
        if not good:
            # Not ours: leave immediately, having sent nothing.
            report.append((ssid, "not-wled", reason))
            return
        action, why = decide_action(mac, lan_macs, attempts, args.max_attempts)
        if action != "push":
            report.append((ssid, action, f"{mac} — {why}"))
            return
        if args.dry_run:
            report.append((ssid, "would-push", f"{mac} — {why}"))
            return
        body = build_wifi_cfg(args.ssid, args.password, args.pin)
        _, perr = post_json(DEVICE_IP, "/json/cfg", body)
        if perr:
            report.append((ssid, "push-failed", perr))
            return
        get_json(DEVICE_IP, "/reset")     # /json/cfg does not reboot on its own
        attempts[mac] = attempts.get(mac, 0) + 1
        report.append((ssid, "pushed", f"{mac} — rebooting"))
    finally:
        plat.forget(ssid)


def main():
    ap = argparse.ArgumentParser(description="Join WLED devices in AP mode onto your network.")
    ap.add_argument("--ssid", required=True, help="network to put the devices on (must be 2.4GHz)")
    ap.add_argument("--password", required=True, help="password for --ssid")
    ap.add_argument("--home-ssid", help="network THIS machine returns to (defaults to --ssid)")
    ap.add_argument("--home-password", help="only needed if not already in the keychain")
    ap.add_argument("--ap-password", default=DEFAULT_AP_PASS, help="password for secured WLED APs")
    ap.add_argument("--pin", help="device settings PIN, if set")
    ap.add_argument("--ssid-pattern", action="append", default=[], metavar="GLOB")
    ap.add_argument("--no-open-probe", action="store_true",
                    help="do not try open 2.4GHz APs (skips renamed devices)")
    ap.add_argument("--yes", action="store_true", help="do not confirm each device")
    ap.add_argument("--dry-run", action="store_true", help="identify only; push nothing")
    ap.add_argument("--iface", default="en0")
    ap.add_argument("--connect-timeout", type=int, default=30)
    ap.add_argument("--safety-timeout", type=int, default=180)
    ap.add_argument("--max-attempts", type=int, default=2)
    ap.add_argument("--verify-deadline", type=int, default=90)
    args = ap.parse_args()

    home_ssid = args.home_ssid or args.ssid
    plat = MacPlatform(args.iface)
    patterns = DEFAULT_PATTERNS + args.ssid_pattern

    # Pre-flight: know what is already here BEFORE touching the radio. This is a correctness input
    # — a device found now is left alone rather than needlessly rewritten.
    print("Scanning the current LAN first (devices already here are left alone)…", file=sys.stderr)
    lan_macs = lan_device_macs()
    print(f"  {len(lan_macs)} WLED device(s) already on this LAN", file=sys.stderr)

    nets, err = plat.scan()
    if err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    seen = {n["ssid"]: n for n in nets}
    for pat in patterns:                      # targeted probes: a sweep has been seen to miss one
        if "*" not in pat and "?" not in pat:
            hits, _ = plat.scan(pat)
            for h in hits:
                seen.setdefault(h["ssid"], h)

    candidates = []
    for n in seen.values():
        ok, why = is_candidate(n, patterns, not args.no_open_probe,
                               exclude=(home_ssid, args.ssid))
        if ok:
            candidates.append((n, why))

    if not candidates:
        print("No candidate APs found.", file=sys.stderr)
        return 0

    print(f"\n{len(candidates)} candidate AP(s):", file=sys.stderr)
    for n, why in candidates:
        print(f"  {n['ssid']:<28} rssi={n['rssi']:<5} ch={n['channel']:<3} {why}", file=sys.stderr)

    if args.dry_run:
        print("\n--dry-run: not joining anything.", file=sys.stderr)
        for n, why in candidates:
            print(f"  would try {n['ssid']}")
        return 0

    if not args.yes:
        try:
            if input(f"\nJoin these {len(candidates)} AP(s)? [y/N] ").strip().lower() != "y":
                print("aborted.", file=sys.stderr)
                return 1
        except EOFError:
            print("no tty for confirmation; use --yes", file=sys.stderr)
            return 1

    # Detached watchdog: survives SIGINT to this process group and even SIGKILL of this process,
    # which is the case a plain background child does not cover.
    dog = plat.watchdog(home_ssid, args.home_password, args.safety_timeout)
    attempts, report = {}, []
    try:
        for n, _why in candidates:
            provision_one(plat, n, args, lan_macs, attempts, report)
    except KeyboardInterrupt:
        print("\ninterrupted — restoring network…", file=sys.stderr)
    finally:
        # Ignore further interrupts so a second Ctrl-C cannot abandon the restore half-done.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        ok, err = plat.join(home_ssid, args.home_password, args.connect_timeout)
        back = plat.wait_online(45)
        if not back:                       # retry once before admitting failure
            plat.join(home_ssid, args.home_password, args.connect_timeout)
            back = plat.wait_online(45)
        if back:
            dog.terminate()       # only disarm once we are demonstrably back
            print(f"back on {home_ssid}.", file=sys.stderr)
        else:
            # Leave the watchdog ARMED. Disarming here and then claiming it might still help was
            # both wrong and actively harmful: it removed the only remaining recovery path.
            print(f"WARNING: could not confirm rejoin to {home_ssid} ({err or 'no address'}). "
                  f"Watchdog left armed — it will force a rejoin shortly.", file=sys.stderr)

    pushed = [r for r in report if r[1] == "pushed"]
    if pushed and back:
        print(f"\nWaiting up to {args.verify_deadline}s for {len(pushed)} device(s) to appear…",
              file=sys.stderr)
        # Verification must happen AFTER leaving the AP: WLED calls WiFi.disconnect() while an AP
        # client is associated and only retries once the client count hits zero.
        end = time.time() + args.verify_deadline
        want = {r[2].split(" ")[0] for r in pushed}
        while time.time() < end and want:
            found = set(lan_device_macs(timeout=1.0))
            want -= {m for m in want if m.lower() in found}
            if want:
                time.sleep(5)
        for r in pushed:
            mac = r[2].split(" ")[0]
            report.append((r[0], "joined" if mac not in want else "never-appeared", mac))

    print("\nSSID                          RESULT          DETAIL")
    print("-" * 78)
    for ssid, result, detail in report:
        print(f"{ssid:<28}  {result:<14}  {detail}")

    bad = [r for r in report if r[1] in ("join-failed", "push-failed", "never-appeared")]
    return 1 if bad or not back else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
