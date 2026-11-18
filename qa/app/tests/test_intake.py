"""Tests for New test (qa/app/DESIGN.md "Simple flow (2026-10-29)"): /api/intake/resolve, the intake check, the
worker's plan (plan_cases), the Start after it and test files for a task read from its link.

python3 -I qa/app/tests/test_intake.py  (no network, no devices: a temporary workspace and a fake ClickUp)
"""
import io
import json
import os
import subprocess
import sys
import threading
import unittest
import urllib.error

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(TESTS_DIR)
sys.path[:0] = [TESTS_DIR, APP_DIR, os.path.join(os.path.dirname(APP_DIR), "runner")]
import case_schema  # noqa: E402
import clickup_read  # noqa: E402
import plan  # noqa: E402
import run  # noqa: E402
import serve  # noqa: E402
import test_uploads  # noqa: E402
import test_worker  # noqa: E402
import worker  # noqa: E402
import writes  # noqa: E402
from test_plan import MD_ORDERED, REAL_URLOPEN, TOKEN, FakeResponse, task  # noqa: E402
from test_worker import TID, put  # noqa: E402

INTAKE_TID, INTAKE_CID = "86abcnew1", "CL-80009"  # a task that is not in today's list
TITLE = "Terrain import on Android"
GUIDANCE = "Import the general DSM only."
SUMMARY = "Test: TERR-004 (existing) on TA5.\nYour description: the DSM import only.\nChanges: nothing."
TEXT = "Imports the general DSM and checks the terrain"
LATER = "2099-01-01T00:00:00.000Z"  # a case version written after the request
ABSENT = object()  # a field the saved copy of a task does not hold
HANDS_OFF = {"kind": "case", "status": "active", "targets": "tablet", "created": "2026-10-20T15:40:00.000Z",
             "steps": [{"n": 1, "text": "read", "gates": [], "read_js": "1"}]}


def written(cid, created=LATER):
    """HANDS_OFF as a complete case record, the way the worker writes one while it plans: a case written or rewritten
    for a plan must pass case_schema.validate_case there (2026-11-05), with its parts whatever its requirement source
    says (wingmate-debug FINDINGS #6: this one keeps a ClickUp source), each part quoting the description."""
    return dict(HANDS_OFF, kind="case", schema=1, id=cid, created=created, author="claude", supersedes=None,
                title=f"{cid} reads the page", area=cid.split("-")[0],
                requirement={"text": GUIDANCE, "sha256": case_schema.requirement_sha256(GUIDANCE),
                             "source": {"type": "clickup_task", "ref": INTAKE_TID}},
                parts=[{"n": 1, "text": "Read the page", "quote": "the general DSM", "check": True}],
                steps=[dict(HANDS_OFF["steps"][0], part=1)],
                routing=[], expected_differences=[], preconditions=[], fixtures=[], covered_paths=[],
                needs={"link": "none", "usb_port": False, "account_lock": False, "flags": [], "drone_hw": None})


def site_case(cid, name):
    """A hands-off tablet case that types the site name, turns on offline use and saves the new site (step 3)."""
    return dict(HANDS_OFF, id=cid, test_data={"site_id": run.engine()["test_data"]["site"]["id"]},
                cloud_writes=[{"step": 3, "what": "creates the site", "object": "site"}], steps=[
                    {"n": 1, "text": "name", "gates": [],
                     "helper": f"act.py {{target}} type testid:input-site-name --text '{name}' --expect x"},
                    {"n": 2, "text": "offline use", "gates": [],
                     "helper": "act.py {target} tap 'text:Enable offline use'"},
                    {"n": 3, "text": "save", "gates": ["cloud_write"],
                     "helper": "act.py {target} tap 'text:Save new site' --expect x"}])


def intake_task(**kw):
    """The ClickUp task the link names: two cases, one of them with an e-mail address."""
    return dict(task(INTAKE_TID, TITLE, MD_ORDERED, custom_id=INTAKE_CID, priority="high"),
                list={"id": "901", "name": "CT Test Planner"}, **kw)


class TaskOpener:
    """Answers every GET /task/<id> with one task, or raises a prepared error; records every request."""

    def __init__(self, obj=None, error=None):
        self.obj, self.error, self.requests = obj, error, []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        if self.error is not None:
            raise self.error
        return FakeResponse(json.dumps(self.obj).encode("utf-8"))


