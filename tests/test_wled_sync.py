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

spec = importlib.util.spec_from_file_location("wled_sync", os.path.join(REPO_ROOT, "wled_sync.py"))
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

    def test_no_phase_omits_timebase(self):
        self.assertNotIn("tb", ws.build_body(fx=1, timebase=None))

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

    def test_moot_detection(self):
        # Gradient/default palettes drive colour themselves; --color would do nothing visible.
        self.assertTrue(ws.colour_is_moot("Default"))
        self.assertTrue(ws.colour_is_moot("Party"))
        self.assertFalse(ws.colour_is_moot("* Color 1"))
        self.assertFalse(ws.colour_is_moot("Random Cycle"))


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


# --- cross-version integration against stub servers --------------------------------------------
class _Stub(BaseHTTPRequestHandler):
    tables = {}

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

    def log_message(self, *a):
        pass


def _serve(tables):
    handler = type("H", (_Stub,), {"tables": tables})
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
            row = ws.sync_one(scan, dev, A(), None, 2.0)
            self.assertEqual(row["status"], "skipped")
            self.assertIn("indices unsafe", row["detail"])
        finally:
            srv.shutdown()
            srv.server_close()

    def test_dry_run_writes_nothing(self):
        # The stub has no POST handler at all, so any write attempt would fail loudly.
        scan = ws.load_scanner()
        host = f"127.0.0.1:{self.s16.server_port}"
        dev = scan.probe(host, 2.0)

        class A:
            effect, palette, color, dry_run = "Hiphotic", "Party", None, True
        row = ws.sync_one(scan, dev, A(), 12345, 2.0)
        self.assertEqual(row["status"], "would apply")
        self.assertIn("fx=", row["detail"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
