"""Tests of the worker's written rules against the app (review of 2026-11-09, tests/2026-11-05-wingmate-debug/FINDINGS.md
#26, and the skill side of #2, #4, #5 and #14): the playbook blocks (qa/playbooks in this copy) and the qa-worker and
qa-run skills (plugin/wingtra-qa/skills in this copy) say what the app and the engine do.

- #26: no block waits right before a tap for that tap's own target; Save new site keeps its wait as --find-timeout 60;
  a step citation names the case file, and its step numbers and texts are those of that file in qa/cases.
- #5: a Confirm is a cloud write, refused from New test whatever its gates: the texts say so and the guard does it.
- #2 and #4: the skill quotes the app's refusal of a check part that quotes only a ClickUp heading, says it names the
  lines sentence by sentence and that an 'e.g.' list holds examples, and its example is what the app accepts; the app
  then adds the line no part quotes to not_covered.
- #14: the skill reads answers_dropped.

python3 -I qa/app/tests/test_playbooks.py [-v]

It reads files only (this copy's playbooks and skills, the case files in qa/cases) and runs the app's plan-time
checks in memory. Nothing here touches a device, ClickUp, a browser or the network.
"""
import json
import os
import re
import shlex
import sys
import unittest
from unittest import mock

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(TESTS_DIR)
COPY = os.path.dirname(APP_DIR)  # this copy's folder (qa, or the staging copy qa_stage), never another one
WS = os.path.dirname(COPY)
CASES = os.path.join(WS, "qa", "cases")  # the case records (one store for every copy)
PLAYBOOKS = os.path.join(COPY, "playbooks")
SKILLS = os.path.join(COPY, "plugin", "wingtra-qa", "skills")
sys.path[:0] = [APP_DIR, os.path.join(COPY, "runner")]
import redact  # noqa: E402

redact._SECRETS = []  # independent of the real workspace secrets file
import run  # noqa: E402
import worker  # noqa: E402

PAGES = ("README.md", "flight-planning.md", "flight-planning-drawing.md", "offline-sites.md")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _flat(text):
    return re.sub(r"\s+", " ", text)


def _pages():
    return {n: _read(os.path.join(PLAYBOOKS, n)) for n in PAGES}


def _skill(name):
    return _read(os.path.join(SKILLS, name, "SKILL.md"))


# ---- reading the playbooks: json blocks, text units, act.py commands -------------------------------------------------

FENCE = re.compile(r"```json\n(.*?)```", re.S)
VERBS = ("tap", "sys-tap", "type", "wait", "choose-file", "key", "map-tap", "shot", "wifi", "read", "ready")
SPAN = re.compile(r"`([^`]+)`")


def json_blocks(md):
    """Each ```json block of a playbook: [step, ...], one JSON step per line."""
    return [[json.loads(line) for line in m.group(1).splitlines() if line.strip()] for m in FENCE.finditer(md)]


def units(md):
    """The text outside the json blocks in order: each table row, each paragraph or list item (its lines joined)."""
    md = FENCE.sub("\n\n", md)
    out, cur = [], []
    for line in md.splitlines():
        new_item = line.lstrip().startswith(("- ", "* ", "#")) or re.match(r"^\s*\d+\. ", line)
        if line.startswith("|") or not line.strip() or new_item:
            if cur:
                out.append(" ".join(cur))
                cur = []
            if line.startswith("|"):
                out.append(line)
            elif line.strip():
                cur = [line.strip()]
        else:
            cur.append(line.strip())
    if cur:
        out.append(" ".join(cur))
    return out


def command(span):
    """(verb, args) of an act.py command as a playbook writes it (with or without 'act.py {target}'), else None; a
    selector written alone (after [W]) is a tap."""
    try:
        a = shlex.split(span)
    except ValueError:
        return None
    if a[:2] == ["act.py", "{target}"]:
        a = a[2:]
    if not a:
        return None
    if a[0] in VERBS:
        return a[0], a[1:]
    if re.match(r"^(testid|text|aria):", a[0]):
        return "tap", a
    return None


def tap_target(verb, args):
    if verb == "sys-tap" and args:
        return "text", args[0]
    if verb == "tap" and args:
        kind, _, val = args[0].partition(":")
        if kind in ("testid", "text", "aria"):
            return kind, val
    return None


def expects(args):
    return [args[i + 1] for i, a in enumerate(args[:-1]) if a == "--expect"]


def waits_for(expect, target):
    """True when a wait's --expect waits for the tap's own target (to show or to be enabled)."""
    kind, val = target
    if kind in ("text", "aria") and expect in (f"text:{val}", f"sel:text:{val}", f"sel:aria:{val}"):
        return True
    if kind == "testid" and (expect == f"sel:testid:{val}" or f"data-testid={val}]" in expect
                             or f'data-testid="{val}"' in expect):
        return True
    return expect.startswith("js:") and kind in ("text", "aria") and (f"'{val}'" in expect or f'"{val}"' in expect)


