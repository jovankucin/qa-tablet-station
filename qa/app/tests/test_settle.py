"""case_schema's settle rule (2026-11-02): in a case written from that day on, a step that reads or types never runs
right after an action that waits for nothing (no --expect, or one that is always true)."""
import copy
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(HERE)
QA_DIR = os.path.dirname(APP_DIR)  # this copy's qa folder (qa, or a staging copy), never another one
sys.path.insert(0, APP_DIR)
import case_schema  # noqa: E402

NEW = "2026-11-02T09:00:00.000Z"


def load(name):
    with open(os.path.join(QA_DIR, "cases", name + ".json")) as f:
        return json.load(f)


def settle_problems(case):
    return [p for p in case_schema.validate_case(case) if "waits for nothing" in p]


def fresh(name):
    """A new case written today from an example: different steps would follow, so no supersedes shortcut."""
    c = copy.deepcopy(load(name))
    c["created"], c["supersedes"] = NEW, None
    return c


class SettleTests(unittest.TestCase):
    def test_it_checks_this_copy(self):
        # review 2026-11-03: it read the live qa/app/case_schema and qa/cases, also from a staging copy
        self.assertEqual(os.path.dirname(os.path.abspath(case_schema.__file__)), APP_DIR)

    def test_older_versions_are_not_checked(self):
        self.assertEqual(case_schema.validate_case(load("TERR-005.c5")), [])
        self.assertEqual(case_schema.validate_case(load("TERR-004.c8")), [])

    def test_the_passing_web_example_is_clean(self):
        self.assertEqual(case_schema.validate_case(fresh("PLAN-001.c3")), [])

    def test_a_copy_of_the_tablet_example_must_add_waits_where_it_reads_or_types(self):
        got = settle_problems(fresh("TERR-005.c5"))
        self.assertEqual([p.split(":")[0] for p in got], ["steps[19] (step 20)", "steps[21] (step 22)",
                                                          "steps[24] (step 25)"], got)
        self.assertTrue(got[0].startswith("steps[19] (step 20): types right after step 19 (tap), which waits for "
                                          "nothing"), got)
        self.assertIn("reads right after step 23 (tap)", got[2])  # the screenshot at step 24 does not count as a wait
        # step 18 types after step 17, which waits for the dataset name's default value: not reported

    def test_an_edit_that_keeps_the_steps_is_not_checked(self):
        c = copy.deepcopy(load("TERR-005.c5"))
        c["created"], c["title"] = NEW, "Rotated GeoTIFF fails with a specific error"
        c["supersedes"] = {"id": "TERR-005", "file": "qa/cases/TERR-005.c5.json", "file_sha256": "0" * 64, "why": "title"}
        self.assertEqual(settle_problems(c), [])
        c["steps"][16]["helper"] = "act.py {target} tap text:Next"  # now step 18 types after a tap that waits for nothing
        self.assertEqual([p.split(":")[0] for p in settle_problems(c)][0], "steps[17] (step 18)")

    def test_a_read_right_after_a_tap_without_expect_or_always_true(self):
        for helper in ("act.py {target} tap testid:x", "qa/runner/act.py {target} tap testid:x --expect js:1",
                       "act.py {target} --timeout 10 tap testid:x --expect 'js: true'"):
            c = fresh("PLAN-001.c3")
            c["steps"][6]["helper"] = helper  # step 7, before the read at step 8
            got = settle_problems(c)
            self.assertEqual(len(got), 1, (helper, got))
            self.assertTrue(got[0].startswith("steps[7] (step 8): reads right after step 7 (tap)"), got)

    def test_clean_up_and_drafts_are_free_and_bad_helpers_do_not_crash(self):
        c = fresh("PLAN-001.c3")
        c["steps"][-1]["helper"] = "act.py {target} tap text:x"   # an always clean-up step
        c["steps"].append({"n": 17, "text": "r", "read_js": "1", "gates": [], "always": True})
        self.assertEqual(settle_problems(c), [])
        d = fresh("TERR-005.c5")
        d["status"] = "draft"
        self.assertEqual(settle_problems(d), [])
        e = fresh("PLAN-001.c3")
        e["steps"][0]["helper"] = 5
        self.assertIn("steps[0].helper: must be a command or null", case_schema.validate_case(e))


if __name__ == "__main__":
    unittest.main()
