"""act.py map-tap (2026-11-03, drawing in the flight planner): a tap at fractions of the one visible Mapbox canvas, on a
tablet through tap_css and on W044 through the Web click, refused outside 0.02..0.98 (Fail 4, before any device call),
with none or several canvases or a covered point (Fail 3) and while a dialog is open (Fail 2). MAP_JS itself runs in
macOS JavaScriptCore (jsc) against a fake DOM; without jsc that part is skipped. No device, no network.

python3 -I qa/runner/tests/test_map_tap.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import act  # noqa: E402
import run  # noqa: E402

FULL = (0, 33, 1920, 1200)        # the WebView on the screen (status bar above), dpr 1.5
CANVAS = [0, 0, 1280, 778]        # the map canvas in CSS px: the whole WebView page
JSC = "/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc"


def page(canvas=CANVAS, canvases=1, dialogs=(), cover=None):
    """MAP_JS's answer as the page would give it: cover (l, t, w, h, label) lies over the canvas there."""
    def answer(fx, fy):
        out = {"canvases": canvases, "dialogs": list(dialogs)}
        if canvases != 1:
            return out
        l, t, w, h = canvas
        x, y = l + fx * w, t + fy * h
        out.update(rect=[l, t, w, h], point=[x, y], hit="canvas")
        if cover and cover[0] <= x < cover[0] + cover[2] and cover[1] <= y < cover[1] + cover[3]:
            out["hit"] = cover[4]
        return out
    return answer


class FakeSess:
    """A page: MAP_JS answers from pages (one per read, the last repeats); --expect readbacks from cond; CDP calls
    recorded."""

    def __init__(self, *pages, cond=True):
        self.pages, self.reads, self.calls, self.cond_value = list(pages), [], [], cond

    def eval(self, expr):
        if expr.startswith(act.MAP_JS):
            fx, fy = json.loads("[" + expr[len(act.MAP_JS):].strip()[1:-1] + "]")
            self.reads.append((fx, fy))
            return self.pages[min(len(self.reads), len(self.pages)) - 1](fx, fy)
        if expr.startswith(act.COND_JS):
            return self.cond_value
        return None

    def call(self, method, params=None):
        assert method in act.Session.ALLOWED, method
        self.calls.append((method, params or {}))
        return {}


class FakeTablet(act.Tablet):
    def __init__(self, sess, focus="app", devtools_rect=FULL, screen=FULL):
        super().__init__("FAKE", {"cdp_port": 1, "tag": "QA9", "dpr": 1.5})
        self.sess, self.rect, self.screen, self.who, self.calls = sess, devtools_rect, screen, focus, []

    def connect(self):
        return {"port": 1, "origin": "web.staging.company.example"}

    def focus(self):
        return self.who, f"mCurrentFocus=Window{{u0 {self.who}/x}}"

    def adb(self, *args, timeout=20, check=False):
        self.calls.append(args)
        if args[:2] == ("shell", "dumpsys input_method | grep -m1 mInputShown || true"):
            return "  mInputShown=false\n"
        return ""

    def webview_origin(self):
        return self.rect or self.screen

    def taps(self):
        return [c[3:] for c in self.calls if c[:3] == ("shell", "input", "tap")]


