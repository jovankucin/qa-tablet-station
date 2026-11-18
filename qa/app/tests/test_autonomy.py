"""Tests for the hands-off check (qa/runner/autonomy.py) and its use in the run plan.

python3 -I qa/app/tests/test_autonomy.py  (no network, no devices)
"""
import json
import os
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(TESTS_DIR)
sys.path[:0] = [TESTS_DIR, APP_DIR, os.path.join(os.path.dirname(APP_DIR), "runner")]
import autonomy  # noqa: E402
import plan  # noqa: E402
import test_worker  # noqa: E402

ENG = {"env": "staging", "test_data": {"site": {"id": "S1", "env": "staging"}, "mock_drone": {"vehicle_id": 99900}}}
READ = {"n": 1, "text": "read the page", "gates": [], "read_js": "location.pathname"}


def case(**kw):
    c = {"kind": "case", "id": "X-001", "status": "active", "targets": "tablet", "steps": [READ], "needs": {}}
    c.update(kw)
    return c


def codes(r):
    return [b["code"] for b in r["blockers"]]


class CheckTests(unittest.TestCase):
    def test_a_read_only_active_case_is_hands_off(self):
        r = autonomy.check(case(), ENG)
        self.assertTrue(r["hands_off"])
        self.assertIn("no_pin", [c["code"] for c in r["checks"]])  # piece 1: the engine brings the app to front

    def test_blockers(self):
        self.assertEqual(codes(autonomy.check(case(status="draft"), ENG)), ["draft"])
        self.assertIn("no_steps", codes(autonomy.check(case(steps=[]), ENG)))
        self.assertIn("step_in_words", codes(autonomy.check(case(steps=[{"n": 1, "text": "tap it", "gates": []}]), ENG)))
        hand = {"n": 2, "text": "By hand: pick the file in the file browser", "gates": ["handover"],
                "helper": "act.py {target} wait --expect text:x"}
        r = autonomy.check(case(steps=[READ, hand]), ENG, can={"file_picker": False})
        self.assertEqual([(b["code"], b["fix"]) for b in r["blockers"]], [("hand_step", autonomy.PIECE["file_picker"])])
        r = autonomy.check(case(steps=[READ, hand]), ENG)  # piece 3 built: the step itself must become sys-tap steps
        self.assertEqual([b["code"] for b in r["blockers"]], ["hand_step"])
        self.assertIn("sys-tap", r["blockers"][0]["fix"])
        write = {"n": 3, "text": "Delete", "gates": ["write_list"], "helper": "act.py {target} tap text:Delete --expect js:true"}
        self.assertIn("write_step", codes(autonomy.check(case(steps=[READ, write]), ENG)))
        nxt = {"n": 3, "text": "Next", "gates": ["write_list"], "helper": "act.py {target} tap text:Next --expect js:true"}
        self.assertNotIn("write_step", codes(autonomy.check(case(steps=[READ, nxt]), ENG)))  # piece 5: standing rule
        self.assertIn("hardware", codes(autonomy.check(case(needs={"link": "real_drone"}), ENG)))

    def test_engine_pieces_switch_blockers_off(self):
        write = {"n": 3, "text": "Next", "gates": ["write_list"], "helper": "act.py {target} tap text:Next --expect js:true"}
        self.assertTrue(autonomy.check(case(steps=[READ, write]), ENG, can={"run_write_steps": True})["hands_off"])
        hand = {"n": 2, "text": "By hand: pick the file in the file browser", "gates": ["handover"],
                "helper": "act.py {target} wait --expect text:x"}
        self.assertFalse(autonomy.check(case(steps=[READ, hand]), ENG, can={"file_picker": True})["hands_off"])
        other_hand = {"n": 2, "text": "Unlock with the PIN", "gates": ["handover"], "helper": "act.py {target} wait --expect js:true"}
        self.assertFalse(autonomy.check(case(steps=[READ, other_hand]), ENG, can={"file_picker": True})["hands_off"])

    def test_web_file_chooser(self):
        drop = {"n": 2, "text": "open the chooser", "gates": [],
                "helper": "act.py {target} tap testid:file-drop-modal-drop-zone --expect js:true"}
        self.assertIn("web_file", codes(autonomy.check(case(targets="both", steps=[READ, drop]), ENG)))
        self.assertTrue(autonomy.check(case(targets="tablet", steps=[READ, drop]), ENG)["hands_off"])

    def test_fixtures_are_delivered_by_the_engine(self):
        fx = [{"name": "a.tif", "path": "tests/x/files/a.tif", "sha256": "0" * 64, "delivery": "engine"}]
        off = autonomy.check(case(fixtures=fx), ENG, can={"deliver_fixtures": False})
        self.assertTrue(off["hands_off"])
        self.assertIn("fixture_on_device", [c["code"] for c in off["checks"]])
        with tempfile.TemporaryDirectory() as root:
            missing = autonomy.check(case(fixtures=fx), ENG, root=root)  # piece 2: the source must be on this Mac
            self.assertEqual(codes(missing), ["fixture_source"])
            os.makedirs(os.path.join(root, "tests", "x", "files"))
            open(os.path.join(root, "tests", "x", "files", "a.tif"), "wb").close()
            r = autonomy.check(case(fixtures=fx), ENG, root=root)
            self.assertTrue(r["hands_off"])
            self.assertIn("fixture_free", [c["code"] for c in r["checks"]])
            self.assertNotIn("fixture_on_device", [c["code"] for c in r["checks"]])

    def test_device_path_in_the_case_schema(self):
        import case_schema

        def problems(**kw):
            f = {"name": "a.tif", "path": "tests/x/files/a.tif", "sha256": "0" * 64, "delivery": "engine"}
            f.update(kw)
            p = []
            case_schema._check_fixtures([f], p)
            return p
        self.assertEqual(problems(), [])
        self.assertEqual(problems(device_path="/sdcard/Download/WingtraTerrainTests/a.tif"), [])
        for bad in ("/sdcard/DCIM/a.tif", "/sdcard/Download/../DCIM/a.tif", "/sdcard/Download/x/b.tif",
                    "/sdcard/Download//a.tif", "sdcard/Download/a.tif", 7):
            self.assertTrue(problems(device_path=bad), bad)

    def test_the_real_terr_004_is_hands_off(self):
        r = autonomy.check_id("TERR-004")
        self.assertTrue(r["hands_off"], r["blockers"])  # c5: pieces 1-3 and 5 (Next runs under the standing rule)


class RunPlanTests(test_worker.Base):
    def test_a_test_whose_ready_case_is_not_hands_off_does_not_start(self):
        test_worker.put(self.root, "qa/cases/TERR-004.c1.json",
                        {"kind": "case", "id": "TERR-004", "status": "draft", "targets": "tablet", "steps": []})
        with self.assertRaisesRegex(plan.PrepError, "hands-off"):
            plan.run_plan(self.root, test_worker.TID, self.day)

    def test_a_hands_off_case_starts(self):
        p = plan.run_plan(self.root, test_worker.TID, self.day)
        self.assertEqual(p["engine_cases"], ["TERR-004"])


if __name__ == "__main__":
    unittest.main()
