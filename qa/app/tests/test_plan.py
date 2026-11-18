"""Tests for the Today view's ClickUp read (qa/app/clickup_read.py, qa/app/plan.py) and its endpoints.

python3 -I qa/app/tests/test_plan.py  (no network: every ClickUp call goes to a fake opener)
"""
import io
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_DIR)
sys.path.insert(0, os.path.join(os.path.dirname(APP_DIR), "runner"))
import clickup_read  # noqa: E402
import plan  # noqa: E402
import redact  # noqa: E402
import serve  # noqa: E402
import writes  # noqa: E402

REAL_URLOPEN = urllib.request.urlopen

TOKEN = "pk_1234567_TESTTOKENVALUEXYZ0123456789ABCDEF"
OFFICE, FIELD = plan.OFFICE_OPTION, "a0000009-0000-4000-8000-000000000009"
LOC_OPTIONS = [{"id": FIELD, "name": "FIELD (ANY)", "orderindex": 0}, {"id": OFFICE, "name": "OFFICE", "orderindex": 1}]
ENV_OPTIONS = [{"id": "e1", "name": "CLOUD STAGING", "orderindex": 2}]

MD_UPG = """[https://app.clickup.com/t/1919191/CL-80002](https://app.clickup.com/t/1919191/CL-80002)

*   Android 12/11 installation rejection
    *   Will look strange
*   Upgrade WingtraApp from an existing installation
*   Emlid flow: connecting and collecting data (field testing)
*   Wi-Fi connection ( Needs to be connected to the drone, can be tested only manually)

Different HW:

*   TA3
*   TA5

Android versions:

*   13
*   14
"""
MD_ORDERED = "1.  First bug [CL-80005](https://app.clickup.com/t/1919191/CL-80005)\n\n1.  Second, see " \
             "[folder](https://drive.google.com/drive/folders/abc) and contact colleague@example.invalid\n"
MD_PARAS = "Process 2 flights with T02 base files.\n\nThen re-use the correction data.\n\nExpected: no new upload.\n"


def task(tid, name, md, loc=OFFICE, status="up next", priority="normal", env=None, custom_id=None, updated=1):
    fields = [{"id": plan.LOCATION_FIELD, "type": "drop_down", "type_config": {"options": LOC_OPTIONS},
               "value": None if loc is None else next(o["orderindex"] for o in LOC_OPTIONS if o["id"] == loc)}]
    if env:
        fields.append({"id": plan.SW_ENV_FIELD, "type": "drop_down", "type_config": {"options": ENV_OPTIONS},
                       "value": 2})
    return {"id": tid, "custom_id": custom_id, "name": name, "status": {"status": status},
            "priority": {"priority": priority} if priority else None, "date_updated": str(updated),
            "folder": {"id": plan.FOLDER_ID}, "custom_fields": fields, "markdown_description": md,
            "assignees": [{"id": 91919191, "username": "StationOwner", "email": "owner@example.invalid"}]}


TASKS = [
    task("t-low", "Low one", MD_PARAS, priority="low"),
    task("t-upg", "Android update", MD_UPG, status="ongoing", priority="normal", env=True),
    task("t-high", "Re-test bugs", MD_ORDERED, priority="high"),
    task("t-unset", "No location", MD_PARAS, loc=None),
    task("t-field", "Field one", MD_PARAS, loc=FIELD),
    task("0000fake005", "Never read", MD_PARAS, custom_id="CL-80008"),
]


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class FakeOpener:
    """Records every request; answers with pages of tasks, or raises a prepared error."""

    def __init__(self, pages=None, error=None):
        self.pages, self.error, self.requests = pages or [TASKS], error, []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        if self.error is not None:
            raise self.error
        page = int(re.search(r"[?&]page=(\d+)", req.full_url).group(1))
        body = {"tasks": self.pages[page], "last_page": page >= len(self.pages) - 1}
        return FakeResponse(json.dumps(body).encode("utf-8"))


