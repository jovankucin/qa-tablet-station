"""run.py {time} (2026-11-03): helpers and read_js get the run's local start as "MM-DD HH:MM", for short QA_ names that
stay unique ("QA_ET65 Offline sites 11-03 14:30"); the other placeholders are unchanged.

python3 -I qa/runner/tests/test_time_placeholder.py
"""
import os
import re
import sys
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import run  # noqa: E402


class TimePlaceholder(unittest.TestCase):
    def stub(self):
        return types.SimpleNamespace(tag="ET65", run_id="R-20261103-ET65-OFFL-001-05", dir="/tmp/qa test/ET65",
                                     time_label="11-03 14:30")

    def test_fill_names(self):
        got = run.TargetRun.fill(self.stub(), 'act.py {target} type testid:input-site-name --text "QA_{target} Offline sites {time}"')
        self.assertEqual(got, 'act.py ET65 type testid:input-site-name --text "QA_ET65 Offline sites 11-03 14:30"')

    def test_other_placeholders_unchanged(self):
        got = run.TargetRun.fill(self.stub(), "act.py {target} shot {shots}/{run}_end.png")
        # as the shell reads it (the quoting splits where it did before {time} existed)
        self.assertEqual(__import__("shlex").split(got),
                         ["act.py", "ET65", "shot", "/tmp/qa test/ET65/shots/R-20261103-ET65-OFFL-001-05_end.png"])

    def test_label_format(self):
        r = run.TargetRun.__new__(run.TargetRun)
        r.time_label = __import__("time").strftime("%m-%d %H:%M")
        self.assertRegex(r.time_label, re.compile(r"^\d{2}-\d{2} \d{2}:\d{2}$"))


if __name__ == "__main__":
    unittest.main()