class Base(test_worker.Base):
    """The shared Today fixture (devices TA5 and ET65, hands-off TERR-004, TID in today's list) over HTTP, with a
    fake ClickUp for the one-task read and two more cases: TERR-006 hands-off, NEWT-001 a draft."""

    def setUp(self):
        super().setUp()
        put(self.root, "qa/cases/TERR-006.json", dict(HANDS_OFF, id="TERR-006"))
        put(self.root, "qa/cases/NEWT-001.json", {"kind": "case", "id": "NEWT-001", "status": "draft",
                                                 "targets": "tablet", "steps": [{"n": 1, "text": "import", "gates": []}]})
        self.opener = TaskOpener(intake_task())
        clickup_read.urllib.request.urlopen = self.opener
        self.srv = serve.build_server(self.root, 0)
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        clickup_read.urllib.request.urlopen = REAL_URLOPEN
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    _req = test_worker.EndpointTests._req
    put_file = test_uploads.EndpointAndPrepareTests.put

    def resolve(self, url=INTAKE_TID):
        return self._req("POST", "/api/intake/resolve", {"url": url})

    def intake_check(self, **body):
        return self._req("POST", "/api/worker/check", dict({"intake": True, "task_id": INTAKE_TID, "guidance": GUIDANCE,
                                                            "targets": ["TA5"], "prepare": True}, **body))

    def plan_case(self, cid="TERR-004", targets=("TA5",), new=False, **kw):
        [v] = plan.case_versions(self.root, [cid])
        return dict({"id": cid, "file": v["file"], "sha256": v["sha256"], "new": new, "targets": list(targets),
                     "text": TEXT}, **kw)

    def answer_plan(self, rid, tags, plan_cases, **extra):
        worker.claim(self.root, rid)
        rows = [{"tag": t, "ok": True, "connected": True, "account": True, "app_ok": True} for t in tags]
        return worker.answer_check(self.root, rid, dict({"targets": rows, "summary": SUMMARY, "plan_cases": plan_cases},
                                                        **extra))

    def planned(self, tags=("TA5",), plan_cases=None, **extra):
        """A done intake check: the task read, devices tags checked, the worker's plan saved."""
        self.resolve()
        status, chk = self.intake_check(targets=list(tags))
        self.assertEqual(status, 201, chk)
        return self.answer_plan(chk["id"], tags, [self.plan_case()] if plan_cases is None else plan_cases, **extra)

    def task(self, tid):
        with open(os.path.join(self.root, "qa/tasks", tid + ".json")) as f:
            return json.load(f)


class ResolveTests(Base):
    def test_a_link_reads_one_task_and_keeps_a_redacted_copy(self):
        status, got = self.resolve(f"https://app.clickup.com/t/{INTAKE_TID}?comment=90")
        self.assertEqual(status, 200)
        self.assertEqual(sorted(got), sorted(plan.INTAKE_KEYS))
        self.assertEqual((got["task_id"], got["custom_id"], got["title"], got["status"], got["priority"], got["list"],
                          got["url"]), (INTAKE_TID, INTAKE_CID, TITLE, "up next", "high", "CT Test Planner",
                                        f"https://app.clickup.com/t/{INTAKE_TID}"))
        self.assertEqual(got["cases"][0], {"n": 1, "text": "First bug CL-80005"})
        self.assertEqual([c["n"] for c in got["cases"]], [1, 2])
        (req,) = self.opener.requests
        self.assertEqual(req.get_method(), "GET")
        self.assertTrue(req.full_url.startswith(f"https://api.clickup.com/api/v2/task/{INTAKE_TID}?"))
        self.assertNotIn("custom_task_ids", req.full_url)
        with open(os.path.join(self.root, plan.intake_rel(INTAKE_TID))) as f:
            raw = f.read()
        self.assertEqual(json.loads(raw)["cases"], got["cases"])
        self.assertIn("First bug [CL-80005]", json.loads(raw)["description"])  # for the worker, not the page
        self.assertNotIn("description", got)
        for leak in ("@company.example", TOKEN):
            self.assertNotIn(leak, raw)
            self.assertNotIn(leak, json.dumps(got))
        self.assertEqual(plan.load_intake(self.root, INTAKE_TID)["kind"], "intake")

    def test_a_custom_id_is_asked_with_the_team(self):
        for ref in (INTAKE_CID, INTAKE_CID.lower(), f"https://app.clickup.com/t/{plan.TEAM_ID}/{INTAKE_CID}"):
            status, got = self.resolve(ref)
            self.assertEqual((status, got["task_id"]), (200, INTAKE_TID))
        self.assertEqual(len(self.opener.requests), 3)
        for req in self.opener.requests:
            self.assertIn(f"/task/{INTAKE_CID}?", req.full_url)
            self.assertIn("custom_task_ids=true", req.full_url)
            self.assertIn(f"team_id={plan.TEAM_ID}", req.full_url)

    def test_never_read_tasks_are_refused_without_a_request(self):
        for ref in ("CL-80008", "cl-80008", "0000fake005", "https://app.clickup.com/t/0000fake005",
                    f"https://app.clickup.com/t/{plan.TEAM_ID}/CL-80008"):
            self.assertEqual(self.resolve(ref)[0], 400, ref)
        self.assertEqual(self.opener.requests, [])
        # ClickUp answers another id with the never-read task: nothing is kept
        self.opener.obj = intake_task(id="0000fake005", custom_id="CL-80008")
        self.assertEqual(self.resolve()[0], 400)
        self.assertFalse(os.path.exists(os.path.join(self.root, "qa/cache/intake/0000fake005.json")))

    def test_a_link_that_names_no_task(self):
        for ref in ("", "   ", "hello world", "a/b", f"https://example.com/t/{INTAKE_TID}",
                    f"https://app.clickup.com/{plan.TEAM_ID}/v/li/901", 5, None):
            self.assertEqual(self.resolve(ref)[0], 400, ref)
        self.assertEqual(self._req("POST", "/api/intake/resolve", {})[0], 400)
        self.assertEqual(self.opener.requests, [])

    def test_no_token(self):
        secrets = os.path.join(self.root, "qa", "secrets.env")
        os.remove(secrets)  # the temporary workspace's fake token
        status, err = self.resolve()
        self.assertEqual((status, err["error"]), (409, clickup_read.no_token_message(secrets).replace(
            "Then press Refresh.", "Then paste the link again.")))
        self.assertTrue(err["error"].startswith("No ClickUp token found."))
        self.assertEqual(self.opener.requests, [])

    def test_a_clickup_error_is_502_with_its_reason(self):
        self.opener.error = urllib.error.HTTPError("https://api.clickup.com/api/v2/task/x", 404, "Not Found", {},
                                                   io.BytesIO(b'{"err": "Task not found", "ECODE": "ITEM_013"}'))
        self.assertEqual(self.resolve(), (502, {"error": "ClickUp answered HTTP 404, ClickUp says: Task not found "
                                                         "ITEM_013. Check the link, or try again later."}))
        self.opener.error = urllib.error.URLError(OSError("no route"))
        status, err = self.resolve()
        self.assertEqual(status, 502)
        self.assertTrue(err["error"].startswith("No internet connection"))
        self.assertFalse(os.path.exists(os.path.join(self.root, plan.intake_rel(INTAKE_TID))))

    def test_needs_the_page_token(self):
        self.assertEqual(self._req("POST", "/api/intake/resolve", {"url": INTAKE_TID}, token=False)[0], 403)
        self.assertEqual(self.opener.requests, [])

    def test_the_writer_allows_only_the_intake_file(self):
        self.w._write(plan.intake_rel(INTAKE_TID), b"{}\n", "replace")
        for rel in ("qa/cache/intake/../x.json", "qa/cache/intake/a/b.json", "qa/cache/intake/x.txt"):
            with self.assertRaises(writes.WriteError):
                self.w._write(rel, b"{}", "replace")


