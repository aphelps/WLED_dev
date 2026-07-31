#!/usr/bin/env python3
"""wled-scan — find WLED devices on the LAN and/or Tailscale and table their configuration.

Standard library only: no pip install, nothing to keep in sync with a requirements file.

Discovery is a concurrent HTTP sweep rather than mDNS. mDNS is the obvious choice and it is not
used deliberately: the macOS and Linux browse tools stream until interrupted and have no portable
one-shot mode, and devices on a Tailscale subnet are not on the local multicast domain at all, so
mDNS cannot see them. A sweep of /json/info is uniform across both, and finishes in a few seconds.

Usage:
  wled-scan                     # LAN (this machine's subnet) + Tailscale peers
  wled-scan --lan               # LAN only
  wled-scan --tailscale         # Tailscale only
  wled-scan --subnet 10.0.0.0/24    # an explicit range (repeatable)
  wled-scan --host wled.local       # probe specific hosts (repeatable)
  wled-scan --json              # machine-readable
  wled-scan --timeout 2.0       # per-request seconds (default 1.0)
"""

import argparse
import concurrent.futures
import ipaddress
import json
import re
import socket
import subprocess
import sys
import urllib.error
import urllib.request

# --- WLED bus types (WLED/wled00/const.h). Two-pin types take [data, clock]. -------------------
BUS_TYPES = {
    0: "none", 22: "WS2812", 23: "GS8608", 24: "WS2811-400k", 25: "TM1829",
    26: "UCS8903", 27: "APA106", 28: "FW1906", 29: "UCS8904", 30: "SK6812",
    31: "TM1814", 32: "WS2805", 33: "TM1914", 34: "SM16825", 21: "WS2812-WWA",
    40: "on/off", 41: "PWM-1ch", 42: "PWM-2ch", 43: "PWM-3ch", 44: "PWM-4ch",
    45: "PWM-5ch", 46: "PWM-6ch",
    50: "WS2801", 51: "APA102", 52: "LPD8806", 53: "P9813", 54: "LPD6803",
}
TWO_PIN_MIN, TWO_PIN_MAX = 48, 63


def bus_name(t):
    return BUS_TYPES.get(t, f"type{t}")


def is_two_pin(t):
    return TWO_PIN_MIN <= t <= TWO_PIN_MAX


