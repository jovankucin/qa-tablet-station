"""Tests for "Needs your input" on the New test plan (2026-11-04, step 1; qa/app/DESIGN.md 8.14): the worker's
result.needs (worker._needs), the person's answers (POST /api/worker/answer, worker.validate_answers), the follow-up
check they make, and the Start refusal while a blocking need is open.

python3 -I qa/app/tests/test_needs.py  (no network, no devices: a temporary workspace and a fake ClickUp)

Here the shapes the page sends and reads are tested on the server side; the page itself (static/app.js: the needs
card, its POST body and the Run rule) is tested in macOS JavaScriptCore by test_needs_page.py.
"""
import contextlib
import datetime
import hashlib
import io
import json
import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(TESTS_DIR)
sys.path[:0] = [TESTS_DIR, APP_DIR, os.path.join(os.path.dirname(APP_DIR), "runner")]
import plan  # noqa: E402
import test_intake  # noqa: E402
import uploads  # noqa: E402
import worker  # noqa: E402
from test_intake import INTAKE_TID  # noqa: E402
from test_worker import put  # noqa: E402

CHOICE = {"id": "N1", "kind": "choice", "text": "Which latitudes should the test DSMs cover?", "blocking": True,
          "options": [{"id": "make", "label": "Make 8 test DSMs"}, {"id": "skip", "label": "Skip latitudes"}]}
FILE = {"id": "N2", "kind": "file", "text": "Attach your own DSM, so the import runs on real data.", "blocking": True,
        "options": [{"id": "make", "label": "Make one for me"}, {"id": "skip", "label": "Skip this part"}],
        "accept": [".tif", ".tiff"]}
TEXT = {"id": "N3", "kind": "text", "text": "Which flight should be kept for offline use?", "blocking": False,
        "options": [{"id": "newest", "label": "The newest flight"}]}
NEEDS = [CHOICE, FILE, TEXT]
DSM, LAS = b"dsm-bytes", b"las-bytes"
MISSING = object()


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Base(test_intake.Base):
    """test_intake's New test fixture (a task read from its link, TERR-004 hands-off on TA5) with helpers for needs."""

    def asked(self, needs=NEEDS, **extra):
        """A done New test plan that asks these needs, with dsm.tif and cloud.las attached before it."""
        self.resolve()
        self.assertEqual(self.put_file(INTAKE_TID, "dsm.tif", DSM, 0, len(DSM))[0], 200)
        self.assertEqual(self.put_file(INTAKE_TID, "cloud.las", LAS, 0, len(LAS))[0], 200)
        return self.planned(needs=needs, **extra)

    def send(self, check, answers=MISSING):
        body = {"check": check}
        if answers is not MISSING:
            body["answers"] = answers
        return self._req("POST", "/api/worker/answer", body)

    def start(self, done, targets=("TA5",)):
        return self._req("POST", "/api/worker/start", {"check": done["id"], "targets": list(targets)})

    def requests(self):
        return worker.list_requests(self.root)

    def task_files(self):
        return [n for n in os.listdir(os.path.join(self.root, "qa", "tasks")) if n.startswith("T-")]


