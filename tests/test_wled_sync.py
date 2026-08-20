#!/usr/bin/env python3
"""Host tests for wled_sync — pure logic plus a cross-version test against stub servers.

No hardware, no network beyond loopback. Run directly or via `make test-sync`.
"""

import importlib.util
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

spec = importlib.util.spec_from_file_location("wled_sync", os.path.join(REPO_ROOT, "scripts", "wled_sync.py"))
ws = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ws)


# Real name tables, trimmed. Indices are what matter: the SAME name sits at a DIFFERENT index in
# each version, which is the entire reason this tool resolves by name.
EFF_16 = ["Solid", "Blink", "Breathe", "Wipe"] + ["RSVD"] * 138 + ["Android"] * 36 + \
         ["Hiphotic"] + ["Candy Cane"] * 41
EFF_014 = ["Solid", "Blink", "Breathe", "Wipe", "Police", "Meteor Smooth"] + ["Android"] * 100 + \
          ["Hiphotic"] + ["Candy Cane"] * 79

PAL_FIXED = ["Default", "* Random Cycle", "* Color 1", "* Colors 1&2", "* Color Gradient",
             "* Colors Only", "Party", "Cloud"]
UMPAL = ["AudioReactive: Ratio", "AudioReactive: Hue", "AudioReactive: Spectrum"]


class TestNameResolution(unittest.TestCase):
    def test_same_name_different_index_across_versions(self):
        # The whole point of the tool. Index 180 is not portable; the name is.
        i16, err16 = ws.resolve_effect("Hiphotic", EFF_16)
        i14, err14 = ws.resolve_effect("Hiphotic", EFF_014)
        self.assertIsNone(err16)
        self.assertIsNone(err14)
        self.assertNotEqual(i16, i14, "test data must exercise differing indices")
        self.assertEqual(EFF_16[i16], "Hiphotic")
        self.assertEqual(EFF_014[i14], "Hiphotic")

    def test_effect_removed_in_newer_firmware_is_skipped(self):
        # Police and Meteor Smooth exist in 0.14.4 and were removed by 16.0.1.
        i, err = ws.resolve_effect("Police", EFF_014)
        self.assertIsNotNone(i)
        i, err = ws.resolve_effect("Police", EFF_16)
        self.assertIsNone(i)
        self.assertIn("not present", err)

    def test_reserved_placeholder_never_resolves(self):
        # RSVD is the only duplicated name in /json/eff and must never be selectable.
        i, err = ws.resolve_effect("RSVD", EFF_16)
        self.assertIsNone(i, "RSVD must not be selectable")
        self.assertIn("not present", err)

    def test_case_and_whitespace_insensitive(self):
        for variant in ("hiphotic", "HIPHOTIC", "  Hiphotic  ", "Hiphotic"):
            i, err = ws.resolve_effect(variant, EFF_16)
            self.assertIsNone(err, f"{variant!r} should resolve")

    def test_palette_star_prefix_is_stripped(self):
        # /json/pal decorates 5 entries with '*'; a user typing the bare name must still match.
        i, err = ws.resolve_palette("Random Cycle", PAL_FIXED)
        self.assertIsNone(err)
        self.assertEqual(i, 1)
        i, err = ws.resolve_palette("* Random Cycle", PAL_FIXED)
        self.assertEqual(i, 1)

    def test_usermod_palettes_use_255_minus_j(self):
        # These are NOT in /json/pal; they are named in info.umpalnames at IDs 255-j.
        i, err = ws.resolve_palette("AudioReactive: Hue", PAL_FIXED, UMPAL)
        self.assertIsNone(err)
        self.assertEqual(i, 255 - 1)

    def test_unknown_palette_reports_rather_than_guessing(self):
        i, err = ws.resolve_palette("Not A Palette", PAL_FIXED, UMPAL)
        self.assertIsNone(i)
        self.assertIn("not present", err)


