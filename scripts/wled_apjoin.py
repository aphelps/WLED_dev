#!/usr/bin/env python3
"""wled_apjoin — find WLED devices sitting in AP-fallback mode and put them on your network.

macOS only for now (see PORTING NOTES at the bottom). Standard library plus a small Swift helper
for scanning, because CoreWLAN is the only interface on macOS 26 that still returns SSIDs.

The dangerous part is not the HTTP: joining a device's AP takes this machine off its own network,
so every path must put it back. That is why the run is wrapped in a restore that executes on
success, failure, exception and signal, plus a detached watchdog that force-restores even if this
process is killed outright.

Usage:
  scripts/wled_apjoin.py --ssid MyNetwork --password secret --dry-run   # always start here
  scripts/wled_apjoin.py --ssid MyNetwork --password secret
  scripts/wled_apjoin.py ... --home-ssid OtherNetwork   # where THIS machine returns to
  scripts/wled_apjoin.py ... --yes                      # skip the confirmation prompt
  scripts/wled_apjoin.py ... --ssid-pattern 'Studio*'

Prerequisites, the output table, recovery if it leaves you off the network, and the known
limitations are all in scripts/README.md.
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
#
# All 331 prefixes IEEE has assigned to Espressif, not a hand-picked subset. That distinction is
# not academic: this started as 34 OUIs guessed from devices we happened to have seen, and the
# first live run against real hardware hit `WLED-TOUCH-BOX` on 2c:bc:bb — genuinely Espressif,
# genuinely missing — so a real WLED device was refused with "MAC is not Espressif". An allowlist
# that fails closed is the right shape, but only if it is actually complete; an incomplete one
# just means the tool does not work and blames the device.
ESPRESSIF_OUIS = {
    "004b12", "007007", "048308", "04b247", "083a8d", "083af2", "089272", "08a6f7", "08ad0a",
    "08b61f", "08d1f9", "08f9e0", "0c4ea0", "0c8b95", "0cb815", "0cdc7e", "10003b", "10061c",
    "1020ba", "1051db", "10521c", "1091a8", "1097bd", "10b41d", "10bda3", "140808", "142b2f",
    "14335c", "146393", "14c19f", "188b0e", "18fe34", "1c2904", "1c6920", "1c8f57", "1c9dc2",
    "1cc3ab", "1cdbd4", "202565", "2043a8", "20500d", "206ef1", "209ba9", "20e7c8", "240ac4",
    "244cab", "24587c", "2462ab", "246f28", "24a160", "24b2de", "24d7eb", "24dcc3", "24ec4a",
    "2805a5", "28372f", "28562f", "288485", "2c3ae8", "2cbcbb", "2cf432", "3030f9", "3076f5",
    "308398", "30aea4", "30c6f7", "30c922", "30eda0", "345f45", "348518", "34865d", "349454",
    "34987a", "34ab95", "34b472", "34b7da", "34cdb0", "38182b", "383e51", "3844be", "3c0f02",
    "3c6105", "3c71bf", "3c8427", "3c8a1f", "3cdc75", "3ce90e", "4022d8", "404cca", "409151",
    "40f520", "441793", "441bf6", "441d64", "44b176", "44bd8d", "4827e2", "4831b7", "483fda",
    "485519", "489d31", "48aff3", "48ca43", "48e729", "48f6ee", "4c11ae", "4c7525", "4cc382",
    "4cebd6", "500291", "50787d", "543204", "5443b2", "545aa6", "582abd", "588c81", "58bf25",
    "58cf79", "58e6c5", "5c013b", "5ccf7f", "600194", "6055f9", "64b708", "64e833", "680947",
    "6825dd", "686725", "689dd2", "68b6b3", "68c63a", "68ee8f", "68fe71", "6cb456", "6cc840",
    "70039f", "70041d", "704bca", "70af09", "70b8f6", "744dbd", "781c3c", "782184", "78421c",
    "78e36d", "78ee4c", "7c0c5f", "7c2c67", "7c4fad", "7c7398", "7c87ce", "7c9ebd", "7cd544",
    "7cdfa1", "7ce8b1", "80456b", "8053e0", "80646f", "806599", "807d3a", "80b54e", "80f1b2",
    "80f3da", "840d8e", "841fe8", "84c7bb", "84cca8", "84f3eb", "84f703", "84fce6", "8813bf",
    "8856a6", "885721", "88f155", "8c4b14", "8c4f00", "8c8c29", "8c94df", "8caab5", "8cbfea",
    "8cce4e", "8cfd49", "901506", "90380c", "90649b", "907069", "9097d5", "90b339", "90da72",
    "90e5b1", "943cc6", "9451dc", "9454c5", "94a990", "94b555", "94b97e", "94e686", "983dae",
    "9888e0", "98a316", "98c377", "98cdac", "98f4ab", "9c139e", "9c9c1f", "9c9e6e", "9ccc01",
    "a020a6", "a0764e", "a085e3", "a0a3b3", "a0b765", "a0dd6c", "a0f262", "a47b9d", "a4cb8f",
    "a4cf12", "a4e57c", "a4f00f", "a8032a", "a842e3", "a84674", "a848fa", "ac0bfb", "ac1518",
    "ac276e", "ac67b2", "aca704", "acd074", "acebe6", "b03fd3", "b08184", "b0a604", "b0a732",
    "b0b21c", "b0cbd8", "b43a45", "b48a0a", "b4a64a", "b4bfe9", "b4e62d", "b81f3f", "b87b4d",
    "b8d61a", "b8f009", "b8f862", "bcddc2", "bcff4d", "c049ef", "c04e30", "c05d89", "c0cdd6",
    "c44f33", "c45bbe", "c49e7e", "c4d8d5", "c4dd57", "c4dee2", "c82b96", "c82e18", "c88541",
    "c88a7b", "c8c9a3", "c8f09e", "cc50e3", "cc68c7", "cc7b5c", "cc7e1f", "cc8da2", "ccba97",
    "ccdba7", "d0cf13", "d0ef76", "d40592", "d48afc", "d48c49", "d4d4da", "d4e9f4", "d4f98d",
    "d8132a", "d83bda", "d885ac", "d8a01d", "d8bc38", "d8bfc0", "d8f15b", "dc0675", "dc0a69",
    "dc1ed5", "dc4f22", "dc5475", "dcb4d9", "dcda0c", "e05a1b", "e072a1", "e08cfe", "e09806",
    "e0e2e6", "e465b8", "e4b063", "e4b323", "e80690", "e831cd", "e83dc1", "e868e7", "e86bea",
    "e89f6d", "e8db84", "e8f60a", "ec6260", "ec64c9", "ec94cb", "ecc9ff", "ecda3b", "ece334",
    "ecfabc", "f008d1", "f0161d", "f024f9", "f09e9e", "f0f5bd", "f412fa", "f42dc9", "f4650b",
    "f4cfa2", "f85b1b", "f8b3b7", "fc012c", "fcb467", "fce8c0", "fcf5c4",
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


AP_SUBNET = "4.3.2."         # every WLED AP leases its client from this subnet
ESP_AP_SUBNET = "192.168.4." # ESP-IDF default softAP lease — Tasmota/ESPHome setup-mode APs


def ensure_off_device_ap(plat, home_ssid, home_password, timeout, home_addr=None):
    """Known radio state between hops. A host still associated to a device AP (after any
    no-reboot verdict — nothing in provision_one disassociates, forget() only edits the
    preferred list) does two kinds of damage: WLED holds its own join retry until the AP client
    count hits zero, so the grace window spends itself preventing the join it is waiting for;
    and the next hop starts with a before_addr the next AP leases again, so the address-change
    check reports "never left" against a join that worked. Not WLED-specific: the open-probe
    also visits stranger ESP APs, which lease from ESP-IDF's default 192.168.4.x — so the test
    is "demonstrably back on the pre-loop home address", not "off WLED's subnet". Without a home
    reference, only a known device-AP subnet forces the rejoin. Returns True once off."""
    addr = plat.current_address()
    if addr and addr == home_addr:
        return True
    if addr and not home_addr and not addr.startswith((AP_SUBNET, ESP_AP_SUBNET)):
        return True
    plat.join(home_ssid, home_password, timeout)
    return bool(plat.wait_online(30))


def watchdog_hop_budget(args):
    """Worst case for ONE candidate: every join retry timing out with a full DHCP wait, the push
    confirm polled twice, plus a flat allowance for identify retries and the finally-restore
    (two join rounds with 45s online-waits ≈ 150s)."""
    joins = max(1, args.join_retries) * (args.connect_timeout + args.ap_settle_timeout
                                         + args.join_retry_delay)
    return int(joins + 2 * args.push_confirm_timeout + args.write_settle + 180)


# A push is followed by a device reboot; the AP staying visible inside this window is normal
# (mid-reboot), not evidence the push failed. Only after the grace has passed does a
# still-broadcasting device earn another push (and, eventually, give-up).
PUSH_GRACE_S = 90


def decide_action(mac, lan_macs, attempts, max_attempts, last_push=None, now=None):
    """What to do with an identified device. Returns (action, reason).

    Adam's rule: skip only if it is on the CURRENT LAN; a device on a *different* network gets
    re-pointed onto this one. `lan_macs` therefore has to be a complete scan of the current LAN —
    if it is incomplete, working devices get needlessly rewritten.

    Finality is per-DEVICE (MAC), not per-SSID: two stock devices both broadcast `WLED-AP`, so
    retiring the SSID after the first push would strand the second. A MAC pushed within
    PUSH_GRACE_S gets "waiting" (non-terminal — the SSID stays eligible for the *other* device);
    one still broadcasting after the grace gets pushed again until `max_attempts` retires it.
    """
    if mac and mac.lower() in {m.lower() for m in lan_macs}:
        return "skip", "already on this LAN"
    # Grace BEFORE give-up: the one state the grace exists for is "final allowed push seconds
    # ago, mid-reboot" — reaching give-up (terminal) there would retire the shared SSID exactly
    # when that push is about to succeed, stranding a second device behind the same name.
    since = None if not last_push or mac not in last_push else (now or time.time()) - last_push[mac]
    if since is not None and since < PUSH_GRACE_S:
        return "waiting", f"pushed {int(since)}s ago — waiting for it to appear"
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

    def current_address(self):
        """The interface's current IPv4 address, or "" if it has none."""
        try:
            r = subprocess.run(["ipconfig", "getifaddr", self.iface],
                               capture_output=True, text=True, timeout=5)
            return (r.stdout or "").strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    def wait_for_address(self, deadline_s=20, different_from=None):
        """Block until the interface actually has an address. Returns it, or None on timeout.

        `different_from` guards the case this function was originally written without: a join that
        reports success but silently leaves us on the network we started on. Any address then
        satisfies "we have an address", instantly — and the run goes on to probe 4.3.2.1 from the
        home subnet, gets nothing, and reports the device as `not-wled  no /json/info response`.
        That is the same lie as the bug this function was added to fix, one layer up: a radio
        failure wearing a verdict about the device. Requiring the address to CHANGE is what
        distinguishes "associated somewhere new" from "never went anywhere".

        Associating is not the same as being able to talk: `networksetup -setairportnetwork`
        returns once the association is up, but DHCP has not run yet, so an HTTP request issued
        immediately afterwards fails with no route. That failure is indistinguishable from "this
        AP is not a WLED device" in the report, which is exactly how it presented live — the same
        device answered on one run and came back `not-wled  no /json/info response` on the next,
        purely on timing. Waiting for the lease removes the race rather than papering over it with
        a longer HTTP timeout, which would not help: there is no route to time out against.

        Deliberately accepts any non-link-local address rather than requiring 4.3.2.x — a stranger
        AP hands out something else entirely, and we still want to probe it and get a clean
        `not-wled` rather than sitting here until the deadline.
        """
        end = time.time() + deadline_s
        while time.time() < end:
            addr = self.current_address()
            if addr and not addr.startswith("169.254."):   # 169.254 = associated, no lease yet
                if not (different_from and addr == different_from):
                    return addr
            time.sleep(0.5)
        return None

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