class NeedsTests(Base):
    """The worker's needs in its answer to a New test check (worker.validate_check_result)."""

    def test_a_valid_set_is_stored_and_read_by_the_plan_screen(self):
        needs = [dict(CHOICE, why="dropped"),
                 dict(FILE, text="  Attach your own DSM,\n  or ask ann.smith@company.example  "),
                 dict(TEXT, options=[{"id": "newest", "label": " The  newest\tflight ", "hint": "dropped"}])]
        want = [CHOICE, dict(FILE, text="Attach your own DSM, or ask [EMAIL]"), TEXT]
        done = self.planned(needs=needs)
        self.assertEqual(done["result"]["needs"], want)
        self.assertEqual(worker.load_request(self.root, done["id"])["result"]["needs"], want)
        status, view = self._req("GET", "/api/worker")
        self.assertEqual(status, 200)
        [got] = [r for r in view["requests"] if r["id"] == done["id"]]
        self.assertEqual(got["result"]["needs"], want)
        self.assertEqual(sorted(got["result"]["needs"][1]), ["accept", "blocking", "id", "kind", "options", "text"])

    def test_absent_or_null_is_an_empty_list(self):
        self.assertEqual(self.planned()["result"]["needs"], [])
        self.assertEqual(self.planned(needs=None)["result"]["needs"], [])

    def test_allowed_on_an_empty_plan(self):
        done = self.planned(plan_cases=[], needs=[CHOICE], not_covered=["Save the flight"])
        self.assertEqual((done["result"]["plan_cases"], done["result"]["needs"], done["result"]["not_covered"]),
                         ([], [CHOICE], ["Save the flight"]))

    def test_the_limits_pass(self):
        four = [{"id": f"o{k}" + "x" * 18, "label": "y" * 39 + str(k)} for k in range(4)]
        needs = [{"id": f"N{k}", "kind": "choice", "text": "t" * 300, "blocking": k % 2 == 0, "options": four}
                 for k in range(1, 7)]
        needs += [{"id": "N7", "kind": "file", "text": "Attach it.", "blocking": True,
                   "accept": list(uploads.EXTENSIONS), "options": four},
                  {"id": "N8", "kind": "text", "text": "Say it.", "blocking": False}]
        got = self.planned(needs=needs)["result"]["needs"]
        self.assertEqual([n["id"] for n in got], [f"N{k}" for k in range(1, 9)])
        self.assertEqual((got[0]["options"], got[6]["accept"], got[7]["options"]), (four, list(uploads.EXTENSIONS), []))
        self.assertNotIn("accept", got[7])
        # accept: null counts as absent on a choice or a text need
        got = self.planned(needs=[dict(CHOICE, accept=None), dict(TEXT, accept=None)])["result"]["needs"]
        self.assertEqual(got, [CHOICE, TEXT])

    def test_refusals(self):
        self.resolve()
        _, chk = self.intake_check()
        worker.claim(self.root, chk["id"])
        rows = [{"tag": "TA5", "ok": True}]
        opt = CHOICE["options"]
        bad = [
            ("N1", "needs: a list of at most 8 questions"), ({"id": "N1"}, "needs: a list"),
            ([dict(TEXT, id=f"N{k}") for k in range(1, 10)], "needs: a list of at most 8"),
            ([1], r"needs\[0\]: \{id, kind, text, blocking, options\}"),
            ([TEXT, "N2"], r"needs\[1\]: \{id"),
            *[([dict(TEXT, id=v)], r"needs\[0\]\.id: N1 to N8") for v in ("N0", "N9", "n1", "N10", " N1", "", None, 1)],
            ([TEXT, dict(CHOICE, id="N3")], r"needs\[1\]\.id: N3 more than once"),
            *[([dict(TEXT, kind=v)], r"needs\[0\]\.kind: choice, file or text") for v in ("radio", "Text", None, [])],
            *[([dict(TEXT, text=v)], r"needs\[0\]\.text: text of 1-300")
              for v in (None, "", "   ", "x" * 301, "a\x00b", 7)],
            *[([dict(TEXT, blocking=v)], r"needs\[0\]\.blocking: true or false") for v in ("yes", 1, 0, None)],
            ([{k: v for k, v in TEXT.items() if k != "blocking"}], r"needs\[0\]\.blocking"),
            *[([dict(CHOICE, options=v)], r"needs\[0\]\.options: 2-4 choices")
              for v in (None, [], opt[:1], opt + [{"id": f"x{k}", "label": "x"} for k in range(3)], "make",
                        {"id": "a"})],
            *[([dict(t, options=opt * 2 + opt[:1])], r"needs\[0\]\.options: at most 4 choices") for t in (FILE, TEXT)],
            ([dict(TEXT, options="skip")], r"needs\[0\]\.options: at most 4 choices"),
            ([dict(CHOICE, options=["make", "skip"])], r"needs\[0\]\.options\[0\]\.id: 1-20 of a-z, 0-9, _ and -"),
            *[([dict(CHOICE, options=[opt[0], {"id": v, "label": "x"}])], r"needs\[0\]\.options\[1\]\.id: 1-20 of a-z")
              for v in ("Make", "1x", "_x", "", "a" * 21, "a b", "café", None, 3)],
            ([dict(CHOICE, options=[opt[0], dict(opt[1], id="make")])], r"needs\[0\]\.options\[1\]\.id: make more"),
            *[([dict(CHOICE, options=[opt[0], dict(opt[1], label=v)])],
               r"needs\[0\]\.options\[1\]\.label: text of 1-40") for v in (None, "", "  ", "x" * 41)],
            *[([dict(FILE, accept=v)], r"needs\[0\]\.accept: some of \.tif, \.tiff, \.las, \.laz, \.zip, each once")
              for v in (None, [], ".tif", [".exe"], [".TIF"], ["tif"], [".tif", ".tif"], [None], [[".tif"]])],
            ([{k: v for k, v in FILE.items() if k != "accept"}], r"needs\[0\]\.accept: some of"),
            ([dict(CHOICE, accept=[".tif"])], r"needs\[0\]\.accept: only for a file"),
            ([dict(TEXT, accept=[])], r"needs\[0\]\.accept: only for a file"),
        ]
        for value, why in bad:
            with self.assertRaisesRegex(worker.WorkerError, why) as e:
                worker.answer_check(self.root, chk["id"], {"targets": rows, "summary": test_intake.SUMMARY,
                                                           "plan_cases": [self.plan_case()], "needs": value})
            self.assertEqual(e.exception.status, 400, why)
        self.assertEqual(worker.load_request(self.root, chk["id"])["state"], "claimed")
        done = worker.answer_check(self.root, chk["id"], {"targets": rows, "summary": test_intake.SUMMARY,
                                                          "plan_cases": [self.plan_case()], "needs": NEEDS})
        self.assertEqual(done["result"]["needs"], NEEDS)

    def test_only_for_a_test_from_new_test(self):
        chk = self.check(("TA5",))  # a Today check
        worker.claim(self.root, chk["id"])
        rows = [{"tag": "TA5", "ok": True}]
        with self.assertRaises(worker.WorkerError) as e:
            worker.answer_check(self.root, chk["id"], {"targets": rows, "needs": [CHOICE]})
        self.assertEqual((e.exception.status, e.exception.message), (400, "needs: only for a test from New test"))
        done = worker.answer_check(self.root, chk["id"], {"targets": rows, "needs": []})
        self.assertNotIn("needs", done["result"])