class IntakeCheckTests(Base):
    def test_needs_the_task_read_from_its_link(self):
        self.assertEqual(self.intake_check(), (409, {"error": "open the test link again"}))

    def test_creates_the_check_request(self):
        _, got = self.resolve()
        status, chk = self.intake_check(targets=["ET65", "TA5"], guidance="  Import the DSM only.\r\nOn both tablets. ")
        self.assertEqual(status, 201)
        self.assertEqual((chk["type"], chk["state"], chk["intake"], chk["prepare"]), ("check", "new", True, True))
        self.assertEqual(chk["clickup"], {"task_id": INTAKE_TID, "custom_id": INTAKE_CID, "title": TITLE,
                                          "url": f"https://app.clickup.com/t/{INTAKE_TID}", "priority": "high",
                                          "status": "up next"})
        self.assertEqual((chk["clickup_cases"], chk["guidance"], chk["targets"]),
                         (got["cases"], "Import the DSM only.\nOn both tablets.", ["TA5", "ET65"]))
        self.assertEqual([chk[k] for k in ("cases", "engine_cases", "fix_commits", "case_versions", "files")],
                         [[], [], [], [], []])
        self.assertEqual(self.intake_check()[0], 409)  # one open check per test

    def test_the_description_is_required(self):
        self.resolve()
        for g in (None, "", "   "):
            self.assertEqual(self.intake_check(guidance=g),
                             (400, {"error": f"guidance: say what to test (1-{writes.GUIDANCE_MAX} characters)"}))
        status, err = self.intake_check(guidance="secret plan " * 200)
        self.assertEqual(status, 400)
        self.assertNotIn("secret plan", err["error"])

    def test_devices_are_some_of_the_set_up_ones(self):
        self.resolve()
        for t in (None, [], ["PIXEL"], ["TA5", "TA5"], ["TA3"], "TA5"):
            self.assertEqual(self.intake_check(targets=t), (400, {"error": "targets: some of ET65, TA5, W044"}), t)
        status, chk = self.intake_check(targets=["W044", "TA5"])
        self.assertEqual((status, chk["targets"]), (201, ["TA5", "W044"]))

    def test_bad_and_never_read_task_ids(self):
        for tid in ("0000fake005", "../x", None):
            self.assertEqual(self.intake_check(task_id=tid)[0], 400, tid)

    def test_the_check_lists_the_attached_files(self):
        self.resolve()
        self.assertEqual(self.put_file(INTAKE_TID, "dsm.tif", b"abc", 0, 3)[0], 200)
        _, chk = self.intake_check()
        self.assertEqual([(f["name"], f["size"], f["path"]) for f in chk["files"]],
                         [("dsm.tif", 3, f"tests/test-files-{INTAKE_TID}/files/dsm.tif")])
        self.assertEqual(len(chk["files"][0]["sha256"]), 64)

    def test_the_old_today_body_is_unchanged(self):
        self.resolve()
        status, chk = self._req("POST", "/api/worker/check", {"task_id": TID, "targets": ["TA5"]})
        self.assertEqual(status, 201)
        self.assertNotIn("intake", chk)
        self.assertEqual((chk["day"], chk["engine_cases"], chk["targets"]), (self.day, ["TERR-004"], ["TA5"]))
        self.assertEqual([v["id"] for v in chk["case_versions"]], ["TERR-004"])
        # without "intake": true a read task is not a Today test
        self.assertEqual(self._req("POST", "/api/worker/check", {"task_id": INTAKE_TID, "intake": "yes"})[0], 409)


