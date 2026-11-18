"""Tests for the QA worker requests (qa/app/worker.py), the Writer's start and greenlight, and /api/worker.

python3 -I qa/app/tests/test_worker.py  (no network, no devices: a temporary workspace and a fake ClickUp)
"""
import copy
import datetime
import io
import json
import os
import re
import sys
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(TESTS_DIR)
sys.path[:0] = [TESTS_DIR, APP_DIR, os.path.join(os.path.dirname(APP_DIR), "runner")]
import plan  # noqa: E402
import serve  # noqa: E402
import worker  # noqa: E402
import writes  # noqa: E402
from test_plan import MD_UPG, REAL_URLOPEN, TASKS, FakeOpener, Workspace, good_prep, task  # noqa: E402

UTC = datetime.timezone.utc


def put(root, rel, obj):
    full = os.path.join(root, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        json.dump(obj, f)


TID = "86abcupg1"  # a ClickUp-shaped id (the shared fixture's ids hold dashes, which task links never do)


def ready_prep(day):
    """good_prep for TID, with case 1 ready for TA5 and ET65 and a fix commit on the dev ticket."""
    p = good_prep(day)
    p["tests"][0]["task_id"] = TID
    p["tests"][0]["cases"][0]["engine_cases"] = ["TERR-004"]
    return p


class Base(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.root = self.ws.root
        put(self.root, "qa/devices/TA5SERIAL01.v1.json", {"kind": "device", "tag": "TA5", "serial": "TA5SERIAL01"})
        put(self.root, "qa/devices/ET65SERIAL0001.v1.json", {"kind": "device", "tag": "ET65", "serial": "ET65SERIAL0001"})
        # a hands-off case (active, one read step, no writes): only hands-off cases start from Today
        put(self.root, "qa/cases/TERR-004.json", {"kind": "case", "id": "TERR-004", "status": "active", "targets": "tablet",
                                                  "created": "2026-10-20T15:40:00.000Z",
                                                  "steps": [{"n": 1, "text": "read", "gates": [], "read_js": "1"}]})
        self.w = writes.Writer(self.root)
        opener = FakeOpener([TASKS + [task(TID, "Android update", MD_UPG, status="ongoing", priority="normal", env=True)]])
        self.day = plan.refresh(self.root, self.w, opener=opener)["snapshot"]["day"]
        plan.put_prep(self.root, self.w, self.day, ready_prep(self.day))

    def tearDown(self):
        self.ws.cleanup()

    def check(self, targets=("TA5", "ET65")):
        p = plan.run_plan(self.root, TID, self.day)
        p["targets"] = list(targets)
        return self.w.create_request(dict(p, type="check"), exclusive=TID)

    def answer(self, rid, ok=("TA5", "ET65"), targets=("TA5", "ET65")):
        worker.claim(self.root, rid)
        rows = [{"tag": t, "ok": t in ok, "connected": True, "account": t in ok, "app_ok": True,
                 "app": "1.74.0-staging-84d3721", "needs": None if t in ok else "Log the CT account in on " + t}
                for t in targets]
        return worker.answer_check(self.root, rid, {"targets": rows, "note": "checked"})


class RunPlanTests(Base):
    def test_ready_cases_engine_cases_and_targets(self):
        p = plan.run_plan(self.root, TID, self.day)
        self.assertEqual(p["engine_cases"], ["TERR-004"])
        self.assertEqual(p["targets"], ["TA5", "ET65"])
        self.assertEqual(p["fix_commits"], ["c5103f54e"])
        self.assertEqual(p["cases"][0]["text"], "Android 12/11 installation rejection")

    def test_refusals(self):
        with self.assertRaises(plan.PrepError):
            plan.run_plan(self.root, "t-low", self.day)  # no notes for this test
        with self.assertRaises(plan.PrepError):
            plan.run_plan(self.root, "nope", self.day)
        snap = plan.load(self.root, self.day)
        next(t for t in snap["tests"] if t["task_id"] == TID)["cases_sig"] = "f" * 16
        with open(os.path.join(self.root, plan.snapshot_rel(self.day)), "w") as f:
            json.dump(snap, f)
        with self.assertRaisesRegex(plan.PrepError, "changed"):
            plan.run_plan(self.root, TID, self.day)


class RequestTests(Base):
    def test_check_is_exclusive_per_test(self):
        r = self.check()
        self.assertEqual((r["state"], r["type"], r["targets"]), ("new", "check", ["TA5", "ET65"]))
        with self.assertRaises(writes.WriteError) as e:
            self.check()
        self.assertEqual(e.exception.status, 409)

    def test_claim_note_answer(self):
        r = self.check()
        with self.assertRaises(worker.WorkerError):
            worker.note(self.root, r["id"], "too early")
        done = self.answer(r["id"], ok=("TA5",))
        self.assertEqual(done["state"], "done")
        self.assertEqual([x["ok"] for x in done["result"]["targets"]], [True, False])
        self.assertEqual(worker.check_usable(done), ["TA5"])

    def test_answer_must_cover_every_target_and_say_what_to_do(self):
        r = self.check()
        worker.claim(self.root, r["id"])
        bad = [{"targets": [{"tag": "TA5", "ok": True}]},
               {"targets": [{"tag": "TA5", "ok": True}, {"tag": "ET65", "ok": False}]},
               {"targets": [{"tag": "TA5", "ok": True, "account": False}, {"tag": "ET65", "ok": True}]},
               {"targets": [{"tag": "TA5", "ok": True}, {"tag": "PIXEL", "ok": True}]}]
        for obj in bad:
            with self.assertRaises(worker.WorkerError):
                worker.answer_check(self.root, r["id"], obj)

    def test_old_check_cannot_start(self):
        done = self.answer(self.check()["id"])
        later = datetime.datetime.now(UTC) + datetime.timedelta(minutes=31)
        with self.assertRaisesRegex(worker.WorkerError, "30 minutes"):
            worker.check_usable(done, later)

    def test_start_creates_the_task_and_the_request(self):
        done = self.answer(self.check()["id"])
        start = self.w.start_run(done, ["TA5"])
        self.assertEqual((start["type"], start["check"], start["targets"]), ("start", done["id"], ["TA5"]))
        task = json.load(open(os.path.join(self.root, "qa/tasks", start["task"] + ".json")))
        self.assertEqual((task["state"], task["cases"], task["targets"]), ("queued", ["TERR-004"], ["TA5"]))
        self.assertIn(done["id"], task["text"])
        self.assertEqual(task["clickup"]["task_id"], TID)
        with self.assertRaises(writes.WriteError):
            self.w.start_run(done, ["TA5"])  # one open start per test

    def test_greenlight_needs_results_ready(self):
        done = self.answer(self.check()["id"])
        tid = self.w.start_run(done, ["TA5"])["task"]
        with self.assertRaises(writes.WriteError):
            self.w.greenlight(tid, ["summary"])
        self.w.set_task_state(tid, "running", by="claude", via="qa-run")
        self.w.set_task_state(tid, "results_ready", by="claude", via="qa-run")
        with self.assertRaises(writes.WriteError):
            self.w.greenlight(tid, ["summary", "poem"])
        req = self.w.greenlight(tid, ["summary", "comment"])
        self.assertEqual((req["type"], req["task"], req["outputs"]), ("write", tid, ["summary", "comment"]))
        task = json.load(open(os.path.join(self.root, "qa/tasks", tid + ".json")))
        self.assertEqual(task["history"][-1]["by"], "user")

    def test_cancel_only_before_the_worker_takes_it(self):
        r = self.check()
        self.assertEqual(worker.cancel(self.root, r["id"])["state"], "cancelled")
        r2 = self.check()
        worker.claim(self.root, r2["id"])
        with self.assertRaises(worker.WorkerError):
            worker.cancel(self.root, r2["id"])

    def test_finish_and_fail(self):
        r = self.check()
        worker.claim(self.root, r["id"])
        with self.assertRaises(worker.WorkerError):
            worker.finish(self.root, r["id"], "done")  # a check ends with answer-check
        self.assertEqual(worker.finish(self.root, r["id"], "failed", "TA5 is not connected")["state"], "failed")


class WaitAndStatusTests(Base):
    def test_wait_prints_the_oldest_new_request(self):
        r = self.check()
        out = io.StringIO()
        self.assertEqual(worker.wait(self.root, "23:59", out=out, sleep=lambda s: None), 0)
        self.assertEqual(json.loads(out.getvalue())["id"], r["id"])
        st = worker.status(self.root)
        self.assertEqual((st["state"], st["request"]), ("busy", r["id"]))  # waking until claimed
        worker.claim(self.root, r["id"])
        self.assertEqual(worker.status(self.root)["state"], "busy")

    def test_ready_while_waiting_and_off_after(self):
        seen = {}

        def sleep(s):
            seen.setdefault("st", worker.status(self.root))
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            worker.wait(self.root, "23:59", out=io.StringIO(), sleep=sleep)
        self.assertEqual(seen["st"]["state"], "ready")
        self.assertEqual(worker.status(self.root)["state"], "off")  # the waiter process is gone (pid is this test)
        later = datetime.datetime.now(UTC) + datetime.timedelta(seconds=worker.READY_S + 5)
        self.assertEqual(worker.status(self.root, later)["state"], "off")

    def test_only_one_waiter(self):
        import fcntl
        d = os.path.join(self.root, worker.WORKER_REL)
        os.makedirs(d, exist_ok=True)
        fd = os.open(os.path.join(d, ".waiter.lock"), os.O_WRONLY | os.O_CREAT)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            with self.assertRaises(worker.WorkerError) as e:
                worker.wait(self.root, "23:59", out=io.StringIO(), sleep=lambda s: None)
            self.assertEqual(e.exception.status, 5)
        finally:
            os.close(fd)

    def test_end_time_stops_the_wait(self):
        clock = [datetime.datetime.now(UTC)]
        end = clock[0].astimezone() + datetime.timedelta(minutes=1)
        out = io.StringIO()

        def sleep(s):
            clock[0] += datetime.timedelta(minutes=2)
        worker.wait(self.root, end.strftime("%H:%M"), out=out, sleep=sleep, clock=lambda: clock[0])
        self.assertIn("stop", json.loads(out.getvalue()))

    def test_expire_fails_old_checks_and_abandoned_claims(self):
        r = self.check()
        later = datetime.datetime.now(UTC) + datetime.timedelta(minutes=31)
        self.assertEqual(worker.expire(self.root, later), [r["id"]])
        self.assertEqual(worker.load_request(self.root, r["id"])["state"], "failed")
        r3 = self.check()
        link = "claude://claude.ai/epitaxy/local_11111111-2222-3333-4444-555555555555"
        worker.claim(self.root, r3["id"], link)
        self.assertEqual(worker.expire(self.root, None, link), [r3["id"]])  # this worker's own claim: abandoned


class ReviewFixTests(Base):
    """The fixes from the step-2 review (2026-10-23)."""

    def start(self):
        done = self.answer(self.check()["id"])
        return self.w.start_run(done, ["TA5"])

    def task(self, tid):
        with open(os.path.join(self.root, "qa/tasks", tid + ".json")) as f:
            return json.load(f)

    def test_started_task_is_never_picked_by_qa_run_next(self):
        import tasks
        st = self.start()
        self.assertEqual(self.task(st["task"])["origin"], {"start": st["id"], "check": st["check"]})
        self.assertIsNone(tasks.next_task(self.root))

    def test_cancel_of_a_start_closes_its_task(self):
        st = self.start()
        req = worker.cancel(self.root, st["id"])
        self.w.close_started_task(req, "cancelled")
        self.assertEqual(self.task(st["task"])["state"], "closed")

    def test_a_stuck_claim_can_be_released_and_is_expired(self):
        r = self.check()
        worker.claim(self.root, r["id"], "claude://claude.ai/epitaxy/local_11111111-2222-3333-4444-555555555555")
        with self.assertRaises(worker.WorkerError):
            worker.cancel(self.root, r["id"])  # working: not cancelled
        later = datetime.datetime.now(UTC) + datetime.timedelta(minutes=21)
        self.assertEqual(worker.status(self.root, later)["state"], "stuck")
        self.assertEqual(worker.expire(self.root, later, None), [r["id"]])  # another worker's silent claim
        r2 = self.check()
        worker.claim(self.root, r2["id"])
        self.assertEqual(worker.cancel(self.root, r2["id"], later)["state"], "failed")

    def test_run_progress_keeps_a_claim_alive(self):
        r = self.check()
        worker.claim(self.root, r["id"])
        later = datetime.datetime.now(UTC) + datetime.timedelta(minutes=25)
        put(self.root, "qa/runner/state/progress/TA5.json",
            {"case": "TERR-004", "target": "TA5", "started": later.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
             "updated": later.strftime("%Y-%m-%dT%H:%M:%S.000Z")})
        self.assertEqual(worker.status(self.root, later)["state"], "busy")

    def test_drafts_are_not_cancelled_and_can_be_asked_again(self):
        st = self.start()
        tid = st["task"]
        self.w.set_task_state(tid, "running", by="claude", via="qa-run")
        self.w.set_task_state(tid, "results_ready", by="claude", via="qa-run")
        wr = self.w.greenlight(tid, ["summary"])
        with self.assertRaises(worker.WorkerError):
            worker.cancel(self.root, wr["id"])
        with self.assertRaises(writes.WriteError):
            self.w.greenlight(tid, ["summary"])  # one open drafts request per task
        worker.claim(self.root, wr["id"])
        worker.finish(self.root, wr["id"], "failed", "disk full")
        again = self.w.greenlight(tid, ["comment"])  # still greenlit: asks for drafts again
        self.assertEqual((again["type"], again["outputs"]), ("write", ["comment"]))

    def test_idle_between_requests_is_not_off(self):
        r = self.check()
        self.answer(r["id"])
        self.assertEqual(worker.status(self.root)["state"], "busy")

    def test_end_time_after_midnight_is_a_few_hours(self):
        now = datetime.datetime.now().astimezone().replace(hour=0, minute=10, second=0, microsecond=0)
        end = worker._until("19:00", now)
        self.assertEqual(end - now, datetime.timedelta(seconds=worker.EVENING_WAIT_S))
        morning = now.replace(hour=8, minute=17)
        self.assertEqual(worker._until("19:00", morning).hour, 19)

    def test_check_carries_side_effects_and_needs(self):
        self.assertIn("side_effects", self.check()["cases"][0])


class EndpointTests(Base):
    def setUp(self):
        super().setUp()
        self.srv = serve.build_server(self.root, 0)
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    def _req(self, method, path, body=None, token=True):
        base = f"http://127.0.0.1:{self.srv.qa_port}"
        headers = {"Host": f"127.0.0.1:{self.srv.qa_port}"}
        data = None
        if method == "POST":
            headers.update({"Content-Type": "application/json", "Origin": base})
            if token:
                headers["X-QA-Token"] = self.srv.qa_token
            data = json.dumps(body or {}).encode("utf-8")
        req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
        try:
            with REAL_URLOPEN(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_flow(self):
        self.assertEqual(self._req("POST", "/api/worker/check", {"task_id": TID}, token=False)[0], 403)
        self.assertEqual(self._req("POST", "/api/worker/check", {"task_id": "t-low"})[0], 409)  # no notes
        self.assertEqual(self._req("POST", "/api/worker/check", {"task_id": "../x"})[0], 400)
        self.assertEqual(self._req("POST", "/api/worker/check", {"task_id": TID, "targets": ["W044"]})[0], 400)
        status, chk = self._req("POST", "/api/worker/check", {"task_id": TID, "targets": ["TA5"]})
        self.assertEqual((status, chk["targets"]), (201, ["TA5"]))
        self.assertEqual(self._req("POST", "/api/worker/start", {"check": chk["id"], "targets": ["TA5"]})[0], 409)
        status, view = self._req("GET", "/api/worker")
        self.assertEqual((status, view["worker"]["state"], view["requests"][0]["id"]), (200, "off", chk["id"]))
        self.answer(chk["id"], targets=("TA5",))
        self.assertEqual(self._req("POST", "/api/worker/start", {"check": chk["id"], "targets": ["ET65"]})[0], 400)
        status, start = self._req("POST", "/api/worker/start", {"check": chk["id"], "targets": ["TA5"]})
        self.assertEqual((status, start["type"]), (201, "start"))
        self.assertEqual(self._req("POST", "/api/worker/greenlight", {"task": start["task"], "outputs": ["summary"]})[0],
                         409)
        self.assertEqual(self._req("POST", "/api/worker/cancel", {"id": start["id"]})[1]["state"], "cancelled")


class PrepareTests(Base):
    """The Run form: guidance and chosen devices, the worker's summary, and both carried into the run."""
    SUMMARY = "Test: TERR-004 on TA5.\nYour guidance: applied to the import step.\nNot tested: ET65."

    def setUp(self):
        super().setUp()
        self.srv = serve.build_server(self.root, 0)
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    _req = EndpointTests._req

    def prepare(self, **body):
        return self._req("POST", "/api/worker/check", dict({"task_id": TID, "prepare": True}, **body))

    def answer_with(self, rid, tags, summary):
        worker.claim(self.root, rid)
        rows = [{"tag": t, "ok": True, "connected": True, "account": True, "app_ok": True} for t in tags]
        return worker.answer_check(self.root, rid, {"targets": rows, "summary": summary})

    def test_guidance_text(self):
        self.assertIsNone(writes.guidance_text("   "))
        self.assertIsNone(writes.guidance_text(None))
        self.assertEqual(writes.guidance_text("  Line one\r\nline two  "), "Line one\nline two")
        with self.assertRaises(writes.WriteError) as e:
            writes.guidance_text("x" * (writes.GUIDANCE_MAX + 1))
        self.assertEqual(e.exception.status, 400)
        self.assertNotIn("person@example.com", writes.guidance_text("ask person@example.com first"))

    def test_prepare_stores_guidance_and_narrows_every_case(self):
        status, chk = self.prepare(targets=["TA5"], guidance="Import the general DSM only.")
        self.assertEqual(status, 201)
        self.assertEqual((chk["prepare"], chk["guidance"], chk["targets"]), (True, "Import the general DSM only.", ["TA5"]))
        self.assertEqual([c["targets"] for c in chk["cases"]], [["TA5"]])

    def test_devices_the_cases_do_not_run_on_are_refused(self):
        status, err = self.prepare(targets=["W044"])  # a tablet-only test
        self.assertEqual((status, err["error"]), (400, "targets: some of TA5, ET65"))

    def test_unregistered_devices_are_left_out(self):
        p = ready_prep(self.day)
        p["tests"][0]["cases"][0]["targets"] = ["TA5", "ET65", "TA3"]  # TA3 has no device profile here
        plan.put_prep(self.root, self.w, self.day, p)
        status, chk = self.prepare()
        self.assertEqual((status, chk["targets"], chk["cases"][0]["targets"]), (201, ["TA5", "ET65"], ["TA5", "ET65"]))

    def test_long_guidance_is_refused_without_echoing_it(self):
        status, err = self.prepare(guidance="secret plan " * 200)
        self.assertEqual(status, 400)
        self.assertNotIn("secret plan", err["error"])

    def test_a_prepare_answer_needs_a_summary(self):
        _, chk = self.prepare(targets=["TA5"])
        worker.claim(self.root, chk["id"])
        with self.assertRaisesRegex(worker.WorkerError, "summary"):
            worker.answer_check(self.root, chk["id"], {"targets": [{"tag": "TA5", "ok": True}]})
        with self.assertRaisesRegex(worker.WorkerError, "summary"):
            worker.answer_check(self.root, chk["id"], {"targets": [{"tag": "TA5", "ok": True}],
                                                       "summary": "\n".join(["line"] * (worker.SUMMARY_LINES + 1))})
        done = worker.answer_check(self.root, chk["id"], {"targets": [{"tag": "TA5", "ok": True}],
                                                          "summary": self.SUMMARY})
        self.assertEqual(done["result"]["summary"], self.SUMMARY)  # line breaks kept

    def test_a_plain_check_still_needs_no_summary(self):
        done = self.answer(self.check()["id"])
        self.assertIsNone(done["result"]["summary"])

    def test_run_carries_the_guidance_and_the_approved_summary(self):
        _, chk = self.prepare(targets=["TA5"], guidance="Import the general DSM only.")
        self.answer_with(chk["id"], ["TA5"], self.SUMMARY)
        status, start = self._req("POST", "/api/worker/start", {"check": chk["id"], "targets": ["TA5"]})
        self.assertEqual(status, 201)
        self.assertEqual((start["guidance"], start["plan_summary"]), ("Import the general DSM only.", self.SUMMARY))
        task = json.load(open(os.path.join(self.root, "qa/tasks", start["task"] + ".json")))
        self.assertEqual((task["guidance"], task["plan_summary"]), ("Import the general DSM only.", self.SUMMARY))
        self.assertIn("with their guidance and the summary they approved", task["text"])

    def test_no_guidance_is_not_claimed(self):
        _, chk = self.prepare(targets=["TA5"], guidance="   ")
        self.answer_with(chk["id"], ["TA5"], self.SUMMARY)
        _, start = self._req("POST", "/api/worker/start", {"check": chk["id"], "targets": ["TA5"]})
        task = json.load(open(os.path.join(self.root, "qa/tasks", start["task"] + ".json")))
        self.assertIsNone(task["guidance"])
        self.assertIn("with the summary they approved", task["text"])
        self.assertNotIn("their guidance", task["text"])

    def test_a_device_unticked_before_run_leaves_the_run(self):
        _, chk = self.prepare(targets=["TA5", "ET65"])
        self.answer_with(chk["id"], ["TA5", "ET65"], self.SUMMARY)
        status, start = self._req("POST", "/api/worker/start", {"check": chk["id"], "targets": ["TA5"]})
        self.assertEqual(status, 201)
        self.assertEqual(([c["targets"] for c in start["cases"]], start["targets"], start["plan_targets"]),
                         ([["TA5"]], ["TA5"], ["TA5", "ET65"]))

    def test_prepare_pins_the_case_version_and_run_carries_it(self):
        put(self.root, "qa/cases/TERR-004.c2.json", {"kind": "case", "id": "TERR-004", "status": "active",
                                                     "targets": "tablet",
                                                     "steps": [{"n": 1, "text": "read", "gates": [], "read_js": "1"}]})
        _, chk = self.prepare(targets=["TA5"])
        [v] = chk["case_versions"]
        self.assertEqual((v["id"], v["file"]), ("TERR-004", "qa/cases/TERR-004.c2.json"))
        self.assertEqual(len(v["sha256"]), 64)
        self.answer_with(chk["id"], ["TA5"], self.SUMMARY)
        status, start = self._req("POST", "/api/worker/start", {"check": chk["id"], "targets": ["TA5"]})
        self.assertEqual((status, start["case_versions"]), (201, [v]))
        task = json.load(open(os.path.join(self.root, "qa/tasks", start["task"] + ".json")))
        self.assertEqual(task["case_versions"], [v])

    def test_run_refuses_a_case_changed_since_prepare(self):
        _, chk = self.prepare(targets=["TA5"])
        self.answer_with(chk["id"], ["TA5"], self.SUMMARY)
        put(self.root, "qa/cases/TERR-004.c1.json", {"kind": "case", "id": "TERR-004", "status": "active",
                                                     "targets": "tablet", "steps": []})  # a new version after Prepare
        status, err = self._req("POST", "/api/worker/start", {"check": chk["id"], "targets": ["TA5"]})
        self.assertEqual((status, err["error"]), (409, "case TERR-004 changed since Prepare summary; prepare again"))

    def test_a_check_from_before_case_versions_still_starts(self):
        _, chk = self.prepare(targets=["TA5"])
        self.answer_with(chk["id"], ["TA5"], self.SUMMARY)
        req = worker.load_request(self.root, chk["id"])
        req.pop("case_versions")
        put(self.root, worker.request_rel(chk["id"]), req)
        status, start = self._req("POST", "/api/worker/start", {"check": chk["id"], "targets": ["TA5"]})
        self.assertEqual((status, start["case_versions"]), (201, []))


class PrepFreshTests(Base):
    """Whether a later prep run (a retry or Run now) has to prepare again."""

    def test_prep_fresh(self):
        self.assertEqual(plan.prep_fresh(self.root, self.day)["fresh"], False)  # notes saved, status not done
        plan.set_prep_status(self.root, self.w, self.day, "done")
        got = plan.prep_fresh(self.root, self.day)  # the fixture's notes cover only TID, not the other listed tests
        self.assertFalse(got["fresh"])
        self.assertTrue(all("came into the list after the pre-test notes" in w for w in got["why"]), got["why"])
        p = ready_prep(self.day)
        p["tests"] += [dict(p["tests"][0], task_id=t["task_id"]) for t in plan.load(self.root, self.day)["tests"]
                       if t["task_id"] != TID]
        plan.put_prep(self.root, self.w, self.day, p)
        self.assertEqual(plan.prep_fresh(self.root, self.day), {"fresh": True, "why": []})
        later = os.stat(os.path.join(self.root, plan.prep_rel(self.day))).st_mtime + 5
        put(self.root, "qa/cases/TERR-004.c1.json", {"kind": "case", "id": "TERR-004"})
        os.utime(os.path.join(self.root, "qa/cases/TERR-004.c1.json"), (later, later))
        got = plan.prep_fresh(self.root, self.day)
        self.assertEqual((got["fresh"], got["why"]), (False, ["case files changed after the notes: TERR-004.c1.json"]))


class RunOnceTests(Base):
    """One approved summary runs once per device: a second Run from the same check needs a new check."""

    def test_a_second_run_on_the_same_device_is_refused(self):
        done = self.answer(self.check()["id"])
        first = self.w.start_run(done, ["TA5"])
        worker.claim(self.root, first["id"])
        worker.finish(self.root, first["id"], "done")
        with self.assertRaises(writes.WriteError) as e:
            self.w.start_run(done, ["TA5", "ET65"])
        self.assertEqual((e.exception.status, e.exception.message),
                         (409, f"already run on TA5 as {first['id']}; check the devices again to run again"))
        self.assertEqual(self.w.start_run(done, ["ET65"])["targets"], ["ET65"])  # a device the first Run left out

    def test_a_start_cancelled_before_the_worker_took_it_ran_nothing(self):
        done = self.answer(self.check()["id"])
        first = self.w.start_run(done, ["TA5"])
        worker.cancel(self.root, first["id"])
        self.assertEqual(self.w.start_run(done, ["TA5"])["targets"], ["TA5"])

    def test_another_check_runs_again(self):
        done = self.answer(self.check()["id"])
        first = self.w.start_run(done, ["TA5"])
        worker.claim(self.root, first["id"])
        worker.finish(self.root, first["id"], "done")
        again = self.answer(self.check()["id"])
        self.assertEqual(self.w.start_run(again, ["TA5"])["check"], again["id"])


NEW_TID, CLICKUP_NAME = "86abcnew9", "Custom terrain - final tests"  # a New test task, not in today's list
GUIDANCE = "Map import accepts any resolution - try with both high and low resolution here"
LISTED = [{"n": 1, "text": "Map import (DSM, orthos) accepts any resolution"}]
CLICKUP_TEXT = ("- Map import (DSM, orthos) accepts any resolution\n"
                "- Check in the altitude graph so the new terrain is used")
LATER = "2099-01-01T00:00:00.000Z"  # a case version written after the request
WHICH = {"id": "N1", "kind": "choice", "blocking": True,
         "text": "Should the test use the existing map, Import file or both?",
         "options": [{"id": "both", "label": "Both ways"}, {"id": "import", "label": "Only Import file"}]}
WHERE = {"id": "N2", "kind": "text", "blocking": False, "text": "Which latitude should the DSM have?"}


class PlanTitleAndPartsTests(Base):
    """A New test plan (2026-11-05, tests/2026-11-04-results-redesign/SPEC.md): its title (TITLE RULE), kept and copied
    to the run at Start; a case written or rewritten for it (CASE RULE), checked when the worker answers: what
    case_schema.validate_case refuses, then each part's quote against the description, the ClickUp text and the
    answered questions."""

    def setUp(self):
        super().setUp()
        put(self.root, f"qa/cache/intake/{NEW_TID}.json", {"kind": "intake", "schema": 1, "task_id": NEW_TID,
                                                            "title": CLICKUP_NAME, "cases": LISTED,
                                                            "description": CLICKUP_TEXT})

    def intake(self, **extra):
        """A New test check as serve.py creates it (the task read from its link, the person's description)."""
        return self.w.create_request(dict({
            "type": "check", "intake": True, "prepare": True, "guidance": GUIDANCE, "targets": ["TA5"],
            "clickup": {"task_id": NEW_TID, "title": CLICKUP_NAME, "url": f"https://app.clickup.com/t/{NEW_TID}"},
            "clickup_cases": LISTED, "cases": [], "engine_cases": [], "fix_commits": [], "case_versions": [],
            "files": []}, **extra))

    def answer_plan(self, rid, cases=("TERR-004",), claim=True, **extra):
        if claim:
            worker.claim(self.root, rid)
        return worker.answer_check(self.root, rid, dict({"targets": [{"tag": "TA5", "ok": True}],
                                                         "summary": "Test: the Maps page on TA5.",
                                                         "plan_cases": self.pinned(cases)}, **extra))

    def refused(self, rid, cases, **extra):
        """The answer's refusal (always 400); the request stays claimed for the corrected answer."""
        with self.assertRaises(worker.WorkerError) as e:
            worker.answer_check(self.root, rid, dict({"targets": [{"tag": "TA5", "ok": True}], "summary": "Test: x.",
                                                      "plan_cases": self.pinned(cases)}, **extra))
        self.assertEqual(e.exception.status, 400)
        self.assertEqual(worker.load_request(self.root, rid)["state"], "claimed")
        return e.exception.message

    def pinned(self, cases):
        out = []
        for cid in cases:
            [v] = plan.case_versions(self.root, [cid])
            out.append({"id": cid, "file": v["file"], "sha256": v["sha256"], "new": True, "targets": ["TA5"],
                        "text": "Checks the Maps page"})
        return out

    def written(self, **change):
        """TERR-901 written for this plan by the rule (test_parts.parts_case, which begins with the start block and
        ends with the clean-up block), with the parts changed as given."""
        from test_parts import parts_case
        case = parts_case(created=LATER, ref=NEW_TID)
        for k, v in change.items():
            case["parts"][int(k[1:]) - 1].update(v)  # p1=..., p2=...: that part's fields
        put(self.root, "qa/cases/TERR-901.json", case)
        return case

    def task(self, tid):
        with open(os.path.join(self.root, "qa/tasks", tid + ".json")) as f:
            return json.load(f)

    # ---- the plan's title --------------------------------------------------------------------------------------

    def test_the_title_is_kept_collapsed_and_redacted(self):
        done = self.answer_plan(self.intake()["id"],
                                title="  Custom terrain on Android:\n 28 m limit, ask ann.smith@company.example ")
        self.assertEqual(done["result"]["title"], "Custom terrain on Android: 28 m limit, ask [EMAIL]")
        self.assertEqual(worker.load_request(self.root, done["id"])["result"]["title"], done["result"]["title"])
        self.assertIsNone(self.answer_plan(self.intake()["id"])["result"]["title"])  # none written: null

    def test_title_refusals(self):
        rid = self.intake()["id"]
        worker.claim(self.root, rid)
        for title in ("", "   ", "x" * (worker.PLAN_TITLE_MAX + 1), "a\x00b", 5, ["Custom terrain"]):
            self.assertEqual(self.refused(rid, ["TERR-004"], title=title), "title: text of 1-80 characters", title)
        done = worker.answer_check(self.root, rid, {"targets": [{"tag": "TA5", "ok": True}], "summary": "Test: x.",
                                                    "plan_cases": self.pinned(["TERR-004"]), "title": "y" * 80})
        self.assertEqual(done["result"]["title"], "y" * 80)

    def test_a_title_only_for_a_test_from_new_test(self):
        chk = self.check(("TA5",))  # a Today check
        worker.claim(self.root, chk["id"])
        rows = [{"tag": "TA5", "ok": True}]
        with self.assertRaises(worker.WorkerError) as e:
            worker.answer_check(self.root, chk["id"], {"targets": rows, "title": "Android update"})
        self.assertEqual((e.exception.status, e.exception.message), (400, "title: only for a test from New test"))
        self.assertNotIn("title", worker.answer_check(self.root, chk["id"], {"targets": rows, "title": None})["result"])

    def test_start_copies_the_title_to_the_request_and_the_task(self):
        title = "Custom terrain on Android: 28 m limit at 4 latitudes"
        start = self.w.start_run(self.answer_plan(self.intake()["id"], title=title), ["TA5"])
        self.assertEqual((start["title"], self.task(start["task"])["title"]), (title, title))
        self.assertIn(f"Run from New test: {CLICKUP_NAME}.", self.task(start["task"])["text"])  # the ClickUp name stays
        worker.cancel(self.root, start["id"])  # one open start per test
        other = self.w.start_run(self.answer_plan(self.intake()["id"]), ["TA5"])
        self.assertEqual((other["title"], self.task(other["task"])["title"]), (None, None))

    # ---- a case written for the plan ----------------------------------------------------------------------------

    def test_a_case_written_by_the_rule_is_planned(self):
        self.written()
        done = self.answer_plan(self.intake()["id"], ["TERR-901"])
        self.assertEqual([(c["id"], c["new"]) for c in done["result"]["plan_cases"]], [("TERR-901", True)])

    def test_a_case_written_for_the_plan_is_checked_as_its_write_would_be(self):
        rid = self.intake()["id"]
        worker.claim(self.root, rid)
        case = self.written()
        del case["parts"]
        put(self.root, "qa/cases/TERR-901.json", case)
        self.assertTrue(self.refused(rid, ["TERR-901"]).startswith(
            "TERR-901: parts: a case from New test lists 1-8 parts first"))
        self.written(p2={"quote": None})
        self.assertTrue(self.refused(rid, ["TERR-901"]).startswith(
            "TERR-901: parts[1] (part 2).quote: the task's own words"))
        from test_parts import MAPS, READ  # where the case's own steps sit, after the start block
        case = self.written()
        case["steps"][READ]["part"] = 1  # the read and its assert under a part that checks nothing
        put(self.root, "qa/cases/TERR-901.json", case)
        self.assertTrue(self.refused(rid, ["TERR-901"]).startswith(
            f"TERR-901: steps[{READ}] (step {READ + 1}): reads the page or asserts in part 1, which is not a check "
            "part"))
        case = self.written()
        case["steps"][MAPS]["helper"] = "act.py {target} tap testid:btn-maps --expect js:true"  # the settle rule too
        put(self.root, "qa/cases/TERR-901.json", case)
        self.assertIn(f"steps[{READ}] (step {READ + 1}): reads right after step {MAPS + 1} (tap), which waits for "
                      "nothing", self.refused(rid, ["TERR-901"]))
        self.written()  # corrected: the same request takes it
        self.assertEqual(self.answer_plan(rid, ["TERR-901"], claim=False)["state"], "done")

    def test_only_cases_new_or_changed_in_the_plan_are_checked(self):
        import case_schema
        with open(os.path.join(self.root, "qa/cases/TERR-004.json")) as f:
            old = json.load(f)
        self.assertNotEqual(case_schema.validate_case(old), [])  # the fixture is no complete record ...
        self.assertEqual(self.answer_plan(self.intake()["id"])["state"], "done")  # ... and older than the request
        put(self.root, "qa/cases/TERR-004.c1.json", dict(old, created=LATER))  # rewritten for this plan
        rid = self.intake()["id"]
        worker.claim(self.root, rid)
        self.assertTrue(self.refused(rid, ["TERR-004"]).startswith("TERR-004: schema: missing; "))

    # ---- each part's quote ----------------------------------------------------------------------------------------

    def test_a_quote_matches_without_case_spaces_or_punctuation(self):
        self.assertEqual(worker._squash(" Map-Import (DSM), ÄCCEPTS\n any…"), "mapimportdsmäcceptsany")
        self.written(p1={"quote": "MAP-IMPORT accepts   any resolution!!"},
                     p2={"quote": ["both high and low", "Resolution - try"]})
        self.assertEqual(self.answer_plan(self.intake()["id"], ["TERR-901"])["state"], "done")

    def test_a_quote_from_the_clickup_text(self):
        # the saved copy's description and listed cases, and the request's listed cases and ClickUp name
        self.written(p1={"quote": "Final tests"}, p2={"quote": ["(DSM, orthos)", "altitude graph so the new terrain"]})
        self.assertEqual(self.answer_plan(self.intake()["id"], ["TERR-901"])["state"], "done")

    def test_a_quote_found_nowhere_is_refused_naming_the_case_and_the_part(self):
        rid = self.intake()["id"]
        worker.claim(self.root, rid)
        self.written(p2={"quote": ["accepts any resolution", "29 m is greyed out"]})
        self.assertEqual(self.refused(rid, ["TERR-901"]),
                         "TERR-901 part 2: the quote '29 m is greyed out' is not in the description, the ClickUp text "
                         "or a question the person answered (case, spaces and punctuation are ignored); quote the "
                         "task's own words, as a list when they are not together")
        self.written(p1={"quote": "accepts any (…) resolution here"})  # words that are apart: a list, no ellipsis
        self.assertTrue(self.refused(rid, ["TERR-901"]).startswith("TERR-901 part 1: the quote"))

    def test_a_quote_from_a_question_the_person_answered(self):
        first = self.answer_plan(self.intake()["id"], [], needs=[WHICH, WHERE])
        follow = self.intake(follows=first["id"], answers=[{"need": "N1", "choice": "both"},
                                                           {"need": "N2", "text": "Spain at 40.4 north"}])
        self.written(p1={"quote": ["the existing map, Import file or both", "Spain at 40.4 north"]},
                     p2={"quote": "Both ways"})
        self.assertEqual(self.answer_plan(follow["id"], ["TERR-901"])["state"], "done")
        # an option the person did not choose is no answer
        again = self.intake(follows=first["id"], answers=[{"need": "N1", "choice": "both"}])
        worker.claim(self.root, again["id"])
        self.written(p2={"quote": "Only Import file"})
        self.assertTrue(self.refused(again["id"], ["TERR-901"]).startswith(
            "TERR-901 part 2: the quote 'Only Import file' is not in"))

    # ---- the parts rule whatever the source, and a version that only adds parts (2026-11-05, FINDINGS #6 and #7) ----

    def test_a_rewrite_of_a_clickup_sourced_case_is_held_to_the_parts_rule(self):
        rid = self.intake()["id"]
        worker.claim(self.root, rid)
        case = self.written()
        case["requirement"]["source"] = {"type": "clickup_task", "ref": NEW_TID, "locator": "description bullet 1"}
        del case["parts"]
        for s in case["steps"]:
            s.pop("part", None)
        put(self.root, "qa/cases/TERR-901.json", case)
        import case_schema
        self.assertEqual(case_schema.validate_case(case), [], "a write from a chat keeps its old rule")
        self.assertTrue(self.refused(rid, ["TERR-901"]).startswith(
            "TERR-901: parts: a case from New test lists 1-8 parts first"), "written for a New test plan: refused")

    def test_a_version_that_only_adds_parts_is_checked(self):
        import case_schema
        old = self.written()
        put(self.root, "qa/cases/TERR-901.json", old)
        new = dict(copy.deepcopy(old), created=LATER, parts=[dict(p, quote=None) for p in old["parts"]],
                   supersedes={"id": "TERR-901", "file": "qa/cases/TERR-901.json", "file_sha256": "0" * 64,
                               "why": "parts added"})
        with mock.patch.object(case_schema, "_same_steps_as_superseded", return_value=True):
            got = case_schema.validate_case(new)
            self.assertTrue(any(p.startswith("parts[0] (part 1).quote") for p in got), got)
            title_only = dict(copy.deepcopy(old), title="Maps page opens, renamed")
            for k in ("parts",):
                title_only.pop(k)
            for s in title_only["steps"]:
                s.pop("part", None)
            self.assertEqual(case_schema.validate_case(title_only), [], "a title-only edit stays unchecked")


NESTED = ("Test cases:\n"
          "*   Map import (without using it as custom terrain for flight planning) accepts any resolution - try with "
          "both high and low resolution here\n"
          "*   Using an existing map for flight planning custom terrain\n"
          "    *   28 m resolution (and finer) is accepted and can be selected as custom terrain. Check in the "
          "altitude graph so the new terrain is used\n"
          "    *   29m and coarser is in the list but can not be selected\n"
          "*   Android\n"
          "    *   Offline - Download a flight plan offline with custom terrain imported and make sure the custom "
          "terrain is still used when going offline\n"
          "*   Test with terrain from various latitudes. e.g.\n"
          "    *   Alaska\n"
          "    *   ...\n"
          "*   [the spec](https://example.org/spec)\n"
          "*   Anything else?\n")
HEADING = "Using an existing map for flight planning custom terrain"
RESULTS = ["28 m resolution (and finer) is accepted and can be selected as custom terrain",
           "29m and coarser is in the list but can not be selected"]
ALTITUDE = "Check in the altitude graph so the new terrain is used"


class TaskLinesTests(Base):
    """2026-11-05 (tests/2026-11-05-wingmate-debug/FINDINGS.md #2 and #4): the ClickUp lines under an item a part
    carries out are its expected results. The ones no part quotes go to not_covered by themselves; a check part that
    quotes the item but none of its result lines is refused, naming them."""

    intake = PlanTitleAndPartsTests.intake
    answer_plan = PlanTitleAndPartsTests.answer_plan
    refused = PlanTitleAndPartsTests.refused
    pinned = PlanTitleAndPartsTests.pinned
    written = PlanTitleAndPartsTests.written

    def setUp(self):
        super().setUp()
        put(self.root, f"qa/cache/intake/{NEW_TID}.json", {"kind": "intake", "schema": 1, "task_id": NEW_TID,
                                                            "title": CLICKUP_NAME, "cases": LISTED,
                                                            "description": NESTED})

    def test_the_items_and_the_lines_under_them(self):
        items = worker.task_items(NESTED)
        self.assertEqual([i["text"] for i in items][:3], [
            "Map import (without using it as custom terrain for flight planning) accepts any resolution - try with "
            "both high and low resolution here", HEADING, RESULTS[0] + ". " + ALTITUDE])
        self.assertEqual(items[1]["below"], [RESULTS[0] + ". " + ALTITUDE, RESULTS[1]])
        self.assertEqual(items[0]["below"], [])
        for line in ("Android", "web", "...", "…", "etc.", "Examples:", "Anything else?", "[spec](https://x.org/a)",
                     "![shot](https://x.org/a.png)", "https://x.org/a"):
            self.assertTrue(worker._no_task_line(line), line)
        for line in (ALTITUDE, "Alaska", "Is 29 m refused?", "Android tablets keep the terrain offline"):
            self.assertFalse(worker._no_task_line(line), line)

    def test_a_check_part_that_quotes_the_heading_only_is_refused_naming_the_result_lines(self):
        rid = self.intake()["id"]
        worker.claim(self.root, rid)
        self.written(p1={"quote": "Map import"}, p2={"quote": [HEADING, "accepts any resolution"]})
        # the lines are named sentence by sentence (review 2026-11-09), so the altitude-graph sentence is one of its own
        self.assertEqual(self.refused(rid, ["TERR-901"]),
                         f"TERR-901 part 2: it checks, but quotes the ClickUp item {HEADING!r}, whose expected results "
                         f"are the lines under it, sentence by sentence: '{RESULTS[0]}.', '{ALTITUDE}', '{RESULTS[1]}'; "
                         "quote the result lines this part checks; the others go to not_covered")
        # quoting one of the result lines is enough; the line no part quotes then goes to not_covered by itself
        self.written(p1={"quote": "Map import"}, p2={"quote": [HEADING] + RESULTS})
        done = self.answer_plan(rid, ["TERR-901"], claim=False, not_covered=["Import file in the planner"])
        self.assertEqual(done["result"]["not_covered"], ["Import file in the planner", ALTITUDE])
        # a part that only does something (no check) may name the item
        rid = self.intake()["id"]
        self.written(p1={"quote": HEADING}, p2={"quote": RESULTS})
        self.assertEqual(self.answer_plan(rid, ["TERR-901"])["result"]["not_covered"], [ALTITUDE])

    def test_the_refusal_names_each_sentence_so_its_quote_leaves_the_unchecked_one_to_not_covered(self):
        # review 2026-11-09: the refusal named each line whole, while not_covered counts sentences; quoting the line it
        # named also quoted the altitude-graph sentence, which no part checks, and that sentence was then listed nowhere
        rid = self.intake()["id"]
        worker.claim(self.root, rid)
        self.written(p1={"quote": "Map import"}, p2={"quote": HEADING})
        msg = self.refused(rid, ["TERR-901"])
        named = re.findall(r"'([^']+)'", msg.split("sentence by sentence: ", 1)[1].split("; quote ", 1)[0])
        self.assertEqual(named, [RESULTS[0] + ".", ALTITUDE, RESULTS[1]], "the altitude-graph sentence is named apart")
        # the part quotes what it checks of what was named: accepted, and the sentence it does not check is listed
        self.written(p1={"quote": "Map import"}, p2={"quote": [HEADING] + [s for s in named if s != ALTITUDE]})
        done = self.answer_plan(rid, ["TERR-901"], claim=False)
        self.assertEqual(done["result"]["not_covered"], [ALTITUDE])
        # a long result sentence is named cut at 120 characters with '…', as not_covered cuts it
        long_line = "The imported terrain " + "keeps its resolution " * 7 + "when it is used"
        items = worker.task_items(f"*   {HEADING}\n    *   {long_line}\n")
        part = {"n": 1, "text": "x", "quote": HEADING, "check": True}
        self.assertIn(repr(worker._cut_line(long_line)), worker._heading_problem("TERR-901", [part], items))
        self.assertTrue(worker._cut_line(long_line).endswith("…") and len(worker._cut_line(long_line)) <= 120)

    def test_the_lines_under_an_examples_item_are_no_result_lines(self):
        # review 2026-11-09: 'e.g.' lists examples (the latitudes of 0000fake007), not results; a case at a place the
        # list does not name is not refused until it quotes one it does not test
        latitudes = ("*   Test with terrain from various latitudes and make sure we have the same resolution "
                     "constraint everywhere. e.g.\n"
                     "    *   Alaska\n    *   Antarctica\n    *   South Africa\n    *   Spain\n    *   ...\n")
        parts = [{"n": 1, "text": "A QA_ site in Norway", "quote": "Norway", "check": False},
                 {"n": 2, "text": "Use existing map: the same 28 m limit", "check": True,
                  "quote": ["make sure we have the same resolution constraint everywhere"]}]
        for ending in (" e.g.", " e.g.:", ", for example:", ", such as"):
            text = latitudes.replace(" e.g.\n", ending + "\n", 1)
            self.assertIsNone(worker._heading_problem("TERR-901", parts, worker.task_items(text)), ending)
        # without the 'e.g.' the lines under the item are its results, and the rule names them
        plain = latitudes.replace(" e.g.\n", "\n", 1)
        self.assertIn("whose expected results are the lines under it, sentence by sentence: 'Alaska', 'Antarctica', "
                      "'South Africa', 'Spain'; quote the result lines",
                      worker._heading_problem("TERR-901", parts, worker.task_items(plain)))
        for text in ("Using an existing map for flight planning custom terrain", "Import a JPEG", "Check the leg",
                     "28 m resolution (and finer) is accepted"):
            self.assertIsNone(worker.EXAMPLES_RE.search(text), text)
        # a plan for Norway with the fixture's 'e.g.' item: accepted, and the example it does not run is listed
        rid = self.intake(guidance=GUIDANCE + "\nTest with terrain from various latitudes in Norway")["id"]
        self.written(p1={"quote": "Norway"}, p2={"quote": ["Test with terrain from various latitudes"]})
        self.assertEqual(self.answer_plan(rid, ["TERR-901"])["result"]["not_covered"], ["Alaska"])

    def test_lines_listed_already_or_quoted_are_not_added(self):
        self.written(p1={"quote": "Map import"}, p2={"quote": RESULTS})
        done = self.answer_plan(self.intake()["id"], ["TERR-901"],
                                not_covered=[f"{ALTITUDE} (needs the flight's altitude graph)"])
        self.assertEqual(done["result"]["not_covered"], [f"{ALTITUDE} (needs the flight's altitude graph)"])
        # only items a part carries out bring their lines: here the Android item and the latitudes are not quoted
        self.assertNotIn("Alaska", done["result"]["not_covered"])

    def test_a_description_line_that_is_a_clickup_line_counts_and_long_lines_are_cut(self):
        self.written(p1={"quote": "Map import"}, p2={"quote": RESULTS})
        guidance = ("Android\nOffline - Download a flight plan offline with custom terrain imported and make sure the "
                    "custom terrain is still used when going offline\nMy own words that are no ClickUp line")
        done = self.answer_plan(self.intake(guidance=guidance)["id"], ["TERR-901"])
        self.assertEqual(done["result"]["not_covered"], [
            ALTITUDE, "Offline - Download a flight plan offline with custom terrain imported and make sure the custom "
                      "terrain is still used…"], "Android is a bare platform word; the person's own line is no task "
                                                "line")

    def test_nothing_is_added_while_a_plan_case_has_no_parts(self):
        self.written(p1={"quote": "Map import"}, p2={"quote": RESULTS})
        done = self.answer_plan(self.intake()["id"], ["TERR-901", "TERR-004"])  # TERR-004: written before the rule
        self.assertEqual(done["result"]["not_covered"], [])
        self.assertEqual(worker.uncovered_task_lines({}, self.root, [], []), [], "an empty plan")

    def test_at_most_ten_lines_are_added(self):
        lines = "".join(f"    *   Result line number {n} of the map import\n" for n in range(14))
        put(self.root, f"qa/cache/intake/{NEW_TID}.json", {
            "kind": "intake", "schema": 1, "task_id": NEW_TID, "title": CLICKUP_NAME, "cases": LISTED,
            "description": f"*   {HEADING}\n{lines}"})
        req = {"clickup": {"task_id": NEW_TID}, "guidance": ""}
        case = {"parts": [{"n": 1, "text": "x", "quote": HEADING, "check": False}]}
        got = worker.uncovered_task_lines(req, self.root, [case], [])
        self.assertEqual(len(got), worker.ADDED_MAX)
        self.assertEqual(got[-1], "5 more lines of the ClickUp task that no part quotes")


class StartEndedTests(Base):
    """2026-11-05 (wingmate-debug FINDINGS #9): a start request that ends failed or cancelled while its task is still
    running no longer leaves the test stuck: with runs recorded the task moves to results_ready (its runs are the
    results, Mark done works), else back to queued; a task started from a chat stays as it is."""

    RUN = "R-20261105-TA5-TERR-004-01"
    LINK = "claude://claude.ai/epitaxy/local_11111111-2222-3333-4444-555555555555"

    def running(self, runs=True):
        done = self.answer(self.check()["id"])
        start = self.w.start_run(done, ["TA5"])
        worker.claim(self.root, start["id"], self.LINK)
        self.w.set_task_state(start["task"], "running", by="claude", via="qa-run")
        if runs:
            self.w.add_run(start["task"], self.RUN)
        return start

    def task(self, tid):
        with open(os.path.join(self.root, "qa/tasks", tid + ".json")) as f:
            return json.load(f)

    def test_expire_moves_a_stopped_tests_task_on(self):
        later = datetime.datetime.now(UTC) + datetime.timedelta(minutes=21)
        st = self.running()
        self.assertEqual(worker.expire(self.root, later), [st["id"]])
        t = self.task(st["task"])
        self.assertEqual((t["state"], t["history"][-1]["by"], t["history"][-1]["via"]),
                         ("results_ready", "claude", "qa-run"))
        self.assertEqual(t["history"][-1]["note"], f"Start {st['id']} failed: Hover stopped while handling this "
                                                   "request.")
        st2 = self.running(runs=False)
        worker.expire(self.root, None, self.LINK)  # this worker's own leftover
        self.assertEqual(self.task(st2["task"])["state"], "queued", "no run: back to queued")

    def test_wingmates_own_fail_moves_the_task_on(self):
        st = self.running()
        worker.finish(self.root, st["id"], "failed", "TA5 lost USB")
        self.assertEqual(self.task(st["task"])["state"], "results_ready")
        self.assertEqual(self.task(st["task"])["history"][-1]["note"], f"Start {st['id']} failed: TA5 lost USB")

    def test_release_in_the_app(self):
        later = datetime.datetime.now(UTC) + datetime.timedelta(minutes=21)
        st = self.running()
        req = worker.cancel(self.root, st["id"], later)  # Release: Hover showed no sign of work for 20 minutes
        self.w.close_started_task(req, "released")
        t = self.task(st["task"])
        self.assertEqual((t["state"], t["history"][-1]["by"], t["history"][-1]["via"]),
                         ("results_ready", "user", "app"), "its runs are the results; Mark done can close it")
        self.w.set_task_state(st["task"], "closed", by="user", via="app")
        st2 = self.running(runs=False)
        self.w.close_started_task(worker.cancel(self.root, st2["id"], later), "released")
        self.assertEqual([h["to"] for h in self.task(st2["task"])["history"]][-2:], ["queued", "closed"])

    def test_the_person_moves_a_running_task_only_after_its_start_ended(self):
        st = self.running(runs=False)
        for to in ("results_ready", "queued"):
            with self.assertRaises(writes.WriteError) as e:
                self.w.set_task_state(st["task"], to, by="user", via="app")
            self.assertEqual((e.exception.status, e.exception.message),
                             (409, f"{st['id']} is still open: Hover is running this test"))
        chat = self.w.create_task({"text": "a chat's /qa-run", "targets": ["TA5"]})["id"]
        self.w.set_task_state(chat, "running", by="claude", via="qa-run")
        with self.assertRaises(writes.WriteError) as e:
            self.w.set_task_state(chat, "results_ready", by="user", via="app")
        self.assertEqual(e.exception.status, 409)
        self.assertIsNone(self.w.settle_started_task(dict(st, state="failed", task=chat), "x"), "a chat task stays")
        self.assertEqual(self.task(chat)["state"], "running")

    def test_a_finished_start_leaves_its_task_alone(self):
        st = self.running()
        self.w.set_task_state(st["task"], "results_ready", by="claude", via="qa-run")
        worker.finish(self.root, st["id"], "done")
        self.assertEqual(self.task(st["task"])["state"], "results_ready")
        self.assertIsNone(self.w.settle_started_task(worker.load_request(self.root, st["id"]), "x"))


class ExpireWordsTests(Base):
    """2026-11-05 (wingmate-debug FINDINGS #27): a check no Hover took in time says so, and a stale New test plan
    says 'prepare again'."""

    def test_a_check_that_was_never_taken(self):
        later = datetime.datetime.now(UTC) + datetime.timedelta(minutes=31)
        today = self.check()
        worker.expire(self.root, later)
        self.assertEqual(worker.load_request(self.root, today["id"])["notes"][-1]["text"],
                         "Hover did not take this device check within 30 minutes (it may be off). Check again once "
                         "Hover runs.")
        plan_req = self.w.create_request({"type": "check", "intake": True, "prepare": True, "targets": ["TA5"],
                                          "clickup": {"task_id": "86abcnew7"}})
        worker.expire(self.root, later)
        self.assertEqual(worker.load_request(self.root, plan_req["id"])["notes"][-1]["text"],
                         "Hover did not take this plan within 30 minutes (it may be off). Press Prepare again once "
                         "Hover runs.")
        start = self.w.start_run(self.answer(self.check()["id"]), ["TA5"])
        worker.expire(self.root, datetime.datetime.now(UTC) + datetime.timedelta(hours=3))
        self.assertEqual(worker.load_request(self.root, start["id"])["notes"][-1]["text"],
                         "Too old to start now (devices may have changed). Start again in the app.")

    def test_check_usable_words(self):
        done = self.answer(self.check()["id"])
        later = datetime.datetime.now(UTC) + datetime.timedelta(minutes=31)
        with self.assertRaises(worker.WorkerError) as e:
            worker.check_usable(done, later)
        self.assertEqual(e.exception.message, "the device check is older than 30 minutes; check again")
        with self.assertRaises(worker.WorkerError) as e:
            worker.check_usable(dict(done, intake=True), later)
        self.assertEqual(e.exception.message, "the device check of this plan is older than 30 minutes; prepare again")


class OpenWorkTests(Base):
    """2026-11-05 (wingmate-debug FINDINGS #16 and #28): what counts as Hover's open work on a test's results, and
    the app's Mark done refused while there is some (the page's rule, for a tab that read the test earlier)."""

    def test_an_expired_file_request_is_no_work(self):
        now = datetime.datetime.now(UTC)
        file_req = {"type": "file", "task": "T-20261105-001", "state": "new",
                    "created": (now - datetime.timedelta(minutes=31)).strftime("%Y-%m-%dT%H:%M:%S.000Z")}
        self.assertEqual(worker.open_work([file_req], "T-20261105-001"), [])
        fresh = dict(file_req, created=now.strftime("%Y-%m-%dT%H:%M:%S.000Z"))
        claimed = dict(file_req, state="claimed")
        self.assertEqual(worker.open_work([fresh, claimed], "T-20261105-001"), [fresh, claimed])
        self.assertEqual(worker.open_work([dict(fresh, type="check")], "T-20261105-001"), [])

    def test_mark_done_waits_for_open_work(self):
        done = self.answer(self.check()["id"])
        tid = self.w.start_run(done, ["TA5"])["task"]
        self.w.set_task_state(tid, "running", by="claude", via="qa-run")
        self.w.set_task_state(tid, "results_ready", by="claude", via="qa-run")
        req = self.w.greenlight(tid, ["summary"])
        with self.assertRaises(writes.WriteError) as e:
            self.w.set_task_state(tid, "closed", by="user", via="app")
        self.assertEqual((e.exception.status, e.exception.message), (409, worker.WORK_OPEN_WHY))
        worker.claim(self.root, req["id"])
        worker.finish(self.root, req["id"], "failed", "disk full")
        self.assertEqual(self.w.set_task_state(tid, "closed", by="user", via="app")["state"], "closed")


if __name__ == "__main__":
    unittest.main()
