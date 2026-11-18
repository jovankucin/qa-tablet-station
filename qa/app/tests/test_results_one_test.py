"""Tests of the results redesign, server side (2026-11-04, tests/2026-11-04-results-redesign/SPEC.md and API.md): the
title rule (views.test_title, description_title, request_title), the one-test answer of GET /api/results/cards
?task=…|current&case=… (views.result_cards with task or case), History's 'Your tests' (GET /api/results/tests,
views.tests) with the Done rule and its failure guard, card_entries by task, the step or part table per device, the
live box of a running test, the per-request title on GET /api/worker, and the answer without parameters, which keeps
its shape.

python3 -I qa/app/tests/test_results_one_test.py [-v]

Every test reads a small fixture workspace in a temp folder (written below); the real workspace is only read by the
last class, which checks that the groups add up. Nothing here touches a device, ClickUp or the network.
"""
import datetime
import hashlib
import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(TESTS_DIR)
REAL_ROOT = os.path.dirname(os.path.dirname(APP_DIR))
sys.path.insert(0, APP_DIR)
sys.path.insert(0, os.path.join(os.path.dirname(APP_DIR), "runner"))

import data as data_model  # noqa: E402
import records  # noqa: E402
import serve  # noqa: E402
import views  # noqa: E402
import writes  # noqa: E402

CU_A, CU_B, CU_C, CU_D, CU_E = "86cuaaaa1", "86cubbbb2", "86cucccc3", "86cudddd4", "86cueeee5"
NAMES = {CU_A: "Custom terrain - final tests", CU_B: "Magic Planning testing",
         CU_C: "Offline sites (instead of offline flights)", CU_D: "Android version update testing",
         CU_E: "Wi-Fi checks"}
EMAIL = "planted.person" + "@" + "example.org"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32

# the person's description of T-20261104-002 (API.md 1.3) and its two titles
GUIDANCE = ("Android\nOffline - Download a flight plan offline with custom terrain imported and make sure the custom "
            "terrain is still used when going offline\n\nTest with terrain from various latitudes and make sure we "
            "have the same resolution constraint everywhere. e.g. ( create new sites for all of them )\nAlaska\n"
            "Antarctica\nSouth Africa\nSpain")
HEADER_TITLE = ("Android · Offline - Download a flight plan offline with… · Test with terrain from various latitudes "
                "and… · Alaska · Antarctica · South Africa · Spain")
LIST_TITLE = "Android · Offline - Download a flight plan offline with…"

# the tests (one Run press each) and what they are for
T1 = "T-20261101-001"   # CU_A, New test, TERR-022 (parts) and TERR-021 on TA5 and ET65: every case a test error
T3 = "T-20261102-001"   # CU_C, OFFS-001 on ET65, test error: replaced by T5
T4 = "T-20261102-002"   # CU_C, OFFS-001 on TA5 and ET65, test error: replaced by T5
T5 = "T-20261102-003"   # CU_C, OFFS-001 on TA5 and ET65, passed: Add result
T23 = "T-20261102-004"  # CU_E, WIFI-001 passed, then its start failed: stopped early
T6 = "T-20261030-001"   # CU_B, PLAN-001 failed on W044: stays in Tested (it passed in T7, the failure guard)
T7 = "T-20261030-002"   # CU_B, PLAN-001 passed, its comment drafted: Post comment
T8 = "T-20261030-003"   # CU_B, PLAN-002 failed: failed again in T9
T9 = "T-20261030-004"   # CU_B, PLAN-002 failed again, a ticket drafted: File ticket
T10 = "T-20261030-005"  # CU_B, PLAN-002 passed
T11 = "T-20261031-001"  # CU_D, UPG-001 passed, comment posted: Done
T12 = "T-20261031-002"  # CU_D, UPG-002 passed, marked done: Done
T13 = "T-20261031-003"  # CU_D, a Today test with guidance, two cases: titled by its ClickUp name
T18 = "T-20261103-001"  # CU_A, New test, running now (Hover took it): the live box
T19 = "T-20261103-002"  # no ClickUp, /qa-run from a chat, running
T20 = "T-20261103-003"  # CU_C, Run pressed, Hover is off; its plan's title on the start request
T21 = "T-20261022-001"  # queued, no runs, no start: left out
T22 = "T-20261023-001"  # queued, its start failed before any run: left out

CAMP_A, CAMP_B, CAMP_C = "2026-11-01-terrain", "2026-10-30-plan", "2026-11-02-offline"
CAMP_D, CAMP_E, CHAT, LIVE = "2026-10-31-upgrade", "2026-11-02-wifi", "2026-10-21-chat", "2026-11-03-live"
RA1, RA2 = "R-20261101-TA5-TERR-022-01", "R-20261101-ET65-TERR-022-01"
RA3, RA4 = "R-20261101-TA5-TERR-021-01", "R-20261101-ET65-TERR-021-01"
RB1, RB2 = "R-20261030-W044-PLAN-001-01", "R-20261030-W044-PLAN-001-02"
RB3, RB4, RB5 = "R-20261030-W044-PLAN-002-01", "R-20261030-W044-PLAN-002-02", "R-20261030-W044-PLAN-002-03"
RC1, RC2, RC3 = "R-20261102-ET65-OFFS-001-01", "R-20261102-TA5-OFFS-001-02", "R-20261102-ET65-OFFS-001-02"
RC4, RC5 = "R-20261102-TA5-OFFS-001-03", "R-20261102-ET65-OFFS-001-03"
RD1, RD2, RD3 = "R-20261031-TA5-UPG-001-01", "R-20261031-TA5-UPG-002-01", "R-20261031-TA5-UPG-001-02"
RD4 = "R-20261031-ET65-UPG-002-02"
RE1 = "R-20261102-TA5-WIFI-001-01"
RX1, RX2 = "R-20261021-TA5-TERR-030-01", "R-20261021-W044-TERR-030-01"
RL1 = "R-20261103-ET65-TERR-022-01"  # ET65's run of T18, handed back at step 2 (no task lists it yet)
EXPLAIN = f"tests/{CAMP_A}/EXPLAIN_{T1}.json"
COMMENT_T7 = f"tests/{CAMP_B}/COMMENT_{CU_B}_{T7}.md"
COMMENT_T11 = f"tests/{CAMP_D}/COMMENT_{CU_D}_{T11}.md"
TICKET_T9 = f"tests/{CAMP_B}/tickets/1"
TICKET_RX2 = f"tests/{CHAT}/tickets/1"
SHOT = f"tests/{LIVE}/TA5/shots/R-20261103-TA5-TERR-022-01_step4.png"

PARTS = [{"n": 1, "text": "New QA_ site at Spain 40.4N", "quote": "create new sites", "check": False},
         {"n": 2, "text": "Maps import, 28 m DSM: accepted", "quote": "accepts any resolution", "check": True},
         {"n": 3, "text": "Import file, 29 m DSM: warning shown, Next off",
          "quote": ["29m and coarser", "can not be selected"], "check": True}]
CHECK = {"kind": "scripted", "expr": "True"}
ENGINE_ACCOUNT = "qa-stag"  # engine.json's account: tickets are filed from its runs only (views.file_rule)


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


def ok(n):
    return {"n": n, "readback": {"expected": ["x"], "got": {"x": True}, "ok": True}}


def missed(n, expr="v['x'] == 1"):
    return {"n": n, "assert": {"kind": "scripted", "expr": expr, "result": False, "by": "script"},
            "readback": {"expected": [], "got": None, "ok": True}}


def case(cid, title, targets, steps, parts=None, created="2026-10-29T08:00:00.000Z"):
    c = {"kind": "case", "schema": 1, "id": cid, "title": title, "area": cid.split("-")[0], "targets": targets,
         "status": "active", "expected": None, "steps": steps, "fixtures": [], "created": created,
         "supersedes": None}
    if parts:
        c["parts"] = parts
    return c


SIMPLE = [{"n": 1, "text": "Open the app", "gates": []},
          {"n": 2, "text": "Read the result", "gates": [], "assert": CHECK},
          {"n": 3, "text": "Leave (always)", "gates": [], "always": True}]
PLAN_STEPS = [{"n": 1, "text": "Open the planner", "gates": []},
              {"n": 2, "text": "Optimize flight", "gates": []},
              {"n": 3, "text": "After: area rows", "gates": [], "assert": CHECK},
              {"n": 4, "text": "Leave the planner (always)", "gates": [], "always": True}]
TERR21_STEPS = [{"n": 1, "text": "Top bar: Sites (a planner left open asks to save first)", "gates": []},
                {"n": 2, "text": "Read the file card of Spain_28m.tif", "gates": [], "assert": CHECK},
                {"n": 3, "text": "Wait for the processing, then read the site's maps", "gates": [], "assert": CHECK},
                {"n": 4, "text": "Read the dialog: 'Dataset has too low resolution...' shown", "gates": [],
                 "assert": CHECK},
                {"n": 5, "text": "Leave the planner (always)", "gates": [], "always": True, "assert": CHECK}]
TERR22_STEPS = [{"n": 1, "text": "Sites: new QA_ site", "gates": [], "part": 1},
                {"n": 2, "text": "Read the 28 m file card", "gates": [], "part": 2, "assert": CHECK},
                {"n": 3, "text": "Import file: 29 m", "gates": [], "part": 3},
                {"n": 4, "text": "Read the warning", "gates": [], "part": 3, "assert": CHECK},
                {"n": 5, "text": "Leave the planner (always)", "gates": [], "always": True}]
TERR30_STEPS = [{"n": 1, "text": "Open the site", "gates": []},
                {"n": 2, "text": "Tablet only: pick the file", "gates": [], "on": "tablet", "assert": CHECK},
                {"n": 3, "text": "Check the list", "gates": [], "assert": CHECK}]


