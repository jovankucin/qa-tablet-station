"""Tests of File ticket (2026-11-03): the Touch ID approval (approvals.py), POST /api/tickets/file (serve.py), the
worker request type "file" (worker.py, writes.Writer) and the ticket fields on the Results cards (views.py).

python3 -I qa/app/tests/test_ticket_filing.py [-v]

Every test works in a temp workspace. The Touch ID prompt is always a fake (FakePrompt): no test compiles or runs
touchid.swift, touches a device, ClickUp or the network.
"""
import datetime
import hashlib
import hmac
import http.client
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(TESTS_DIR)
sys.path.insert(0, APP_DIR)
sys.path.insert(0, os.path.join(os.path.dirname(APP_DIR), "runner"))

import approvals  # noqa: E402
import data as data_model  # noqa: E402
import gate  # noqa: E402  (at import time: test_write_guard later puts the live qa/runner first on sys.path)
import records  # noqa: E402
import serve  # noqa: E402
import views  # noqa: E402
import worker  # noqa: E402
import writes  # noqa: E402

CAMP = "2026-11-03-filing"
RID = "R-20261103-TA5-TERR-001-01"          # FAIL on the CT account (the fixture's engine account), online
OFF = "R-20261103-ET65-TERR-001-01"         # FAIL on the CT account, the app showed offline at ready
WEB = "R-20261103-W044-TERR-001-01"         # FAIL on W044
PART = "R-20261103-TA5-TERR-002-01"         # PARTIAL with a failed check
BLK = "R-20261103-TA5-TERR-003-01"          # BLOCKED without a failed step
PASS = "R-20261103-TA5-TERR-004-01"         # PASS
OTHER = "R-20261103-TA5-TERR-005-01"        # FAIL on another account
TA3 = "R-20261103-TA3-TERR-001-01"          # FAIL on TA3 (not a filing target)
CORR = "R-20261103-TA5-TERR-006-01"         # FAIL, corrected to ERROR by CORR + "c1"
TASK = "T-20261103-001"
TITLE = "[CT] Maps: terrain import shows no rows (DSM upload on TA5)"
DESC = "Issue Summary\nRows missing.\n\nSteps to Reproduce\n1. Import.\n\nExpected Result\nRows.\n\nActual Result\nNone.\n"
ONLINE_READY = ["woke the screen"]
OFFLINE_READY = ["the app shows it is offline (no network): check the Wi-Fi"]
FAILED_STEP = {"n": 2, "assert": {"kind": "scripted", "expr": "rows > 0", "result": False, "by": "script"},
               "readback": {"expected": [], "got": None, "ok": True}}
OK_STEP = {"n": 1, "readback": {"expected": ["x"], "got": {"x": True}, "ok": True}}


def put(root, rel, content):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if isinstance(content, (dict, list)):
        content = records.canonical(content)
    with open(path, "wb") as f:
        f.write(content.encode("utf-8") if isinstance(content, str) else content)
    return path


def run_rec(rid, verdict, account="shared-stag", acc_st="verified", steps=(OK_STEP,), ready=ONLINE_READY,
            supersedes=None):
    tag = rid.split("-")[2]
    target = {"class": "web", "host": "webpc044"} if tag == "W044" else {"class": "tablet", "tag": tag, "serial": "S1"}
    return {"kind": "run", "schema": 1, "id": rid, "created": "2026-11-03T08:10:00.000Z", "author": "runner",
            "supersedes": supersedes, "mode": "live", "case": rid.split("-")[3] + "-" + rid.split("-")[4],
            "case_file_sha256": "a" * 64, "target": target,
            "build": {"hash7": {"v": "abc1234", "st": "verified"}, "semver": {"v": "1.80.0", "st": "verified"},
                      "env": {"v": "staging", "st": "verified"}},
            "account": {"v": account, "st": acc_st, "src": []},
            "attempts": [{"n": 1, "started": "2026-11-03T08:00:00.000Z", "ended": "2026-11-03T08:09:00.000Z",
                          "identity": [{"verb": "ready", "checks": [], "actions": list(ready)}],
                          "steps": list(steps), "evidence": []}],
            "verdict": {"v": verdict, "decided": "2026-11-03T08:09:00.000Z", "evidence_kind": "device"}}


class FakePrompt:
    """The Touch ID prompt stand-in: answers code (0 yes, 1 cancel, 2 unavailable, None no answer) and keeps reasons."""

    def __init__(self, code=0, side=None):
        self.code, self.side, self.reasons = code, side, []

    def __call__(self, reason):
        self.reasons.append(reason)
        if self.side:
            self.side()
        return self.code