class Workspace:
    def __init__(self, with_token=True):
        self.root = tempfile.mkdtemp(prefix="qa-plan-")
        os.makedirs(os.path.join(self.root, "qa", "tasks"))
        os.makedirs(os.path.join(self.root, "qa", "ledger"))
        if with_token:
            with open(os.path.join(self.root, "qa", "secrets.env"), "w") as f:
                f.write(f"# test\nOTHER=value1234\nCLICKUP_API_TOKEN={TOKEN}\n")

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


class ParseTests(unittest.TestCase):
    def test_bullets_notes_matrix_and_markers(self):
        cases, matrix, intro = plan.parse_cases(MD_UPG)
        self.assertEqual([c["n"] for c in cases], [1, 2, 3, 4])
        self.assertEqual(cases[0]["text"], "Android 12/11 installation rejection")
        self.assertEqual(cases[0]["notes"], ["Will look strange"])
        self.assertEqual(matrix, ["TA3", "TA5", "13", "14"])
        self.assertTrue(cases[2]["markers"]["field"])
        self.assertTrue(cases[3]["markers"]["drone"])
        self.assertFalse(cases[0]["markers"]["field"] or cases[0]["markers"]["drone"])

    def test_ordered_list_with_repeated_numbers_and_links(self):
        cases, _, _ = plan.parse_cases(MD_ORDERED)
        self.assertEqual([c["n"] for c in cases], [1, 2])
        self.assertEqual(cases[0]["text"], "First bug CL-80005")
        self.assertNotIn("drive.google.com", cases[1]["text"])
        self.assertIn("folder", cases[1]["text"])

    def test_paragraph_only_description_is_one_case(self):
        cases, _, _ = plan.parse_cases(MD_PARAS)
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["text"], "Process 2 flights with T02 base files.")
        self.assertEqual(cases[0]["notes"], ["Then re-use the correction data.", "Expected: no new upload."])

    def test_dev_tickets_never_credential_ticket(self):
        self.assertEqual(plan.dev_tickets(MD_UPG + " CL-80008"), ["CL-80002"])

    def test_clean_text_drops_urls_and_escapes(self):
        self.assertEqual(plan.clean_text(r"see https://x.example/y and 2\_3 \*bold\*"), "see [link] and 2_3 *bold*")


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.snap = plan.build_snapshot(TASKS, "2026-10-23T06:00:00.000Z", "2026-10-23")

    def test_office_only_ordered_by_priority(self):
        self.assertEqual([t["task_id"] for t in self.snap["tests"]], ["t-high", "t-upg", "t-low"])
        self.assertEqual(self.snap["hidden"], {"location_unset": 1, "not_office": 1})

    def test_never_lists_credential_ticket(self):
        self.assertNotIn("0000fake005", json.dumps(self.snap))
        self.assertNotIn("CL-80008", json.dumps(self.snap))

    def test_fields_and_redaction(self):
        upg = next(t for t in self.snap["tests"] if t["task_id"] == "t-upg")
        self.assertEqual(upg["sw_env"], "CLOUD STAGING")
        self.assertEqual(upg["dev_tickets"], ["CL-80002"])
        self.assertEqual(upg["url"], "https://app.clickup.com/t/t-upg")
        text = json.dumps(self.snap)
        self.assertNotIn("@company.example", text)
        self.assertNotIn("drive.google.com", text)
        self.assertNotIn("StationOwner", text)  # assignees are not copied
        for url in re.findall(r"https?://[^\"\s]+", text):
            self.assertTrue(url.startswith("https://app.clickup.com/t/"), url)

    def test_status_and_folder_filter(self):
        other = task("t-done", "Done", MD_PARAS, status="done")
        away = dict(task("t-away", "Elsewhere", MD_PARAS), folder={"id": "1"}, locations=[])
        snap = plan.build_snapshot(TASKS + [other, away], "x", "2026-10-23")
        ids = [t["task_id"] for t in snap["tests"]]
        self.assertNotIn("t-done", ids)
        self.assertNotIn("t-away", ids)