class Fixture:
    """The fixture workspace; the expected groups, titles and rows below come from these records."""

    def __init__(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="qa-one-test-"))
        self.root = os.path.join(self.tmp, "ws")
        os.makedirs(self.root)
        self.build()

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def path(self, rel):
        return os.path.join(self.root, *rel.split("/"))

    def run(self, camp, rid, case_id, verdict, started, ended, steps=(), values=None, harness=None, handback=None,
            account=ENGINE_ACCOUNT):
        tag = rid.split("-")[2]
        web = tag == "W044"
        v = {"v": verdict, "evidence_kind": "device", "decided": ended}
        if harness:
            v["harness"] = harness
        if handback:
            v["handback"] = handback
        rec = {"kind": "run", "schema": 1, "id": rid, "created": ended, "mode": "live", "case": case_id,
               "case_file_sha256": self.sha[case_id], "supersedes": None,
               "target": {"class": "web" if web else "tablet", "tag": tag},
               "build": {"semver": {"v": "1.76.0", "st": "observed"}, "env": {"v": "staging", "st": "observed"},
                         "hash7": {"v": "82e23ff", "st": "observed"},
                         "version_code": {"v": "web" if web else "144", "st": "observed"}},
               "attempts": [{"n": 1, "started": started, "ended": ended, "steps": list(steps), "evidence": []}],
               "verdict": v, "values": values or {}, "account": {"v": account, "st": "verified", "src": []}}
        put(self.root, f"qa/runs/{camp}/{rid}.json", rec)
        return rec

    def task(self, tid, created, runs, cases, clickup, results_at=None, state="results_ready", history=None,
             targets=("TA5", "ET65"), **extra):
        ck = {"cl": None, "task_id": clickup, "url": f"https://app.clickup.com/t/{clickup}"} if clickup else None
        if history is None:
            history = [{"at": created, "from": None, "to": "queued", "by": "user", "via": "app", "note": None}]
            if results_at:
                history += [{"at": created, "from": "queued", "to": "running", "by": "claude", "via": "qa-run",
                             "note": None},
                            {"at": results_at, "from": "running", "to": "results_ready", "by": "claude",
                             "via": "qa-run", "note": None}]
        t = {"kind": "task", "schema": 1, "id": tid, "created": created, "author": "user",
             "text": f"Run from New test: {NAMES.get(clickup, 'x')}. Cases {', '.join(cases)} on TA5.",
             "clickup": ck, "targets": list(targets), "priority": "normal", "cases": list(cases), "state": state,
             "history": history, "runs": list(runs), "campaign": "x",
             "outputs": {"summary": None, "tickets": [], "comments": []}}
        t.update(extra)
        put(self.root, f"qa/tasks/{tid}.json", t)
        return t

    def request(self, rid, typ, state, created, clickup=None, **fields):
        r = {"kind": "worker_request", "schema": 1, "id": rid, "type": typ, "state": state, "created": created,
             "claimed_at": None, "done_at": None, "link": None, "notes": [], "result": None}
        if clickup:
            r["clickup"] = {"task_id": clickup, "custom_id": None, "title": NAMES[clickup],
                            "url": f"https://app.clickup.com/t/{clickup}"}
        r.update(fields)
        put(self.root, f"qa/runner/state/worker/{rid}.json", r)
        return r

    def ticket(self, d, run_rel):
        put(self.root, f"{d}/title.txt", "[CT] Planner: rows missing (W044)\n")
        put(self.root, f"{d}/description.txt", "## Issue Summary\nRows missing.\n")
        put(self.root, f"{d}/draft.json", {"source": run_rel, "run_record": run_rel, "finding": "confirmed"})

    def build(self):
        r = self.root
        put(r, "qa/runner/engine.json", {"env": "staging", "account": ENGINE_ACCOUNT})
        cases = [case("TERR-021", "Custom terrain 28 m limit at Spain: existing map and Import file", "tablet",
                      TERR21_STEPS),
                 case("TERR-022", "Custom terrain 28 m limit at Spain, in parts", "tablet", TERR22_STEPS, PARTS),
                 case("OFFS-001", "Offline sites: new QA_ site with offline use, then a flight offline", "tablet",
                      SIMPLE),
                 case("PLAN-001", "Optimize flight on an L-shaped area (web)", "web", PLAN_STEPS),
                 case("PLAN-002", "Area splitting on a hilly area (web)", "web", PLAN_STEPS),
                 case("UPG-001", "App log files stay readable", "tablet", SIMPLE),
                 case("UPG-002", "Old log files are kept", "tablet", SIMPLE),
                 case("WIFI-001", "Wi-Fi off and on again", "tablet", SIMPLE),
                 case("TERR-030", "Terrain selector lists the DSM", "both", TERR30_STEPS)]
        for c in cases:
            put(r, f"qa/cases/{c['id']}.json", c)
        # a newer version of OFFS-001 that no test ran yet
        put(r, "qa/cases/OFFS-001.c1.json", case("OFFS-001", "Offline sites: tablet-named QA_ site", "tablet",
                                                 SIMPLE, created="2026-11-03T08:00:00.000Z"))
        self.sha = {c["id"]: sha(r, f"qa/cases/{c['id']}.json") for c in cases}
        self.offs_c1 = sha(r, "qa/cases/OFFS-001.c1.json")

        # T1: New test, answered plan, every case a test error; ET65's TERR-021 failure was corrected (c1)
        self.run(CAMP_A, RA1, "TERR-022", "ERROR", "2026-11-01T09:10:00.000Z", "2026-11-01T09:12:00.000Z",
                 [ok(1), ok(2), {"n": 3, "error": "covered: Import file"}],
                 harness={"component": "act.py", "detail": "covered: Import file"})
        self.run(CAMP_A, RA2, "TERR-022", "ERROR", "2026-11-01T09:10:01.000Z", "2026-11-01T09:11:31.000Z",
                 [ok(1), {"n": 2, "error": "not found: card"}],
                 harness={"component": "act.py", "detail": "not found: card"})
        self.run(CAMP_A, RA3, "TERR-021", "ERROR", "2026-11-01T09:20:00.000Z", "2026-11-01T09:31:25.000Z",
                 [ok(1), ok(2), {"n": 3, "error": "timed out"}], harness={"component": "act.py", "detail": "timed out"})
        base = self.run(CAMP_A, RA4, "TERR-021", "FAIL", "2026-11-01T09:20:01.000Z", "2026-11-01T09:21:00.000Z",
                        [missed(1)], values={"x": 0})
        put(r, f"qa/runs/{CAMP_A}/{RA4}c1.json", dict(
            base, id=RA4 + "c1", created="2026-11-01T09:59:00.000Z", supersedes={"id": RA4, "why": "false failure"},
            verdict={"v": "ERROR", "decided": "2026-11-01T09:59:00.000Z",
                     "harness": {"component": "case", "detail": "the check was wrong"}}))
        put(r, EXPLAIN, {"cases": {"TERR-021": {
            "tested": "New site at Spain.", "happened": "Both tablets stopped.",
            "why": "Test error, says nothing about the app: the site lookup by name returned nothing.",
            "not_tested": "Web not in scope."}}})
        self.task(T1, "2026-11-01T09:00:00.000Z", [RA1, RA2, RA3, RA4], ["TERR-022", "TERR-021"], CU_A,
                  results_at="2026-11-01T10:00:00.000Z", intake=True, guidance=GUIDANCE,
                  origin={"check": "W-20261101-002", "start": "W-20261101-003"},
                  plan_cases=[{"id": "TERR-021", "text": "Spain 40.4N: 28 m selectable, 29 m greyed out",
                               "targets": ["TA5", "ET65"]}],
                  plan_summary="Test: TERR-022 and TERR-021 on TA5, ET65.\nNot tested in this run: W044 (web): "
                               "tablets only.",
                  outputs={"summary": None, "tickets": [], "comments": [], "explain": [EXPLAIN]})
        self.request("W-20261101-001", "check", "done", "2026-11-01T08:40:00.000Z", CU_A, intake=True,
                     guidance=GUIDANCE, targets=["TA5", "ET65"],
                     result={"title": "Custom terrain on Android: 28 m limit", "needs": [{"id": "N1"}]})
        self.request("W-20261101-002", "check", "done", "2026-11-01T08:50:00.000Z", CU_A, intake=True,
                     guidance=GUIDANCE, targets=["TA5", "ET65"], follows="W-20261101-001",
                     answers=[{"need": "N1", "choice": "both"}],
                     result={"not_covered": ["reopening the flight offline"], "needs": []})
        self.request("W-20261101-003", "start", "done", "2026-11-01T09:00:00.000Z", CU_A, intake=True,
                     guidance=GUIDANCE, task=T1, check="W-20261101-002", targets=["TA5", "ET65"],
                     claimed_at="2026-11-01T09:00:30.000Z", done_at="2026-11-01T10:00:00.500Z",
                     cases=[{"engine_cases": ["TERR-022"], "text": "Spain in parts: new site, 28 m in, 29 m out"}],
                     notes=[{"at": "2026-11-01T09:01:00.000Z", "text": "Running TERR-022 on TA5, ET65"},
                            {"at": "2026-11-01T10:00:00.400Z",
                             "text": "2 of 3 devices; TA5 and ET65: ERROR on both cases. Fix the 'Import file' tap."}])

        # CU_C: OFFS-001 twice a test error, then passed (T5 replaces T3 and T4)
        self.run(CAMP_C, RC1, "OFFS-001", "ERROR", "2026-11-02T08:05:00.000Z", "2026-11-02T08:06:00.000Z",
                 [{"n": 1, "error": "x"}], harness={"component": "act.py", "detail": "x"})
        for rid in (RC2, RC3):
            self.run(CAMP_C, rid, "OFFS-001", "ERROR", "2026-11-02T08:35:00.000Z", "2026-11-02T08:36:00.000Z",
                     [{"n": 1, "error": "x"}], harness={"component": "act.py", "detail": "x"})
        for rid in (RC4, RC5):
            self.run(CAMP_C, rid, "OFFS-001", "PASS", "2026-11-02T09:05:00.000Z", "2026-11-02T09:10:00.000Z",
                     [ok(1), ok(2), ok(3)])
        offline = "Create a site with offline use (tablets), then a flight offline"
        self.task(T3, "2026-11-02T08:00:00.000Z", [RC1], ["OFFS-001"], CU_C, results_at="2026-11-02T08:20:00.000Z",
                  intake=True, guidance=offline)
        self.task(T4, "2026-11-02T08:30:00.000Z", [RC2, RC3], ["OFFS-001"], CU_C,
                  results_at="2026-11-02T08:50:00.000Z", intake=True, guidance=offline)
        self.task(T5, "2026-11-02T09:00:00.000Z", [RC4, RC5], ["OFFS-001"], CU_C,
                  results_at="2026-11-02T09:20:00.000Z", intake=True, guidance=offline)

        # CU_E: passed, then the start failed (stopped early)
        self.run(CAMP_E, RE1, "WIFI-001", "PASS", "2026-11-02T10:05:00.000Z", "2026-11-02T10:06:00.000Z",
                 [ok(1), ok(2)])
        self.task(T23, "2026-11-02T10:00:00.000Z", [RE1], ["WIFI-001", "UPG-001"], CU_E, state="queued",
                  origin={"check": "W-20261102-009", "start": "W-20261102-010"})
        self.request("W-20261102-009", "check", "done", "2026-11-02T09:58:00.000Z", CU_E)
        self.request("W-20261102-010", "start", "failed", "2026-11-02T10:00:00.000Z", CU_E, task=T23,
                     done_at="2026-11-02T10:30:00.000Z",
                     notes=[{"at": "2026-11-02T10:30:00.000Z",
                             "text": "Hover stopped while handling this request."}])

        # CU_B on W044: PLAN-001 failed then passed; PLAN-002 failed, failed again, then passed
        self.run(CAMP_B, RB1, "PLAN-001", "FAIL", "2026-10-30T09:05:00.000Z", "2026-10-30T09:06:00.000Z",
                 [ok(1), ok(2), missed(3)], values={"x": 0})
        self.run(CAMP_B, RB2, "PLAN-001", "PASS", "2026-10-30T09:35:00.000Z", "2026-10-30T09:36:00.000Z",
                 [ok(1), ok(2), ok(3)])
        for rid, at in ((RB3, "2026-10-30T10:02:00.000Z"), (RB4, "2026-10-30T10:12:00.000Z")):
            self.run(CAMP_B, rid, "PLAN-002", "FAIL", at, at, [ok(1), ok(2), missed(3)], values={"x": 0})
        self.run(CAMP_B, RB5, "PLAN-002", "PASS", "2026-10-30T10:22:00.000Z", "2026-10-30T10:23:00.000Z",
                 [ok(1), ok(2), ok(3)])
        for tid, created, run, cid, res in ((T6, "09:00", RB1, "PLAN-001", "09:20"),
                                            (T7, "09:30", RB2, "PLAN-001", "09:50"),
                                            (T8, "10:00", RB3, "PLAN-002", "10:05"),
                                            (T9, "10:10", RB4, "PLAN-002", "10:15"),
                                            (T10, "10:20", RB5, "PLAN-002", "10:25")):
            self.task(tid, f"2026-10-30T{created}:00.000Z", [run], [cid], CU_B,
                      results_at=f"2026-10-30T{res}:00.000Z", targets=("W044",),
                      outputs={"summary": None, "tickets": [], "comments": [COMMENT_T7] if tid == T7 else []})
        put(r, COMMENT_T7, "PLAN-001 passed on W044.")
        self.ticket(TICKET_T9, f"qa/runs/{CAMP_B}/{RB4}.json")

        # CU_D: posted, marked done, and a Today test with guidance (UPG-001 on TA5, UPG-002 on ET65)
        for rid, cid, at in ((RD1, "UPG-001", "09:05"), (RD2, "UPG-002", "10:05"), (RD3, "UPG-001", "11:05"),
                             (RD4, "UPG-002", "11:06")):
            self.run(CAMP_D, rid, cid, "PASS", f"2026-10-31T{at}:00.000Z", f"2026-10-31T{at}:30.000Z", [ok(1), ok(2)])
        put(r, COMMENT_T11, "UPG-001 passed on TA5.")
        put(r, COMMENT_T11[:-3] + ".posted.json", {"comment_id": "901", "posted_at": "2026-10-31T09:45:00.000Z",
                                                   "sha256": "0" * 64, "task_id": CU_D})
        h = [{"at": "2026-10-31T09:00:00.000Z", "from": None, "to": "queued", "by": "user", "via": "app", "note": None},
             {"at": "2026-10-31T09:20:00.000Z", "from": "running", "to": "results_ready", "by": "claude",
              "via": "qa-run", "note": None},
             {"at": "2026-10-31T09:30:00.000Z", "from": "results_ready", "to": "greenlit", "by": "user", "via": "app",
              "note": None},
             {"at": "2026-10-31T09:40:00.000Z", "from": "greenlit", "to": "drafted", "by": "claude", "via": "qa-run",
              "note": None}]
        self.task(T11, "2026-10-31T09:00:00.000Z", [RD1], ["UPG-001"], CU_D, state="drafted", history=h,
                  targets=("TA5",), outputs={"summary": None, "tickets": [], "comments": [COMMENT_T11]})
        self.request("W-20261031-005", "write", "done", "2026-10-31T09:30:00.000Z", CU_D, task=T11,
                     outputs=["comment"])
        h = [{"at": "2026-10-31T10:00:00.000Z", "from": None, "to": "queued", "by": "user", "via": "app", "note": None},
             {"at": "2026-10-31T10:20:00.000Z", "from": "running", "to": "results_ready", "by": "claude",
              "via": "qa-run", "note": None},
             {"at": "2026-10-31T10:30:00.000Z", "from": "results_ready", "to": "closed", "by": "user", "via": "app",
              "note": None}]
        self.task(T12, "2026-10-31T10:00:00.000Z", [RD2], ["UPG-002"], CU_D, state="closed", history=h,
                  targets=("TA5",))
        self.task(T13, "2026-10-31T11:00:00.000Z", [RD3, RD4], ["UPG-001", "UPG-002"], CU_D,
                  results_at="2026-10-31T11:20:00.000Z", targets=("TA5",),
                  guidance="In the results, say how many logcat files are in Logs.",
                  text="Run from Today: x. Cases UPG-001, UPG-002 on TA5.",
                  origin={"check": "W-20261031-006", "start": "W-20261031-007"})
        self.request("W-20261031-006", "check", "done", "2026-10-31T10:58:00.000Z", CU_D)
        self.request("W-20261031-007", "start", "done", "2026-10-31T11:00:00.000Z", CU_D, task=T13,
                     done_at="2026-10-31T11:20:00.000Z", engine_cases=["UPG-001", "UPG-002"],
                     cases=[{"engine_cases": ["UPG-001"], "text": "Logs stay readable after the update"}])

        # runs without a task (chat runs): TERR-030 passed on TA5, failed on W044 with a ticket drafted
        self.run(CHAT, RX1, "TERR-030", "PASS", "2026-10-21T11:49:00.000Z", "2026-10-21T11:49:40.000Z",
                 [ok(1), ok(2), ok(3)])
        self.run(CHAT, RX2, "TERR-030", "FAIL", "2026-10-21T11:50:00.000Z", "2026-10-21T11:50:40.000Z",
                 [ok(1), missed(3)], values={"x": 0})
        self.ticket(TICKET_RX2, f"qa/runs/{CHAT}/{RX2}.json")

        # running now: T18 (Hover took it; TA5 at step 4 of TERR-022, ET65 handed back), T19 (a chat's /qa-run),
        # T20 (Hover has not taken it and is off)
        self.task(T18, "2026-11-03T09:00:00.000Z", [], ["TERR-022", "TERR-021"], CU_A, state="queued",
                  intake=True, guidance="Custom terrain offline",
                  origin={"check": "W-20261103-001", "start": "W-20261103-002"},
                  case_versions=[{"id": "TERR-022", "file": "qa/cases/TERR-022.json", "sha256": self.sha["TERR-022"]}])
        self.request("W-20261103-001", "check", "done", "2026-11-03T08:50:00.000Z", CU_A, intake=True,
                     guidance="Custom terrain offline", targets=["TA5", "ET65"], result={"needs": []})
        self.request("W-20261103-002", "start", "claimed", "2026-11-03T09:00:00.000Z", CU_A, intake=True,
                     guidance="Custom terrain offline", task=T18, targets=["TA5", "ET65"],
                     claimed_at="2026-11-03T09:01:00.000Z",
                     notes=[{"at": "2026-11-03T09:01:30.000Z", "text": "Running TERR-022 on TA5, ET65"}])
        put(r, SHOT, PNG)
        put(r, "qa/runner/state/progress/S-TA5.json",
            {"run": "R-20261103-TA5-TERR-022-01", "case": "TERR-022", "target": "TA5", "attempt": 1,
             "step": {"n": 4, "text": "Read the warning", "started": "2026-11-03T09:05:00.000Z"}, "steps_total": 5,
             "started": "2026-11-03T09:02:00.000Z", "updated": "2026-11-03T09:05:05.000Z", "state": "running",
             "verdict": None, "last_shot": SHOT})
        put(r, "qa/runner/state/progress/S-ET65.json",
            {"run": RL1, "case": "TERR-022", "target": "ET65", "attempt": 1, "step": None, "steps_total": 5,
             "started": "2026-11-03T09:02:01.000Z", "updated": "2026-11-03T09:03:00.000Z", "state": "finished",
             "verdict": "BLOCKED", "last_shot": None})
        self.run(LIVE, RL1, "TERR-022", "BLOCKED", "2026-11-03T09:02:01.000Z", "2026-11-03T09:03:00.000Z",
                 [ok(1)], handback={"step": 2, "reason": "needs a PIN", "command": "x"})
        self.task(T19, "2026-11-03T10:00:00.000Z", [], ["PLAN-001"], None, state="running", targets=("W044",),
                  text="t")
        self.task(T20, "2026-11-03T11:00:00.000Z", [], ["OFFS-001"], CU_C, state="queued", targets=("TA5",),
                  intake=True, guidance="Offline sites again", origin={"start": "W-20261103-004"})
        self.request("W-20261103-004", "start", "new", "2026-11-03T11:00:00.000Z", CU_C, intake=True,
                     guidance="Offline sites again", task=T20, targets=["TA5"], title="Offline sites on TA5")

        # zombies: queued with no run and no open start
        self.task(T21, "2026-10-22T09:00:00.000Z", [], ["UPG-001"], CU_D, state="queued", targets=("TA5",))
        self.task(T22, "2026-10-23T09:00:00.000Z", [], ["UPG-001"], CU_D, state="queued", targets=("TA5",),
                  origin={"start": "W-20261023-001"})
        self.request("W-20261023-001", "start", "failed", "2026-10-23T09:00:00.000Z", CU_D, task=T22)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fx = Fixture()
        cls.root = cls.fx.root
        cls.data = data_model.Data(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.fx.close()

    def one(self, task=None, case=None, posting=()):
        return views.result_cards(data_model.Data(self.root), task=task, case=case, posting=posting)

    def card(self, out, cid):
        return next(c for c in out["cards"] if c["case"] == cid)


# ---- the title rule ------------------------------------------------------------------------------------------------

class TitleTests(Base):
    def test_description_title_cuts_the_persons_words(self):
        self.assertEqual(views.description_title(GUIDANCE, views.TITLE_HEADER), HEADER_TITLE)
        self.assertEqual(views.description_title(GUIDANCE, views.TITLE_LIST), LIST_TITLE, "whole lines that fit")
        one = ("Map import (without using it as custom terrain for flight planning) accepts any resolution - try with "
               "both high and low resolution here")
        self.assertEqual(views.description_title(one, 90),
                         "Map import accepts any resolution - try with both high and low resolution here")
        steps = ("In a downloaded site, while offline, \n- open/edit/save an existing flight and check so the changes "
                 "are saved. Go online and make sure the flight plan has your new changes.\n- Create a new flight\n"
                 "- Import a flight.")
        self.assertEqual(views.description_title(steps, 90),
                         "In a downloaded site, while offline, · open/edit/save an existing flight and check so…")
        marks = ("Test cases:\n1. Import a 28 m DSM\n2) Import a 29 m DSM\n* Check the warning (in the dialog (both))"
                 "\n•Spain")
        self.assertEqual(views.description_title(marks, 90),
                         "Import a 28 m DSM · Import a 29 m DSM · Check the warning · Spain",
                         "list marks, a label line and nested asides go")
        self.assertEqual(views.description_title("Alpha beta\nGamma delta\nEpsilon", 20), "Alpha beta…",
                         "lines left out: an ellipsis")
        self.assertEqual(views.description_title("x " * 60, 30), "x x x x x x x x x x x x x x…")
        for nothing in (None, "", "  \n\n", "Test cases:\n(only an aside)", 5):
            self.assertIsNone(views.description_title(nothing, 90), nothing)
        self.assertEqual(views.description_title(f"Ask {EMAIL} first", 90), "Ask [EMAIL] first", "redacted")

    def test_the_rule_in_order(self):
        ctx = views._Ctx(self.data)
        t1 = ctx.tasks[T1]
        # 3: a New test task's description (two cases, no plan title), header and list form
        self.assertEqual(views.test_title(ctx, t1, views.TITLE_HEADER), HEADER_TITLE)
        self.assertEqual(views.test_title(ctx, t1), LIST_TITLE)
        # 1: the plan's title, from the task, the start request or the check request's result
        self.assertEqual(views.test_title(ctx, dict(t1, title="Custom terrain on Android")),
                         "Custom terrain on Android")
        self.assertEqual(views.test_title(ctx, ctx.tasks[T20]), "Offline sites on TA5", "the start request's title")
        checked = dict(t1, origin={"check": "W-20261101-001"})
        self.assertEqual(views.test_title(ctx, checked), "Custom terrain on Android: 28 m limit", "the check's title")
        # 2: one case: the title of the version that ran (T5 ran c0; c1 is newer), else the pinned one, else the newest
        self.assertEqual(views.test_title(ctx, ctx.tasks[T5]),
                         "Offline sites: new QA_ site with offline use, then a flight offline")
        never = {"id": "T-20261231-001", "cases": ["OFFS-001"], "intake": True, "guidance": "Offline"}
        self.assertEqual(views.test_title(ctx, never), "Offline sites: tablet-named QA_ site", "the newest version")
        pinned = dict(never, case_versions=[{"id": "OFFS-001", "sha256": self.fx.sha["OFFS-001"]}])
        self.assertEqual(views.test_title(ctx, pinned),
                         "Offline sites: new QA_ site with offline use, then a flight offline", "the pinned version")
        # a Today task is never titled by its guidance (an instruction to Hover): its ClickUp name instead
        self.assertEqual(views.test_title(ctx, ctx.tasks[T13]), NAMES[CU_D])
        self.assertEqual(views.test_title(ctx, dict(ctx.tasks[T13], intake=True)),
                         "In the results, say how many logcat files are in Logs.")
        # 4: the ClickUp name from the request, else from the task's text; 5: the task id
        bare = {"id": "T-20261231-002", "cases": ["A-001", "B-002"], "clickup": {"task_id": CU_E},
                "text": "Run from Today: Wi-Fi checks. Cases A-001, B-002 on TA5."}
        self.assertEqual(views.test_title(ctx, bare), "Wi-Fi checks")
        self.assertEqual(views.test_title(ctx, dict(bare, text="t", clickup=None)), "T-20261231-002")
        self.assertEqual(views.test_title(ctx, dict(bare, title="x" * 200), views.TITLE_LIST), "x" * 89 + "…")
        # the ClickUp name is never the title of a test that has words of its own
        for t in (T1, T5, T20):
            self.assertNotIn(views.test_title(ctx, ctx.tasks[t]), NAMES.values(), t)

    def test_request_titles_for_your_plans(self):
        reqs = {p: data_model.Data(self.root)._json(self.fx.path(f"qa/runner/state/worker/{p}.json"))
                for p in ("W-20261101-001", "W-20261101-002", "W-20261101-003", "W-20261103-004",
                          "W-20261031-005", "W-20261102-009")}
        self.assertEqual(views.request_title(reqs["W-20261101-001"]), "Custom terrain on Android: 28 m limit")
        self.assertEqual(views.request_title(reqs["W-20261101-002"]), LIST_TITLE, "no plan title: the description")
        self.assertEqual(views.request_title(reqs["W-20261101-003"]), LIST_TITLE)
        self.assertEqual(views.request_title(reqs["W-20261103-004"]), "Offline sites on TA5", "a start's own title")
        self.assertIsNone(views.request_title(reqs["W-20261031-005"]), "a write request")
        self.assertIsNone(views.request_title(reqs["W-20261102-009"]), "a check that is not from New test")
        no_words = dict(reqs["W-20261101-002"], guidance="Test cases:")
        self.assertEqual(views.request_title(no_words), NAMES[CU_A], "the ClickUp name")
        self.assertEqual(views.request_title(dict(no_words, clickup={"task_id": CU_A})), CU_A)
        self.assertEqual(views.request_title(dict(no_words, clickup=None)), "W-20261101-002")


# ---- one test on Results ---------------------------------------------------------------------------------------------

class OneTestTests(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.t1 = views.result_cards(cls.data, task=T1)

    def test_the_header(self):
        t = self.t1["test"]
        self.assertEqual(set(t), {"task", "state", "group", "title", "clickup", "devices", "not_tested_devices",
                                  "build", "native", "run_at", "results_at", "stopped_early", "result", "done_note",
                                  "asked", "comment", "outputs", "plan_again", "next", "can_mark_done",
                                  "mark_done_why", "done_how", "open_write", "waiting", "also_running"})
        self.assertEqual((t["task"], t["state"], t["group"], t["title"]), (T1, "results_ready", "tested", HEADER_TITLE))
        self.assertEqual(t["clickup"], {"task_id": CU_A, "custom_id": None, "url": f"https://app.clickup.com/t/{CU_A}",
                                        "title": NAMES[CU_A]})
        self.assertEqual((t["devices"], t["not_tested_devices"]),
                         (["TA5", "ET65"], [{"tag": "W044", "why": "tablets only"}]))
        self.assertEqual((t["build"], t["native"], t["run_at"], t["results_at"], t["stopped_early"]),
                         ("1.76.0 staging", "144", "2026-11-01T09:00:00.000Z", "2026-11-01T10:00:00.000Z", False))
        self.assertEqual(t["result"], {
            "verdict": "not_decided", "verdict_label": "Test error", "word": "test error",
            "counts": {"failed": 0, "failed_suspected": 0, "blocked": 0, "not_decided": 2, "partly": 0, "passed": 0,
                       "not_tested": 0},
            "case_count": 2, "sentence": "Test error in 2 of 2 cases, on TA5 and ET65",
            "meaning": "The test itself stopped before it could check the app. This says nothing about the app."})
        self.assertEqual(t["done_note"], '2 of 3 devices; TA5 and ET65: test error on both cases. Fix the "Import file" '
                                         'tap.', "the start's last note, ERROR in words")
        self.assertEqual(t["asked"], {"guidance": GUIDANCE, "plan_summary": self.fx_task(T1)["plan_summary"],
                                      "not_covered": ["reopening the flight offline"]})
        self.assertEqual(t["comment"]["state"], "none")
        self.assertEqual(t["outputs"], {"summary": None, "comment_text": None, "comment_file": None})
        # T18's plan (W-20261103-001, CU_A, run since) came after T1's: Plan again is secondary (2026-11-05, FINDINGS
        # #21), so the next press is Add result
        self.assertEqual(t["plan_again"], {"intake": True, "prepare": True, "task_id": CU_A, "guidance": GUIDANCE,
                                           "targets": ["TA5", "ET65"], "answers_from": "W-20261101-002",
                                           "newer": {"id": "W-20261103-001", "fresh": False}})
        self.assertEqual(t["next"], {"press": "add_result", "label": "Add result", "case": None})
        self.assertEqual((t["can_mark_done"], t["mark_done_why"], t["done_how"], t["open_write"]),
                         (True, None, None, False))
        self.assertEqual(t["waiting"], 7, "the other 7 tests in Tested")
        self.assertEqual([(x["task"], x["title"], x["started"]) for x in t["also_running"]],
                         [(T20, "Offline sites on TA5", "2026-11-03T11:00:00.000Z"),
                          (T19, "Optimize flight on an L-shaped area (web)", "2026-11-03T10:00:00.000Z"),
                          (T18, "Custom terrain offline", "2026-11-03T09:00:00.000Z")])
        self.assertIsNone(self.t1["running"])

    def fx_task(self, tid):
        return views._Ctx(self.data).tasks[tid]

    def test_cards_are_this_tests_own_in_the_plans_order(self):
        self.assertEqual([c["key"] for c in self.t1["cards"]], [f"{T1}|TERR-022", f"{T1}|TERR-021"])
        c = self.card(self.t1, "TERR-021")
        title = "Custom terrain 28 m limit at Spain: existing map and Import file"
        self.assertEqual((c["title"], c["case_title"]), (title, title), "the case title, not the ClickUp name")
        self.assertEqual(c["devices"], [
            {"tag": "TA5", "verdict": "not_decided", "verdict_label": "Test error", "word": "test error",
             "failed_step": {"n": 3, "text": "Wait for the processing, then read the site\'s maps"},
             "duration_s": 685, "runs": 1, "started": "2026-11-01T09:20:00.000Z", "ended": "2026-11-01T09:31:25.000Z",
             "run": RA3},
            {"tag": "ET65", "verdict": "not_decided", "verdict_label": "Test error", "word": "test error",
             "failed_step": {"n": 1, "text": "Top bar: Sites (a planner left open asks to save first)"},
             "duration_s": 59, "runs": 1, "started": "2026-11-01T09:20:01.000Z", "ended": "2026-11-01T09:21:00.000Z",
             "run": RA4 + "c1"}])
        self.assertEqual(c["what_it_checks"], "Spain 40.4N: 28 m selectable, 29 m greyed out", "the plan's line")
        self.assertEqual(c["why_line"], "the site lookup by name returned nothing.", "Hover's why, no verdict clause")
        self.assertEqual((c["rows_from"], c["open"]), ("checks", False))
        for k in ("explain", "auto", "proof", "run_ids", "tickets", "report", "comment", "outputs", "state"):
            self.assertIn(k, c)
        self.assertNotIn("steps", c, "every engine step stays behind 'Run records ›' (GET /api/results)")
        self.assertFalse([k for k in c if k.startswith("_")])
        self.assertEqual(c["comment"], self.t1["test"]["comment"])

    def test_rows_without_parts_are_the_checks_and_each_stop(self):
        rows = self.card(self.t1, "TERR-021")["rows"]
        self.assertEqual([(r["n"], r["cells"]) for r in rows], [
            (1, {"TA5": "as_expected", "ET65": "stopped"}),       # ET65's stop step (no check)
            (2, {"TA5": "as_expected", "ET65": "not_run"}),
            (3, {"TA5": "stopped", "ET65": "not_run"}),           # TA5's stop step is a check
            (4, {"TA5": "not_run", "ET65": "not_run"})], "the clean-up step's assert is left out")
        self.assertEqual(rows[3]["text"], 'Read the dialog: "Dataset has too low resolution..." shown')

    def test_rows_with_parts(self):
        c = self.card(self.t1, "TERR-022")
        self.assertEqual(c["rows_from"], "parts")
        self.assertEqual(c["rows"], [
            {"n": 1, "text": "New QA_ site at Spain 40.4N", "cells": {"TA5": "as_expected", "ET65": "as_expected"}},
            {"n": 2, "text": "Maps import, 28 m DSM: accepted", "cells": {"TA5": "as_expected", "ET65": "stopped"}},
            {"n": 3, "text": "Import file, 29 m DSM: warning shown, Next off",
             "cells": {"TA5": "stopped", "ET65": "not_run"}}])
        self.assertEqual(c["what_it_checks"], "Spain in parts: new site, 28 m in, 29 m out", "the start's case line")
        self.assertTrue(c["why_line"].startswith("The test itself stopped at step 3"), "auto.why when no explain")
        # steps_of carries each step's part; clean-up carries none
        ctx = views._Ctx(self.data)
        e = next(x for x in ctx.entries if x["id"] == RA1)
        self.assertEqual([(s["n"], s["part"]) for s in views.steps_of(ctx, e)], [(1, 1), (2, 2), (3, 3), (4, 3), (5, None)])

    def test_a_device_without_the_case_or_the_step_has_an_empty_cell(self):
        # T13 ran UPG-001 on TA5 and UPG-002 on ET65: one column per device of the test
        t13 = self.one(task=T13)
        self.assertEqual((t13["test"]["devices"], t13["test"]["result"]["sentence"]),
                         (["TA5", "ET65"], "Passed: 2 of 2 cases, on TA5 and ET65"))
        self.assertEqual([(r["n"], r["cells"]) for r in self.card(t13, "UPG-001")["rows"]],
                         [(2, {"TA5": "as_expected", "ET65": None})])
        self.assertEqual([(r["n"], r["cells"]) for r in self.card(t13, "UPG-002")["rows"]],
                         [(2, {"TA5": None, "ET65": "as_expected"})])
        self.assertEqual(self.card(t13, "UPG-001")["what_it_checks"], "Logs stay readable after the update",
                         "a Today test: the start request's case line")
        self.assertIsNone(self.card(t13, "UPG-001")["why_line"], "passed: no why")
        # a tablet-only step has no cell on W044 (the chat card of TERR-030 ran on TA5 and W044)
        chat = self.card(self.one(case="TERR-030"), "TERR-030")
        self.assertEqual([(r["n"], r["text"], r["cells"]) for r in chat["rows"]],
                         [(2, "Tablet only: pick the file", {"TA5": "as_expected", "W044": None}),
                          (3, "Check the list", {"TA5": "as_expected", "W044": "not_as_expected"})])
        # a failed device opens its row; the card holds this test's runs only (no merge across tests)
        c = self.card(self.one(task=T9), "PLAN-002")
        self.assertEqual([(r["n"], r["cells"]) for r in c["rows"]], [(3, {"W044": "not_as_expected"})])
        self.assertTrue(c["open"], "a device failed: the row opens")
        self.assertEqual(c["why_line"], "Step 3 did not happen as expected: After: area rows.")
        self.assertEqual((c["runs"], c["tasks"], c["run_ids"]), (1, [T9], [RB4]), "no merge across tests")

    def test_current_case_and_none(self):
        cur = self.one(task="current")
        self.assertEqual(cur["test"]["task"], T20, "the newest test by Run press, running or not")
        self.assertEqual(self.one(case="TERR-021")["test"]["task"], T1)
        opened = self.one(case="TERR-021")
        self.assertEqual([(c["case"], c["open"]) for c in opened["cards"]], [("TERR-022", False), ("TERR-021", True)])
        both = self.one(task=T1, case="PLAN-001")
        self.assertEqual(both["test"]["task"], T1, "a case the test does not have is ignored")
        self.assertFalse(any(c["open"] for c in both["cards"]))
        self.assertEqual(self.one(case="OFFS-001")["test"]["task"], T5, "the newest test that ran it")
        for task in (T21, T22, "T-20991231-001"):
            with self.assertRaises(views.NotFound) as e:
                self.one(task=task)
            self.assertEqual(str(e.exception), f"no test {task}: it has no runs and is not running")
        with self.assertRaises(views.NotFound) as e:
            self.one(case="NONE-001")
        self.assertEqual(str(e.exception), "no run of NONE-001")

    def test_a_chat_run(self):
        out = self.one(case="TERR-030")
        t, (c,) = out["test"], out["cards"]
        self.assertIsNone(out["running"])
        for k in ("task", "state", "group", "clickup", "asked", "comment", "outputs", "plan_again", "done_note",
                  "done_how"):
            self.assertIsNone(t[k], k)
        self.assertEqual((t["title"], t["devices"], t["build"], t["native"]),
                         ("Terrain selector lists the DSM", ["TA5", "W044"], "1.76.0 staging", "144"))
        self.assertEqual((t["run_at"], t["results_at"]), ("2026-10-21T11:49:00.000Z", "2026-10-21T11:50:40.000Z"))
        self.assertEqual(t["result"]["sentence"], "Failed (suspected) in 1 of 1 case, on W044")
        self.assertEqual(t["next"], {"press": "file_ticket", "label": "File ticket", "case": "TERR-030"})
        self.assertEqual((t["can_mark_done"], t["mark_done_why"]),
                         (False, "A run from a chat is not a test, so it cannot be marked done."))
        self.assertEqual((t["stopped_early"], t["waiting"]), (False, 8))
        self.assertEqual((c["key"], c["open"], c["title"]), (f"tests/{CHAT}|TERR-030|{self.fx.sha['TERR-030']}", True,
                                                            "Terrain selector lists the DSM"))
        self.assertEqual([x["dir"] for x in c["tickets"]], [TICKET_RX2])

    def test_a_running_test(self):
        out = self.one(task=T18)
        t, run = out["test"], out["running"]
        self.assertEqual(out["cards"], [], "verdicts show when the test ends")
        self.assertEqual((t["group"], t["result"], t["next"], t["plan_again"], t["done_note"], t["results_at"]),
                         ("ongoing", None, None, None, None, None))
        self.assertEqual((t["can_mark_done"], t["mark_done_why"]), (False, "The test is still running."))
        self.assertEqual((t["devices"], t["title"]), (["TA5", "ET65"], "Custom terrain offline"))
        self.assertEqual({k: run[k] for k in ("task", "title", "cases", "started", "state", "request", "note",
                                              "wingmate", "current_case")},
                         {"task": T18, "title": "Custom terrain offline", "cases": ["TERR-022", "TERR-021"],
                          "started": "2026-11-03T09:00:00.000Z", "state": "queued",
                          "request": {"id": "W-20261103-002", "state": "claimed"},
                          "note": "Running TERR-022 on TA5, ET65", "wingmate": "working",
                          "current_case": {"n": 1, "of": 2, "id": "TERR-022"}})
        ta5, et65 = run["devices"]
        self.assertEqual({k: ta5[k] for k in ("tag", "state", "step", "of", "text", "case", "part", "waits_at")},
                         {"tag": "TA5", "state": "running", "step": 4, "of": 5, "text": "Read the warning",
                          "case": "TERR-022", "part": {"n": 3, "of": 3, "text": "Import file, 29 m DSM: warning "
                                                                               "shown, Next off"},
                          "waits_at": None})
        self.assertEqual(ta5["shot_url"], "/files/" + SHOT)
        self.assertEqual({k: et65[k] for k in ("state", "verdict", "run", "part", "waits_at")},
                         {"state": "finished", "verdict": "BLOCKED", "run": RL1, "part": None, "waits_at": 2})
        self.assertEqual(run["case_rows"], [
            {"case": "TERR-022", "title": "Custom terrain 28 m limit at Spain, in parts",
             "devices": {"TA5": "running", "ET65": "done"}},
            {"case": "TERR-021", "title": "Custom terrain 28 m limit at Spain: existing map and Import file",
             "devices": {"TA5": "waiting", "ET65": "waiting"}}])
        # T20's start request waits while Hover works on T18's (worker.status: busy); a chat's /qa-run: working
        self.assertEqual(self.one(task=T20)["running"]["wingmate"], "waiting")
        chat = self.one(task=T19)["running"]
        self.assertEqual((chat["wingmate"], chat["request"], chat["current_case"]), ("working", None, None))
        self.assertEqual(chat["case_rows"], [{"case": "PLAN-001", "title": "Optimize flight on an L-shaped area (web)",
                                              "devices": {"W044": "waiting"}}])

    def test_result_sentences(self):
        r = views._result({"A-001": "failed_suspected", "B-001": "passed", "C-001": "passed"},
                          {("A-001", "W044"): "failed_suspected", ("B-001", "W044"): "passed",
                           ("C-001", "W044"): "passed"})
        self.assertEqual(r["sentence"], "Failed (suspected) in 1 of 3 cases, on W044; 2 passed")
        self.assertEqual(r["meaning"], "The app did not do what the test expects. It is not confirmed yet.")
        r = views._result({"A-001": "passed", "B-001": "passed"}, {("A-001", "W044"): "passed",
                                                                  ("B-001", "W044"): "passed"})
        self.assertEqual((r["sentence"], r["meaning"]), ("Passed: 2 of 2 cases, on W044", None))
        r = views._result({"A-001": "not_decided", "B-001": "partly"},
                          {("A-001", "TA5"): "not_decided", ("A-001", "ET65"): "passed", ("B-001", "W044"): "partly"})
        self.assertEqual(r["sentence"], "Test error in 1 of 2 cases, on TA5; 1 partly passed")
        self.assertEqual(views._results_words({"A": "not_decided", "B": "not_decided", "C": "failed_suspected"}),
                         "1 failed (suspected), 2 test errors")

    def test_mark_done_and_its_why(self):
        self.assertEqual(self.one(task=T3)["test"]["mark_done_why"], "It is in Done already.")
        # a test that stopped early while its task was still queued: the person may close it (tasks.TRANSITIONS), so
        # it does not wait in Tested for good (review 2026-11-04)
        stopped = self.one(task=T23)["test"]
        self.assertEqual((stopped["stopped_early"], stopped["can_mark_done"], stopped["mark_done_why"],
                          stopped["next"]), (True, True, None, None))
        self.assertEqual(self.one(task=T7, posting={T7})["test"]["mark_done_why"], "The comment is being posted.")
        self.assertTrue(self.one(task=T5)["test"]["can_mark_done"])

    def test_what_it_checks_leaves_out_the_case_id(self):
        ctx = views._Ctx(self.data)
        task = {"plan_cases": [{"id": "PLAN-001", "text": "PLAN-001 c2: Optimize flight on the QA_ hilly plan"},
                               {"id": "PLAN-002", "text": "PLAN-002:"}]}
        self.assertEqual(views._what_it_checks(ctx, task, "PLAN-001", "auto"), "Optimize flight on the QA_ hilly plan")
        self.assertEqual(views._what_it_checks(ctx, task, "PLAN-002", "auto"), "auto", "nothing left: what it checks")
        self.assertEqual(views._what_it_checks(ctx, {"plan_cases": [{"id": "TERR-012", "text": "Spain 40.4N: 28 m"}]},
                                               "TERR-012", "auto"), "Spain 40.4N: 28 m", "a plain line stays whole")


class OneTestChangesTests(unittest.TestCase):
    """What moves a test: Mark done, an open request, a posted comment, a plan title (a fresh workspace each)."""

    def setUp(self):
        self.fx = Fixture()
        self.root = self.fx.root

    def tearDown(self):
        self.fx.close()

    def one(self, task):
        return views.result_cards(data_model.Data(self.root), task=task)["test"]

    def test_mark_done_moves_the_test_to_done(self):
        writes.Writer(self.root).set_task_state(T5, "closed", by="user", via="app")
        t = self.one(T5)
        self.assertEqual((t["group"], t["done_how"]["how"], t["next"], t["can_mark_done"]),
                         ("done", "marked", None, False))
        self.assertEqual(self.one(T3)["done_how"]["by_task"], T5, "a replaced test stays replaced")

    def test_open_work_holds_mark_done_and_add_result(self):
        writes.Writer(self.root).request_comment(T10)  # Add result pressed: greenlit, a write request open
        t = self.one(T10)
        self.assertEqual((t["can_mark_done"], t["next"], t["comment"]["state"]),
                         (False, None, "drafting"))
        self.assertEqual(t["mark_done_why"], "Hover is still working on this test (a comment, a ticket draft or "
                                             "filing a ticket).")

    def test_a_ticket_being_drafted_holds_report_issue(self):
        self.assertEqual(self.one(T6)["next"]["press"], "report_issue")
        writes.Writer(self.root).create_draft_request({"type": "draft", "task": T6, "case": "PLAN-001", "run": RB1})
        self.assertEqual(self.one(T6)["next"]["press"], "add_result")

    def test_a_posted_comment_moves_the_test_to_done(self):
        put(self.root, COMMENT_T7[:-3] + ".posted.json", {"comment_id": "902", "posted_at": "2026-10-30T11:00:00.000Z",
                                                          "sha256": "0" * 64, "task_id": CU_B})
        t = self.one(T7)
        self.assertEqual((t["group"], t["done_how"]), ("done", {"how": "posted", "at": "2026-10-30T11:00:00.000Z",
                                                                "by_task": None,
                                                                "url": f"https://app.clickup.com/t/{CU_B}"}))
        self.assertEqual(self.one(T6)["group"], "tested", "its failure did not fail again")

    def test_a_test_in_done_offers_no_plan_again(self):
        self.assertIsNotNone(self.one(T1)["plan_again"])
        writes.Writer(self.root).set_task_state(T1, "closed", by="user", via="app")
        t = self.one(T1)
        self.assertEqual((t["group"], t["done_how"]["how"], t["plan_again"], t["next"]), ("done", "marked", None, None))

    def test_a_test_stays_ongoing_until_wingmate_ends_its_start(self):
        # Hover moves the task to results_ready, then writes its done note and ends the start (qa-worker section 2);
        # in between the test is still ongoing, so the result box comes with that note and Mark done waits
        rel = "qa/runner/state/worker/W-20261101-003.json"
        with open(self.fx.path(rel), encoding="utf-8") as f:
            start = json.load(f)
        put(self.root, rel, dict(start, state="claimed", done_at=None, notes=start["notes"][:1]))
        out = views.result_cards(data_model.Data(self.root), task=T1)
        t = out["test"]
        self.assertEqual((t["state"], t["group"], t["result"], t["done_note"], t["can_mark_done"], out["cards"]),
                         ("results_ready", "ongoing", None, None, False, []))
        self.assertEqual((out["running"]["wingmate"], t["mark_done_why"]), ("working", "The test is still running."))
        put(self.root, rel, start)
        t = self.one(T1)
        self.assertEqual((t["group"], t["can_mark_done"], t["done_note"][:14]), ("tested", True, "2 of 3 devices"))

    def test_a_drafted_ticket_opens_its_row_until_its_run_is_corrected(self):
        # an older run of T1's TERR-021 on TA5 failed and Hover drafted its ticket; the newest run there is a test
        # error. The draft still waits for File ticket: its row opens and File ticket is the next press. Once a
        # correction makes that run a test error, File ticket would be refused (serve.run_fileable): neither holds
        old = "R-20261101-TA5-TERR-021-00"
        rec = self.fx.run(CAMP_A, old, "TERR-021", "FAIL", "2026-11-01T09:15:00.000Z", "2026-11-01T09:16:00.000Z",
                          [missed(1)], values={"x": 0})
        with open(self.fx.path(f"qa/tasks/{T1}.json"), encoding="utf-8") as f:
            task = json.load(f)
        put(self.root, f"qa/tasks/{T1}.json", dict(task, runs=task["runs"] + [old]))
        self.fx.ticket(f"tests/{CAMP_A}/tickets/1", f"qa/runs/{CAMP_A}/{old}.json")
        out = views.result_cards(data_model.Data(self.root), task=T1)
        c = self.card(out, "TERR-021")
        self.assertEqual((c["verdict"], c["report"]["enabled"], c["open"]), ("not_decided", False, True))
        self.assertEqual(out["test"]["next"], {"press": "file_ticket", "label": "File ticket", "case": "TERR-021"})
        put(self.root, f"qa/runs/{CAMP_A}/{old}c1.json", dict(
            rec, id=old + "c1", created="2026-11-01T09:58:00.000Z", supersedes={"id": old, "why": "false failure"},
            verdict={"v": "ERROR", "decided": "2026-11-01T09:58:00.000Z",
                     "harness": {"component": "case", "detail": "the check was wrong"}}))
        out = views.result_cards(data_model.Data(self.root), task=T1)
        self.assertEqual(([t["state"] for t in self.card(out, "TERR-021")["tickets"]], self.card(out, "TERR-021")["open"]),
                         (["draft"], False))
        self.assertEqual(out["test"]["next"]["press"], "add_result", "not File ticket (a newer plan: not Plan again)")

    def card(self, out, cid):
        return next(c for c in out["cards"] if c["case"] == cid)

    def test_a_plan_title_names_the_test(self):
        with open(self.fx.path(f"qa/tasks/{T1}.json"), encoding="utf-8") as f:
            t = json.load(f)
        put(self.root, f"qa/tasks/{T1}.json", dict(t, title="Custom terrain on Android: 28 m limit, offline"))
        self.assertEqual(self.one(T1)["title"], "Custom terrain on Android: 28 m limit, offline")

    def test_wingmate_off(self):
        # no request claimed and no heartbeat: worker.status is "off", so T20's waiting start says Hover is off
        with open(self.fx.path("qa/runner/state/worker/W-20261103-002.json"), encoding="utf-8") as f:
            req = json.load(f)
        put(self.root, "qa/runner/state/worker/W-20261103-002.json", dict(req, state="failed"))
        out = views.result_cards(data_model.Data(self.root), task=T20)
        self.assertEqual((out["running"]["wingmate"], out["test"]["group"]), ("off", "ongoing"))
        with self.assertRaises(views.NotFound):
            views.result_cards(data_model.Data(self.root), task=T18)  # its start failed before any run


class DebugFixesTests(unittest.TestCase):
    """The fixes of the 2026-11-05 debug pass (tests/2026-11-05-wingmate-debug/FINDINGS.md), server side; a fresh
    workspace each."""

    def setUp(self):
        self.fx = Fixture()
        self.root = self.fx.root

    def tearDown(self):
        self.fx.close()

    def one(self, task=None, case=None, posting=()):
        return views.result_cards(data_model.Data(self.root), task=task, case=case, posting=posting)

    def card(self, out, cid):
        return next(c for c in out["cards"] if c["case"] == cid)

    def edit(self, rel, **change):
        with open(self.fx.path(rel), encoding="utf-8") as f:
            obj = json.load(f)
        obj.update(change)
        put(self.root, rel, obj)
        return obj

    # #1: ?case=X answers X's newest run, a chat run too

    def test_a_case_answers_its_newest_run_a_chat_run_too(self):
        self.assertEqual(self.one(case="OFFS-001")["test"]["task"], T5, "T5's run of OFFS-001 is the newest")
        self.fx.run("2026-11-03-chat", "R-20261103-TA5-OFFS-001-01", "OFFS-001", "FAIL", "2026-11-03T12:00:00.000Z",
                    "2026-11-03T12:01:00.000Z", [ok(1), missed(2)], values={"x": 0})
        out = self.one(case="OFFS-001")
        self.assertEqual((out["test"]["task"], out["test"]["result"]["sentence"]),
                         (None, "Failed (suspected) in 1 of 1 case, on TA5"), "a newer chat FAIL never hides behind "
                                                                             "an older pass")
        self.assertEqual(self.one(task=T5, case="OFFS-001")["test"]["task"], T5, "with task: that test")
        # a chat run older than the test's run: the test
        self.fx.run("2026-11-03-chat", "R-20261103-TA5-OFFS-001-01", "OFFS-001", "FAIL", "2026-11-02T07:00:00.000Z",
                    "2026-11-02T07:01:00.000Z", [ok(1), missed(2)], values={"x": 0})
        self.assertEqual(self.one(case="OFFS-001")["test"]["task"], T5)

    def test_a_chat_case_shows_the_group_of_its_newest_run(self):
        # an older chat run corrected later must not stand for a newer one (its card's "when" is the correction's)
        later = "R-20261022-TA5-TERR-030-01"
        self.fx.run("2026-10-22-chat", later, "TERR-030", "PASS", "2026-10-22T09:00:00.000Z",
                    "2026-10-22T09:01:00.000Z", [ok(1), ok(2), ok(3)])
        base = self.edit(f"qa/runs/{CHAT}/{RX2}.json")
        put(self.root, f"qa/runs/{CHAT}/{RX2}c1.json", dict(
            base, id=RX2 + "c1", created="2026-10-25T09:00:00.000Z", supersedes={"id": RX2, "why": "false failure"},
            verdict={"v": "ERROR", "decided": "2026-10-25T09:00:00.000Z",
                     "harness": {"component": "case", "detail": "the check was wrong"}}))
        out = self.one(case="TERR-030")
        self.assertEqual(out["cards"][0]["run_ids"], [later])

    # #3: each ticket says whether File ticket can send it

    def test_tickets_say_whether_file_ticket_can_send_them(self):
        (t9,) = self.card(self.one(task=T9), "PLAN-002")["tickets"]
        self.assertEqual((t9["dir"], t9["fileable"], t9["fileable_why"]), (TICKET_T9, True, None))
        base = self.edit(f"qa/runs/{CAMP_B}/{RB4}.json")
        put(self.root, f"qa/runs/{CAMP_B}/{RB4}c1.json", dict(
            base, id=RB4 + "c1", created="2026-10-30T12:00:00.000Z", supersedes={"id": RB4, "why": "false failure"},
            verdict={"v": "ERROR", "decided": "2026-10-30T12:00:00.000Z",
                     "harness": {"component": "case", "detail": "the check was wrong"}}))
        out = self.one(task=T9)
        (t9,) = self.card(out, "PLAN-002")["tickets"]
        self.assertEqual((t9["state"], t9["fileable"], t9["fileable_why"]),
                         ("draft", False, "Not filed: its run was corrected to a test error, so there is nothing to "
                                          "file."))
        self.assertNotEqual((out["test"]["next"] or {}).get("press"), "file_ticket")
        (chat,) = self.card(self.one(case="TERR-030"), "TERR-030")["tickets"]
        self.assertEqual((chat["fileable"], chat["fileable_why"]), (True, None), "a chat card's ticket too")

    # #8: a run on an earlier engine account is not reported or filed

    def test_runs_on_an_earlier_engine_account_are_not_reported(self):
        self.fx.run(CAMP_B, RB1, "PLAN-001", "FAIL", "2026-10-30T09:05:00.000Z", "2026-10-30T09:06:00.000Z",
                    [ok(1), ok(2), missed(3)], values={"x": 0}, account="shared-stag")
        out = self.one(task=T6)
        c = self.card(out, "PLAN-001")
        self.assertEqual(c["report"], {"enabled": False,
                                       "why": "ran on the earlier engine account shared-stag; not filed"})
        self.assertTrue(c["open"], "a failed device still opens its row")
        self.assertEqual(out["test"]["next"]["press"], "add_result", "not Report issue")
        tested = {r["task_id"]: r for r in views.tests(data_model.Data(self.root))["tested"]["rows"]}
        self.assertEqual((tested[T6]["unreported"], tested[T6]["next"]["press"]), (0, "add_result"))
        # a drafted ticket of such a run says why File ticket does not send it
        self.fx.ticket(f"tests/{CAMP_B}/tickets/2", f"qa/runs/{CAMP_B}/{RB1}.json")
        (t,) = self.card(self.one(task=T6), "PLAN-001")["tickets"]
        self.assertEqual((t["fileable"], t["fileable_why"]),
                         (False, "Not filed: its run ran on the earlier engine account shared-stag."))
        self.assertEqual(views.file_rule({"account": {"v": "qa-stag", "st": "verified"}}, "TA3", "qa-stag"),
                         ("target", "TA3"))
        self.assertEqual(views.file_rule({"account": {"v": "qa-stag", "st": "observed"}}, "TA5", "qa-stag"),
                         ("account", None))
        self.assertIsNone(views.file_rule({"account": {"v": "qa-stag", "st": "verified"}}, "W044", "qa-stag"))

    # #21: a newer plan of the same ClickUp task

    def test_a_newer_plan_of_the_test(self):
        # T18's plan (W-20261103-001, CU_A) came after T1's and expired: Plan again stays, secondary
        t = self.one(task=T1)["test"]
        self.assertEqual((t["plan_again"]["newer"], t["next"]["press"]),
                         ({"id": "W-20261103-001", "fresh": False}, "add_result"))
        # a plan made a minute ago: fresh, so the page links it instead of Plan again
        now = datetime.datetime.now(datetime.timezone.utc)
        iso = (now - datetime.timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        rid = f"W-{now:%Y%m%d}-901"
        self.fx.request(rid, "check", "done", iso, CU_A, intake=True, guidance=GUIDANCE, targets=["TA5"],
                        result={"checked_at": iso, "needs": []})
        self.assertEqual(self.one(task=T1)["test"]["plan_again"]["newer"], {"id": rid, "fresh": True})
        self.fx.request(rid, "check", "claimed", iso, CU_A, intake=True, guidance=GUIDANCE, targets=["TA5"])
        self.assertEqual(self.one(task=T1)["test"]["plan_again"]["newer"], {"id": rid, "fresh": True}, "being made")
        # a cancelled or failed one does not count; with no newer plan Plan again is the next press
        self.fx.request(rid, "check", "cancelled", iso, CU_A, intake=True)
        self.edit("qa/runner/state/worker/W-20261103-001.json", state="failed")
        t = self.one(task=T1)["test"]
        self.assertEqual((t["plan_again"]["newer"], t["next"]["press"]), (None, "plan_again"))

    # #23: a done note written before a correction says so

    def test_a_done_note_before_a_correction(self):
        self.assertTrue(self.one(task=T1)["test"]["done_note"].endswith("tap."), "RA4c1 came before the note")
        base = self.edit(f"qa/runs/{CAMP_A}/{RA3}.json")
        put(self.root, f"qa/runs/{CAMP_A}/{RA3}c1.json", dict(
            base, id=RA3 + "c1", created="2026-11-02T08:30:00.000Z", supersedes={"id": RA3, "why": "false failure"},
            verdict={"v": "ERROR", "decided": "2026-11-02T08:30:00.000Z",
                     "harness": {"component": "case", "detail": "the wait was too short"}}))
        note = self.one(task=T1)["test"]["done_note"]
        self.assertEqual(note, '2 of 3 devices; TA5 and ET65: test error on both cases. Fix the "Import file" tap. '
                               "(written before the correction of 2 Nov)")
        self.assertLessEqual(len(note), views.NOTE_MAX)

    # #25: a device that does not run a case

    def test_a_device_that_does_not_run_a_case_gets_no_live_state(self):
        self.edit("qa/runner/state/worker/W-20261103-002.json", cases=[
            {"engine_cases": ["TERR-022"], "targets": ["TA5", "ET65"], "text": "in parts"},
            {"engine_cases": ["TERR-021"], "targets": ["ET65"], "text": "Spain"}])
        rows = {r["case"]: r["devices"] for r in self.one(task=T18)["running"]["case_rows"]}
        self.assertEqual(rows, {"TERR-022": {"TA5": "running", "ET65": "done"},
                                "TERR-021": {"TA5": None, "ET65": "waiting"}})

    # #31 and #16: open work on the results

    def test_open_write_and_an_expired_file_approval(self):
        t = self.one(task=T5)["test"]
        self.assertEqual((t["open_write"], t["can_mark_done"]), (False, True))
        writes.Writer(self.root).request_comment(T5)
        t = self.one(task=T5)["test"]
        self.assertEqual((t["open_write"], t["can_mark_done"]), (True, False))
        # a File ticket approval that expired before Hover took it is no work: Mark done is free
        old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=31)).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z")
        self.fx.request("W-20261030-901", "file", "new", old, task=T9, ticket_dir=TICKET_T9, target="W044",
                        approval_id="A-20261030120000-abcdef", run=RB4)
        t = self.one(task=T9)["test"]
        self.assertEqual((t["can_mark_done"], t["mark_done_why"]), (True, None))
        self.assertEqual([x["state"] for x in self.card(self.one(task=T9), "PLAN-002")["tickets"]], ["failed"],
                         "the ticket itself says the approval expired")

    # #13: GET /api/worker names a started test as Results and History do

    def test_worker_titles_a_started_test_by_its_test_title(self):
        self.edit("qa/runner/state/worker/W-20261103-004.json", title=None)
        s = Server(self.root)
        try:
            status, body = s.get("/api/worker")
        finally:
            s.close()
        self.assertEqual(status, 200)
        titles = {r["id"]: r["title"] for r in body["requests"]}
        want = views.test_title(views._Ctx(data_model.Data(self.root)), self.edit(f"qa/tasks/{T20}.json"),
                                views.TITLE_LIST)
        self.assertEqual((titles["W-20261103-004"], want), ("Offline sites: tablet-named QA_ site",) * 2,
                         "one case, no plan title: the case title, as on Results (not the description)")
        self.assertEqual(views.request_title(self.edit("qa/runner/state/worker/W-20261103-004.json")),
                         "Offline sites again", "the request's own title rule, which GET /api/worker used before")


# ---- History's 'Your tests' ----------------------------------------------------------------------------------------

class GroupTests(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.out = views.tests(cls.data)

    def rows(self, group):
        return {r["task_id"]: r for r in self.out[group]["rows"]}

    def test_the_groups(self):
        self.assertEqual((self.out["shown"], self.out["rows_max"]), (5, 100))
        self.assertEqual([r["task_id"] for r in self.out["ongoing"]["rows"]], [T20, T19, T18])
        self.assertEqual([r["task_id"] for r in self.out["tested"]["rows"]], [T23, T5, T1, T13, T10, T9, T7, T6],
                         "newest change first")
        self.assertEqual([r["task_id"] for r in self.out["done"]["rows"]], [T4, T3, T12, T11, T8])
        self.assertEqual({g: self.out[g]["count"] for g in ("ongoing", "tested", "done")},
                         {"ongoing": 3, "tested": 8, "done": 5})
        self.assertEqual(self.out["tested"]["more_unreported"], 2, "T9 and T6 after the first five")
        every = [r["task_id"] for g in ("ongoing", "tested", "done") for r in self.out[g]["rows"]]
        for left_out in (T21, T22):
            self.assertNotIn(left_out, every, "a zombie queued task is no test")
        self.assertEqual(len(every), len(set(every)), "every test is in one group")

    def test_the_done_rule(self):
        done = self.rows("done")
        self.assertEqual(done[T3]["done_how"], {"how": "replaced", "at": "2026-11-02T09:20:00.000Z", "by_task": T5,
                                                "url": None})
        self.assertEqual(done[T4]["done_how"]["by_task"], T5)
        self.assertEqual(done[T8]["done_how"], {"how": "failed_again", "at": "2026-10-30T10:15:00.000Z",
                                                "by_task": T9, "url": None},
                         "the newest test that failed again is named, not the later pass")
        self.assertEqual(done[T11]["done_how"], {"how": "posted", "at": "2026-10-31T09:45:00.000Z", "by_task": None,
                                                 "url": f"https://app.clickup.com/t/{CU_D}"})
        self.assertEqual(done[T12]["done_how"], {"how": "marked", "at": "2026-10-31T10:30:00.000Z", "by_task": None,
                                                 "url": None})
        tested = self.rows("tested")
        # the failure guard: T6 failed PLAN-001 and T7 passed it, so T6 stays; T9 failed PLAN-002 and T10 passed it
        self.assertEqual((tested[T6]["done_how"], tested[T6]["unreported"]), (None, 1))
        self.assertIn(T9, tested)
        self.assertNotIn(T13, done, "T13 ran UPG-001 only: T12's UPG-002 is not covered (and T12 is marked)")

    def test_the_rows(self):
        tested, done = self.rows("tested"), self.rows("done")
        r = tested[T1]
        self.assertEqual(set(r), {"task_id", "title", "clickup", "devices", "created", "last_change", "verdict",
                                  "verdict_label", "results", "next", "stopped_early", "unreported", "done_how",
                                  "running"})
        self.assertEqual({k: r[k] for k in ("title", "devices", "created", "last_change", "verdict", "verdict_label",
                                            "results", "stopped_early", "unreported", "running")},
                         {"title": LIST_TITLE, "devices": ["TA5", "ET65"], "created": "2026-11-01T09:00:00.000Z",
                          "last_change": "2026-11-01T10:00:00.500Z", "verdict": "not_decided",
                          "verdict_label": "Test error", "results": "2 test errors", "stopped_early": False,
                          "unreported": 0, "running": None})
        self.assertEqual(r["clickup"]["title"], NAMES[CU_A])
        nexts = {t: (x["next"] or {}).get("press") for t, x in tested.items()}
        self.assertEqual(nexts, {T23: None, T5: "add_result", T1: "add_result", T13: "add_result", T10: "add_result",
                                 T9: "file_ticket", T7: "post_comment", T6: "report_issue"},
                         "T1: a newer plan of CU_A exists (T18's), so Plan again is not the next press")
        self.assertEqual((tested[T9]["next"]["case"], tested[T6]["next"]["case"]), ("PLAN-002", "PLAN-001"))
        self.assertEqual((tested[T9]["verdict"], tested[T9]["results"], tested[T6]["results"]),
                         ("failed", "1 failed", "1 failed (suspected)"))
        self.assertEqual((tested[T23]["stopped_early"], tested[T23]["last_change"]), (True, "2026-11-02T10:30:00.000Z"))
        self.assertEqual(tested[T13]["title"], NAMES[CU_D])
        self.assertTrue(all(x["next"] is None and x["done_how"] for x in done.values()))
        ongoing = self.rows("ongoing")[T18]
        self.assertEqual((ongoing["verdict"], ongoing["results"], ongoing["next"], ongoing["running"]["wingmate"]),
                         (None, None, None, "working"))

    def test_runs_without_a_task_are_no_test(self):
        every = [r["task_id"] for g in ("ongoing", "tested", "done") for r in self.out[g]["rows"]]
        self.assertTrue(all(t.startswith("T-") for t in every))
        ctx = views._Ctx(self.data)
        self.assertTrue(any(e["task"] is None for e in ctx.entries), "the fixture has chat runs")


# ---- card_entries, the answer without parameters, the words --------------------------------------------------------

class UnchangedAnswerTests(Base):
    def test_card_entries_by_task(self):
        old = views.card_entries(self.data, T8, "PLAN-002")
        self.assertEqual([e["id"] for e in old], [RB3], "a test whose card the newest cards merged away")
        self.assertEqual([e["eff"] for e in views.card_entries(self.data, T1, "TERR-021")], [RA4 + "c1", RA3],
                         "newest first")
        self.assertIsNone(views.card_entries(self.data, T1, "PLAN-002"))
        self.assertIsNone(views.card_entries(self.data, T21, "UPG-001"))

    def test_the_answer_without_parameters_keeps_its_shape(self):
        out = views.result_cards(self.data)
        self.assertEqual(set(out), {"cards", "older", "running"})
        cards = out["cards"] + out["older"]
        merged = next(c for c in cards if c["task"] == T9)
        self.assertEqual((merged["tasks"], merged["runs"]), ([T9, T8], 2), "merged as before")
        t1 = next(c for c in cards if c["key"] == f"{T1}|TERR-021")
        self.assertEqual(t1["title"], NAMES[CU_A], "ClickUp name first, as before")
        self.assertEqual(t1["verdict_label"], "Test error")
        for c in cards:
            for d in c["devices"]:
                self.assertEqual(set(d), {"tag", "verdict", "verdict_label", "failed_step"})
            self.assertNotIn("rows", c)
            self.assertFalse([k for k in c if k.startswith("_")])
        self.assertEqual([r["task"] for r in out["running"]], [T20, T19, T18])
        for r in out["running"]:
            self.assertEqual(set(r), {"task", "title", "cases", "started", "devices", "state", "request", "note"})
            for d in r["devices"]:
                self.assertEqual(set(d), {"tag", "state", "step", "of", "text", "shot_url", "updated", "case", "run",
                                          "verdict"})
        self.assertEqual([r["title"] for r in out["running"]],
                         ["Offline sites on TA5", "Optimize flight on an L-shaped area (web)", "Custom terrain offline"],
                         "running rows use the title rule")

    def test_test_error_words(self):
        self.assertEqual((views.LABELS["not_decided"], views.WORDS["not_decided"]), ("Test error", "test error"))
        self.assertEqual(views._quotes("TA5: ERROR on 2 cases, ERRORS in both, PASS, FAIL; TimeoutError stays"),
                         "TA5: test error on 2 cases, test errors in both, pass, failure; TimeoutError stays")
        self.assertEqual(views._count_word(1, "not_decided"), "1 test error")
        self.assertEqual(views._count_word(5, "not_decided"), "5 test errors")
        self.assertEqual(views._count_word(2, "failed_suspected"), "2 failed (suspected)")
        self.assertEqual(views.REPORT_WHY["not_decided"], "Nothing failed in the app: the test itself stopped, which "
                                                          "says nothing about the app.")


# ---- routes ----------------------------------------------------------------------------------------------------------

class Server:
    """serve.build_server(root, 0) in a thread; request logs are dropped."""

    def __init__(self, root):
        self._log = serve.Handler.log_message
        serve.Handler.log_message = lambda self, fmt, *args: None
        self.srv = serve.build_server(root, 0)
        self.port = self.srv.qa_port
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def get(self, path, method="GET"):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        try:
            c.request(method, path, headers={"Host": f"127.0.0.1:{self.port}"})
            r = c.getresponse()
            body = r.read()
            return r.status, json.loads(body) if body else None
        finally:
            c.close()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()
        serve.Handler.log_message = self._log


def snapshot(root):
    out = {}
    for dirpath, _, files in os.walk(root):
        for f in files:
            st = os.stat(os.path.join(dirpath, f))
            out[os.path.join(dirpath, f)] = (st.st_size, st.st_mtime_ns)
    return out


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()
        self.root = self.fx.root
        self.s = Server(self.root)

    def tearDown(self):
        self.s.close()
        self.fx.close()

    def test_one_test_routes(self):
        before = snapshot(self.root)
        status, body = self.s.get("/api/results/cards?task=current")
        self.assertEqual((status, set(body), body["test"]["task"]), (200, {"test", "cards", "running"}, T20))
        status, body = self.s.get(f"/api/results/cards?task={T1}")
        self.assertEqual((status, body["test"]["title"], len(body["cards"])), (200, HEADER_TITLE, 2))
        status, body = self.s.get("/api/results/cards?case=TERR-021")
        self.assertEqual((status, body["test"]["task"]), (200, T1))
        status, body = self.s.get(f"/api/results/cards?task={T1}&case=TERR-022")
        self.assertTrue(next(c for c in body["cards"] if c["case"] == "TERR-022")["open"])
        self.assertEqual(self.s.get("/api/results/cards?case=TERR-030")[1]["test"]["task"], None, "a chat run")
        self.assertEqual(self.s.get(f"/api/results/cards?task={T21}"),
                         (404, {"error": f"no test {T21}: it has no runs and is not running"}))
        self.assertEqual(self.s.get("/api/results/cards?case=NONE-001"), (404, {"error": "no run of NONE-001"}))
        for bad, name in (("task=T-1", "task"), ("task=Current", "task"), ("task=current&case=terr", "case"),
                          ("case=x%20y", "case")):
            self.assertEqual(self.s.get("/api/results/cards?" + bad), (400, {"error": f"bad {name} parameter"}), bad)
        status, body = self.s.get("/api/results/cards?task=&case=")
        self.assertEqual((status, set(body)), (200, {"cards", "older", "running"}), "empty parameters: as before")
        self.assertEqual(self.s.get("/api/results/cards?task=current", method="HEAD"), (200, None))
        self.assertEqual(snapshot(self.root), before, "nothing is written")

    def test_tests_route(self):
        status, body = self.s.get("/api/results/tests")
        self.assertEqual((status, set(body)), (200, {"ongoing", "tested", "done", "shown", "rows_max"}))
        self.assertEqual((body["tested"]["count"], body["done"]["count"], body["ongoing"]["count"]), (8, 5, 3))
        self.assertEqual(self.s.get("/api/results/tests?x=1")[1], body, "a query string is ignored")
        self.assertEqual(self.s.get("/api/results/tests", method="HEAD"), (200, None))
        routes = {rx.pattern: m for rx, m in serve.ROUTES}
        self.assertEqual(routes[r"/api/results/tests"], {"GET": "api_result_tests"})

    def test_worker_titles(self):
        day = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d")
        now = records.now_utc()
        self.fx.request(f"W-{day}-001", "check", "done", now, CU_A, intake=True, guidance=GUIDANCE,
                        targets=["TA5"], result={"title": "Custom terrain on Android"})
        self.fx.request(f"W-{day}-002", "check", "failed", now, CU_C, intake=True, guidance=GUIDANCE, targets=["TA5"])
        self.fx.request(f"W-{day}-003", "write", "done", now, CU_D, task=T11, outputs=["comment"])
        status, body = self.s.get("/api/worker")
        self.assertEqual(status, 200)
        titles = {r["id"]: r["title"] for r in body["requests"]}
        self.assertEqual(titles[f"W-{day}-001"], "Custom terrain on Android")
        self.assertEqual(titles[f"W-{day}-002"], LIST_TITLE)
        self.assertIsNone(titles[f"W-{day}-003"])
        self.assertEqual(titles["W-20261103-004"], "Offline sites on TA5", "an open start request")
        self.assertTrue(all("title" in r for r in body["requests"]))


# ---- the real workspace (read only) -------------------------------------------------------------------------------

class RealWorkspaceTests(unittest.TestCase):
    """On the real records the groups add up and every row reads as its group says (exact values: API.md 8.3)."""

    def test_groups_add_up(self):
        d = data_model.Data(REAL_ROOT)
        out = views.tests(d)
        ctx = views._Ctx(d)
        tests = {tid for tid, t in ctx.tasks.items() if views._ongoing(ctx, t)[0] or ctx.by_task.get(tid)}
        rows = [r for g in ("ongoing", "tested", "done") for r in out[g]["rows"]]
        self.assertEqual(sum(out[g]["count"] for g in ("ongoing", "tested", "done")), len(tests))
        self.assertEqual({r["task_id"] for r in rows}, tests)
        for r in out["ongoing"]["rows"]:
            self.assertTrue(r["running"] and r["verdict"] is None and r["next"] is None, r["task_id"])
        for r in out["tested"]["rows"]:
            self.assertTrue(r["verdict"] and r["done_how"] is None and r["running"] is None, r["task_id"])
        for r in out["done"]["rows"]:
            self.assertTrue(r["verdict"] and r["done_how"] and r["next"] is None, r["task_id"])
        cur = views.result_cards(d, task="current")
        self.assertEqual(set(cur), {"test", "cards", "running"})
        if cur["test"]:
            self.assertIn(cur["test"]["task"], tests)


if __name__ == "__main__":
    unittest.main()
