#!/usr/bin/env python3
"""Host tests for wled_apjoin — pure logic plus a fake platform. No radio, no network.

The assertions that matter most are the safety ones: that a non-WLED AP never receives credentials,
and that every path forgets the AP it joined.
"""

import importlib.util
import os
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "wled_apjoin", os.path.join(REPO_ROOT, "scripts", "wled_apjoin.py"))
aj = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aj)

PATTERNS = aj.DEFAULT_PATTERNS
ESP_MAC = "244cab3383a0"        # the live device's OUI
NON_ESP_MAC = "aabbccddeeff"


def net(ssid, open_=False, band=2, rssi=-50, ch=1):
    return {"ssid": ssid, "open": open_, "band": band, "rssi": rssi, "channel": ch}


class TestCandidates(unittest.TestCase):
    def test_wled_names_match(self):
        for s in ("WLED-AP", "wled-ap", "WLED-A1B2C3", "WLED_AP"):
            ok, _ = aj.is_candidate(net(s), PATTERNS, True)
            self.assertTrue(ok, s)

    def test_open_24ghz_is_a_candidate(self):
        # This is what finds RENAMED devices — they are indistinguishable by name.
        ok, why = aj.is_candidate(net("Trancender", open_=True, band=2), PATTERNS, True)
        self.assertTrue(ok)
        self.assertIn("renamed", why)

    def test_open_5ghz_is_not(self):
        # An ESP32 has no 5GHz radio, so a 5GHz open AP cannot be a WLED device. This is what
        # keeps the tool away from public hotspots (observed: xfinitywifi on channel 44).
        ok, why = aj.is_candidate(net("xfinitywifi", open_=True, band=5), PATTERNS, True)
        self.assertFalse(ok)
        self.assertIn("5GHz", why)

    def test_secured_stranger_is_not(self):
        ok, _ = aj.is_candidate(net("CBCI-3B76", open_=False, band=2), PATTERNS, True)
        self.assertFalse(ok)

    def test_open_probe_can_be_disabled(self):
        ok, _ = aj.is_candidate(net("Trancender", open_=True, band=2), PATTERNS, False)
        self.assertFalse(ok, "--no-open-probe must exclude open APs")

    def test_user_pattern_extends_matching(self):
        ok, _ = aj.is_candidate(net("Studio-Left"), PATTERNS + ["Studio*"], False)
        self.assertTrue(ok)


class TestIdentity(unittest.TestCase):
    def test_espressif_oui(self):
        self.assertTrue(aj.is_espressif(ESP_MAC))
        self.assertTrue(aj.is_espressif("24:4C:AB:33:83:A0"))
        self.assertFalse(aj.is_espressif(NON_ESP_MAC))
        self.assertFalse(aj.is_espressif(None))
        self.assertFalse(aj.is_espressif(""))

    def test_identify_requires_both_brand_and_oui(self):
        ok, mac, _ = aj.identify({"brand": "WLED", "mac": ESP_MAC})
        self.assertTrue(ok)
        self.assertEqual(mac, ESP_MAC)

        ok, _, why = aj.identify({"brand": "NotWLED", "mac": ESP_MAC})
        self.assertFalse(ok)
        self.assertIn("not WLED", why)

        ok, _, why = aj.identify({"brand": "WLED", "mac": NON_ESP_MAC})
        self.assertFalse(ok, "brand alone must not be enough")
        self.assertIn("Espressif", why)

        ok, _, _ = aj.identify(None)
        self.assertFalse(ok)


class TestDecision(unittest.TestCase):
    def test_skip_when_already_on_this_lan(self):
        action, why = aj.decide_action(ESP_MAC, {ESP_MAC}, {}, 2)
        self.assertEqual(action, "skip")
        self.assertIn("already on this LAN", why)

    def test_push_when_on_a_different_lan(self):
        # Adam's rule: a device on another network gets re-pointed, not skipped.
        action, _ = aj.decide_action(ESP_MAC, {"1122334455aa"}, {}, 2)
        self.assertEqual(action, "push")

    def test_case_insensitive_mac_match(self):
        action, _ = aj.decide_action("244CAB3383A0", {ESP_MAC}, {}, 2)
        self.assertEqual(action, "skip")

    def test_gives_up_after_cap(self):
        # Wrong credentials fail silently — WLED stores them, never joins, keeps its AP up. Without
        # a cap the loop re-pushes the same bad credentials forever.
        action, _ = aj.decide_action(ESP_MAC, set(), {ESP_MAC: 2}, 2)
        self.assertEqual(action, "give-up")