def wait_before_tap(seq):
    """The (wait, tap) pairs of a command sequence where the wait waits for the tap's own target."""
    bad = []
    for (v1, a1, w1), (v2, a2, w2) in zip(seq, seq[1:]):
        target = tap_target(v2, a2)
        if v1 == "wait" and target and any(waits_for(e, target) for e in expects(a1)):
            bad.append(f"{w1[:70]!r} right before {w2[:60]!r}")
    return bad


def never_add_problems(pages):
    bad = []
    for name, md in pages.items():
        for block in json_blocks(md):
            seq = [c + (s.get("text") or "",) for s in block for c in [command(s.get("helper") or "")] if c]
            bad += [f"{name} json block: {b}" for b in wait_before_tap(seq)]
        for u in units(md):
            seq = [c + (sp,) for sp in SPAN.findall(u) for c in [command(sp)] if c]
            bad += [f"{name}: {b}" for b in wait_before_tap(seq)]
    return bad


# ---- step citations ---------------------------------------------------------------------------------------------------

ID = r"[A-Z]{2,5}-\d{3}"
NUMS = r"\d+(?:\s*-\s*\d+)?(?:(?:\s*,\s*|\s+and\s+)\d+(?:\s*-\s*\d+)?)*"
# "TERR-012 steps 3-10" or "PLAN-001 c3 steps 6 and 9": an id without its file, which the skills resolve to the newest
# version, whose steps are numbered anew
BARE = re.compile(rf"(?<![\w.-])({ID})(?!(?:\.c\d+)?\.json)(?:\s+c\d+)?(?:'s)?\s+(?:at\s+)?(?:its\s+|their\s+)?"
                  r"steps?\s+\d+")
FILED = re.compile(rf"`({ID}(?:\.c\d+)?\.json)`\s+(?:at\s+)?(?:its\s+|their\s+)?steps?\s+({NUMS})")
QUOTES = re.compile(r'^\s*[,(]?\s*"([^"]*)"((?:\s*(?:,\s*and|,|and|to)\s*"[^"]*")*)')


def numbers(text):
    out = []
    for part in re.split(r"\s*,\s*|\s+and\s+", text.strip()):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b) + 1)) if b.strip() else [int(a)]
    return out


def _norm(t):
    return " ".join(t.lower().split())


def starts(quote, text):
    """True when the step text starts with the quoted words (<…> in the quote stands for any words)."""
    pat = re.escape(_norm(quote))
    pat = re.sub(r"<[^>]*>", ".+?", pat.replace(r"\<", "<").replace(r"\>", ">"))
    return re.match(pat, _norm(text)) is not None


def citation_problems(docs):
    bad, cached = [], {}
    for name, text in docs.items():
        t = _flat(FENCE.sub(" ", text))
        for m in BARE.finditer(t):
            bad.append(f"{name}: bare citation {m.group(0)!r} (name the case file)")
        for m in FILED.finditer(t):
            f, nums = m.group(1), numbers(m.group(2))
            path = os.path.join(CASES, f)
            if not os.path.exists(path):
                bad.append(f"{name}: {f} is not in qa/cases")
                continue
            if f not in cached:
                cached[f] = {s["n"]: s for s in json.loads(_read(path))["steps"] if isinstance(s, dict)}
            by_n = cached[f]
            missing = [n for n in nums if n not in by_n]
            if missing:
                bad.append(f"{name}: {f} has no step {missing}")
                continue
            q = QUOTES.match(t[m.end():])
            for quote in ([q.group(1)] + re.findall(r'"([^"]*)"', q.group(2))) if q else []:
                if not any(starts(quote, by_n[n].get("text") or "") for n in nums):
                    bad.append(f"{name}: {f} steps {nums}: no step text starts with {quote!r}")
    return bad


# ---- the ClickUp lines of the skill's example (0000fake007, as cached on 2026-11-05) ----------------------------------

HEADING = "Using an existing map for flight planning custom terrain"
CHECKED = ("28 m resolution (and finer) is accepted and can be selected as custom terrain",
           "29m and coarser is in the list but can not be selected")
ALTITUDE = "Check in the altitude graph so the new terrain is used"
DESCRIPTION = (f"Test cases:\n*   {HEADING}\n    *   {CHECKED[0]}. {ALTITUDE}\n    *   {CHECKED[1]}\n"
               "*   Test with terrain from various latitudes and make sure we have the same resolution constraint "
               "everywhere. e.g.\n    *   Alaska\n    *   Antarctica\n    *   ...\n*   Anything else?\n")


