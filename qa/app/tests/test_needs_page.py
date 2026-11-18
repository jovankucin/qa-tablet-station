"""Tests of "Needs your input" on the New test plan, page side (2026-11-04, step 1; qa/app/DESIGN.md 8.14): static/app.js
and app.css. The card right after "What I will do", the body Send answers posts and the screen it opens, Run kept off
while a blocking question is open, the empty-plan words, "Your answers" on a follow-up plan, Attach a file and one files
box per screen. Since 2026-11-04 (tests/2026-11-04-results-redesign/SPEC.md screen 6): the plan's title on top of the
Plan screen, each case's parts (checks marked, quotes shown) or check steps with "All engine steps (n)" folded, and New
test's "Your plans" with the line to the tests that wait in History. Since 2026-11-05 (tests/2026-11-05-wingmate-debug/
FINDINGS.md, harness checks 13-15): a cancelled newer check hides no plan in Your plans (#10), the Plan screen shows the
case version the plan pinned (#19), and Run records shows its records before the filters and keeps view=detail on its
filters and Close (#24, #20).

python3 -I qa/app/tests/test_needs_page.py [-v]

The flows run in macOS JavaScriptCore (jsc) against a fake DOM, window and fetch (needs_page_harness.js), which also
calls runBlocked, answersBody and answerLines cut out of app.js; without jsc that part is skipped. Nothing here touches
a device, ClickUp, a browser or the network.
"""
import json
import os
import re
import shutil
import subprocess
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(os.path.dirname(TESTS_DIR), "static")
APP_JS = os.path.join(STATIC, "app.js")
APP_CSS = os.path.join(STATIC, "app.css")
HARNESS = os.path.join(TESTS_DIR, "needs_page_harness.js")
JSC = "/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc"
JS_HEAD = "  // ---- New test plan: the worker's questions and the person's answers (DESIGN.md 8.14) ----"
CSS_HEAD = "/* New test plan: the worker's questions and the person's answers (DESIGN.md 8.14)."


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _jsc():
    return JSC if os.access(JSC, os.X_OK) else shutil.which("jsc")


def _code(js):
    """The script without its comments ("//" to the end of its line only: with re.S, "//.*$" would eat the rest of the
    file)."""
    return re.sub(r"/\*.*?\*/|//[^\n]*", "", js, flags=re.S)


def _needs_js(js):
    """The needs code of app.js: from its heading to the progress screen that follows it."""
    start = js.index(JS_HEAD)
    return js[start:js.index("  async function progressScreen(", start)]


