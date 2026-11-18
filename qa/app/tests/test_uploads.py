"""Tests for test files attached on the Run form (qa/app/uploads.py, /api/files, Prepare with files).

python3 -I qa/app/tests/test_uploads.py  (temporary workspaces, no network, no devices)
"""
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error
import urllib.request

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(TESTS_DIR)
sys.path[:0] = [TESTS_DIR, APP_DIR, os.path.join(os.path.dirname(APP_DIR), "runner")]
import fixtures  # noqa: E402
import plan  # noqa: E402
import uploads  # noqa: E402
import worker  # noqa: E402
import writes  # noqa: E402
from test_plan import REAL_URLOPEN  # noqa: E402
from test_worker import TID, PrepareTests, put, ready_prep  # noqa: E402

DATA = os.urandom(3 * 1024 * 1024 + 123)  # a file of three pieces and a bit


def send(root, task, name, data, piece, tamper=None):
    """Upload data in pieces of piece bytes the way the browser does; returns the last result."""
    res, off = None, 0
    while off < len(data):
        chunk = data[off:off + piece]
        if tamper:
            chunk = tamper(off, chunk)
        res = uploads.put_piece(root, task, name, off, len(data), len(chunk), io.BytesIO(chunk).read)
        off += len(chunk)
    return res


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="qa-uploads-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_upload_in_pieces_lists_and_hashes(self):
        res = send(self.root, TID, "CustomerA_DSM.tif", DATA, 1024 * 1024)
        self.assertTrue(res["done"])
        self.assertEqual(res["file"]["sha256"], hashlib.sha256(DATA).hexdigest())
        (f,) = uploads.list_files(self.root, TID)
        self.assertEqual((f["name"], f["size"], f["path"]),
                         ("CustomerA_DSM.tif", len(DATA), f"tests/test-files-{TID}/files/CustomerA_DSM.tif"))
        # the engine accepts the folder as a test-file source (act.py deliver, autonomy)
        fixtures.source({"name": f["name"], "path": f["path"], "sha256": f["sha256"], "delivery": "auto"}, self.root)

    def test_names_and_types(self):
        for bad in ("../x.tif", "a/b.tif", "x.exe", "x.tif.qa-upload", ".hidden.tif", "x.tif" + fixtures.PARTIAL, ""):
            with self.assertRaises(writes.WriteError):
                uploads.check_name(bad)
        self.assertEqual(uploads.check_name("Site_1.LAZ"), "Site_1.LAZ")
        with self.assertRaises(writes.WriteError):
            uploads.folder_rel("../etc")

    def test_pieces_must_come_in_order(self):
        uploads.put_piece(self.root, TID, "a.tif", 0, 10, 4, io.BytesIO(b"0123").read)
        with self.assertRaisesRegex(writes.WriteError, "out of order"):
            uploads.put_piece(self.root, TID, "a.tif", 8, 10, 2, io.BytesIO(b"89").read)
        uploads.put_piece(self.root, TID, "a.tif", 4, 10, 6, io.BytesIO(b"456789").read)
        self.assertEqual(uploads.list_files(self.root, TID)[0]["size"], 10)

    def test_a_short_piece_is_dropped(self):
        with self.assertRaisesRegex(writes.WriteError, "incomplete"):
            uploads.put_piece(self.root, TID, "a.tif", 0, 10, 10, io.BytesIO(b"0123").read)
        self.assertEqual(os.listdir(os.path.join(self.root, f"tests/test-files-{TID}/files")), [])

    def test_size_limits(self):
        with self.assertRaises(writes.WriteError) as e:
            uploads.put_piece(self.root, TID, "a.tif", 0, uploads.MAX_BYTES + 1, 1, io.BytesIO(b"x").read)
        self.assertEqual(e.exception.status, 413)
        with self.assertRaises(writes.WriteError):
            uploads.put_piece(self.root, TID, "a.tif", 0, 10, uploads.PIECE_MAX + 1, io.BytesIO(b"x").read)

    def test_same_name_twice_is_refused_until_removed(self):
        send(self.root, TID, "a.tif", b"abc", 2)
        with self.assertRaises(writes.WriteError) as e:
            send(self.root, TID, "a.tif", b"abc", 2)
        self.assertEqual(e.exception.status, 409)
        self.assertEqual(uploads.remove(self.root, TID, "a.tif"), {"removed": "a.tif"})
        self.assertEqual(uploads.list_files(self.root, TID), [])
        send(self.root, TID, "a.tif", b"abcd", 2)

    def test_remove_touches_only_attached_files(self):
        folder = os.path.join(self.root, f"tests/test-files-{TID}/files")
        os.makedirs(folder)
        open(os.path.join(folder, "other.tif"), "wb").write(b"not attached")
        with self.assertRaises(writes.WriteError) as e:
            uploads.remove(self.root, TID, "other.tif")
        self.assertEqual(e.exception.status, 404)
        self.assertTrue(os.path.exists(os.path.join(folder, "other.tif")))
        self.assertEqual(uploads.list_files(self.root, TID), [])  # not in the manifest: not listed

    def test_remove_an_unfinished_upload(self):
        uploads.put_piece(self.root, TID, "a.tif", 0, 10, 4, io.BytesIO(b"0123").read)
        self.assertEqual(uploads.remove(self.root, TID, "a.tif"), {"removed": "a.tif", "unfinished": True})
        uploads.put_piece(self.root, TID, "a.tif", 0, 3, 3, io.BytesIO(b"abc").read)  # can start again

    def test_a_changed_file_is_not_listed(self):
        send(self.root, TID, "a.tif", b"abc", 3)
        open(os.path.join(self.root, f"tests/test-files-{TID}/files/a.tif"), "ab").write(b"more")
        self.assertEqual(uploads.list_files(self.root, TID), [])

    def test_a_file_deleted_by_hand_does_not_block_its_name(self):
        send(self.root, TID, "a.tif", b"abc", 3)
        os.remove(os.path.join(self.root, f"tests/test-files-{TID}/files/a.tif"))
        res = send(self.root, TID, "a.tif", b"new", 3)
        self.assertEqual(res["file"]["sha256"], hashlib.sha256(b"new").hexdigest())

    def test_a_failed_manifest_save_leaves_a_removable_upload(self):
        real = uploads._save_manifest

        def boom(path, items):
            raise OSError(28, "No space left on device")
        uploads._save_manifest = boom
        try:
            with self.assertRaises(writes.WriteError) as e:
                send(self.root, TID, "a.tif", b"abc", 3)
            self.assertEqual(e.exception.status, 507)
        finally:
            uploads._save_manifest = real
        folder = os.path.join(self.root, f"tests/test-files-{TID}/files")
        self.assertEqual(sorted(os.listdir(folder)), ["a.tif" + uploads.PART])  # back to unfinished
        self.assertEqual(uploads.remove(self.root, TID, "a.tif"), {"removed": "a.tif", "unfinished": True})
        send(self.root, TID, "a.tif", b"abc", 3)

    def test_stale_unfinished_uploads_are_swept(self):
        uploads.put_piece(self.root, TID, "a.tif", 0, 10, 4, io.BytesIO(b"0123").read)
        part = os.path.join(self.root, f"tests/test-files-{TID}/files/a.tif" + uploads.PART)
        old = os.path.getmtime(part) - uploads.STALE_PART_S - 60
        os.utime(part, (old, old))
        uploads.list_files(self.root, TID)
        self.assertFalse(os.path.exists(part))

    def test_unfinished_uploads_count_toward_the_limit(self):
        real = uploads.MAX_FILES
        uploads.MAX_FILES = 2
        try:
            send(self.root, TID, "a.tif", b"abc", 3)
            uploads.put_piece(self.root, TID, "b.tif", 0, 10, 4, io.BytesIO(b"0123").read)
            with self.assertRaisesRegex(writes.WriteError, "at most 2"):
                uploads.put_piece(self.root, TID, "c.tif", 0, 3, 3, io.BytesIO(b"abc").read)
        finally:
            uploads.MAX_FILES = real


