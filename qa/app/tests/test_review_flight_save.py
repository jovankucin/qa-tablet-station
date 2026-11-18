"""Independent review of the 2026-11-03 build (map-tap, the own-site flight save, not_covered), adversarial part.

- Export flight saves the open flight (WingtraCloud 82e23ff4a useFlightPlanMenu.ts:198-212), whatever flight and site:
  it is a write-list control now (act.CLOUD_WRITE_TEXT), so New test refuses it and act.py refuses it under the runner,
  also as a standing step and when another selector finds it.
- Page JS (read_js, when_js, a read helper, js: readbacks, differential.api_js) runs under the runner with no gate:
  New test refuses JS that may act (worker.NEW_TEST_JS_ACTS, run.JS_WRITE) and keeps the read-only JS of the cases.
- The QA_ site name: a type step by a label (text:Site name) after the QA_ name could type over it.
- A fresh case built from OFFL-001 c5 plus the drawing playbook's recipe and the own-site save passes validate_case,
  the New test guard, run.standing_step, run.own_site_step and autonomy.check, and act.py parses every helper.
"""
import copy
import glob
import json
import os
import shlex
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(HERE)
QA_DIR = os.path.dirname(APP_DIR)  # this copy's qa folder (qa, or a staging copy), never another one
sys.path[:0] = [APP_DIR, os.path.join(QA_DIR, "runner")]
import act  # noqa: E402
import autonomy  # noqa: E402
import case_schema  # noqa: E402
import run  # noqa: E402
import worker  # noqa: E402

SITE = "a0000001-0000-4000-8000-000000000001"  # the QA engine site OFFL-001 declares (engine.json test_data.site)
ENG = {"env": "staging", "test_data": {"site": {"id": SITE, "env": "staging"}}}
TABLET = "{target}"

