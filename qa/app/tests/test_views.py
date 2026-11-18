"""Tests of views.py (2026-11-02): the Results cards (/api/results/cards), the quality numbers (/api/stats), the
flight plan download rule and the "explain" task output. Since 2026-11-04 also the results redesign on this fixture
(OneTestTests: card_entries by task, the title rule, History's groups, the one-test answer); its full cases are in
test_results_one_test.py.

python3 -I qa/app/tests/test_views.py [-v]

Every test reads a small fixture workspace in a temp folder (records written below); the real workspace is only read
by the last class, which checks that the numbers add up. Nothing here touches a device, ClickUp or the network.
"""
import contextlib
import hashlib
import http.client
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import types
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(TESTS_DIR)
REAL_ROOT = os.path.dirname(os.path.dirname(APP_DIR))
sys.path.insert(0, APP_DIR)
sys.path.insert(0, os.path.join(os.path.dirname(APP_DIR), "runner"))

import data as data_model  # noqa: E402
import records  # noqa: E402
import redact  # noqa: E402
import security  # noqa: E402
import serve  # noqa: E402
import tasks  # noqa: E402
import views  # noqa: E402
import writes  # noqa: E402

TODAY = "2026-11-02"
PLAN = "2026-10-30-plan"      # campaign of the app tasks
TERR = "2026-11-01-terr"      # engine runs without a task
READY = "2026-11-01-ready"    # blocked, partly, not tested and a device that was not ready
FP = f"tests/{PLAN}/files/QA_A.flightplan"
FP_OTHER = f"tests/{PLAN}/files/other.flightplan"
FP_LINK = f"tests/{PLAN}/files/linked.flightplan"
EXPLAIN = f"tests/{PLAN}/EXPLAIN_T-20261031-001.json"
COMMENT = f"tests/{PLAN}/COMMENT_cu1_T-20261031-001.md"
CU1 = "86cu1abcd"
EMAIL = "planted.person" + "@" + "example.org"
PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")

R1 = "R-20261030-W044-PLAN-001-01"
R2 = "R-20261030-W044-PLAN-001-02"
R3 = "R-20261031-W044-PLAN-001-03"
R4 = "R-20261031-W044-PLAN-001-04"
R5 = "R-20261031-W044-PLAN-001-05"
R6 = "R-20261101-TA5-TERR-001-01"
R7 = "R-20261101-TA5-TERR-001-02"
R8 = "R-20261101-ET65-TERR-001-02"
R9 = "R-20261101-ET65-TERR-001-03"
R10 = "R-20261101-W044-TERR-001-01"
R11 = "R-20261101-TA5-TERR-001-04"
R12 = "R-20261101-ET65-TERR-001-04"
R13 = "R-20261101-W044-TERR-001-04"
R14 = "R-20261101-TA3-TERR-001-04"


def put(root, rel, content):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if isinstance(content, (dict, list)):
        content = records.canonical(content)
    with open(path, "wb") as f:
        f.write(content.encode("utf-8") if isinstance(content, str) else content)
    return path


