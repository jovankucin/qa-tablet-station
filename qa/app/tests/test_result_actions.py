"""Tests of the Results card actions, server and worker side (2026-11-03): the running tests with live progress per
device, Report issue (POST /api/tickets/draft, the worker request "draft"), Add result (POST /api/results/comment) and
Post comment (POST /api/results/comment/post: Touch ID, then the app's one ClickUp write, clickup_post.py, and the
posted marker), the card fields views.py adds ("running", "report", "comment", ticket state "drafting") and the gate
rules that keep the Post comment route and the poster to the person's press.

python3 -I qa/app/tests/test_result_actions.py [-v]

Every test works in a temp workspace. The Touch ID prompt is always a fake (FakePrompt) and ClickUp is always a fake
opener (FakeClickUp): nothing here compiles or runs touchid.swift, touches a device, ClickUp or the network.
"""
import datetime
import hashlib
import http.client
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(TESTS_DIR)
QA_DIR = os.path.dirname(APP_DIR)
sys.path.insert(0, APP_DIR)
sys.path.insert(0, os.path.join(QA_DIR, "runner"))

import approvals  # noqa: E402
import clickup_post  # noqa: E402
import data as data_model  # noqa: E402
import gate  # noqa: E402  (at import time, from this copy's runner folder)
import records  # noqa: E402
import serve  # noqa: E402
import tasks  # noqa: E402
import views  # noqa: E402
import worker  # noqa: E402
import writes  # noqa: E402

CAMP = "2026-11-03-actions"
CLICKUP_ID = "86c1abcd2"
TOKEN = "pk_4242_TESTTOKENFORTHEFAKEOPENER"
TASK = "T-20261103-001"          # results_ready: TERR-001 failed on TA5 and ET65
TASK_PASS = "T-20261103-002"     # results_ready: TERR-002 passed on TA5
TASK_OTHER = "T-20261103-003"    # TERR-003 failed, but on another account
TASK_PART = "T-20261103-004"     # TERR-004 partly passed, a check failed
TASK_BLK = "T-20261103-005"      # TERR-005 handed back, nothing failed
FAIL_TA5 = "R-20261103-TA5-TERR-001-01"
FAIL_ET65 = "R-20261103-ET65-TERR-001-01"
PASS_TA5 = "R-20261103-TA5-TERR-002-01"
OTHER_TA5 = "R-20261103-TA5-TERR-003-01"
PART_TA5 = "R-20261103-TA5-TERR-004-01"
BLK_TA5 = "R-20261103-TA5-TERR-005-01"
START = "W-20261103-002"         # the start request TASK came from (done)
CHECK = "W-20261103-001"
TITLE = "Offline sites: save a flight offline"
COMMENT = "tests/{camp}/COMMENT_{cu}_{task}.md"
FAILED_STEP = {"n": 2, "assert": {"kind": "scripted", "expr": "rows > 0", "result": False, "by": "script"},
               "readback": {"expected": [], "got": None, "ok": True}}
OK_STEP = {"n": 1, "readback": {"expected": ["x"], "got": {"x": True}, "ok": True}}
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def put(root, rel, content):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if isinstance(content, (dict, list)):
        content = records.canonical(content)
    with open(path, "wb") as f:
        f.write(content.encode("utf-8") if isinstance(content, str) else content)
    return path


def run_rec(rid, verdict, started, account="shared-stag", steps=(OK_STEP,)):
    tag = rid.split("-")[2]
    target = {"class": "web", "host": "webpc044"} if tag == "W044" else \
        {"class": "tablet", "tag": tag, "serial": "S1"}
    rec = {"kind": "run", "schema": 1, "id": rid, "created": started, "author": "runner", "supersedes": None,
           "mode": "live", "case": "-".join(rid.split("-")[3:5]), "case_file_sha256": "a" * 64, "target": target,
           "build": {"hash7": {"v": "abc1234", "st": "verified"}, "semver": {"v": "1.80.0", "st": "verified"},
                     "env": {"v": "staging", "st": "verified"}},
           "account": {"v": account, "st": "verified", "src": []},
           "attempts": [{"n": 1, "started": started, "ended": started,
                         "identity": [{"verb": "ready", "checks": [], "actions": ["woke the screen"]}],
                         "steps": list(steps), "evidence": []}],
           "verdict": {"v": verdict, "decided": started, "evidence_kind": "device"}}
    if verdict == "BLOCKED":
        rec["verdict"]["handback"] = {"step": 1, "reason": "needs a PIN"}
    return rec


def task_rec(tid, state, runs, cases, clickup=CLICKUP_ID, origin=None, created="2026-11-03T07:59:00.000Z", **extra):
    ck = {"url": f"https://app.clickup.com/t/{clickup}", "task_id": clickup, "cl": None} if clickup else None
    t = {"kind": "task", "schema": 1, "id": tid, "created": created, "author": "user",
         "text": f"Run from New test: {tid}", "clickup": ck,
         "targets": ["TA5", "ET65"], "priority": "normal", "cases": list(cases), "state": state,
         "history": [{"at": created, "from": None, "to": "queued", "by": "user", "via": "app", "note": None}],
         "runs": list(runs), "campaign": CAMP, "outputs": {"summary": None, "tickets": [], "comments": []}}
    if origin:
        t["origin"] = origin
    t.update(extra)
    return t


def request(rid, typ, state, created, **fields):
    return dict({"kind": "worker_request", "schema": 1, "id": rid, "type": typ, "state": state, "created": created,
                 "claimed_at": None, "done_at": None, "link": None, "notes": [], "result": None}, **fields)


class FakePrompt:
    """The Touch ID prompt stand-in: answers code (0 yes, 1 cancel, 2 unavailable, None no answer) and keeps reasons."""

    def __init__(self, code=0, side=None):
        self.code, self.side, self.reasons = code, side, []

    def __call__(self, reason):
        self.reasons.append(reason)
        if self.side:
            self.side()
        return self.code


class _Answer:
    def __init__(self, status, body):
        self.status, self._body = status, body

    def read(self, n=-1):
        return self._body if n is None or n < 0 else self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeClickUp:
    """A urlopen stand-in for ClickUp: keeps every request it is given and answers status and body, or raises exc."""

    def __init__(self, status=200, body=b'{"id": "90150012345", "hist_id": "26508", "date": 1568036964079}', exc=None):
        self.status, self.body, self.exc, self.calls = status, body, exc, []

    def __call__(self, req, timeout=None):
        self.calls.append({"url": req.full_url, "method": req.get_method(), "headers": dict(req.header_items()),
                           "body": req.data, "timeout": timeout})
        if self.exc is not None:
            raise self.exc
        return _Answer(self.status, self.body)


def http_error(code, body=b"", url="https://api.clickup.com/api/v2/task/x/comment"):
    return urllib.error.HTTPError(url, code, "error", {}, io.BytesIO(body))


class Workspace:
    def __init__(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="qa-actions-test-"))
        self.root = os.path.join(self.tmp, "ws")
        os.makedirs(self.root)
        r = self.root
        put(r, "qa/runner/engine.json", {"env": "staging", "account": "shared-stag"})
        put(r, "qa/secrets.env", f"CLICKUP_API_TOKEN={TOKEN}\n")
        for rid, verdict, started, kw in (
                (FAIL_TA5, "FAIL", "2026-11-03T08:00:00.000Z", {}),
                (FAIL_ET65, "FAIL", "2026-11-03T08:05:00.000Z", {}),
                (PASS_TA5, "PASS", "2026-11-03T07:00:00.000Z", {}),
                (OTHER_TA5, "FAIL", "2026-11-03T06:00:00.000Z", {"account": "someone-else"}),
                (PART_TA5, "PARTIAL", "2026-11-03T05:00:00.000Z", {"steps": (OK_STEP, FAILED_STEP)}),
                (BLK_TA5, "BLOCKED", "2026-11-03T04:00:00.000Z", {})):
            put(r, f"qa/runs/{CAMP}/{rid}.json", run_rec(rid, verdict, started, **kw))
        origin = {"start": START, "check": CHECK}
        put(r, f"qa/tasks/{TASK}.json", task_rec(TASK, "results_ready", [FAIL_TA5, FAIL_ET65], ["TERR-001"],
                                                 origin=origin))
        put(r, f"qa/tasks/{TASK_PASS}.json", task_rec(TASK_PASS, "results_ready", [PASS_TA5], ["TERR-002"]))
        put(r, f"qa/tasks/{TASK_OTHER}.json", task_rec(TASK_OTHER, "results_ready", [OTHER_TA5], ["TERR-003"]))
        put(r, f"qa/tasks/{TASK_PART}.json", task_rec(TASK_PART, "results_ready", [PART_TA5], ["TERR-004"]))
        put(r, f"qa/tasks/{TASK_BLK}.json", task_rec(TASK_BLK, "results_ready", [BLK_TA5], ["TERR-005"]))
        clickup = {"task_id": CLICKUP_ID, "custom_id": "CL-12345", "title": TITLE,
                   "url": f"https://app.clickup.com/t/{CLICKUP_ID}", "priority": None, "status": "to do"}
        put(r, f"qa/runner/state/worker/{CHECK}.json",
            request(CHECK, "check", "done", "2026-11-03T07:50:00.000Z", clickup=clickup, intake=True, targets=["TA5"]))
        put(r, f"qa/runner/state/worker/{START}.json",
            request(START, "start", "done", "2026-11-03T07:58:00.000Z", clickup=clickup, task=TASK, check=CHECK,
                    targets=["TA5", "ET65"], engine_cases=["TERR-001"]))
        self.n = 0

    def path(self, rel):
        return os.path.join(self.root, *rel.split("/"))

    def draft(self, rid, **extra):
        """A ticket draft folder for rid, as bug-draft writes it."""
        self.n += 1
        d = f"tests/{CAMP}/tickets/{self.n}"
        put(self.root, f"{d}/title.txt", "[CT] Maps: rows missing (TA5)\n")
        put(self.root, f"{d}/description.txt", "## Issue Summary\nRows missing.\n")
        rec = f"qa/runs/{CAMP}/{rid}.json"
        dj = {"source": rec, "run_record": rec, "finding": "confirmed"}
        dj.update(extra)
        put(self.root, f"{d}/draft.json", dj)
        return d

    def comment(self, text, task=TASK, register=True):
        """The worker's results comment file for task, registered as its outputs.comments (as tasks.py add-output)."""
        rel = COMMENT.format(camp=CAMP, cu=CLICKUP_ID, task=task)
        put(self.root, rel, text)
        if register:
            t = tasks.load_task(self.root, task)
            t["outputs"]["comments"] = [rel]
            put(self.root, f"qa/tasks/{task}.json", t)
        return rel

    def set_state(self, task, state):
        t = tasks.load_task(self.root, task)
        t["state"] = state
        put(self.root, f"qa/tasks/{task}.json", t)

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