class TestBody(unittest.TestCase):
    def test_udp_suppression_is_always_present(self):
        # The highest-value assertion here. Without udpn.nn each device re-broadcasts the RAW
        # index to the fleet, undoing per-device name resolution. A regression is silent.
        for kwargs in ({"fx": 1}, {"pal": 2}, {"col": [1, 2, 3]},
                       {"fx": 1, "pal": 2, "col": [1, 2, 3], "timebase": 99}):
            body = ws.build_body(**kwargs)
            self.assertTrue(body.get("udpn", {}).get("nn"),
                            f"udpn.nn missing for {kwargs}")

    def test_shared_timebase_and_power(self):
        body = ws.build_body(fx=1, timebase=1234567)
        self.assertEqual(body["tb"], 1234567)
        self.assertIs(body["on"], True)
        self.assertIs(body["v"], True)

    def test_timebase_survives_wled_signed_32bit_parse(self):
        # Regression. WLED reads tb into `long tr` (json.cpp:398) and ignores it unless tr >= 0
        # (json.cpp:417-418); `long` is 32-bit signed on ESP32. An unmasked millisecond epoch
        # exceeds INT32_MAX, so tb would be dropped and phase alignment would silently no-op.
        # The failure is INTERMITTENT, which is what makes it nasty: bit 31 of epoch-ms flips
        # about every 24.8 days, so an unmasked value is fine for ~25 days, silently broken for
        # ~25 days, then fine again. Construct the bad half explicitly rather than trusting "now".
        INT32_MAX = 2**31 - 1
        raw = 0x1C0000000                       # low 32 bits = 0xC0000000, i.e. bit 31 set
        self.assertGreater(raw & 0xFFFFFFFF, INT32_MAX, "test must exercise the overflow case")
        tb = ws.make_timebase(raw)
        self.assertGreaterEqual(tb, 0)
        self.assertLessEqual(tb, INT32_MAX, "tb must fit a positive signed 32-bit long")
        # and what actually goes on the wire is bounded too
        self.assertLessEqual(ws.build_body(fx=1, timebase=ws.make_timebase())["tb"], INT32_MAX)

    def test_timebase_is_shared_not_per_device(self):
        # Alignment depends on every device receiving the SAME anchor.
        tb = ws.make_timebase()
        bodies = [ws.build_body(fx=1, timebase=tb) for _ in range(3)]
        self.assertEqual(len({b["tb"] for b in bodies}), 1)

    def test_no_phase_omits_timebase(self):
        self.assertNotIn("tb", ws.build_body(fx=1, timebase=None))

    def test_fxdef_is_never_sent(self):
        # Regression. fxdef looks like the tidy way to equalise sx/ix, but setMode(fx, true) also
        # resets map1D2D unguarded for effects not declaring m12= — WLED's own call site warns
        # "may change map1D2D causing geometry change" (json.cpp:291) — and applies the effect's
        # default palette (FX_fcn.cpp:616-617), silently overriding colour. It would wreck a
        # matrix's expansion mode, unrecoverably by re-running.
        for kwargs in ({"fx": 7}, {"fx": 7, "pal": 2}, {"fx": 7, "speed": 100}):
            self.assertNotIn("fxdef", ws.build_body(**kwargs).get("seg", {}),
                             f"fxdef must never be sent ({kwargs})")

    def test_speed_and_intensity_sent_only_when_asked(self):
        # Not normalised by default: speed is often deliberately per-installation.
        seg = ws.build_body(fx=7).get("seg", {})
        self.assertNotIn("sx", seg)
        self.assertNotIn("ix", seg)
        seg = ws.build_body(fx=7, speed=120, intensity=30)["seg"]
        self.assertEqual((seg["sx"], seg["ix"]), (120, 30))

    def test_segment_carries_no_id(self):
        # No 'id' => applies to every selected segment; never assume segment 0 is main.
        body = ws.build_body(fx=1, pal=2)
        self.assertNotIn("id", body["seg"])

    def test_one_request_carries_everything(self):
        # So a device never visibly steps through intermediate looks.
        body = ws.build_body(fx=1, pal=2, col=[255, 0, 0], timebase=5)
        self.assertEqual(body["seg"]["fx"], 1)
        self.assertEqual(body["seg"]["pal"], 2)
        self.assertEqual(body["seg"]["col"], [[255, 0, 0]])