class ClickUpTextTests(Base):
    """2026-11-10: a New test check request carries the task's ClickUp text (clickup_description), so the worker plans
    from the task's own words. Rounds 1 and 2 of ClickUp test 0000fake009 were BLOCKED because the worker planned
    without opening the saved copy, whose "Test setup" line asks for the flags off."""

    SETUP = "Test setup: Have the flags OfflineSync and OfflineSItes turned of"

    def save(self, **fields):
        """The saved copy of the task with these fields changed, as a hand edit would leave it; a field given as
        ABSENT is left out (a copy saved without it)."""
        rec = dict(plan.load_intake(self.root, INTAKE_TID), **fields)
        put(self.root, plan.intake_rel(INTAKE_TID), {k: v for k, v in rec.items() if v is not ABSENT})

    def fields(self):
        return serve.intake_fields(self.root, INTAKE_TID, {"task_id": INTAKE_TID}, [], GUIDANCE, ["TA5"])

    def test_the_request_holds_the_saved_text_and_nothing_drops_it(self):
        self.opener.obj = intake_task(markdown_description=f"{self.SETUP}\n\n{MD_ORDERED}")
        self.resolve()
        status, chk = self.intake_check()
        self.assertEqual(status, 201, chk)
        text = chk["clickup_description"]
        self.assertEqual(text, plan.load_intake(self.root, INTAKE_TID)["description"])
        self.assertTrue(text.startswith(self.SETUP + "\n\n1.  First bug [CL-80005]"), text)
        self.assertIn("contact [EMAIL]", text)  # redacted, as the saved copy is
        self.assertNotIn("@company.example", json.dumps(chk))
        # the stored request, the page's read and what the waiting worker takes all hold it
        self.assertEqual(worker.load_request(self.root, chk["id"])["clickup_description"], text)
        _, view = self._req("GET", "/api/worker")
        [got] = [r for r in view["requests"] if r["id"] == chk["id"]]
        self.assertEqual(got["clickup_description"], text)
        out = io.StringIO()
        self.assertEqual(worker.wait(self.root, "23:59", out=out, sleep=lambda s: None), 0)
        self.assertEqual(json.loads(out.getvalue())["clickup_description"], text)
        # the worker's answer leaves it on the request, and Run starts from that plan as before
        done = self.answer_plan(chk["id"], ["TA5"], [self.plan_case()])
        self.assertEqual((done["state"], done["clickup_description"]), ("done", text))
        status, start = self._req("POST", "/api/worker/start", {"check": done["id"], "targets": ["TA5"]})
        self.assertEqual((status, start["engine_cases"]), (201, ["TERR-004"]), start)

    def test_no_saved_text_is_an_empty_string(self):
        self.resolve()
        for value in (ABSENT, None, 5, ["Test setup"], "", "  \n "):
            self.save(description=value)
            self.assertEqual(self.fields()["clickup_description"], "", value)
        for tid in ("86abcnone1", "0000fake005", "../x", "", None, 5):  # no saved copy, a never-read task, no task id
            self.assertEqual(worker.clickup_description(self.root, tid), "", tid)

    def test_a_long_text_is_cut_and_a_copy_changed_by_hand_is_redacted_again(self):
        self.resolve()
        self.save(description="Step one.\n" + "x" * 13000)
        text = self.fields()["clickup_description"]
        self.assertEqual((len(text), text[:14], text[-2:]), (worker.CLICKUP_TEXT_MAX, "Step one.\nxxxx", "x…"))
        self.save(description="y" * worker.CLICKUP_TEXT_MAX)  # the limit itself passes whole
        self.assertEqual(self.fields()["clickup_description"], "y" * worker.CLICKUP_TEXT_MAX)
        self.save(description="  Ask ann.smith@company.example\x00 first,\npassword=hunter2secret then.\n ")
        _, chk = self.intake_check()
        self.assertEqual(chk["clickup_description"], "Ask [EMAIL] first,\npassword=[REDACTED] then.")