def cards(root, posting=()):
    out = views.result_cards(data_model.Data(root), posting=posting)
    return out


def card(root, task, posting=()):
    out = cards(root, posting)
    return next(c for c in out["cards"] + out["older"] if c["task"] == task)


def requests_of(root, typ):
    return [r for r in reversed(worker.list_requests(root)) if r.get("type") == typ]


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Server:
    def __init__(self, root):
        self._log = serve.Handler.log_message
        serve.Handler.log_message = lambda self, fmt, *args: None
        self.srv = serve.build_server(root, 0)
        self.port, self.token = self.srv.qa_port, self.srv.qa_token
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def request(self, method, path, obj=None, token=True):
        body = json.dumps(obj).encode("utf-8") if obj is not None else b""
        host = f"127.0.0.1:{self.port}"
        h = {"Host": host, "Origin": f"http://{host}", "Content-Type": "application/json",
             "Content-Length": str(len(body))}
        if token:
            h["X-QA-Token"] = self.token
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        try:
            c.request(method, path, body=body or None, headers=h)
            r = c.getresponse()
            return r.status, json.loads(r.read() or b"null")
        finally:
            c.close()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()
        serve.Handler.log_message = self._log


# ---- views: running, report, comment, drafting -----------------------------------------------------------------------

class RunningTests(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        r = self.root = self.ws.root
        shot = f"tests/{CAMP}/TA5/shots/R-20261103-TA5-TERR-001-02_step3.png"
        put(r, shot, PNG)
        self.shot = shot
        # started from Run, the worker took it: TA5 at step 3 of 10, ET65 not started since the claim
        put(r, "qa/tasks/T-20261103-010.json",
            task_rec("T-20261103-010", "queued", [], ["TERR-001"], origin={"start": "W-20261103-010",
                                                                             "check": "W-20261103-009"},
                     created="2026-11-03T09:00:00.000Z"))
        put(r, "qa/runner/state/worker/W-20261103-010.json",
            request("W-20261103-010", "start", "claimed", "2026-11-03T09:00:00.000Z",
                    claimed_at="2026-11-03T09:01:00.000Z",
                    targets=["ET65", "TA5"], task="T-20261103-010", engine_cases=["TERR-001"],
                    clickup={"task_id": CLICKUP_ID, "title": TITLE},
                    notes=[{"at": "2026-11-03T09:01:30.000Z", "text": "Running TERR-001 on TA5, ET65"}]))
        put(r, "qa/runner/state/progress/S-TA5.json",
            {"run": "R-20261103-TA5-TERR-001-02", "case": "TERR-001", "target": "TA5", "attempt": 1,
             "step": {"n": 3, "text": "Tap 'Import' in the Maps page", "started": "2026-11-03T09:03:00.000Z"},
             "steps_total": 10, "started": "2026-11-03T09:02:00.000Z", "updated": "2026-11-03T09:03:05.000Z",
             "state": "running", "verdict": None, "last_shot": shot, "clock_offset_ms": None})
        put(r, "qa/runner/state/progress/S-ET65.json",
            {"run": "R-20261103-ET65-TERR-001-01", "case": "TERR-001", "target": "ET65", "attempt": 1, "step": None,
             "steps_total": 10, "started": "2026-11-03T08:05:00.000Z", "updated": "2026-11-03T08:09:00.000Z",
             "state": "finished", "verdict": "FAIL", "last_shot": None})
        # a test /qa-run runs from a chat: W044 finished its case
        put(r, "qa/tasks/T-20261103-011.json",
            task_rec("T-20261103-011", "running", [], ["TERR-002"], clickup=None, created="2026-11-03T09:30:00.000Z",
                     targets=["W044"]))
        put(r, "qa/runner/state/progress/webpc044.json",
            {"run": "R-20261103-W044-TERR-002-01", "case": "TERR-002", "target": "W044", "attempt": 1, "step": None,
             "steps_total": 8, "started": "2026-11-03T09:31:00.000Z", "updated": "2026-11-03T09:33:00.000Z",
             "state": "finished", "verdict": "PASS", "last_shot": None})
        # not running: a Run whose start failed, a task queued in a chat, a task with results
        put(r, "qa/tasks/T-20261103-012.json",
            task_rec("T-20261103-012", "queued", [], ["TERR-001"], origin={"start": "W-20261103-012"},
                     created="2026-11-03T09:40:00.000Z"))
        put(r, "qa/runner/state/worker/W-20261103-012.json",
            request("W-20261103-012", "start", "failed", "2026-11-03T09:40:00.000Z", targets=["TA5"]))
        put(r, "qa/tasks/T-20261103-013.json", task_rec("T-20261103-013", "queued", [], ["TERR-001"],
                                                        created="2026-11-03T09:50:00.000Z"))

    def tearDown(self):
        self.ws.close()

    def test_running_lists_the_open_runs_newest_first_with_live_progress(self):
        running = cards(self.root)["running"]
        self.assertEqual([x["task"] for x in running], ["T-20261103-011", "T-20261103-010"])
        chat, app = running
        self.assertEqual({k: app[k] for k in ("task", "title", "cases", "started", "state", "request", "note")},
                         {"task": "T-20261103-010", "title": TITLE, "cases": ["TERR-001"],
                          "started": "2026-11-03T09:00:00.000Z", "state": "queued",
                          "request": {"id": "W-20261103-010", "state": "claimed"},
                          "note": "Running TERR-001 on TA5, ET65"})
        ta5, et65 = app["devices"]  # in TAG_ORDER
        self.assertEqual({k: ta5[k] for k in ("tag", "state", "step", "of", "updated", "case", "run", "verdict")},
                         {"tag": "TA5", "state": "running", "step": 3, "of": 10, "updated": "2026-11-03T09:03:05.000Z",
                          "case": "TERR-001", "run": "R-20261103-TA5-TERR-001-02", "verdict": None})
        self.assertEqual(ta5["text"], 'Tap "Import" in the Maps page')
        self.assertEqual(ta5["shot_url"], "/files/" + self.shot)
        # ET65's progress file is from a run before the worker took this request: not this run's
        self.assertEqual(et65, {"tag": "ET65", "state": "waiting", "step": None, "of": None, "text": None,
                                "shot_url": None, "updated": None, "case": None, "run": None, "verdict": None})
        self.assertEqual(chat["devices"], [{"tag": "W044", "state": "finished", "step": 8, "of": 8, "text": None,
                                            "shot_url": None, "updated": "2026-11-03T09:33:00.000Z",
                                            "case": "TERR-002", "run": "R-20261103-W044-TERR-002-01",
                                            "verdict": "PASS"}])
        self.assertEqual((chat["request"], chat["state"], chat["started"]),
                         (None, "running", "2026-11-03T09:30:00.000Z"))
        for x in running:
            self.assertTrue({"task", "title", "cases", "started", "devices"} <= set(x))
            for d in x["devices"]:
                self.assertTrue({"tag", "state", "step", "of", "text", "shot_url", "updated"} <= set(d))

    def test_the_shot_url_is_one_the_files_route_serves(self):
        s = Server(self.root)
        try:
            status, body = s.request("GET", "/api/results/cards")
            self.assertEqual(status, 200)
            url = body["running"][1]["devices"][0]["shot_url"]
            c = http.client.HTTPConnection("127.0.0.1", s.port, timeout=30)
            c.request("GET", url, headers={"Host": f"127.0.0.1:{s.port}"})
            r = c.getresponse()
            self.assertEqual((r.status, r.read()), (200, PNG))
            c.close()
        finally:
            s.close()

    def test_a_finished_run_leaves_the_list(self):
        # Hover moves the task to results_ready first, then writes its done note and ends the start (qa-worker
        # section 2): the run stays in the list until the start ends (2026-11-04, the results redesign review)
        t = tasks.load_task(self.root, "T-20261103-010")
        t["state"] = "results_ready"
        put(self.root, "qa/tasks/T-20261103-010.json", t)
        self.assertEqual([x["task"] for x in cards(self.root)["running"]], ["T-20261103-011", "T-20261103-010"])
        with open(os.path.join(self.root, "qa", "runner", "state", "worker", "W-20261103-010.json"),
                  encoding="utf-8") as f:
            req = json.load(f)
        put(self.root, "qa/runner/state/worker/W-20261103-010.json",
            dict(req, state="done", done_at="2026-11-03T09:40:00.000Z"))
        self.assertEqual([x["task"] for x in cards(self.root)["running"]], ["T-20261103-011"])


class ReportAndCommentFieldTests(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.root = self.ws.root

    def tearDown(self):
        self.ws.close()

    def test_report_is_enabled_only_when_something_went_wrong(self):
        self.assertEqual(card(self.root, TASK)["verdict"], "failed_suspected")
        self.assertEqual(card(self.root, TASK)["report"], {"enabled": True, "why": None})
        self.assertEqual(card(self.root, TASK_PART)["report"], {"enabled": True, "why": None})
        self.assertEqual(card(self.root, TASK_PASS)["report"],
                         {"enabled": False, "why": "Nothing went wrong: the test passed on every device that ran it."})
        self.assertEqual(card(self.root, TASK_BLK)["report"],
                         {"enabled": False, "why": "Nothing failed in the app: the run was handed back to you."})
        # the pure rule, for every card verdict
        for v in ("failed", "failed_suspected", "partly"):
            self.assertTrue(views.report_field({"verdict": v, "devices": [{"verdict": v}]})["enabled"], v)
        for v in ("passed", "blocked", "not_decided", "not_tested"):
            r = views.report_field({"verdict": v, "devices": [{"verdict": v}]})
            self.assertFalse(r["enabled"], v)
            self.assertEqual(r["why"], views.REPORT_WHY[v])
        self.assertTrue(views.report_field({"verdict": "failed", "devices": [{"verdict": "passed"},
                                                                             {"verdict": "failed"}]})["enabled"])

    def test_comment_states(self):
        base = {"text": None, "sha256": None, "posted_at": None, "error": None,
                "clickup_url": f"https://app.clickup.com/t/{CLICKUP_ID}"}
        self.assertEqual(card(self.root, TASK)["comment"], dict(base, state="none"))
        w = writes.Writer(self.root)
        req = w.request_comment(TASK)
        self.assertEqual(card(self.root, TASK)["comment"], dict(base, state="drafting"))
        text = "@Colleague works well on staging.\nTested by someone@example.com on TA5 and ET65.\n"
        self.ws.comment(text)
        self.assertEqual(card(self.root, TASK)["comment"]["state"], "drafting")  # until the worker is done
        worker.claim(self.root, req["id"])
        worker.finish(self.root, req["id"], "done", "Wrote the comment")
        c = card(self.root, TASK)["comment"]
        shown = "@Colleague works well on staging.\nTested by [EMAIL] on TA5 and ET65."  # stripped and redacted
        self.assertEqual(c, dict(base, state="draft", text=shown, sha256=sha(shown)))
        self.assertEqual(card(self.root, TASK, posting={TASK})["comment"], dict(base, state="posting", text=shown,
                                                                              sha256=sha(shown)))
        put(self.root, views.posted_rel(self.ws.comment(text, register=False)),
            {"comment_id": "901", "posted_at": "2026-11-03T10:00:00.000Z", "sha256": sha(shown), "task_id": CLICKUP_ID})
        self.assertEqual(card(self.root, TASK)["comment"], dict(base, state="posted", text=shown, sha256=sha(shown),
                                                                posted_at="2026-11-03T10:00:00.000Z"))

    def test_comment_failed_states(self):
        w = writes.Writer(self.root)
        req = w.request_comment(TASK)
        worker.claim(self.root, req["id"])
        worker.finish(self.root, req["id"], "failed", "The run records could not be read.")
        c = card(self.root, TASK)["comment"]
        self.assertEqual((c["state"], c["error"], c["text"]), ("failed", "The run records could not be read.", None))
        self.ws.comment("x" * 1001)
        c = card(self.root, TASK)["comment"]
        self.assertEqual((c["state"], len(c["text"]), c["sha256"]), ("failed", 1000, None))
        self.assertIn("1001 characters", c["error"])
        self.ws.comment("  \n ")
        self.assertEqual(card(self.root, TASK)["comment"]["error"], "The comment file is empty.")
        # a comment file the worker does not write for this task: shown as not postable
        t = tasks.load_task(self.root, TASK)
        t["outputs"]["comments"] = [f"tests/{CAMP}/notes.md"]
        put(self.root, f"qa/tasks/{TASK}.json", t)
        put(self.root, f"tests/{CAMP}/notes.md", "hello")
        c = card(self.root, TASK)["comment"]
        self.assertEqual(c["state"], "failed")
        self.assertIn("COMMENT_<ClickUp id>_<task>.md", c["error"])
        # a link is never read
        rel = COMMENT.format(camp=CAMP, cu=CLICKUP_ID, task=TASK)
        os.remove(self.ws.path(rel))
        os.symlink(self.ws.path(f"tests/{CAMP}/notes.md"), self.ws.path(rel))
        t["outputs"]["comments"] = [rel]
        put(self.root, f"qa/tasks/{TASK}.json", t)
        c = card(self.root, TASK)["comment"]
        self.assertEqual((c["state"], c["text"]), ("failed", None))

    def test_which_comment_file_counts_and_a_token_in_it(self):
        # review 2026-11-03: the first entry that is this task's COMMENT file counts, so a wrongly named first entry
        # does not hide the right one the worker wrote after it, and a second COMMENT file never replaces one that may
        # already be posted
        rel = self.ws.comment("Works well on staging.")
        t = tasks.load_task(self.root, TASK)
        t["outputs"]["comments"] = [f"tests/{CAMP}/notes.md", rel]
        put(self.root, f"qa/tasks/{TASK}.json", t)
        put(self.root, f"tests/{CAMP}/notes.md", "hello")
        c = card(self.root, TASK)
        self.assertEqual((c["comment"]["state"], c["comment"]["text"]), ("draft", "Works well on staging."))
        self.assertEqual(c["outputs"]["comment_file"], rel)  # the card's own text and file agree
        other = COMMENT.format(camp=CAMP, cu="CL-12345", task=TASK)
        put(self.root, other, "Something newer.")
        t["outputs"]["comments"] = [rel, other]
        put(self.root, f"qa/tasks/{TASK}.json", t)
        self.assertEqual(card(self.root, TASK)["comment"]["text"], "Works well on staging.")
        # a ClickUp token in the text: not postable, and masked where the card shows it
        self.ws.comment("Works well. Token pk_81234567_ABCDEFGHIJKLMNOPQRSTUVWX0123 for the API.")
        c = card(self.root, TASK)["comment"]
        self.assertEqual((c["state"], c["sha256"], c["error"]), ("failed", None, clickup_post.TOKEN_TEXT_PROBLEM))
        self.assertEqual(c["text"], "Works well. Token [REDACTED] for the API.")

    def test_a_card_without_a_task_or_clickup(self):
        put(self.root, "qa/runs/2026-11-03-loose/R-20261103-TA5-TERR-009-01.json",
            run_rec("R-20261103-TA5-TERR-009-01", "PASS", "2026-11-03T03:00:00.000Z"))
        loose = next(c for c in cards(self.root)["cards"] + cards(self.root)["older"] if c["case"] == "TERR-009")
        self.assertIsNone(loose["task"])
        self.assertEqual(loose["comment"], {"state": "none", "text": None, "sha256": None, "posted_at": None,
                                            "error": None, "clickup_url": None})
        self.assertEqual(views.COMMENT_STATES, ("none", "drafting", "draft", "posting", "posted", "failed"))
        self.assertEqual(views.COMMENT_MAX, clickup_post.TEXT_MAX)

    def test_drafting_and_failed_draft_requests_show_as_tickets(self):
        w = writes.Writer(self.root)
        req = w.create_draft_request({"type": "draft", "task": TASK, "case": "TERR-001", "run": FAIL_ET65})
        tickets = card(self.root, TASK)["tickets"]
        self.assertEqual(tickets, [{"title": "", "description": "", "dir": None, "n": None, "filed": False,
                                    "target": "ET65", "run": FAIL_ET65, "state": "drafting", "cl_id": None,
                                    "offline": False, "request": {"id": req["id"], "state": "new"}, "error": None}])
        worker.claim(self.root, req["id"])
        worker.finish(self.root, req["id"], "failed", "Suspected, not confirmed yet: re-run the test 3 times.")
        t = card(self.root, TASK)["tickets"][0]
        self.assertEqual((t["state"], t["error"], t["request"]),
                         ("failed", "Suspected, not confirmed yet: re-run the test 3 times.",
                          {"id": req["id"], "state": "failed"}))
        req2 = w.create_draft_request({"type": "draft", "task": TASK, "case": "TERR-001", "run": FAIL_ET65})
        worker.claim(self.root, req2["id"])
        d = self.ws.draft(FAIL_ET65)
        tickets = card(self.root, TASK)["tickets"]  # the folder shows its own state, even before done
        self.assertEqual([(t["dir"], t["state"], t["run"]) for t in tickets], [(d, "draft", FAIL_ET65)])
        worker.finish(self.root, req2["id"], "done")
        self.assertEqual([t["state"] for t in card(self.root, TASK)["tickets"]], ["draft"])

    def test_existing_card_fields_stay(self):
        c = card(self.root, TASK)
        for k in ("key", "task", "clickup", "case", "title", "verdict", "devices", "auto", "steps", "proof", "state",
                  "outputs", "actions", "run_ids", "tasks", "tickets", "report", "comment"):
            self.assertIn(k, c)
        self.assertFalse([k for k in c if k.startswith("_")])
        self.assertEqual(c["run_ids"], [FAIL_ET65, FAIL_TA5])
        self.assertEqual(cards(self.root)["running"], [])


# ---- worker.py: the "draft" request ----------------------------------------------------------------------------------

def draft_fields(**over):
    f = {"type": "draft", "task": TASK, "case": "TERR-001", "run": FAIL_TA5}
    f.update(over)
    return f


class WorkerDraftTests(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.root = self.ws.root
        self.w = writes.Writer(self.root)

    def tearDown(self):
        self.ws.close()

    def test_type_and_fields_are_validated(self):
        self.assertIn("draft", worker.TYPES)
        req = self.w.create_request(draft_fields())
        self.assertEqual({k: req[k] for k in worker.DRAFT_FIELDS}, draft_fields())
        self.assertEqual(req["state"], "new")
        bad = [{k: v for k, v in draft_fields().items() if k != "run"}, draft_fields(extra=1), draft_fields(task=None),
               draft_fields(task="T-1"), draft_fields(case="terr-001"), draft_fields(case=None),
               draft_fields(run="x y"), draft_fields(run=None)]
        for f in bad:
            with self.assertRaises(writes.WriteError, msg=f) as cm:
                self.w.create_request(f)
            self.assertEqual(cm.exception.status, 400, f)
        self.assertEqual(len(requests_of(self.root, "draft")), 1)

    def test_life_cycle_and_done_needs_the_draft_folder(self):
        req = self.w.create_draft_request(draft_fields())
        out = io.StringIO()
        self.assertEqual(worker.wait(self.root, "23:59", out=out, sleep=lambda s: None), 0)
        self.assertEqual((json.loads(out.getvalue())["id"], json.loads(out.getvalue())["type"]), (req["id"], "draft"))
        worker.claim(self.root, req["id"])
        worker.note(self.root, req["id"], "Drafting the ticket for TA5")
        with self.assertRaises(worker.WorkerError) as cm:
            worker.finish(self.root, req["id"], "done")
        self.assertEqual(cm.exception.status, 409)
        d = self.ws.draft(FAIL_ET65)  # another run's draft does not count
        with self.assertRaises(worker.WorkerError):
            worker.finish(self.root, req["id"], "done")
        os.remove(self.ws.path(d + "/draft.json"))
        d = self.ws.draft(FAIL_TA5)
        os.remove(self.ws.path(d + "/description.txt"))  # a draft without its description is no draft yet
        with self.assertRaises(worker.WorkerError):
            worker.finish(self.root, req["id"], "done")
        put(self.root, d + "/description.txt", "## Issue Summary\nRows missing.\n")
        self.assertEqual(worker.draft_dir_for_run(self.root, FAIL_TA5), d)
        done = worker.finish(self.root, req["id"], "done", "Drafted: " + d)
        self.assertEqual(done["state"], "done")
        other = self.w.create_request(draft_fields(run=FAIL_ET65))
        worker.claim(self.root, other["id"])
        failed = worker.finish(self.root, other["id"], "failed", "Suspected, not confirmed yet: re-run 3 times.")
        self.assertEqual(failed["state"], "failed")

    def test_draft_dir_for_run(self):
        self.assertIsNone(worker.draft_dir_for_run(self.root, FAIL_TA5))
        d = self.ws.draft(FAIL_TA5 + "c1", run_record=f"qa/runs/{CAMP}/{FAIL_TA5}c1.json")  # its correction counts
        self.assertEqual(worker.draft_dir_for_run(self.root, FAIL_TA5), d)
        self.assertIsNone(worker.draft_dir_for_run(self.root, "R-20261103-TA5-TERR-001"))  # not a prefix match
        os.remove(self.ws.path(d + "/title.txt"))
        os.symlink(self.ws.path(d + "/description.txt"), self.ws.path(d + "/title.txt"))
        self.assertIsNone(worker.draft_dir_for_run(self.root, FAIL_TA5))  # a link is no draft file
        for bad in (None, "", "../x", 5):
            self.assertIsNone(worker.draft_dir_for_run(self.root, bad))

    def test_one_open_request_per_card_and_none_for_a_drafted_run(self):
        req = self.w.create_draft_request(draft_fields())
        with self.assertRaises(writes.WriteError) as cm:
            self.w.create_draft_request(draft_fields(run=FAIL_ET65))
        self.assertEqual((cm.exception.status, cm.exception.message),
                         (409, f"{req['id']} is still drafting a ticket for this test"))
        self.assertEqual(worker.cancel(self.root, req["id"])["state"], "cancelled")
        self.ws.draft(FAIL_TA5)
        with self.assertRaises(writes.WriteError) as cm:
            self.w.create_draft_request(draft_fields())
        self.assertEqual((cm.exception.status, cm.exception.message), (409, "this run already has a ticket draft"))
        self.assertEqual(self.w.create_draft_request(draft_fields(run=FAIL_ET65))["run"], FAIL_ET65)


# ---- clickup_post.py -------------------------------------------------------------------------------------------------

class PosterTests(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.root = self.ws.root

    def tearDown(self):
        self.ws.close()

    def test_one_post_to_the_comment_url_with_the_text_and_the_token(self):
        fake = FakeClickUp()
        res = clickup_post.post_comment(self.root, CLICKUP_ID, "Works well on staging.", opener=fake)
        self.assertEqual(res["comment_id"], "90150012345")
        self.assertRegex(res["posted_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z\Z")
        self.assertEqual(len(fake.calls), 1)
        call = fake.calls[0]
        self.assertEqual((call["url"], call["method"], call["timeout"]),
                         (f"https://api.clickup.com/api/v2/task/{CLICKUP_ID}/comment", "POST", clickup_post.TIMEOUT_S))
        self.assertEqual(json.loads(call["body"].decode("utf-8")),
                         {"comment_text": "Works well on staging.", "notify_all": False})
        self.assertEqual(call["headers"]["Authorization"], TOKEN)
        self.assertEqual(call["headers"]["Content-type"], "application/json")
        # a numeric id is kept as text; an answer without an id still counts as posted
        numeric = FakeClickUp(body=b'{"id": 458315}')
        self.assertEqual(clickup_post.post_comment(self.root, CLICKUP_ID, "x", opener=numeric)["comment_id"], "458315")
        self.assertIsNone(clickup_post.post_comment(self.root, CLICKUP_ID, "x", opener=FakeClickUp(body=b"nope"))
                          ["comment_id"])

    def test_never_another_url(self):
        fake = FakeClickUp()
        for bad in ("CL-12345", "../task", "abc/def", "ABCD1234", "abc", "a" * 41, "0000fake005", None, 5,
                    "abcd1234?x=1", "abcd1234#x"):
            with self.assertRaises(clickup_post.PostError, msg=bad) as cm:
                clickup_post.post_comment(self.root, bad, "text", opener=fake)
            self.assertEqual(cm.exception.kind, "no_task")
        self.assertEqual(fake.calls, [])
        self.assertEqual(clickup_post.comment_url("abcd1234"), "https://api.clickup.com/api/v2/task/abcd1234/comment")
        self.assertEqual(clickup_post.API_HOST, "api.clickup.com")

    def test_text_and_token_are_checked_before_any_request(self):
        fake = FakeClickUp()
        for bad in ("", "   ", "x" * 1001, "a\x00b", None):
            with self.assertRaises(clickup_post.PostError) as cm:
                clickup_post.post_comment(self.root, CLICKUP_ID, bad, opener=fake)
            self.assertEqual(cm.exception.kind, "bad_text")
        res = clickup_post.post_comment(self.root, CLICKUP_ID, "x" * 1000, opener=fake)
        self.assertEqual(len(res["posted_at"]), 24)
        os.remove(self.ws.path("qa/secrets.env"))
        with self.assertRaises(clickup_post.PostError) as cm:
            clickup_post.post_comment(self.root, CLICKUP_ID, "x", opener=fake)
        self.assertEqual(cm.exception.kind, "no_token")
        self.assertIn("Post comment again", cm.exception.message)
        self.assertEqual(clickup_post.token_problem(self.root), cm.exception.message)
        self.assertEqual(len(fake.calls), 1)

    def test_refusals_are_plain_and_never_hold_the_token(self):
        cases = ((http_error(401, b'{"err": "Token invalid ' + TOKEN.encode() + b'", "ECODE": "OAUTH_025"}'), "refused",
                  "HTTP 401"),
                 (http_error(404, b'{"err": "Task not found", "ECODE": "ITEM_013"}'), "refused", "HTTP 404"),
                 (http_error(429), "refused", "HTTP 429"),
                 (http_error(500, b"<html>" + TOKEN.encode()), "refused", "HTTP 500"),
                 (http_error(302), "refused", "redirect"),
                 (urllib.error.URLError("no route"), "offline", "could not be reached"),
                 (TimeoutError(), "offline", "did not answer in time"))
        for exc, kind, words in cases:
            with self.assertRaises(clickup_post.PostError) as cm:
                clickup_post.post_comment(self.root, CLICKUP_ID, "x", opener=FakeClickUp(exc=exc))
            self.assertEqual(cm.exception.kind, kind, words)
            self.assertIn(words, cm.exception.message)
            self.assertNotIn(TOKEN, cm.exception.message)
            self.assertNotIn("pk_", cm.exception.message)
        with self.assertRaises(clickup_post.PostError) as cm:
            clickup_post.post_comment(self.root, CLICKUP_ID, "x", opener=FakeClickUp(status=301, body=b""))
        self.assertEqual(cm.exception.kind, "refused")

    def test_a_lost_answer_never_turns_into_a_second_post(self):
        # review 2026-11-03: a broken status line (http.client.HTTPException, not an OSError) is "offline", never an
        # error the server answers with a bare 500; a 2xx whose body is lost counts as posted, so it is not offered again
        for exc in (http.client.BadStatusLine("x"), http.client.IncompleteRead(b""), http.client.LineTooLong("status")):
            with self.assertRaises(clickup_post.PostError, msg=type(exc).__name__) as cm:
                clickup_post.post_comment(self.root, CLICKUP_ID, "x", opener=FakeClickUp(exc=exc))
            self.assertEqual(cm.exception.kind, "offline")
        for exc in (http.client.IncompleteRead(b'{"id": "9'), TimeoutError(), ConnectionResetError()):
            calls = []

            class Lost(_Answer):
                def read(self, n=-1, exc=exc):
                    raise exc

            def opener(req, timeout=None):
                calls.append(req.full_url)
                return Lost(200, b"")
            res = clickup_post.post_comment(self.root, CLICKUP_ID, "x", opener=opener)
            self.assertEqual((res["comment_id"], len(calls)), (None, 1), type(exc).__name__)

    def test_a_text_holding_a_token_is_never_sent(self):
        fake = FakeClickUp()
        for text in (f"Works well. {TOKEN}", "Works well. pk_81234567_ABCDEFGHIJKLMNOPQRSTUVWX0123"):
            with self.assertRaises(clickup_post.PostError) as cm:
                clickup_post.post_comment(self.root, CLICKUP_ID, text, opener=fake)
            self.assertEqual((cm.exception.kind, cm.exception.message), ("bad_text", clickup_post.TOKEN_TEXT_PROBLEM))
        self.assertEqual(fake.calls, [])
        # words like pk_value, or a short pk_1_ab, are no token
        clickup_post.post_comment(self.root, CLICKUP_ID, "The pk_value column and pk_1_ab stay.", opener=fake)
        self.assertEqual(len(fake.calls), 1)

    def test_the_default_opener_follows_no_redirect(self):
        h = clickup_post._NoRedirect()
        req = urllib.request.Request("https://api.clickup.com/api/v2/task/abcd1234/comment", data=b"{}", method="POST",
                                     headers={"Authorization": TOKEN})
        for code in (301, 302, 303, 307, 308):
            self.assertIsNone(h.redirect_request(req, None, code, "moved", {}, "https://elsewhere.example/x"))
        opener = urllib.request.build_opener(clickup_post._NoRedirect)
        self.assertTrue(any(isinstance(x, clickup_post._NoRedirect) for x in opener.handlers))
        self.assertFalse(any(type(x) is urllib.request.HTTPRedirectHandler for x in opener.handlers))

    def test_comment_target_comes_from_the_records(self):
        t = tasks.load_task(self.root, TASK)
        self.assertEqual(clickup_post.comment_target(self.root, t), (CLICKUP_ID, TITLE))
        # a link that named a custom id: the New test copy of that task gives its id and title
        put(self.root, f"qa/cache/intake/{CLICKUP_ID}.json", {"kind": "intake", "task_id": CLICKUP_ID,
                                                              "custom_id": "CL-12345", "title": "From the copy"})
        cl_only = dict(t, clickup={"url": "https://app.clickup.com/t/CL-12345", "task_id": None, "cl": "CL-12345"})
        cl_only.pop("origin")
        self.assertEqual(clickup_post.comment_target(self.root, cl_only), (CLICKUP_ID, "From the copy"))
        no_id = "no ClickUp task id"
        for bad, why in ((dict(cl_only, clickup={"task_id": None, "cl": "CL-99999", "url": None}), no_id),
                         (dict(t, clickup=None), no_id),
                         (dict(t, clickup={"task_id": "../x", "cl": None}), no_id),
                         (dict(t, clickup={"task_id": "0000fake005", "cl": None}), no_id),
                         (dict(t, clickup={"task_id": "abcd9999", "cl": None}), "not the one its plan"),
                         (dict(cl_only, clickup={"task_id": CLICKUP_ID, "cl": "CL-777"}), "disagree")):
            with self.assertRaises(clickup_post.PostError, msg=why) as cm:
                clickup_post.comment_target(self.root, bad)
            self.assertIn(why, cm.exception.message)
        self.assertEqual(clickup_post.task_url(CLICKUP_ID), f"https://app.clickup.com/t/{CLICKUP_ID}")
        self.assertIsNone(clickup_post.task_url("CL-12345"))


# ---- the posted marker (writes.Writer) -------------------------------------------------------------------------------

class PostedMarkerTests(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.root = self.ws.root
        self.w = writes.Writer(self.root)
        self.rel = views.posted_rel(self.ws.comment("Works well."))

    def tearDown(self):
        self.ws.close()

    def marker(self, **over):
        m = {"comment_id": "901", "posted_at": "2026-11-03T10:00:00.000Z", "sha256": "a" * 64, "task_id": CLICKUP_ID}
        m.update(over)
        return m

    def test_created_once_with_exactly_the_four_fields(self):
        self.assertEqual(self.rel, f"tests/{CAMP}/COMMENT_{CLICKUP_ID}_{TASK}.posted.json")
        self.w.write_posted(self.rel, self.marker())
        with open(self.ws.path(self.rel), encoding="utf-8") as f:
            self.assertEqual(json.load(f), self.marker())
        with self.assertRaises(writes.WriteError) as cm:
            self.w.write_posted(self.rel, self.marker(comment_id="902"))
        self.assertEqual((cm.exception.status, cm.exception.message),
                         (409, "the results comment of this test was already posted"))
        with open(self.ws.path(self.rel), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["comment_id"], "901")
        for bad in (dict(self.marker(), extra=1), {k: v for k, v in self.marker().items() if k != "task_id"}, None):
            with self.assertRaises(writes.WriteError):
                self.w.write_posted(self.rel.replace(TASK, "T-20261103-002"), bad)

    def test_only_next_to_its_comment_file(self):
        with self.assertRaises(writes.WriteError) as cm:
            self.w.posted_target(f"tests/{CAMP}/COMMENT_{CLICKUP_ID}_{TASK_PASS}.posted.json")
        self.assertEqual(cm.exception.status, 404)
        for rel in (f"tests/{CAMP}/posted.json", f"tests/{CAMP}/COMMENT_x_{TASK}.json", f"qa/tasks/{TASK}.posted.json",
                    f"tests/{CAMP}/../{CAMP}/COMMENT_{CLICKUP_ID}_{TASK}.posted.json"):
            with self.assertRaises(writes.WriteError, msg=rel) as cm:
                self.w.posted_target(rel)
            self.assertEqual(cm.exception.status, 403, rel)
        self.assertEqual(self.w.posted_target(self.rel), self.ws.path(self.rel))


# ---- serve.py: the three routes --------------------------------------------------------------------------------------

class RouteTests(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.root = self.ws.root
        self.s = Server(self.root)
        self.prompt = FakePrompt(0)
        self.fake = FakeClickUp()
        self.s.srv.touchid_prompt = self.prompt
        self.s.srv.clickup_opener = self.fake

    def tearDown(self):
        self.s.close()
        self.ws.close()

    def post(self, path, body, **kw):
        return self.s.request("POST", path, body, **kw)

    # Report issue

    def test_draft_picks_the_newest_failed_run_without_a_draft(self):
        status, out = self.post("/api/tickets/draft", {"task": TASK, "case": "TERR-001"})
        self.assertEqual(status, 200, out)
        req = out["request"]
        self.assertEqual({k: req[k] for k in worker.DRAFT_FIELDS},
                         {"type": "draft", "task": TASK, "case": "TERR-001", "run": FAIL_ET65})
        status, out = self.post("/api/tickets/draft", {"task": TASK, "case": "TERR-001"})
        self.assertEqual((status, out["error"]), (409, f"{req['id']} is still drafting a ticket for this test"))
        worker.claim(self.root, req["id"])
        self.ws.draft(FAIL_ET65)
        worker.finish(self.root, req["id"], "done")
        status, out = self.post("/api/tickets/draft", {"task": TASK, "case": "TERR-001"})
        self.assertEqual((status, out["request"]["run"]), (200, FAIL_TA5))
        worker.claim(self.root, out["request"]["id"])
        self.ws.draft(FAIL_TA5)
        worker.finish(self.root, out["request"]["id"], "done")
        status, out = self.post("/api/tickets/draft", {"task": TASK, "case": "TERR-001"})
        self.assertEqual((status, out["error"]), (409, "a ticket is already drafted for each failed run of this test"))
        self.assertEqual(len(requests_of(self.root, "draft")), 2)

    def test_draft_after_a_failed_request_and_for_a_partly_passed_run(self):
        status, out = self.post("/api/tickets/draft", {"task": TASK_PART, "case": "TERR-004"})
        self.assertEqual((status, out["request"]["run"]), (200, PART_TA5))
        worker.claim(self.root, out["request"]["id"])
        worker.finish(self.root, out["request"]["id"], "failed", "bug-draft stopped: the finding is suspected.")
        status, again = self.post("/api/tickets/draft", {"task": TASK_PART, "case": "TERR-004"})
        self.assertEqual((status, again["request"]["run"]), (200, PART_TA5))
        self.assertNotEqual(again["request"]["id"], out["request"]["id"])

    def test_draft_400_and_404(self):
        for body in ({}, {"task": TASK}, {"task": "T-1", "case": "TERR-001"}, {"task": TASK, "case": "terr"},
                     {"task": 5, "case": "TERR-001"}):
            self.assertEqual(self.post("/api/tickets/draft", body)[0], 400, body)
        status, out = self.post("/api/tickets/draft", {"task": TASK_PASS, "case": "TERR-002"})
        self.assertEqual((status, out["error"]),
                         (400, "nothing failed in this test: no run failed or has a failed step"))
        status, out = self.post("/api/tickets/draft", {"task": TASK_OTHER, "case": "TERR-003"})
        self.assertEqual(status, 400)
        self.assertIn("not on the verified engine account (shared-stag)", out["error"])
        self.assertEqual(self.post("/api/tickets/draft", {"task": TASK_BLK, "case": "TERR-005"})[0], 400)
        self.assertEqual(self.post("/api/tickets/draft", {"task": TASK, "case": "TERR-002"})[0], 404)
        self.assertEqual(self.post("/api/tickets/draft", {"task": "T-20261103-099", "case": "TERR-001"})[0], 404)
        self.assertEqual(self.post("/api/tickets/draft", {"task": TASK, "case": "TERR-001"}, token=False)[0], 403)
        self.assertEqual(self.s.request("GET", "/api/tickets/draft")[0], 405)
        self.assertEqual(requests_of(self.root, "draft"), [])

    # Add result

    def test_add_result_greenlights_and_asks_for_the_comment(self):
        status, out = self.post("/api/results/comment", {"task": TASK})
        self.assertEqual(status, 200, out)
        req = out["request"]
        self.assertEqual((req["type"], req["task"], req["outputs"], req["state"]), ("write", TASK, ["comment"], "new"))
        self.assertEqual(req["clickup"]["task_id"], CLICKUP_ID)
        t = tasks.load_task(self.root, TASK)
        self.assertEqual(t["state"], "greenlit")
        self.assertEqual({k: t["history"][-1][k] for k in ("from", "to", "by", "via", "note")},
                         {"from": "results_ready", "to": "greenlit", "by": "user", "via": "app",
                          "note": "outputs: comment"})
        status, out = self.post("/api/results/comment", {"task": TASK})
        self.assertEqual((status, out["error"]), (409, "the drafts for this task are already being written"))
        worker.claim(self.root, req["id"])
        worker.finish(self.root, req["id"], "failed", "The worker stopped.")
        status, out = self.post("/api/results/comment", {"task": TASK})  # greenlit, the last request failed: again
        self.assertEqual((status, tasks.load_task(self.root, TASK)["state"]), (200, "greenlit"))
        worker.claim(self.root, out["request"]["id"])
        self.ws.comment("Works well on staging.")
        worker.finish(self.root, out["request"]["id"], "done")
        status, out = self.post("/api/results/comment", {"task": TASK})
        self.assertEqual((status, out["error"]), (409, "a results comment is already drafted for this test"))

    def test_mark_done_waits_for_open_work_and_a_comment_being_posted(self):
        # 2026-11-05 (wingmate-debug FINDINGS #28): the page's own rule on the state route, for a tab that read the
        # test before Add result was pressed in another one
        status, out = self.post("/api/results/comment", {"task": TASK})
        self.assertEqual(status, 200, out)
        status, err = self.post(f"/api/tasks/{TASK}/state", {"to": "closed"})
        self.assertEqual((status, err["error"]), (409, views.MARK_WHY["work"]))
        worker.claim(self.root, out["request"]["id"])
        worker.finish(self.root, out["request"]["id"], "failed", "The worker stopped.")
        self.s.srv.comment_posting = TASK  # the Touch ID prompt of Post comment is open, or the post on its way
        status, err = self.post(f"/api/tasks/{TASK}/state", {"to": "closed"})
        self.assertEqual((status, err["error"]), (409, "The comment is being posted."))
        self.s.srv.comment_posting = None
        status, t = self.post(f"/api/tasks/{TASK}/state", {"to": "closed"})
        self.assertEqual((status, t["state"]), (200, "closed"))

    def test_report_is_off_for_a_run_on_another_engine_account(self):
        # 2026-11-05 (wingmate-debug FINDINGS #8): POST /api/tickets/draft refuses it (test_draft_400_and_404), so the
        # one-test answer does not offer it, and it is not the next press
        out = views.result_cards(data_model.Data(self.root), task=TASK_OTHER)
        (c,) = out["cards"]
        self.assertEqual(c["report"], {"enabled": False,
                                       "why": "ran on the earlier engine account someone-else; not filed"})
        self.assertNotEqual((out["test"]["next"] or {}).get("press"), "report_issue")
        self.assertEqual(views.result_cards(data_model.Data(self.root), task=TASK)["cards"][0]["report"],
                         {"enabled": True, "why": None}, "on the engine account")

    def test_add_result_on_a_drafted_task_and_after_an_unusable_draft(self):
        self.ws.set_state(TASK, "drafted")
        status, out = self.post("/api/results/comment", {"task": TASK})
        self.assertEqual((status, out["request"]["outputs"]), (200, ["comment"]))
        self.assertEqual(tasks.load_task(self.root, TASK)["state"], "drafted")
        worker.claim(self.root, out["request"]["id"])
        self.ws.comment("x" * 1200)
        worker.finish(self.root, out["request"]["id"], "done")
        self.assertEqual(self.post("/api/results/comment", {"task": TASK})[0], 200)  # too long: a new one may be asked

    def test_add_result_409_400_404(self):
        for state, words in (("queued", "not in yet"), ("running", "not in yet"), ("closed", "closed")):
            self.ws.set_state(TASK_PASS, state)
            status, out = self.post("/api/results/comment", {"task": TASK_PASS})
            self.assertEqual(status, 409, state)
            self.assertIn(words, out["error"])
        t = tasks.load_task(self.root, TASK_OTHER)
        t["clickup"] = None
        put(self.root, f"qa/tasks/{TASK_OTHER}.json", t)
        status, out = self.post("/api/results/comment", {"task": TASK_OTHER})
        self.assertEqual((status, out["error"]), (409, "this test has no ClickUp task to comment on"))
        rel = self.ws.comment("Works well.")
        put(self.root, views.posted_rel(rel), {"comment_id": "1", "posted_at": "x", "sha256": "y", "task_id": "z"})
        status, out = self.post("/api/results/comment", {"task": TASK})
        self.assertEqual((status, out["error"]), (409, "the results comment of this test was already posted"))
        for body in ({}, {"task": "T-1"}, {"task": None}):
            self.assertEqual(self.post("/api/results/comment", body)[0], 400, body)
        self.assertEqual(self.post("/api/results/comment", {"task": "T-20261103-099"})[0], 404)
        self.assertEqual(self.post("/api/results/comment", {"task": TASK_PASS}, token=False)[0], 403)
        self.assertEqual(requests_of(self.root, "write"), [])

    # Post comment

    def draft_comment(self, text="Works well on staging, TA5 and ET65 failed (suspected) at step 2.\n"):
        self.ws.comment(text)
        c = card(self.root, TASK)["comment"]
        self.assertEqual(c["state"], "draft")
        return c

    def test_post_comment_posts_once_after_touch_id(self):
        c = self.draft_comment()
        seen = []
        self.prompt.side = lambda: seen.append(self.s.request("GET", "/api/results/cards")[1])
        status, out = self.post("/api/results/comment/post", {"task": TASK, "sha256": c["sha256"]})
        self.assertEqual(status, 200, out)
        self.assertEqual(sorted(out), ["comment_id", "posted_at"])
        self.assertEqual(out["comment_id"], "90150012345")
        self.assertEqual(self.prompt.reasons, [f"Post the results comment to '{TITLE}'"])
        # while the prompt was open, the card said "posting"
        during = next(x for x in seen[0]["cards"] + seen[0]["older"] if x["task"] == TASK)["comment"]
        self.assertEqual((during["state"], during["sha256"]), ("posting", c["sha256"]))
        self.assertEqual(len(self.fake.calls), 1)
        call = self.fake.calls[0]
        self.assertEqual((call["url"], call["method"]),
                         (f"https://api.clickup.com/api/v2/task/{CLICKUP_ID}/comment", "POST"))
        self.assertEqual(json.loads(call["body"]), {"comment_text": c["text"], "notify_all": False})
        rel = views.posted_rel(COMMENT.format(camp=CAMP, cu=CLICKUP_ID, task=TASK))
        with open(self.ws.path(rel), encoding="utf-8") as f:
            marker = json.load(f)
        self.assertEqual(marker, {"comment_id": "90150012345", "posted_at": out["posted_at"], "sha256": c["sha256"],
                                  "task_id": CLICKUP_ID})
        after = card(self.root, TASK)["comment"]
        self.assertEqual((after["state"], after["posted_at"]), ("posted", out["posted_at"]))
        # never a second post: no prompt, no request
        status, again = self.post("/api/results/comment/post", {"task": TASK, "sha256": c["sha256"]})
        self.assertEqual((status, again["error"]), (409, "the results comment of this test was already posted"))
        self.assertEqual((len(self.prompt.reasons), len(self.fake.calls)), (1, 1))
        # not even when the marker is gone, while this server runs
        os.remove(self.ws.path(rel))
        self.assertEqual(self.post("/api/results/comment/post", {"task": TASK, "sha256": c["sha256"]})[0], 409)
        self.assertEqual((len(self.prompt.reasons), len(self.fake.calls)), (1, 1))
        self.assertNotIn(TOKEN, json.dumps(out) + json.dumps(again))

    def test_no_post_without_touch_id(self):
        c = self.draft_comment()
        for code, msg in ((1, "Not approved: Touch ID was cancelled"),
                          (2, "Not approved: Touch ID and the account password are not available on this Mac"),
                          (None, "Not approved: no answer to the Touch ID prompt within 90 s"),
                          (7, "Not approved: the Touch ID prompt failed (exit 7)")):
            self.s.srv.touchid_prompt = FakePrompt(code)
            status, out = self.post("/api/results/comment/post", {"task": TASK, "sha256": c["sha256"]})
            self.assertEqual((status, out), (403, {"error": msg}))
        self.assertEqual(self.fake.calls, [])
        self.assertFalse(os.path.exists(self.ws.path(views.posted_rel(COMMENT.format(camp=CAMP, cu=CLICKUP_ID,
                                                                                    task=TASK)))))
        self.assertEqual(card(self.root, TASK)["comment"]["state"], "draft")

    def test_changed_text_is_refused(self):
        c = self.draft_comment()
        status, out = self.post("/api/results/comment/post", {"task": TASK, "sha256": "0" * 64})
        self.assertEqual(status, 409)
        self.assertIn("changed since the page showed it", out["error"])
        self.assertEqual(self.prompt.reasons, [])
        # changed while the prompt was open: nothing is posted
        self.prompt.side = lambda: self.ws.comment("Something else.", register=False)
        status, out = self.post("/api/results/comment/post", {"task": TASK, "sha256": c["sha256"]})
        self.assertEqual(status, 409)
        self.assertIn("while the Touch ID prompt was open", out["error"])
        self.assertEqual((len(self.prompt.reasons), self.fake.calls), (1, []))

    def test_post_409_before_the_prompt(self):
        status, out = self.post("/api/results/comment/post", {"task": TASK, "sha256": "a" * 64})
        self.assertEqual((status, out["error"]),
                         (409, "there is no results comment to post yet; press Add result first"))
        c = self.draft_comment()
        self.assertTrue(self.s.srv.filing.acquire(blocking=False))  # a File ticket prompt is open
        try:
            status, out = self.post("/api/results/comment/post", {"task": TASK, "sha256": c["sha256"]})
        finally:
            self.s.srv.filing.release()
        self.assertEqual((status, out["error"]), (409, "a Touch ID prompt is already open on this Mac"))
        os.remove(self.ws.path("qa/secrets.env"))
        status, out = self.post("/api/results/comment/post", {"task": TASK, "sha256": c["sha256"]})
        self.assertEqual(status, 409)
        self.assertIn("No ClickUp token found", out["error"])
        put(self.root, "qa/secrets.env", f"CLICKUP_API_TOKEN={TOKEN}\n")
        t = tasks.load_task(self.root, TASK)
        t["clickup"] = {"url": None, "task_id": "abcd9999", "cl": None}  # not the task its plan was made for
        put(self.root, f"qa/tasks/{TASK}.json", t)
        status, out = self.post("/api/results/comment/post", {"task": TASK, "sha256": c["sha256"]})
        self.assertEqual(status, 409)
        self.assertIn("not the one its plan was made for", out["error"])
        self.ws.comment("x" * 1001)
        t["clickup"] = {"url": None, "task_id": CLICKUP_ID, "cl": None}
        put(self.root, f"qa/tasks/{TASK}.json", t)
        self.assertEqual(self.post("/api/results/comment/post", {"task": TASK, "sha256": sha("x" * 1001)})[0], 409)
        self.assertEqual((self.prompt.reasons, self.fake.calls), ([], []))

    def test_post_502_writes_nothing(self):
        c = self.draft_comment()
        for exc, words in ((http_error(401, b'{"err": "Token invalid", "ECODE": "OAUTH_025"}'),
                            "ClickUp refused the comment (HTTP 401, ClickUp says: Token invalid OAUTH_025)"),
                           (urllib.error.URLError("down"), "could not be reached")):
            self.s.srv.clickup_opener = FakeClickUp(exc=exc)
            status, out = self.post("/api/results/comment/post", {"task": TASK, "sha256": c["sha256"]})
            self.assertEqual(status, 502)
            self.assertIn(words, out["error"])
            self.assertNotIn(TOKEN, out["error"])
            self.assertEqual(card(self.root, TASK)["comment"]["state"], "draft")
        self.assertFalse(os.path.exists(self.ws.path(views.posted_rel(COMMENT.format(camp=CAMP, cu=CLICKUP_ID,
                                                                                    task=TASK)))))
        self.assertEqual(len(self.prompt.reasons), 2)  # asked each time; a refused post can be pressed again

    def test_a_lost_answer_is_502_or_posted_never_500(self):
        # review 2026-11-03: a broken status line was an unhandled http.client error (500 "internal error", nothing
        # recorded); a 2xx whose body was lost is a post: the marker is written and the comment is not offered again
        c = self.draft_comment()
        self.s.srv.clickup_opener = FakeClickUp(exc=http.client.BadStatusLine("HTTP/1.1 2"))
        status, out = self.post("/api/results/comment/post", {"task": TASK, "sha256": c["sha256"]})
        self.assertEqual(status, 502)
        self.assertIn("Look at the ClickUp task before you post again", out["error"])
        self.assertEqual(card(self.root, TASK)["comment"]["state"], "draft")

        class Lost(_Answer):
            def read(self, n=-1):
                raise http.client.IncompleteRead(b"")
        calls = []
        self.s.srv.clickup_opener = lambda req, timeout=None: calls.append(req.full_url) or Lost(200, b"")
        status, out = self.post("/api/results/comment/post", {"task": TASK, "sha256": c["sha256"]})
        self.assertEqual((status, out["comment_id"], len(calls)), (200, None, 1))
        after = card(self.root, TASK)["comment"]
        self.assertEqual((after["state"], after["posted_at"]), ("posted", out["posted_at"]))
        self.assertEqual(self.post("/api/results/comment/post", {"task": TASK, "sha256": c["sha256"]})[0], 409)
        self.assertEqual(len(calls), 1)

    def test_a_token_in_the_comment_is_refused_before_the_prompt(self):
        self.ws.comment("Works well on staging. pk_81234567_ABCDEFGHIJKLMNOPQRSTUVWX0123")
        status, out = self.post("/api/results/comment/post", {"task": TASK, "sha256": "a" * 64})
        self.assertEqual((status, out["error"]), (409, clickup_post.TOKEN_TEXT_PROBLEM))
        self.assertEqual((self.prompt.reasons, self.fake.calls), ([], []))
        self.assertNotIn("pk_8", json.dumps(cards(self.root)))
        self.assertEqual(self.post("/api/results/comment", {"task": TASK})[0], 200)  # Add result asks for a new one

    def test_post_400_404_and_the_write_checks(self):
        c = self.draft_comment()
        for body in ({}, {"task": TASK}, {"task": TASK, "sha256": "A" * 64}, {"task": TASK, "sha256": "a" * 63},
                     {"task": "T-1", "sha256": "a" * 64}, {"task": TASK, "sha256": 5}):
            self.assertEqual(self.post("/api/results/comment/post", body)[0], 400, body)
        self.assertEqual(self.post("/api/results/comment/post", {"task": "T-20261103-099", "sha256": "a" * 64})[0], 404)
        self.assertEqual(self.post("/api/results/comment/post", {"task": TASK, "sha256": c["sha256"]}, token=False)[0],
                         403)
        self.assertEqual(self.s.request("GET", "/api/results/comment/post")[0], 405)
        self.assertEqual((self.prompt.reasons, self.fake.calls), ([], []))

    def test_the_ticket_route_shares_the_one_prompt(self):
        c = self.draft_comment()
        d = self.ws.draft(FAIL_TA5)
        filed = []
        self.prompt.side = lambda: filed.append(self.post("/api/tickets/file", {"dir": d}))
        self.assertEqual(self.post("/api/results/comment/post", {"task": TASK, "sha256": c["sha256"]})[0], 200)
        self.assertEqual(filed[0], (409, {"error": "a Post comment prompt is already open on this Mac"}))


# ---- the gate: only the person's press starts a post -----------------------------------------------------------------

class GateTests(unittest.TestCase):
    def decide(self, tool, inp):
        return gate.decide(tool, inp)[0]

    def test_this_copys_gate(self):
        self.assertEqual(os.path.dirname(os.path.abspath(gate.__file__)), os.path.join(QA_DIR, "runner"))

    def test_the_route_and_the_poster_are_denied_outside_plain_reads(self):
        deny = ["curl -s -X POST -d '{}' http://127.0.0.1:8787/api/results/comment/post",
                "python3 -I -c \"import urllib.request as u; "
                "u.urlopen('http://localhost:8787/api/results/comment/post')\"",
                "python3 -I -c 'import clickup_post'", "python3 -I qa/app/clickup_post.py",
                "env QA=1 python3 -I -m clickup_post"]
        for cmd in deny:
            self.assertEqual(gate.decide("Bash", {"command": cmd}), ("deny", gate.POSTER_REASON if "clickup_post" in cmd
                                                                     else gate.COMMENT_ROUTE_REASON), cmd)
        for tool, inp in (("mcp__Claude_Browser__javascript_tool", {"text": "fetch('/api/results/comment/post')"}),
                          ("mcp__claude-in-chrome__javascript_tool", {"text": "fetch('/api/results/comment/post')"}),
                          ("mcp__terminal__run_in_terminal", {"command": "python3 -c 'import clickup_post'"})):
            self.assertEqual(self.decide(tool, inp), "deny", tool)
        for cmd in ("grep -rn api/results/comment/post qa/app", "cat qa/app/clickup_post.py",
                    "python3 -I qa/app/tests/test_result_actions.py", "curl -s http://127.0.0.1:8787/api/results/cards",
                    "grep -n test_clickup_post qa/app/tests/x.txt"):
            self.assertEqual(self.decide("Bash", {"command": cmd}), "pass", cmd)
        self.assertEqual(self.decide("mcp__Claude_Browser__javascript_tool", {"text": "fetch('/api/results/cards')"}),
                         "pass")
        # the File ticket route keeps its own rule and reason
        self.assertEqual(gate.decide("mcp__Claude_Browser__javascript_tool", {"text": "fetch('/api/tickets/file')"}),
                         ("deny", gate.FILE_ROUTE_REASON))

    def test_spellings_that_reach_the_same_route_or_the_poster(self):
        # review 2026-11-03: curl and browsers drop URL dot segments, curl expands [t-t], the shell expands {post,} and
        # $P, and serve.py and views.py import the poster, so serve.clickup_post posts with no prompt at all
        url = "http://127.0.0.1:8787/api/"
        for cmd, reason in ((f"curl -X POST -d @b.json '{url}results/./comment/post'", gate.COMMENT_ROUTE_REASON),
                            (f"curl -X POST -d @b.json {url}results/x/../comment/post", gate.COMMENT_ROUTE_REASON),
                            (f"curl -X POST -d @b.json '{url}results/comment/pos[t-t]'", gate.COMMENT_ROUTE_REASON),
                            (f"curl -X POST -d @b.json {url}results/comment/{{post,}}", gate.COMMENT_ROUTE_REASON),
                            (f"P=post; curl -X POST -d @b.json {url}results/comment/$P", gate.COMMENT_ROUTE_REASON),
                            ("python3 -I -c \"import urllib.request as u; u.urlopen('http://localhost:8787"
                             "/api/results/comment/' + 'post')\"", gate.COMMENT_ROUTE_REASON),
                            (f"curl -X POST -d '{{}}' {url}tickets/./file", gate.FILE_ROUTE_REASON),
                            (f"curl -X POST -d '{{}}' {url}tickets/x/../file", gate.FILE_ROUTE_REASON),
                            ("python3 -I -c \"import serve; serve.clickup_post.post_comment('.', 'abcd1234', 'x')\"",
                             gate.POSTER_REASON),
                            ("python3 -I -c 'import views as v; v.clickup_post.comment_url(\"abcd1234\")'",
                             gate.POSTER_REASON)):
            self.assertEqual(gate.decide("Bash", {"command": cmd}), ("deny", reason), cmd)
        for text in ("fetch('/api/results/comment/' + 'post', {method: 'POST'})",
                     "fetch('/api/results/comment\\/post', {method: 'POST'})",
                     "fetch('/api/results/./comment/post', {method: 'POST'})"):
            self.assertEqual(self.decide("mcp__Claude_Browser__javascript_tool", {"text": text}), "deny", text)
        # ClickUp's own comment API with a token skips the app and Touch ID: denied like the ClickUp comment tools
        for cmd in ("curl -s -X POST -H 'Authorization: pk_1_X' -d '{\"comment_text\": \"x\"}' "
                    "https://api.clickup.com/api/v2/task/abcd1234/comment",
                    "python3 -I -c \"import urllib.request as u; u.urlopen(u.Request('https://api.clickup.com/api/v2/"
                    "task/' + t + '/comment', data=b'{}'))\"",
                    "curl -X DELETE https://api.clickup.com/api/v2/comment/90150012345"):
            self.assertEqual(gate.decide("Bash", {"command": cmd}), ("deny", gate.CLICKUP_REST_REASON), cmd)
        self.assertEqual(self.decide("mcp__terminal__run_in_terminal",
                                     {"command": "curl -X POST https://api.clickup.com/api/v2/task/x/comment"}), "deny")
        # reads stay reads, and Add result (POST /api/results/comment) only asks the worker
        for cmd in ("grep -rn 'api/results/comment' qa/app/static", "ls qa/app/__pycache__/clickup_post.cpython-313.pyc",
                    "grep -n 'api.clickup.com' qa/app/clickup_post.py",
                    "grep -n 'serve.clickup_post' qa/app/serve.py", "curl -s http://127.0.0.1:8787/api/results/comment",
                    "python3 -I qa/app/tests/test_results_live.py"):
            self.assertEqual(self.decide("Bash", {"command": cmd}), "pass", cmd)

    def test_the_fixture_rows(self):
        with open(os.path.join(QA_DIR, "runner", "tests", "gate_fixtures_comments.jsonl"), encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
        self.assertGreaterEqual(len(rows), 15)
        self.assertEqual({r["expect"] for r in rows}, {"deny", "pass"})
        for r in rows:
            self.assertEqual(self.decide(r["tool_name"], r["tool_input"]), r["expect"], r["tool_input"])

    def test_the_routes_match_the_gate(self):
        routes = {rx.pattern: m for rx, m in serve.ROUTES}
        self.assertEqual(routes[r"/api/results/comment/post"], {"POST": "api_results_comment_post"})
        self.assertEqual(routes[r"/api/results/comment"], {"POST": "api_results_comment"})
        self.assertEqual(routes[r"/api/tickets/draft"], {"POST": "api_ticket_draft"})
        self.assertTrue(gate.COMMENT_ROUTE.search("POST /api/results/comment/post"))
        self.assertFalse(gate.COMMENT_ROUTE.search("/api/results/comment"))  # Add result only asks the worker
        self.assertTrue(gate.POSTER.search(os.path.join(APP_DIR, "clickup_post.py")))


# ---- the worker's instructions ---------------------------------------------------------------------------------------

class WorkerSkillTests(unittest.TestCase):
    def test_the_skill_covers_draft_requests_and_the_posted_comment(self):
        path = os.path.join(QA_DIR, "plugin", "wingtra-qa", "skills", "qa-worker", "SKILL.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("## 5. draft:", text)
        for words in ("bug-draft", "owner-clickup-style", "worker.py done", "Post comment", "@mention", "1000",
                      "CL id", "Add result", "drafted"):
            self.assertIn(words, text)


if __name__ == "__main__":
    unittest.main()
