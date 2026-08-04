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
        self.assertIn("2.4GHz only", why)

    def test_secured_stranger_is_not(self):
        ok, _ = aj.is_candidate(net("CBCI-3B76", open_=False, band=2), PATTERNS, True)
        self.assertFalse(ok)

    def test_open_probe_can_be_disabled(self):
        ok, _ = aj.is_candidate(net("Trancender", open_=True, band=2), PATTERNS, False)
        self.assertFalse(ok, "--no-open-probe must exclude open APs")

    def test_our_own_networks_are_never_candidates(self):
        # Without this a run joins the home network, finds no WLED device, and then FORGETS it —
        # removing it from the preferred list on the way past.
        ok, why = aj.is_candidate(net("HomeNet", open_=True, band=2), PATTERNS, True,
                                  exclude=("HomeNet", "TargetNet"))
        self.assertFalse(ok)
        self.assertIn("our own", why)
        ok, _ = aj.is_candidate(net("homenet", open_=True, band=2), PATTERNS, True,
                                exclude=("HomeNet",))
        self.assertFalse(ok, "exclusion must be case-insensitive")
        # ...and a WLED-named AP is still excluded if it happens to be one of ours.
        ok, _ = aj.is_candidate(net("WLED-AP"), PATTERNS, True, exclude=("WLED-AP",))
        self.assertFalse(ok)

    def test_unknown_band_fails_closed(self):
        # Testing `!= 5` would let an unknown band through; require 2.4 positively.
        for band in (None, -1, 6, 5):
            ok, _ = aj.is_candidate(net("Mystery", open_=True, band=band), PATTERNS, True)
            self.assertFalse(ok, f"band={band} must not be a candidate")
        ok, _ = aj.is_candidate(net("Mystery", open_=True, band=2), PATTERNS, True)
        self.assertTrue(ok)

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

    def test_oui_table_is_the_full_assignment_not_a_sample(self):
        # Regression for the first live run: WLED-TOUCH-BOX reported 2c:bc:bb — a real Espressif
        # prefix that the original hand-picked 34-entry table lacked — so an actual WLED device was
        # refused with "MAC is not Espressif". The table is now every prefix IEEE has assigned to
        # Espressif; a count assertion is what catches someone trimming it back to "the ones we
        # use", which is precisely how the bug was introduced.
        self.assertTrue(aj.is_espressif("2cbcbbd98960"), "the OUI that broke the first live run")
        self.assertGreaterEqual(len(aj.ESPRESSIF_OUIS), 331)
        # `o == o.lower()`, not `o.islower()`: an all-digit prefix like "202565" has no cased
        # characters, so islower() is False for it and the assertion would fail on valid data.
        bad = [o for o in aj.ESPRESSIF_OUIS
               if len(o) != 6 or o != o.lower() or not all(c in "0123456789abcdef" for c in o)]
        self.assertEqual(bad, [], "entries must be lowercase 6-hex-char prefixes — is_espressif "
                                  "lowercases its input, so an uppercase entry never matches")

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
    def __init__(self, join_ok=True, addr="4.3.2.2"):
        self.join_ok = join_ok
        self.addr = addr                 # None = associated but DHCP never completed
        self.before_addr = "192.168.1.26"   # what the host had before any join
        self.joined, self.forgotten, self.waited = [], [], []

    def join(self, ssid, password=None, timeout=30):
        self.joined.append(ssid)
        return (True, None) if self.join_ok else (False, "no such network")

    def current_address(self):
        return self.before_addr

    def wait_for_address(self, deadline_s=20, different_from=None):
        self.waited.append(deadline_s)
        if self.addr is not None and different_from and self.addr == different_from:
            return None          # never left the network we started on
        return self.addr

    def forget(self, ssid):
        self.forgotten.append(ssid)


class Args:
    ssid, password, pin = "HomeNet", "homepass", None
    ap_password, connect_timeout = "wled1234", 5
    dry_run, max_attempts = False, 2
    # push_confirm_timeout is deliberately tiny: the give-up path polls for this long TWICE (push,
    # retry, give up), and at the 12s production default that is 24s of sleeping in a unit suite.
    ap_settle_timeout, push_confirm_timeout = 20, 1
    inspect = False
    write_settle = 0.0
    join_retries, join_retry_delay = 1, 0.0