class AnswerTests(Base):
    """POST /api/worker/answer {check, answers}: the follow-up check of the same test."""

    def test_the_answers_make_a_follow_up_check(self):
        done = self.asked()
        late = b"attached after the plan"
        self.assertEqual(self.put_file(INTAKE_TID, "late.tif", late, 0, len(late))[0], 200)
        answers = [{"need": "N1", "choice": "make", "note": "dropped"},
                   {"need": "N2", "file": {"name": "late.tif", "sha256": sha(late), "size": 1}},
                   {"need": "N3", "text": "  The one  named\r\nQA_offline \t ask ann.smith@company.example\r\r\n\n "}]
        status, req = self.send(done["id"], answers)
        self.assertEqual(status, 201, req)
        self.assertEqual((req["type"], req["state"], req["intake"], req["prepare"], req["follows"]),
                         ("check", "new", True, True, done["id"]))
        self.assertEqual(req["answers"], [{"need": "N1", "choice": "make"},
                                          {"need": "N2", "file": {"name": "late.tif", "sha256": sha(late)}},
                                          {"need": "N3", "text": "The one named\nQA_offline ask [EMAIL]"}])
        # exactly the fields of a Prepare plan, plus follows and answers
        self.assertEqual(sorted(set(req) - {"follows", "answers"}), sorted(done))
        for k in ("clickup", "clickup_cases", "guidance", "targets"):
            self.assertEqual(req[k], done[k], k)
        self.assertEqual([req[k] for k in ("cases", "engine_cases", "fix_commits", "case_versions", "result")],
                         [[], [], [], [], None])
        # the test's files now, the one attached after the plan too
        self.assertEqual([f["name"] for f in done["files"]], ["dsm.tif", "cloud.las"])
        self.assertEqual(req["files"], [{k: f[k] for k in ("name", "size", "sha256", "path")}
                                        for f in uploads.list_files(self.root, INTAKE_TID)])
        self.assertEqual([f["name"] for f in req["files"]], ["dsm.tif", "cloud.las", "late.tif"])
        self.assertEqual(worker.load_request(self.root, req["id"]), req)
        # the Plan screen reads it from GET /api/worker
        _, view = self._req("GET", "/api/worker")
        [got] = [r for r in view["requests"] if r["id"] == req["id"]]
        self.assertEqual((got["follows"], got["answers"]), (done["id"], req["answers"]))

    def test_the_follow_up_and_prepare_again_carry_the_clickup_text_as_saved_then(self):
        # 2026-11-10: every New test check request holds the task's ClickUp text (clickup_description), read from the
        # saved copy when the request is made, so a link opened again in between is planned from its newer text
        done = self.asked()
        first = plan.load_intake(self.root, INTAKE_TID)["description"]
        self.assertEqual(done["clickup_description"], first)
        self.opener.obj = test_intake.intake_task(markdown_description="Test setup: both flags off.\n\n*   Download "
                                                                       "the flight\n")
        self.resolve()
        newer = plan.load_intake(self.root, INTAKE_TID)["description"]
        self.assertTrue(newer.startswith("Test setup: both flags off.") and newer != first, newer)
        status, req = self.send(done["id"], [{"need": "N1", "choice": "make"}])
        self.assertEqual((status, req["clickup_description"]), (201, newer))
        self.assertEqual(worker.load_request(self.root, done["id"])["clickup_description"], first)  # the old plan's own
        # Prepare again on the answered plan (answers_from): the new plan request holds it too
        new = self.answer_plan(req["id"], ["TA5"], [self.plan_case()])
        status, again = self.intake_check(targets=["TA5"], answers_from=new["id"])
        self.assertEqual((status, again["clickup_description"], again["follows"]), (201, newer, done["id"]))

    def test_the_waiting_worker_takes_it_and_answers_with_a_new_plan(self):
        done = self.asked()
        _, req = self.send(done["id"], [{"need": "N2", "file": {"name": "dsm.tif", "sha256": sha(DSM)}}])
        out = io.StringIO()
        self.assertEqual(worker.wait(self.root, "23:59", out=out, sleep=lambda s: None), 0)
        self.assertEqual(json.loads(out.getvalue())["id"], req["id"])
        # worker.py show <follows>: the old plan with its needs
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(worker.main(["--root", self.root, "show", req["follows"]]), 0)
        self.assertEqual(json.loads(buf.getvalue())["result"]["needs"], NEEDS)
        # the new plan uses the answered file and may ask again
        new = self.answer_plan(req["id"], ["TA5"], [self.plan_case()], needs=[TEXT],
                               plan_files=[{"name": "dsm.tif", "case": "TERR-004"}])
        self.assertEqual((new["state"], new["result"]["needs"], new["result"]["plan_files"]),
                         ("done", [TEXT], [{"name": "dsm.tif", "case": "TERR-004"}]))
        # its own needs are answered the same way: a follow-up of the follow-up
        status, again = self.send(new["id"], [{"need": "N3", "choice": "newest"}])
        self.assertEqual((status, again["follows"], again["answers"]), (201, new["id"], [{"need": "N3",
                                                                                           "choice": "newest"}]))

    def test_no_such_plan_is_404(self):
        done = self.asked()
        write = self.w.create_request({"type": "write", "task": "T-20261104-001", "outputs": ["summary"],
                                       "clickup": {}})
        for cid in ("W-20261104-999", "nope", "", None, 5, [done["id"]], write["id"]):
            self.assertEqual(self.send(cid, [{"need": "N1", "choice": "make"}]), (404, {"error": "no such plan"}), cid)

    def test_a_plan_that_is_not_finished_is_409(self):
        not_finished = (409, {"error": "this plan is not finished"})
        today = self.answer(self.check(("TA5",))["id"], ok=("TA5",), targets=("TA5",))  # a done Today check
        self.assertEqual(self.send(today["id"], [{"need": "N1", "choice": "make"}]), not_finished)
        self.resolve()
        _, chk = self.intake_check()
        self.assertEqual(self.send(chk["id"], [{"need": "N1", "choice": "make"}]), not_finished)  # new
        worker.claim(self.root, chk["id"])
        self.assertEqual(self.send(chk["id"], [{"need": "N1", "choice": "make"}]), not_finished)  # claimed
        worker.finish(self.root, chk["id"], "failed", "TA5 is not connected")
        self.assertEqual(self.send(chk["id"], [{"need": "N1", "choice": "make"}]), not_finished)  # failed

    def test_a_plan_without_questions_is_400(self):
        done = self.planned()
        self.assertEqual(self.send(done["id"], [{"need": "N1", "choice": "make"}]),
                         (400, {"error": "this plan has no questions"}))

    def test_a_plan_is_answered_once(self):
        done = self.asked()
        status, first = self.send(done["id"], [{"need": "N1", "choice": "skip"}])
        self.assertEqual(status, 201)
        answered = (409, {"error": "already answered; open the newer plan"})
        self.assertEqual(self.send(done["id"], [{"need": "N1", "choice": "make"}]), answered)
        # a follow-up cancelled (or failed) before any plan came from it used no answer: the plan can be answered again
        worker.cancel(self.root, first["id"])
        status, second = self.send(done["id"], [{"need": "N1", "choice": "make"}])
        self.assertEqual((status, second["follows"], second["answers"]), (201, done["id"],
                                                                          [{"need": "N1", "choice": "make"}]))
        self.assertEqual(self.send(done["id"], [{"need": "N1", "choice": "make"}]), answered)  # open
        self.answer_plan(second["id"], ["TA5"], [self.plan_case()])
        self.assertEqual(self.send(done["id"], [{"need": "N1", "choice": "make"}]), answered)  # done

    def test_bad_answers_are_400(self):
        done = self.asked()
        dsm = {"name": "dsm.tif", "sha256": sha(DSM)}
        one = {"need": "N1", "choice": "make"}
        bad = [
            (MISSING, "answers: 1-8 answers"), (None, "answers: 1-8 answers"), ("N1", "answers: 1-8 answers"),
            ([], "answers: 1-8 answers"), ({"N1": "make"}, "answers: 1-8 answers"),
            ([one] * 9, "answers: 1-8 answers"),
            (["N1"], r"belongs to a question this plan does not ask"),
            ([{"need": "N4", "choice": "make"}], r"belongs to a question this plan does not ask"),
            ([{"need": "n1", "choice": "make"}], r"belongs to a question this plan does not ask"),
            ([{"choice": "make"}], r"belongs to a question this plan does not ask"),
            ([one, {"need": "N1", "choice": "skip"}], r"a question was answered twice"),
            ([{"need": "N1"}], r"each answer is one choice, one text or one file"),
            ([{"need": "N1", "choice": None}], r"each answer is one choice, one text or one file"),
            ([{"need": "N3", "choice": "newest", "text": "the newest"}], r"each answer is one choice, one text or one file"),
            ([{"need": "N2", "choice": "make", "file": dsm}], r"each answer is one choice, one text or one file"),
            ([{"need": "N1", "choice": "later"}], r"a choice is not one this question offers"),
            ([{"need": "N1", "choice": ["make"]}], r"a choice is not one this question offers"),
            ([{"need": "N1", "choice": "Make"}], r"a choice is not one this question offers"),
            ([{"need": "N3", "choice": "make"}], r"a choice is not one this question offers"),
            ([{"need": "N1", "text": "make"}], r"an answer does not fit its question"),
            ([{"need": "N2", "text": "dsm.tif"}], r"an answer does not fit its question"),
            ([{"need": "N1", "file": dsm}], r"an answer does not fit its question"),
            ([{"need": "N3", "file": dsm}], r"an answer does not fit its question"),
            *[([{"need": "N3", "text": v}], r"an answer needs 1 to 1000 characters")
              for v in ("", " \r\n\t ", "x" * 1001, "a\x00b", 5, ["x"], {"text": "x"})],
            *[([{"need": "N2", "file": v}], r"choose one of the attached files")
              for v in ("dsm.tif", {"name": "dsm.tif"}, {"sha256": sha(DSM)}, {"name": 1, "sha256": sha(DSM)}, [dsm])],
            ([{"need": "N2", "file": dict(dsm, name="gone.tif")}], r"gone\.tif is no longer attached to this test"),
            ([{"need": "N2", "file": dict(dsm, name="DSM.tif")}], r"DSM\.tif is no longer attached to this test"),
            ([{"need": "N2", "file": dict(dsm, sha256="0" * 64)}], r"dsm\.tif changed after you chose it"),
            ([{"need": "N2", "file": dict(dsm, sha256=sha(DSM).upper())}], r"dsm\.tif changed after you chose it"),
            ([{"need": "N2", "file": {"name": "cloud.las", "sha256": sha(LAS)}}],
             r"this question takes only \.tif or \.tiff files"),
            ([one, {"need": "N3", "text": "x" * 1001}], r"an answer needs 1 to 1000 characters"),
        ]
        for value, why in bad:
            status, err = self.send(done["id"], value)
            self.assertEqual(status, 400, (value, err))
            self.assertRegex(err["error"], why)
        self.assertEqual([r["id"] for r in self.requests()], [done["id"]])  # nothing was created
        # the limits pass: every need once, a text of 1000 characters, an upper-case extension the need accepts
        self.assertEqual(self.put_file(INTAKE_TID, "Big.TIFF", DSM, 0, len(DSM))[0], 200)
        status, req = self.send(done["id"], [{"need": "N3", "text": "y" * 1000}, one,
                                             {"need": "N2", "file": {"name": "Big.TIFF", "sha256": sha(DSM)}}])
        self.assertEqual((status, [a["need"] for a in req["answers"]], len(req["answers"][0]["text"])),
                         (201, ["N3", "N1", "N2"], 1000))

    def test_the_test_link_must_still_be_read(self):
        done = self.asked()
        os.remove(os.path.join(self.root, plan.intake_rel(INTAKE_TID)))
        self.assertEqual(self.send(done["id"], [{"need": "N1", "choice": "make"}]),
                         (409, {"error": "open the test link again"}))

    def test_one_open_check_per_test(self):
        done = self.asked()
        status, again = self.intake_check()  # Prepare again: a new check of the same test, still open
        self.assertEqual(status, 201)
        # Prepare again made a newer plan of this test: the old plan's questions are not answered any more
        self.assertEqual(self.send(done["id"], [{"need": "N1", "choice": "make"}]),
                         (409, {"error": "a newer plan of this test exists; open it"}))

    def test_a_post_with_the_page_token_only(self):
        done = self.asked()
        self.assertEqual(self._req("POST", "/api/worker/answer", {"check": done["id"], "answers": [
            {"need": "N1", "choice": "make"}]}, token=False)[0], 403)
        self.assertEqual(self._req("GET", "/api/worker/answer")[0], 405)
        self.assertEqual(worker.followers(self.root, done["id"]), [])