class Workspace:
    def __init__(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="qa-filing-test-"))
        self.root = os.path.join(self.tmp, "ws")
        os.makedirs(self.root)
        put(self.root, "qa/runner/engine.json", {"env": "staging", "account": "shared-stag"})
        for rid, verdict, kw in ((RID, "FAIL", {}), (OFF, "FAIL", {"ready": OFFLINE_READY}), (WEB, "FAIL", {}),
                                 (PART, "PARTIAL", {"steps": (OK_STEP, FAILED_STEP)}), (BLK, "BLOCKED", {}),
                                 (PASS, "PASS", {}), (OTHER, "FAIL", {"account": "someone-else"}),
                                 (TA3, "FAIL", {}), (CORR, "FAIL", {})):
            put(self.root, f"qa/runs/{CAMP}/{rid}.json", run_rec(rid, verdict, **kw))
        put(self.root, f"qa/runs/{CAMP}/{CORR}c1.json", run_rec(CORR + "c1", "ERROR", supersedes=CORR))
        put(self.root, f"qa/tasks/{TASK}.json", {"kind": "task", "schema": 1, "id": TASK, "state": "results_ready",
                                                  "created": "2026-11-03T07:59:00.000Z", "runs": [RID, OFF]})
        self.n = 0

    def draft(self, rid, title=TITLE, desc=DESC, **extra):
        self.n += 1
        d = f"tests/{CAMP}/tickets/{self.n}"
        put(self.root, f"{d}/title.txt", title + "\n")
        put(self.root, f"{d}/description.txt", desc)
        dj = {"source": f"qa/runs/{CAMP}/{rid}.json", "tablet_name": "TA5 SM-X300", "finding": "confirmed"}
        if rid is not None:
            dj["run_record"] = f"qa/runs/{CAMP}/{rid}.json"
        dj.update(extra)
        put(self.root, f"{d}/draft.json", dj)
        return d

    def path(self, rel):
        return os.path.join(self.root, *rel.split("/"))

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


def approvals_on_disk(root):
    d = approvals.approvals_dir(root)
    return sorted(n for n in os.listdir(d) if n.endswith(".json")) if os.path.isdir(d) else []


def file_requests(root):
    return [r for r in reversed(worker.list_requests(root)) if r.get("type") == "file"]


# ---- approvals.py ---------------------------------------------------------------------------------------------------