def sha(root, rel):
    with open(os.path.join(root, *rel.split("/")), "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def ok(n, text=None):
    return {"n": n, "text": text, "readback": {"expected": ["x"], "got": {"x": True}, "ok": True}}


def check(n, expr, result):
    return {"n": n, "assert": {"kind": "scripted", "expr": expr, "result": result, "by": "script"},
            "readback": {"expected": [], "got": None, "ok": True}}


def case(cid, title, targets, steps, expected=None, fixtures=(), status="active", area=None):
    return {"kind": "case", "schema": 1, "id": cid, "title": title, "area": area or cid.split("-")[0],
            "targets": targets, "status": status, "expected": expected, "steps": steps, "fixtures": list(fixtures),
            "created": "2026-10-29T08:00:00.000Z", "supersedes": None}


PLAN_STEPS = [{"n": 1, "text": "Open the planner", "gates": []},
              {"n": 2, "text": "Optimize flight (cloud write)", "gates": []},
              {"n": 3, "text": "After: area rows", "gates": []},
              {"n": 4, "text": "Read the result", "gates": []},
              {"n": 5, "text": "Leave the planner (always)", "gates": [], "always": True},
              {"n": 6, "text": "Exit without saving when asked", "gates": [], "always": True}]
TERR_STEPS = [{"n": 1, "text": "Open the site", "gates": []},
              {"n": 2, "text": "Tablet only: pick the file", "gates": [], "on": "tablet"},
              {"n": 3, "text": "Check the list", "gates": []}]


class Fixture:
    """The fixture workspace; the expected numbers in StatsTests come from these records."""

    def __init__(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="qa-views-test-"))
        self.root = os.path.join(self.tmp, "ws")
        os.makedirs(self.root)
        self.build()

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run(self, rid, case_id, tag, verdict, started, case_sha, steps=(), evidence=(), values=None, harness=None,
            handback=None, supersedes=None, mode="live", camp=PLAN, created=None, ended=None, decided=None):
        web = tag == "W044"
        v = {"v": verdict, "evidence_kind": "device", "decided": decided or ended or started}
        if harness:
            v["harness"] = harness
        if handback:
            v["handback"] = handback
        rec = {"kind": "run", "schema": 1, "id": rid, "created": created or decided or ended or started,
               "mode": mode, "case": case_id, "case_file_sha256": case_sha, "supersedes": supersedes,
               "target": {"class": "web" if web else "tablet", "tag": tag},
               "build": {"semver": {"v": "1.75.0", "st": "observed"}, "env": {"v": "staging", "st": "observed"},
                         "hash7": {"v": "261b2d9", "st": "observed"},
                         "version_code": {"v": "web" if web else "143", "st": "observed"}},
               "attempts": [{"n": 1, "started": started, "ended": ended or started, "steps": list(steps),
                             "evidence": list(evidence)}],
               "verdict": v, "values": values or {}}
        put(self.root, f"qa/runs/{camp}/{rid}.json", rec)
        return rec

    def build(self):
        r = self.root
        put(r, "qa/runner/engine.json", {"env": "staging"})
        # cases: PLAN-001 c0 and c1 (web), TERR-001 (both), a draft that lists a linked plan file, a self-test case
        put(r, "qa/cases/PLAN-001.json", case(
            "PLAN-001", "Optimize flight", "web", PLAN_STEPS,
            expected="Optimize flight ends with 'Flight successfully optimized' (or no improvement); a FAIL when "
                     "the site's plan is saved.",
            fixtures=[{"name": "QA_A.flightplan", "path": FP, "sha256": "0" * 64}]))
        put(r, "qa/cases/PLAN-001.c1.json", dict(case(
            "PLAN-001", "Optimize flight, c1", "web", [s for s in PLAN_STEPS if s["n"] != 4], expected="It optimises.",
            fixtures=[{"name": "QA_A.flightplan", "path": FP, "sha256": "0" * 64}]), created="2026-10-31T08:00:00.000Z"))
        put(r, "qa/cases/TERR-001.json", case("TERR-001", "Terrain list", "both", TERR_STEPS, expected="The DSM is listed."))
        put(r, "qa/cases/TERR-002.json", case("TERR-002", "Draft", "both", TERR_STEPS, status="draft",
                                              fixtures=[{"name": "linked.flightplan", "path": FP_LINK}]))
        put(r, "qa/runner/tests/cases/SELFTEST-001.json", case("SELFTEST-001", "Self-test", "both", TERR_STEPS,
                                                               area="HARNESS"))
        c0, c1 = sha(r, "qa/cases/PLAN-001.json"), sha(r, "qa/cases/PLAN-001.c1.json")
        t0 = sha(r, "qa/cases/TERR-001.json")
        self.c0, self.c1, self.t0 = c0, c1, t0

        plan = {"flightPlan": {"items": [
            {"complexItemType": "takeoff", "coordinate": [47.0, 8.0, 0]},
            {"complexItemType": "area", "polygon": [[47.1, 8.1], [47.2, 8.1], [47.2, 8.2], [47.1, 8.1]]},
            {"complexItemType": "area", "polygon": [[46.0, 7.0], [46.1, 7.0], ["x", 7.1], [46.1, 7.1]]},
            {"complexItemType": "land", "coordinate": [47.0, 8.0, 0]}]}}
        put(r, FP, plan)
        put(r, FP_OTHER, plan)
        os.symlink("QA_A.flightplan", os.path.join(r, *FP_LINK.split("/")))

        # the merged pair: two tasks of the same ClickUp test, same case file, both failed on W044 (step 3's check)
        fail_steps = [ok(1), ok(2), check(3, "v['after']['rows'] > v['before']['rows'] and v['after']['failed'] is False",
                                          False), ok(5), {"n": 6, "skipped": True}]
        values = {"before": {"rows": 1}, "after": {"rows": 1, "failed": False}}
        self.run(R1, "PLAN-001", "W044", "FAIL", "2026-10-30T09:00:00.000Z", c0, fail_steps, values=values,
                 ended="2026-10-30T09:02:00.000Z",
                 evidence=[{"kind": "shot", "path": f"tests/{PLAN}/W044/shots/{R1}_end.png"}])
        shots = [f"tests/{PLAN}/W044/shots/{R2}_step2.png", f"tests/{PLAN}/W044/shots/{R2}_end.png",
                 f"tests/{PLAN}/W044/shots/{R2}_result.png", f"tests/{PLAN}/W044/shots/{R2}_missing_result.png",
                 f"tests/{PLAN}/W044/shots/{R2}_after_import.png"]
        self.run(R2, "PLAN-001", "W044", "FAIL", "2026-10-30T10:00:00.000Z", c0, fail_steps, values=values,
                 ended="2026-10-30T10:01:30.000Z",
                 evidence=[{"kind": "shot", "path": p} for p in shots] + [{"kind": "har", "path": f"tests/{PLAN}/x.har"}])
        for p in [f"tests/{PLAN}/W044/shots/{R1}_end.png"] + shots[:3] + shots[4:] + [f"tests/{PLAN}/W044/shots/unlisted.png"]:
            put(r, p, PNG)
        # c1: passed once on W044, then failed once (validated, not repeated), then the browser timed out
        self.run(R3, "PLAN-001", "W044", "PASS", "2026-10-31T09:00:00.000Z", c1,
                 [ok(1), ok(2), check(3, "True", True), ok(5), {"n": 6, "skipped": True}],
                 ended="2026-10-31T09:01:40.000Z")
        self.run(R4, "PLAN-001", "W044", "FAIL", "2026-10-31T10:00:00.000Z", c1,
                 [ok(1), ok(2), check(3, "v['after']['split'] is True", False)], values={"after": {"split": False}})
        self.run(R5, "PLAN-001", "W044", "ERROR", "2026-10-31T11:00:00.000Z", c1,
                 [ok(1), {"n": 2, "error": "TimeoutError: timed out"}],
                 harness={"component": "act.py", "detail": "TimeoutError: timed out"})

        # TERR-001 without a task: TA5 passed then failed, ET65 failed (confirmed: validated on a tablet, 2 devices),
        # ET65 failed again but that was corrected (c1: the case was wrong), W044's runner broke before step 1
        self.run(R6, "TERR-001", "TA5", "PASS", "2026-11-01T08:00:00.000Z", t0, [ok(1), ok(2), check(3, "True", True)],
                 camp=TERR)
        list_check = check(3, "'DSM' in v['list']", False)
        self.run(R7, "TERR-001", "TA5", "FAIL", "2026-11-01T09:00:00.000Z", t0, [ok(1), ok(2), list_check],
                 values={"list": "Maps only"}, camp=TERR)
        self.run(R8, "TERR-001", "ET65", "FAIL", "2026-11-01T09:00:05.000Z", t0, [ok(1), ok(2), list_check],
                 values={"list": "Maps only"}, camp=TERR)
        base9 = self.run(R9, "TERR-001", "ET65", "FAIL", "2026-11-01T10:00:00.000Z", t0, [ok(1), ok(2), list_check],
                         values={"list": "Maps only"}, camp=TERR)
        put(r, f"qa/runs/{TERR}/{R9}c1.json", dict(
            base9, id=R9 + "c1", created="2026-11-01T12:00:00.000Z", supersedes={"id": R9, "why": "false failure"},
            verdict={"v": "ERROR", "decided": "2026-11-01T12:00:00.000Z", "reasoning": "The original FAIL ...",
                     "harness": {"component": "case", "detail": "case authoring: the check was wrong"}}))
        self.run(R10, "TERR-001", "W044", "ERROR", "2026-11-01T08:30:00.000Z", t0, [],
                 harness={"component": "runner", "detail": "TypeError: Strings must be encoded"}, camp=TERR)

        # one launch with every other result
        self.run(R11, "TERR-001", "TA5", "ERROR", "2026-11-01T11:00:00.000Z", t0, [{"n": 1, "error": "device LOST"}],
                 harness={"component": "act.py", "detail": "device LOST: not connected"}, camp=READY)
        self.run(R12, "TERR-001", "ET65", "BLOCKED", "2026-11-01T11:00:01.000Z", t0, [ok(1)],
                 handback={"step": 2, "reason": "needs your OK", "command": "x"}, camp=READY)
        self.run(R13, "TERR-001", "W044", "PARTIAL", "2026-11-01T11:00:02.000Z", t0, [ok(1), ok(3)], camp=READY)
        self.run(R14, "TERR-001", "TA3", "NOT_TESTED", "2026-11-01T11:00:03.000Z", t0, [], camp=READY)

        # never counted: a back-test record and a self-test run
        self.run("R-20261020-TA5-PLAN-001-09", "PLAN-001", "TA5", "PASS", "2026-10-20T09:00:00.000Z", None,
                 mode="backtest", camp="2026-10-20-backtest")
        self.run("R-20261101-TA5-SELFTEST-001-01", "SELFTEST-001", "TA5", "PASS", "2026-11-01T07:00:00.000Z", None,
                 camp="2026-11-01-selftest")

        # tasks and the worker requests behind them
        def task(tid, created, runs, state="results_ready", origin=None, clickup=True, targets=("W044",), **extra):
            t = {"kind": "task", "schema": 1, "id": tid, "created": created, "author": "user", "text": "t",
                 "clickup": {"cl": None, "task_id": CU1, "url": f"https://app.clickup.com/t/{CU1}"} if clickup else None,
                 "targets": list(targets), "priority": "normal", "cases": ["PLAN-001"], "state": state,
                 "history": [], "runs": runs, "campaign": PLAN, "origin": origin,
                 "outputs": {"summary": None, "tickets": [], "comments": []}}
            t.update(extra)
            put(r, f"qa/tasks/{tid}.json", t)

        not_web = "Test: PLAN-001 on W044.\nNot tested in this run: TA5, ET65 and TA3 (web feature, only W044 requested)."
        task("T-20261030-001", "2026-10-30T08:59:30.000Z", [R1], origin={"check": "W-20261030-001",
                                                                       "start": "W-20261030-002"})
        task("T-20261030-002", "2026-10-30T09:58:00.000Z", [R2], origin={"check": "W-20261030-003",
                                                                       "start": "W-20261030-004"},
             plan_summary=not_web)
        task("T-20261031-001", "2026-10-31T08:59:00.000Z", [R3],
             plan_summary="Not tested in this run:\nTA5: not selected. ET65: not set up.",
             outputs={"summary": f"tests/{PLAN}/SUMMARY.md", "tickets": [], "comments": [COMMENT],
                      "explain": ["tests/../qa/outside.json", EXPLAIN]})
        task("T-20261031-002", "2026-10-31T09:59:00.000Z", [R4], state="greenlit")
        task("T-20261031-003", "2026-10-31T10:59:00.000Z", [R5], clickup=False, targets=("W044", "TA5"),
             origin={"check": "W-20261031-001", "start": "W-20261031-002"})
        task("T-20261031-004", "2026-10-31T12:00:00.000Z", [], state="queued")

        def req(rid, typ, created, done, state="done", **extra):
            put(r, f"qa/runner/state/worker/{rid}.json", dict(
                {"kind": "worker_request", "id": rid, "type": typ, "state": state, "created": created,
                 "claimed_at": created, "done_at": done,
                 "clickup": {"task_id": CU1, "title": "Magic Planning testing", "custom_id": "CL-80001"}}, **extra))

        req("W-20261030-001", "check", "2026-10-30T08:58:00.000Z", "2026-10-30T08:59:00.000Z", intake=True)
        req("W-20261030-002", "start", "2026-10-30T08:59:30.000Z", "2026-10-30T09:03:30.000Z")
        req("W-20261030-003", "check", "2026-10-30T09:57:00.000Z", "2026-10-30T09:57:40.000Z", intake=True)
        req("W-20261030-004", "start", "2026-10-30T09:58:00.000Z", "2026-10-30T10:02:00.000Z")
        req("W-20261031-001", "check", "2026-10-31T10:58:00.000Z", "2026-10-31T10:58:30.000Z", intake=True)
        req("W-20261031-002", "start", "2026-10-31T10:59:00.000Z", "2026-10-31T11:01:00.000Z", state="failed")

        put(r, EXPLAIN, {"cases": {"PLAN-001": {"tested": "t" * 600, "happened": f"Asked {EMAIL} first.",
                                                "why": "It optimised.", "not_tested": "TA5: not selected", "x": 1}}})
        put(r, "qa/outside.json", {"cases": {"PLAN-001": {"tested": "from outside tests/"}}})
        put(r, COMMENT, "c" * 1200)
        put(r, "qa/history/bugs.json", {"kind": "bug_register", "schema": 1, "bugs": [
            {"title": "a", "found": "2026-10-25", "filed": "2026-10-26", "status": "filed by us"},
            {"title": "b", "found": "2026-09-01", "status": "found, not filed"},
            {"title": "c", "found": "2026-10-28", "status": "fixed before a ticket"},
            {"title": "d", "found": "2026-10-29", "filed": "2026-10-29", "status": "existing ticket we linked"}]})


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fx = Fixture()
        cls.root = cls.fx.root
        cls.data = data_model.Data(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.fx.close()


class CardTests(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        out = views.result_cards(cls.data)
        cls.out = out
        cls.cards = out["cards"] + out["older"]
        cls.by_case = {}
        for c in cls.cards:
            cls.by_case.setdefault((c["case"], c["case_version"], c["task"]), c)

    def card(self, task=None, case_version=None, key=None):
        if key:
            return next(c for c in self.cards if c["key"] == key)
        return next(c for c in self.cards if c["task"] == task and (case_version is None or c["case_version"] == case_version))

    def test_grouping_and_order(self):
        keys = [c["key"] for c in self.cards]
        # the TERR card's newest run was corrected at 12:00, after the READY launch: its time is the correction's
        self.assertEqual(keys, [f"tests/{TERR}|TERR-001|{self.fx.t0}", f"tests/{READY}|TERR-001|{self.fx.t0}",
                                "T-20261031-003|PLAN-001", "T-20261031-002|PLAN-001", "T-20261031-001|PLAN-001",
                                "T-20261030-002|PLAN-001"], "newest first; back-test and self-test runs make no card")
        when = [c["when"] for c in self.cards]
        self.assertEqual(when, sorted(when, reverse=True))
        self.assertEqual(self.out["cards"], self.cards, "fewer than 12 cards: nothing is older")
        split = views.result_cards(self.data, newest=2, limit=3)
        self.assertEqual([c["key"] for c in split["cards"]], keys[:2])
        self.assertEqual([c["key"] for c in split["older"]], keys[2:3])

    def test_consecutive_same_results_merge(self):
        c = self.card("T-20261030-002")
        self.assertEqual((c["runs"], c["same_result"], c["tasks"]), (2, True, ["T-20261030-002", "T-20261030-001"]))
        self.assertEqual(c["run_ids"], [R2, R1])
        self.assertEqual(c["case_version"], "c0")
        self.assertFalse(any(x["task"] == "T-20261030-001" for x in self.cards), "the older card is merged away")
        # same ClickUp test and case, but another case file or another result: not merged
        self.assertEqual(self.card("T-20261031-001")["verdict"], "passed")
        self.assertEqual(self.card("T-20261031-002")["verdict"], "failed_suspected")
        self.assertFalse(any(k.startswith("_") for k in c), "no private fields")

    def test_card_fields(self):
        c = self.card("T-20261030-002")
        self.assertEqual(c["clickup"], {"task_id": CU1, "custom_id": "CL-80001", "url": f"https://app.clickup.com/t/{CU1}",
                                        "title": "Magic Planning testing"})
        self.assertEqual((c["title"], c["case_title"]), ("Magic Planning testing", "Optimize flight"))
        self.assertEqual((c["verdict"], c["verdict_label"]), ("failed_suspected", "Failed, suspected"))
        self.assertEqual((c["build"], c["native"], c["duration_s"], c["when"]),
                         ("1.75.0 staging", None, 90, "2026-10-30T10:01:30.000Z"))
        self.assertEqual(c["devices"], [{"tag": "W044", "verdict": "failed_suspected",
                                         "verdict_label": "Failed, suspected",
                                         "failed_step": {"n": 3, "text": "After: area rows"}}])
        self.assertEqual(c["not_tested_devices"], [{"tag": "TA5", "why": "web feature, only W044 requested"},
                                                   {"tag": "ET65", "why": "web feature, only W044 requested"}])
        self.assertEqual((c["state"], c["actions"]), ("results_ready", ["write_drafts", "details"]))
        self.assertIsNone(c["explain"])
        terr = self.card(key=f"tests/{TERR}|TERR-001|{self.fx.t0}")
        self.assertEqual((terr["task"], terr["clickup"], terr["state"], terr["native"]), (None, None, None, "143"))
        self.assertEqual(terr["actions"], ["details"])
        self.assertEqual(terr["title"], "Terrain list")

    def test_not_tested_devices(self):
        c3 = self.card("T-20261031-001")
        self.assertEqual(c3["not_tested_devices"], [{"tag": "TA5", "why": "not selected"},
                                                    {"tag": "ET65", "why": "not set up"}])
        c5 = self.card("T-20261031-003")
        self.assertEqual(c5["not_tested_devices"], [{"tag": "TA5", "why": "no run recorded"},
                                                    {"tag": "ET65", "why": "not in this test"}])
        ready = self.card(key=f"tests/{READY}|TERR-001|{self.fx.t0}")
        self.assertEqual(ready["not_tested_devices"], [])
        self.assertEqual([(d["tag"], d["verdict"]) for d in ready["devices"]],
                         [("TA5", "not_decided"), ("ET65", "blocked"), ("W044", "partly"), ("TA3", "not_tested")])

    def test_effective_verdict_follows_the_correction(self):
        c = self.card(key=f"tests/{TERR}|TERR-001|{self.fx.t0}")
        self.assertEqual([(d["tag"], d["verdict"]) for d in c["devices"]],
                         [("TA5", "failed"), ("ET65", "not_decided"), ("W044", "not_decided")])
        self.assertIn(R9 + "c1", c["run_ids"])
        self.assertNotIn(R9, c["run_ids"])
        self.assertEqual((c["runs"], c["same_result"]), (5, False))
        self.assertIn("On ET65, it was first recorded as failed at step 3 (Check the list), then corrected: the test "
                      "case was wrong, not the app.", c["auto"]["happened"])

    def test_suspected_and_confirmed(self):
        terr = self.card(key=f"tests/{TERR}|TERR-001|{self.fx.t0}")
        self.assertEqual(terr["verdict"], "failed", "passed once on a tablet, failed on 2 devices: confirmed")
        self.assertIn("Not confirmed yet: this version of the test has not passed on a web device yet.",
                      self.card("T-20261030-002")["auto"]["why"], "2 failures on one build, never passed: suspected")
        self.assertIn("Not confirmed yet: the failure has not repeated yet.", self.card("T-20261031-002")["auto"]["why"])

    def test_auto_texts(self):
        passed = self.card("T-20261031-001")["auto"]
        self.assertEqual(passed["why"], "Every check passed.")
        self.assertEqual(passed["happened"], "Every step that ran went as expected on W044 (4 steps; 1 more was not "
                                             "needed this time).")
        self.assertEqual(passed["tested"], "It optimises.")
        failed = self.card("T-20261030-002")["auto"]
        self.assertEqual(failed["tested"], 'Optimize flight ends with "Flight successfully optimized"; a failure '
                                           "when the site's plan is saved.", "asides out, double quotes, no capitals")
        self.assertEqual(failed["happened"], "On W044, step 3 did not go as expected: After: area rows. The check "
                                             "found: after rows should be more than before rows (1), was 1. 2 runs "
                                             "in a row gave this result.")
        self.assertTrue(failed["why"].startswith("Step 3 did not happen as expected: After: area rows."))
        self.assertIn('The check found: list should contain "DSM".',
                      self.card(key=f"tests/{TERR}|TERR-001|{self.fx.t0}")["auto"]["happened"])
        nd = self.card("T-20261031-003")["auto"]
        self.assertEqual(nd["why"], "The test itself stopped at step 2 (the browser on W044 did not answer in time). "
                                    "This says nothing about the app.")
        self.assertEqual(nd["happened"], "On W044, the test itself stopped at step 2 (Optimize flight): the browser "
                                         "on W044 did not answer in time.")
        terr = self.card(key=f"tests/{TERR}|TERR-001|{self.fx.t0}")["auto"]
        self.assertIn("On W044, the test itself stopped before the first step: an error in the test engine.",
                      terr["happened"])
        self.assertTrue(terr["happened"].endswith("Earlier runs of this test: 1 failed and 1 passed."))
        ready = self.card(key=f"tests/{READY}|TERR-001|{self.fx.t0}")["auto"]
        self.assertEqual(ready["why"], "The run waits for you at step 2.")
        self.assertIn("On TA5, the test itself stopped at step 1 (Open the site): the device was not ready.",
                      ready["happened"])
        self.assertIn("On W044, only part of the test could be checked.", ready["happened"])
        self.assertIn("TA3 was not tested.", ready["happened"])
        for c in self.cards:
            for k in ("tested", "happened", "why"):
                for word in ("PASS", "FAIL", "harness", "assert", "readback", "sha256"):
                    self.assertNotIn(word, c["auto"][k], f"{c['key']} {k}")

    def test_steps_outcomes(self):
        c = self.card("T-20261030-002")
        self.assertEqual([(s["n"], s["outcome"], s["cleanup"]) for s in c["steps"]],
                         [(1, "as_expected", False), (2, "as_expected", False), (3, "not_as_expected", False),
                          (4, "not_run", False), (5, "as_expected", True), (6, "not_run", True)])
        self.assertEqual(c["steps"][1]["text"], "Optimize flight (cloud write)", "the case's text when the run has none")
        nd = self.card("T-20261031-003")
        self.assertEqual([(s["n"], s["outcome"]) for s in nd["steps"]],
                         [(1, "as_expected"), (2, "stopped"), (3, "not_run"), (5, "not_run"), (6, "not_run")])
        terr = self.card(key=f"tests/{TERR}|TERR-001|{self.fx.t0}")
        self.assertEqual([s["n"] for s in terr["steps"]], [1, 2, 3], "TA5 leads: the tablet-only step is listed")
        web = self.card(key=f"tests/{READY}|TERR-001|{self.fx.t0}")
        blocked = next(d for d in web["devices"] if d["tag"] == "ET65")
        self.assertEqual(blocked["failed_step"], {"n": 2, "text": "Tablet only: pick the file"})

    def test_proof(self):
        c = self.card("T-20261030-002")
        self.assertEqual([(p["kind"], p["name"], p["run"]) for p in c["proof"]],
                         [("result", f"{R2}_result.png", R2), ("result", f"{R2}_after_import.png", R2),
                          ("end", f"{R2}_end.png", R2), ("other", f"{R2}_step2.png", R2)],
                         "newest run only; a missing file gets no entry; a case's _after_ shot is a result too")
        self.assertTrue(all(p["url"].startswith("/files/tests/") for p in c["proof"]))
        self.assertEqual(self.card("T-20261031-001")["proof"], [])

    def test_flight_plans(self):
        c = self.card("T-20261030-002")
        self.assertEqual(c["flight_plans"], [{"name": "QA_A.flightplan", "url": "/files/" + FP,
                                              "polygons": [[[47.1, 8.1], [47.2, 8.1], [47.2, 8.2], [47.1, 8.1]],
                                                           [[46.0, 7.0], [46.1, 7.0], [46.1, 7.1]]]}])
        self.assertEqual(self.card(key=f"tests/{TERR}|TERR-001|{self.fx.t0}")["flight_plans"], [])

    def test_explain_and_outputs(self):
        c = self.card("T-20261031-001")
        ex = c["explain"]
        self.assertEqual(set(ex), {"tested", "happened", "why", "not_tested"})
        self.assertEqual(len(ex["tested"]), 500)
        self.assertNotIn(EMAIL, ex["happened"])
        self.assertIn("[EMAIL]", ex["happened"])
        self.assertEqual(ex["why"], "It optimised.")
        self.assertEqual(c["outputs"]["comment_file"], COMMENT)
        self.assertEqual(c["outputs"]["comment_text"], "c" * 1000)
        self.assertEqual(c["outputs"]["summary"], f"tests/{PLAN}/SUMMARY.md")
        self.assertEqual(c["actions"], ["write_drafts", "copy_comment", "details"])
        self.assertEqual(self.card("T-20261031-002")["actions"], ["details"], "greenlit: nothing to write")


class OneTestTests(Base):
    """The results redesign (2026-11-04, tests/2026-11-04-results-redesign/API.md) on this fixture."""

    def test_card_entries_by_task(self):
        # T-20261030-001's card is merged into T-20261030-002's on the answer without parameters; Report issue on that
        # test (opened from History) still finds its run
        self.assertEqual([e["id"] for e in views.card_entries(self.data, "T-20261030-001", "PLAN-001")], [R1])
        self.assertEqual([e["id"] for e in views.card_entries(self.data, "T-20261030-002", "PLAN-001")], [R2])
        self.assertIsNone(views.card_entries(self.data, "T-20261031-004", "PLAN-001"), "queued: no run")
        self.assertIsNone(views.card_entries(self.data, "T-20261030-002", "TERR-001"))

    def test_titles(self):
        ctx = views._Ctx(self.data)
        # a one-case test: the title of the case version that ran (R5 ran c1, R2 ran c0), never the ClickUp name
        self.assertEqual(views.test_title(ctx, ctx.tasks["T-20261031-003"]), "Optimize flight, c1")
        self.assertEqual(views.test_title(ctx, ctx.tasks["T-20261030-002"]), "Optimize flight")
        # a Today task with guidance and two cases: its ClickUp name; the same task from New test: its description
        today = dict(ctx.tasks["T-20261030-002"], cases=["PLAN-001", "TERR-001"], guidance="Say how many files")
        self.assertEqual(views.test_title(ctx, today), "Magic Planning testing")
        self.assertEqual(views.test_title(ctx, dict(today, intake=True)), "Say how many files")

    def test_groups(self):
        out = views.tests(self.data)
        self.assertEqual((out["ongoing"]["count"], out["tested"]["count"], out["done"]["count"]), (0, 2, 3))
        self.assertEqual([r["task_id"] for r in out["tested"]["rows"]], ["T-20261031-003", "T-20261031-002"])
        self.assertEqual([(r["task_id"], r["done_how"]["how"], r["done_how"]["by_task"]) for r in out["done"]["rows"]],
                         [("T-20261030-002", "failed_again", "T-20261031-002"),
                          ("T-20261030-001", "failed_again", "T-20261031-002"),
                          ("T-20261031-001", "replaced", "T-20261031-002")])
        every = [r["task_id"] for g in ("ongoing", "tested", "done") for r in out[g]["rows"]]
        self.assertNotIn("T-20261031-004", every, "queued with no run and no start: no test")

    def test_one_test_answer(self):
        out = views.result_cards(self.data, task="current")
        t = out["test"]
        self.assertEqual((t["task"], t["title"], t["group"], t["stopped_early"]),
                         ("T-20261031-003", "Optimize flight, c1", "tested", True), "its start failed after the run")
        self.assertEqual(t["result"]["sentence"], "Test error in 1 of 1 case, on W044")
        self.assertEqual((t["clickup"], t["next"], t["plan_again"], t["waiting"]), (None, None, None, 1))
        (c,) = out["cards"]
        self.assertEqual((c["title"], c["verdict_label"], c["rows_from"]), ("Optimize flight, c1", "Test error",
                                                                            "checks"))
        self.assertEqual(c["rows"], [{"n": 2, "text": "Optimize flight (cloud write)", "cells": {"W044": "stopped"}}],
                         "no check steps in this case: the stop step")
        self.assertEqual([d["word"] for d in c["devices"]], ["test error"])


class DownloadRuleTests(Base):
    def test_rule(self):
        self.assertEqual(views.fixture_paths(self.data), {FP, FP_LINK})
        self.assertTrue(views.fixture_allowed(self.data, FP))
        self.assertFalse(views.fixture_allowed(self.data, FP_OTHER))
        self.assertEqual(views.download_url(self.data, FP), "/files/" + FP)
        for rel in (FP_OTHER, FP_LINK, "tests/../" + FP):
            self.assertIsNone(views.download_url(self.data, rel), rel)
        allow = lambda r: views.fixture_allowed(self.data, r)  # noqa: E731
        self.assertEqual(security.resolve_read(self.root, FP, None, allow), os.path.join(self.root, *FP.split("/")))
        for rel, status in ((FP, 403), (FP_OTHER, 403)):
            with self.assertRaises(security.ReadError) as e:
                security.resolve_read(self.root, rel, None, None if rel == FP else allow)
            self.assertEqual(e.exception.status, status, rel)
        with self.assertRaises(security.ReadError) as e:
            security.resolve_read(self.root, FP_LINK, None, allow)
        self.assertEqual(e.exception.status, 403, "a listed path that is a link to another file is refused")
        self.assertEqual(security.file_kind("a/b.FlightPlan"), "download")

    def test_route(self):
        srv = Server(self.root)
        try:
            status, headers, body = srv.get("/files/" + FP)
            self.assertEqual(status, 200)
            self.assertEqual(headers["Content-Type"], serve.JSON_TYPE)
            self.assertEqual(headers["Content-Disposition"], 'attachment; filename="QA_A.flightplan"')
            self.assertEqual(json.loads(body)["flightPlan"]["items"][1]["complexItemType"], "area")
            for rel in (FP_OTHER, FP_LINK):
                self.assertEqual(srv.get("/files/" + rel)[0], 403, rel)
            self.assertEqual(srv.get("/files/tests/nope/none.flightplan")[0], 404)
            self.assertEqual(srv.get(f"/files/tests/{PLAN}/W044/shots/{R2}_end.png")[0], 200, "images keep their rule")
            self.assertEqual(srv.get(f"/files/tests/{PLAN}/W044/shots/unlisted.png")[0], 403)
        finally:
            srv.close()


class StatsTests(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s = views.quality_stats(cls.data, days=30, today=TODAY)

    def test_window(self):
        self.assertEqual(self.s["window"], {"days": 30, "from": "2026-10-04", "to": TODAY})
        self.assertEqual(views.quality_stats(self.data, days=500, today=TODAY)["window"]["days"], 120)

    def test_app_numbers(self):
        app = self.s["app"]
        self.assertEqual(app["tests"], {"runs": 13, "cases": 2, "days": 3})
        self.assertEqual({k: app["pass_rate"][k] for k in ("value", "passed", "failed", "partly")},
                         {"value": 25.0, "passed": 2, "failed": 5, "partly": 1})
        self.assertEqual(app["fails"], {"confirmed": 2, "suspected": 3})
        self.assertEqual(app["bugs"], {"found": 3, "filed": 1, "fixed": 1, "linked": 1})
        self.assertEqual(app["coverage"], {"devices": {"TA5": 2, "ET65": 1, "W044": 5, "TA3": 0},
                                           "cases_passed": 2, "cases_active": 2})

    def test_engine_numbers(self):
        eng = self.s["engine"]
        self.assertEqual(eng["decided_rate"], {"value": 69.2, "decided": 9, "runs": 13, "target": 90})
        self.assertEqual(eng["false_fails"], {"corrected": 1, "fails_before_correction": 6, "value": 16.7})
        self.assertEqual(eng["not_decided_causes"], [{"cause": "test case", "n": 1}, {"cause": "engine", "n": 1},
                                                     {"cause": "device not ready", "n": 1},
                                                     {"cause": "W044 browser", "n": 1}, {"cause": "other", "n": 0}])
        self.assertEqual(eng["hands_off"], {"runs_from_app": 5, "runs": 13, "value": 38.5})
        self.assertEqual(eng["times"], {"prepare_median_s": 50, "run_median_s": 240, "result_median_s": 315,
                                        "samples": 2})

    def test_series(self):
        ser = self.s["series"]
        self.assertEqual(len(ser["per_day"]), 30)
        self.assertEqual(ser["per_day"][0], {"day": "2026-10-04", "PASS": 0, "FAIL": 0, "PARTIAL": 0, "BLOCKED": 0,
                                             "ERROR": 0})
        busy = {d["day"]: d for d in ser["per_day"] if any(d[v] for v in views.VERDICTS)}
        self.assertEqual(busy, {
            "2026-10-30": {"day": "2026-10-30", "PASS": 0, "FAIL": 2, "PARTIAL": 0, "BLOCKED": 0, "ERROR": 0},
            "2026-10-31": {"day": "2026-10-31", "PASS": 1, "FAIL": 1, "PARTIAL": 0, "BLOCKED": 0, "ERROR": 1},
            "2026-11-01": {"day": "2026-11-01", "PASS": 1, "FAIL": 2, "PARTIAL": 1, "BLOCKED": 1, "ERROR": 3}})
        weeks = {w["week"]: w for w in ser["decided_per_week"]}
        self.assertEqual(list(weeks), ["2026-W40", "2026-W41", "2026-W42", "2026-W43", "2026-W44", "2026-W45"])
        self.assertEqual(weeks["2026-W40"], {"week": "2026-W40", "from": "2026-10-04", "decided": 0, "runs": 0,
                                             "rate": None})
        self.assertEqual(weeks["2026-W44"], {"week": "2026-W44", "from": "2026-10-26", "decided": 9, "runs": 13,
                                             "rate": 69.2})
        self.assertEqual(ser["per_device"], [
            {"tag": "TA5", "PASS": 1, "FAIL": 1, "PARTIAL": 0, "BLOCKED": 0, "ERROR": 1},
            {"tag": "ET65", "PASS": 0, "FAIL": 1, "PARTIAL": 0, "BLOCKED": 1, "ERROR": 1},
            {"tag": "W044", "PASS": 1, "FAIL": 3, "PARTIAL": 1, "BLOCKED": 0, "ERROR": 2},
            {"tag": "TA3", "PASS": 0, "FAIL": 0, "PARTIAL": 0, "BLOCKED": 0, "ERROR": 0}])
        self.assertEqual(ser["per_area"], [
            {"area": "TERR", "PASS": 1, "FAIL": 2, "PARTIAL": 1, "BLOCKED": 1, "ERROR": 3},
            {"area": "PLAN", "PASS": 1, "FAIL": 3, "PARTIAL": 0, "BLOCKED": 0, "ERROR": 1}])
        self.assertEqual(ser["times"], [
            {"task": "T-20261030-001", "case": "PLAN-001", "day": "2026-10-30", "prepare_s": 60, "run_s": 240,
             "result_s": 330},
            {"task": "T-20261030-002", "case": "PLAN-001", "day": "2026-10-30", "prepare_s": 40, "run_s": 240,
             "result_s": 300}])
        self.assertEqual(self.s["backtest"]["records"], 1)
        self.assertEqual(set(self.s["definitions"]) >= {"pass_rate", "decided_rate", "false_fails", "hands_off",
                                                        "times"}, True)

    def test_a_short_window_counts_only_its_days(self):
        s = views.quality_stats(self.data, days=1, today="2026-10-31")
        self.assertEqual(s["app"]["tests"], {"runs": 3, "cases": 1, "days": 1})
        self.assertEqual(s["engine"]["decided_rate"]["decided"], 2)
        self.assertEqual(s["app"]["bugs"]["found"], 0)
        self.assertEqual(s["engine"]["times"]["samples"], 0, "the plan and the start ran on 10-30 and 10-31's failed")


class EmptyTests(unittest.TestCase):
    def test_empty_workspace(self):
        tmp = os.path.realpath(tempfile.mkdtemp(prefix="qa-views-empty-"))
        try:
            os.makedirs(os.path.join(tmp, "qa"))
            d = data_model.Data(tmp)
            self.assertEqual(views.result_cards(d), {"cards": [], "older": [], "running": []})  # running: 2026-11-03
            s = views.quality_stats(d, days=7, today=TODAY)
            self.assertEqual(s["app"]["tests"], {"runs": 0, "cases": 0, "days": 0})
            self.assertIsNone(s["app"]["pass_rate"]["value"])
            self.assertIsNone(s["engine"]["decided_rate"]["value"])
            self.assertIsNone(s["engine"]["false_fails"]["value"])
            self.assertEqual(s["engine"]["times"], {"prepare_median_s": None, "run_median_s": None,
                                                    "result_median_s": None, "samples": 0})
            self.assertEqual(s["app"]["bugs"], {"found": 0, "filed": 0, "fixed": 0, "linked": 0})
            self.assertEqual(len(s["series"]["per_day"]), 7)
            self.assertEqual(s["series"]["per_area"], [])
            self.assertEqual(s["backtest"]["records"], 0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class RouteTests(Base):
    def test_routes(self):
        srv = Server(self.root)
        try:
            status, _, body = srv.get("/api/results/cards")
            self.assertEqual(status, 200)
            self.assertEqual(set(json.loads(body)), {"cards", "older", "running"})
            status, _, body = srv.get("/api/results/cards?task=current")  # one test (2026-11-04)
            self.assertEqual((status, set(json.loads(body))), (200, {"test", "cards", "running"}))
            self.assertEqual(srv.get("/api/results/cards?task=T-20261031-004")[0], 404, "queued, no run")
            status, _, body = srv.get("/api/results/tests")  # History's 'Your tests' (2026-11-04)
            self.assertEqual((status, json.loads(body)["tested"]["count"]), (200, 2))
            status, _, body = srv.get("/api/stats?days=7")
            self.assertEqual((status, json.loads(body)["window"]["days"]), (200, 7))
            self.assertEqual(json.loads(srv.get("/api/stats")[2])["window"]["days"], 30)
            for bad in ("0", "121", "abc", "1000", "-1"):
                self.assertEqual(srv.get(f"/api/stats?days={bad}")[0], 400, bad)
            self.assertEqual(srv.get("/api/results")[0], 200, "the old route stays")
        finally:
            srv.close()

    def test_cli(self):
        for argv, key in ((["--root", self.root, "cards"], "cards"), (["--root", self.root, "stats", "--days", "5"],
                                                                      "window")):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(views.main(argv), 0)
            self.assertIn(key, json.loads(buf.getvalue()))
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(views.main(["--root", self.root, "stats", "--days", "0"]), 2)


class ExplainOutputTests(unittest.TestCase):
    """tasks.py's "explain" output kind: added while the runs are in or later, a file under tests/ that exists."""

    def setUp(self):
        self.fx = Fixture()
        self.w = writes.Writer(self.fx.root)
        put(self.fx.root, f"tests/{PLAN}/EXPLAIN_2.json", {"cases": {}})

    def tearDown(self):
        self.fx.close()

    def test_explain_kind(self):
        self.assertIn("explain", tasks.OUTPUT_KINDS)
        args = tasks._parser().parse_args(["add-output", "T-20261030-002", "explain", f"tests/{PLAN}/EXPLAIN_2.json"])
        self.assertEqual(args.kind, "explain")
        t = self.w.add_output("T-20261030-002", "explain", f"tests/{PLAN}/EXPLAIN_2.json")
        self.assertEqual(t["outputs"]["explain"], [f"tests/{PLAN}/EXPLAIN_2.json"])
        t = self.w.add_output("T-20261030-002", "explain", f"tests/{PLAN}/EXPLAIN_2.json")
        self.assertEqual(len(t["outputs"]["explain"]), 1, "added once")
        for kind, rel, status in (("summary", COMMENT, 409),                       # drafts wait for the greenlight
                                  ("explain", "qa/outside.json", 400),             # not under tests/
                                  ("explain", f"tests/{PLAN}/none.json", 404)):    # missing
            with self.assertRaises(writes.WriteError) as e:
                self.w.add_output("T-20261030-002", kind, rel)
            self.assertEqual(e.exception.status, status, (kind, rel))
        with self.assertRaises(writes.WriteError) as e:
            self.w.add_output("T-20261031-004", "explain", f"tests/{PLAN}/EXPLAIN_2.json")
        self.assertEqual(e.exception.status, 409, "a queued task has no runs to explain")
        self.assertEqual(self.w.add_output("T-20261031-002", "explain", EXPLAIN)["outputs"]["explain"], [EXPLAIN],
                         "greenlit is fine too")


class SafetyReviewTests(unittest.TestCase):
    """Security review of 2026-11-02: what reaches a response is redacted before it is cut or reworded, the files the
    cards read inline follow the /files/ rule, the explain output is a JSON file of its own, and one odd record cannot
    break the Results page. Secret values are set by hand (redact._SECRETS), never read from qa/secrets.env."""

    SECRET = "S3cr3tValue-4711"
    SECRET_CAPS = "PASS-4711x"
    MAIL = "planted" + "@" + "example.org"

    def setUp(self):
        self.fx = Fixture()
        self.root = self.fx.root
        self._secrets = redact._SECRETS
        redact._SECRETS = [self.SECRET, self.SECRET_CAPS]

    def tearDown(self):
        redact._SECRETS = self._secrets
        self.fx.close()

    def _cards_json(self):
        return serve.json_bytes(views.result_cards(data_model.Data(self.root))).decode("utf-8")

    def test_texts_are_redacted_before_they_are_cut_or_reworded(self):
        # a cut through a secret or an address leaves a piece that the final redaction no longer recognises
        self.assertNotIn("planted", views._cut("x" * 45 + "/" + self.MAIL, 60))
        self.assertNotIn("S3cr3t", views._cut("y" * 45 + ":" + self.SECRET, 60))
        # "PASS" in capitals is reworded to "pass": the secret must be masked before that
        self.assertNotIn("4711", views._quotes(f"Type {self.SECRET_CAPS} in the field"))
        self.assertNotIn("4711", views._tested({"expected": f"Shows ({self.SECRET}) and {self.SECRET_CAPS}."}))
        # a reason in the plan summary is read up to the first full stop: "planted@example" alone is no address
        rows = views._not_tested(types.SimpleNamespace(requests={}), {
            "plan_summary": f"Not tested in this run:\nTA5: asked {self.MAIL} first."}, set())
        self.assertNotIn("planted", json.dumps(rows))
        self.assertEqual(rows[0], {"tag": "TA5", "why": "asked [EMAIL] first"})

    def test_explain_is_redacted_before_it_is_cut(self):
        put(self.root, EXPLAIN, {"cases": {"PLAN-001": {
            "tested": "t" * 480 + " " + self.MAIL, "happened": "h" * 490 + self.SECRET, "why": "w", "not_tested": None}}})
        body = self._cards_json()
        for piece in ("planted", "S3cr3t"):
            self.assertNotIn(piece, body)
        ex = next(c for c in json.loads(body)["cards"] + json.loads(body)["older"]
                  if c["task"] == "T-20261031-001")["explain"]
        self.assertLessEqual(max(len(ex["tested"]), len(ex["happened"])), views.EXPLAIN_MAX)

    def test_step_texts_with_a_secret_in_capitals(self):
        rec_path = os.path.join(self.root, "qa", "runs", PLAN, f"{R2}.json")
        with open(rec_path, encoding="utf-8") as f:
            rec = json.load(f)
        rec["attempts"][-1]["steps"][0]["text"] = f"Log in with {self.SECRET_CAPS}"
        put(self.root, f"qa/runs/{PLAN}/{R2}.json", rec)
        self.assertNotIn("4711", self._cards_json())

    def test_inline_reads_follow_the_files_rule(self):
        # a comment or explain file that is a hard link to a file outside tests/ (a planted key file) is not read
        fake = put(self.root, "qa/planted_keys.txt", "QA_FAKE_KEY=planted-value-not-real\n")
        comment = os.path.join(self.root, *COMMENT.split("/"))
        os.remove(comment)
        os.link(fake, comment)
        explain = os.path.join(self.root, *EXPLAIN.split("/"))
        os.remove(explain)
        os.link(put(self.root, "qa/other.json", {"cases": {"PLAN-001": {"tested": "from qa/"}}}), explain)
        body = self._cards_json()
        self.assertNotIn("planted-value", body)
        self.assertNotIn("from qa/", body)
        card = next(c for c in json.loads(body)["cards"] + json.loads(body)["older"] if c["task"] == "T-20261031-001")
        self.assertIsNone(card["outputs"]["comment_text"])
        self.assertIsNone(card["explain"])
        ctx = views._Ctx(data_model.Data(self.root))
        self.assertIsNone(ctx.tests_file(COMMENT))
        # a link to another file inside tests/ and a file over 2 MiB are not read either
        os.remove(explain)
        os.symlink("SUMMARY_real.json", explain)
        put(self.root, f"tests/{PLAN}/SUMMARY_real.json", {"cases": {"PLAN-001": {"tested": "linked"}}})
        self.assertIsNone(ctx.tests_file(EXPLAIN))
        os.remove(explain)
        put(self.root, EXPLAIN, b"{" + b" " * (security.TEXT_CAP + 1) + b"}")
        self.assertIsNone(ctx.tests_file(EXPLAIN))
        self.assertEqual(ctx.tests_file(FP), os.path.join(self.root, *FP.split("/")), "a plain file is read")

    def test_explain_output_is_a_json_file_of_its_own(self):
        w = writes.Writer(self.root)
        good = f"tests/{PLAN}/EXPLAIN_ok.json"
        put(self.root, good, {"cases": {"PLAN-001": {"tested": "t"}}})
        self.assertEqual(w.add_output("T-20261030-002", "explain", good)["outputs"]["explain"], [good])
        put(self.root, f"tests/{PLAN}/notes.md", "# not JSON")
        put(self.root, f"tests/{PLAN}/broken.json", "{not json")
        put(self.root, f"tests/{PLAN}/list.json", [1, 2])
        put(self.root, f"tests/{PLAN}/nocases.json", {"tested": "t"})
        put(self.root, f"tests/{PLAN}/big.json", b'{"cases": {}, "pad": "' + b"x" * writes.EXPLAIN_FILE_MAX + b'"}')
        os.symlink("EXPLAIN_ok.json", os.path.join(self.root, "tests", PLAN, "link.json"))
        os.link(os.path.join(self.root, *good.split("/")), os.path.join(self.root, "tests", PLAN, "hard.json"))
        for name, status in (("notes.md", 400), ("files", 400), ("broken.json", 400), ("list.json", 400),
                             ("nocases.json", 400), ("big.json", 413), ("link.json", 400), ("hard.json", 400)):
            with self.assertRaises(writes.WriteError) as e:
                w.add_output("T-20261030-002", "explain", f"tests/{PLAN}/{name}")
            self.assertEqual(e.exception.status, status, name)
        self.assertEqual(tasks.load_task(self.root, "T-20261030-002")["outputs"]["explain"], [good], "nothing added")

    def test_a_flightplan_url_serves_a_listed_test_file_or_nothing(self):
        # a listed .flightplan name that is a link to a text file under qa/ or tests/ took the text rule before
        tv_files = os.path.join(self.root, "tests", PLAN, "files")
        put(self.root, "qa/planted_keys.txt", "QA_FAKE_KEY=planted-value-not-real\n")
        put(self.root, f"tests/{PLAN}/notes.txt", "planted notes")
        os.symlink(os.path.join(self.root, "qa", "planted_keys.txt"), os.path.join(tv_files, "keys.flightplan"))
        os.symlink("../notes.txt", os.path.join(tv_files, "notes.flightplan"))
        rels = [f"tests/{PLAN}/files/keys.flightplan", f"tests/{PLAN}/files/notes.flightplan"]
        put(self.root, "qa/cases/PLAN-009.json", case("PLAN-009", "Links", "web", PLAN_STEPS,
                                                     fixtures=[{"name": "x.flightplan", "path": p} for p in rels]))
        allow = lambda r: views.fixture_allowed(data_model.Data(self.root), r)  # noqa: E731
        for rel in rels:
            with self.assertRaises(security.ReadError) as e:
                security.resolve_read(self.root, rel, None, allow)
            self.assertEqual(e.exception.status, 403, rel)
        srv = Server(self.root)
        try:
            for rel in rels:
                status, _, body = srv.get("/files/" + rel)
                self.assertEqual(status, 403, rel)
                self.assertNotIn(b"planted", body)
            self.assertEqual(srv.get("/files/" + FP)[0], 200, "the listed plain file still downloads")
            self.assertEqual(srv.get(f"/files/tests/{PLAN}/notes.txt")[0], 200, "a text file keeps its own rule")
        finally:
            srv.close()

    def test_a_deep_check_expression_does_not_break_the_page(self):
        for expr in ("v" + "[0]" * 1300 + " == 1", "1+" * 1990 + "1 == 2"):
            self.assertIsNone(views.check_found({"assert": {"result": False, "expr": expr}}, {"values": {}}))
        rec_path = os.path.join(self.root, "qa", "runs", PLAN, f"{R2}.json")
        with open(rec_path, encoding="utf-8") as f:
            rec = json.load(f)
        rec["attempts"][-1]["steps"][2]["assert"]["expr"] = "v" + "[0]" * 1300 + " == 1"
        put(self.root, f"qa/runs/{PLAN}/{R2}.json", rec)
        srv = Server(self.root)
        try:
            self.assertEqual(srv.get("/api/results/cards")[0], 200)
        finally:
            srv.close()


class RealWorkspaceTests(unittest.TestCase):
    """Read-only: on the real records the numbers add up (exact values are checked by hand, not here)."""

    def test_numbers_add_up(self):
        d = data_model.Data(REAL_ROOT)
        s = views.quality_stats(d, days=30)
        runs = s["app"]["tests"]["runs"]
        self.assertEqual(sum(sum(x[v] for v in views.VERDICTS) for x in s["series"]["per_day"]), runs)
        self.assertEqual(sum(sum(x[v] for v in views.VERDICTS) for x in s["series"]["per_device"]), runs)
        self.assertEqual(s["engine"]["decided_rate"]["runs"], runs)
        errors = sum(x["ERROR"] for x in s["series"]["per_day"])
        self.assertEqual(sum(c["n"] for c in s["engine"]["not_decided_causes"]), errors)
        self.assertLessEqual(s["engine"]["false_fails"]["corrected"], s["engine"]["false_fails"]["fails_before_correction"])
        out = views.result_cards(d)
        self.assertLessEqual(len(out["cards"]), views.CARDS_NEWEST)
        self.assertLessEqual(len(out["cards"]) + len(out["older"]), views.CARDS_MAX)


class Server:
    """serve.build_server(root, 0) in a thread, with a plain GET helper; request logs are dropped."""

    def __init__(self, root):
        self._log = serve.Handler.log_message
        serve.Handler.log_message = lambda self, fmt, *args: None
        self.srv = serve.build_server(root, 0)
        self.port = self.srv.qa_port
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def get(self, path):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        try:
            c.request("GET", path, headers={"Host": f"127.0.0.1:{self.port}"})
            r = c.getresponse()
            return r.status, dict(r.getheaders()), r.read()
        finally:
            c.close()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()
        serve.Handler.log_message = self._log


if __name__ == "__main__":
    unittest.main()
