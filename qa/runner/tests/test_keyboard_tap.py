"""A tablet tap below the WebView's known bounds (2026-11-03, R-20261103-ET65-OFFL-001-01/-03/-04): act.py closes the
keyboard with Back only while Android says it shows, then measures the WebView again from the screen (DevTools' screen
rectangle can be stale: it kept the keyboard-sized WebView after the keyboard closed) and taps; a target still outside
stops as before.

python3 -I qa/runner/tests/test_keyboard_tap.py
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import act  # noqa: E402

FULL = (0, 33, 1920, 1200)
WITH_KEYBOARD = (0, 33, 1920, 694)


class KbTablet(act.Tablet):
    """A tablet whose keyboard is up until Back closes it. rect is what DevTools reported at connect (maybe stale);
    the UI dump (webview_origin with rect None) gives the WebView's real bounds now."""

    def __init__(self, keyboard=True, back_closes=True, devtools_rect=None, screen=None):
        super().__init__("FAKE", {"cdp_port": 1, "tag": "QA9", "dpr": 1.5})
        self.kb, self.back_closes, self.calls, self.screen = keyboard, back_closes, [], screen
        self.rect = devtools_rect

    def adb(self, *args, timeout=20, check=False):
        self.calls.append(args)
        if args[:2] == ("shell", "dumpsys input_method | grep -m1 mInputShown || true"):
            return f"  mInputShown={'true' if self.kb else 'false'}\n"
        if args == ("shell", "input", "keyevent", "KEYCODE_BACK") and self.back_closes:
            self.kb = False
        return ""

    def webview_origin(self):
        if self.rect:
            return self.rect
        if self.screen:
            return self.screen
        return WITH_KEYBOARD if self.kb else FULL

    def taps(self):
        return [c for c in self.calls if c[:3] == ("shell", "input", "tap")]

    def backs(self):
        return [c for c in self.calls if c == ("shell", "input", "keyevent", "KEYCODE_BACK")]


class KeyboardTap(unittest.TestCase):
    def setUp(self):
        self.saved = (act.KEYBOARD_CLOSE_S, act.KEYBOARD_SETTLE_S)
        act.KEYBOARD_CLOSE_S, act.KEYBOARD_SETTLE_S = 0.3, 0

    def tearDown(self):
        act.KEYBOARD_CLOSE_S, act.KEYBOARD_SETTLE_S = self.saved

    def test_closes_the_keyboard_then_taps(self):
        t = KbTablet()
        out = t.tap_css(735, 649)            # 1.5 x 649 + 33 = 1007: under the keyboard (the OFFL-001 Create button)
        self.assertEqual(len(t.backs()), 1)
        self.assertEqual(t.taps(), [("shell", "input", "tap", "1102", "1006")])
        self.assertTrue(out["keyboard_closed"])
        self.assertEqual(out["webview"], list(FULL))

    def test_stale_devtools_rect_is_measured_again_without_back(self):
        # R-20261103-ET65-OFFL-001-03/-04: no keyboard, DevTools still said the WebView ends at 694
        t = KbTablet(keyboard=False, devtools_rect=WITH_KEYBOARD)
        out = t.tap_css(735, 649)
        self.assertEqual(t.backs(), [], "no Back without a keyboard")
        self.assertEqual(t.taps(), [("shell", "input", "tap", "1102", "1006")])
        self.assertNotIn("keyboard_closed", out)
        self.assertEqual(out["webview"], list(FULL))

    def test_a_visible_target_needs_no_back_and_no_new_measure(self):
        t = KbTablet(devtools_rect=WITH_KEYBOARD)
        out = t.tap_css(100, 100)
        self.assertEqual(t.backs(), [])
        self.assertEqual(out["webview"], list(WITH_KEYBOARD))
        self.assertNotIn("keyboard_closed", out)

    def test_really_off_screen_without_keyboard_stops(self):
        t = KbTablet(keyboard=False)
        with self.assertRaises(act.Fail) as e:
            t.tap_css(735, 900)              # 1383: off the screen with no keyboard: Back would leave the page
        self.assertEqual(t.backs(), [])
        self.assertEqual(t.taps(), [])
        self.assertIn("is outside the WebView", e.exception.msg)
        self.assertIn("measured again from the screen", e.exception.msg)

    def test_still_covered_after_back_stops_without_a_tap(self):
        t = KbTablet(back_closes=False)
        with self.assertRaises(act.Fail) as e:
            t.tap_css(735, 649)
        self.assertEqual(len(t.backs()), 1, "one Back only")
        self.assertEqual(t.taps(), [])
        self.assertIn("after closing the keyboard", e.exception.msg)


if __name__ == "__main__":
    unittest.main()