# OFFL-001 c5 (qa/cases, 2026-11-03), steps 1-21 and its clean-up, verbatim
OFFL_001_C5 = json.loads(r'''[
{"gates": [], "helper": "act.py {target} tap testid:btn-sites --expect \"js:/Exit without saving/.test(document.body.innerText) || location.pathname === '/sites'\" --timeout 20", "n": 1, "text": "Top bar: Sites (a planner left open by an earlier run asks to save first)"},
{"gates": [], "helper": "act.py {target} tap \"text:Exit without saving\" --expect \"js:location.pathname === '/sites'\" --timeout 20", "n": 2, "text": "Exit without saving, when the app asks (writes nothing)", "when_js": "/Exit without saving/.test(document.body.innerText)"},
{"gates": [], "helper": "act.py {target} tap testid:btn-new-site --expect sel:testid:input-site-name --timeout 20", "n": 3, "text": "New site (opens site creation with the name field)"},
{"gates": [], "helper": "act.py {target} type testid:input-site-name --text \"QA_{target} Offline sites\"", "n": 4, "text": "Site name: QA_{target} Offline sites (the tablet and the test title)"},
{"gates": [], "helper": "act.py {target} type testid:input-location --text \"47.352000, 8.491000\"", "n": 5, "text": "Location: type the lat/long 47.352000, 8.491000"},
{"gates": [], "helper": "act.py {target} wait --expect \"text:47.352000, 8.491000\" --timeout 20", "n": 6, "text": "Wait for the lat/long suggestion"},
{"gates": [], "helper": "act.py {target} tap \"text:47.352000, 8.491000\" --expect \"js:!document.querySelector('[data-testid=location-option]')\" --timeout 15", "n": 7, "pause_s": 1, "text": "Pick the lat/long suggestion: the map centres there at zoom 14 and the default site area is set"},
{"gates": [], "helper": "act.py {target} tap \"text:Enable offline use\" --expect \"js:[...document.querySelectorAll('[role=checkbox]')].some(e => { let p = e; for (let i = 0; i < 4 && p; i++) { if (/Enable offline use/.test(p.innerText || '')) return e.getAttribute('aria-checked') === 'true'; p = p.parentElement; } return false; })\" --timeout 15", "n": 8, "pause_s": 3, "text": "Turn on Enable offline use"},
{"gates": [], "helper": "act.py {target} tap testid:btn-sitecreation-next --expect \"text:Save new site\" --timeout 30", "n": 9, "text": "Next (storage check; goes to the coordinate system step)"},
{"gates": [], "helper": "act.py {target} wait --expect \"js:[...document.querySelectorAll('button')].some(b => b.innerText.trim() === 'Save new site' && !b.disabled)\" --timeout 60", "n": 10, "text": "Wait until Save new site is enabled (coordinate system ready)"},
{"gates": ["cloud_write"], "helper": "act.py {target} tap \"text:Save new site\" --expect \"js:/^\\/sites\\/[0-9a-f-]{36}/.test(location.pathname)\" --timeout 60", "n": 11, "text": "Save new site: creates the QA_ site (cloud write) and starts its offline download"},
{"gates": [], "helper": "act.py {target} wait --expect \"js:/Site available offline|Failed to make|Cannot make site available offline|Not enough storage/.test(document.body.innerText)\" --timeout 300", "n": 12, "text": "Wait for 'Site available offline' (up to 5 min)"},
{"gates": [], "helper": "act.py {target} shot {shots}/{run}_downloaded.png", "n": 13, "text": "Screenshot: the site after the download"},
{"assert": {"expr": "v['download']['site_path'] is True and v['download']['available'] is True and v['download']['failed'] is False", "kind": "scripted"}, "gates": [], "n": 14, "read_js": "({site_path: /^\\/sites\\/[0-9a-f-]{36}/.test(location.pathname), available: /Site available offline/.test(document.body.innerText), failed: /Failed to make|Cannot make site available offline|Not enough storage/.test(document.body.innerText), site_id: (location.pathname.split('/')[2] || null)})", "save_as": "download", "text": "Check the site is available offline"},
{"gates": [], "helper": "act.py {target} wifi off --until-offline", "n": 15, "text": "Wi-Fi off on the tablet; wait until the app shows it is offline"},
{"gates": [], "helper": "act.py {target} wait --expect sel:testid:create-new-flight-button --timeout 30", "n": 16, "text": "Wait for Create flight in the empty site"},
{"gates": [], "helper": "act.py {target} tap testid:create-new-flight-button --expect sel:testid:new-flight-name-input --timeout 20", "n": 17, "text": "Create flight (opens the New flight form)"},
{"gates": [], "helper": "act.py {target} type testid:new-flight-name-input --text \"QA_{target} Offline flight\"", "n": 18, "text": "Flight name: QA_{target} Offline flight"},
{"gates": [], "helper": "act.py {target} wait --expect \"js:[...document.querySelectorAll('[role=dialog] button')].some(b => b.innerText.trim() === 'Create' && !b.disabled)\" --timeout 30", "n": 19, "text": "Wait until Create is enabled (disabled while the site's map data is missing)"},
{"gates": ["cloud_write"], "helper": "act.py {target} tap text:Create --expect \"js:/\\/planning\\/[0-9a-f-]{36}$/.test(location.pathname) || (window.__qaToasts || []).some(x => /Flight successfully created|went wrong|error/i.test(x))\" --timeout 30", "n": 20, "text": "Create: creates the QA_ flight in the new site offline (synced later; cloud write)"},
{"gates": [], "helper": "act.py {target} wait --expect \"js:/\\/planning\\/[0-9a-f-]{36}$/.test(location.pathname) || (window.__qaToasts || []).some(x => /went wrong|error/i.test(x))\" --timeout 30", "n": 21, "text": "Wait for the planner of the new flight"}
]''')
OFFL_001_CLEANUP = json.loads(r'''[
{"always": true, "gates": [], "helper": "act.py {target} tap testid:btn-sites --expect \"js:/Exit without saving/.test(document.body.innerText) || location.pathname === '/sites'\" --timeout 20", "text": "Clean-up: leave the planner (always)"},
{"always": true, "gates": [], "helper": "act.py {target} tap \"text:Exit without saving\" --expect \"js:location.pathname === '/sites'\" --timeout 20", "text": "Exit without saving when asked (always; writes nothing)", "when_js": "/Exit without saving/.test(document.body.innerText)"},
{"always": true, "gates": [], "helper": "act.py {target} wifi on", "text": "Clean-up: Wi-Fi back on (always)"}
]''')
# qa/playbooks/flight-planning-drawing.md "Recipe", exactly as the playbook writes it (steps 1-13 there)
DRAWING = [
    'act.py {target} wait --expect "text:Create area and take-off point" --timeout 30',
    "act.py {target} tap testid:btn-draw-area --expect sel:css:.mapboxgl-map.mode-draw_polygon",
    "act.py {target} map-tap 0.44 0.42",
    "act.py {target} map-tap 0.56 0.42",
    "act.py {target} map-tap 0.56 0.58",
    'act.py {target} map-tap 0.44 0.58 --double --expect "text:A1 - Area"',
    "act.py {target} tap testid:open-tool-button-btn-draw-area --expect text:Corridor",
    "act.py {target} tap text:Corridor --expect sel:css:.mapboxgl-map.mode-draw_line_string",
    "act.py {target} map-tap 0.44 0.34",
    "act.py {target} map-tap 0.50 0.31",
    'act.py {target} map-tap 0.56 0.34 --double --expect "text:C1 - Corridor"',
    "act.py {target} tap testid:btn-takeoff --expect sel:css:.mapboxgl-map.mode-draw_point",
    'act.py {target} map-tap 0.50 0.50 --expect "text:T - Take off"',
    "act.py {target} shot {shots}/{run}_drawn.png",
]
SAVE = 'act.py {target} tap text:Save --expect "text:Save & exit" --timeout 60'
SAVE_EXIT = 'act.py {target} tap "text:Save & exit" --expect "text:Flight successfully saved" --timeout 30'