class AnswerTests(Base):
    def test_a_good_plan(self):
        self.resolve()
        self.put_file(INTAKE_TID, "dsm.tif", b"abc", 0, 3)
        _, chk = self.intake_check(targets=["TA5", "ET65"])
        done = self.answer_plan(chk["id"], ["TA5", "ET65"],
                                [self.plan_case("TERR-004", ["ET65", "TA5"]), self.plan_case("TERR-006", ["ET65"], True)],
                                plan_files=[{"name": "dsm.tif", "case": "TERR-004"}])
        res = done["result"]
        self.assertEqual((done["state"], res["summary"], res["complete"]), ("done", SUMMARY, []))
        # both cases are older than the request: the worker's "new" for TERR-006 is not used
        self.assertEqual([(c["id"], c["new"], c["changed_in_plan"], c["targets"], c["text"])
                          for c in res["plan_cases"]],
                         [("TERR-004", False, False, ["TA5", "ET65"], TEXT),
                          ("TERR-006", False, False, ["ET65"], TEXT)])
        self.assertEqual(res["plan_cases"][0]["file"], "qa/cases/TERR-004.json")
        self.assertEqual(res["plan_files"], [{"name": "dsm.tif", "case": "TERR-004"}])

    def test_a_case_that_saves_a_site_it_names_qa_passes_the_write_guard(self):
        # the offline-sites decision (2026-11-03): the guard reads the whole case (the QA_ name typed before the save)
        put(self.root, "qa/cases/SITE-002.json", site_case("SITE-002", "QA_SITE-002_{run}"))
        self.resolve()
        _, chk = self.intake_check()
        done = self.answer_plan(chk["id"], ["TA5"], [self.plan_case("SITE-002")])
        self.assertEqual([c["id"] for c in done["result"]["plan_cases"]], ["SITE-002"])

    def test_an_empty_plan_is_allowed_but_not_without_a_summary(self):
        self.resolve()
        _, chk = self.intake_check()
        worker.claim(self.root, chk["id"])
        rows = [{"tag": "TA5", "ok": True}]
        with self.assertRaisesRegex(worker.WorkerError, "summary"):
            worker.answer_check(self.root, chk["id"], {"targets": rows, "plan_cases": []})
        done = worker.answer_check(self.root, chk["id"], {"targets": rows, "plan_cases": [],
                                                          "summary": "Not tested in this run: no case fits yet."})
        self.assertEqual((done["state"], done["result"]["plan_cases"]), ("done", []))

    def test_new_and_changed_are_decided_by_the_case_files(self):
        self.resolve()
        _, chk = self.intake_check()
        # written while the plan was made (after the request), each a complete record (checked at plan time): a new
        # case (MAP-001), a new version of TERR-006, a case known only from versions whose lowest one is old (SYNC-001)
        put(self.root, "qa/cases/MAP-001.json", written("MAP-001"))
        put(self.root, "qa/cases/TERR-006.c1.json", written("TERR-006"))
        put(self.root, "qa/cases/SYNC-001.c2.json", dict(HANDS_OFF, id="SYNC-001"))
        put(self.root, "qa/cases/SYNC-001.c3.json", written("SYNC-001"))
        done = self.answer_plan(chk["id"], ["TA5"], [self.plan_case("TERR-004", new=True)] + [
            self.plan_case(cid) for cid in ("MAP-001", "TERR-006", "SYNC-001")])
        self.assertEqual([(c["id"], c["new"], c["changed_in_plan"]) for c in done["result"]["plan_cases"]],
                         [("TERR-004", False, False), ("MAP-001", True, False), ("TERR-006", False, True),
                          ("SYNC-001", False, True)])

    def test_a_case_without_a_readable_time_counts_as_new(self):
        # the server never shows a case it cannot date as an old one, so the plan checks it as written now, and its
        # missing time is what validate_case refuses
        self.resolve()
        _, chk = self.intake_check()
        put(self.root, "qa/cases/LTA-001.json", written("LTA-001", created=None))
        self.assertTrue(worker._created_after(self.root, "qa/cases/LTA-001.json", chk["created"]))
        with self.assertRaisesRegex(worker.WorkerError, r"^LTA-001: created: must be an ISO-8601 UTC time") as e:
            self.answer_plan(chk["id"], ["TA5"], [self.plan_case("LTA-001")])
        self.assertEqual(e.exception.status, 400)
        self.assertEqual(worker.load_request(self.root, chk["id"])["state"], "claimed")

    def test_refusals(self):
        self.resolve()
        self.put_file(INTAKE_TID, "dsm.tif", b"abc", 0, 3)
        old = self.plan_case()
        put(self.root, "qa/cases/TERR-004.c1.json", dict(HANDS_OFF, id="TERR-004"))  # a newer version
        # beside autonomy: a log-in, and a gate other than the standing write_list / cloud_write
        put(self.root, "qa/cases/LOGN-001.json", dict(HANDS_OFF, id="LOGN-001", steps=[
            {"n": 1, "text": "log in", "gates": [], "helper": "act.py {target} type testid:pw --secret CT_PASSWORD"}]))
        for n, gate in enumerate(("account_wide", "install"), 1):
            put(self.root, f"qa/cases/GATE-00{n}.json", dict(HANDS_OFF, id=f"GATE-00{n}", steps=[
                {"n": 1, "text": "read", "gates": ["write_list", gate], "read_js": "1"}]))
        put(self.root, "qa/cases/SITE-001.json", site_case("SITE-001", "Offline site"))  # a new site not named QA_
        _, chk = self.intake_check(targets=["TA5", "W044"])
        worker.claim(self.root, chk["id"])
        good = self.plan_case()
        rows = [{"tag": t, "ok": True} for t in ("TA5", "W044")]
        bad = [
            ({"plan_cases": None}, "plan_cases"),
            ({"plan_cases": "TERR-004"}, "plan_cases"),
            ({"plan_cases": [dict(good, id=f"TERR-{n:03d}") for n in range(11)]}, "at most 10"),
            ({"plan_cases": [dict(good, id="../x")]}, r"plan_cases\[0\]\.id"),
            ({"plan_cases": [dict(good, sha256="0" * 64)]}, "newest version"),
            ({"plan_cases": [old]}, "newest version"),
            ({"plan_cases": [self.plan_case("TERR-099")]}, "none found"),
            ({"plan_cases": [self.plan_case("NEWT-001")]}, "NEWT-001 is not hands-off"),
            ({"plan_cases": [self.plan_case("LOGN-001")]}, r"LOGN-001 step 1: a log-in \(--secret\) never runs"),
            ({"plan_cases": [self.plan_case("GATE-001")]}, "GATE-001 step 1: gate account_wide never runs from New"),
            ({"plan_cases": [self.plan_case("GATE-002")]}, "GATE-002 step 1: gate install never runs from New test"),
            ({"plan_cases": [self.plan_case("SITE-001")]}, "SITE-001 step 3 saves a site through "
                                                            "text:Save new site, which never runs from New test"),
            ({"plan_cases": [dict(good, targets=["ET65"])]}, "targets: some of TA5, W044"),
            ({"plan_cases": [dict(good, targets=[])]}, "targets"),
            ({"plan_cases": [dict(good, targets=["TA5", "TA5"])]}, "targets"),
            ({"plan_cases": [dict(good, targets=["W044"])]}, "a tablet case runs on the tablets only"),
            ({"plan_cases": [good, dict(good, targets=["TA5"])]}, "TERR-004 more than once"),
            ({"plan_cases": [dict(good, new="yes")]}, "new: true or false"),
            ({"plan_cases": [dict(good, text="x" * 201)]}, "text"),
            ({"plan_cases": [good], "plan_files": [{"name": "other.tif", "case": "TERR-004"}]}, "plan_files"),
            ({"plan_cases": [good], "plan_files": [{"name": "dsm.tif", "case": "TERR-006"}]}, "plan_files"),
            ({"plan_cases": [good], "complete": ["TERR-004"]}, "complete"),
        ]
        for change, why in bad:
            with self.assertRaisesRegex(worker.WorkerError, why) as e:
                worker.answer_check(self.root, chk["id"], dict({"targets": rows, "summary": SUMMARY}, **change))
            self.assertEqual(e.exception.status, 400, why)
        self.assertEqual(worker.load_request(self.root, chk["id"])["state"], "claimed")
        done = worker.answer_check(self.root, chk["id"], {"targets": rows, "summary": SUMMARY, "plan_cases": [good]})
        self.assertEqual(done["result"]["plan_cases"][0]["file"], "qa/cases/TERR-004.c1.json")


