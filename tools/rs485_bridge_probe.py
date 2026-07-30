#!/usr/bin/env python3
"""Build, send and decode HMTL frames for the WLED rs485_bridge usermod.

Bench companion for todo_plans/rs485-bridge-hardware-bring-up-on-real-esp32-legacy-hmtl-bus.md.
The point is to make a bench failure unambiguous: not "it didn't work" but "the bridge relayed a
POLL response with MSG_FLAG_ERROR and address 0".

    # send an RGB command to module 3 via the bridge
    ./rs485_bridge_probe.py --host 192.168.1.55 --emit rgb --addr 3 --rgb 255,0,0

    # send, then keep listening on the SAME socket for anything relayed back
    ./rs485_bridge_probe.py --host 192.168.1.55 --emit poll --addr 3 --await-reply 20

    # no hardware: prove the layout table still matches the firmware
    ./rs485_bridge_probe.py --self-test

    # decode a hex dump from a serial log
    ./rs485_bridge_probe.py --decode "fc 3a 02 17 02 01 03 00 ..."

TWO THINGS THAT ARE EASY TO GET WRONG, both learned the hard way:

1. Send and receive on ONE socket, never rebinding. The bridge relays to the source address AND
   ephemeral source port of the last datagram it received (usermod_rs485_bridge.cpp, serviceUdp /
   relayToPeer). A separate listener -- or one bound to 21331 -- will never see a reply. It also
   no-ops until it has heard from someone, so relays cannot be observed passively: the probe must
   send first.

2. A POLL *response* from a module is never relayed. It comes back addressed to the bridge with
   MSG_FLAG_ACK set, and rs485b_decide() has no ACK guard, so the bridge answers it as if it were a
   fresh poll request instead of forwarding it. That is current behaviour, tracked separately -- so
   --emit poll --await-reply will time out, and that is not a bring-up failure. Use relayable
   traffic instead (see the runbook's step 8).
"""

import argparse
import re
import socket
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------------------------
# Wire format
# ---------------------------------------------------------------------------------------------
# Stated explicitly in Python rather than parsed out of the C headers, because those headers are
# not self-describing: HMTLWireFormat.h includes Socket.h for socket_addr_t (a *different*
# submodule), config_hdr_t is selected by an #if on HMTL_CONFIG_VERSION, and msg_value_t is a C
# bitfield that no offset table can express. --self-test cross-checks every number below against
# the assertions the firmware already carries, so this cannot silently drift.

HMTL_MSG_START = 0xFC
HMTL_MSG_VERSION = 2
HMTL_CONFIG_MAGIC = 0x5C
HMTL_CONFIG_VERSION = 3

MSG_TYPE = {
    "OUTPUT": 0x01,
    "POLL": 0x02,
    "SET_ADDR": 0x03,
    "SENSOR": 0x04,
    "TIMESYNC": 0x05,
    "DUMP_CONFIG": 0xE0,
}
MSG_TYPE_NAME = {v: k for k, v in MSG_TYPE.items()}

MSG_FLAG = {"ACK": 1 << 0, "RESPONSE": 1 << 1, "MORE_DATA": 1 << 2, "ERROR": 1 << 3}

HMTL_OUTPUT = {
    "NONE": 0xFF, "VALUE": 0x1, "RGB": 0x2, "PROGRAM": 0x3,
    "PIXELS": 0x4, "MPR121": 0x5, "RS485": 0x6, "XBEE": 0x7,
}
HMTL_OUTPUT_NAME = {v: k for k, v in HMTL_OUTPUT.items()}

SOCKET_ADDR_ANY = 0xFFFF

# msg_hdr_t: 8 bytes. `length` is the TOTAL frame length, header included; `address` is the
# DESTINATION (the header carries no source -- that lives in the RS485 socket layer beneath).
HDR_LEN = 8
HDR_LAYOUT = {"startcode": 0, "crc": 1, "version": 2, "length": 3, "type": 4, "flags": 5, "address": 6}

