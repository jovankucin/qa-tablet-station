"""Security review of File ticket (2026-11-03): attempts to approve a ticket without the person, through the app.

python3 -I qa/app/tests/test_ticket_security.py [-v]

Every test works in a temp workspace with the fake Touch ID prompt of test_ticket_filing (no prompt is ever shown, no
helper is compiled or run, nothing touches a device, ClickUp or the network). The gate side (the helper's build, the
route from a shell, JS clicks, sweeps of the .used markers) is in qa/runner/tests/gate_fixtures_tickets.jsonl; the form
check at the filing Next is in qa/runner/tests/test_ticket_form.py.
"""
import datetime
import json
import os
import sys
import threading
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS_DIR)

import test_ticket_filing as tf  # noqa: E402
from test_ticket_filing import RID, FakePrompt, Server, Workspace, approvals, file_requests, put, worker, writes  # noqa: E402,E501


class SupersedeTests(unittest.TestCase):
    """A second press after a failed request leaves one valid approval, not two (each would file the ticket once)."""

    def setUp(self):
        self.ws = Workspace()
        self.root = self.ws.root
        self.dir = self.ws.draft(RID)

    def tearDown(self):
        self.ws.close()

    def approve(self, d, minute):
        now = datetime.datetime(2026, 11, 3, 9, minute, 0, tzinfo=datetime.timezone.utc)
        return approvals.approve(self.root, d, "TA5", "File the ticket 'x' on TA5", prompt=FakePrompt(0), now=now)

    def test_a_new_approval_retires_the_older_ones_of_the_same_ticket(self):
        other = self.ws.draft(RID)
        first, kept = self.approve(self.dir, 1), self.approve(other, 2)
        second = self.approve(self.dir, 3)
        now = datetime.datetime(2026, 11, 3, 9, 5, 0, tzinfo=datetime.timezone.utc)
        self.assertEqual(approvals.verify(self.root, first, now), "the approval was used")
        self.assertIsNone(approvals.verify(self.root, second, now))
        self.assertIsNone(approvals.verify(self.root, kept, now), "another ticket's approval stays")
        with open(os.path.join(approvals.approvals_dir(self.root), first["id"] + ".used"), encoding="utf-8") as f:
            marker = json.load(f)
        self.assertEqual((marker["by"], marker["superseded_by"]), ("superseded", second["id"]))

    def test_a_marker_the_gate_wrote_is_left_alone(self):
        first = self.approve(self.dir, 1)
        used = os.path.join(approvals.approvals_dir(self.root), first["id"] + ".used")
        with open(used, "w", encoding="utf-8") as f:
            f.write('{"by": "gate"}')
        self.approve(self.dir, 2)
        with open(used, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"by": "gate"})

    def test_the_gate_then_finds_only_the_newest(self):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(TESTS_DIR)), "runner"))
        import gate
        first = self.approve(self.dir, 1)
        second = self.approve(self.dir, 3)
        saved = gate.QA_DIR, gate._now
        gate.QA_DIR = approvals.qa_dir(self.root)
        gate._now = lambda: datetime.datetime(2026, 11, 3, 9, 5, 0, tzinfo=datetime.timezone.utc)
        try:
            found = gate._valid_approval(self.dir, "TA5")
        finally:
            gate.QA_DIR, gate._now = saved
        self.assertIsNotNone(found)
        self.assertEqual(found[1], second["id"])
        self.assertNotEqual(found[1], first["id"])


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.root = self.ws.root
        self.s = Server(self.root)
        self.prompt = FakePrompt(0)
        self.s.srv.touchid_prompt = self.prompt

    def tearDown(self):
        self.s.close()
        self.ws.close()

    def test_only_a_confirmed_finding_asks_for_the_fingerprint(self):
        for finding in ("suspected", "reported", None, ["confirmed"]):
            d = self.ws.draft(RID, finding=finding)
            status, out = self.s.file(d)
            self.assertEqual(status, 400, (finding, out))
            self.assertIn("not confirmed", out["error"])
        self.assertEqual(self.prompt.reasons, [])
        self.assertEqual(file_requests(self.root), [])

    def test_no_token_wrong_token_or_wrong_origin_never_prompts(self):
        d = self.ws.draft(RID)
        self.assertEqual(self.s.file(d, token=False)[0], 403)
        good = self.s.token
        self.s.token = good[:-1] + ("A" if good[-1] != "A" else "B")
        try:
            self.assertEqual(self.s.file(d)[0], 403)
        finally:
            self.s.token = good
        import http.client
        body = json.dumps({"dir": d}).encode()
        host = f"127.0.0.1:{self.s.port}"
        for origin in ("http://evil.example", "null", f"http://localhost:{self.s.port}"):
            c = http.client.HTTPConnection("127.0.0.1", self.s.port, timeout=30)
            try:
                c.request("POST", "/api/tickets/file", body=body, headers={
                    "Host": host, "Origin": origin, "Content-Type": "application/json",
                    "Content-Length": str(len(body)), "X-QA-Token": good})
                self.assertEqual(c.getresponse().status, 403, origin)
            finally:
                c.close()
        self.assertEqual(self.prompt.reasons, [])

    def test_two_presses_at_once_ask_once(self):
        d = self.ws.draft(RID)
        gate_open, release = threading.Event(), threading.Event()

        def slow(reason):
            gate_open.set()
            release.wait(10)
            return 0
        self.s.srv.touchid_prompt = slow
        out = {}
        th = threading.Thread(target=lambda: out.setdefault("first", self.s.file(d)))
        th.start()
        self.assertTrue(gate_open.wait(10))
        second = self.s.file(self.ws.draft(RID))  # another ticket, while the first prompt is open
        same = self.s.file(d)
        release.set()
        th.join(10)
        self.assertEqual(out["first"][0], 200)
        self.assertEqual(second, (409, {"error": "a File ticket prompt is already open on this Mac"}))
        self.assertEqual(same[0], 409)
        self.assertEqual(len(file_requests(self.root)), 1)
        self.assertEqual(len(tf.approvals_on_disk(self.root)), 1)

    def test_a_retry_after_a_failed_request_leaves_one_approval(self):
        d = self.ws.draft(RID)
        first = self.s.file(d)[1]
        req = first["request"]
        worker.claim(self.root, req["id"])
        worker.finish(self.root, req["id"], "failed", worker.OFFLINE_NOTE.format(tag="TA5"))
        status, second = self.s.file(d)
        self.assertEqual(status, 200, second)
        self.assertTrue(approvals.is_used(self.root, first["approval_id"]))
        self.assertFalse(approvals.is_used(self.root, second["approval_id"]))

    def test_unicode_digits_are_not_a_ticket_folder(self):
        for bad in ("tests/٢٠٢٦-11-03-filing/tickets/1", "tests/2026-11-03-filing/tickets/١"):
            self.assertEqual(self.s.request("POST", "/api/tickets/file", {"dir": bad})[0], 400, bad)
            self.assertIsNone(approvals.TICKET_DIR_RE.match(bad))
            self.assertIsNone(worker.TICKET_DIR_RE.match(bad))
        self.assertIsNone(approvals.APPROVAL_ID_RE.match("A-٢٠٢٦1103091507-abcdef"))
        self.assertEqual(self.prompt.reasons, [])


class HelperTests(unittest.TestCase):
    def test_the_prompt_path_and_reason_come_from_the_server_only(self):
        # the program is a fixed path next to approvals.py, run without a shell, with the reason as one argument
        self.assertEqual(approvals.BINARY, os.path.join(approvals.APP_DIR, ".touchid"))
        self.assertEqual(approvals.SWIFTC, "/usr/bin/swiftc")
        ws = Workspace()
        try:
            d = ws.draft(RID)
            for reason in ("two\nlines", "bidi ‮ override", "", " ", "x" * 201, None):
                with self.assertRaises(approvals.ApprovalError) as cm:
                    approvals.approve(ws.root, d, "TA5", reason, prompt=FakePrompt(0))
                self.assertEqual(cm.exception.status, 400, repr(reason))
            with self.assertRaises(approvals.ApprovalError):
                approvals.approve(ws.root, d, "TA3", "File it", prompt=FakePrompt(0))
            self.assertEqual(tf.approvals_on_disk(ws.root), [])
        finally:
            ws.close()


if __name__ == "__main__":
    unittest.main()