class TestExitRule(unittest.TestCase):
    """The exit code must reflect whether devices actually got the look."""

    @staticmethod
    def _code(statuses):
        # Calls the REAL rule. An earlier version re-implemented it here, which proved nothing —
        # mutating main() left every test green.
        return ws.exit_code([{"status": st} for st in statuses])

    def test_all_applied_is_success(self):
        self.assertEqual(self._code(["applied", "applied"]), 0)
        self.assertEqual(self._code(["would apply"]), 0)

    def test_skipped_or_unreachable_is_failure(self):
        # These used to exit 0: a run where every device was skipped is a failed sync.
        for st in ("skipped", "unreachable", "failed", "mismatch"):
            self.assertEqual(self._code(["applied", st]), 1, st)
            self.assertEqual(self._code([st]), 1, st)

    def test_no_devices_is_failure(self):
        self.assertEqual(self._code([]), 1)

    def test_calls_the_shipped_rule_directly(self):
        # Belt and braces on the vacuous-test failure mode: exercise ws.exit_code by name, so this
        # cannot pass against a hand-copied rule in the test file.
        self.assertEqual(ws.exit_code([{"status": "applied"}]), 0)
        self.assertEqual(ws.exit_code([{"status": "skipped"}]), 1)
        self.assertEqual(ws.exit_code([]), 1)


class TestArgRanges(unittest.TestCase):
    """--speed / --intensity range enforcement.

    This existed with NO coverage: deleting the check at wled_sync.py:411-414 left the whole suite
    green, while scripts/README.md advertises the exit-2 behaviour to users. It matters because WLED
    does NOT clamp — getVal passes no bounds and ArduinoJson returns 0 for a value that will not fit
    a uint8_t, so `--speed 999` sets sx=0 on every device, a visible freeze, with nothing downstream
    to catch it.

    Driven through main() rather than by calling the validator, so deleting the call site fails too,
    not just gutting the function.
    """

    # Pin to one address and never change anything: without --host the tool sweeps the whole
    # subnet, so a test that reaches this code would contact every real device on the LAN.
    HERMETIC = ["--dry-run", "--host", "127.0.0.1", "--timeout", "1"]

    def _run(self, argv):
        """Return the exit code. argparse raises SystemExit(2); a successful run just returns."""
        old = sys.argv
        sys.argv = ["wled_sync"] + argv
        try:
            try:
                rc = ws.main()
            except SystemExit as e:
                return e.code
            return 0 if rc is None else rc
        finally:
            sys.argv = old

    def test_speed_above_255_is_rejected(self):
        self.assertEqual(self._run(["--speed", "999"] + self.HERMETIC), 2)

    def test_intensity_above_255_is_rejected(self):
        self.assertEqual(self._run(["--intensity", "256"] + self.HERMETIC), 2)

    def test_negative_values_are_rejected(self):
        self.assertEqual(self._run(["--speed", "-1"] + self.HERMETIC), 2)
        self.assertEqual(self._run(["--intensity", "-5"] + self.HERMETIC), 2)

    def test_the_boundaries_themselves_are_ACCEPTED(self):
        # The half that a too-eager check would break: 0 and 255 are legal values, and 0 is
        # falsy — a validator written as `if not val` would reject it.
        # --host pins it to one address so this never sweeps a subnet; --dry-run changes nothing.
        for argv in (["--speed", "0"], ["--speed", "255"],
                     ["--intensity", "0"], ["--intensity", "255"]):
            code = self._run(argv + self.HERMETIC)
            self.assertNotEqual(code, 2, f"{argv} should not be an argument error")


class TestColour(unittest.TestCase):
    def test_parse_forms(self):
        self.assertEqual(ws.parse_colour("#FF0000")[0], [255, 0, 0])
        self.assertEqual(ws.parse_colour("00ff00")[0], [0, 255, 0])
        self.assertEqual(ws.parse_colour("0,0,255")[0], [0, 0, 255])

    def test_bad_forms_report(self):
        for bad in ("#GGGGGG", "1,2", "300,0,0", "nonsense"):
            rgb, err = ws.parse_colour(bad)
            self.assertIsNone(rgb, f"{bad!r} should not parse")
            self.assertIsNotNone(err)

    def test_moot_detection_follows_the_code_not_the_names(self):
        # These two are the ones the names get wrong, and an earlier version had both backwards.
        # Palette 0 "Default" short-circuits to the segment colour (FX_fcn.cpp:1168-1170), so
        # --color is MOST effective there.
        self.assertFalse(ws.colour_is_moot("Default"))
        # "* Random Cycle" wears the same '*' as the colour palettes but uses _randomPalette and
        # never reads colors[] (FX_fcn.cpp:248-249), so --color does nothing.
        self.assertTrue(ws.colour_is_moot("Random Cycle"))
        self.assertTrue(ws.colour_is_moot("* Random Cycle"))

        # Palettes 2-5 build their palette from colors[0..2].
        for name in ("* Color 1", "Color 1", "Colors 1&2", "Color Gradient", "Colors Only"):
            self.assertFalse(ws.colour_is_moot(name), f"{name} is colour-driven")

        # A genuine gradient palette ignores col.
        self.assertTrue(ws.colour_is_moot("Party"))
        self.assertTrue(ws.colour_is_moot("Cloud"))