class TestProvisionOne(unittest.TestCase):
    """provision_one must never leave state behind and never push to something unidentified."""

    def setUp(self):
        self.posts = []
        self.gets = []
        # Path-aware: /json/cfg is what confirm_cfg reads back, and it has to be able to disagree
        # with what /json/info says, or the "config did not persist" path cannot be tested at all.
        self.cfg = {"nw": {"ins": [{"ssid": Args.ssid, "pskl": len(Args.password)}]}}
        self._get, self._post = aj.get_json, aj.post_json

        def fake_get(host, path, timeout=5):
            self.gets.append(path)
            return self.cfg if path == "/json/cfg" else self.info

        aj.get_json = fake_get
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

    def test_does_not_reboot_a_device_whose_config_never_persisted(self):
        # Live regression: POST /json/cfg applies the values but defers the flash write to a later
        # main-loop pass, so an immediate /reset can reboot the device before cfg.json is written
        # and the credentials vanish. Observed on WLED-TOUCH-MATRIX — reported `pushed`, came back
        # up still broadcasting its AP. Rebooting unconfirmed turns a fixable failure into a
        # confident lie in the report.
        self.info = {"brand": "WLED", "mac": ESP_MAC}
        self.cfg = {"nw": {"ins": [{"ssid": "SomethingElse", "pskl": 0}]}}  # push never took
        plat, rep = FakePlatform(), []
        aj.provision_one(plat, net("WLED-AP"), Args(), set(), {}, rep)
        self.assertEqual(rep[0][1], "push-failed")
        self.assertNotIn("/reset", self.gets, "must not reboot a device with unsaved config")
        self.assertEqual(len([p for p, _ in self.posts if p == "/json/cfg"]), 2,
                         "should retry the push once before giving up")

    def test_confirms_the_config_before_rebooting(self):
        self.info = {"brand": "WLED", "mac": ESP_MAC}
        plat, rep = FakePlatform(), []
        aj.provision_one(plat, net("WLED-AP"), Args(), set(), {}, rep)
        self.assertEqual(rep[0][1], "pushed")
        self.assertIn("/json/cfg", self.gets, "must read the config back")
        self.assertLess(self.gets.index("/json/cfg"), self.gets.index("/reset"),
                        "the read-back has to happen BEFORE the reboot to mean anything")

    def test_a_saved_ssid_with_an_unsaved_passphrase_is_not_success(self):
        # WLED never echoes the PSK, it returns the stored length as `pskl`. Confirming only the
        # SSID would call this a success and reboot a device that can never authenticate — the
        # symptom being a device that looks configured and silently never appears on the network.
        self.info = {"brand": "WLED", "mac": ESP_MAC}
        self.cfg = {"nw": {"ins": [{"ssid": Args.ssid, "pskl": 0}]}}
        plat, rep = FakePlatform(), []
        aj.provision_one(plat, net("WLED-AP"), Args(), set(), {}, rep)
        self.assertEqual(rep[0][1], "push-failed")
        self.assertNotIn("/reset", self.gets)

    def test_missing_pskl_does_not_fail_an_otherwise_good_device(self):
        # Older builds omit `pskl`. Absent must mean "cannot disprove", not "wrong".
        self.info = {"brand": "WLED", "mac": ESP_MAC}
        self.cfg = {"nw": {"ins": [{"ssid": Args.ssid}]}}
        plat, rep = FakePlatform(), []
        aj.provision_one(plat, net("WLED-AP"), Args(), set(), {}, rep)
        self.assertEqual(rep[0][1], "pushed")

    def test_settles_after_confirming_before_it_reboots(self):
        # The read-back proves the values are in RAM; GET /json/cfg serves the in-memory config
        # either way. WLED writes cfg.json on a later main-loop pass, so confirming on the first
        # poll and resetting at once can beat the write — the device then accepts the push, reads
        # it back, and reverts on the next real power cycle. Assert the settle actually happens
        # between the confirmation and the reboot.
        self.info = {"brand": "WLED", "mac": ESP_MAC}
        a = Args(); a.write_settle = 0.05
        slept = []
        real_sleep = aj.time.sleep
        aj.time.sleep = lambda s: slept.append(s) or real_sleep(0)
        try:
            plat, rep = FakePlatform(), []
            aj.provision_one(plat, net("WLED-AP"), a, set(), {}, rep)
        finally:
            aj.time.sleep = real_sleep
        self.assertEqual(rep[0][1], "pushed")
        self.assertIn(0.05, slept, "must wait for the deferred write before /reset")
        self.assertIn("/reset", self.gets)

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

    def test_inspect_pushes_nothing_and_does_not_reboot(self):
        # --inspect exists to diagnose a device that will not join, so it runs against hardware
        # someone is already unsure about. It must be provably read-only: no POST, no /reset, and
        # the AP still forgotten on the way out.
        self.info = {"brand": "WLED", "mac": ESP_MAC}
        a = Args(); a.inspect = True
        plat, rep = FakePlatform(), []
        aj.provision_one(plat, net("WLED-AP"), a, set(), {}, rep)
        self.assertEqual(self.posts, [], "--inspect must never POST")
        self.assertNotIn("/reset", self.gets, "--inspect must never reboot the device")
        self.assertEqual(rep[0][1], "inspected")
        self.assertIn("WLED-AP", plat.forgotten)

    def test_dry_run_pushes_nothing(self):
        self.info = {"brand": "WLED", "mac": ESP_MAC}
        a = Args(); a.dry_run = True
        plat, rep = FakePlatform(), []
        aj.provision_one(plat, net("WLED-AP"), a, set(), {}, rep)
        self.assertEqual(self.posts, [])
        self.assertEqual(rep[0][1], "would-push")

    def test_waits_for_dhcp_before_speaking_ip(self):
        # Live regression: networksetup returns on association, before DHCP. Probing immediately
        # got "no /json/info response" — reported as `not-wled`, i.e. a real device misclassified
        # as a stranger. Same two devices, two consecutive runs, opposite answers, purely on
        # timing. The wait must happen BEFORE the first HTTP request, not as a longer timeout on it.
        self.info = {"brand": "WLED", "mac": ESP_MAC}
        plat, rep = FakePlatform(), []
        aj.provision_one(plat, net("WLED-AP"), Args(), set(), {}, rep)
        self.assertEqual(plat.waited, [20], "must wait for an address before any HTTP")
        self.assertEqual(rep[0][1], "pushed")

    def test_no_dhcp_lease_is_a_join_failure_not_a_verdict_on_the_device(self):
        # Never let "we could not get an address" masquerade as "not a WLED device": that reads as
        # a hardware problem and sends someone debugging the wrong box.
        self.info = {"brand": "WLED", "mac": ESP_MAC}
        plat, rep = FakePlatform(addr=None), []
        aj.provision_one(plat, net("WLED-AP"), Args(), set(), {}, rep)
        self.assertEqual(rep[0][1], "join-failed")
        self.assertNotEqual(rep[0][1], "not-wled")
        self.assertEqual(self.posts, [], "nothing may be sent without an address")
        self.assertEqual(self.gets, [], "must not even probe before the lease lands")
        self.assertIn("WLED-AP", plat.forgotten)

    def test_an_unchanged_address_is_a_join_failure_not_a_verdict(self):
        # networksetup can report success while leaving the host exactly where it was. Any
        # "do we have an address?" check then passes instantly on the HOME address, and the run
        # probes 4.3.2.1 from the wrong subnet and blames the device: `not-wled  no /json/info
        # response`. Seen live against WLED-TOUCH-MATRIX.
        self.info = {"brand": "WLED", "mac": ESP_MAC}
        plat, rep = FakePlatform(addr="192.168.1.26"), []      # same address as before the join
        aj.provision_one(plat, net("WLED-AP"), Args(), set(), {}, rep)
        self.assertEqual(rep[0][1], "join-failed")
        self.assertIn("never left", rep[0][2])
        self.assertEqual(self.gets, [], "must not probe from the network we never left")
        self.assertEqual(self.posts, [])

    def test_retries_the_join_before_giving_up(self):
        # macOS serves cached scan results, so a candidate can be an artifact of an AP that has
        # already cycled off. One attempt per sighting loses that race repeatedly; the retry has
        # to happen in place, because re-scanning to retry just re-reads the same cache.
        self.info = {"brand": "WLED", "mac": ESP_MAC}
        a = Args(); a.join_retries, a.join_retry_delay = 3, 0.0
        plat, rep = FakePlatform(join_ok=False), []
        aj.provision_one(plat, net("WLED-AP"), a, set(), {}, rep)
        self.assertEqual(len(plat.joined), 3, "must actually retry, not just report that it did")
        self.assertEqual(rep[0][1], "join-failed")
        self.assertIn("after 3 attempts", rep[0][2])
        self.assertIn("WLED-AP", plat.forgotten)

    def test_stops_retrying_as_soon_as_it_is_on(self):
        self.info = {"brand": "WLED", "mac": ESP_MAC}
        a = Args(); a.join_retries, a.join_retry_delay = 5, 0.0
        plat, rep = FakePlatform(), []
        aj.provision_one(plat, net("WLED-AP"), a, set(), {}, rep)
        self.assertEqual(len(plat.joined), 1, "a successful join must not keep retrying")
        self.assertEqual(rep[0][1], "pushed")

    def test_forgets_even_when_join_fails(self):
        # This assertion was missing: the test passed against code that did the OPPOSITE of its
        # name. A failed join can still have added the SSID to the preferred list, so forgetting
        # it matters most on exactly this path.
        self.info = None
        plat, rep = FakePlatform(join_ok=False), []
        aj.provision_one(plat, net("WLED-AP"), Args(), set(), {}, rep)
        self.assertEqual(rep[0][1], "join-failed")
        self.assertEqual(self.posts, [])
        self.assertIn("WLED-AP", plat.forgotten, "must forget the AP even when the join failed")

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