class NotCoveredTests(Base):
    """The plan's "not_covered" (2026-11-03): the parts of the person's description the plan leaves out."""

    def test_stored_with_the_plan_and_read_by_the_plan_screen(self):
        done = self.planned(not_covered=["Save the flight", "  make sure it is\n  available offline ",
                                         "corridor for ann.smith@company.example"])
        want = ["Save the flight", "make sure it is available offline", "corridor for [EMAIL]"]
        self.assertEqual(done["result"]["not_covered"], want)
        self.assertEqual(worker.load_request(self.root, done["id"])["result"]["not_covered"], want)
        # the Plan screen reads the check from GET /api/worker
        status, view = self._req("GET", "/api/worker")
        self.assertEqual(status, 200)
        [got] = [r for r in view["requests"] if r["id"] == done["id"]]
        self.assertEqual(got["result"]["not_covered"], want)

    def test_absent_or_null_is_an_empty_list(self):
        self.assertEqual(self.planned()["result"]["not_covered"], [])
        self.assertEqual(self.planned(not_covered=None)["result"]["not_covered"], [])

    def test_allowed_on_an_empty_plan(self):
        done = self.planned(plan_cases=[], not_covered=["Draw the flight area on the map"])
        self.assertEqual((done["result"]["plan_cases"], done["result"]["not_covered"]),
                         ([], ["Draw the flight area on the map"]))

    def test_refusals(self):
        self.resolve()
        _, chk = self.intake_check()
        worker.claim(self.root, chk["id"])
        rows = [{"tag": "TA5", "ok": True}]
        bad = [("Save the flight", "not_covered: a list"), ({"x": 1}, "not_covered: a list"),
               ([f"part {n}" for n in range(11)], "at most 10"), ([1], r"not_covered\[0\]"),
               (["ok", ""], r"not_covered\[1\]: text of 1-1000"), (["   "], r"not_covered\[0\]"),
               (["x" * 1001], r"not_covered\[0\]: text of 1-1000"), (["a\x00b"], r"not_covered\[0\]"),
               ([None], r"not_covered\[0\]"), (["Save it", " Save  it"], r"not_covered\[1\]: the same text")]
        for value, why in bad:
            with self.assertRaisesRegex(worker.WorkerError, why) as e:
                worker.answer_check(self.root, chk["id"], {"targets": rows, "summary": SUMMARY,
                                                           "plan_cases": [self.plan_case()], "not_covered": value})
            self.assertEqual(e.exception.status, 400, why)
        self.assertEqual(worker.load_request(self.root, chk["id"])["state"], "claimed")
        ten = [("y" * 118) + f"{n:02d}" for n in range(10)]  # the limits themselves pass
        done = worker.answer_check(self.root, chk["id"], {"targets": rows, "summary": SUMMARY,
                                                          "plan_cases": [self.plan_case()], "not_covered": ten})
        self.assertEqual(done["result"]["not_covered"], ten)

    def test_a_long_task_line_is_cut_not_refused(self):
        # 2026-11-05 (wingmate-debug FINDINGS #2): the 135-character ClickUp line of 0000fake007 did not fit 120, so
        # Hover had to paraphrase it; now it is kept in the task's words and cut on a word with '…'
        line = ("Offline - Download a flight plan offline with custom terrain imported and make sure the custom "
                "terrain is still used when going offline")
        self.assertGreater(len(line), worker.NOT_COVERED_TEXT)
        done = self.planned(not_covered=[line, line[:60] + " and more words that differ after the cut point here, "
                                                            "long enough to be cut as well"])
        got = done["result"]["not_covered"]
        self.assertEqual(got[0], "Offline - Download a flight plan offline with custom terrain imported and make sure "
                                 "the custom terrain is still used…")
        self.assertLessEqual(max(len(x) for x in got), worker.NOT_COVERED_TEXT)
        self.assertEqual(len(got), 2, "two lines that differ stay two")

    def test_only_for_a_test_from_new_test(self):
        chk = self.check(("TA5",))
        worker.claim(self.root, chk["id"])
        rows = [{"tag": "TA5", "ok": True}]
        with self.assertRaisesRegex(worker.WorkerError, "not_covered: only for a test from New test"):
            worker.answer_check(self.root, chk["id"], {"targets": rows, "not_covered": ["Save the flight"]})
        done = worker.answer_check(self.root, chk["id"], {"targets": rows, "not_covered": []})
        self.assertNotIn("not_covered", done["result"])

    def test_the_person_can_still_run(self):
        done = self.planned(not_covered=["Save the flight"])
        status, s = self._req("POST", "/api/worker/start", {"check": done["id"], "targets": ["TA5"]})
        self.assertEqual((status, s["engine_cases"]), (201, ["TERR-004"]), s)