# Payload struct sizes, all packed (HMTL#6). These are what --self-test pins.
STRUCT_SIZES = {
    "msg_hdr_t": 8,
    "output_hdr_t": 2,
    "msg_value_t": 4,
    "msg_rgb_t": 5,
    "msg_program_t": 35,
    "msg_set_addr_t": 4,
    "config_hdr_v1_t": 5,
    "config_hdr_v2_t": 8,
    "config_hdr_v3_t": 10,
    "msg_poll_response_t": 15,
}
STRUCT_OFFSETS = {
    "msg_poll_response_t": {"config": 0, "object_type": 10, "recv_buffer_size": 12,
                            "msg_version": 14, "data": 15},
    "config_hdr_v2_t": {"address": 3, "reserved": 5, "num_outputs": 6, "flags": 7},
    "config_hdr_v3_t": {"device_id": 6, "address": 8},
    "msg_rgb_t": {"values": 2},
    "msg_program_t": {"type": 2, "values": 3},
    "msg_set_addr_t": {"address": 2},
    "msg_sensor_data_t": {"data": 2},
    "output_hdr_t": {"output": 1},
}
HMTL_MSG_POLL_MIN_LEN = 23

DEFAULT_UDP_PORT = 21331


def crc8_hmtl(buf):
    """CRC-8 over a whole frame, poly 0xD8, MSB-first, init 0, with byte 1 (the crc field) as zero.

    Matches rs485b_hmtl_crc() in rs485_bridge_protocol.h and ArduinoLibs' EEPROM_crc. NOT the same
    polynomial as the RS485 link layer's CRC (Gammon's is 0x8C, LSB-first) -- two different CRCs
    live in this stack and confusing them produces frames that look right and are silently dropped.
    """
    rem = 0
    for i, b in enumerate(buf):
        rem ^= 0 if i == 1 else b
        for _ in range(8):
            rem = ((rem << 1) ^ 0xD8) & 0xFF if rem & 0x80 else (rem << 1) & 0xFF
    return rem


def build_header(dest_addr, total_len, msg_type, flags=0):
    return bytearray([
        HMTL_MSG_START, 0, HMTL_MSG_VERSION, total_len & 0xFF, msg_type, flags,
        dest_addr & 0xFF, (dest_addr >> 8) & 0xFF,
    ])


def stamp_crc(frame):
    frame[1] = crc8_hmtl(frame)
    return bytes(frame)


def emit_value(addr, output, value):
    """OUTPUT/VALUE. msg_value_t packs a 13-bit value and 3 flag bits into one little-endian word.

    Note where this lands on the bridge: VALUE is a white level mapped to WLED MASTER BRIGHTNESS,
    not segment colour, and it saturates at 255 rather than wrapping -- so sending 4096 and seeing
    full brightness is correct.
    """
    frame = build_header(addr, HDR_LEN + STRUCT_SIZES["msg_value_t"], MSG_TYPE["OUTPUT"])
    frame += bytes([HMTL_OUTPUT["VALUE"], output & 0xFF])
    word = (value & 0x1FFF)
    frame += bytes([word & 0xFF, (word >> 8) & 0xFF])
    return stamp_crc(frame)


def emit_rgb(addr, output, rgb):
    frame = build_header(addr, HDR_LEN + STRUCT_SIZES["msg_rgb_t"], MSG_TYPE["OUTPUT"])
    frame += bytes([HMTL_OUTPUT["RGB"], output & 0xFF]) + bytes(rgb)
    return stamp_crc(frame)


def emit_poll(addr):
    return stamp_crc(build_header(addr, HDR_LEN, MSG_TYPE["POLL"], MSG_FLAG["RESPONSE"]))


def emit_set_addr(addr, device_id, new_address):
    frame = build_header(addr, HDR_LEN + STRUCT_SIZES["msg_set_addr_t"], MSG_TYPE["SET_ADDR"])
    frame += bytes([device_id & 0xFF, (device_id >> 8) & 0xFF,
                    new_address & 0xFF, (new_address >> 8) & 0xFF])
    return stamp_crc(frame)


def emit_timesync(addr):
    """TIMESYNC exists here purely as a RELAY STIMULUS for the runbook's step 8.

    The bridge does not decode it, so a TIMESYNC addressed to the bridge decodes as
    RS485B_ACT_UNSUPPORTED -- which is counted *and* relayed to the WiFi peer. That makes it the
    cheapest way to observe the relay path, since a poll response never gets relayed at all.
    """
    return stamp_crc(build_header(addr, HDR_LEN, MSG_TYPE["TIMESYNC"]))


EMITTERS = {"value": "emit_value", "rgb": "emit_rgb", "poll": "emit_poll",
            "setaddr": "emit_set_addr", "timesync": "emit_timesync"}


