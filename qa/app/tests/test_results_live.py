"""Tests of the Results page (static/results.js, results.css, app.js): since 2026-11-04 one test per page
(tests/2026-11-04-results-redesign/SPEC.md screens 1-4, the JSON of API.md): Run lands on Results; the header, the box
with its one Sun Orange button (File ticket > Post comment > Report issue > Plan again > Add result), Plan again and
Mark done…; the case rows with a chip per device; the live box while the test runs (a look every 4 s at ?task=<id>,
the result box in its place when it ends); Report issue on the rows and Add result with Post comment (Touch ID, the
sha256 of the text the person read) in the box, as on 2026-11-03. Since 2026-11-05 (tests/2026-11-05-wingmate-debug/
FINDINGS.md, harness checks 15b and 23-27): a posted comment reads the test again (no Plan again or Report issue in
Done), a newer plan of the test takes Plan again's place, a later draft or comment takes away an earlier 409, '–' for a
device that does not run a live case, Add result off while a write request is open, ?case=X without an engine run
points at Run records; and the footer's words.

python3 -I qa/app/tests/test_results_live.py [-v]

The flows run in macOS JavaScriptCore (jsc) against a fake DOM and a fake window.QA (results_live_harness.js);
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
APP_JS = os.path.join(STATIC, "app.js")
INDEX_HTML = os.path.join(STATIC, "index.html")
HARNESS = os.path.join(TESTS_DIR, "results_live_harness.js")
JSC = "/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc"
OSASCRIPT = "/usr/bin/osascript"
# classes results.js uses only to find its own buttons (rv-empty rides on .card); the prefixes it completes with a
# verdict or an id ("rv-v-" + verdict) end in "-" and are skipped
HOOKS = {"rv-report-btn", "rv-add-btn", "rv-post-btn", "rv-file-btn", "rv-plan-btn", "rv-done-btn", "rv-next",
         "rv-empty"}


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _jsc():
    return JSC if os.access(JSC, os.X_OK) else shutil.which("jsc")


def _code(js):
    """The script without its comments ("//" to the end of its line only: with re.S, "//.*$" would eat the rest of the
    file)."""
    return re.sub(r"/\*.*?\*/|//[^\n]*", "", js, flags=re.S)


class RunLandsOnResults(unittest.TestCase):
    """app.js: Run on the Plan screen opens Results for the new task; the progress screen stays for old links."""

    def setUp(self):
        js = _read(APP_JS)
        start = js.index("  function checkResult(t, r, wv) {")
        self.check_result = js[start:js.index("\n  }\n", start)]
        self.js = js

    def test_run_goes_to_results_for_the_new_task(self):
        handler = self.check_result[self.check_result.index("start.addEventListener('click'"):]
        post = handler.index("sendJSON('POST', '/api/worker/start'")
        go = handler.index("goTo(s.task ? routeHash('results', {task: s.task}) : reqHash(t, 'progress', s.id));")
        self.assertLess(post, go)
        self.assertEqual(handler.count("goTo("), 1)

    def test_the_progress_screen_stays_for_old_links(self):
        self.assertIn("if ((show === 'plan' || show === 'progress') && rid) {", self.js)
        self.assertIn("await (show === 'plan' ? checkScreen : progressScreen)(root, t, rid, alive);", self.js)


class Styles(unittest.TestCase):
    def setUp(self):
        self.css = _read(RESULTS_CSS)
        self.js = _read(RESULTS_JS)

    def test_every_class_in_the_script_has_a_rule(self):
        used = set(re.findall(r"rv-[a-z][a-z_-]*", _code(self.js)))
        self.assertTrue({"rv-live", "rv-box", "rv-test-title", "rv-case", "rv-step-table", "rv-comment", "rv-why",
                         "rv-ticket-panel", "rv-confirm"} <= used)
        # gone with the redesign: Running now, the older fold, the compact card
        self.assertFalse({"rv-running", "rv-run-card", "rv-older", "rv-older-row", "rv-nested", "rv-card"} & used)
        for cls in sorted(c for c in used - HOOKS if not c.endswith("-")):
            self.assertRegex(self.css, r"\." + re.escape(cls) + r"(?![\w-])", f".{cls} has no style")

    def test_new_parts_use_the_wingtra_tokens(self):
        for sel in (".rv-comment-text", ".rv-live", ".rv-v-running", ".rv-why", ".rv-run-shot", ".rv-box", ".rv-note",
                    ".rv-confirm", ".rv-test-title", ".rv-why-line", ".rv-v-not_decided", ".rv-o-stopped"):
            m = re.search(r"(?m)^" + re.escape(sel) + r"\s*\{([^}]*)\}", self.css)
            self.assertIsNotNone(m, f"{sel} has no rule")
            self.assertNotRegex(m.group(1), r"#[0-9A-Fa-f]{3,6}\b", f"{sel} uses a colour outside the tokens")
        self.assertIn("border: 1px solid var(--mercury-100)", re.search(r"(?m)^\.rv-comment-text \{([^}]*)\}",
                                                                          self.css).group(1))

    def rule(self, sel):
        m = re.search(r"(?m)^" + re.escape(sel) + r"\s*\{([^}]*)\}", self.css)
        self.assertIsNotNone(m, f"{sel} has no rule")
        return m.group(1)

    def test_test_error_is_hatched_and_the_title_has_two_lines(self):
        # "Test error" is hatched at 45 degrees, as History hatches ERROR (history.css .hx-k-ERROR)
        self.assertIn("repeating-linear-gradient(45deg", self.rule(".rv-v-not_decided"))
        self.assertIn("border: 1px dashed", self.rule(".rv-v-not_decided"))
        title = self.rule(".rv-test-title")
        self.assertIn("-webkit-line-clamp: 2", title)
        self.assertIn("overflow: hidden", title)

    def test_the_rows_stack_on_a_phone(self):
        # a grid of one to four device columns; on a phone the chips wrap under the title and name their device
        for n in range(1, 5):
            self.assertIn(f".rv-cols-{n} .rv-case-line", self.css)
        self.assertRegex(self.css, r"@media \(max-width: 720px\) \{[^@]*\.rv-cell-tag \{ position: static;")

    def test_running_shots_fit_a_phone(self):
        self.assertRegex(self.css, r"@media \(max-width: 720px\) \{[^@]*\.rv-run-dev \{ grid-template-columns: "
                                   r"minmax\(0, 1fr\) 96px; \}")


class ScriptRules(unittest.TestCase):
    """CSP and text rules for the new parts: no inline styles, no HTML strings, plain words."""

    def setUp(self):
        self.js = _read(RESULTS_JS)

    def test_no_inline_style_or_html(self):
        code = _code(self.js)
        for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", ".style.", "'style'", "cssText", "setAttribute('style'"):
            self.assertNotIn(bad, code)

    def test_plain_words(self):
        for text in ("'Report issue'", "'Add result'", "'Post comment'", "'Copy ClickUp comment'", "'Plan again'",
                     "'Mark done…'", "'It moves to Done in History; Add result is off after this.'",
                     "'What you asked and the plan'", "'Run records ›'", "'What it checks: '", "'Test error'",
                     "'Drafting the ticket…'", "'Writing the comment…'", "'Posting the comment…'",
                     "'Confirm with Touch ID on the Mac…'", "'Waiting for Hover'", "'Hover is off'",
                     "'Results comment, not posted yet'", "' other tests wait'", "'Also running: '",
                     # 2026-11-05: the newer plan's link, a device that does not run a live case, no engine run of X
                     "'A newer plan of this test exists ›'", "'does not run this case'", "'No engine run of '"):
            self.assertIn(text, self.js)
        # the words that went with the redesign: the question-mark verdict, Running now, the older fold, run took
        code = _code(self.js)
        for gone in ("Not decided", "not decided", "Running now", "Older results", "run took", "same result"):
            self.assertNotIn(gone, code)

    def test_the_contract_paths(self):
        code = _code(self.js)
        self.assertIn("Q.sendJSON('POST', '/api/tickets/draft', {task: c.task, case: c.case})", code)
        self.assertIn("Q.sendJSON('POST', '/api/results/comment', {task: t.task})", code)
        # Post comment sends the sha256 of the text the person read (the server's text, shown as sent)
        self.assertIn("Q.sendJSON('POST', '/api/results/comment/post', {task: t.task, sha256: m.sha256})", code)
        # Plan again: the server's Prepare again body unchanged; Mark done: the existing state route
        self.assertIn("Q.sendJSON('POST', '/api/worker/check', body)", code)
        self.assertIn("Q.sendJSON('POST', '/api/tasks/' + encodeURIComponent(t.task) + '/state', {to: 'closed'})", code)
        # the page writes only through those and File ticket; it never talks to ClickUp itself
        self.assertEqual(sorted(set(re.findall(r"sendJSON\('POST', '([^']+)'", code))),
                         ["/api/results/comment", "/api/results/comment/post", "/api/tasks/", "/api/tickets/draft",
                          "/api/tickets/file", "/api/worker/check"])
        self.assertNotIn("api.clickup.com", code)
        self.assertIn("const RUN_POLL_MS = 4000;", code)
        self.assertIn("const POLL_MS = 5000;", code)
        # one test: the page reads /api/results/cards with ?task= (or ?case=), never the whole list
        self.assertNotIn("getJSON('/api/results/cards')", code)
        self.assertIn("Q.qs({task: task || (cs ? '' : 'current'), case: cs || ''})", code)


class Footer(unittest.TestCase):
    """FINDINGS #29 (2026-11-05): the footer on every page names the one way to ClickUp, the person's press and Touch ID
    (Post comment and File ticket), instead of saying nothing posts there."""

    def test_footer_words(self):
        html = _read(INDEX_HTML)
        self.assertIn('<footer class="footer">Engine account only · results first · nothing reaches ClickUp without '
                      'your Touch ID</footer>', html)
        self.assertNotIn("nothing posts to ClickUp", html)


@unittest.skipUnless(os.path.exists(OSASCRIPT), "needs macOS osascript")
class Parses(unittest.TestCase):
    def test_scripts_parse(self):
        for path in (RESULTS_JS, APP_JS):
            script = ("ObjC.import('Foundation'); var s = $.NSString.stringWithContentsOfFileEncodingError("
                      f"{json.dumps(path)}, $.NSUTF8StringEncoding, null).js; new Function(s); 'ok'")
            out = subprocess.run([OSASCRIPT, "-l", "JavaScript", "-e", script], capture_output=True, text=True,
                                 timeout=60)
            self.assertEqual((out.returncode, out.stdout.strip()), (0, "ok"), f"{path}: {out.stderr}")


@unittest.skipUnless(_jsc(), "no JavaScriptCore jsc on this machine")
class LiveFlow(unittest.TestCase):
    def test_flow_in_javascriptcore(self):
        p = subprocess.run([_jsc(), HARNESS, "--", RESULTS_JS], capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr or p.stdout)
        lines = [ln for ln in p.stdout.splitlines() if ln.startswith("{")]
        self.assertTrue(lines, f"no result from the harness: {p.stdout} {p.stderr}")
        out = json.loads(lines[-1])
        self.assertEqual(out["failures"], [])
        self.assertGreater(out["checks"], 450)  # 466 with the 2026-11-05 checks


if __name__ == "__main__":
    unittest.main()
