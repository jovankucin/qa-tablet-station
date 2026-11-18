"""The filing Next of an approved ticket taps only when the reporter's form holds the approved draft (security review
2026-11-03).

python3 -I qa/runner/tests/test_ticket_form.py

The gate passes QA_TICKET=<ticket dir> python3 -I qa/runner/act.py <tag> tap text:Next ... --approved only for a Touch ID
approval of that draft's title.txt and description.txt. act.py then reads the reporter's title and description and
refuses (exit 2, no tap) unless they equal that draft, so text typed in by hand never goes out under the approval.
Runs act.run in-process against a fake target and page: no device, no ledger.
"""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import act  # noqa: E402

TICKET = "tests/2026-11-03-demo/tickets/1"
TITLE = "[CT] Flight planning: terrain import fails (offline)"
DESC = "Issue Summary\nx\nSteps to Reproduce\ny\nExpected Behavior\nz\nActual Behavior\nw"


class FakeSess:
    def __init__(self, form):
        self.form, self.evals = form, []

    def eval(self, expr):
        self.evals.append(expr)
        return self.form if expr == act.TICKET_FORM_JS else None  # None: no update dialog open


class FakeTarget:
    serial = "FAKE-SERIAL"

    def __init__(self, form):
        self.sess, self.taps = FakeSess(form), []

    def connect(self):
        return {"fake": True}

    def require_app_focus(self):
        pass

    def tap_css(self, cx, cy):
        self.taps.append((cx, cy))
        return {"tapped": [cx, cy]}


class TicketFormTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ticket_form_")
        self.qa = os.path.join(self.tmp, "qa")
        os.makedirs(self.qa)
        self.tdir = os.path.join(self.tmp, TICKET)
        os.makedirs(self.tdir)
        self.write("title.txt", TITLE + "\n")
        self.write("description.txt", DESC + "\n")
        self.saved = {k: getattr(act, k) for k in ("QA_DIR", "load_profiles", "resolve_target", "check_lock",
                                                     "find_waiting", "wait_all")}
        self.env = {k: os.environ.get(k) for k in ("QA_TICKET", "QA_RUNNER", "QA_CLOUD_WRITE")}
        for k in self.env:
            os.environ.pop(k, None)
        act.QA_DIR = self.qa
        act.load_profiles = lambda: {}
        act.check_lock = lambda name: None
        act.find_waiting = lambda sess, sel, nth, timeout: {
            "tag": "BUTTON", "text": sel.split(":", 1)[1], "testid": None, "label": sel.split(":", 1)[1],
            "inDialog": True, "dialogTitle": "Report bug", "cx": 100, "cy": 200, "unblocked": True}
        act.wait_all = lambda t, conds, timeout: {"ok": True}

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(act, k, v)
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, name, text, mode="w"):
        with open(os.path.join(self.tdir, name), mode + ("b" if isinstance(text, bytes) else ""),
                  **({} if isinstance(text, bytes) else {"encoding": "utf-8"})) as f:
            f.write(text)

    def tap(self, form, selector="text:Next", ticket=TICKET):
        target = FakeTarget(form)
        act.resolve_target = lambda name, profiles: target
        if ticket is not None:
            os.environ["QA_TICKET"] = ticket
        a = act.parser().parse_args(["TA5", "tap", selector, "--expect", "gone:" + selector, "--approved"])
        try:
            return act.run(a), target
        except act.Fail as f:
            return f, target

    def refused(self, got, target, words):
        res, t = got, target
        self.assertIsInstance(res, act.Fail)
        self.assertEqual(res.code, 2)
        self.assertIn(words, res.msg)
        self.assertEqual(t.taps, [], "no tap after a refusal")

    def test_form_equal_to_the_draft_taps(self):
        res, t = self.tap([TITLE, DESC])
        self.assertNotIsInstance(res, act.Fail)
        self.assertEqual(t.taps, [(100, 200)])
        self.assertEqual(res["ticket_form"]["title"], "matches the draft")
        self.assertIn(act.TICKET_FORM_JS, t.sess.evals)

    def test_other_title_typed_by_hand_is_refused(self):
        self.refused(*self.tap(["[CT] something the person never approved", DESC]), "title differ")

    def test_other_description_is_refused(self):
        self.refused(*self.tap([TITLE, DESC + "\nand one more line"]), "description differ")

    def test_both_differ_named(self):
        self.refused(*self.tap(["x", "y"]), "title and description differ")

    def test_form_not_found_or_doubled_is_refused(self):
        self.refused(*self.tap(None), "were not found")
        self.refused(*self.tap(["only one"]), "were not found")
        self.refused(*self.tap([TITLE, 5]), "were not found")

    def test_draft_changed_after_typing_is_refused(self):
        self.write("description.txt", DESC + "\nedited after the approval\n")
        self.refused(*self.tap([TITLE, DESC]), "description differ")

    def test_missing_draft_is_refused(self):
        os.unlink(os.path.join(self.tdir, "title.txt"))
        self.refused(*self.tap([TITLE, DESC]), "cannot be read")

    def test_bad_ticket_dir_is_refused(self):
        for bad in ("", "../x", "tests/2026-11-03-demo/tickets/1/..", "/tmp/tickets/1", "tests/x/tickets/1"):
            self.refused(*self.tap([TITLE, DESC], ticket=bad), "QA_TICKET must be")

    def test_crlf_and_trailing_newlines_are_read_as_typed(self):
        self.write("description.txt", DESC.replace("\n", "\r\n").encode("utf-8") + b"\r\n\r\n")
        res, t = self.tap([TITLE, DESC])
        self.assertNotIsInstance(res, act.Fail)
        self.assertEqual(len(t.taps), 1)

    def test_no_ticket_no_form_read(self):
        res, t = self.tap(["anything", "anything"], ticket=None)
        self.assertNotIsInstance(res, act.Fail)
        self.assertEqual(len(t.taps), 1)
        self.assertNotIn(act.TICKET_FORM_JS, t.sess.evals)
        self.assertNotIn("ticket_form", res)

    def test_report_bug_tap_reads_no_form(self):
        res, t = self.tap(None, selector="text:Report bug")
        self.assertNotIsInstance(res, act.Fail)
        self.assertEqual(len(t.taps), 1)
        self.assertNotIn(act.TICKET_FORM_JS, t.sess.evals)


if __name__ == "__main__":
    unittest.main()