# ---------------------------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------------------------
def decode(frame, indent=""):
    out = []
    if len(frame) < HDR_LEN:
        return [f"{indent}RUNT: {len(frame)} bytes, shorter than an {HDR_LEN}-byte HMTL header"]

    start, crc, ver, length, mtype, flags = frame[0:6]
    addr = frame[6] | (frame[7] << 8)
    tname = MSG_TYPE_NAME.get(mtype, f"0x{mtype:02X}?")
    fnames = [n for n, bit in MSG_FLAG.items() if flags & bit] or ["none"]

    out.append(f"{indent}startcode 0x{start:02X}" + ("" if start == HMTL_MSG_START else "  <-- BAD, expected 0xFC"))
    out.append(f"{indent}version   {ver}" + ("" if ver == HMTL_MSG_VERSION else f"  <-- BAD, expected {HMTL_MSG_VERSION}"))
    out.append(f"{indent}length    {length}" + ("" if length == len(frame) else f"  <-- declared {length}, got {len(frame)} bytes"))
    out.append(f"{indent}type      {tname} (0x{mtype:02X})")
    out.append(f"{indent}flags     0x{flags:02X} [{', '.join(fnames)}]")
    out.append(f"{indent}address   {addr}" + ("  (broadcast)" if addr == SOCKET_ADDR_ANY else ""))

    expect = crc8_hmtl(frame)
    if crc == 0:
        out.append(f"{indent}crc       0 (unset -- stock HMTL builds leave HMTL_USE_CRC off; the bridge accepts this)")
    elif crc == expect:
        out.append(f"{indent}crc       0x{crc:02X} (ok)")
    else:
        out.append(f"{indent}crc       0x{crc:02X}  <-- BAD, computed 0x{expect:02X}")

    payload = frame[HDR_LEN:]
    if mtype == MSG_TYPE["OUTPUT"] and len(payload) >= 2:
        otype, onum = payload[0], payload[1]
        out.append(f"{indent}output    #{onum} type {HMTL_OUTPUT_NAME.get(otype, hex(otype))}")
        if otype == HMTL_OUTPUT["RGB"] and len(payload) >= 5:
            out.append(f"{indent}          rgb = {payload[2]},{payload[3]},{payload[4]}")
        elif otype == HMTL_OUTPUT["VALUE"] and len(payload) >= 4:
            word = payload[2] | (payload[3] << 8)
            out.append(f"{indent}          value = {word & 0x1FFF} (flags {(word >> 13) & 0x7})")
    elif mtype == MSG_TYPE["POLL"] and flags & MSG_FLAG["ACK"]:
        out += decode_poll_response(payload, indent)
    elif mtype == MSG_TYPE["SET_ADDR"] and len(payload) >= 4:
        out.append(f"{indent}set_addr  device_id={payload[0] | (payload[1] << 8)} "
                   f"new_address={payload[2] | (payload[3] << 8)}")
    elif payload:
        out.append(f"{indent}payload   {payload.hex(' ')}")
    return out


def decode_poll_response(p, indent=""):
    if len(p) < STRUCT_SIZES["msg_poll_response_t"]:
        return [f"{indent}POLL RESPONSE TRUNCATED: {len(p)} bytes, need "
                f"{STRUCT_SIZES['msg_poll_response_t']}"]
    o = STRUCT_OFFSETS["msg_poll_response_t"]
    magic, proto, hw, baud, nout, cflags = p[0], p[1], p[2], p[3], p[4], p[5]
    dev = p[6] | (p[7] << 8)
    caddr = p[8] | (p[9] << 8)
    obj = p[o["object_type"]] | (p[o["object_type"] + 1] << 8)
    rbuf = p[o["recv_buffer_size"]] | (p[o["recv_buffer_size"] + 1] << 8)
    mver = p[o["msg_version"]]
    lines = [
        f"{indent}poll response:",
        f"{indent}  magic       0x{magic:02X}" + ("" if magic == HMTL_CONFIG_MAGIC else "  <-- BAD, expected 0x5C"),
        f"{indent}  proto/hw    {proto} / {hw}",
        # baud is in units of 1200; 23 decodes to 27600, not 28000. That is the format, not a bug.
        f"{indent}  baud        {baud} -> {baud * 1200} baud"
        + ("  (28000 encodes as 23 -> reads back 27600; expected)" if baud == 23 else ""),
        f"{indent}  num_outputs {nout}   flags 0x{cflags:02X}",
        f"{indent}  device_id   {dev}",
        f"{indent}  address     {caddr}",
        f"{indent}  object_type 0x{obj:04X}" + ("  ('W' = a WLED bridge, not a real HMTL module)" if obj == 0x0057 else ""),
        f"{indent}  recv_buffer {rbuf}",
        f"{indent}  msg_version {mver}",
    ]
    return lines