class PlaybookBlocks(unittest.TestCase):
    """#26: the blocks keep the never-add rule and cite the case files they come from."""

    def setUp(self):
        self.pages = _pages()

    def test_no_block_waits_right_before_a_tap_for_its_own_target(self):
        self.assertEqual(never_add_problems(self.pages), [])
        # the check finds such a wait, in a json block and in a table row, and leaves a wait for something else
        own = ('{"text": "Wait for Create", "gates": [], "helper": "act.py {target} wait --expect text:Create"}\n'
               '{"text": "Create", "gates": [], "helper": "act.py {target} tap text:Create --expect js:true"}\n')
        self.assertEqual(len(never_add_problems({"x.md": f"```json\n{own}```\n"})), 1)
        enabled = ('{"text": "Wait until Create is enabled", "gates": [], "helper": "act.py {target} wait --expect '
                   '\\"js:![...document.querySelectorAll(\'button\')].find(b => b.innerText === \'Create\').disabled\\""}\n'
                   '{"text": "Create", "gates": [], "helper": "act.py {target} tap text:Create --expect js:true"}\n')
        self.assertEqual(len(never_add_problems({"x.md": f"```json\n{enabled}```\n"})), 1)
        self.assertEqual(len(never_add_problems({"x.md": "| x | `wait --expect text:Save`; `tap text:Save --expect "
                                                         "js:true` | y |"})), 1)
        other = ('{"text": "Wait for the name", "gates": [], "helper": "act.py {target} wait --expect text:QA_x"}\n'
                 '{"text": "Create", "gates": [], "helper": "act.py {target} tap text:Create --expect js:true"}\n')
        self.assertEqual(never_add_problems({"x.md": f"```json\n{other}```\n"}), [])

    def test_save_new_site_keeps_its_wait_as_a_find_timeout(self):
        block = next(b for b in json_blocks(self.pages["offline-sites.md"])
                     if any(s.get("text") == "New site (opens site creation with the name field)" for s in b))
        save = [s for s in block if 'tap "text:Save new site"' in s["helper"]]
        self.assertEqual(len(save), 1)
        self.assertIn("--find-timeout 60", save[0]["helper"])

    def test_the_never_add_rule_reads_the_same_everywhere(self):
        rule = "a wait right before a tap for that tap's own target"
        for name in ("qa-worker", "qa-run"):
            self.assertIn(rule, _flat(_skill(name)), name)
        self.assertIn("--find-timeout", _flat(_skill("qa-worker")))
        self.assertIn("No block waits right before a tap for that tap's own target", _flat(self.pages["README.md"]))

    def test_corrected_facts_stay_corrected(self):
        o = _flat(self.pages["offline-sites.md"])
        self.assertNotIn("no engine run yet", o)  # OFFL-001 c3 and OFFL-002 passed on TA5 and ET65 on 2026-11-03
        self.assertNotIn("stay in the CT account", o)  # the engine account since 2026-11-04

    @unittest.skipUnless(os.path.isdir(CASES), "no qa/cases next to this copy")
    def test_citations_name_the_case_file_and_match_it(self):
        docs = dict(self.pages, **{f"{n}/SKILL.md": _skill(n) for n in ("qa-worker", "qa-run")})
        self.assertEqual(citation_problems(docs), [])
        # the check finds the kinds of wrong citation the debug pass found
        for text in ("as TERR-012 steps 3-10 do", "`PLAN-001.c3.json` step 600", '`TERR-012.c1.json` step 1 ("Exit '
                     'without saving")', "`TERR-999.json` steps 1-2"):
            self.assertTrue(citation_problems({"x.md": text}), text)
        self.assertEqual(citation_problems({"x.md": '`TERR-012.c1.json` steps 1-4 ("Leave a flight setup\'s")'}), [])