class ScriptRules(unittest.TestCase):
    """CSP and text rules for the needs code: no HTML strings or inline styles, one write, plain words."""

    def setUp(self):
        self.js = _read(APP_JS)
        self.part = _needs_js(self.js)

    def test_no_html_or_inline_style(self):
        code = _code(self.part)
        for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", ".style", "cssText", "eval("):
            self.assertNotIn(bad, code)

    def test_the_one_write_and_where_it_goes(self):
        code = _code(self.part)
        self.assertEqual(re.findall(r"sendJSON\('(\w+)', '([^']+)'", code), [("POST", "/api/worker/answer")])
        self.assertIn("sendJSON('POST', '/api/worker/answer', answersBody(r, answers))", code)
        self.assertIn("goTo(reqHash(t, 'check', next.id));", code)
        self.assertIn("put(page.errSlot, msgBox('error', failText(e)));", code)

    def test_plain_words(self):
        for text in ("'Needs your input'", "' required'", "'Optional'", "'Send answers'", "'Attach a file'",
                     "'Choose an attached file'", "'Your answers'", "'open the newer plan'"):
            self.assertIn(text, self.part)
        strings = re.findall(r"'((?:[^'\\\n]|\\.)*)'", _code(self.part))
        self.assertTrue(strings)
        self.assertEqual([s for s in strings if "!" in s], [])
        self.assertIn("'Answer the questions first'", self.js)
        self.assertIn("'Answered: use the newer plan'", self.js)
        self.assertIn("'Hover needs your input above.'", self.js)

    def test_the_card_follows_the_plan_card(self):
        start = self.js.index("  function checkResult(t, r, wv) {")
        src = self.js[start:self.js.index("\n  }\n", start)]
        self.assertIn("r.prepare ? planCard(r, wv) : null,\n      needsCard(t, r, wv),\n", src)
        self.assertIn("const blocked = runBlocked(r);", src)

    def test_one_files_box_per_screen(self):
        # the box is made once per screen and reused; a new screen stops the last one's box
        self.assertEqual(_code(self.part).count("testFilesBox("), 1)
        self.assertIn("if (hasFile && !page.files) {", self.part)
        self.assertIn("if (planPage && planPage.files) planPage.files.stop();", self.js)


class PlanAndPlans(unittest.TestCase):
    """The Plan screen's title and parts, and "Your plans" on New test (2026-11-04)."""

    def setUp(self):
        self.js = _read(APP_JS)
        self.css = _read(APP_CSS)

    def part(self, head, end):
        start = self.js.index(head)
        return self.js[start:self.js.index(end, start)]

    def test_the_plan_title_is_on_top(self):
        src = self.part("  async function checkScreen(", "\n  }\n")
        self.assertIn("requestHead(t, routeHash('new'), 'Back to New test', h1, clickupLine(t.clickup))", src)
        # the plan's own title replaces the cut description when the plan comes
        self.assertIn("if (t.intake && r && r.title && h1.textContent !== String(r.title)) h1.textContent = "
                      "String(r.title);", src)
        view = self.part("  async function viewNew(", "\n  }\n")
        self.assertIn("title: req.title || cu.title || cu.task_id || rid", view)

    def test_parts_then_the_engine_steps_folded(self):
        src = _code(self.part("  function caseSteps(pc) {", "\n  }\n"))
        self.assertIn("list(c.parts)", src)
        self.assertIn("s.assert && !s.always", src)
        self.assertIn("'All engine steps (' + steps.length + ')'", src)
        self.assertIn("chip('s-info', 'Check')", src)
        for cls in ("plan-parts", "plan-quote", "plan-steps"):
            self.assertIn(cls, src)
            self.assertRegex(self.css, r"(?m)^\." + cls + r"[ >{]", f".{cls} has no rule")

    def test_pinned_case_version(self):
        # FINDINGS #19: a newer version than the plan's pin reads the pinned file, which must be a qa/cases file
        src = _code(self.part("  function caseSteps(pc) {", "\n  }\n"))
        self.assertIn("getJSON(fileUrl(pc.file))", src)
        self.assertIn("CASE_FILE_RE.test(pc.file)", src)
        self.assertIn("'This plan runs an earlier version of this case; a newer one exists.'", src)
        self.assertIn(r"const CASE_FILE_RE = /^qa\/cases\/[A-Za-z0-9][A-Za-z0-9._-]*\.json$/;", self.js)

    def test_run_records_keeps_its_view(self):
        # FINDINGS #20 and #24: the filters, an opened run and Close keep view=detail; the records do not wait for the
        # case list
        view = _code(self.part("  async function viewResults(", "\n  }\n"))
        self.assertNotIn("Promise.allSettled([\n      getJSON('/api/results'", view)
        self.assertIn("Promise.allSettled([getJSON('/api/cases'), getJSON('/api/tasks')])", view)
        self.assertIn("go('results', {view: 'detail', case: caseSel.value", self.js)
        self.assertEqual(self.js.count("routeHash('results', {view: 'detail', case: ctx.caseId"), 1)
        self.assertIn("      view: 'detail', case: ctx.caseId, task: ctx.taskId, harness: ctx.harness ? '1' : '', run: runId,",
                      self.js)

    def test_your_plans(self):
        src = _code(self.part("  function yourPlans(wv, tests) {", "\n  }\n"))
        self.assertIn("el('h2', {}, 'Your plans')", src)
        # FINDINGS #10: a cancelled check made no plan, so it hides none
        self.assertLess(src.index("if (r.state === 'cancelled') continue;"), src.index("if (seen[cu.task_id]) continue;"))
        self.assertIn("'Tests you ran: '", src)
        self.assertIn("String(r.title || cu.title || cu.task_id || r.id)", src)
        # the result rows went to History's "Your tests"; the plans no longer read the tasks
        self.assertNotIn("TEST_ROW", self.js)
        for gone in ("Results ready", "Drafts written", "Run from (?:New test|Today)", "list(tasks)"):
            self.assertNotIn(gone, src)
        self.assertIn("getJSON('/api/results/tests')", self.part("  async function viewNew(", "\n  }\n"))
        for cls in ("plan-sub", "tests-line"):
            self.assertRegex(self.css, r"\." + cls + r" \{", f".{cls} has no rule")


class Styles(unittest.TestCase):
    def setUp(self):
        self.css = _read(APP_CSS)
        self.part = self.css[self.css.index(CSS_HEAD):]

    def test_every_need_class_has_a_rule(self):
        used = set(re.findall(r"\bneed-[a-z][a-z-]*", _code(_needs_js(_read(APP_JS)))))
        self.assertEqual(used, {"need-row", "need-opt", "need-options", "need-file"})
        for cls in sorted(used):
            self.assertRegex(self.part, r"\." + re.escape(cls) + r"(?![\w-])", f".{cls} has no style")

    def test_tokens_only(self):
        self.assertNotRegex(self.part, r"#[0-9A-Fa-f]{3,8}\b")
        self.assertNotRegex(self.part, r"rgba?\(")
        rule = re.search(r'(?m)^\.btn\.need-opt\[aria-pressed="true"\] \{([^}]*)\}', self.part)
        self.assertIsNotNone(rule, "the pressed option has no rule")
        self.assertIn("var(--mercury-600)", rule.group(1))


@unittest.skipUnless(_jsc(), "no JavaScriptCore jsc on this machine")
class Flow(unittest.TestCase):
    def test_in_javascriptcore(self):
        p = subprocess.run([_jsc(), HARNESS, "--", APP_JS], capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr or p.stdout)
        lines = [ln for ln in p.stdout.splitlines() if ln.startswith("{")]
        self.assertTrue(lines, f"no result from the harness: {p.stdout} {p.stderr}")
        out = json.loads(lines[-1])
        self.assertEqual(out["failures"], [])
        self.assertGreaterEqual(out["checks"], 150)  # 152 with the 2026-11-05 checks


if __name__ == "__main__":
    unittest.main()
