"""Tests of the Results page's ticket block (static/results.js and results.css, 2026-11-03; since 2026-11-04 in the row of
the case where a device failed, on the one-test page): the ticket draft styles and the File ticket flow (offline notice,
one primary button on the page, Touch ID wait, filing, filed, refused presses). Since 2026-11-05 (tests/2026-11-05-
wingmate-debug/FINDINGS.md #3, #18 and #22, harness checks 8 to 10): a draft the server would not file
(ticket.fileable false) shows its why instead of File ticket and is never the orange button; a filing ticket the looks
lost is no longer followed; Report issue's wait ends when a new draft came, one File ticket cannot send included, and
never on a draft set aside before the press. Since 2026-11-09 (check 11): a failed row the server takes no ticket for
(it ran on the earlier engine account) says why ('Report issue: ...') where its ticket part would be.

python3 -I qa/app/tests/test_results_tickets.py [-v]

The flow runs in macOS JavaScriptCore (jsc) against a fake DOM and a fake window.QA (results_ticket_harness.js);
without jsc that part is skipped. Nothing here touches a device, ClickUp, a browser or the network.
"""
import json
import os
import re
import shutil
import subprocess
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(os.path.dirname(TESTS_DIR), "static")
RESULTS_JS = os.path.join(STATIC, "results.js")
RESULTS_CSS = os.path.join(STATIC, "results.css")
HARNESS = os.path.join(TESTS_DIR, "results_ticket_harness.js")
JSC = "/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc"


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _jsc():
    return JSC if os.access(JSC, os.X_OK) else shutil.which("jsc")


class TicketStyles(unittest.TestCase):
    def setUp(self):
        self.css = _read(RESULTS_CSS)

    def rule(self, selector):
        m = re.search(r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}", self.css)
        self.assertIsNotNone(m, f"{selector} has no rule")
        return m.group(1)

    def test_ticket_box_uses_the_wingtra_tokens(self):
        box = self.rule(".rv-ticket-one")
        self.assertIn("background: var(--uranus-0)", box)
        self.assertIn("border: 1px solid var(--mercury-100)", box)
        self.assertIn("border-radius: var(--radius)", box)  # app.css --radius: 8px
        self.assertIn("color: var(--mercury-600)", self.rule(".rv-ticket-label"))

    def test_every_ticket_class_in_the_script_has_a_rule(self):
        js = _read(RESULTS_JS)
        used = set(re.findall(r"rv-ticket[a-z-]*", js))
        self.assertTrue({"rv-ticket", "rv-ticket-one", "rv-ticket-note", "rv-ticket-done"} <= used)
        for cls in used:
            self.assertRegex(self.css, r"\." + re.escape(cls) + r"\b", f".{cls} has no style")


class TicketScriptRules(unittest.TestCase):
    """The page's CSP and text rules: no inline styles, no HTML strings."""

    def test_no_inline_style_or_html(self):
        # the comments say "no innerHTML"; the code must not use it ("//" comments end at their line: with re.S,
        # "//.*$" ate the whole rest of the file and this test checked an empty string)
        code = re.sub(r"/\*.*?\*/|//[^\n]*", "", _read(RESULTS_JS), flags=re.S)
        self.assertIn("function ticketOne(", code)
        self.assertIn("function caseRow(", code)
        for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", ".style.", "'style'", "cssText"):
            self.assertFalse(bad in code, f"results.js uses {bad}")

    def test_plain_words(self):
        js = _read(RESULTS_JS)
        for text in ("File ticket", "Confirm with Touch ID on the Mac…", "Not approved (Touch ID cancelled).",
                     "' to the internet, then press File ticket.'", "'Filed: '", "'Filing on '",
                     "'Not filed: its run was corrected to a test error, so there is nothing to file.'",
                     "'This result is no longer in the list. Reload the page to see where the ticket stands.'",
                     "'Report issue: ' + rep.why"):
            self.assertIn(text, js)

    def test_file_ticket_only_for_a_fileable_draft(self):
        # FINDINGS #3: the server's word (ticket.fileable) decides File ticket; a server without it leaves the press on
        code = re.sub(r"/\*.*?\*/|//[^\n]*", "", _read(RESULTS_JS), flags=re.S)
        self.assertIn("return t.fileable === false && Boolean(str(t.dir)) && ticketState(t) === 'draft';", code)
        self.assertIn("btn.hidden = state !== 'draft' || blocked;", code)


@unittest.skipUnless(_jsc(), "no JavaScriptCore jsc on this machine")
class TicketFlow(unittest.TestCase):
    def test_flow_in_javascriptcore(self):
        p = subprocess.run([_jsc(), HARNESS, "--", RESULTS_JS], capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr or p.stdout)
        lines = [ln for ln in p.stdout.splitlines() if ln.startswith("{")]
        self.assertTrue(lines, f"no result from the harness: {p.stdout} {p.stderr}")
        out = json.loads(lines[-1])
        self.assertEqual(out["failures"], [])
        self.assertGreater(out["checks"], 140)  # 131 with the 2026-11-05 checks, 144 with check 11 (2026-11-09)


if __name__ == "__main__":
    unittest.main()