def get_json(host, path, timeout):
    url = f"http://{host}{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            if r.status != 200:
                return None
            return json.loads(r.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None


def probe(host, timeout):
    """Return info dict if `host` is a WLED device, else None."""
    info = get_json(host, "/json/info", timeout)
    # `brand` is the reliable marker; stock and forks both set it. Fall back to the shape of the
    # payload so an unusual build is not missed.
    if not isinstance(info, dict):
        return None
    if info.get("brand") != "WLED" and "leds" not in info:
        return None
    info["_host"] = host
    return info


# --- target enumeration ------------------------------------------------------------------------
def local_subnets():
    """This machine's IPv4 subnets, as networks, skipping loopback and Tailscale's own range."""
    nets = []
    try:
        out = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        out = ""
    if not out:  # Linux without ifconfig
        try:
            out = subprocess.run(["ip", "-4", "addr"], capture_output=True, text=True,
                                 timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            return nets

    # ifconfig: "inet 10.1.10.248 netmask 0xffffff00"   |   ip: "inet 10.1.10.248/24"
    for m in re.finditer(r"inet (\d+\.\d+\.\d+\.\d+)(?:/(\d+))?(?:\s+netmask\s+(\S+))?", out):
        ip, cidr, mask = m.group(1), m.group(2), m.group(3)
        if ip.startswith("127."):
            continue
        if ip.startswith("100."):     # Tailscale CGNAT space — enumerated from its own API
            continue
        try:
            if cidr:
                prefix = int(cidr)
            elif mask:
                mask_int = int(mask, 16) if mask.startswith("0x") else int(
                    ipaddress.IPv4Address(mask))
                prefix = bin(mask_int).count("1")
            else:
                continue
            if prefix < 22:           # refuse to sweep anything huge by accident
                continue
            nets.append(ipaddress.ip_network(f"{ip}/{prefix}", strict=False))
        except ValueError:
            continue
    return nets


def tailscale_peers():
    """Tailscale peer IPs (and self), or [] if tailscale is unavailable."""
    for cmd in (["tailscale", "status", "--json"],
                ["/Applications/Tailscale.app/Contents/MacOS/Tailscale", "status", "--json"]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            continue
        if out.returncode != 0 or not out.stdout.strip():
            continue
        try:
            data = json.loads(out.stdout)
        except json.JSONDecodeError:
            continue
        ips = []
        for section in ("Self", "Peer"):
            node = data.get(section)
            if not node:
                continue
            nodes = node.values() if section == "Peer" else [node]
            for n in nodes:
                if not n.get("Online", True):
                    continue
                ips.extend(n.get("TailscaleIPs") or [])
        return [i for i in ips if ":" not in i]     # IPv4 only
    return []


# --- config extraction -------------------------------------------------------------------------
def describe(info, timeout):
    host = info["_host"]
    cfg = get_json(host, "/json/cfg", timeout) or {}
    hw = cfg.get("hw", {})
    led = hw.get("led", {})

    buses = []
    for b in led.get("ins", []) or []:
        t = b.get("type", 0)
        pins = b.get("pin", []) or []
        pin_s = ",".join(str(p) for p in pins)
        label = f"{bus_name(t)}[{pin_s}]"
        if is_two_pin(t) and len(pins) < 2:
            label += "!"          # clocked type missing its clock pin
        buses.append(f"{label}x{b.get('len', '?')}")

    matrix = led.get("matrix") or {}
    panels = matrix.get("panels") or []
    if panels:
        p = panels[0]
        mat = f"{p.get('w','?')}x{p.get('h','?')}" + (f" x{len(panels)}" if len(panels) > 1 else "")
    else:
        mat = "-"

    i2c = hw.get("if", {}).get("i2c-pin") or []
    sync = []
    if cfg.get("nw", {}).get("espnow"):
        sync.append("espnow")
    if cfg.get("if", {}).get("sync", {}).get("espnow"):
        sync.append("wled-sync")

    up = info.get("uptime")
    if isinstance(up, int):
        uptime = f"{up // 86400}d{(up % 86400) // 3600}h" if up >= 86400 else \
                 f"{up // 3600}h{(up % 3600) // 60}m" if up >= 3600 else f"{up // 60}m"
    else:
        uptime = "?"

    build = info.get("git") or "-"
    if info.get("branch") and info["branch"] not in ("HEAD", ""):
        build += f"@{info['branch']}"

    return {
        "name": info.get("name") or "?",
        "host": host,
        "ver": info.get("ver") or "?",
        "build": build,
        "leds": info.get("leds", {}).get("count", "?"),
        "buses": "; ".join(buses) or "-",
        "matrix": mat,
        "i2c": ",".join(str(p) for p in i2c) if i2c else "-",
        "sync": "+".join(sync) or "-",
        "uptime": uptime,
        "_info": info,
        "_cfg": cfg,
    }


# --- output ------------------------------------------------------------------------------------
COLUMNS = [
    ("name", "NAME"), ("host", "ADDRESS"), ("ver", "VER"), ("build", "BUILD"),
    ("leds", "LEDS"), ("buses", "BUSES (type[pins]xN)"), ("matrix", "MATRIX"),
    ("i2c", "I2C"), ("sync", "SYNC"), ("uptime", "UP"),
]


def table(rows):
    if not rows:
        return "No WLED devices found."
    widths = {k: len(h) for k, h in COLUMNS}
    for r in rows:
        for k, _ in COLUMNS:
            widths[k] = max(widths[k], len(str(r[k])))
    line = "  ".join(h.ljust(widths[k]) for k, h in COLUMNS)
    out = [line, "  ".join("-" * widths[k] for k, _ in COLUMNS)]
    for r in sorted(rows, key=lambda x: x["name"].lower()):
        out.append("  ".join(str(r[k]).ljust(widths[k]) for k, _ in COLUMNS))
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="Find WLED devices and table their configuration.")
    ap.add_argument("--lan", action="store_true", help="scan local subnets only")
    ap.add_argument("--tailscale", action="store_true", help="scan Tailscale peers only")
    ap.add_argument("--subnet", action="append", default=[], metavar="CIDR",
                    help="explicit range to sweep (repeatable)")
    ap.add_argument("--host", action="append", default=[], metavar="HOST",
                    help="probe a specific host (repeatable)")
    ap.add_argument("--timeout", type=float, default=1.0, help="per-request seconds (default 1.0)")
    ap.add_argument("--workers", type=int, default=64, help="concurrent probes (default 64)")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = ap.parse_args()

    do_lan = args.lan or not (args.lan or args.tailscale or args.subnet or args.host)
    do_ts = args.tailscale or not (args.lan or args.tailscale or args.subnet or args.host)

    targets, sources = [], []
    for h in args.host:
        targets.append(h)
    if args.host:
        sources.append(f"{len(args.host)} host(s)")

    nets = [ipaddress.ip_network(s, strict=False) for s in args.subnet]
    if do_lan:
        nets += local_subnets()
    for n in nets:
        targets += [str(ip) for ip in n.hosts()]
    if nets:
        sources.append(", ".join(str(n) for n in nets))

    if do_ts:
        ts = tailscale_peers()
        targets += ts
        sources.append(f"tailscale ({len(ts)} peer(s))" if ts else "tailscale (unavailable)")

    # de-dupe, keep order
    seen, ordered = set(), []
    for t in targets:
        if t not in seen:
            seen.add(t)
            ordered.append(t)

    if not ordered:
        print("Nothing to scan — no local subnet detected and no Tailscale peers.", file=sys.stderr)
        return 1

    if not args.json:
        print(f"Scanning {len(ordered)} address(es): {'; '.join(sources)}", file=sys.stderr)

    found = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        for info in ex.map(lambda h: probe(h, args.timeout), ordered):
            if info:
                found.append(info)

    # config fetch is a second, smaller round — only for confirmed devices
    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(16, args.workers)) as ex:
        for row in ex.map(lambda i: describe(i, max(args.timeout, 3.0)), found):
            rows.append(row)

    if args.json:
        print(json.dumps([{k: v for k, v in r.items() if not k.startswith("_")} for r in rows],
                         indent=2))
    else:
        print(table(rows))
        flagged = [r for r in rows if "!" in r["buses"]]
        if flagged:
            print("\n!  clocked LED type with fewer than 2 pins — needs [data, clock] on one bus:")
            for r in flagged:
                print(f"     {r['name']} ({r['host']}): {r['buses']}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
