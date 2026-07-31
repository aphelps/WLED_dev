#!/usr/bin/env python3
"""wled_sync — drive every WLED device on the LAN to the same effect, colour and palette.

Discovery reuses skills/wled-scan/wled-scan.py. Standard library only; Python 3.9 compatible.

The interesting parts are not the HTTP calls:

  * Effect and palette NUMBERS are firmware-specific (220 effects on 16.0.1, 187 on 0.14.4), so
    the same index is a different effect on different devices. Everything is therefore resolved
    BY NAME, per device, from that device's own /json/eff and /json/pal.

  * A normal state write makes the receiving device re-broadcast the raw index bytes to the whole
    fleet over WLED's own UDP sync, which would undo the name resolution through a side channel.
    Every request carries udpn.nn=true to suppress that.

  * Palette IDs are not one contiguous list: fixed palettes are 0..N from /json/pal, usermod
    palettes are 255-j, custom are 200-k. Position in /json/pal is only the ID for the fixed block.

Usage:
  wled_sync.py --effect Hiphotic
  wled_sync.py --effect Blink --palette "Random Cycle" --color '#FF0000'
  wled_sync.py --effect Hiphotic --dry-run
  wled_sync.py --effect Hiphotic --tailscale        # include Tailscale peers (off by default)
"""

import argparse
import concurrent.futures
import importlib.util
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
SCAN_PATH = os.path.join(REPO_ROOT, "skills", "wled-scan", "wled-scan.py")

# Palette ID bases (WLED/wled00/const.h). Only the fixed block is addressed by list position.
USERMOD_PALETTE_ID_BASE = 255
CUSTOM_PALETTE_ID_BASE = 200

# The reserved-slot placeholder in /json/eff. The only duplicated name in either table, and never
# a legitimate target — WLED uses it to keep effect indices stable across gaps.
RESERVED_EFFECT_NAME = "RSVD"

# Palettes whose output comes from the segment colours rather than a gradient. --color is only
# meaningful under one of these; everything else ignores col entirely for most effects.
COLOUR_DRIVEN_PALETTES = {
    "color 1", "colors 1&2", "color gradient", "colors only", "random cycle",
}


# --- pure logic (host-tested; no network) ------------------------------------------------------
def normalise(name):
    """Fold a user-supplied or device-supplied name for comparison.

    /json/pal decorates five entries with a leading '*' ('* Random Cycle', '* Color 1', ...), so a
    user typing "Random Cycle" must still match. Effect names are already '@'-stripped by WLED's
    serializeModeNames, so they need no equivalent handling.
    """
    if name is None:
        return ""
    return re.sub(r"\s+", " ", str(name).lstrip("*").strip()).lower()


def resolve_effect(name, eff_names):
    """Resolve an effect name to this device's index. Returns (index, error)."""
    want = normalise(name)
    if not want:
        return None, "no effect name given"
    for i, n in enumerate(eff_names):
        if n == RESERVED_EFFECT_NAME:
            continue                      # reserved gap filler, never selectable
        if normalise(n) == want:
            return i, None
    return None, f"effect {name!r} not present on this device"


def resolve_palette(name, pal_names, umpal_names=None):
    """Resolve a palette name to this device's palette ID. Returns (id, error).

    Fixed palettes are addressed by their position in /json/pal. Usermod palettes are NOT in that
    list — they are named in info.umpalnames and addressed at 255-j. Custom palettes (200-k) are
    not exposed by name at all and so cannot be selected this way.
    """
    want = normalise(name)
    if not want:
        return None, "no palette name given"
    for i, n in enumerate(pal_names):
        if normalise(n) == want:
            return i, None
    for j, n in enumerate(umpal_names or []):
        if normalise(n) == want:
            return USERMOD_PALETTE_ID_BASE - j, None
    return None, f"palette {name!r} not present on this device"


def parse_colour(text):
    """'#RRGGBB' / 'RRGGBB' / 'r,g,b' -> [r,g,b]. Returns (rgb, error)."""
    if text is None:
        return None, None
    t = str(text).strip()
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", t)
    if m:
        v = int(m.group(1), 16)
        return [(v >> 16) & 255, (v >> 8) & 255, v & 255], None
    parts = [p.strip() for p in t.split(",")]
    if len(parts) == 3:
        try:
            rgb = [int(p) for p in parts]
        except ValueError:
            return None, f"cannot parse colour {text!r}"
        if all(0 <= c <= 255 for c in rgb):
            return rgb, None
        return None, f"colour channels out of range in {text!r}"
    return None, f"cannot parse colour {text!r} (want #RRGGBB or r,g,b)"