class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.root = self.ws.root
        self.dir = self.ws.draft(RID)

    def tearDown(self):
        self.ws.close()

    def approve(self, code=0, **kw):
        prompt = FakePrompt(code)
        return approvals.approve(self.root, self.dir, "TA5", "File the ticket 'x' on TA5", prompt=prompt, **kw), prompt

    def test_approve_writes_the_signed_approval_of_the_contract(self):
        now = datetime.datetime(2026, 11, 3, 9, 15, 7, 123456, tzinfo=datetime.timezone.utc)
        a, prompt = self.approve(now=now)
        self.assertEqual(prompt.reasons, ["File the ticket 'x' on TA5"])
        self.assertEqual(sorted(a), sorted(approvals.FIELDS))
        self.assertRegex(a["id"], r"^A-20261103091507-[0-9a-f]{6}\Z")
        self.assertEqual((a["ticket_dir"], a["target"], a["by"]), (self.dir, "TA5", "touch_id"))
        self.assertEqual((a["created"], a["expires"]), ("2026-11-03T09:15:07Z", "2026-11-03T09:45:07Z"))
        with open(self.ws.path(self.dir + "/title.txt"), "rb") as f:
            title = f.read()
        with open(self.ws.path(self.dir + "/description.txt"), "rb") as f:
            desc = f.read()
        self.assertEqual(a["draft_sha256"], hashlib.sha256(title + b"\n" + desc).hexdigest())
        # the signature as the gate recomputes it: HMAC-SHA256 with the key's 32 bytes over the compact sorted JSON
        key_file = approvals.key_path(self.root)
        self.assertEqual(key_file, os.path.join(self.root, "qa", "runner", "state", "approval.key"))
        with open(key_file, encoding="ascii") as f:
            key_hex = f.read().strip()
        self.assertRegex(key_hex, r"^[0-9a-f]{64}\Z")
        rest = {k: v for k, v in a.items() if k != "sig"}
        want = hmac.new(bytes.fromhex(key_hex), json.dumps(rest, sort_keys=True, separators=(",", ":")).encode(),
                        hashlib.sha256).hexdigest()
        self.assertEqual(a["sig"], want)
        path = os.path.join(self.root, "qa", "runner", "state", "approvals", a["id"] + ".json")
        with open(path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), a)
        for p in (path, key_file):
            self.assertEqual(stat.S_IMODE(os.lstat(p).st_mode), 0o600, p)
        self.assertEqual(stat.S_IMODE(os.lstat(os.path.dirname(path)).st_mode), 0o700)
        self.assertEqual(os.listdir(os.path.dirname(path)), [a["id"] + ".json"])  # no temp file left
        self.assertEqual(approvals.load_approval(self.root, a["id"]), a)
        self.assertIsNone(approvals.verify(self.root, a, now=now))

    def test_cancel_and_other_noes_write_nothing(self):
        for code, msg in ((1, "Not approved: Touch ID was cancelled"),
                          (2, "Not approved: Touch ID and the account password are not available on this Mac"),
                          (None, "Not approved: no answer to the Touch ID prompt within 90 s"),
                          (7, "Not approved: the Touch ID prompt failed (exit 7)")):
            with self.assertRaises(approvals.ApprovalError) as cm:
                self.approve(code)
            self.assertEqual((cm.exception.status, cm.exception.message), (403, msg))
        self.assertEqual(approvals_on_disk(self.root), [])

    def test_a_bad_draft_is_refused_before_the_prompt(self):
        prompt = FakePrompt(0)
        cases = []
        os.remove(self.ws.path(self.dir + "/description.txt"))
        cases.append((self.dir, 404))
        d2 = self.ws.draft(RID)
        os.remove(self.ws.path(d2 + "/title.txt"))
        os.symlink(self.ws.path(self.dir + "/draft.json"), self.ws.path(d2 + "/title.txt"))
        cases.append((d2, 400))
        d3 = self.ws.draft(RID)
        os.symlink(self.ws.path(d3), self.ws.path(f"tests/{CAMP}/tickets/99"))
        cases.append((f"tests/{CAMP}/tickets/99", 400))
        d4 = self.ws.draft(RID)
        put(self.root, d4 + "/draft.json", "[1, 2]")
        cases.append((d4, 400))
        for bad in ("tests/../qa/tickets/1", f"tests/{CAMP}/tickets/1/", "/etc/tickets/1", f"tests/{CAMP}/x/1", None):
            cases.append((bad, 400))
        cases.append((f"tests/{CAMP}/tickets/404", 404))
        for d, status in cases:
            with self.assertRaises(approvals.ApprovalError, msg=d) as cm:
                approvals.approve(self.root, d, "TA5", "reason", prompt=prompt)
            self.assertEqual(cm.exception.status, status, d)
        for target in ("TA3", "ta5", None):
            with self.assertRaises(approvals.ApprovalError):
                approvals.approve(self.root, d4, target, "reason", prompt=prompt)
        for reason in ("", "two\nlines", "x" * 201, None):
            with self.assertRaises(approvals.ApprovalError):
                approvals.approve(self.root, self.ws.draft(RID), "TA5", reason, prompt=prompt)
        self.assertEqual(prompt.reasons, [])
        self.assertEqual(approvals_on_disk(self.root), [])

    def test_the_draft_changing_during_the_prompt_writes_nothing(self):
        prompt = FakePrompt(0, side=lambda: put(self.root, self.dir + "/description.txt", DESC + "more\n"))
        with self.assertRaises(approvals.ApprovalError) as cm:
            approvals.approve(self.root, self.dir, "TA5", "reason", prompt=prompt)
        self.assertEqual(cm.exception.status, 409)
        self.assertEqual(approvals_on_disk(self.root), [])

    def test_verify_signature(self):
        a, _ = self.approve()
        self.assertIsNone(approvals.verify(self.root, a))
        for k, v in (("target", "ET65"), ("ticket_dir", f"tests/{CAMP}/tickets/7"), ("draft_sha256", "0" * 64),
                     ("sig", "0" * 64)):
            self.assertEqual(approvals.verify(self.root, dict(a, **{k: v})), "the signature does not match", k)
        other = dict(a, sig=hmac.new(b"\x01" * 32, approvals.payload(a), hashlib.sha256).hexdigest())
        self.assertEqual(approvals.verify(self.root, other), "the signature does not match")
        self.assertIn("fields", approvals.verify(self.root, dict(a, extra="x")))
        self.assertIn("fields", approvals.verify(self.root, {k: v for k, v in a.items() if k != "by"}))
        self.assertIn("form", approvals.verify(self.root, dict(a, by="password")))

    def test_verify_expiry(self):
        t0 = datetime.datetime(2026, 11, 3, 9, 0, 0, tzinfo=datetime.timezone.utc)
        a, _ = self.approve(now=t0)
        self.assertIsNone(approvals.verify(self.root, a, now=t0 + datetime.timedelta(minutes=29, seconds=59)))
        self.assertEqual(approvals.verify(self.root, a, now=t0 + datetime.timedelta(minutes=30)), "the approval expired")
        self.assertEqual(approvals.verify(self.root, a, now=t0 - datetime.timedelta(minutes=5)),
                         "the approval is from the future")
        # a longer life, even signed with the right key, is not an approval
        key = approvals.load_key(self.root, create=False)
        long = dict(a, expires="2026-11-03T10:00:00Z")
        long["sig"] = approvals.sign(key, long)
        self.assertIn("30 minutes", approvals.verify(self.root, long, now=t0))

    def test_verify_draft_change_and_used_marker(self):
        a, _ = self.approve()
        put(self.root, self.dir + "/title.txt", TITLE + " (edited)\n")
        self.assertEqual(approvals.verify(self.root, a), "the draft changed after the approval")
        put(self.root, self.dir + "/title.txt", TITLE + "\n")
        self.assertIsNone(approvals.verify(self.root, a))
        put(self.root, f"qa/runner/state/approvals/{a['id']}.used", "")
        self.assertTrue(approvals.is_used(self.root, a["id"]))
        self.assertEqual(approvals.verify(self.root, a), "the approval was used")

    def test_key_made_once_and_a_loose_key_is_refused_before_the_prompt(self):
        a, _ = self.approve()
        b, _ = self.approve()
        self.assertNotEqual(a["id"], b["id"])
        # security review 2026-11-03: the newer approval of the same ticket retires the older one (test_ticket_security)
        self.assertEqual(approvals.verify(self.root, a), "the approval was used")
        self.assertIsNone(approvals.verify(self.root, b))
        os.chmod(approvals.key_path(self.root), 0o644)
        prompt = FakePrompt(0)
        with self.assertRaises(approvals.ApprovalError) as cm:
            approvals.approve(self.root, self.dir, "TA5", "reason", prompt=prompt)
        self.assertEqual(cm.exception.status, 500)
        self.assertEqual(prompt.reasons, [])
        self.assertIn("0600", approvals.verify(self.root, a))
        os.chmod(approvals.key_path(self.root), 0o600)
        put(self.root, "qa/runner/state/approval.key", "not hex\n")
        os.chmod(approvals.key_path(self.root), 0o600)
        with self.assertRaises(approvals.ApprovalError):
            approvals.load_key(self.root)

    def test_a_symlinked_approvals_folder_is_refused(self):
        os.makedirs(os.path.join(self.root, "qa", "runner", "state"), exist_ok=True)
        elsewhere = os.path.join(self.ws.tmp, "elsewhere")
        os.makedirs(elsewhere)
        os.symlink(elsewhere, approvals.approvals_dir(self.root))
        with self.assertRaises(approvals.ApprovalError) as cm:
            self.approve()
        self.assertEqual(cm.exception.status, 500)
        self.assertEqual(os.listdir(elsewhere), [])

    def test_qa_dir_is_records_qa_dir_for_the_real_workspace(self):
        self.assertEqual(approvals.qa_dir(records.WORKSPACE), os.path.realpath(records.QA_DIR))
        self.assertEqual(approvals.qa_dir(self.root), os.path.join(self.root, "qa"))
        self.assertEqual(approvals.load_approval(self.root, "../x"), None)