class StartTests(Base):
    """POST /api/worker/start while a need is open."""

    def test_a_blocking_need_stops_run(self):
        done = self.asked()
        self.assertEqual(self.start(done), (409, {"error": "answer the questions on the plan first"}))
        self.assertEqual(self.task_files(), [])
        self.assertEqual([r["type"] for r in self.requests()], ["check"])
        # before the other checks: an empty plan with a blocking need says the same
        empty = self.planned(plan_cases=[], needs=[dict(TEXT, blocking=True)])
        self.assertEqual(self.start(empty), (409, {"error": "answer the questions on the plan first"}))

    def test_only_non_blocking_needs_leave_run_as_it_is(self):
        done = self.planned(needs=[TEXT, dict(FILE, blocking=False)])
        status, s = self.start(done)
        self.assertEqual((status, s["engine_cases"]), (201, ["TERR-004"]), s)

    def test_the_follow_up_plan_runs_and_the_old_one_still_waits(self):
        done = self.asked()
        _, req = self.send(done["id"], [{"need": "N1", "choice": "skip"}, {"need": "N2", "choice": "skip"}])
        new = self.answer_plan(req["id"], ["TA5"], [self.plan_case()], not_covered=["Latitudes (you skipped it)"])
        self.assertEqual(self.start(done), (409, {"error": "answer the questions on the plan first"}))
        status, s = self.start(new)
        self.assertEqual((status, s["check"], s["engine_cases"]), (201, new["id"], ["TERR-004"]), s)

    def test_blocking_needs(self):
        done = self.asked()
        self.assertEqual(worker.blocking_needs(done), [CHOICE, FILE])
        self.assertEqual(worker.blocking_needs(dict(done, intake=None)), [])  # a Today check never waits for needs
        for result in (None, {}, {"needs": None}, {"needs": "N1"}, {"needs": [1, dict(TEXT, blocking="true")]}):
            self.assertEqual(worker.blocking_needs(dict(done, result=result)), [], result)
        self.assertEqual(worker.blocking_needs(None), [])