class ConfirmRule(unittest.TestCase):
    """#5: a Confirm is a cloud write whatever its gates: refused from New test, and the texts say so."""

    def test_the_texts_say_a_confirm_is_refused_whatever_its_gates(self):
        w = _flat(_skill("qa-worker"))
        self.assertRegex(w, r"a Confirm \(`text:Confirm`, `aria:Confirm`, `sys-tap Confirm`,.{0,80}`testid:none`: a "
                            r"cloud write whatever its gates")
        self.assertIn("needs the person's OK first: it goes to `not_covered`", w)
        self.assertIn("a Confirm included whatever its gates", _flat(_skill("qa-run")))
        pages = _pages()
        self.assertIn("a Confirm included whatever its gates", _flat(pages["README.md"]))
        o = _flat(pages["offline-sites.md"])
        self.assertNotRegex(o, r"Edit flight Confirm \([^)]*\)[^.;]*between the site save and the flight save")
        self.assertRegex(o, r"Confirm[^.]{0,200}whatever its gates")

    def test_the_guard_refuses_every_confirm_the_texts_name(self):
        for helper, gates in (("act.py {target} tap text:Confirm --expect js:true", []),
                              ("act.py {target} tap text:Confirm --expect js:true", ["cloud_write"]),
                              ("act.py {target} tap text:Confirm --expect js:true", ["write_list", "cloud_write"]),
                              ("act.py {target} tap aria:Confirm --expect js:true", []),
                              ("act.py {target} sys-tap Confirm --expect js:true", []),
                              ("act.py {target} tap testid:none --expect js:true", []),
                              ("act.py {target} tap testid:btn-wrapper-none --expect js:true", [])):
            why = worker.new_test_write_problem({"n": 7, "text": "Confirm", "gates": gates, "helper": helper}, run, {})
            self.assertTrue(why and "Confirm" in why, f"{helper} with gates {gates}: {why}")


class ResultLinesRule(unittest.TestCase):
    """#2 and #4: what the skill says about result lines and Not covered is what the app does."""

    def setUp(self):
        self.w = _flat(_skill("qa-worker"))
        self.items = worker.task_items(DESCRIPTION)

    def test_the_skill_quotes_the_apps_refusal(self):
        msg = worker._heading_problem("TERR-099", [{"n": 1, "text": "x", "quote": HEADING, "check": True}], self.items)
        self.assertTrue(msg, "a check part that quotes only the heading is refused")
        self.assertIn("; ".join(msg.rsplit("; ", 2)[-2:]), self.w)  # 'quote the result lines this part checks; ...'
        self.assertIn("sentence by sentence", msg)
        self.assertIn("names the lines sentence by sentence", self.w)
        self.assertIn("Quote each sentence the part checks on its own", self.w)
        self.assertIn("a check part quotes the result lines it checks, each sentence on its own",
                      _flat(_skill("qa-run")))

    def test_the_skills_example_is_what_the_app_accepts(self):
        for line in CHECKED + (ALTITUDE, HEADING):
            self.assertIn(f'"{line}"', self.w)
        part = {"n": 1, "text": "Use existing map: 28 m selectable, 29 m not", "quote": [HEADING, *CHECKED],
                "check": True}
        self.assertIsNone(worker._heading_problem("TERR-099", [part], self.items))
        self.assertEqual(_uncovered(part), [ALTITUDE], "the app adds the line the example leaves out")

    def test_an_examples_list_and_the_skip_list_are_as_the_skill_says(self):
        self.assertIn('ends in "e.g.", "for example" or "such as" are examples, not results', self.w)
        norway = [{"n": 1, "text": "A QA_ site in Norway", "quote": "Norway", "check": False},
                  {"n": 2, "text": "The same limit", "quote": "make sure we have the same resolution constraint "
                                                             "everywhere", "check": True}]
        self.assertIsNone(worker._heading_problem("TERR-099", norway, self.items))
        for words in ("The app then adds what you left out", f"cut at {worker.NOT_COVERED_TEXT} characters with '…'",
                      f"at most {worker.ADDED_MAX} lines", "A check part quotes the result lines it checks"):
            self.assertIn(words, self.w)
        for sample in ("Anything else?", "Android", "Web", "Test cases:", "...", "etc.",
                       "[https://app.clickup.com/t/1/CL-1](https://app.clickup.com/t/1/CL-1)"):
            self.assertTrue(worker._no_task_line(sample), sample)


def _uncovered(part):
    """uncovered_task_lines for a plan of one case with the part, over DESCRIPTION as the task's ClickUp text."""
    with mock.patch.object(worker, "_intake_text", return_value=DESCRIPTION):
        return worker.uncovered_task_lines({"clickup": {}, "guidance": ""}, WS, [{"parts": [part]}], [])


class FollowUpRule(unittest.TestCase):
    """#14: Prepare again and Plan again drop an answer that no longer fits (serve._intake_check, answers_dropped);
    the skill asks that need again and says why."""

    def test_the_skill_reads_answers_dropped(self):
        w = _flat(_skill("qa-worker"))
        self.assertIn("`answers_dropped`", w)
        self.assertIn("Ask each of those needs again, with its `why` in the need's text", w)
        self.assertIn("When none fitted, the check has no `follows`", w)
        with open(os.path.join(APP_DIR, "serve.py"), encoding="utf-8") as f:
            self.assertIn('fields["answers_dropped"] = dropped', f.read())


if __name__ == "__main__":
    unittest.main()