def colour_is_moot(palette_name):
    """True when the chosen palette means most effects will ignore col[0].

    Under palette 0 ('Default') or any gradient palette the effect takes its colours from the
    palette, so --color has no visible result. Silently doing nothing reads as a tool bug, so
    callers warn instead.
    """
    n = normalise(palette_name)
    if not n:
        return False
    return n not in COLOUR_DRIVEN_PALETTES


def build_body(fx=None, pal=None, col=None, timebase=None, turn_on=True, verbose=True):
    """Assemble the POST body.

    Every field here is load-bearing:
      on    — a reachable device is powered, so light it. WLED restores its own briLast, so no
              brightness is imposed and per-device levels survive.
      tb    — shared phase anchor; strip.timebase = tb - millis(), so all devices agree on
              strip.now and animate in step.
      udpn.nn — suppress WLED's own UDP sync. WITHOUT THIS each device re-broadcasts the raw
              effect/palette index to the fleet, undoing the per-device name resolution.
      v     — makes WLED reply with the full serialized state instead of {"success":true}, so
              apply and verify are one round trip with no window for another writer.
      seg   — no 'id': applies to every selected segment, rather than assuming segment 0 is main.
    """
    seg = {}
    if fx is not None:
        seg["fx"] = fx
    if pal is not None:
        seg["pal"] = pal
    if col is not None:
        seg["col"] = [list(col)]

    body = {"udpn": {"nn": True}}
    if turn_on:
        body["on"] = True
    if timebase is not None:
        body["tb"] = int(timebase)
    if verbose:
        body["v"] = True
    if seg:
        body["seg"] = seg
    return body


def verify_applied(state, fx=None, pal=None, col=None):
    """Check a returned /json/state actually holds what we asked for. Returns list of mismatches.

    Necessary because a 200 does not mean applied: 'pal' is silently ignored for non-RGB segments,
    for one.
    """
    problems = []
    segs = (state or {}).get("seg") or []
    if not segs:
        return ["device reported no segments"]
    applied = [s for s in segs if s.get("sel", True)] or segs
    for s in applied:
        if fx is not None and s.get("fx") != fx:
            problems.append(f"seg{s.get('id')} fx={s.get('fx')} (wanted {fx})")
        if pal is not None and s.get("pal") != pal:
            problems.append(f"seg{s.get('id')} pal={s.get('pal')} (wanted {pal})")
        if col is not None:
            got = (s.get("col") or [[None]])[0]
            if list(got)[:3] != list(col)[:3]:
                problems.append(f"seg{s.get('id')} col={got} (wanted {list(col)})")
    return problems


