#!/usr/bin/env python3
"""Drive HMTL_Command_CLI over USB serial and capture what it prints.

Bench companion for todo_plans/rs485-bridge-hardware-bring-up-on-real-esp32-legacy-hmtl-bus.md.
This is what makes steps 5-9 of that runbook scriptable instead of "type into a serial monitor and
read the scrollback" -- so Claude can originate bus traffic and capture the result, and so a bench
observation ends up in the transcript rather than in someone's memory.

    # list candidate ports
    ./hmtl_cli.py --list

    # poll module 3 and watch for 5 seconds
    ./hmtl_cli.py --port /dev/tty.usbserial-XXXX --cmd "p 3" --listen 5

    # just listen (e.g. in one terminal while the probe sends UDP in another)
    ./hmtl_cli.py --port /dev/tty.usbserial-XXXX --listen 20

HMTL_Command_CLI's USB serial is 57600 baud, NOT the 115200 a WLED board uses -- getting this wrong
produces plausible-looking garbage rather than an obvious failure. Its commands are single letters:

    p <addr>   poll request
    s <addr>   sensor request
    t <addr>   time sync
    h          help

Why this sketch rather than a stock HMTL_Module: it prints every frame the socket layer hands it --
source, destination, length, type, flags, hex dump -- and, the part that matters, it decodes poll
responses WITHOUT filtering on the HMTL header address. A stock module gates everything on
msg_hdr->address == its own (MessageHandler.cpp:78-79, no else-branch), so it silently discards the
bridge's poll response, and you cannot tell "the bridge sent nothing" from "my module dropped it".

WHAT THIS IS NOT: a bus sniffer. The sketch calls hmtl_socket_getmsg(&rs485, &msglen,
config.address), which filters on the SOCKET-layer destination (RS485Socket::getMsg ->
SOCKET_ADDRESS_MATCH, RS485Utils.cpp:365), so a frame addressed to some other node never reaches
process_message() at all and nothing is printed. To observe a frame here it must be addressed to
this module's address or broadcast (0xFFFF). Point the probe at this module, not at a third node,
or you will watch the bridge's tx counter climb while this prints nothing and conclude, wrongly,
that the bridge never transmitted.

The port is held open only for the duration of one invocation, so `pio device monitor` can be used
between steps without fighting over it.
"""

import argparse
import sys
import time

DEFAULT_BAUD = 57600   # HMTL_Command_CLI.ino: Serial.begin(57600)

# Time to let an auto-reset finish before writing. USB-serial adapters assert DTR on open and an
# ATMega328 then sits in its bootloader for ~1.5-2 s.
RESET_SETTLE_SECS = 2.0


def _import_serial():
    """Import pyserial lazily, so --help and py_compile work without it installed.

    --list genuinely needs it (it enumerates ports through serial.tools), so that path exits
    with the install hint rather than degrading.
    """
    try:
        import serial  # noqa: F401
        return serial
    except ImportError:
        sys.stderr.write(
            "pyserial is not installed. Install it into the repo venv:\n"
            "    ./.venv/bin/pip install pyserial\n"
            "(PlatformIO already depends on it, so it is usually present there.)\n")
        raise SystemExit(2)


def list_ports():
    # Called for its error message only -- the import below is what actually does the work. Without
    # this, a missing pyserial surfaces as the much less helpful "list_ports unavailable" instead of
    # the pip line that fixes it.
    _import_serial()
    try:
        from serial.tools import list_ports as lp
    except ImportError:
        sys.stderr.write("serial.tools.list_ports unavailable\n")
        return 2
    found = list(lp.comports())
    if not found:
        print("no serial ports found")
        return 1
    for p in found:
        print(f"{p.device}\t{p.description}")
    # The FTDI/CH340/CP210x adapters these boards use are the likely candidates; a Bluetooth
    # pseudo-port is not, and picking one produces a silent timeout rather than an error.
    print("\n(pick the USB-serial adapter -- FTDI / CH340 / CP210x -- not a Bluetooth port)")
    return 0


def run(port, baud, cmd, listen, echo_raw=False):
    serial = _import_serial()
    with serial.Serial(port, baud, timeout=0.2) as ser:
        # FTDI/CH340/CP210x adapters assert DTR on open, which drops an ATMega328 into its
        # bootloader for ~1.5-2 s. Writing before that finishes loses the command silently -- the
        # bootloader eats it -- and the symptom is an empty capture that looks like a wiring or baud
        # problem. So wait the reset out rather than draining after 200 ms and hoping.
        #
        # Anything the sketch printed while we waited is drained afterwards, since stale boot output
        # would otherwise read as a response to a command we had not sent yet. Its banner is
        # "*** HMTL_Command_CLI starting ***"; seeing that in a capture means the board just reset.
        time.sleep(RESET_SETTLE_SECS)
        ser.reset_input_buffer()

        if cmd:
            ser.write((cmd + "\n").encode())
            ser.flush()
            print(f"-> {cmd}")

        if listen <= 0:
            return 0

        deadline = time.monotonic() + listen
        buf = b""
        lines = 0
        while time.monotonic() < deadline:
            chunk = ser.read(256)
            if not chunk:
                continue
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                text = line.decode("utf-8", "replace").rstrip("\r")
                if text:
                    print(f"<- {text}")
                    lines += 1
        if buf:
            print(f"<- {buf.decode('utf-8', 'replace').rstrip()}")
            lines += 1

        if lines == 0:
            print(f"\n(nothing received in {listen}s)")
            print("Check, in this order:")
            print("  1. Was the frame addressed to THIS module (or broadcast 0xFFFF)? The sketch is")
            print("     not a sniffer -- the socket layer drops frames addressed to other nodes, so")
            print("     traffic aimed at a third node is invisible here and looks like silence.")
            print("  2. Did the adapter reset the board and eat the command? A capture containing")
            print("     '*** HMTL_Command_CLI starting ***' means exactly that; raise RESET_SETTLE_SECS.")
            print("  3. Baud 57600, not the 115200 a WLED board uses.")
            print("  4. Right port, and the sketch really is HMTL_Command_CLI.")
            print("  5. RS485 A/B plus a COMMON GROUND between every node -- a floating ground reads")
            print("     as silence on both ends.")
            print("Note the sketch spells it 'Recieved', so grep for that literally.")
        return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", help="serial device, e.g. /dev/tty.usbserial-XXXX")
    ap.add_argument("--baud", type=int, default=DEFAULT_BAUD,
                    help=f"default {DEFAULT_BAUD} (HMTL_Command_CLI's USB rate, not the bus rate)")
    ap.add_argument("--cmd", help='command to send, e.g. "p 3"')
    ap.add_argument("--listen", type=float, default=0.0, metavar="SECS",
                    help="seconds to capture output after sending")
    ap.add_argument("--list", action="store_true", help="list serial ports and exit")
    args = ap.parse_args(argv)

    if args.list:
        return list_ports()
    if not args.port:
        ap.error("--port is required (use --list to find it)")
    if not args.cmd and args.listen <= 0:
        ap.error("give --cmd, --listen, or both")
    return run(args.port, args.baud, args.cmd, args.listen)


if __name__ == "__main__":
    sys.exit(main())