def fresh_case():
    """OFFL-001 c5 up to the planner of its own flight, the drawing recipe, the own-site save (the planner's Save, then
    Save & exit, both gated write_list + cloud_write and listed), a check, and OFFL-001's clean-up."""
    steps = copy.deepcopy(OFFL_001_C5)
    steps += [{"gates": [], "helper": h, "text": f"drawing recipe {k + 1}"} for k, h in enumerate(DRAWING)]
    steps += [{"gates": ["write_list", "cloud_write"], "helper": SAVE, "text": "Save (opens Flight overview)"},
              {"gates": ["write_list", "cloud_write"], "helper": SAVE_EXIT, "text": "Save & exit: saves the own flight"},
              {"gates": [], "helper": "act.py {target} shot {shots}/{run}_saved.png", "text": "Screenshot: saved"},
              {"gates": [], "read_js": "({saved: document.body.innerText.includes('Flight successfully saved'), "
                                      "path: location.pathname})", "save_as": "saved",
               "assert": {"kind": "scripted", "expr": "v['saved']['saved'] is True"}, "text": "Check the save"}]
    steps += copy.deepcopy(OFFL_001_CLEANUP)
    for n, s in enumerate(steps, 1):
        s["n"] = n
    save_n = [s["n"] for s in steps if s.get("helper") in (SAVE, SAVE_EXIT)]
    return {
        "kind": "case", "schema": 1, "id": "OFFL-901", "created": "2026-11-03T15:00:00.000Z", "author": "claude",
        "supersedes": None, "title": "Offline sites: a QA_ flight drawn and saved offline in its own QA_ site",
        "area": "OFFL",
        "requirement": {"text": "create a flight, add a flight area, corridor and takeoff position in the middle, then "
                                "save the flight", "sha256": case_schema.requirement_sha256(
                                    "create a flight, add a flight area, corridor and takeoff position in the middle, "
                                    "then save the flight"),
                        "source": {"type": "user_chat", "ref": "0000fake008", "custom_id": None,
                                   "locator": "review fixture", "requester": "Owner"}},
        "targets": "tablet", "routing": [], "expected_differences": [],
        "preconditions": [{"id": "P1", "text": "CT account logged in", "check": None, "applies_to": "tablet"}],
        "steps": steps, "fixtures": [], "covered_paths": ["apps/src/pages/FlightPlanning/**"],
        "needs": {"link": "none", "usb_port": False, "account_lock": False, "flags": ["OfflineSites"],
                  "drone_hw": None},
        "status": "active", "expected": "The drawn QA_ flight is saved offline in the case's own QA_ site.",
        "cloud_writes": [{"step": 11, "what": "creates the QA_ site", "object": "new site"},
                         {"step": 20, "what": "creates the QA_ flight", "object": "that site"}]
                        + [{"step": n, "what": "saves the case's own flight", "object": "that flight"}
                           for n in save_n],
        "test_data": {"site_id": SITE, "new_site": "QA_<tablet> Offline sites"},
    }