class FollowUpTests(Base):
    """After the 2026-11-04 review: copied device rows keep the real check time (devices_copied), Prepare again on an
    answered plan carries its answers (answers_from), and a case is new against the first plan of a chain."""

    def follow_up(self, answers=None):
        done = self.asked()
        status, req = self.send(done["id"], answers or [{"need": "N1", "choice": "make"}])
        self.assertEqual(status, 201, req)
        return done, req

    def test_copied_device_rows_keep_the_real_check_time(self):
        done, req = self.follow_up()
        new = self.answer_plan(req["id"], ["TA5"], [self.plan_case()], devices_copied=True)
        self.assertEqual((new["result"]["checked_at"], new["result"].get("devices_copied")),
                         (done["result"]["checked_at"], True))

    def test_copied_device_rows_older_than_20_minutes_are_refused(self):
        # 2026-11-05 (wingmate-debug FINDINGS #15): the 20-minute limit lived only in the skill; an older copy made a
        # plan that Run then refused at once (check_usable's 30 minutes)
        done, req = self.follow_up()
        old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=21)).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z")
        rel = f"qa/runner/state/worker/{done['id']}.json"
        with open(os.path.join(self.root, rel), encoding="utf-8") as f:
            stored = json.load(f)
        stored["result"]["checked_at"] = old
        put(self.root, rel, stored)
        worker.claim(self.root, req["id"])
        rows = [dict(r) for r in stored["result"]["targets"]]
        with self.assertRaises(worker.WorkerError) as e:
            worker.answer_check(self.root, req["id"], {"targets": rows, "summary": "x", "plan_cases": [self.plan_case()],
                                                       "devices_copied": True})
        self.assertEqual((e.exception.status, e.exception.message),
                         (400, "devices_copied: the copied device check is older than 20 minutes; check the devices "
                               "again"))
        new = worker.answer_check(self.root, req["id"], {"targets": rows, "summary": "x",
                                                         "plan_cases": [self.plan_case()]})
        self.assertNotIn("devices_copied", new["result"], "checked again: the plan keeps its own check time")

    def test_without_devices_copied_the_plan_is_checked_now(self):
        _, req = self.follow_up()
        new = self.answer_plan(req["id"], ["TA5"], [self.plan_case()])
        self.assertNotIn("devices_copied", new["result"])

    def test_copied_rows_must_be_the_old_plans(self):
        _, req = self.follow_up()
        worker.claim(self.root, req["id"])
        rows = [{"tag": "TA5", "ok": True, "connected": True, "account": True, "app_ok": True, "app": "1.76.0"}]
        with self.assertRaises(worker.WorkerError) as e:
            worker.answer_check(self.root, req["id"], {"targets": rows, "summary": "x",
                                                       "plan_cases": [self.plan_case()], "devices_copied": True})
        self.assertEqual(e.exception.status, 400)
        self.assertIn("devices_copied", e.exception.message)

    def test_devices_copied_needs_a_plan_it_follows(self):
        self.resolve()
        status, chk = self.intake_check(targets=["TA5"])
        self.assertEqual(status, 201, chk)
        with self.assertRaises(worker.WorkerError) as e:
            self.answer_plan(chk["id"], ["TA5"], [self.plan_case()], devices_copied=True)
        self.assertEqual(e.exception.status, 400)

    def test_prepare_again_on_an_answered_plan_carries_the_answers(self):
        done, req = self.follow_up([{"need": "N2", "file": {"name": "dsm.tif", "sha256": sha(DSM)}}])
        new = self.answer_plan(req["id"], ["TA5"], [self.plan_case()],
                               plan_files=[{"name": "dsm.tif", "case": "TERR-004"}])
        status, again = self.intake_check(targets=["TA5"], answers_from=new["id"])
        self.assertEqual(status, 201, again)
        self.assertEqual((again["follows"], again["answers"]), (done["id"], req["answers"]))

    def test_answers_from_must_be_an_answered_plan_of_this_test(self):
        done = self.asked()
        for bad in ("W-x", done["id"], 5):
            status, err = self.intake_check(targets=["TA5"], answers_from=bad)
            self.assertEqual(status, 400, (bad, err))

    def test_carried_answers_that_no_longer_fit_are_dropped_with_their_reason(self):
        # 2026-11-05 (wingmate-debug FINDINGS #14): before, a file answer that no longer fit made Prepare again and Plan
        # again a 409 that sent the person to the first plan, which then refused the answer. Now each answer is checked
        # on its own: the ones that fit carry over, the others are dropped with their reason (Hover asks again)
        done, req = self.follow_up([{"need": "N1", "choice": "make"},
                                    {"need": "N2", "file": {"name": "dsm.tif", "sha256": sha(DSM)}}])
        new = self.answer_plan(req["id"], ["TA5"], [self.plan_case()])
        status, _ = self._req("POST", f"/api/files/{test_intake.INTAKE_TID}/remove", {"name": "dsm.tif"})
        self.assertEqual(status, 200)
        status, again = self.intake_check(targets=["TA5"], answers_from=new["id"])
        self.assertEqual(status, 201, again)
        self.assertEqual((again["follows"], again["answers"]), (done["id"], [{"need": "N1", "choice": "make"}]))
        self.assertEqual(again["answers_dropped"], [
            {"need": "N2", "why": "dsm.tif is no longer attached to this test. Attach it again, then send."}])
        worker.cancel(self.root, again["id"])  # one open plan per test
        # nothing left to carry: a plain new plan, which asks every question again
        keep = ("type", "intake", "prepare", "clickup", "clickup_cases", "guidance", "targets", "cases", "engine_cases",
                "fix_commits", "case_versions", "files")
        lone = self.w.create_request(dict({k: req[k] for k in keep}, follows=done["id"],
                                          answers=[{"need": "N2", "file": {"name": "dsm.tif", "sha256": sha(DSM)}}]))
        lone = self.answer_plan(lone["id"], ["TA5"], [self.plan_case()])
        status, fresh = self.intake_check(targets=["TA5"], answers_from=lone["id"])
        self.assertEqual(status, 201, fresh)
        self.assertNotIn("follows", fresh)
        self.assertNotIn("answers", fresh)
        self.assertEqual([d["need"] for d in fresh["answers_dropped"]], ["N2"])

    def test_cases_are_dated_against_the_first_plan_of_the_chain(self):
        done, req = self.follow_up()
        self.assertEqual(worker.first_plan_created(self.root, req), done["created"])
        self.assertEqual(worker.first_plan_created(self.root, done), done["created"])


if __name__ == "__main__":
    unittest.main()