class PromptTests(unittest.TestCase):
    """run_touchid and build_helper with subprocess.run replaced: nothing is compiled or run."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="qa-touchid-test-"))
        self.saved = (approvals.BINARY, approvals.SOURCE, approvals.subprocess.run)
        approvals.BINARY = os.path.join(self.tmp, ".touchid")
        approvals.SOURCE = os.path.join(self.tmp, "touchid.swift")
        with open(approvals.SOURCE, "w") as f:
            f.write("// source\n")
        self.calls = []

    def tearDown(self):
        approvals.BINARY, approvals.SOURCE, approvals.subprocess.run = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fake_run(self, rc=0, timeout=False, make=True):
        def run(args, **kw):
            self.calls.append((list(args), kw))
            if timeout:
                raise subprocess.TimeoutExpired(args, kw.get("timeout"))
            if make and args[0] == approvals.SWIFTC:
                with open(args[args.index("-o") + 1], "w") as f:
                    f.write("binary")
            return subprocess.CompletedProcess(args, rc, b"", b"error: line one\nerror: it broke")
        approvals.subprocess.run = run

    def test_the_paths_are_fixed(self):
        self.assertEqual(self.saved[0], os.path.join(APP_DIR, ".touchid"))
        self.assertEqual(self.saved[1], os.path.join(APP_DIR, "touchid.swift"))
        self.assertEqual(approvals.SWIFTC, "/usr/bin/swiftc")
        self.assertTrue(os.path.isfile(self.saved[1]))
        with open(self.saved[1], encoding="utf-8") as f:
            src = f.read()
        self.assertIn("evaluatePolicy(.deviceOwnerAuthentication, localizedReason: args[1])", src)
        for code in ("exit(2)", "exit(answer.approved ? 0 : 1)", "canEvaluatePolicy(.deviceOwnerAuthentication"):
            self.assertIn(code, src)

    def test_build_once_then_again_when_the_source_is_newer(self):
        self.fake_run()
        self.assertEqual(approvals.build_helper(), approvals.BINARY)
        self.assertEqual(self.calls[0][0], [approvals.SWIFTC, "-O", "-o", approvals.BINARY + ".build", approvals.SOURCE])
        self.assertEqual(stat.S_IMODE(os.stat(approvals.BINARY).st_mode), 0o700)
        self.assertFalse(os.path.exists(approvals.BINARY + ".build"))
        approvals.build_helper()
        self.assertEqual(len(self.calls), 1)
        st = os.stat(approvals.BINARY)
        os.utime(approvals.SOURCE, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
        approvals.build_helper()
        self.assertEqual(len(self.calls), 2)

    def test_a_failed_build_is_an_error(self):
        self.fake_run(rc=1, make=False)
        with self.assertRaises(approvals.ApprovalError) as cm:
            approvals.build_helper()
        self.assertEqual(cm.exception.status, 500)
        self.assertIn("error: it broke", cm.exception.message)
        self.assertFalse(os.path.exists(approvals.BINARY))

    def test_run_touchid_runs_the_fixed_program_with_the_reason_and_a_timeout(self):
        with open(approvals.BINARY, "w") as f:
            f.write("binary")
        os.chmod(approvals.BINARY, 0o700)
        for rc in (0, 1, 2):
            self.fake_run(rc=rc)
            self.assertEqual(approvals.run_touchid("File it"), rc)
        args, kw = self.calls[-1]
        self.assertEqual(args, [approvals.BINARY, "File it"])
        self.assertEqual(kw.get("timeout"), 90)
        self.assertIs(kw.get("stdin"), subprocess.DEVNULL)
        self.fake_run(timeout=True)
        self.assertIsNone(approvals.run_touchid("File it"))


# ---- worker.py: the "file" request ----------------------------------------------------------------------------------

def file_fields(**over):
    f = {"type": "file", "ticket_dir": f"tests/{CAMP}/tickets/1", "target": "TA5",
         "approval_id": "A-20261103091507-abc123", "task": TASK, "run": RID}
    f.update(over)
    return f


class WorkerFileTests(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.root = self.ws.root
        self.dir = self.ws.draft(RID)
        self.w = writes.Writer(self.root)

    def tearDown(self):
        self.ws.close()

    def test_type_and_fields_are_validated(self):
        self.assertIn("file", worker.TYPES)
        req = self.w.create_request(file_fields(task=None))
        self.assertEqual((req["type"], req["state"], req["task"]), ("file", "new", None))
        bad = [{k: v for k, v in file_fields().items() if k != "run"}, file_fields(extra=1),
               file_fields(target="TA3"), file_fields(approval_id="A-1"), file_fields(ticket_dir="tests/x/tickets/1"),
               file_fields(ticket_dir=f"tests/{CAMP}/tickets/1/../2"), file_fields(task="T-1"), file_fields(run="x y"),
               file_fields(run=None)]
        for f in bad:
            with self.assertRaises(writes.WriteError, msg=f) as cm:
                self.w.create_request(f)
            self.assertEqual(cm.exception.status, 400, f)
        self.assertEqual(len(file_requests(self.root)), 1)

    def test_wait_returns_it_and_the_life_cycle_is_claim_note_done_or_fail(self):
        req = self.w.create_request(file_fields(ticket_dir=self.dir))
        out = io.StringIO()
        self.assertEqual(worker.wait(self.root, "23:59", out=out, sleep=lambda s: None), 0)
        self.assertEqual(json.loads(out.getvalue())["id"], req["id"])
        self.assertEqual(json.loads(out.getvalue())["type"], "file")
        worker.claim(self.root, req["id"])
        worker.note(self.root, req["id"], "TA5 online; filing with sak-submit")
        with self.assertRaises(worker.WorkerError) as cm:
            worker.finish(self.root, req["id"], "done")
        self.assertEqual(cm.exception.status, 409)  # done only after submitted.json
        put(self.root, self.dir + "/submitted.json", {"cl_id": "CL-12345", "title": TITLE})
        done = worker.finish(self.root, req["id"], "done")
        self.assertEqual(done["state"], "done")
        other = self.w.create_request(file_fields(ticket_dir=self.dir))
        worker.claim(self.root, other["id"])
        failed = worker.finish(self.root, other["id"], "failed", worker.OFFLINE_NOTE.format(tag="TA5"))
        self.assertEqual(failed["notes"][-1]["text"], "Connect TA5 to the internet, then press File ticket again.")
        self.assertRegex(failed["notes"][-1]["text"], worker.OFFLINE_NOTE_RE)

    def test_an_expired_new_request_is_failed_and_one_not_taken_can_be_cancelled(self):
        req = self.w.create_request(file_fields(ticket_dir=self.dir))
        later = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=31)
        self.assertEqual(worker.expire(self.root, now=later), [req["id"]])
        r = worker.load_request(self.root, req["id"])
        self.assertEqual((r["state"], r["notes"][-1]["text"]), ("failed", worker.EXPIRED_NOTE))
        req2 = self.w.create_request(file_fields(ticket_dir=self.dir))
        self.assertEqual(worker.cancel(self.root, req2["id"])["state"], "cancelled")

    def test_file_conflict(self):
        self.assertIsNone(self.w.file_conflict(self.dir))
        req = self.w.create_file_request(file_fields(ticket_dir=self.dir))
        self.assertEqual(self.w.file_conflict(self.dir), f"{req['id']} for this ticket is still open")
        with self.assertRaises(writes.WriteError) as cm:
            self.w.create_file_request(file_fields(ticket_dir=self.dir))
        self.assertEqual(cm.exception.status, 409)

        def age(r):
            r["created"] = "2026-01-01T00:00:00.000Z"
        self.w.update_request(req["id"], age)
        self.assertIsNone(self.w.file_conflict(self.dir))  # the expired one is failed on the spot
        r = worker.load_request(self.root, req["id"])
        self.assertEqual((r["state"], r["notes"][-1]["text"]), ("failed", worker.EXPIRED_NOTE))
        put(self.root, self.dir + "/filed.json", {"cl": "CL-1234"})
        self.assertEqual(self.w.file_conflict(self.dir), "this ticket is already marked as filed")
        put(self.root, self.dir + "/submitted.json", {"cl_id": "CL-1234"})
        self.assertEqual(self.w.file_conflict(self.dir), "this ticket is already filed (submitted.json)")


# ---- serve.py: POST /api/tickets/file -------------------------------------------------------------------------------

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

    def file(self, d, **kw):
        return self.request("POST", "/api/tickets/file", {"dir": d}, **kw)

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()
        serve.Handler.log_message = self._log


class ServeTests(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.root = self.ws.root
        self.s = Server(self.root)
        self.prompt = FakePrompt(0)
        self.s.srv.touchid_prompt = self.prompt

    def tearDown(self):
        self.s.close()
        self.ws.close()

    def test_approved_creates_the_file_request(self):
        d = self.ws.draft(RID)
        status, out = self.s.file(d + "/")
        self.assertEqual(status, 200, out)
        self.assertEqual(sorted(out), ["approval_id", "request"])
        req = out["request"]
        self.assertEqual({k: req[k] for k in worker.FILE_FIELDS},
                         {"type": "file", "ticket_dir": d, "target": "TA5", "approval_id": out["approval_id"],
                          "task": TASK, "run": RID})
        self.assertEqual(req["state"], "new")
        self.assertEqual(worker.load_request(self.root, req["id"])["approval_id"], out["approval_id"])
        a = approvals.load_approval(self.root, out["approval_id"])
        self.assertEqual((a["ticket_dir"], a["target"]), (d, "TA5"))
        self.assertIsNone(approvals.verify(self.root, a))
        self.assertEqual(self.prompt.reasons,
                         [f"File the ticket '{TITLE}' with the Swiss Army Knife on TA5"])

    def test_reason_title_is_cut_at_60(self):
        long = "[CT] Maps: " + "terrain rows are missing after import " * 3
        d = self.ws.draft(RID, title=long + "\x07")
        self.assertEqual(self.s.file(d)[0], 200)
        shown = self.prompt.reasons[0].split("'")[1]
        self.assertEqual(len(shown), 60)
        self.assertTrue(shown.endswith("…"))
        self.assertTrue(long.startswith(shown[:-1]))

    def test_web_and_partial_runs_file_too(self):
        status, out = self.s.file(self.ws.draft(WEB))
        self.assertEqual((status, out["request"]["target"], out["request"]["task"]), (200, "W044", None))
        status, out = self.s.file(self.ws.draft(PART))
        self.assertEqual((status, out["request"]["target"]), (200, "TA5"))
        self.assertTrue(self.prompt.reasons[0].endswith("on W044"))

    def test_cancel_is_403_and_writes_nothing(self):
        self.s.srv.touchid_prompt = FakePrompt(1)
        status, out = self.s.file(self.ws.draft(RID))
        self.assertEqual((status, out), (403, {"error": "Not approved: Touch ID was cancelled"}))
        self.assertEqual(file_requests(self.root), [])
        self.assertEqual(approvals_on_disk(self.root), [])

    def test_409_open_request_filed_and_a_prompt_already_open(self):
        d = self.ws.draft(RID)
        self.assertEqual(self.s.file(d)[0], 200)
        status, out = self.s.file(d)
        self.assertEqual(status, 409)
        self.assertIn("still open", out["error"])
        self.assertEqual(len(self.prompt.reasons), 1)  # the person is not asked for nothing
        req = file_requests(self.root)[0]
        worker.claim(self.root, req["id"])
        put(self.root, d + "/submitted.json", {"cl_id": "CL-23456"})
        worker.finish(self.root, req["id"], "done")
        status, out = self.s.file(d)
        self.assertEqual((status, out["error"]), (409, "this ticket is already filed (submitted.json)"))
        d2 = self.ws.draft(RID)
        put(self.root, d2 + "/filed.json", {"cl": "CL-34567"})
        self.assertEqual(self.s.file(d2)[0], 409)
        d3 = self.ws.draft(RID)
        self.assertTrue(self.s.srv.filing.acquire(blocking=False))
        try:
            status, out = self.s.file(d3)
        finally:
            self.s.srv.filing.release()
        self.assertEqual((status, out["error"]), (409, "a File ticket prompt is already open on this Mac"))
        self.assertEqual(len(self.prompt.reasons), 1)

    def test_an_expired_request_does_not_hold_the_ticket(self):
        d = self.ws.draft(RID)
        old = self.s.file(d)[1]["request"]

        def age(r):
            r["created"] = "2026-01-01T00:00:00.000Z"
        writes.Writer(self.root).update_request(old["id"], age)
        status, out = self.s.file(d)
        self.assertEqual(status, 200, out)
        self.assertNotEqual(out["request"]["id"], old["id"])
        self.assertEqual(worker.load_request(self.root, old["id"])["state"], "failed")

    def test_400_not_a_failed_ct_run(self):
        drafts = {"pass": self.ws.draft(PASS), "blocked": self.ws.draft(BLK), "other account": self.ws.draft(OTHER),
                  "TA3": self.ws.draft(TA3), "corrected": self.ws.draft(CORR), "no run": self.ws.draft(None),
                  "unknown run": self.ws.draft("R-20261103-TA5-NOPE-001-01")}
        for why, d in drafts.items():
            status, out = self.s.file(d)
            self.assertEqual(status, 400, (why, out))
        for body in ({"dir": "tests/x/tickets/1"}, {"dir": 5}, {}, {"dir": f"tests/{CAMP}/tickets/1/../2"}):
            self.assertEqual(self.s.request("POST", "/api/tickets/file", body)[0], 400, body)
        self.assertEqual(self.s.file(f"tests/{CAMP}/tickets/77")[0], 404)
        self.assertEqual(self.prompt.reasons, [])
        self.assertEqual(file_requests(self.root), [])

    def test_the_engine_account_decides_not_the_ct_label(self):
        """2026-11-04: the engine account became qa-stag. Its verified runs file; a run on the earlier engine account
        (CT), an unverified run, and any run while engine.json names no account do not."""
        put(self.root, "qa/runner/engine.json", {"env": "staging", "account": "qa-stag"})
        jk, unv = "R-20261104-TA5-TERR-001-01", "R-20261104-TA5-TERR-002-01"
        put(self.root, f"qa/runs/{CAMP}/{jk}.json", run_rec(jk, "FAIL", account="qa-stag"))
        put(self.root, f"qa/runs/{CAMP}/{unv}.json", run_rec(unv, "FAIL", account="qa-stag", acc_st="unverified"))
        d = data_model.Data(self.root)
        self.assertEqual(serve.run_fileable(d, jk), (jk, "TA5", None))
        for rid in (RID, unv):
            got = serve.run_fileable(d, rid)
            self.assertEqual(got[:2], (rid, None))
            self.assertIn("not on the verified engine account (qa-stag)", got[2])
        status, out = self.s.file(self.ws.draft(jk))
        self.assertEqual((status, out["request"]["run"], out["request"]["target"]), (200, jk, "TA5"), out)
        status, out = self.s.file(self.ws.draft(RID))
        self.assertEqual(status, 400, out)
        put(self.root, "qa/runner/engine.json", {"env": "staging"})
        got = serve.run_fileable(data_model.Data(self.root), jk)
        self.assertEqual(got[:2], (jk, None))
        self.assertIn("(none is set in engine.json)", got[2])

    def test_post_checks_apply(self):
        d = self.ws.draft(RID)
        self.assertEqual(self.s.file(d, token=False)[0], 403)
        self.assertEqual(self.s.request("GET", "/api/tickets/file")[0], 405)
        self.assertEqual(self.prompt.reasons, [])

    def test_files_route_never_serves_the_key_or_an_approval(self):
        aid = self.s.file(self.ws.draft(RID))[1]["approval_id"]
        put(self.root, "qa/runner/state/approval.key.txt", "copy")  # a text-typed neighbour is still served
        for rel in (f"qa/runner/state/approvals/{aid}.json", "qa/runner/state/approval.key",
                    "qa/runner/state/approvals/x.txt", "qa/runner/state/Approvals/" + aid + ".json"):
            with self.assertRaises(serve.security.ReadError, msg=rel) as cm:
                serve.security.resolve_read(self.root, rel, None, None)
            self.assertIn(cm.exception.status, (403, 404), rel)
        self.assertEqual(serve.security.resolve_read(self.root, "qa/runner/state/approval.key.txt", None, None),
                         os.path.join(self.root, "qa", "runner", "state", "approval.key.txt"))


# ---- views.py: ticket fields on the cards ---------------------------------------------------------------------------

class ViewsTests(unittest.TestCase):
    NOW = datetime.datetime(2026, 11, 3, 10, 0, tzinfo=datetime.timezone.utc)

    def state(self, reqs=(), offline=False, **draft):
        return views.ticket_state(draft, list(reqs), offline, now=self.NOW)

    @staticmethod
    def req(state, created="2026-11-03T09:50:00.000Z", note=None, rid="W-20261103-001"):
        return {"id": rid, "type": "file", "state": state, "created": created,
                "notes": [{"at": created, "text": note}] if note else []}

    def test_states(self):
        base = {"cl_id": None, "offline": False, "request": None, "error": None}
        self.assertEqual(self.state(), dict(base, state="draft"))
        self.assertEqual(self.state(offline=True), dict(base, state="waiting_online", offline=True))
        self.assertEqual(self.state([self.req("new")]),
                         dict(base, state="approved", request={"id": "W-20261103-001", "state": "new"}))
        self.assertEqual(self.state([self.req("claimed")])["state"], "filing")
        self.assertEqual(self.state([self.req("claimed")], offline=True)["request"],
                         {"id": "W-20261103-001", "state": "claimed"})
        note = "Connect ET65 to the internet, then press File ticket again."
        self.assertEqual(self.state([self.req("failed", note=note)]),
                         dict(base, state="waiting_online", error=note))
        self.assertEqual(self.state([self.req("failed", note="Account check failed.")], offline=True),
                         dict(base, state="failed", offline=True, error="Account check failed."))
        # the newest request decides; a cancelled one does not count
        self.assertEqual(self.state([self.req("failed", note=note), self.req("cancelled", rid="W-20261103-002")])
                         ["state"], "waiting_online")
        self.assertEqual(self.state([self.req("failed", note=note), self.req("new", rid="W-20261103-002")])
                         ["state"], "approved")
        stale = self.state([self.req("new", created="2026-11-03T09:29:00.000Z")])
        self.assertEqual((stale["state"], stale["request"]), ("failed", None))
        self.assertIn("expired", stale["error"])
        self.assertEqual(self.state([self.req("done")])["state"], "failed")
        filed = self.state([self.req("done")], submitted={"cl_id": "CL-12345"})
        self.assertEqual(filed, dict(base, state="filed", cl_id="CL-12345"))
        self.assertEqual(self.state(filed={"cl": "CL-2345"})["cl_id"], "CL-2345")
        self.assertEqual(self.state(submitted={"cl_id": "not a cl"}), dict(base, state="filed"))

    def test_ready_offline(self):
        self.assertTrue(views.ready_offline(run_rec(OFF, "FAIL", ready=OFFLINE_READY)))
        self.assertFalse(views.ready_offline(run_rec(RID, "FAIL")))
        self.assertFalse(views.ready_offline({"attempts": [{"identity": [{"verb": "probe", "actions": ["offline"]}]}]}))
        self.assertFalse(views.ready_offline({"attempts": "x"}))

    def test_the_cards_carry_the_ticket_fields(self):
        ws = Workspace()
        try:
            root = ws.root
            d_on, d_off, d_filed = ws.draft(RID), ws.draft(OFF), ws.draft(WEB)
            put(root, d_filed + "/submitted.json", {"cl_id": "CL-45678", "title": TITLE})
            w = writes.Writer(root)
            req = w.create_file_request(file_fields(ticket_dir=d_on, approval_id="A-20261103091507-abc123"))
            drafts = views._ticket_drafts(data_model.Data(root))
            on, off, filed = drafts[RID][0], drafts[OFF][0], drafts[WEB][0]
            self.assertEqual((on["state"], on["request"], on["target"], on["offline"]),
                             ("approved", {"id": req["id"], "state": "new"}, "TA5", False))
            self.assertEqual((off["state"], off["offline"], off["target"]), ("waiting_online", True, "ET65"))
            self.assertEqual((filed["state"], filed["cl_id"], filed["filed"]), ("filed", "CL-45678", True))
            worker.claim(root, req["id"])
            worker.finish(root, req["id"], "failed", worker.OFFLINE_NOTE.format(tag="TA5"))
            on = views._ticket_drafts(data_model.Data(root))[RID][0]
            self.assertEqual((on["state"], on["error"]),
                             ("waiting_online", "Connect TA5 to the internet, then press File ticket again."))
            cards = views.result_cards(data_model.Data(root))
            tickets = [t for c in cards["cards"] + cards["older"] for t in c["tickets"]]
            self.assertEqual(sorted(t["dir"] for t in tickets), sorted([d_on, d_off, d_filed]))
            for t in tickets:
                self.assertTrue({"state", "cl_id", "offline", "request", "error", "target"} <= set(t), t)
        finally:
            ws.close()


# ---- the gate reads what approvals.py writes (integration, 2026-11-03) ---------------------------------------------------

class GateReadsApprovalsTests(unittest.TestCase):
    """approvals.approve's file passes qa/runner/gate.py's approval check for sak-submit's two taps with the QA_TICKET
    prefix (the same key path, signature form, draft hash and time format), the Next tap writes the .used marker
    approvals.verify reads, and the gate asks again afterwards. In-process, against the temp workspace."""

    def setUp(self):
        self.gate = gate
        self.ws = Workspace()
        self.root = self.ws.root
        self.dir = self.ws.draft(RID)
        self.saved = gate.QA_DIR, gate.WORKSPACE, gate._now
        gate.QA_DIR, gate.WORKSPACE = os.path.join(self.root, "qa"), self.root

    def tearDown(self):
        self.gate.QA_DIR, self.gate.WORKSPACE, self.gate._now = self.saved
        self.ws.close()

    def taps(self, target="TA5", ticket=None):
        t = ticket or self.dir
        return (f'QA_TICKET={t} python3 -I qa/runner/act.py {target} tap "text:Report bug" '
                f'--expect "sel:css:input[name=title]" --approved',
                f'QA_TICKET={t} python3 -I qa/runner/act.py {target} tap text:Next --expect "gone:text:Next" '
                f'--timeout 15 --approved')

    def decide(self, cmd):
        return self.gate.decide("Bash", {"command": cmd})[0]

    def test_an_app_approval_passes_both_taps_once(self):
        a = approvals.approve(self.root, self.dir, "TA5", "File the ticket 'x' on TA5", prompt=FakePrompt(0))
        report, nxt = self.taps()
        self.assertEqual(self.decide(report), "pass")
        self.assertIsNone(approvals.verify(self.root, a))
        self.assertEqual(self.decide(nxt), "pass")
        self.assertTrue(approvals.is_used(self.root, a["id"]))
        self.assertEqual(approvals.verify(self.root, a), "the approval was used")
        self.assertEqual(self.decide(nxt), "ask")
        self.assertEqual(self.decide(report), "ask")

    def test_other_target_draft_change_and_expiry_ask(self):
        a = approvals.approve(self.root, self.dir, "TA5", "File the ticket 'x' on TA5", prompt=FakePrompt(0))
        for cmd in self.taps(target="ET65"):
            self.assertEqual(self.decide(cmd), "ask", cmd)
        created = datetime.datetime.strptime(a["created"], approvals.TIME_FMT).replace(tzinfo=datetime.timezone.utc)
        self.gate._now = lambda: created + datetime.timedelta(seconds=approvals.VALID_S)
        self.assertEqual(self.decide(self.taps()[0]), "ask")
        self.gate._now = lambda: created + datetime.timedelta(seconds=approvals.VALID_S - 1)
        self.assertEqual(self.decide(self.taps()[0]), "pass")
        put(self.root, f"{self.dir}/description.txt", DESC + "one more line\n")
        self.assertEqual(self.decide(self.taps()[0]), "ask")
        self.assertFalse(approvals.is_used(self.root, a["id"]))

    def test_the_contract_paths_agree(self):
        # the staging gate, next to the records.py approvals.py uses (not the live qa/runner copy)
        self.assertEqual(os.path.dirname(os.path.abspath(gate.__file__)),
                         os.path.dirname(os.path.abspath(approvals.records.__file__)))
        state = os.path.join(self.gate.QA_DIR, "runner", "state")
        self.assertEqual(approvals.key_path(self.root), os.path.join(state, self.gate.APPROVAL_KEY))
        self.assertEqual(approvals.approvals_dir(self.root), os.path.join(state, "approvals"))
        self.assertEqual(set(approvals.FIELDS), self.gate.APPROVAL_FIELDS)
        self.assertEqual(set(approvals.TARGETS), self.gate.APPROVAL_TARGETS)
        self.assertEqual(set(approvals.TARGETS), set(worker.FILE_TARGETS))
        self.assertEqual(approvals.VALID_S, self.gate.APPROVAL_TTL.total_seconds())
        self.assertEqual(approvals.VALID_S, worker.STALE_NEW_S["file"])
        self.assertEqual(approvals.VALID_S, views.FILE_STALE_S)
        for d in (self.dir, "tests/2026-11-03-a-b-9/tickets/12"):
            self.assertTrue(approvals.TICKET_DIR_RE.match(d) and self.gate.TICKET_DIR.match(d)
                            and worker.TICKET_DIR_RE.match(d), d)
        self.assertTrue(views.OFFLINE_NOTE_RE.match(worker.OFFLINE_NOTE.format(tag="W044")))


if __name__ == "__main__":
    unittest.main()