def nth(case, helper):
    return next(s for s in case["steps"] if s.get("helper") == helper)


def problems(case):
    return [p for p in (worker.new_test_write_problem(s, run, case) for s in case["steps"]) if p]


def act_args(helper):
    args = shlex.split(helper.replace("{target}", "TA5").replace("{shots}", "/tmp/s").replace("{run}", "R"))
    return args[1:]  # act.py's own argv: target verb ...


class FreshCaseTests(unittest.TestCase):
    """Task 3 of the review: one fresh case, as the worker would write it from the playbooks, passes every check."""

    def test_the_playbook_recipe_passes_every_check(self):
        case = fresh_case()
        self.assertEqual(case_schema.validate_case(case), [])
        self.assertEqual(problems(case), [])
        saves = [nth(case, SAVE), nth(case, SAVE_EXIT)]
        for s in saves:
            self.assertTrue(run.flight_save_step(s), s["helper"])
            self.assertTrue(run.standing_step(case, s, ENG), s["helper"])
        site_save = case["steps"][10]
        self.assertTrue(run.own_site_step(case, site_save))
        self.assertFalse(any(run.own_site_step(case, s) for s in case["steps"] if s is not site_save))
        verdict = autonomy.check(case, eng=ENG)
        self.assertTrue(verdict["hands_off"], verdict["blockers"])
        for s in case["steps"]:  # act.py parses every helper as written; map-tap fractions in range
            if s.get("helper"):
                a = act.parser().parse_args(act_args(s["helper"]))
                if a.verb == "map-tap":
                    self.assertTrue(act.MAP_MIN <= min(act.map_fractions(a)) <= max(act.map_fractions(a))
                                    <= act.MAP_MAX, s["helper"])
                    self.assertGreaterEqual(run.step_limit(act_args(s["helper"])[1:]), run.STEP_LIMIT_MIN_S)

    def test_variants_that_would_save_another_flight_are_refused(self):
        variants = {}
        c = fresh_case()  # Export flight in place of Save & exit: saves whatever flight is open
        nth(c, SAVE_EXIT)["helper"] = 'act.py {target} tap "text:Export flight" --expect x'
        variants["Export flight"] = c
        c = fresh_case()  # a JS readback that clicks Save & exit
        nth(c, SAVE)["helper"] = ("act.py {target} tap text:Save --expect \"js:(document.querySelector("
                                  "'[data-testid=btn-save-and-exit]') || {click() {}}).click() || true\"")
        variants["js: readback clicks"] = c
        c = fresh_case()  # read_js that opens another flight under the own site's route
        c["steps"][21]["read_js"] = "location.href = location.pathname.split('/planning/')[0] + '/planning/abc'"
        del c["steps"][21]["helper"]
        variants["read_js navigates"] = c
        c = fresh_case()  # the site name typed over through its label after the QA_ name
        c["steps"][4]["helper"] = 'act.py {target} type "text:Site name" --text "Customer site"'
        variants["site name retyped by its label"] = c
        for why, case in variants.items():
            self.assertNotEqual(problems(case), [], why)
        for s in (nth(variants["site name retyped by its label"], SAVE),
                  nth(variants["site name retyped by its label"], SAVE_EXIT)):
            self.assertFalse(run.standing_step(variants["site name retyped by its label"], s, ENG))