class ClientTests(unittest.TestCase):
    def test_get_only_to_clickup_with_token_header(self):
        op = FakeOpener(pages=[TASKS[:2], TASKS[2:]])
        got = clickup_read.team_tasks("1919191", "91919191", plan.STATUSES, TOKEN, op)
        self.assertEqual(len(got), len(TASKS))
        self.assertEqual(len(op.requests), 2)
        for r in op.requests:
            self.assertEqual(r.get_method(), "GET")
            self.assertTrue(r.full_url.startswith("https://api.clickup.com/api/v2/team/1919191/task?"))
            self.assertEqual(r.get_header("Authorization"), TOKEN)
            self.assertIsNone(r.data)
            self.assertIn("statuses%5B%5D=up+next", r.full_url)
            self.assertIn("assignees%5B%5D=91919191", r.full_url)

    def test_module_has_no_write_method(self):
        src = open(os.path.join(APP_DIR, "clickup_read.py"), encoding="utf-8").read()
        for word in ("POST", "PUT", "PATCH", "DELETE", "data="):
            self.assertNotIn(word, src.replace("GET requests only", ""), word)

    def test_errors_are_named(self):
        cases = [
            (urllib.error.URLError(socket.gaierror(8, "nodename nor servname provided")), "offline"),
            (urllib.error.URLError(socket.timeout("timed out")), "offline"),
            (urllib.error.HTTPError("u", 401, "no", {}, None), "auth"),
            (urllib.error.HTTPError("u", 500, "err", {}, None), "http"),
        ]
        for err, kind in cases:
            with self.assertRaises(clickup_read.ClickUpError) as cm:
                clickup_read.team_tasks("1919191", "1", plan.STATUSES, TOKEN, FakeOpener(error=err))
            self.assertEqual(cm.exception.kind, kind)
            self.assertNotIn(TOKEN, cm.exception.message)

    def test_no_token(self):
        with self.assertRaises(clickup_read.ClickUpError) as cm:
            clickup_read.team_tasks("1919191", "1", plan.STATUSES, None, FakeOpener())
        self.assertEqual(cm.exception.kind, "no_token")

    def test_read_token(self):
        ws = Workspace()
        try:
            self.assertEqual(clickup_read.read_token(os.path.join(ws.root, "qa", "secrets.env")), TOKEN)
            self.assertIsNone(clickup_read.read_token(os.path.join(ws.root, "qa", "missing.env")))
        finally:
            ws.cleanup()