class KeyboardTablet(FakeTablet):
    """The on-screen keyboard shows until Back."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.keyboard = True

    def adb(self, *args, timeout=20, check=False):
        if args[:2] == ("shell", "dumpsys input_method | grep -m1 mInputShown || true"):
            self.calls.append(args)
            return f"  mInputShown={'true' if self.keyboard else 'false'}\n"
        if args == ("shell", "input", "keyevent", "KEYCODE_BACK"):
            self.keyboard = False
        return super().adb(*args, timeout=timeout, check=check)

    def backs(self):
        return self.calls.count(("shell", "input", "keyevent", "KEYCODE_BACK"))


class FakeWeb(act.Web):
    def __init__(self, sess):
        super().__init__()
        self.sess, self.origin = sess, "web.staging.company.example"

    def connect(self):
        return {"browser_id": "abcd1234", "origin": self.origin}

    def clicks(self):
        return [(p["x"], p["y"]) for m, p in self.sess.calls if m == "Input.dispatchMouseEvent" and p["type"] == "mousePressed"]


class NoDevice:
    """resolve_target for a call that must stop before any device call."""

    def __call__(self, name, profiles):
        raise AssertionError("a device was resolved")


def call(t, argv):
    """act.run on t with argv after the target; (result, Fail)."""
    real = act.resolve_target, act.check_lock, act.load_profiles
    act.resolve_target = t if isinstance(t, NoDevice) else (lambda n, p: t)
    act.check_lock, act.load_profiles = (lambda n: None), (lambda: {})
    try:
        return act.run(act.parser().parse_args(["TA5"] + argv)), None
    except act.Fail as e:
        return None, e
    finally:
        act.resolve_target, act.check_lock, act.load_profiles = real


class Fractions(unittest.TestCase):
    def test_edges_and_inside_are_taken(self):
        for fx, fy in (("0.02", "0.98"), ("0.5", "0.5"), ("0.98", "0.02"), ("1e-1", " 0.3")):
            a = act.parser().parse_args(["TA5", "map-tap", fx, fy])
            self.assertEqual(act.map_fractions(a), (float(fx), float(fy)))

    def test_outside_or_not_a_number_is_refused_before_any_device_call(self):
        for fx, fy in (("0.01", "0.5"), ("0.5", "0.99"), ("0", "0.5"), ("1", "0.5"), ("-0.1", "0.5"), ("nan", "0.5"),
                       ("0.5", "inf"), ("abc", "0.5"), ("0.5,0.4", "0.5")):
            _, e = call(NoDevice(), ["map-tap", fx, fy, "--expect", "js:true"])
            self.assertTrue(e and e.code == 4, (fx, fy, e and e.msg))
        _, e = call(NoDevice(), ["map-tap", "0.5"])
        self.assertTrue(e and e.code == 4 and "two fractions" in e.msg, e and e.msg)
        _, e = call(NoDevice(), ["map-tap", "0.01", "0.5"])
        self.assertIn("outside 0.02..0.98", e.msg)

    def test_map_tap_options_on_other_verbs_are_refused(self):
        _, e = call(NoDevice(), ["tap", "testid:btn-takeoff", "--double", "--expect", "js:true"])
        self.assertTrue(e and e.code == 4 and "--double goes with map-tap" in e.msg)
        _, e = call(NoDevice(), ["tap", "text:Save", "site", "--expect", "js:true"])
        self.assertTrue(e and e.code == 4 and "takes one argument" in e.msg)


class TabletMapTap(unittest.TestCase):
    def test_taps_the_fraction_of_the_canvas_through_tap_css(self):
        t = FakeTablet(FakeSess(page()))
        res, e = call(t, ["map-tap", "0.25", "0.5", "--expect", "text:A1 - Area"])
        self.assertIsNone(e, e and e.msg)
        # CSS (320, 389): screen 0 + 320 x 1.5, 33 + 389 x 1.5
        self.assertEqual(t.taps(), [("480", "616")])
        self.assertEqual(res["map"], {"fractions": [0.25, 0.5], "canvas": [0, 0, 1280, 778], "css": [320.0, 389.0],
                                      "taps": 1})
        self.assertEqual(res["readback"], {"text:A1 - Area": True})

    def test_the_canvas_is_measured_at_call_time(self):
        t = FakeTablet(FakeSess(page(canvas=[100, 50, 800, 600])))
        res, e = call(t, ["map-tap", "0.5", "0.5"])
        self.assertIsNone(e, e and e.msg)
        self.assertEqual(res["map"]["css"], [500.0, 350.0])
        self.assertEqual(t.taps(), [("750", "558")])

    def test_no_expect_is_allowed(self):
        t = FakeTablet(FakeSess(page()))
        res, e = call(t, ["map-tap", "0.3", "0.4"])
        self.assertIsNone(e, e and e.msg)
        self.assertNotIn("readback", res)
        self.assertEqual(len(t.taps()), 1)

    def test_double_taps_the_same_point_twice_after_a_new_read(self):
        sess = FakeSess(page())
        t = FakeTablet(sess)
        res, e = call(t, ["map-tap", "0.43", "0.62", "--double", "--expect", "text:A1 - Area"])
        self.assertIsNone(e, e and e.msg)
        self.assertEqual(len(t.taps()), 2)
        self.assertEqual(t.taps()[0], t.taps()[1])
        self.assertEqual(len(sess.reads), 2, "the map is read again before the second tap")
        self.assertEqual(res["map"]["taps"], 2)
        self.assertGreaterEqual(res["map"]["gap_s"], act.MAP_DOUBLE_GAP_S)
        self.assertEqual(len(res["action"]["taps"]), 2)

    def test_double_stops_when_the_first_tap_opened_a_dialog(self):
        t = FakeTablet(FakeSess(page(), page(dialogs=["Missing basic components"])))
        _, e = call(t, ["map-tap", "0.43", "0.62", "--double"])
        self.assertTrue(e and e.code == 2, e and e.msg)
        self.assertEqual(len(t.taps()), 1, "one tap only")
        self.assertEqual(e.extra["map"]["taps"], 1, "the record keeps the tap that was made")

    def test_a_dialog_over_the_map_is_refused(self):
        t = FakeTablet(FakeSess(page(dialogs=["Exiting the flight planning"])))
        _, e = call(t, ["map-tap", "0.3", "0.4", "--expect", "js:true"])
        self.assertTrue(e and e.code == 2, e and e.msg)
        self.assertIn('a dialog is open over the map ("Exiting the flight planning")', e.msg)
        self.assertEqual(e.extra["device_state"], 'dialog open: "Exiting the flight planning"')
        self.assertEqual(t.taps(), [])

    def test_no_canvas_or_two_canvases_is_an_error(self):
        for n in (0, 2):
            t = FakeTablet(FakeSess(page(canvases=n)))
            _, e = call(t, ["map-tap", "0.3", "0.4"])
            self.assertTrue(e and e.code == 3 and f"{n} visible canvas.mapboxgl-canvas" in e.msg, e and e.msg)
            self.assertEqual(t.taps(), [])

    def test_a_covered_point_is_not_tapped(self):
        sidebar = (880, 0, 400, 778, 'div.flight-planning-sidebar "Flight lines"')
        t = FakeTablet(FakeSess(page(cover=sidebar)))
        _, e = call(t, ["map-tap", "0.8", "0.5"])
        self.assertTrue(e and e.code == 3 and e.msg.startswith("covered: map point (0.8, 0.5)"), e and e.msg)
        self.assertIn("flight-planning-sidebar", e.msg)
        self.assertEqual(t.taps(), [])
        res, e = call(t, ["map-tap", "0.3", "0.5"])     # left of the sidebar: the bare map
        self.assertIsNone(e, e and e.msg)

    def test_the_app_must_be_in_front(self):
        t = FakeTablet(FakeSess(page()), focus="com.android.settings")
        _, e = call(t, ["map-tap", "0.3", "0.4"])
        self.assertTrue(e and e.code == 3 and "focus is not the app" in e.msg, e and e.msg)
        self.assertEqual(t.taps(), [])

    def test_a_stale_webview_rect_is_measured_again_by_tap_css(self):
        # DevTools kept the keyboard-sized WebView: tap_css measures it again from the screen, as for a tap
        t = FakeTablet(FakeSess(page()), devtools_rect=(0, 33, 1920, 694))
        res, e = call(t, ["map-tap", "0.3", "0.8"])
        self.assertIsNone(e, e and e.msg)
        self.assertEqual(t.taps(), [("576", "967")])
        self.assertEqual(res["action"]["taps"][0]["webview"], list(FULL))

    def test_a_readback_that_fails_keeps_the_taps(self):
        t = FakeTablet(FakeSess(page(), cond=False))
        _, e = call(t, ["map-tap", "0.3", "0.4", "--expect", "text:A1 - Area", "--timeout", "0.3"])
        self.assertTrue(e and e.code == 3 and e.msg.startswith("readback"), e and e.msg)
        self.assertEqual(e.extra["map"]["taps"], 1)

    def test_a_map_tap_is_not_a_write(self):
        a = act.parser().parse_args(["TA5", "map-tap", "0.3", "0.4", "--expect", "js:true"])
        self.assertIsNone(act.write_list_request(a, True))
        self.assertIsNone(act.write_list_request(a, False))

    def test_the_keyboard_path_is_the_tap_path(self):
        # review 2026-11-03: a point below the keyboard-sized WebView closes the keyboard with Back (only while Android
        # says it shows), measures the WebView again and taps the same CSS point, as a tap on an element does
        t = KeyboardTablet(FakeSess(page()), devtools_rect=(0, 33, 1920, 694))
        res, e = call(t, ["map-tap", "0.3", "0.8", "--double"])
        self.assertIsNone(e, e and e.msg)
        self.assertEqual(t.backs(), 1, "one Back, while the keyboard showed")
        self.assertEqual(t.taps(), [("576", "967"), ("576", "967")])
        self.assertTrue(res["action"]["taps"][0]["keyboard_closed"])
        self.assertNotIn("keyboard_closed", res["action"]["taps"][1])
        # above the keyboard: no Back, the tap goes where it is
        t = KeyboardTablet(FakeSess(page()), devtools_rect=(0, 33, 1920, 694))
        _, e = call(t, ["map-tap", "0.3", "0.4"])
        self.assertIsNone(e, e and e.msg)
        self.assertEqual((t.backs(), t.taps()), (0, [("576", "500")]))


class WebMapTap(unittest.TestCase):
    def test_clicks_at_the_css_point(self):
        sess = FakeSess(page(canvas=[0, 0, 1440, 820]))
        w = FakeWeb(sess)
        res, e = call(w, ["map-tap", "0.25", "0.5", "--expect", "text:T - Take off"])
        self.assertIsNone(e, e and e.msg)
        self.assertEqual(w.clicks(), [(360.0, 410.0)])
        self.assertEqual(res["action"]["taps"], [{"css": [360.0, 410.0]}])

    def test_double_clicks_twice(self):
        w = FakeWeb(FakeSess(page(canvas=[0, 0, 1440, 820])))
        res, e = call(w, ["map-tap", "0.5", "0.5", "--double"])
        self.assertIsNone(e, e and e.msg)
        self.assertEqual(w.clicks(), [(720.0, 410.0), (720.0, 410.0)])
        self.assertEqual(len([m for m, _ in w.sess.calls if m == "Input.dispatchMouseEvent"]), 6)

    def test_a_dialog_is_refused_on_the_web_too(self):
        w = FakeWeb(FakeSess(page(dialogs=["Abort optimization?"])))
        _, e = call(w, ["map-tap", "0.5", "0.5"])
        self.assertTrue(e and e.code == 2, e and e.msg)
        self.assertEqual(w.clicks(), [])


class RunnerKnowsTheVerb(unittest.TestCase):
    def test_helper_and_step_limit(self):
        self.assertIn("map-tap", run.VERBS)
        step = {"helper": "act.py {target} map-tap 0.43 0.62 --double --expect 'text:A1 - Area'"}
        self.assertEqual(run.helper_call(step), ("map-tap", "0.43", ["text:A1 - Area"]))
        self.assertEqual(run.step_selectors(step), [])
        self.assertEqual(run.step_limit(["map-tap", "0.3", "0.4"]), run.STEP_LIMIT_MIN_S)
        # only its own --timeout counts (no element to find): 100 + the margin
        self.assertEqual(run.step_limit(["map-tap", "0.3", "0.4", "--timeout", "100"]), 100 + run.STEP_MARGIN_S)
        self.assertEqual(run.step_limit(["tap", "testid:x", "--timeout", "100"]), 100 + 10 + run.STEP_MARGIN_S)

    def test_focus_is_checked_like_a_tap(self):
        me = types.SimpleNamespace(is_web=False, prof={"user_agent_model": "M", "dpr": 1.5},
                                   eng={"web_origin": "web.staging.company.example"})
        out = {"ok": True, "identity": {"port": 1, "ua_model": "M", "dpr": 1.5, "origin": "web.staging.company.example"}}
        self.assertIs(run.TargetRun.identity_checks(me, out, "map-tap")["focus_ok"], True)
        self.assertEqual(run.TargetRun.identity_checks(me, dict(out, ok=False), "map-tap")["focus_ok"], "n/a")


@unittest.skipUnless(os.access(JSC, os.X_OK) or shutil.which("jsc"), "no JavaScriptCore jsc on this machine")
class MapJs(unittest.TestCase):
    """MAP_JS in jsc against a fake DOM: canvases, dialogs and the elements over the point, all as rectangles."""

    FAKE_DOM = r"""