class ExportFlightTests(unittest.TestCase):
    def test_it_is_a_write_list_control(self):
        self.assertIn("export flight", act.WRITE_LIST_TEXT)
        self.assertIn("export flight", run.G3_TEXT)
        self.assertNotIn("export flight", act.FLIGHT_SAVE_TEXT)
        self.assertEqual(act.side_effect_what("export flight"), "cloud_update")

    def test_new_test_refuses_it_by_any_selector_even_for_the_own_flight(self):
        for h in ('act.py {target} tap "text:Export flight" --expect x',
                  'act.py {target} tap "aria:Export  FLIGHT" --expect x',
                  'act.py {target} sys-tap "Export flight" --expect focus:app',
                  'act.py {target} type "text:Export flight" --text x'):
            for gates in ([], ["write_list", "cloud_write"]):
                case = fresh_case()
                s = nth(case, SAVE_EXIT)
                s["helper"], s["gates"] = h, gates
                why = worker.new_test_write_problem(s, run, case)
                self.assertIsNotNone(why, (h, gates))
                self.assertFalse(run.standing_step(case, s, ENG), (h, gates))

    def test_act_refuses_it_under_the_runner_also_as_a_standing_step(self):
        env = {"QA_RUNNER": "1", "QA_STANDING_STEP": "37", "QA_CLOUD_WRITE": "1", "QA_OWN_SITE": "a" * 36}
        with mock.patch.dict(os.environ, env):
            for sel in ("text:Export flight", "aria:Export flight", "text:  export   FLIGHT "):
                a = act.parser().parse_args(["TA5", "tap", sel, "--expect", "path:/x"])
                self.assertFalse(act.standing_flight_save(a), sel)
                self.assertTrue(act.write_list_request(a, True), sel)

    def test_the_element_check_catches_it_under_another_selector(self):
        el = {"label": "Export flight", "text": "Export flight", "testids": ["none", "btn-wrapper-none"]}
        self.assertTrue(act.is_write_list(el, "testid:none"))
        menu = {"label": "Export flight", "text": "Export flight", "testids": []}  # the ... menu item
        self.assertTrue(act.is_write_list(menu, "text:Export flight"))
        self.assertIsNotNone(act.flight_save_control(dict(el, testids=["tool-button"]), "text:Save"))