class TokenStatusTests(unittest.TestCase):
    def setUp(self):
        self._saved = redact._SECRETS
        redact._SECRETS = []  # independent of the real workspace secrets file

    def tearDown(self):
        redact._SECRETS = self._saved

    def check(self, content, why, token=None):
        d = tempfile.mkdtemp(prefix="qa-tok-")
        try:
            p = os.path.join(d, "s.env")
            with open(p, "wb") as f:
                f.write(content)
            self.assertEqual(clickup_read.token_status(p), (why, token))
            if why != "ok":
                msg = clickup_read.no_token_message(p)
                self.assertIn("CLICKUP_API_TOKEN", msg)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_reasons(self):
        self.check(b"{\\rtf1\\ansi CLICKUP_API_TOKEN=pk_abc\\}", "rich_text")
        self.check(b"OTHER=1\n", "no_key")
        self.check(b"CLICKUP_API_TOKEN=\n", "empty")
        self.check(b"export CLICKUP_API_TOKEN = \"pk_abc\"\n", "ok", "pk_abc")
        self.check("﻿CLICKUP_API_TOKEN=“pk_abc”\n".encode("utf-8"), "ok", "pk_abc")
        self.assertEqual(clickup_read.token_status("/nonexistent/x.env"), ("no_file", None))
        self.check(b"CLICKUP_API_TOKEN=pk_old\nX=1\nCLICKUP_API_TOKEN=pk_new\n", "ok", "pk_new")
        self.check(b"CLICKUP_API_TOKEN=CLICKUP_API_TOKEN=\n", "not_a_token")
        self.check(b"CLICKUP_API_TOKEN=some other text\n", "not_a_token")

    def test_auth_error_names_clickups_reason(self):
        body = io.BytesIO(b'{"err":"Token invalid","ECODE":"OAUTH_025"}')
        err = urllib.error.HTTPError("u", 401, "no", {}, body)
        with self.assertRaises(clickup_read.ClickUpError) as cm:
            clickup_read.team_tasks("1919191", "1", plan.STATUSES, TOKEN, FakeOpener(error=err))
        self.assertIn("Token invalid OAUTH_025", cm.exception.message)
        self.assertEqual(redact.redact(cm.exception.message), cm.exception.message)

    def test_messages_survive_redaction(self):
        for why, text in clickup_read.NO_TOKEN_HELP.items():
            full = text + clickup_read.TOKEN_HOWTO
            self.assertEqual(redact.redact(full), full, why)


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.writer = writes.Writer(self.ws.root)

    def tearDown(self):
        self.ws.cleanup()

    def test_refresh_writes_today_and_load_returns_it(self):
        out = plan.refresh(self.ws.root, self.writer, opener=FakeOpener())
        self.assertTrue(out["ok"])
        day = out["snapshot"]["day"]
        path = os.path.join(self.ws.root, "qa", "cache", "plan", day + ".json")
        self.assertTrue(os.path.isfile(path))
        raw = open(path, encoding="utf-8").read()
        self.assertNotIn(TOKEN, raw)
        self.assertEqual(plan.load(self.ws.root)["day"], day)

    def test_offline_keeps_the_last_snapshot(self):
        plan.refresh(self.ws.root, self.writer, opener=FakeOpener())
        err = urllib.error.URLError(socket.gaierror(8, "no dns"))
        out = plan.refresh(self.ws.root, self.writer, opener=FakeOpener(error=err))
        self.assertFalse(out["ok"])
        self.assertEqual(out["kind"], "offline")
        self.assertIn("internet", out["message"].lower())
        self.assertIsNotNone(out["snapshot"])

    def test_writer_allows_only_the_plan_path(self):
        with self.assertRaises(writes.WriteError):
            self.writer._write("qa/cache/clickup/x.json", b"{}", "replace")
        with self.assertRaises(writes.WriteError):
            self.writer._write("qa/cache/plan/../x.json", b"{}", "replace")
        with self.assertRaises(writes.WriteError):
            self.writer._write("qa/cache/plan/2026-10-23.json", b"{}", "create")


def good_prep(day):
    return {"kind": "today_prep", "schema": 1, "day": day, "created": "2026-10-23T06:05:00.000Z",
            "based_on": "2026-10-23T06:00:00.000Z", "engine_env": "staging",
            "branches": {"sky-staging": {"head": "9a89646c1", "describe": "1.74.0-7-g9a89646c1"}},
            "tests": [{"task_id": "t-upg", "summary": "Two of four cases can run in the office today.",
                       "dev_tickets": [{"cl": "CL-80002", "status": "deployed to staging", "env": "staging",
                                        "fix": {"commits": ["c5103f54e"], "pr": "#10524",
                                                "in": {"sky-staging": True, "sky-production": False}}}],
                       "cases": [{"n": 1, "status": "ready", "why": ["Engine case exists."], "green": True,
                                  "engine_cases": ["UPG-001"], "targets": ["TA5", "ET65"],
                                  "missing_devices": [{"tag": "TA3", "why": "no profile"}],
                                  "required": {"env": "staging", "build_note": "any staging build"},
                                  "needs": [], "side_effects": [], "estimate_min": 10},
                                 {"n": 3, "status": "not_office", "why": ["Field test."]}]}]}