class EndpointAndPrepareTests(PrepareTests):
    """Over HTTP, on the shared fixture: today's test TID with case 1 ready and case 3 needing input."""
    SUMMARY = "Test: TERR-004 and TERR-005 on TA5.\nCreates: 2 maps."

    def setUp(self):
        super().setUp()
        p = ready_prep(self.day)
        p["tests"][0]["cases"][1].update({"status": "needs_input", "engine_cases": ["TERR-005"],
                                         "targets": ["TA5", "ET65"], "why": ["Needs the customer file."],
                                         "needs": [], "side_effects": ["1 DSM map per device"], "estimate_min": 6})
        plan.put_prep(self.root, self.w, self.day, p)
        # the pending case is a draft the worker completes from source with the attached file
        put(self.root, "qa/cases/TERR-005.json", {"kind": "case", "id": "TERR-005", "status": "draft", "targets": "both",
                                                 "steps": [{"n": 1, "text": "import the customer file", "gates": []}]})

    def put(self, task, name, data, offset, total, token=True, ctype="application/octet-stream"):
        base = f"http://127.0.0.1:{self.srv.qa_port}"
        headers = {"Host": f"127.0.0.1:{self.srv.qa_port}", "Origin": base, "Content-Type": ctype}
        if token:
            headers["X-QA-Token"] = self.srv.qa_token
        req = urllib.request.Request(f"{base}/api/files/{task}/{name}?offset={offset}&total={total}", data=data,
                                     method="PUT", headers=headers)
        try:
            with REAL_URLOPEN(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def upload(self, name, data):
        return self.put(TID, name, data, 0, len(data))

    def test_upload_endpoint_checks(self):
        self.assertEqual(self.put(TID, "a.tif", b"abc", 0, 3, token=False)[0], 403)
        self.assertEqual(self.put(TID, "a.tif", b"abc", 0, 3, ctype="application/json")[0], 415)
        self.assertEqual(self.put("t-notinlist", "a.tif", b"abc", 0, 3)[0], 409)
        self.assertEqual(self.put(TID, "a.exe", b"abc", 0, 3)[0], 400)
        status, res = self.upload("CustomerA.tif", b"abc")
        self.assertEqual((status, res["done"], res["file"]["size"]), (200, True, 3))
        status, listed = self._req("GET", f"/api/files/{TID}")
        self.assertEqual((status, [f["name"] for f in listed["files"]]), (200, ["CustomerA.tif"]))

    def test_remove_endpoint(self):
        self.upload("CustomerA.tif", b"abc")
        status, res = self._req("POST", f"/api/files/{TID}/remove", {"name": "CustomerA.tif"})
        self.assertEqual((status, res), (200, {"removed": "CustomerA.tif"}))
        self.assertEqual(self._req("POST", f"/api/files/{TID}/remove", {"name": "CustomerA.tif"})[0], 404)

    def test_without_files_the_pending_case_stays_out(self):
        _, chk = self.prepare(targets=["TA5"])
        self.assertNotIn("pending", chk)
        self.assertEqual(chk["engine_cases"], ["TERR-004"])

    def test_with_files_prepare_offers_the_pending_case_and_run_adds_it(self):
        self.upload("CustomerA.tif", b"abc")
        _, chk = self.prepare(targets=["TA5"], guidance="Import the customer file.")
        self.assertEqual(([c["n"] for c in chk["pending"]], [f["name"] for f in chk["files"]]), ([3], ["CustomerA.tif"]))
        self.assertEqual(chk["pending"][0]["targets"], ["TA5"])
        worker.claim(self.root, chk["id"])
        bad = {"targets": [{"tag": "TA5", "ok": True}], "summary": self.SUMMARY, "complete": ["TERR-004"]}
        with self.assertRaisesRegex(worker.WorkerError, "complete"):  # not a pending case
            worker.answer_check(self.root, chk["id"], bad)
        bad = dict(bad, complete=["TERR-005"], plan_files=[{"name": "Other.tif", "case": "TERR-005"}])
        with self.assertRaisesRegex(worker.WorkerError, "plan_files"):
            worker.answer_check(self.root, chk["id"], bad)
        done = worker.answer_check(self.root, chk["id"], dict(bad, plan_files=[{"name": "CustomerA.tif",
                                                                                "case": "TERR-005"}]))
        self.assertEqual((done["result"]["complete"], done["result"]["plan_files"]),
                         (["TERR-005"], [{"name": "CustomerA.tif", "case": "TERR-005"}]))
        status, start = self._req("POST", "/api/worker/start", {"check": chk["id"], "targets": ["TA5"]})
        self.assertEqual(status, 201)
        self.assertEqual(start["engine_cases"], ["TERR-004", "TERR-005"])
        self.assertEqual([f["name"] for f in start["files"]], ["CustomerA.tif"])
        self.assertEqual(start["files"][0]["sha256"], hashlib.sha256(b"abc").hexdigest())
        task = json.load(open(os.path.join(self.root, "qa/tasks", start["task"] + ".json")))
        self.assertEqual((task["cases"], [f["name"] for f in task["files"]]), (["TERR-004", "TERR-005"], ["CustomerA.tif"]))
        # the file is in use while the run is open
        self.assertEqual(self._req("POST", f"/api/files/{TID}/remove", {"name": "CustomerA.tif"})[0], 409)

    def test_only_the_engine_cases_a_file_completes_join_the_run(self):
        p = ready_prep(self.day)
        p["tests"][0]["cases"][1].update({"status": "needs_input", "engine_cases": ["TERR-005", "TERR-006"],
                                         "targets": ["TA5"], "why": ["Needs two customer files."], "needs": [],
                                         "side_effects": [], "estimate_min": 6})
        plan.put_prep(self.root, self.w, self.day, p)
        self.upload("CustomerA.tif", b"abc")
        _, chk = self.prepare(targets=["TA5"])
        worker.claim(self.root, chk["id"])
        worker.answer_check(self.root, chk["id"], {"targets": [{"tag": "TA5", "ok": True}], "summary": self.SUMMARY,
                                                   "complete": ["TERR-005"],
                                                   "plan_files": [{"name": "CustomerA.tif", "case": "TERR-005"}]})
        _, start = self._req("POST", "/api/worker/start", {"check": chk["id"], "targets": ["TA5"]})
        self.assertEqual((start["engine_cases"], start["completes"]), (["TERR-004", "TERR-005"], ["TERR-005"]))

    def test_the_file_plan_is_strict(self):
        self.upload("CustomerA.tif", b"abc")
        _, chk = self.prepare(targets=["TA5"])
        worker.claim(self.root, chk["id"])
        rows = {"targets": [{"tag": "TA5", "ok": True}], "summary": self.SUMMARY}
        for bad in ({"complete": ["TERR-005"]},  # a completed case needs a file
                    {"complete": ["TERR-005"], "plan_files": [{"name": "CustomerA.tif", "case": "TERR-004"}]},  # ready
                    {"complete": ["TERR-005"], "plan_files": [{"name": ["CustomerA.tif"], "case": "TERR-005"}]},
                    {"complete": [5]}):
            with self.assertRaises(worker.WorkerError):
                worker.answer_check(self.root, chk["id"], dict(rows, **bad))

    def answer_files(self, chk, tags):
        worker.claim(self.root, chk["id"])
        worker.answer_check(self.root, chk["id"], {"targets": [{"tag": t, "ok": True} for t in tags],
                                                   "summary": self.SUMMARY, "complete": ["TERR-005"],
                                                   "plan_files": [{"name": "CustomerA.tif", "case": "TERR-005"}]})

    def test_run_refuses_a_file_changed_since_prepare(self):
        self.upload("CustomerA.tif", b"abc")
        _, chk = self.prepare(targets=["TA5"])
        self.answer_files(chk, ["TA5"])
        self._req("POST", f"/api/files/{TID}/remove", {"name": "CustomerA.tif"})
        self.upload("CustomerA.tif", b"another file")
        status, err = self._req("POST", "/api/worker/start", {"check": chk["id"], "targets": ["TA5"]})
        self.assertEqual((status, err["error"]), (409, "the attached files changed since Prepare summary; prepare again"))

    def test_an_unticked_device_drops_its_completed_case_and_file(self):
        p = ready_prep(self.day)
        p["tests"][0]["cases"][1].update({"status": "needs_input", "engine_cases": ["TERR-005"], "targets": ["TA5"],
                                         "why": ["Needs the customer file."], "needs": [], "side_effects": [],
                                         "estimate_min": 6})
        plan.put_prep(self.root, self.w, self.day, p)
        self.upload("CustomerA.tif", b"abc")
        _, chk = self.prepare(targets=["TA5", "ET65"])
        self.answer_files(chk, ["TA5", "ET65"])
        _, start = self._req("POST", "/api/worker/start", {"check": chk["id"], "targets": ["ET65"]})
        self.assertEqual((start["engine_cases"], start["completes"], start["files"], start["plan_files"]),
                         (["TERR-004"], [], [], []))

    def test_a_pending_case_the_worker_does_not_complete_stays_out(self):
        self.upload("CustomerA.tif", b"abc")
        _, chk = self.prepare(targets=["TA5"])
        self.answer_with(chk["id"], ["TA5"], self.SUMMARY)
        _, start = self._req("POST", "/api/worker/start", {"check": chk["id"], "targets": ["TA5"]})
        self.assertEqual((start["engine_cases"], start["files"]), (["TERR-004"], []))


if __name__ == "__main__":
    unittest.main(verbosity=1)