class FlightSaveLabelsTests(unittest.TestCase):
    """Every flight-save label is on the write list (review 2026-11-03): Save as draft in the exit dialog is the shared
    btn-dialog-action, whose element check found no write-list test id, so it ran under the runner unasked."""

    def test_all_flight_save_labels_are_write_list(self):
        self.assertLessEqual(act.FLIGHT_SAVE_TEXT, act.WRITE_LIST_TEXT)
        self.assertFalse(act.CLOUD_CHANGE_TEXT & act.WRITE_LIST_TEXT)  # test_g3's rule: G3-only stays off the list
        self.assertLessEqual(act.FLIGHT_SAVE_TEXT | {"export flight"}, run.G3_TEXT)

    def test_refused_under_the_runner_without_the_standing_step(self):
        draft = {"label": "Save as draft", "text": "Save as draft", "inDialog": True,
                 "testids": ["btn-dialog-action", "btn-wrapper-btn-dialog-action"]}
        self.assertTrue(act.is_write_list(draft, "text:Save as draft", "Exiting the flight planning Do you want"))
        with mock.patch.dict(os.environ, {"QA_RUNNER": "1"}):
            for k in ("QA_STANDING_STEP", "QA_CLOUD_WRITE"):
                os.environ.pop(k, None)
            for sel in ("text:Save as draft", "text:Save & exit", "aria:Save as draft"):
                a = act.parser().parse_args(["W044", "tap", sel, "--expect", "path:/x"])
                self.assertTrue(act.write_list_request(a, True), sel)
        with mock.patch.dict(os.environ, {"QA_RUNNER": "1", "QA_STANDING_STEP": "36", "QA_CLOUD_WRITE": "1"}):
            for sel in ("text:Save as draft", "text:Save & exit"):  # the standing save still passes, on to its checks
                a = act.parser().parse_args(["W044", "tap", sel, "--expect", "path:/x"])
                self.assertIsNone(act.write_list_request(a, True), sel)
            self.assertIsNone(act.flight_save_control(draft, "text:Save as draft",
                                                      "Exiting the flight planning Do you want"))


ACTING_JS = [
    "document.querySelector('[data-testid=btn-save-and-exit]').click()",
    "[...document.querySelectorAll('button')].find(b => b.innerText === 'Save').click()",
    "document.querySelector('form').requestSubmit()",
    "el.dispatchEvent(new MouseEvent('click', {bubbles: true}))",
    "(0, el['cl' + 'ick'])()",
    "with (document.querySelector('button')) click()",
    "el.cl\\u0069ck()",
    "setTimeout('document.body.click()')",
    "location.href = '/sites/x/planning/y'",
    "location.assign('/x')",
    "history.pushState({}, '', '/x')",
    "fetch(u, {method: 'PUT', headers: h})",
    "fetch(u, {headers: h, method: m})",
    "fetch(u, JSON.parse(s))",
    "window.Capacitor.Plugins.CapacitorHttp.request({url: u, method: m})",
    "const P = window.Capacitor.Plugins; P.WiPPlugin.x()",
    "window.Capacitor.Plugins.Filesystem.deleteFile({path: p})",
    "localStorage.setItem('k', 'v')",
    "queryClient.getMutationCache(); m.mutate(x)",
    "el[Object.keys(el).find(k => k.startsWith('__reactProps'))].onClick({})",  # React's own handler, no DOM click
    "const f = el[k].memoizedProps.onSubmit; f()",
    "store.saveFlight(x)",
]


class PageJsTests(unittest.TestCase):
    def test_acting_js_is_refused_wherever_a_step_runs_it(self):
        for js in ACTING_JS:
            self.assertTrue(worker.js_acts(js, run), js)
            places = [{"read_js": js}, {"helper": "act.py {target} wait --expect x", "when_js": js},
                      {"helper": f"act.py {{target}} read {shlex.quote(js)}"},
                      {"helper": f"act.py {{target}} wait --expect {shlex.quote('js:' + js)}"},
                      {"helper": f"act.py {{target}} tap testid:btn-sites --expect {shlex.quote('js:' + js)}"}]
            for extra in places:
                s = dict({"n": 5, "text": "x", "gates": []}, **extra)
                self.assertIsNotNone(worker.new_test_write_problem(s, run, fresh_case()), (js, extra))

    def test_the_read_only_js_of_the_cases_passes(self):
        seen = 0
        for d in (os.path.join(QA_DIR, "cases"), os.path.join(QA_DIR, "runner", "tests", "cases")):
            for f in sorted(glob.glob(os.path.join(d, "*.json"))):
                with open(f, encoding="utf-8") as fh:
                    case = json.load(fh)
                for s in case.get("steps") or []:
                    for js in run.step_js(s):
                        seen += 1
                        self.assertFalse(worker.js_acts(js, run), (os.path.basename(f), s.get("n"), js[:120]))
        for s in fresh_case()["steps"]:
            for js in run.step_js(s):
                seen += 1
                self.assertFalse(worker.js_acts(js, run), js[:120])
        self.assertGreater(seen, 40)

    def test_the_differential_api_js_is_checked_at_intake(self):
        case = fresh_case()
        case["differential"] = {"values": ["x"], "api_js": "fetch(u, {method: 'DELETE', headers: h})"}
        version = {"file": "qa/cases/OFFL-901.json", "sha256": "a" * 64}
        obj = {"plan_cases": [{"id": "OFFL-901", "file": version["file"], "sha256": version["sha256"], "new": True,
                               "targets": ["TA5"], "text": "draws and saves its own flight"}]}
        import plan  # noqa: E402 (the modules _intake_plan imports itself)
        with mock.patch.object(plan, "case_versions", return_value=[version]), \
                mock.patch.object(autonomy, "load_newest", return_value=(case, version["file"])), \
                mock.patch.object(autonomy, "check_id", return_value={"hands_off": True, "blockers": []}):
            with self.assertRaisesRegex(worker.WorkerError, "differential.api_js runs page JS that may act"):
                worker._intake_plan(obj, {"targets": ["TA5"]}, QA_DIR)
            case["differential"]["api_js"] = "fetch(u, {headers: h})"  # a GET read passes on to the next checks
            try:
                worker._intake_plan(obj, {"targets": ["TA5"]}, QA_DIR)
            except worker.WorkerError as e:
                self.assertNotIn("api_js", e.message)