# ---------------------------------------------------------------------------------------------
# --self-test: cross-check the table above against the firmware's own assertions
# ---------------------------------------------------------------------------------------------
def _scrape(path, pattern):
    """Return regex matches, raising if the file yields none.

    Failing on zero matches is the whole point. A renamed field or a relocated assertion would
    otherwise turn this drift guard off silently -- which is exactly the failure it exists to catch.
    """
    text = Path(path).read_text()
    found = re.findall(pattern, text)
    if not found:
        raise AssertionError(f"no matches for {pattern!r} in {path} -- "
                             "the firmware moved and this cross-check has gone blind")
    return found


def self_test():
    proto = REPO_ROOT / "WLED/usermods/rs485_bridge/rs485_bridge_protocol.h"
    tests = REPO_ROOT / "WLED/usermods/rs485_bridge/tests/rs485_bridge_test.cpp"
    wire = REPO_ROOT / "HMTL/Libraries/HMTLprotocol/HMTLWireFormat.h"
    problems = []

    # 1. static_assert(sizeof(X) == N) in the protocol header
    sizes = dict((m, int(n)) for m, n in
                 _scrape(proto, r"static_assert\(sizeof\((\w+)\)\s*==\s*(\d+)"))
    for name, expected in STRUCT_SIZES.items():
        if name in sizes and sizes[name] != expected:
            problems.append(f"sizeof({name}): firmware says {sizes[name]}, table says {expected}")
    checked_sizes = [n for n in STRUCT_SIZES if n in sizes]
    if not checked_sizes:
        problems.append("no struct sizes matched -- the static_assert block moved or was renamed")

    # 2. HMTL_MSG_POLL_MIN_LEN
    poll = _scrape(proto, r"HMTL_MSG_POLL_MIN_LEN\s*==\s*(\d+)")
    if int(poll[0]) != HMTL_MSG_POLL_MIN_LEN:
        problems.append(f"HMTL_MSG_POLL_MIN_LEN: firmware {poll[0]}, table {HMTL_MSG_POLL_MIN_LEN}")

    # 3. offsetof(X, field) == N in the host test
    offs = _scrape(tests, r"offsetof\((\w+),\s*(\w+)\)\s*==\s*(\d+)")
    fw_offsets = {}
    for struct, field, val in offs:
        fw_offsets.setdefault(struct, {})[field] = int(val)
    for struct, fields in STRUCT_OFFSETS.items():
        for field, expected in fields.items():
            got = fw_offsets.get(struct, {}).get(field)
            if got is not None and got != expected:
                problems.append(f"offsetof({struct}, {field}): firmware {got}, table {expected}")

    # 4. msg_hdr_t's offsets are pinned by POINTER ARITHMETIC, not offsetof -- a scraper that looks
    #    only for offsetof finds nothing here and would wrongly report the header as unguarded.
    hdr_checks = _scrape(tests, r"\(uint8_t \*\)&h\.(\w+)\s*-\s*\(uint8_t \*\)&h\)\s*==\s*(\d+)")
    for field, val in hdr_checks:
        if HDR_LAYOUT.get(field) != int(val):
            problems.append(f"msg_hdr_t.{field}: firmware {val}, table {HDR_LAYOUT.get(field)}")

    # 5. Constants straight from the wire format header.
    for macro, expected in (("HMTL_MSG_START", HMTL_MSG_START),
                            ("HMTL_MSG_VERSION", HMTL_MSG_VERSION),
                            ("HMTL_CONFIG_MAGIC", HMTL_CONFIG_MAGIC),
                            ("HMTL_CONFIG_VERSION", HMTL_CONFIG_VERSION)):
        got = _scrape(wire, rf"#define {macro}\s+(0x[0-9A-Fa-f]+|\d+)")[0]
        if int(got, 0) != expected:
            problems.append(f"{macro}: firmware {got}, table {expected}")

    # 6. Round-trip every emitter through the decoder.
    for frame, label in ((emit_rgb(3, 0, (255, 0, 0)), "rgb"),
                         (emit_value(3, 0, 200), "value"),
                         (emit_poll(3), "poll"),
                         (emit_set_addr(3, 42, 7), "setaddr"),
                         (emit_timesync(3), "timesync")):
        if frame[0] != HMTL_MSG_START or frame[3] != len(frame):
            problems.append(f"{label}: emitted frame is self-inconsistent")
        if crc8_hmtl(frame) != frame[1]:
            problems.append(f"{label}: CRC does not verify against its own frame")
        decode(frame)

    print(f"checked {len(checked_sizes)} struct sizes, "
          f"{sum(len(v) for v in STRUCT_OFFSETS.values())} field offsets, "
          f"{len(hdr_checks)} header offsets, 4 constants, 5 round-trips")
    if problems:
        print("\nSELF-TEST FAILED -- the Python layout table and the firmware disagree:")
        for p in problems:
            print(f"  {p}")
        return 1
    print("self-test OK")
    return 0