class TestVerify(unittest.TestCase):
    def test_detects_wrong_effect(self):
        state = {"seg": [{"id": 0, "fx": 5, "pal": 2, "col": [[255, 0, 0]]}]}
        self.assertTrue(ws.verify_applied(state, fx=9))
        self.assertFalse(ws.verify_applied(state, fx=5))

    def test_detects_silently_ignored_palette(self):
        # pal is dropped for non-RGB segments and the 200 says nothing — readback is the only way.
        state = {"seg": [{"id": 0, "fx": 5, "pal": 0}]}
        problems = ws.verify_applied(state, fx=5, pal=3)
        self.assertTrue(any("pal" in p for p in problems))

    def test_no_segments_is_a_problem(self):
        self.assertTrue(ws.verify_applied({"seg": []}, fx=1))

    def test_unselected_segments_report_the_real_reason(self):
        # A seg body with no id only touches SELECTED segments (json.cpp:470-474). If none are
        # selected, nothing was written — that must not surface as "wrong effect applied".
        state = {"seg": [{"id": 0, "sel": False, "fx": 99}]}
        problems = ws.verify_applied(state, fx=1)
        self.assertTrue(any("selected" in p for p in problems), problems)
        self.assertFalse(any("fx=" in p for p in problems), "should not claim a wrong effect")

    def test_non_rgb_segment_does_not_false_fail(self):
        # WLED skips pal and forces col to white on non-RGB segments (lc bit 0 = RGB), so checking
        # them there would pin the exit code to 1 forever.
        state = {"seg": [{"id": 0, "sel": True, "lc": 0, "fx": 5, "pal": 0,
                          "col": [[255, 255, 255]]}]}
        self.assertFalse(ws.verify_applied(state, fx=5, pal=3, col=[255, 0, 0]))
        # ...but a wrong effect is still caught there.
        self.assertTrue(ws.verify_applied(state, fx=9))

    def test_rgbw_four_channel_colour_does_not_false_fail(self):
        state = {"seg": [{"id": 0, "sel": True, "lc": 3, "fx": 5,
                          "col": [[255, 0, 0, 0]]}]}
        self.assertFalse(ws.verify_applied(state, fx=5, col=[255, 0, 0]))


# --- cross-version integration against stub servers --------------------------------------------
bodies = []          # raw POST bodies seen by any stub, for assertions on what went on the wire


class _Stub(BaseHTTPRequestHandler):
    tables = {}
    posts = None          # list; appended to on any POST so a write cannot pass unnoticed

    def do_GET(self):
        body = self.tables.get(self.path)
        if body is None:
            self.send_response(404); self.end_headers(); return
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        # Record rather than 501. A 501 would be swallowed by post_json into a quiet "failed" row,
        # so the dry-run test would pass even if it HAD written.
        if self.posts is not None:
            self.posts.append(self.path)
            n = int(self.headers.get("Content-Length") or 0)
            bodies.append(self.rfile.read(n).decode() if n else "{}")
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *a):
        pass