class AssertTests(unittest.TestCase):
    """A scripted assert runs in the runner's process: New test refuses one that is not a plain check (run.ASSERT_NODES),
    which could otherwise reach os and subprocess and tap or save anything past the write lists and the gate."""

    def test_an_escaping_assert_is_refused(self):
        for expr in ("[c for c in ().__class__.__base__.__subclasses__()] and True",
                     "set([(g := (g.gi_frame.f_back.f_back.f_globals['act'] for _ in [0])) and None] + [y for y in g])",
                     "(lambda: True)()", "v.get('saved')", "__import__('os') and True"):
            case = fresh_case()
            s = case["steps"][-4]  # "Check the save"
            s["assert"]["expr"] = expr
            why = worker.new_test_write_problem(s, run, case)
            self.assertIsNotNone(why, expr)
            self.assertIn("not a plain check", why, expr)

    def test_the_fresh_case_assert_and_the_case_asserts_are_plain(self):
        self.assertIsNone(run.assert_problem(fresh_case()["steps"][-4]["assert"]["expr"]))
        self.assertIsNone(run.assert_problem("v['download']['site_path'] is True and len(v) > 0"))


class SiteNameTests(unittest.TestCase):
    def test_a_type_step_that_may_reach_the_name_field_after_the_qa_name_is_refused(self):
        for h in ('act.py {target} type "text:Site name" --text "Customer site"',
                  'act.py {target} type "aria:Site name" --text "Customer site"',
                  "act.py {target} type testid:none --nth 0 --text Customer",
                  "act.py {target} type testid:btn-wrapper-none --nth 0 --text Customer",
                  "act.py {target} type css:input --text Customer"):
            case = fresh_case()
            case["steps"][4]["helper"] = h  # step 5, after the QA_ name, before Save new site
            self.assertFalse(worker.qa_site_named(case["steps"][10], run, case), h)
            self.assertIsNotNone(worker.new_test_write_problem(case["steps"][10], run, case), h)

    def test_typing_other_fields_by_their_own_test_id_still_passes(self):
        case = fresh_case()  # OFFL-001 types the location by testid:input-location after the name
        self.assertTrue(worker.qa_site_named(case["steps"][10], run, case))
        case["steps"][4]["helper"] = 'act.py {target} type testid:input-site-name --text "Customer site"'
        self.assertFalse(worker.qa_site_named(case["steps"][10], run, case))  # the last name typed counts


if __name__ == "__main__":
    unittest.main()