# --- network -----------------------------------------------------------------------------------
def load_scanner():
    """Import wled-scan.py by path — its hyphen makes it an invalid module name for `import`."""
    spec = importlib.util.spec_from_file_location("wled_scan", SCAN_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load scanner at {SCAN_PATH}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def post_json(host, path, body, timeout):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"http://{host}{path}", data=data, method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
        try:
            return json.loads(raw), None
        except json.JSONDecodeError:
            return None, f"non-JSON response: {raw[:80]}"
    except (urllib.error.URLError, OSError) as e:
        return None, str(e)


def sync_one(scan, dev, args, timebase, timeout):
    """Resolve names and apply to one device. Returns a result row."""
    host = dev["_host"]
    name = dev.get("name") or host
    row = {"name": name, "host": host, "status": "", "detail": ""}

    eff_names = scan.get_json(host, "/json/eff", timeout)
    pal_names = scan.get_json(host, "/json/pal", timeout)
    if not isinstance(eff_names, list) or not isinstance(pal_names, list):
        row["status"] = "unreachable"
        row["detail"] = "could not read /json/eff or /json/pal"
        return row

    # If this ever trips, array position no longer equals effect index and every write is wrong.
    fxcount = dev.get("fxcount")
    if isinstance(fxcount, int) and fxcount != len(eff_names):
        row["status"] = "skipped"
        row["detail"] = f"fxcount {fxcount} != len(/json/eff) {len(eff_names)} — indices unsafe"
        return row

    fx = pal = None
    if args.effect:
        fx, err = resolve_effect(args.effect, eff_names)
        if err:
            row["status"] = "skipped"
            row["detail"] = err
            return row
    if args.palette:
        pal, err = resolve_palette(args.palette, pal_names, dev.get("umpalnames"))
        if err:
            row["status"] = "skipped"
            row["detail"] = err
            return row

    col, err = parse_colour(args.color)
    if err:
        row["status"] = "skipped"
        row["detail"] = err
        return row

    resolved = []
    if fx is not None:
        resolved.append(f"fx={fx}")
    if pal is not None:
        resolved.append(f"pal={pal}")
    if col is not None:
        resolved.append(f"col={col}")
    row["detail"] = " ".join(resolved)

    if args.dry_run:
        row["status"] = "would apply"
        return row

    body = build_body(fx=fx, pal=pal, col=col, timebase=timebase,
                      turn_on=True, verbose=True)
    resp, err = post_json(host, "/json/state", body, timeout)
    if err:
        row["status"] = "failed"
        row["detail"] = err
        return row

    problems = verify_applied(resp, fx=fx, pal=pal, col=col)
    if problems:
        row["status"] = "mismatch"
        row["detail"] = "; ".join(problems[:3])
    else:
        row["status"] = "applied"
    return row


def main():
    ap = argparse.ArgumentParser(
        description="Sync every WLED device on the LAN to the same effect, colour and palette.")
    ap.add_argument("--effect", help="effect name, e.g. 'Hiphotic' (matched case-insensitively)")
    ap.add_argument("--palette", help="palette name, e.g. 'Random Cycle'")
    ap.add_argument("--color", "--colour", dest="color", help="#RRGGBB or r,g,b")
    ap.add_argument("--dry-run", action="store_true", help="resolve and print; change nothing")
    ap.add_argument("--no-phase", action="store_true",
                    help="do not align animation phase (devices will visibly drift apart)")
    ap.add_argument("--tailscale", action="store_true",
                    help="also scan Tailscale peers (off by default: different trust boundary)")
    ap.add_argument("--subnet", action="append", default=[], metavar="CIDR")
    ap.add_argument("--host", action="append", default=[], metavar="HOST")
    ap.add_argument("--timeout", type=float, default=2.0)
    ap.add_argument("--workers", type=int, default=16)
    args = ap.parse_args()

    if not (args.effect or args.palette or args.color):
        ap.error("nothing to sync — give at least one of --effect / --palette / --color")

    if args.color and args.palette and colour_is_moot(args.palette):
        print(f"WARNING: palette {args.palette!r} supplies its own colours; most effects will "
              f"ignore --color. Use a colour-driven palette (e.g. 'Color 1') to see it.",
              file=sys.stderr)

    scan = load_scanner()

    targets = list(args.host)
    import ipaddress
    nets = [ipaddress.ip_network(s, strict=False) for s in args.subnet]
    if not args.host and not args.subnet:
        nets += scan.local_subnets()
    for n in nets:
        targets += [str(ip) for ip in n.hosts()]
    if args.tailscale:
        targets += scan.tailscale_peers()

    seen, ordered = set(), []
    for t in targets:
        if t not in seen:
            seen.add(t)
            ordered.append(t)
    if not ordered:
        print("No addresses to scan.", file=sys.stderr)
        return 1

    print(f"Scanning {len(ordered)} address(es)…", file=sys.stderr)
    devices = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=64) as ex:
        for info in ex.map(lambda h: scan.probe(h, 1.0), ordered):
            if info:
                devices.append(info)
    if not devices:
        print("No WLED devices found. Are you on the right network?", file=sys.stderr)
        return 1

    # One timebase for the whole run, so every device anchors to the same instant.
    timebase = None if args.no_phase else int(time.time() * 1000) & 0xFFFFFFFF

    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(sync_one, scan, d, args, timebase, args.timeout) for d in devices]
        for f in concurrent.futures.as_completed(futs):
            rows.append(f.result())

    widths = {k: max(len(k.upper()), max((len(str(r[k])) for r in rows), default=0))
              for k in ("name", "host", "status", "detail")}
    order = ("name", "host", "status", "detail")
    print("  ".join(k.upper().ljust(widths[k]) for k in order))
    print("  ".join("-" * widths[k] for k in order))
    for r in sorted(rows, key=lambda x: x["name"].lower()):
        print("  ".join(str(r[k]).ljust(widths[k]) for k in order))

    bad = [r for r in rows if r["status"] in ("failed", "mismatch")]
    return 1 if bad else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
