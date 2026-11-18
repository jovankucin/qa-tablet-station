"""case_schema and run.py accept act.py wifi steps (2026-11-03, offline sites). The settle rule treats wifi as an action
that changes the page: its --expect (or --until-offline) may be left out only when a wait follows it."""
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
_spec = importlib.util.spec_from_file_location("stage_case_schema", os.path.join(APP_DIR, "case_schema.py"))
case_schema = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(case_schema)
sys.path.insert(0, os.path.join(QA_DIR, "runner"))
import run  # noqa: E402

NEW = "2026-11-03T09:00:00.000Z"
WAIT_OFFLINE = "act.py {target} wait --expect sel:testid:offline-status-indicator --timeout 20"


def case(*helpers, always_last=None):
    """A new active case (from PLAN-001.c3's header) whose steps run these helpers; read_js for a dict."""
    with open(os.path.join(QA_DIR, "cases", "PLAN-001.c3.json"), encoding="utf-8") as f:
        c = json.load(f)
    c["created"], c["supersedes"], c["status"], c["targets"] = NEW, None, "active", "tablet"
    c["steps"] = []
    c.pop("cloud_writes", None)  # PLAN-001's own writes (steps 6 and 9) are not these steps'
    for i, h in enumerate(helpers + ((always_last,) if always_last else ())):
        s = {"n": i + 1, "text": f"step {i + 1}", "gates": []}
        s.update(h if isinstance(h, dict) else {"helper": h})
        c["steps"].append(s)
    if always_last:
        c["steps"][-1]["always"] = True
    return c


def wifi_problems(c):
    return [p for p in case_schema.validate_case(c) if "wifi" in p or "waits for nothing" in p]


SITES = "act.py {target} tap testid:btn-sites --expect path:/sites"
READ = {"read_js": "document.title"}


class WifiStepTests(unittest.TestCase):
    def test_the_verb_is_known(self):
        self.assertIn("wifi", case_schema.VERBS)
        self.assertEqual(case_schema.VERBS, run.VERBS, "case_schema.VERBS mirrors run.VERBS")
        self.assertIn("wifi", case_schema.ACTIONS)
        self.assertEqual(case_schema._helper({"helper": "act.py {target} wifi off --until-offline"}),
                         ("wifi", [case_schema.UNTIL_OFFLINE]))
        self.assertEqual(run.helper_call({"helper": "act.py {target} wifi on --expect js:navigator.onLine"}),
                         ("wifi", "on", ["js:navigator.onLine"]))

    def test_settled_wifi_steps_are_valid(self):
        for helpers in (
                (SITES, "act.py {target} wifi off --until-offline", READ),
                (SITES, "act.py {target} wifi off --expect sel:testid:offline-status-indicator", READ),
                (SITES, "act.py {target} wifi off", WAIT_OFFLINE, READ),
                (SITES, "act.py {target} wifi off", "act.py {target} shot {shots}/{run}_off.png", WAIT_OFFLINE, READ),
                (SITES, "act.py {target} wifi on --expect gone:testid:offline-status-indicator", READ)):
            self.assertEqual(case_schema.validate_case(case(*helpers)), [], helpers)
        # a clean-up step that turns Wi-Fi back on needs nothing after it
        c = case(SITES, "act.py {target} wifi off --until-offline", READ, always_last="act.py {target} wifi on")
        self.assertEqual(case_schema.validate_case(c), [])

    def test_wifi_without_a_wait_after_it(self):
        for helpers in ((SITES, "act.py {target} wifi off", SITES),
                        (SITES, "act.py {target} wifi off --expect js:true", SITES),
                        (SITES, "act.py {target} wifi off", "act.py {target} wait"),
                        (SITES, "act.py {target} wifi off", "act.py {target} shot {shots}/{run}_x.png"),
                        (SITES, "act.py {target} wifi on")):
            got = wifi_problems(case(*helpers))
            self.assertEqual(len(got), 1, (helpers, got))
            self.assertTrue(got[0].startswith("steps[1] (step 2): wifi waits only for the tablet's Wi-Fi state"), got)

    def test_a_read_right_after_wifi_is_reported_once(self):
        got = wifi_problems(case(SITES, "act.py {target} wifi off", READ))
        self.assertEqual(len(got), 1, got)
        self.assertTrue(got[0].startswith("steps[2] (step 3): reads right after step 2 (wifi), which waits for nothing"), got)
        got = wifi_problems(case(SITES, "act.py {target} wifi off", "act.py {target} type testid:name --text QA_x"))
        self.assertEqual(len(got), 1, got)
        self.assertIn("types right after step 2 (wifi)", got[0])

    def test_older_and_draft_cases_are_not_checked(self):
        c = case(SITES, "act.py {target} wifi off", SITES)
        d = copy.deepcopy(c)
        d["created"] = "2026-11-01T09:00:00.000Z"
        self.assertEqual(case_schema.validate_case(d), [])
        d = copy.deepcopy(c)
        d["status"] = "draft"
        self.assertEqual(wifi_problems(d), [])

    def test_wifi_is_a_tablet_step(self):
        c = case(SITES, "act.py {target} wifi off --until-offline", READ)
        for targets in ("both", "web"):
            c["targets"] = targets
            self.assertEqual([p for p in case_schema.validate_case(c) if p.startswith("steps[1].on")],
                             ["steps[1].on: wifi is for tablets; in a case that also runs on web the step says on: tablet"],
                             targets)
        c["targets"], c["steps"][1]["on"] = "both", "tablet"
        self.assertEqual(case_schema.validate_case(c), [])

    def test_wifi_steps_are_not_writes(self):
        c = case(SITES, "act.py {target} wifi off --until-offline", READ)
        self.assertEqual(run.cloud_write_steps(c), [])
        self.assertEqual(run.step_selectors(c["steps"][1]), [])


if __name__ == "__main__":
    unittest.main()