class PrepTests(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.writer = writes.Writer(self.ws.root)
        self.day = plan.refresh(self.ws.root, self.writer, opener=FakeOpener())["snapshot"]["day"]

    def tearDown(self):
        self.ws.cleanup()

    def test_valid_prep_saved_and_loaded(self):
        clean = plan.put_prep(self.ws.root, self.writer, self.day, good_prep(self.day))
        self.assertEqual(clean["tests"][0]["cases"][0]["status"], "ready")
        prep, status = plan.load_prep(self.ws.root, self.day)
        self.assertEqual(prep["tests"][0]["dev_tickets"][0]["fix"]["in"]["sky-staging"], True)
        self.assertIsNone(status)
        plan.set_prep_status(self.ws.root, self.writer, self.day, "done", "3 tests")
        self.assertEqual(plan.load_prep(self.ws.root, self.day)[1]["state"], "done")

    def test_refusals(self):
        bad = []
        p = good_prep(self.day); p["day"] = "2020-01-01"; bad.append(p)
        p = good_prep(self.day); p["tests"][0]["cases"][0]["status"] = "maybe"; bad.append(p)
        p = good_prep(self.day); p["tests"][0]["cases"][0]["why"] = ["see https://drive.google.com/x"]; bad.append(p)
        p = good_prep(self.day); p["tests"][0]["cases"][0]["targets"] = ["PIXEL"]; bad.append(p)
        p = good_prep(self.day); p["tests"][0]["task_id"] = "0000fake005"; bad.append(p)
        p = good_prep(self.day); p["tests"][0]["dev_tickets"][0]["cl"] = "CL-80008"; bad.append(p)
        p = good_prep(self.day); p["tests"][0]["cases"][0]["engine_cases"] = ["../x"]; bad.append(p)
        for obj in bad:
            with self.assertRaises(plan.PrepError):
                plan.put_prep(self.ws.root, self.writer, self.day, obj)

    def test_notes_carry_the_lists_case_fingerprint(self):
        entry = next(t for t in plan.load(self.ws.root, self.day)["tests"] if t["task_id"] == "t-upg")
        self.assertEqual(len(entry["cases_sig"]), 16)
        clean = plan.put_prep(self.ws.root, self.writer, self.day, good_prep(self.day))
        self.assertEqual(clean["tests"][0]["cases_sig"], entry["cases_sig"])
        forged = good_prep(self.day)
        forged["tests"][0]["cases_sig"] = "0" * 16  # set by the server from the list, never by the writer
        self.assertEqual(plan.put_prep(self.ws.root, self.writer, self.day, forged)["tests"][0]["cases_sig"],
                         entry["cases_sig"])
        swapped = [{"n": 1, "text": "b"}, {"n": 2, "text": "a"}]
        self.assertNotEqual(plan.cases_sig([{"n": 1, "text": "a"}, {"n": 2, "text": "b"}]), plan.cases_sig(swapped))

    def test_needs_a_snapshot_for_the_day(self):
        with self.assertRaises(plan.PrepError):
            plan.put_prep(self.ws.root, self.writer, "2020-01-01", good_prep("2020-01-01"))

    def test_text_is_redacted(self):
        p = good_prep(self.day)
        p["tests"][0]["cases"][0]["note"] = "ask colleague@example.invalid"
        clean = plan.put_prep(self.ws.root, self.writer, self.day, p)
        self.assertNotIn("@company.example", json.dumps(clean))


class EndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ws = Workspace()
        cls.srv = serve.build_server(cls.ws.root, 0)
        cls.port = cls.srv.qa_port
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        clickup_read.urllib.request.urlopen = REAL_URLOPEN
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.ws.cleanup()

    def _req(self, method, path, body=None, token=True):
        base = f"http://127.0.0.1:{self.port}"
        headers = {"Host": f"127.0.0.1:{self.port}"}
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

    def test_get_plan_before_any_refresh(self):
        status, obj = self._req("GET", "/api/plan")
        self.assertEqual(status, 200)
        self.assertIn("today", obj)

    def test_refresh_needs_the_page_token(self):
        status, _ = self._req("POST", "/api/plan/refresh", token=False)
        self.assertEqual(status, 403)

    def test_refresh_with_fake_clickup(self):
        clickup_read.urllib.request.urlopen = FakeOpener()
        try:
            status, obj = self._req("POST", "/api/plan/refresh")
        finally:
            clickup_read.urllib.request.urlopen = REAL_URLOPEN
        self.assertEqual(status, 200)
        self.assertTrue(obj["ok"])
        self.assertEqual([t["task_id"] for t in obj["snapshot"]["tests"]], ["t-high", "t-upg", "t-low"])
        self.assertNotIn(TOKEN, json.dumps(obj))


if __name__ == "__main__":
    unittest.main(verbosity=1)
