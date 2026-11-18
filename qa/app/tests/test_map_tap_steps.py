"""case_schema, run.py and New test's write guard know act.py map-tap (2026-11-03, drawing in the flight planner). The
settle rule treats map-tap as an action that changes the page: a read or a typed value right after one that waits for
nothing is reported; taps in a row need no wait between them. A map tap only draws, so it is no write."""
import copy
import importlib.util
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(HERE)
QA_DIR = os.path.dirname(APP_DIR)
# this app's own case_schema, by path: another test module may have imported a case_schema from elsewhere first
_spec = importlib.util.spec_from_file_location("stage_case_schema_map", os.path.join(APP_DIR, "case_schema.py"))
case_schema = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(case_schema)
sys.path[:0] = [APP_DIR, os.path.join(QA_DIR, "runner")]
import run  # noqa: E402
import worker  # noqa: E402

NEW = "2026-11-03T09:00:00.000Z"
AREA_TOOL = "act.py {target} tap testid:btn-area-tool --expect sel:css:.mapboxgl-map.mode-draw_polygon"
CORNERS = ("act.py {target} map-tap 0.27 0.38", "act.py {target} map-tap 0.43 0.38", "act.py {target} map-tap 0.43 0.62")
FINISH = "act.py {target} map-tap 0.27 0.62 --double --expect 'text:A1 - Area'"
READ = {"read_js": "document.title"}


def case(*helpers):
    """A new active tablet case (from PLAN-001.c3's header) whose steps run these helpers; read_js for a dict."""
    with open(os.path.join(QA_DIR, "cases", "PLAN-001.c3.json"), encoding="utf-8") as f:
        c = json.load(f)
    c["created"], c["supersedes"], c["status"], c["targets"] = NEW, None, "active", "tablet"
    c.pop("cloud_writes", None)  # PLAN-001's own writes are not these steps'
    c["steps"] = []
    for i, h in enumerate(helpers):
        s = {"n": i + 1, "text": f"step {i + 1}", "gates": []}
        s.update(h if isinstance(h, dict) else {"helper": h})
        c["steps"].append(s)
    return c


def settle_problems(c):
    return [p for p in case_schema.validate_case(c) if "waits for nothing" in p]


class MapTapStepTests(unittest.TestCase):
    def test_the_verb_is_known(self):
        self.assertIn("map-tap", case_schema.VERBS)
        self.assertEqual(case_schema.VERBS, run.VERBS, "case_schema.VERBS mirrors run.VERBS")
        self.assertIn("map-tap", case_schema.ACTIONS)
        self.assertEqual(case_schema._helper({"helper": FINISH}), ("map-tap", ["text:A1 - Area"]))
        self.assertEqual(run.helper_call({"helper": FINISH}), ("map-tap", "0.27", ["text:A1 - Area"]))

    def test_an_area_drawn_and_read_is_valid(self):
        self.assertEqual(case_schema.validate_case(case(AREA_TOOL, *CORNERS, FINISH, READ)), [])
        # a wait after a bare map tap settles it too
        wait = "act.py {target} wait --expect 'text:T - Take off'"
        self.assertEqual(case_schema.validate_case(case("act.py {target} tap testid:btn-takeoff --expect x",
                                                        "act.py {target} map-tap 0.35 0.5", wait, READ)), [])

    def test_a_read_or_type_right_after_a_bare_map_tap_is_reported(self):
        got = settle_problems(case(AREA_TOOL, *CORNERS, READ))
        self.assertEqual(len(got), 1, got)
        self.assertTrue(got[0].startswith("steps[4] (step 5): reads right after step 4 (map-tap), which waits for nothing"),
                        got)
        got = settle_problems(case(AREA_TOOL, "act.py {target} map-tap 0.3 0.4 --expect js:true",
                                   "act.py {target} type testid:name --text QA_x"))
        self.assertEqual(len(got), 1, got)
        self.assertIn("types right after step 2 (map-tap)", got[0])

    def test_map_taps_in_a_row_need_no_wait(self):
        self.assertEqual(settle_problems(case(AREA_TOOL, *CORNERS, *CORNERS)), [])

    def test_older_cases_are_not_checked(self):
        c = case(AREA_TOOL, *CORNERS, READ)
        d = copy.deepcopy(c)
        d["created"] = "2026-11-01T09:00:00.000Z"
        self.assertEqual(case_schema.validate_case(d), [])

    def test_a_map_tap_is_no_write(self):
        c = case(AREA_TOOL, *CORNERS, FINISH, READ)
        self.assertEqual(run.cloud_write_steps(c), [])
        for s in c["steps"]:
            if "map-tap" in s.get("helper", ""):
                self.assertEqual(run.step_selectors(s), [], "a map tap names no control")
            self.assertIsNone(worker.new_test_write_problem(s, run, c), s)
        # a case that gates one as a cloud write still meets the guard: map-tap is no way past it
        gated = dict(c["steps"][1], gates=["cloud_write"])
        self.assertIsNotNone(worker.new_test_write_problem(gated, run, c))


if __name__ == "__main__":
    unittest.main()