def confirm_cfg(host, want_ssid, want_psk_len, deadline_s=12):
    """Poll GET /json/cfg until the device reports back the credentials we pushed. True if stuck.

    Checks the passphrase too, via `pskl`. WLED never echoes the PSK — it returns the stored
    *length* instead — and that is enough to catch the failure that matters: an SSID that saved
    while the passphrase did not, which produces a device that looks correctly configured and can
    never authenticate. Confirming only the SSID would report that as success.

    Conservative on failure to read: if /json/cfg cannot be fetched at all (a PIN is set, or the
    endpoint is unavailable), we cannot confirm and return False rather than assuming success —
    the caller then retries the push and, failing that, reports it honestly instead of rebooting
    a device into a config that may never have been saved.
    """
    end = time.time() + deadline_s
    while time.time() < end:
        cfg = get_json(host, "/json/cfg")
        try:
            ins = cfg["nw"]["ins"][0]
            # `pskl` is absent on older builds; treat missing as "cannot disprove" rather than
            # failing a device that is actually fine.
            psk_ok = ins.get("pskl", want_psk_len) == want_psk_len
            if ins["ssid"] == want_ssid and psk_ok:
                return True
        except (KeyError, IndexError, TypeError):
            pass
        time.sleep(1.0)
    return False


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
def provision_one(plat, net, args, lan_macs, attempts, report, last_push=None):
    """Join one AP, identify it, maybe push credentials, leave. Returns after restoring nothing —
    the caller owns restoration, so a failure here can never skip it."""
    ssid = net["ssid"]
    before_addr = plat.current_address()      # what "we moved" is measured against
    try:
        # Retry the join in place rather than waiting for the next scan. macOS serves cached
        # scan results — repeat sweeps come back in well under a second — so a candidate can be
        # a cache artifact of an AP that has already gone, and the join then fails with "Could
        # not find network" against an AP the scan just reported. Re-scanning to retry costs
        # seconds and re-reads the same cache; retrying the join directly is both faster and
        # actually samples the radio. Everything is inside the try so the finally still forgets
        # the SSID: even a failed join can add it to the preferred list, which the OS would
        # then auto-join later on its own.
        joined, err = False, None
        tries = max(1, args.join_retries)
        for i in range(tries):
            ok, jerr = plat.join(ssid, None if net.get("open") else args.ap_password,
                                 args.connect_timeout)
            if not ok:
                err = jerr or "?"
            # Wait for DHCP before speaking IP, and require the address to have CHANGED — see
            # MacPlatform.wait_for_address for why "we have an address" alone is not enough.
            elif plat.wait_for_address(args.ap_settle_timeout, different_from=before_addr):
                joined = True
                break
            else:
                err = (f"never left {before_addr or 'the previous network'} — the join "
                       f"reported success but the address did not change")
            if i + 1 < tries:
                time.sleep(args.join_retry_delay)
        if not joined:
            report.append((ssid, "join-failed",
                           f"{err or '?'} (after {tries} attempt{'s' if tries != 1 else ''})"))
            return
        info = get_json(DEVICE_IP, "/json/info")
        if info is None:
            # One retry: the lease can land a beat before the device's HTTP server is serving.
            time.sleep(2.0)
            info = get_json(DEVICE_IP, "/json/info", timeout=8)
        good, mac, reason = identify(info)
        if not good:
            # Not ours: leave immediately, having sent nothing.
            report.append((ssid, "not-wled", reason))
            return
        if args.inspect:
            # Read-only diagnosis. --dry-run answers "which APs would I touch?" without using the
            # radio at all; this answers "why is that device refusing to join?", which can only be
            # learned from the device itself. Nothing is ever sent — it returns before any push.
            cfg = get_json(DEVICE_IP, "/json/cfg") or {}
            apc = cfg.get("ap", {})
            # EVERY entry, with its static-IP fields. Reading only ins[0]'s ssid/pskl hides two
            # things that look identical from outside: a second configured network taking
            # priority, and a leftover STATIC IP. A stale static address is the nastier one — the
            # device associates perfectly and is then simply unreachable on the wrong subnet, which
            # presents exactly like "it never joined". build_wifi_cfg writes only ssid/psk, so such
            # an address survives a credential push untouched.
            entries = []
            for i, e in enumerate(cfg.get("nw", {}).get("ins") or [{}]):
                ip = e.get("ip") or [0, 0, 0, 0]
                static = ".".join(str(o) for o in ip) if any(ip) else "dhcp"
                entries.append(f"[{i}] ssid={e.get('ssid')!r} pskl={e.get('pskl')} addr={static}")
            # Filesystem usage and uptime, because a config read-back proves less than it looks.
            # GET /json/cfg serves the IN-MEMORY config, so it returns what we just pushed whether
            # or not cfg.json was actually written. If LittleFS is full or unwritable the push
            # "confirms", survives until the next real power cycle, and then reverts — which is
            # exactly the observed behaviour. fs.u vs fs.t shows whether there is room to write,
            # and uptime says whether the /reset we issued actually rebooted anything.
            fs = info.get("fs") or {}
            report.append((ssid, "inspected",
                           f"{mac} ver={info.get('ver')} up={info.get('uptime')}s "
                           f"fs={fs.get('u')}/{fs.get('t')}kB heap={info.get('freeheap')} "
                           f"ap.behav={apc.get('behav')} ap.chan={apc.get('chan')} "
                           f"nw={' '.join(entries)}"))
            return
        action, why = decide_action(mac, lan_macs, attempts, args.max_attempts, last_push)
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
        # Read the SSID back before rebooting. POST /json/cfg does NOT write the file inline: it
        # applies the values and flags a serialize that happens on a later main-loop pass, so a
        # /reset issued immediately can reboot the device before cfg.json is written and the
        # credentials are simply lost. That is not theoretical — of the two devices adopted in the
        # first successful live run, one came back up still broadcasting its AP, having accepted
        # the push a moment earlier. Polling the value back is the only honest confirmation; a
        # fixed sleep just moves the race.
        pskl = len(args.password or "")
        if not confirm_cfg(DEVICE_IP, args.ssid, pskl, args.push_confirm_timeout):
            post_json(DEVICE_IP, "/json/cfg", body)          # one more try
            if not confirm_cfg(DEVICE_IP, args.ssid, pskl, args.push_confirm_timeout):
                report.append((ssid, "push-failed", "config did not persist (never read back)"))
                return
        # Settle before rebooting. The read-back above proves the values are in RAM, NOT that
        # cfg.json was written: GET /json/cfg serves the in-memory config, so it answers correctly
        # whether or not the file landed. WLED defers the actual write to a later main-loop pass,
        # so confirming on the first poll and resetting immediately can still beat the write —
        # which presents as a device that accepts the push, reads it back, and reverts to its old
        # network on the next real power cycle. Observed on WLED-TOUCH-MATRIX, whose filesystem
        # has plenty of free space (24/983kB), so this is a race and not a full disk.
        time.sleep(args.write_settle)
        get_json(DEVICE_IP, "/reset")     # /json/cfg does not reboot on its own
        attempts[mac] = attempts.get(mac, 0) + 1
        if last_push is not None:
            last_push[mac] = time.time()
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
    ap.add_argument("--yes", action="store_true",
                    help="skip the confirmation prompt (one prompt covers the whole candidate list)")
    ap.add_argument("--dry-run", action="store_true", help="identify only; push nothing")
    ap.add_argument("--inspect", action="store_true",
                    help="join and report each device's config, pushing nothing "
                         "(unlike --dry-run this does use the radio)")
    ap.add_argument("--iface", default="en0")
    ap.add_argument("--connect-timeout", type=int, default=30)
    ap.add_argument("--join-retries", type=int, default=4,
                    help="attempts to associate to one AP before moving on (its AP may have "
                         "vanished between the scan and the join)")
    ap.add_argument("--join-retry-delay", type=float, default=4.0,
                    help="seconds between those attempts")
    ap.add_argument("--ap-settle-timeout", type=int, default=20,
                    help="seconds to wait for DHCP after associating to a device AP")
    ap.add_argument("--adopt-deadline", type=int, default=0, metavar="SECONDS",
                    help="keep re-scanning and retrying for this long, because a device in "
                         "AP-fallback drops its AP while it retries its own connection "
                         "(0 = single pass)")
    ap.add_argument("--rescan-interval", type=int, default=15,
                    help="seconds between re-scans while waiting for an AP to reappear")
    ap.add_argument("--safety-timeout", type=int, default=180)
    ap.add_argument("--max-attempts", type=int, default=2)
    ap.add_argument("--verify-deadline", type=int, default=90)
    ap.add_argument("--push-confirm-timeout", type=int, default=12,
                    help="seconds to wait for the pushed config to read back "
                         "before rebooting the device")
    ap.add_argument("--write-settle", type=float, default=6.0,
                    help="seconds to wait after the config reads back before rebooting, so the "
                         "deferred cfg.json write can land (the read-back only proves RAM)")
    args = ap.parse_args()

    home_ssid = args.home_ssid or args.ssid
    plat = MacPlatform(args.iface)
    patterns = DEFAULT_PATTERNS + args.ssid_pattern

    # The watchdog is re-armed per hop (see the adopt loop), so its budget only has to cover ONE
    # candidate — join retries, identify/push/confirm, and the two-round restore in `finally` —
    # not the whole adoption window. Sizing it off the actual per-hop knobs keeps it meaningful:
    # a single run-wide budget was either undersized (fired mid-run, force-rejoining home and
    # mislabelling later joins) or so large it no longer protected anything.
    hop_budget = max(args.safety_timeout, watchdog_hop_budget(args))
    if hop_budget != args.safety_timeout:
        print(f"note: watchdog per-hop budget {hop_budget}s (--safety-timeout {args.safety_timeout}s "
              f"is smaller than one worst-case hop plus restore)", file=sys.stderr)

    # Pre-flight: know what is already here BEFORE touching the radio. This is a correctness input
    # — a device found now is left alone rather than needlessly rewritten.
    print("Scanning the current LAN first (devices already here are left alone)…", file=sys.stderr)
    lan_macs = lan_device_macs()
    print(f"  {len(lan_macs)} WLED device(s) already on this LAN", file=sys.stderr)

    def find_candidates(exclude_ssids=()):
        """One scan pass → [(net, why)]. Broken out because the AP set is not stable: a device in
        AP-fallback drops its SoftAP each time it retries its STA connection, so a candidate seen
        in the opening scan is routinely gone by the time we try to join it, and one that was
        absent appears a minute later. Confirmed live on two devices. The caller re-runs this."""
        nets, err = plat.scan()
        if err:
            return None, err
        seen = {n["ssid"]: n for n in nets}
        for pat in patterns:                  # targeted probes: a sweep has been seen to miss one
            if "*" not in pat and "?" not in pat:
                hits, _ = plat.scan(pat)
                for h in hits:
                    seen.setdefault(h["ssid"], h)
        out = []
        for n in seen.values():
            if n["ssid"] in exclude_ssids:
                continue
            ok, why = is_candidate(n, patterns, not args.no_open_probe,
                                   exclude=(home_ssid, args.ssid))
            if ok:
                out.append((n, why))
        return out, None

    # One deadline for the whole run, started here rather than after discovery. With flapping APs
    # the opening scan finding nothing is a normal starting state, not a reason to quit — quitting
    # there is why a `--adopt-deadline` run could exit immediately having done nothing at all.
    deadline = time.time() + args.adopt_deadline

    candidates, err = find_candidates()
    if err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    scan_errs = 0
    while not candidates and time.time() < deadline:
        print(f"  …no candidate APs yet, waiting ({int(deadline - time.time())}s left)",
              file=sys.stderr)
        time.sleep(args.rescan_interval)
        candidates, err = find_candidates()
        if err:
            # A scan that starts failing persistently (swift gone, Location Services revoked,
            # Wi-Fi powered off) must not decay into "No candidate APs found." exit 0.
            scan_errs += 1
            if scan_errs >= 3:
                print(f"error: scans failing persistently: {err}", file=sys.stderr)
                return 2
            candidates = []
        else:
            scan_errs = 0

    if not candidates:
        if err:
            print(f"error: last scan failed: {err}", file=sys.stderr)
            return 2
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
    dog = plat.watchdog(home_ssid, args.home_password, hop_budget)
    # The between-hop detach compares against this, not against a subnet list: it is captured
    # while the host is still on its own network (the LAN pre-flight just ran from it).
    home_addr = plat.current_address()
    attempts, last_push, report = {}, {}, []
    # A verdict that says something about the DEVICE is final; one that says something about the
    # RADIO is not. `join-failed` is the flapping case — the AP was there during the scan and gone
    # a second later — so that SSID stays eligible and we come back for it. Without this split the
    # run either gives up on a device that is merely mid-retry, or re-pushes to one it already did.
    # `pushed` is deliberately NOT here: it retires a MAC (via decide_action's grace/attempts),
    # never the SSID — a second stock device broadcasts the same `WLED-AP` and still needs a turn.
    TERMINAL = {"not-wled", "skip", "give-up", "push-failed", "inspected"}
    done, empty_rounds, scan_broken, radio_stuck = set(), 0, False, False
    try:
        while True:
            for n, _why in candidates:
                if n["ssid"] in done:
                    continue
                # Fresh watchdog per hop: one that has fired (or been consumed by an earlier
                # hop's slow join) protects nothing for the hops after it.
                dog.terminate()
                dog = plat.watchdog(home_ssid, args.home_password, hop_budget)
                mark = len(report)
                provision_one(plat, n, args, lan_macs, attempts, report, last_push)
                if any(r[1] in TERMINAL for r in report[mark:]):
                    done.add(n["ssid"])
                if not ensure_off_device_ap(plat, home_ssid, args.home_password,
                                            args.connect_timeout, home_addr):
                    print("WARNING: could not detach from the device AP; "
                          "stopping adoption early.", file=sys.stderr)
                    radio_stuck = True
                    break
                # Refresh the reference after a successful detach: a mid-run DHCP lease change
                # would otherwise fail the equality check on every later hop and pay a
                # redundant join + wait_online each time.
                if home_addr:
                    home_addr = plat.current_address() or home_addr
            if radio_stuck:
                break

            if time.time() >= deadline:
                break
            # Stop once things have settled: everything seen has a final verdict and two further
            # scans turned up nothing new. Two rather than one because a single empty scan means
            # very little when the APs are known to blink in and out.
            candidates, serr = find_candidates(exclude_ssids=done)
            if serr:
                scan_errs += 1
                if scan_errs >= 3:
                    print(f"WARNING: scans failing persistently ({serr}); "
                          f"stopping adoption early.", file=sys.stderr)
                    scan_broken = True
                    break
                candidates = []
            else:
                scan_errs = 0
            if candidates:
                empty_rounds = 0
                print(f"  …{len(candidates)} AP(s) back in range: "
                      f"{', '.join(c[0]['ssid'] for c in candidates)}", file=sys.stderr)
            else:
                empty_rounds += 1
                if empty_rounds >= 2 and (done or last_push):
                    break
                remaining = int(deadline - time.time())
                print(f"  …waiting for APs to reappear ({remaining}s left)", file=sys.stderr)
                time.sleep(args.rescan_interval)
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
    if scan_broken:
        return 2
    return 1 if bad or radio_stuck or not back else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