var innerWidth = SC.viewport[0], innerHeight = SC.viewport[1];
function el(d) {
  return {tagName: d.tag.toUpperCase(), className: d.cls || '', innerText: d.text || '', _d: d,
    getBoundingClientRect() { const [l, t, w, h] = d.rect; return {left: l, top: t, width: w, height: h, right: l + w, bottom: t + h}; },
    getAttribute(n) { return (d.attrs || {})[n] === undefined ? null : d.attrs[n]; },
    contains(o) { return o === this || (d.kids || []).includes(o && o._d && o._d.id); },
    querySelector() { return null; }};
}
const canvases = SC.canvases.map(el), dialogs = SC.dialogs.map(el), covers = SC.covers.map(el);
function getComputedStyle(e) { return Object.assign({visibility: 'visible', display: 'block', opacity: '1'}, e._d.style || {}); }
const inside = (e, x, y) => { const r = e.getBoundingClientRect(); return x >= r.left && x < r.right && y >= r.top && y < r.bottom; };
var document = {
  querySelectorAll(sel) { return sel === 'canvas.mapboxgl-canvas' ? canvases : dialogs; },
  getElementById() { return null; },
  elementFromPoint(x, y) { return covers.find(c => inside(c, x, y)) || canvases.find(c => inside(c, x, y)) || null; },
};
"""

    def js(self, sc, fx, fy):
        sc = dict({"viewport": [1280, 778], "dialogs": [], "covers": []}, **sc)
        src = "var SC = " + json.dumps(sc) + ";\n" + self.FAKE_DOM + "print(JSON.stringify(" + act.MAP_JS.strip() + \
              f"({json.dumps(fx)}, {json.dumps(fy)})));\n"
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "map.js")
            with open(path, "w", encoding="utf-8") as f:
                f.write(src)
            p = subprocess.run([JSC if os.access(JSC, os.X_OK) else shutil.which("jsc"), path], capture_output=True,
                               text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout.strip().splitlines()[-1])

    CANVAS = {"tag": "canvas", "cls": "mapboxgl-canvas", "rect": [0, 0, 1280, 778]}

    def test_fractions_of_the_canvas(self):
        out = self.js({"canvases": [self.CANVAS]}, 0.25, 0.5)
        self.assertEqual(out, {"canvases": 1, "dialogs": [], "rect": [0, 0, 1280, 778], "point": [320, 389], "hit": "canvas"})
        out = self.js({"canvases": [dict(self.CANVAS, rect=[100, 50, 800, 600])]}, 0.02, 0.98)
        self.assertEqual(out["point"], [116, 638])

    def test_only_the_part_in_the_viewport_counts(self):
        out = self.js({"canvases": [dict(self.CANVAS, rect=[-100, -20, 1500, 900])]}, 0.5, 0.5)
        self.assertEqual(out["rect"], [0, 0, 1280, 778])
        self.assertEqual(out["point"], [640, 389])

    def test_hidden_canvases_do_not_count(self):
        hidden = [dict(self.CANVAS, style={"display": "none"}), dict(self.CANVAS, rect=[0, 0, 0, 0]),
                  dict(self.CANVAS, rect=[0, 900, 300, 200]), dict(self.CANVAS, style={"opacity": "0"})]
        self.assertEqual(self.js({"canvases": hidden + [self.CANVAS]}, 0.5, 0.5)["canvases"], 1)
        two = self.js({"canvases": [self.CANVAS, dict(self.CANVAS, rect=[0, 500, 400, 200])]}, 0.5, 0.5)
        self.assertEqual(two, {"canvases": 2, "dialogs": []})
        self.assertEqual(self.js({"canvases": hidden}, 0.5, 0.5), {"canvases": 0, "dialogs": []})

    def test_dialogs_by_title_outermost_only(self):
        outer = {"id": "o", "tag": "div", "rect": [0, 0, 1280, 778], "text": "Missing basic components\nSave as draft",
                 "attrs": {"role": "dialog"}, "kids": ["i"]}
        inner = {"id": "i", "tag": "div", "rect": [400, 300, 400, 200], "text": "inner", "attrs": {"aria-modal": "true"}}
        gone = {"id": "g", "tag": "div", "rect": [0, 0, 100, 100], "text": "Faded", "style": {"opacity": "0"}}
        out = self.js({"canvases": [self.CANVAS], "dialogs": [outer, inner, gone]}, 0.5, 0.5)
        self.assertEqual(out["dialogs"], ["Missing basic components"])

    def test_what_lies_on_the_point(self):
        sidebar = {"tag": "div", "cls": "flight-planning-sidebar overflow-hidden min-h-full", "rect": [880, 0, 400, 778],
                   "text": "Flight lines", "attrs": {"data-testid": "sidebar"}}
        out = self.js({"canvases": [self.CANVAS], "covers": [sidebar]}, 0.8, 0.5)
        self.assertEqual(out["hit"], 'div[data-testid=sidebar].flight-planning-sidebar.overflow-hidden "Flight lines"')
        self.assertEqual(self.js({"canvases": [self.CANVAS], "covers": [sidebar]}, 0.5, 0.5)["hit"], "canvas")


if __name__ == "__main__":
    unittest.main()