class PlanScreenTests(unittest.TestCase):
    """static/app.js on the Plan screen: the not-covered warning above Run and "Run anyway" (2026-11-03)."""

    def setUp(self):
        with open(os.path.join(APP_DIR, "static", "app.js"), encoding="utf-8") as f:
            self.js = f.read()
        with open(os.path.join(APP_DIR, "static", "app.css"), encoding="utf-8") as f:
            self.css = f.read()

    def check_result(self):
        start = self.js.index("  function checkResult(t, r, wv) {")
        return self.js[start:self.js.index("\n  }\n", start)]

    def test_warning_and_run_anyway(self):
        src = self.check_result()
        self.assertIn("list(res.not_covered)", src)
        # review 2026-11-03: every New test plan lists what the worker sent, an empty plan too; the button of a plan
        # without a case still says "Nothing to run" (emptyPlan comes first)
        self.assertIn("const notCovered = r.intake ?\n", src)
        self.assertNotIn("!emptyPlan", src)
        self.assertIn("const runWord = notCovered.length ? 'Run anyway' : 'Run';", src)
        self.assertIn("emptyPlan ? 'Nothing to run' : !tags.length ? runWord :", src)
        self.assertIn("runWord + ' on ' + tags.join(', ')", src)
        # the box sits right above the buttons, text through text nodes, styled by classes only (CSP)
        box = src.index("'Not covered by this run:'")
        self.assertLess(src.index("emptyPlan ? msgBox('warn'"), box)
        self.assertLess(box, src.index("el('div', {class: 'form-actions'}, start"))
        self.assertNotIn("innerHTML", src)
        self.assertNotIn("style", src)
        for cls in ("msg-head", "msg-lines"):
            self.assertRegex(self.css, r"(?m)^\." + cls + r" \{")

    @unittest.skipUnless(os.path.exists("/usr/bin/osascript"), "needs macOS osascript")
    def test_app_js_parses(self):
        script = ("ObjC.import('Foundation'); var s = $.NSString.stringWithContentsOfFileEncodingError("
                  f"{json.dumps(os.path.join(APP_DIR, 'static', 'app.js'))}, $.NSUTF8StringEncoding, null).js; "
                  "new Function(s); 'ok'")
        out = subprocess.run(["/usr/bin/osascript", "-l", "JavaScript", "-e", script], capture_output=True, text=True,
                             timeout=60)
        self.assertEqual((out.returncode, out.stdout.strip()), (0, "ok"), out.stderr)