def _serve(tables, posts=None):
    handler = type("H", (_Stub,), {"tables": tables, "posts": posts})
    srv = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class TestCrossVersionOverHTTP(unittest.TestCase):
    """Only one real device is reachable, so two firmware versions are stood up locally."""

    @classmethod
    def setUpClass(cls):
        cls.s16 = _serve({
            "/json/info": {"brand": "WLED", "name": "new", "ver": "16.0.1",
                           "fxcount": len(EFF_16), "leds": {"count": 10}},
            "/json/eff": EFF_16, "/json/pal": PAL_FIXED})
        cls.s14 = _serve({
            "/json/info": {"brand": "WLED", "name": "old", "ver": "0.14.4",
                           "fxcount": len(EFF_014), "leds": {"count": 10}},
            "/json/eff": EFF_014, "/json/pal": PAL_FIXED})

    @classmethod
    def tearDownClass(cls):
        for srv in (cls.s16, cls.s14):
            srv.shutdown()
            srv.server_close()

    def test_same_name_resolves_per_device(self):
        scan = ws.load_scanner()
        results = {}
        for srv in (self.s16, self.s14):
            host = f"127.0.0.1:{srv.server_port}"
            info = scan.probe(host, 2.0)
            self.assertIsNotNone(info, "stub should look like a WLED device")
            eff = scan.get_json(host, "/json/eff", 2.0)
            idx, err = ws.resolve_effect("Hiphotic", eff)
            self.assertIsNone(err)
            results[info["name"]] = idx
        self.assertNotEqual(results["new"], results["old"],
                            "same name must map to different indices per firmware")

    def test_fxcount_mismatch_is_caught(self):
        # If serializeModeNames ever drops a mode, position != index and every write is wrong.
        srv = _serve({"/json/info": {"brand": "WLED", "name": "bad", "fxcount": 999,
                                     "leds": {"count": 1}},
                      "/json/eff": EFF_16, "/json/pal": PAL_FIXED})
        try:
            scan = ws.load_scanner()
            host = f"127.0.0.1:{srv.server_port}"
            dev = scan.probe(host, 2.0)

            class A:
                effect, palette, color, dry_run = "Hiphotic", None, None, True
                speed = intensity = None
            row = ws.sync_one(scan, dev, A(), None, 2.0)
            self.assertEqual(row["status"], "skipped")
            self.assertIn("indices unsafe", row["detail"])
        finally:
            srv.shutdown()
            srv.server_close()

    def test_apply_forwards_speed_and_intensity(self):
        # The line forwarding args.speed into build_body had no coverage at all: every existing
        # test used dry_run=True and returned before reaching it. This also makes the stubs'
        # `speed`/`intensity` attributes load-bearing rather than decorative.
        posts = []
        srv = _serve({"/json/info": {"brand": "WLED", "name": "apply", "ver": "16.0.1",
                                     "fxcount": len(EFF_16), "leds": {"count": 10}},
                      "/json/eff": EFF_16, "/json/pal": PAL_FIXED}, posts=posts)
        try:
            scan = ws.load_scanner()
            host = f"127.0.0.1:{srv.server_port}"
            dev = scan.probe(host, 2.0)

            class A:
                effect, palette, color, dry_run = "Hiphotic", None, None, False
                speed, intensity = 120, 30
            ws.sync_one(scan, dev, A(), 4242, 2.0)
            self.assertEqual(posts, ["/json/state"], "apply path must POST exactly once")
            body = json.loads(bodies[-1])
            self.assertEqual(body["seg"]["sx"], 120)
            self.assertEqual(body["seg"]["ix"], 30)
            self.assertEqual(body["tb"], 4242)
            self.assertTrue(body["udpn"]["nn"])
        finally:
            srv.shutdown()
            srv.server_close()

    def test_dry_run_writes_nothing(self):
        # The stub ACCEPTS POSTs and records them, so a stray write is caught. (Returning 501
        # instead would be swallowed by post_json into a quiet "failed" row and this would pass
        # even if it had written.)
        posts = []
        srv = _serve({"/json/info": {"brand": "WLED", "name": "dry", "ver": "16.0.1",
                                     "fxcount": len(EFF_16), "leds": {"count": 10}},
                      "/json/eff": EFF_16, "/json/pal": PAL_FIXED}, posts=posts)
        try:
            scan = ws.load_scanner()
            host = f"127.0.0.1:{srv.server_port}"
            dev = scan.probe(host, 2.0)

            class A:
                effect, palette, color, dry_run = "Hiphotic", "Party", None, True
                speed = intensity = None
            row = ws.sync_one(scan, dev, A(), 12345, 2.0)
            self.assertEqual(row["status"], "would apply")
            self.assertIn("fx=", row["detail"])
            self.assertEqual(posts, [], "--dry-run must not POST anything")
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