class TestConfigBody(unittest.TestCase):
    def test_uses_json_cfg_shape_not_the_settings_form(self):
        body = aj.build_wifi_cfg("Net", "pw")
        self.assertEqual(body["nw"]["ins"][0]["ssid"], "Net")
        self.assertEqual(body["nw"]["ins"][0]["psk"], "pw")
        # The form fields must never appear: POSTing CS0/PW0 to /settings/wifi wipes apSSID,
        # forces apBehavior=0 and disables ESP-NOW, because that handler reads every field
        # unconditionally and missing ones arrive as "".
        flat = str(body)
        for forbidden in ("CS0", "PW0", "AS", "AB", "RE"):
            self.assertNotIn(forbidden, flat)

    def test_pin_included_only_when_given(self):
        self.assertNotIn("pin", aj.build_wifi_cfg("N", "p"))
        self.assertEqual(aj.build_wifi_cfg("N", "p", "1234")["pin"], "1234")


# --- fake platform ------------------------------------------------------------------------------
class FakePlatform:
    def __init__(self, join_ok=True):
        self.join_ok = join_ok
        self.joined, self.forgotten = [], []

    def join(self, ssid, password=None, timeout=30):
        self.joined.append(ssid)
        return (True, None) if self.join_ok else (False, "no such network")

    def forget(self, ssid):
        self.forgotten.append(ssid)


class Args:
    ssid, password, pin = "HomeNet", "homepass", None
    ap_password, connect_timeout = "wled1234", 5
    dry_run, max_attempts = False, 2


class TestProvisionOne(unittest.TestCase):
    """provision_one must never leave state behind and never push to something unidentified."""

    def setUp(self):
        self.posts = []
        self.gets = []
        self._get, self._post = aj.get_json, aj.post_json
        aj.get_json = lambda h, p, timeout=5: self.gets.append(p) or self.info
        aj.post_json = lambda h, p, b, timeout=10: (self.posts.append((p, b)), ("{}", None))[1]

    def tearDown(self):
        aj.get_json, aj.post_json = self._get, self._post

    def test_pushes_to_an_identified_device(self):
        self.info = {"brand": "WLED", "mac": ESP_MAC}
        plat, rep = FakePlatform(), []
        aj.provision_one(plat, net("WLED-AP"), Args(), set(), {}, rep)
        self.assertEqual([p for p, _ in self.posts], ["/json/cfg"])
        self.assertEqual(rep[0][1], "pushed")
        self.assertIn("WLED-AP", plat.forgotten)

    def test_never_pushes_to_a_non_wled_ap(self):
        # The safety property. Open-AP probing means associating to networks that are not ours;
        # sending the home PSK to one would be the serious failure.
        self.info = {"brand": "SomeoneElse"}
        plat, rep = FakePlatform(), []
        aj.provision_one(plat, net("Basement speaker", open_=True), Args(), set(), {}, rep)
        self.assertEqual(self.posts, [], "must not POST to an unidentified AP")
        self.assertEqual(rep[0][1], "not-wled")
        self.assertIn("Basement speaker", plat.forgotten, "must still forget it")

    def test_never_pushes_to_wled_brand_with_foreign_oui(self):
        self.info = {"brand": "WLED", "mac": NON_ESP_MAC}
        plat, rep = FakePlatform(), []
        aj.provision_one(plat, net("WLED-AP"), Args(), set(), {}, rep)
        self.assertEqual(self.posts, [])

    def test_skips_a_device_already_on_this_lan(self):
        self.info = {"brand": "WLED", "mac": ESP_MAC}
        plat, rep = FakePlatform(), []
        aj.provision_one(plat, net("WLED-AP"), Args(), {ESP_MAC}, {}, rep)
        self.assertEqual(self.posts, [], "a working device must not be rewritten")
        self.assertEqual(rep[0][1], "skip")

    def test_dry_run_pushes_nothing(self):
        self.info = {"brand": "WLED", "mac": ESP_MAC}
        a = Args(); a.dry_run = True
        plat, rep = FakePlatform(), []
        aj.provision_one(plat, net("WLED-AP"), a, set(), {}, rep)
        self.assertEqual(self.posts, [])
        self.assertEqual(rep[0][1], "would-push")

    def test_forgets_even_when_join_fails(self):
        self.info = None
        plat, rep = FakePlatform(join_ok=False), []
        aj.provision_one(plat, net("WLED-AP"), Args(), set(), {}, rep)
        self.assertEqual(rep[0][1], "join-failed")
        self.assertEqual(self.posts, [])

    def test_forgets_even_when_identification_raises(self):
        self.info = {"brand": "WLED", "mac": ESP_MAC}
        aj.post_json = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        plat, rep = FakePlatform(), []
        with self.assertRaises(RuntimeError):
            aj.provision_one(plat, net("WLED-AP"), Args(), set(), {}, rep)
        self.assertIn("WLED-AP", plat.forgotten,
                      "forget() must run even when the body of the hop raises")


if __name__ == "__main__":
    unittest.main(verbosity=2)