class StartTests(Base):
    def start(self, done, targets):
        return self._req("POST", "/api/worker/start", {"check": done["id"], "targets": list(targets)})

    def test_runs_the_ticked_plan_cases_as_pinned(self):
        done = self.planned(("TA5", "ET65"), [self.plan_case("TERR-004", ["TA5", "ET65"]),
                                              self.plan_case("TERR-006", ["ET65"], True)])
        status, start = self.start(done, ["TA5"])
        self.assertEqual(status, 201)
        [v] = plan.case_versions(self.root, ["TERR-004"])
        pinned = dict(done["result"]["plan_cases"][0], targets=["TA5"])
        self.assertEqual((start["intake"], start["targets"], start["engine_cases"]), (True, ["TA5"], ["TERR-004"]))
        self.assertEqual(start["cases"], [{"n": None, "text": TEXT, "engine_cases": ["TERR-004"], "targets": ["TA5"],
                                           "side_effects": [], "needs": []}])
        self.assertEqual((start["plan_cases"], start["case_versions"]), ([pinned], [v]))
        self.assertEqual((start["guidance"], start["plan_summary"], start["plan_targets"]),
                         (GUIDANCE, SUMMARY, ["TA5", "ET65"]))
        self.assertEqual((start["files"], start["plan_files"], start["completes"], start["fix_commits"]),
                         ([], [], [], []))
        task = self.task(start["task"])
        self.assertEqual(task["text"], f"Run from New test: {TITLE}. Cases TERR-004 on TA5. Started by the user in the "
                                       f"app after plan {done['id']} with the description and plan they approved.")
        self.assertEqual((task["cases"], task["targets"], task["clickup"]["task_id"], task["origin"]),
                         (["TERR-004"], ["TA5"], INTAKE_TID, {"start": start["id"], "check": done["id"]}))
        self.assertEqual([task[k] for k in ("intake", "guidance", "plan_summary", "plan_targets", "plan_cases",
                                            "case_versions", "files", "plan_files")],
                         [True, GUIDANCE, SUMMARY, ["TA5", "ET65"], [pinned], [v], [], []])

    def test_every_ticked_device_runs_its_cases(self):
        done = self.planned(("TA5", "ET65"), [self.plan_case("TERR-004", ["TA5", "ET65"]),
                                              self.plan_case("TERR-006", ["ET65"], True)])
        status, start = self.start(done, ["TA5", "ET65"])
        self.assertEqual((status, start["engine_cases"]), (201, ["TERR-004", "TERR-006"]))
        self.assertEqual([c["targets"] for c in start["cases"]], [["TA5", "ET65"], ["ET65"]])
        self.assertIn("Cases TERR-004, TERR-006 on TA5, ET65.", self.task(start["task"])["text"])

    def test_a_ticked_device_without_a_planned_case_stays_out(self):
        done = self.planned(("TA5", "ET65"), [self.plan_case("TERR-004", ["TA5"])])
        status, start = self.start(done, ["TA5", "ET65"])
        self.assertEqual((status, start["targets"], self.task(start["task"])["targets"]), (201, ["TA5"], ["TA5"]))

    def test_nothing_to_run_is_400(self):
        done = self.planned(("TA5", "ET65"), [self.plan_case("TERR-004", ["ET65"])])
        self.assertEqual(self.start(done, ["TA5"]),
                         (400, {"error": "targets: no case of the plan runs on the chosen devices"}))
        empty = self.planned(("TA5",), [])
        self.assertEqual(self.start(empty, ["TA5"])[0], 400)

    def test_a_case_changed_since_the_plan_is_refused(self):
        done = self.planned(("TA5", "ET65"), [self.plan_case("TERR-004", ["TA5"]), self.plan_case("TERR-006", ["ET65"])])
        put(self.root, "qa/cases/TERR-004.c1.json", dict(HANDS_OFF, id="TERR-004"))
        self.assertEqual(self.start(done, ["TA5"]), (409, {"error": "case TERR-004 changed since the plan; prepare again"}))
        status, start = self.start(done, ["ET65"])  # TERR-004 does not run there
        self.assertEqual((status, start["engine_cases"]), (201, ["TERR-006"]))

    def test_a_plan_runs_once_per_device(self):
        done = self.planned(("TA5", "ET65"), [self.plan_case("TERR-004", ["TA5", "ET65"])])
        status, first = self.start(done, ["TA5"])
        self.assertEqual(status, 201)
        worker.claim(self.root, first["id"])
        worker.finish(self.root, first["id"], "done")
        self.assertEqual(self.start(done, ["TA5"]),
                         (409, {"error": f"already run from this plan as {first['id']}; prepare again"}))
        self.assertEqual(self.start(done, ["ET65"])[0], 201)

    def test_the_planned_files_travel_with_the_run(self):
        self.resolve()
        for name in ("dsm.tif", "other.tif"):
            self.put_file(INTAKE_TID, name, b"abc", 0, 3)
        done = self.planned(("TA5",), [self.plan_case()], plan_files=[{"name": "dsm.tif", "case": "TERR-004"}])
        status, start = self.start(done, ["TA5"])
        self.assertEqual(status, 201)
        self.assertEqual(([f["name"] for f in start["files"]], start["plan_files"]),
                         (["dsm.tif"], [{"name": "dsm.tif", "case": "TERR-004"}]))
        self.assertEqual(self.task(start["task"])["plan_files"], [{"name": "dsm.tif", "case": "TERR-004"}])

    def test_a_planned_file_changed_since_the_plan_is_refused(self):
        self.resolve()
        self.put_file(INTAKE_TID, "dsm.tif", b"abc", 0, 3)
        done = self.planned(("TA5",), [self.plan_case()], plan_files=[{"name": "dsm.tif", "case": "TERR-004"}])
        self._req("POST", f"/api/files/{INTAKE_TID}/remove", {"name": "dsm.tif"})
        self.put_file(INTAKE_TID, "dsm.tif", b"abcd", 0, 4)
        self.assertEqual(self.start(done, ["TA5"]),
                         (409, {"error": "the attached files changed since the plan; prepare again"}))


class UploadTests(Base):
    def test_files_are_taken_for_a_task_read_from_its_link(self):
        self.assertEqual(self.put_file(INTAKE_TID, "dsm.tif", b"abc", 0, 3),
                         (409, {"error": "open the test link first"}))
        self.resolve()
        status, res = self.put_file(INTAKE_TID, "dsm.tif", b"abc", 0, 3)
        self.assertEqual((status, res["done"]), (200, True))
        status, listed = self._req("GET", f"/api/files/{INTAKE_TID}")
        self.assertEqual((status, [f["name"] for f in listed["files"]]), (200, ["dsm.tif"]))
        self.assertEqual(self.put_file(TID, "dsm.tif", b"abc", 0, 3)[0], 200)  # today's list still counts

    def test_a_test_of_an_old_today_list_keeps_todays_wording(self):
        os.replace(os.path.join(self.root, plan.snapshot_rel(self.day)),
                   os.path.join(self.root, plan.snapshot_rel("2026-01-02")))
        self.assertEqual(self.put_file(TID, "dsm.tif", b"abc", 0, 3),
                         (409, {"error": "that test is not in today's list"}))
        self.assertEqual(self.put_file(INTAKE_TID, "dsm.tif", b"abc", 0, 3),
                         (409, {"error": "open the test link first"}))

    def test_never_for_a_never_read_task(self):
        put(self.root, "qa/cache/intake/0000fake005.json", {"kind": "intake", "task_id": "0000fake005"})
        self.assertEqual(self.put_file("0000fake005", "dsm.tif", b"abc", 0, 3)[0], 409)


if __name__ == "__main__":
    unittest.main()
