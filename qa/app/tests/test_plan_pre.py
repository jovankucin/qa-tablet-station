"""Tests of what a New test plan's cases take as given, on the Plan screen (2026-11-10; static/app.js and app.css). On
2026-11-10 Run approved a plan for ClickUp test 0000fake009 whose case rested on a precondition "set by the person":
the Plan screen showed a case's preconditions nowhere, only the case page did. Now each case of a plan lists them
under its parts, titled "Before the run, this test takes as given", as the case page shows them (one function,
preItem, draws the line for both); a case without preconditions shows nothing; a line from the person (its check is
"person", or its text says something was done "by the person" or "by you") is a warning box with the chip "Set by
you". The list reads only and changes nothing about Run.

python3 -I qa/app/tests/test_plan_pre.py [-v]

The screen runs in macOS JavaScriptCore (jsc) against a fake DOM, window and fetch (plan_pre_harness.js), which also
calls fromPerson and preItem cut out of app.js; without jsc that part is skipped. That both routes the screen reads
(/api/cases/<id> and /files/<the pinned case file>) carry the preconditions is in test_app.py (PlanCaseReadTests).
Nothing here touches a device, ClickUp, a browser or the network.
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
HARNESS = os.path.join(TESTS_DIR, "plan_pre_harness.js")
JSC = "/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc"
LABEL = "'Before the run, this test takes as given'"
MARK = "preItem(p, mine ? 'msg msg-warn' : null, mine ? [' ', chip('s-warn', 'Set by you')] : null)"


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _jsc():
    return JSC if os.access(JSC, os.X_OK) else shutil.which("jsc")


def _code(js):
    """The script without its comments ("//" to the end of its line only: with re.S, "//.*$" would eat the rest of the
    file)."""
    return re.sub(r"/\*.*?\*/|//[^\n]*", "", js, flags=re.S)


class ScriptRules(unittest.TestCase):
    """Where the list sits, what draws its lines, and the CSP and text rules for the new code."""

    def setUp(self):
        self.js = _read(APP_JS)

    def fn(self, name):
        """One function of app.js, without its comments."""
        start = self.js.index(f"\n  function {name}(")
        return _code(self.js[start:self.js.index("\n  }\n", start)])

    def test_the_list_sits_under_the_parts_above_the_engine_steps(self):
        src = self.fn("caseSteps")
        parts = src.index("el('ol', {class: 'plan-parts'}")
        pre = src.index("pre.length ? el('div', {class: 'plan-pre'},")
        steps = src.index("el('details', {class: 'plan-steps'}")
        self.assertLess(parts, pre)
        self.assertLess(pre, steps)
        # titled in plain words, once in the whole page; a case without preconditions shows nothing
        self.assertIn(f"el('div', {{class: 'field-label'}}, {LABEL})", src)
        self.assertEqual(self.js.count(LABEL), 1)
        self.assertIn("}))) : null,\n        steps.length ?", src)
        # the preconditions are the pinned version's, like the parts: one record feeds both
        self.assertIn("const pre = list(c.preconditions).filter(", src)
        self.assertIn("const parts = list(c.parts).filter(", src)

    def test_one_line_for_the_case_page_and_the_plan_screen(self):
        self.assertEqual(self.js.count("\n  function preItem(p, cls, after) {"), 1)
        # the case page: the plain line, no mark
        detail = self.fn("caseDetail")
        self.assertIn("list(c.preconditions).map(function (p) { return preItem(p); })", detail)
        for mark in ("fromPerson", "msg-warn", "Set by you"):
            self.assertNotIn(mark, detail)
        # the Plan screen: the same line, and the person's as a warning box with the chip
        src = self.fn("caseSteps")
        self.assertIn("const mine = fromPerson(p);", src)
        self.assertIn(MARK, src)
        self.assertEqual(_code(self.js).count("'Set by you'"), 1)

    def test_the_rule_for_a_line_from_the_person(self):
        src = self.fn("fromPerson")
        # the check is the word "person" first (also "the person", or with more after it); the text says "by the
        # person" or "by you"; a wider match would only add warning boxes, a narrower one hides a line
        self.assertIn(r"/^\s*(?:the\s+)?person\b/i.test(o.check)", src)
        self.assertIn(r"/\bby\s+(?:the\s+person|you)\b/i.test(o.text)", src)

    def test_no_html_no_inline_style_no_write(self):
        for name in ("fromPerson", "preItem", "caseSteps"):
            src = self.fn(name)
            for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", ".style", "cssText", "eval(", "sendJSON("):
                self.assertNotIn(bad, src, name)

    def test_plain_words(self):
        for name in ("fromPerson", "preItem", "caseSteps"):
            strings = re.findall(r"'((?:[^'\\\n]|\\.)*)'", self.fn(name))
            self.assertEqual([s for s in strings if "!" in s], [], name)


class Styles(unittest.TestCase):
    """The list has its own spacing only; the warning colours are the ones the app already has."""

    def setUp(self):
        self.css = _read(APP_CSS)
        self.rules = re.findall(r"(?m)^(\.plan-pre[^{]*)\{([^}]*)\}", self.css)

    def test_the_list_has_rules(self):
        self.assertEqual([sel.strip() for sel, _ in self.rules],
                         [".plan-pre", ".plan-pre > ul", ".plan-pre li", ".plan-pre li.msg"])

    def test_no_new_colours(self):
        for sel, body in self.rules:
            for bad in ("#", "rgb", "color", "background", "var(--"):
                self.assertNotIn(bad, body, f"{sel.strip()} sets a colour")
        # the box and the chip of a line from the person: the existing warning styles
        for sel in (r"\.msg-warn", r"\.s-warn"):
            rule = re.search(r"(?m)^" + sel + r" \{([^}]*)\}", self.css)
            self.assertIsNotNone(rule, f"{sel} has no rule")
            self.assertIn("var(--yamabuki-0)", rule.group(1))
            self.assertIn("var(--yamabuki-700)", rule.group(1))


@unittest.skipUnless(_jsc(), "no JavaScriptCore jsc on this machine")
class Flow(unittest.TestCase):
    def test_in_javascriptcore(self):
        p = subprocess.run([_jsc(), HARNESS, "--", APP_JS], capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr or p.stdout)
        lines = [ln for ln in p.stdout.splitlines() if ln.startswith("{")]
        self.assertTrue(lines, f"no result from the harness: {p.stdout} {p.stderr}")
        out = json.loads(lines[-1])
        self.assertEqual(out["failures"], [])
        self.assertGreaterEqual(out["checks"], 85)  # 85 on 2026-11-10


if __name__ == "__main__":
    unittest.main()
