"""Tests of History's first card, 'Your tests' (static/history.js, 2026-11-04; tests/2026-11-04-results-redesign/SPEC.md
screen 5 over GET /api/results/tests, API.md section 4): Ongoing · Tested · Done, each row opening its test on Results,
the next press, how a test in Done ended, what an ongoing test waits for, and the 'n more' and 'Show all' labels. Since
2026-11-05 (tests/2026-11-05-wingmate-debug/FINDINGS.md #30, harness check 5): the charts call an ERROR run a test
error, as the rest of the app does.

python3 -I qa/app/tests/test_history_tests.py [-v]

The card runs in macOS JavaScriptCore (jsc) against a fake DOM and a fake window.QA (history_tests_harness.js); without
jsc that part is skipped. Nothing here touches a device, ClickUp, a browser or the network.
"""
import json
import os
import shutil
import subprocess
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_JS = os.path.join(os.path.dirname(TESTS_DIR), "static", "history.js")
HARNESS = os.path.join(TESTS_DIR, "history_tests_harness.js")
JSC = "/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc"


def _jsc():
    return JSC if os.access(JSC, os.X_OK) else shutil.which("jsc")


class Words(unittest.TestCase):
    def test_test_error_words(self):
        with open(HISTORY_JS, encoding="utf-8") as f:
            js = f.read()
        self.assertIn("ERROR: 'Test error'}", js)
        self.assertIn("const LEGEND_LABEL = {ERROR: 'Test error (the test itself stopped)'};", js)
        self.assertIn("Q.getJSON('/api/results/tests')", js)
        # FINDINGS #30: the charts say test error(s) where they said "not decided" for ERROR runs
        self.assertNotRegex(js, r"(?i)\bnot decided\b|\bundecided\b")
        for text in ("kicker: 'Why runs ended in a test error'", "'All test errors'", "'Test errors'",
                     "'No run ended in a test error in this period.'"):
            self.assertIn(text, js)


@unittest.skipUnless(_jsc(), "no JavaScriptCore jsc on this machine")
class YourTestsCard(unittest.TestCase):
    def test_card_in_javascriptcore(self):
        p = subprocess.run([_jsc(), HARNESS, "--", HISTORY_JS], capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr or p.stdout)
        lines = [ln for ln in p.stdout.splitlines() if ln.startswith("{")]
        self.assertTrue(lines, f"no result from the harness: {p.stdout} {p.stderr}")
        out = json.loads(lines[-1])
        self.assertEqual(out["failures"], [])
        self.assertGreaterEqual(out["checks"], 29)  # 29 with the 2026-11-05 checks


if __name__ == "__main__":
    unittest.main()