# ---------------------------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", help="bridge IP")
    ap.add_argument("--port", type=int, default=DEFAULT_UDP_PORT)
    ap.add_argument("--emit", choices=sorted(EMITTERS))
    ap.add_argument("--addr", type=int, default=1, help="destination HMTL address")
    ap.add_argument("--output", type=int, default=0, help="HMTL output index")
    ap.add_argument("--rgb", help="R,G,B for --emit rgb")
    ap.add_argument("--value", type=int, default=255, help="level for --emit value (13-bit)")
    ap.add_argument("--device-id", type=int, default=0, help="for --emit setaddr")
    ap.add_argument("--new-address", type=int, default=1, help="for --emit setaddr")
    ap.add_argument("--await-reply", type=float, default=0.0, metavar="SECS",
                    help="after sending, listen on the SAME socket for relayed frames")
    ap.add_argument("--hex", action="store_true", help="print the frame as hex instead of sending")
    ap.add_argument("--no-wait", action="store_true", help="send and exit (for load loops)")
    ap.add_argument("--decode", metavar="HEX", help="decode a hex dump and exit")
    ap.add_argument("--self-test", action="store_true",
                    help="verify the layout table against the firmware; no hardware needed")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()

    if args.decode:
        raw = bytes.fromhex(re.sub(r"[^0-9A-Fa-f]", "", args.decode))
        print(f"{len(raw)} bytes: {raw.hex(' ')}")
        print("\n".join(decode(raw, "  ")))
        return 0

    if not args.emit:
        ap.error("one of --emit, --decode or --self-test is required")

    if args.emit == "rgb":
        if not args.rgb:
            ap.error("--emit rgb needs --rgb R,G,B")
        rgb = [int(x) for x in args.rgb.split(",")]
        if len(rgb) != 3:
            ap.error("--rgb wants exactly three comma-separated values")
        frame = emit_rgb(args.addr, args.output, rgb)
    elif args.emit == "value":
        frame = emit_value(args.addr, args.output, args.value)
    elif args.emit == "poll":
        frame = emit_poll(args.addr)
    elif args.emit == "setaddr":
        frame = emit_set_addr(args.addr, args.device_id, args.new_address)
    else:
        frame = emit_timesync(args.addr)

    if args.hex:
        print(frame.hex(" "))
        return 0

    if not args.host:
        ap.error("--host is required unless --hex, --decode or --self-test is given")

    # One socket for both directions, bound to an ephemeral port and never rebound -- see the
    # module docstring. Binding to 21331 here would be the intuitive thing and would never work.
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", 0))
    sock.sendto(frame, (args.host, args.port))
    print(f"sent {len(frame)} bytes to {args.host}:{args.port} from local port {sock.getsockname()[1]}")
    print("\n".join(decode(frame, "  ")))

    if args.no_wait or args.await_reply <= 0:
        return 0

    print(f"\nlistening {args.await_reply}s on the same socket for relayed frames...")
    sock.settimeout(args.await_reply)
    seen = 0
    import time
    deadline = time.monotonic() + args.await_reply
    while time.monotonic() < deadline:
        try:
            sock.settimeout(max(0.1, deadline - time.monotonic()))
            data, peer = sock.recvfrom(512)
        except socket.timeout:
            break
        seen += 1
        print(f"\n<- {len(data)} bytes from {peer[0]}:{peer[1]}: {data.hex(' ')}")
        print("\n".join(decode(data, "   ")))
    if not seen:
        print("nothing relayed back.")
        print("If you sent a POLL: that is EXPECTED -- a module's poll response is addressed to the")
        print("bridge with MSG_FLAG_ACK, and the bridge answers it instead of relaying it. Use")
        print("relayable traffic (a frame for another node, a SENSOR broadcast, or TIMESYNC) and")
        print("check the 'RS485 udp in/relayed' counter on /json/info.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
