"""The start block and the clean-up block (2026-11-10, the person's OK after ClickUp test 0000fake009 failed): when the
worker answers a New test plan, the app refuses a case that opens a page (a step taps testid:btn-sites) without the 5
start steps, and a clean-up step that could leave a planner showing a flight that is not Hover's own
(worker.start_block_problem, called by worker._intake_plan; never at Run).

- The blocks in worker.py (START_BLOCK, CLEANUP_BLOCK, OWN_FLIGHT_JS) are the contract's steps, and the JSON blocks of
  this copy's playbooks/README.md, which the worker copies, are the same steps (ReadmeTests).
- A case written for the plan and a reused one are both checked; a case that taps no Sites button is not.
- The refusal names the case and the step and says what to do; the request stays claimed for the corrected answer.

python3 -I qa/app/tests/test_start_block.py  (no network, no devices: files of this copy and a temporary workspace)
"""
import copy
import json
import os
import re
import shlex
import subprocess
import sys
import unittest
from unittest import mock

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(TESTS_DIR)
QA_DIR = os.path.dirname(APP_DIR)  # this copy's qa folder (qa, or a staging copy), never another one
sys.path[:0] = [TESTS_DIR, APP_DIR, os.path.join(QA_DIR, "runner")]
import act  # noqa: E402
import autonomy  # noqa: E402
import case_schema  # noqa: E402
import run  # noqa: E402
import test_intake  # noqa: E402 (test classes are reached through their module, so they do not run again here)
import test_needs_page  # noqa: E402
import test_worker  # noqa: E402
import worker  # noqa: E402
from test_parts import CLEAN, MAPS, block_steps, parts_case  # noqa: E402
from test_review_flight_save import fresh_case  # noqa: E402 (OFFL-001 c5's steps, written before the start block)
from test_worker import LATER, NEW_TID, put  # noqa: E402

README = os.path.join(QA_DIR, "playbooks", "README.md")
FENCE = re.compile(r"```json\n(.*?)```", re.S)
ENG = {"env": "staging", "test_data": {"site": {"id": "S1", "env": "staging"}}}

# The contract of 2026-11-10, written out. OWN_JS reads the planner's flight name: the label, or its input while the
# name is edited (WingtraCloud 8203f39 and 82e23ff, FlightNameEditableLabel.tsx).
OWN_JS = ("(((document.querySelector('[data-testid=flight-name-editable-label]')||{}).innerText||(document."
          "querySelector('[data-testid=flight-name-editable-label-input]')||{}).value||'').trim().startsWith('QA_'))")
# The 5 start steps and the 3 clean-up steps as the README writes them, one JSON step per line: the 4 start steps and
# the 3 clean-up steps of the README of 2026-11-09 (its lines 57-60 and 71-73), plus the guard as step 2 and the
# guarded when_js on the first two clean-up steps (OWN_JS stands for the expression above). The texts are free.
CONTRACT_START = r'''
{"text": "Leave a flight setup's 'Do you want to exit?' dialog when one is open (Exit; writes nothing)", "gates": [], "when_js": "/Do you really want to exit\\?/.test(document.body.innerText)", "helper": "act.py {target} tap text:Exit --expect \"js:!/Do you really want to exit\\?/.test(document.body.innerText)\" --timeout 20"}
{"text": "Stop when the planner shows a flight that is not Hover's own (name not starting with QA_): a person's flight is never left or dropped; close it by hand, then run again", "gates": [], "when_js": "location.pathname.includes('/planning/')", "helper": "act.py {target} wait --expect \"js:OWN_JS\" --timeout 5"}
{"text": "Top bar: Sites (a planner or a flight setup left open asks first)", "gates": [], "helper": "act.py {target} tap testid:btn-sites --expect \"js:/Exit without saving|Do you really want to exit\\?/.test(document.body.innerText) || location.pathname === '/sites'\" --timeout 20"}
{"text": "Exit without saving, when the planner asks (writes nothing)", "gates": [], "when_js": "/Exit without saving/.test(document.body.innerText)", "helper": "act.py {target} tap \"text:Exit without saving\" --expect \"js:location.pathname === '/sites'\" --timeout 20"}
{"text": "Exit, when a flight setup asks 'Do you want to exit?' (writes nothing)", "gates": [], "when_js": "/Do you really want to exit\\?/.test(document.body.innerText)", "helper": "act.py {target} tap text:Exit --expect \"js:location.pathname === '/sites'\" --timeout 20"}
'''
CONTRACT_CLEANUP = r'''
{"text": "Clean-up: leave the planner (always; a planner showing a flight that is not Hover's own stays as it is)", "gates": [], "always": true, "when_js": "!location.pathname.includes('/planning/') || OWN_JS", "helper": "act.py {target} tap testid:btn-sites --expect \"js:/Exit without saving|Do you really want to exit\\?/.test(document.body.innerText) || location.pathname === '/sites'\" --timeout 20"}
{"text": "Exit without saving when asked (always; writes nothing; only for Hover's own flight)", "gates": [], "always": true, "when_js": "/Exit without saving/.test(document.body.innerText) && OWN_JS", "helper": "act.py {target} tap \"text:Exit without saving\" --expect \"js:location.pathname === '/sites'\" --timeout 20"}
{"text": "Exit when a flight setup asks 'Do you want to exit?' (always; writes nothing)", "gates": [], "always": true, "when_js": "/Do you really want to exit\\?/.test(document.body.innerText)", "helper": "act.py {target} tap text:Exit --expect \"js:location.pathname === '/sites'\" --timeout 20"}
'''
FIX = ("Write a new case version that copies the start block and the clean-up block of qa/playbooks/README.md whole: "
       "each step's when_js and helper exactly as written there ({target} stays), no read_js and no gate on them; only "
       "the step text is free. The app checks that version as a case written for this plan (parts and quotes too), so "
       "a case without parts also gets its parts.")
NO_WHEN, WHEN_DIFFERS = "it has no when_js", "its when_js differs"
EXTRA_WHEN, HELPER_DIFFERS = "it has a when_js, the block's step has none", "its helper differs"


def contract(lines):
    """The steps of CONTRACT_START or CONTRACT_CLEANUP, with OWN_JS written out as the README writes it."""
    return [json.loads(line.replace("OWN_JS", OWN_JS)) for line in lines.strip().splitlines()]


def readme_block(heading):
    """The steps of the first json block under the README heading that starts with these words."""
    with open(README, encoding="utf-8") as f:
        md = f.read()
    m = FENCE.search(md, md.index("### " + heading))
    return [json.loads(line) for line in m.group(1).splitlines() if line.strip()]


def acts(steps):
    """What the app compares in a step, and what decides whether the runner runs it as written."""
    return [(s.get("when_js"), s.get("helper"), s.get("gates"), s.get("always"), s.get("read_js"), s.get("on"))
            for s in steps]


def block_acts(block, always=None):
    return [(b["when_js"], b["helper"], [], always, None, None) for b in block]


def problem(case):
    return worker.start_block_problem(case.get("id", "TERR-901"), case, run)


def numbered(steps):
    for n, s in enumerate(steps, 1):
        s["n"] = n
    return steps


def old_clean_up():
    """The clean-up as it was written before 2026-11-10: Sites, then Exit without saving when asked, both unguarded."""
    return [{"text": "Clean-up: leave the planner (always)", "gates": [], "always": True, "helper": worker.SITES_TAP},
            {"text": "Exit without saving when asked (always)", "gates": [], "always": True,
             "when_js": worker.EXIT_UNSAVED_JS, "helper": worker.EXIT_UNSAVED_TAP}]


def old_case(cid="TERR-020", created="2026-10-20T15:40:00.000Z"):
    """A hands-off tablet case as they were written before the blocks: Sites, Exit without saving when asked, a read,
    then the old clean-up. Older than any request: a reused case, which no other plan-time check reads as written."""
    return {"kind": "case", "id": cid, "status": "active", "targets": "tablet", "created": created, "steps": numbered([
        {"text": "Top bar: Sites", "gates": [], "helper": worker.SITES_TAP},
        {"text": "Exit without saving, when the app asks", "gates": [], "when_js": worker.EXIT_UNSAVED_JS,
         "helper": worker.EXIT_UNSAVED_TAP},
        {"text": "read", "gates": [], "read_js": "1"}] + old_clean_up())}


class ContractTests(unittest.TestCase):
    """worker.py's constants are the contract's steps, and the engine takes them."""

    def test_it_checks_this_copy(self):
        self.assertEqual(os.path.dirname(os.path.abspath(worker.__file__)), APP_DIR)

    def test_the_own_flight_expression(self):
        self.assertEqual(worker.OWN_FLIGHT_JS, OWN_JS)
        self.assertNotIn('"', OWN_JS)  # it sits inside a helper's double quotes
        self.assertNotIn("\\", OWN_JS)

    def test_the_blocks_are_the_contracts_steps(self):
        self.assertEqual(acts(contract(CONTRACT_START)), block_acts(worker.START_BLOCK))
        self.assertEqual(acts(contract(CONTRACT_CLEANUP)), block_acts(worker.CLEANUP_BLOCK, always=True))
        self.assertEqual((len(worker.START_BLOCK), len(worker.CLEANUP_BLOCK)), (5, 3))
        self.assertEqual(worker.START_BLOCK[1], {
            "when_js": "location.pathname.includes('/planning/')",
            "helper": f'act.py {{target}} wait --expect "js:{OWN_JS}" --timeout 5'})
        # the clean-up leaves a planner only for Hover's own flight; outside a planner the Sites tap runs as before
        self.assertEqual(worker.CLEANUP_BLOCK[0]["when_js"], f"!location.pathname.includes('/planning/') || {OWN_JS}")
        self.assertEqual(worker.CLEANUP_BLOCK[1]["when_js"],
                         f"/Exit without saving/.test(document.body.innerText) && {OWN_JS}")
        # the steps that were not changed are the README's of 2026-11-09: one helper per control
        self.assertEqual([b["helper"] for b in worker.CLEANUP_BLOCK], [b["helper"] for b in worker.START_BLOCK[2:]])

    def test_the_engine_takes_every_block_step(self):
        # act.py parses each helper; the page JS only reads (New test's JS guard and write guard pass it); no block
        # step reads the page for the parts rule, so the start steps can stand in any part
        for name, block in (("start", worker.START_BLOCK), ("clean-up", worker.CLEANUP_BLOCK)):
            for k, s in enumerate(numbered(block_steps(block)), 1):
                a = act.parser().parse_args(shlex.split(s["helper"].replace("{target}", "TA5"))[1:])
                self.assertIn(a.verb, ("tap", "wait"), (name, k))
                self.assertEqual([js for js in run.step_js(s) if worker.js_acts(js, run)], [], (name, k))
                self.assertIsNone(worker.new_test_write_problem(s, run, {"steps": [s]}), (name, k))
                self.assertFalse(case_schema._reads(s), (name, k))
        guard = act.parser().parse_args(shlex.split(worker.START_BLOCK[1]["helper"].replace("{target}", "TA5"))[1:])
        self.assertEqual((guard.verb, guard.timeout, guard.expect), ("wait", 5.0, ["js:" + OWN_JS]))

    def test_a_case_with_the_blocks_passes_every_other_plan_time_check(self):
        case = parts_case()
        self.assertEqual(case_schema.validate_case(case, for_new_test=True), [])
        self.assertEqual([p for p in (worker.new_test_write_problem(s, run, case) for s in case["steps"]) if p], [])
        verdict = autonomy.check(case, eng=ENG)
        self.assertTrue(verdict["hands_off"], verdict["blockers"])
        self.assertIsNone(problem(case))


PLANNER = "/sites/5f0c/planning/9a1d"
ASKS = "Exiting the flight planning Do you want to save your changes? Exit without saving Save as draft"
# fake pages: the path, the planner's name label (None: not on the page), its input while the name is edited, the text
PAGES = {
    "sites": ("/sites", None, None, "Sites"),
    "site": ("/sites/5f0c", None, None, "Flights"),
    "own": (PLANNER, "QA_TA5 Offline flight", None, ""),
    "own padded": (PLANNER, "  QA_TA5 x ", None, ""),
    "own, name edited": (PLANNER, None, "QA_TA5 new name", ""),
    "own, asks": (PLANNER, "QA_TA5 Offline flight", None, ASKS),
    "person's": (PLANNER, "Customterrain_03", None, ""),
    "person's, lower case qa_": (PLANNER, "qa_mine", None, ""),
    "person's, name edited": (PLANNER, None, "Customterrain_03", ""),
    "person's, asks": (PLANNER, "Customterrain_03", None, ASKS),
    "no name on the screen": (PLANNER, None, None, ""),
    "flight setup asks": ("/surveying/9a1d", None, None, "Do you want to exit? Do you really want to exit? Exit"),
}
OWN = {"own", "own padded", "own, name edited", "own, asks"}
NO_PLANNER = {"sites", "site", "flight setup asks"}
JS_HARNESS = """
const exprs = %s, pages = %s, out = {};
for (const e in exprs) {
  out[e] = [];
  for (const n in pages) {
    const p = pages[n];
    const doc = {body: {innerText: p[3]}, querySelector: function (sel) {
      if (sel === '[data-testid=flight-name-editable-label]') return p[1] === null ? null : {innerText: p[1]};
      if (sel === '[data-testid=flight-name-editable-label-input]') return p[2] === null ? null : {value: p[2]};
      return null;
    }};
    if ((new Function('document', 'location', 'return (' + exprs[e] + ');'))(doc, {pathname: p[0]})) out[e].push(n);
  }
}
print(JSON.stringify(out));
"""


@unittest.skipUnless(test_needs_page._jsc(), "needs macOS JavaScriptCore (jsc)")
class PageJsTests(unittest.TestCase):
    """The blocks' page expressions, run in macOS JavaScriptCore against fake pages (no device, no browser): they
    parse, and Hover leaves a planner only when it can read that the open flight's name starts with QA_."""

    @classmethod
    def setUpClass(cls):
        start, clean = worker.START_BLOCK, worker.CLEANUP_BLOCK
        exprs = {"own flight": worker.OWN_FLIGHT_JS}
        for name, block in (("start", start), ("clean-up", clean)):
            for k, s in enumerate(block_steps(block), 1):
                when, expects = s.get("when_js"), run.helper_call(s)[2]
                exprs.update({f"{name} {k} when": when} if when else {})
                exprs.update({f"{name} {k} expect": e[3:] for e in expects if e.startswith("js:")})
        p = subprocess.run([test_needs_page._jsc(), "-e", JS_HARNESS % (json.dumps(exprs), json.dumps(PAGES))],
                           capture_output=True, text=True, timeout=60)
        assert p.returncode == 0, p.stderr or p.stdout
        cls.true_on = {k: set(v) for k, v in json.loads(p.stdout.strip().splitlines()[-1]).items()}

    def test_every_expression_ran(self):
        both = ("when", "expect")
        self.assertEqual(sorted(self.true_on), sorted(
            ["own flight"] + [f"start {k} {x}" for k in range(1, 6) for x in both if (k, x) != (3, "when")]
            + [f"clean-up {k} {x}" for k in range(1, 4) for x in both]))

    def test_the_flight_is_hovers_own_only_by_a_name_it_can_read(self):
        self.assertEqual(self.true_on["own flight"], OWN)

    def test_the_guard_runs_in_every_planner_and_passes_only_for_hovers_own_flight(self):
        self.assertEqual(self.true_on["start 2 when"], set(PAGES) - NO_PLANNER)
        self.assertEqual(self.true_on["start 2 expect"], OWN)

    def test_the_clean_up_leaves_no_planner_that_shows_another_flight(self):
        self.assertEqual(self.true_on["clean-up 1 when"], OWN | NO_PLANNER)  # outside a planner: Sites as before
        self.assertEqual(self.true_on["clean-up 2 when"], {"own, asks"})   # never "person's, asks"

    def test_the_steps_that_were_not_changed_read_the_dialogs_as_before(self):
        asks = {"own, asks", "person's, asks"}
        self.assertEqual(self.true_on["start 4 when"], asks)
        self.assertEqual(self.true_on["start 3 expect"], asks | {"sites", "flight setup asks"})
        for name in ("start 1 when", "start 5 when", "clean-up 3 when"):
            self.assertEqual(self.true_on[name], {"flight setup asks"}, name)
        self.assertEqual(self.true_on["start 1 expect"], set(PAGES) - {"flight setup asks"})
        for name in ("start 4 expect", "start 5 expect", "clean-up 2 expect", "clean-up 3 expect"):
            self.assertEqual(self.true_on[name], {"sites"}, name)


class ReadmeTests(unittest.TestCase):
    """The README's JSON blocks, which the worker copies, are the app's blocks: what it copies is what the app takes."""

    def test_the_start_block(self):
        self.assertEqual(acts(readme_block("Start block")), block_acts(worker.START_BLOCK))

    def test_the_clean_up_block(self):
        self.assertEqual(acts(readme_block("Clean-up block")), block_acts(worker.CLEANUP_BLOCK, always=True))

    def test_a_case_copied_from_the_readme_is_planned(self):
        case = parts_case()
        start = [dict(s, part=1) for s in readme_block("Start block")]
        case["steps"] = numbered(start + case["steps"][MAPS:CLEAN] + readme_block("Clean-up block"))
        self.assertIsNone(problem(case))
        self.assertEqual(case_schema.validate_case(case, for_new_test=True), [])


class ExitWithoutSavingTests(unittest.TestCase):
    """Review 2026-11-11: "Exit without saving" outside clean-up runs only as the start block's step 4 (after the guard)
    or with the clean-up block's guarded when_js, whichever control opened the dialog; any truthy always is clean-up."""

    def refused(self, case, n):
        self.assertEqual(problem(case), f"TERR-901 step {n} taps Exit without saving outside the start block's step 4 "
                                        "and outside clean-up, without the clean-up block's guard (its second step's "
                                        f"when_js), so it could drop the unsaved changes of a person's flight. {FIX}")

    def exit_step(self, when=None):
        s = {"text": "Exit without saving, when the app asks", "gates": [], "helper": worker.EXIT_UNSAVED_TAP}
        if when:
            s["when_js"] = when
        return s

    def test_after_another_control_it_is_refused(self):
        # the review's case: the planner left by Maps, then an unguarded Exit without saving; no Sites tap at all
        for h in ("act.py {target} tap testid:btn-maps --expect \"js:/Exit without saving/.test(document.body.innerText)"
                  " || location.pathname === '/maps'\" --timeout 20",
                  "act.py {target} tap testid:WingtraLogo --expect js:true", "act.py {target} key back --expect js:true"):
            case = {"id": "TERR-901", "targets": "tablet", "steps": numbered([
                {"text": "leave", "gates": [], "helper": h}, self.exit_step(worker.EXIT_UNSAVED_JS),
                {"text": "read", "gates": [], "read_js": "1"}])}
            self.refused(case, 2)

    def test_a_second_unguarded_exit_after_the_start_block_is_refused(self):
        case = parts_case()
        case["steps"].insert(CLEAN, self.exit_step(worker.EXIT_UNSAVED_JS))
        numbered(case["steps"])
        self.refused(case, CLEAN + 1)

    def test_with_the_guard_it_passes_anywhere(self):
        case = parts_case()
        case["steps"].insert(CLEAN, self.exit_step(worker.CLEANUP_BLOCK[1]["when_js"]))
        numbered(case["steps"])
        self.assertIsNone(problem(case))
        alone = {"id": "TERR-901", "targets": "tablet", "steps": numbered([
            {"text": "Maps", "gates": [], "helper": "act.py {target} tap testid:btn-maps --expect js:true"},
            self.exit_step(worker.CLEANUP_BLOCK[1]["when_js"])])}
        self.assertIsNone(problem(alone))

    def test_step_4_is_free_only_after_the_whole_start_block(self):
        # step 4 written as the block's step 4, but no guard before it: refused
        case = {"id": "TERR-901", "targets": "tablet", "steps": numbered([
            {"text": "x", "gates": [], "helper": "act.py {target} wait --expect js:true"},
            {"text": "y", "gates": [], "helper": "act.py {target} wait --expect js:true"},
            {"text": "Maps", "gates": [], "helper": "act.py {target} tap testid:btn-maps --expect js:true"},
            dict(self.exit_step(worker.EXIT_UNSAVED_JS))])}
        self.refused(case, 4)
        self.assertIsNone(problem(parts_case()))  # the start block's own step 4 passes

    def test_any_truthy_always_is_clean_up(self):
        for always in (1, "true", "yes"):
            case = parts_case()
            case["steps"][CLEAN]["always"] = always
            case["steps"][CLEAN].pop("when_js")
            self.assertTrue(problem(case).startswith(f"TERR-901 step {CLEAN + 1} taps testid:btn-sites in clean-up"),
                            always)
            case = parts_case()
            case["steps"][CLEAN + 1]["always"] = always
            case["steps"][CLEAN + 1]["when_js"] = worker.EXIT_UNSAVED_JS
            self.assertTrue(problem(case).startswith(f"TERR-901 step {CLEAN + 2} taps Exit without saving in clean-up"),
                            always)


class StartBlockTests(unittest.TestCase):
    """Rule a: a case that opens a page begins with the 5 start steps, in order."""

    def refused(self, case, step, why, opens=3):
        """The refusal names the case, the step that opens a page, the first step that is not the block's and why, and
        says what to do. why None: the case has no such step."""
        what = f"it has no step {step}" if why is None else f"its step {step} is not that block's step {step} ({why})"
        self.assertEqual(problem(case), f"TERR-901 opens a page (step {opens} taps testid:btn-sites) but does not "
                                        f"begin with the 5 steps of the start block: {what}. {FIX}")

    def test_a_case_with_the_blocks_is_planned(self):
        self.assertIsNone(problem(parts_case()))

    def test_the_step_text_is_free_and_the_helper_is_read_as_the_runner_splits_it(self):
        case = parts_case()
        for s in case["steps"]:
            s["text"] = "Any words"
        case["steps"][3]["helper"] = ("act.py {target} tap 'text:Exit without saving' --expect "
                                      "\"js:location.pathname === '/sites'\"   --timeout 20")  # other quotes and spaces
        case["steps"][2]["pause_s"] = 1
        case["steps"][0]["when_js"] = "  " + case["steps"][0]["when_js"] + " "
        self.assertIsNone(problem(case))

    def test_a_case_that_taps_no_sites_button_is_not_checked(self):
        upg = {"id": "UPG-013", "targets": "tablet", "steps": [{"n": 1, "text": "read", "gates": [], "read_js": "1"}]}
        self.assertIsNone(problem(upg))
        maps = parts_case()
        maps["steps"] = numbered(maps["steps"][MAPS:CLEAN])  # Maps and the read only
        self.assertIsNone(problem(maps))
        for odd in ({}, {"steps": None}, {"steps": "x"}, {"steps": [None, 5, "tap"]}, None,
                    {"steps": [{"n": 1, "helper": "act.py {target} tap 'text:unbalanced"}]},
                    {"steps": [{"n": 1, "helper": 5, "always": True}]}):
            self.assertIsNone(worker.start_block_problem("X-001", odd, run), odd)

    def test_todays_et65_case_is_refused(self):
        # OFFL-003 c2 (2026-11-10) kept 2 of the then 4 start steps, the flight-setup Exit and Sites, and went on to
        # the site search; a planner left open by a crashed run stopped it at step 3, behind "Exit without saving?"
        case = parts_case()
        start = case["steps"][:5]
        case["steps"] = numbered([start[0], start[2]] + case["steps"][MAPS:])
        self.refused(case, 2, NO_WHEN, opens=2)

    def test_each_start_step_must_be_there_in_order(self):
        # one step left out: the step that then stands in its place is named
        for k, why in enumerate((WHEN_DIFFERS, NO_WHEN, EXTRA_WHEN, WHEN_DIFFERS, NO_WHEN)):
            case = parts_case()
            del case["steps"][k]
            numbered(case["steps"])
            self.assertIn(f": its step {k + 1} is not that block's step {k + 1} ({why}). Write a new case version",
                          problem(case), k)
        case = parts_case()  # the guard after the Sites tap: too late, the tap already asked to leave
        case["steps"][1], case["steps"][2] = case["steps"][2], case["steps"][1]
        numbered(case["steps"])
        self.refused(case, 2, NO_WHEN, opens=2)
        case = parts_case()  # a step of its own before the block
        case["steps"] = numbered([{"text": "Top bar: Maps", "gates": [], "part": 1,
                                   "helper": "act.py {target} tap testid:btn-maps --expect path:/maps"}]
                                 + case["steps"])
        self.refused(case, 1, NO_WHEN, opens=4)
        case = parts_case()  # the case ends after the Sites tap
        case["steps"] = case["steps"][:3]
        self.refused(case, 4, None)

    def test_a_changed_when_js_or_helper_is_not_the_block_step(self):
        start = worker.START_BLOCK
        changes = [
            (0, {"when_js": "/Do you want to exit\\?/.test(document.body.innerText)"}, WHEN_DIFFERS),
            (1, {"when_js": "location.pathname.includes('/planning')"}, WHEN_DIFFERS),
            (1, {"when_js": None}, NO_WHEN),
            (2, {"when_js": worker.EXIT_UNSAVED_JS}, EXTRA_WHEN),
            (3, {"when_js": worker.EXIT_UNSAVED_JS + " && " + OWN_JS}, WHEN_DIFFERS),
            (0, {"helper": start[0]["helper"].replace("{target}", "TA5")}, HELPER_DIFFERS),  # {target} stays
            (1, {"helper": start[1]["helper"].replace("QA_", "QA")}, HELPER_DIFFERS),
            (1, {"helper": start[1]["helper"].replace("--timeout 5", "--timeout 0")}, HELPER_DIFFERS),
            (1, {"helper": 'act.py {target} wait --expect "js:true" --timeout 5'}, HELPER_DIFFERS),
            (2, {"helper": "act.py {target} tap testid:btn-sites --expect path:/sites --timeout 20"}, HELPER_DIFFERS),
            (3, {"helper": worker.EXIT_UNSAVED_TAP + " --nth 1"}, HELPER_DIFFERS),
            (4, {"helper": None}, HELPER_DIFFERS),
            (4, {"helper": "act.py {target} tap 'text:Exit"}, HELPER_DIFFERS),  # cannot be split
        ]
        for k, change, why in changes:
            case = parts_case()
            case["steps"][k].update(change)
            if "when_js" in change and change["when_js"] is None:
                del case["steps"][k]["when_js"]
            self.refused(case, k + 1, why)

    def test_a_block_step_the_runner_would_not_run_as_written_is_not_the_block_step(self):
        changes = [({"read_js": "1"}, "it has read_js, which runs in the helper's place"),
                   ({"gates": ["write_list"]}, "it has a gate"), ({"gates": ["cloud_write"]}, "it has a gate"),
                   ({"on": "web"}, "it does not run on every device the case runs on")]
        for k in range(5):
            for change, why in changes:
                case = parts_case()
                case["steps"][k].update(change)
                self.refused(case, k + 1, why)
        case = parts_case()  # a tablet case may say on: tablet; a case for both kinds of device may not
        for s in case["steps"][:5]:
            s["on"] = "tablet"
        self.assertIsNone(problem(case))
        case["targets"] = "both"
        self.refused(case, 1, "it does not run on every device the case runs on")

    def test_a_sites_tap_anywhere_makes_the_case_open_a_page(self):
        own = parts_case()["steps"][MAPS:CLEAN]
        taps = ["act.py {target} tap testid:btn-sites --expect path:/sites --timeout 20",
                "act.py {target} tap testid:btn-wrapper-btn-sites --expect path:/sites",  # the shared Button's wrapper
                "act.py {target} tap --timeout 20 testid:btn-sites --expect path:/sites"]
        for h in taps:
            for extra in ({"part": 1}, {"always": True, "when_js": worker.CLEANUP_BLOCK[0]["when_js"]}):
                case = parts_case()
                case["steps"] = numbered(copy.deepcopy(own) + [dict({"text": "Sites", "gates": [], "helper": h},
                                                                    **extra)])
                self.refused(case, 1, NO_WHEN)
        # a wait for the Sites button, a find, or a tap on another button opens no page by this rule
        for h in ("act.py {target} wait --expect sel:testid:btn-sites", "act.py {target} find testid:btn-sites",
                  "act.py {target} tap testid:btn-sites-search --expect sel:testid:input-sites-search"):
            case = parts_case()
            case["steps"] = numbered(copy.deepcopy(own) + [{"text": "x", "gates": [], "part": 1, "helper": h}])
            self.assertIsNone(problem(case), h)

    def test_the_cases_written_before_the_blocks_are_refused(self):
        # OFFL-001 c5 (2026-11-03) starts with Sites and Exit without saving: 2 of the 5 steps
        self.assertEqual(worker.start_block_problem("OFFL-901", fresh_case(), run),
                         "OFFL-901 opens a page (step 1 taps testid:btn-sites) but does not begin with the 5 steps of "
                         f"the start block: its step 1 is not that block's step 1 ({NO_WHEN}). {FIX}")
        # the README's start block of 2026-11-09 (4 steps, no guard) is refused at its step 2
        case = parts_case()
        del case["steps"][1]
        numbered(case["steps"])
        self.refused(case, 2, NO_WHEN, opens=2)


class CleanUpTests(unittest.TestCase):
    """Rule b: an always: true step that taps Sites or Exit without saving carries the clean-up block's when_js."""

    def refused(self, case, n, what):
        self.assertEqual(problem(case), f"TERR-901 step {n} taps {what} in clean-up (always: true) without the when_js "
                                        "that step has in the clean-up block, so it could leave a planner that shows a "
                                        f"person's flight (a name that does not start with QA_). {FIX}")

    def test_the_clean_up_block_passes(self):
        self.assertIsNone(problem(parts_case()))
        case = parts_case()  # an area's own clean-up steps before the block and after it
        case["steps"][CLEAN:CLEAN] = [{"text": "Bring the app to the front", "gates": [], "always": True,
                                       "helper": "act.py {target} ready"}]
        case["steps"].append({"text": "Clean-up: Wi-Fi back on (always)", "gates": [], "always": True,
                              "helper": "act.py {target} wifi on"})
        numbered(case["steps"])
        self.assertIsNone(problem(case))

    def test_leaving_the_planner_unguarded_is_refused(self):
        sites, unsaved = CLEAN, CLEAN + 1  # the block's first two steps in parts_case
        for when in (None, worker.IN_PLANNER_JS, worker.OWN_FLIGHT_JS, worker.CLEANUP_BLOCK[1]["when_js"],
                     "!location.pathname.includes('/planning/')", worker.CLEANUP_BLOCK[0]["when_js"] + " || true"):
            case = parts_case()
            case["steps"][sites].pop("when_js")
            if when:
                case["steps"][sites]["when_js"] = when
            self.refused(case, sites + 1, "testid:btn-sites")
        for when in (None, worker.EXIT_UNSAVED_JS, worker.OWN_FLIGHT_JS, worker.CLEANUP_BLOCK[0]["when_js"]):
            case = parts_case()
            case["steps"][unsaved].pop("when_js")
            if when:
                case["steps"][unsaved]["when_js"] = when
            self.refused(case, unsaved + 1, "Exit without saving")

    def test_the_old_clean_up_is_refused(self):
        case = parts_case()
        case["steps"] = numbered(case["steps"][:CLEAN] + old_clean_up())
        self.refused(case, CLEAN + 1, "testid:btn-sites")

    def test_by_any_selector_that_names_the_control(self):
        for h, what in (("act.py {target} tap testid:btn-wrapper-btn-sites --expect path:/sites", "testid:btn-sites"),
                        ("act.py {target} tap 'text:Exit  without SAVING' --expect path:/sites", "Exit without saving"),
                        ("act.py {target} tap 'aria:Exit without saving' --expect path:/sites", "Exit without saving"),
                        ("act.py {target} sys-tap 'Exit without saving' --expect focus:app", "Exit without saving")):
            case = parts_case()
            case["steps"].append({"text": "x", "gates": [], "always": True, "helper": h,
                                  "when_js": worker.EXIT_UNSAVED_JS})
            numbered(case["steps"])
            self.refused(case, len(case["steps"]), what)

    def test_only_clean_up_steps_and_only_these_two_taps(self):
        case = parts_case()  # the start block's own Sites and Exit without saving taps are no clean-up
        self.assertEqual([s.get("always") for s in case["steps"][2:4]], [None, None])
        # Exit of a flight setup's dialog, Cancel and a wait stay as the area writes them
        case["steps"] += [{"text": "x", "gates": [], "always": True, "helper": h} for h in (
            worker.EXIT_SETUP_TAP, "act.py {target} tap text:Cancel --expect js:true",
            "act.py {target} wait --expect \"text:Exit without saving\" --timeout 5")]
        numbered(case["steps"])
        self.assertIsNone(problem(case))

    def test_a_case_that_opens_no_page_is_held_to_it_too(self):
        # Maps only, then "Exit without saving" in clean-up: the tap that drops a person's work is guarded everywhere
        case = parts_case()
        case["steps"] = numbered(case["steps"][MAPS:CLEAN] + old_clean_up()[1:])
        self.refused(case, 3, "Exit without saving")
        case["steps"][2]["when_js"] = worker.CLEANUP_BLOCK[1]["when_js"]
        self.assertIsNone(problem(case))


class PlanTimeTests(test_worker.Base):
    """worker.answer_check on a New test plan: every case of the plan, written for it or reused; the request stays
    claimed after a refusal, and a new case version with the blocks is then planned."""

    intake = test_worker.PlanTitleAndPartsTests.intake
    answer_plan = test_worker.PlanTitleAndPartsTests.answer_plan
    refused = test_worker.PlanTitleAndPartsTests.refused
    pinned = test_worker.PlanTitleAndPartsTests.pinned

    def setUp(self):
        super().setUp()
        put(self.root, f"qa/cache/intake/{NEW_TID}.json", {"kind": "intake", "schema": 1, "task_id": NEW_TID,
                                                            "title": test_worker.CLICKUP_NAME, "cases": [],
                                                            "description": ""})

    def version(self, cid, clean_up=None):
        """A complete case record written for this plan (after the request), as the worker writes a new version: the
        start block, its own steps and the clean-up block, or the given clean-up steps in the block's place."""
        case = parts_case(cid=cid, created=LATER, ref=NEW_TID)
        if clean_up is not None:
            case["steps"] = numbered(case["steps"][:CLEAN] + clean_up)
        return case

    def test_a_reused_case_without_the_start_block_is_refused_until_a_new_version_has_the_blocks(self):
        put(self.root, "qa/cases/TERR-020.json", old_case())  # older than the request: a reused case
        rid = self.intake()["id"]
        worker.claim(self.root, rid)
        self.assertEqual(self.refused(rid, ["TERR-020"]),
                         "TERR-020 opens a page (step 1 taps testid:btn-sites) but does not begin with the 5 steps of "
                         f"the start block: its step 1 is not that block's step 1 ({NO_WHEN}). {FIX}")
        # a new version with the start block but the old clean-up: the unguarded Sites tap is named
        put(self.root, "qa/cases/TERR-020.c1.json", self.version("TERR-020", old_clean_up()))
        self.assertEqual(self.refused(rid, ["TERR-020"]),
                         f"TERR-020 step {CLEAN + 1} taps testid:btn-sites in clean-up (always: true) without the "
                         "when_js that step has in the clean-up block, so it could leave a planner that shows a "
                         f"person's flight (a name that does not start with QA_). {FIX}")
        # both blocks copied whole, as a new case version: the same request takes it
        put(self.root, "qa/cases/TERR-020.c2.json", self.version("TERR-020"))
        done = self.answer_plan(rid, ["TERR-020"], claim=False)
        self.assertEqual([(c["id"], c["file"], c["changed_in_plan"]) for c in done["result"]["plan_cases"]],
                         [("TERR-020", "qa/cases/TERR-020.c2.json", True)])

    def test_a_case_written_for_the_plan_is_checked_too(self):
        rid = self.intake()["id"]
        worker.claim(self.root, rid)
        case = self.version("TERR-901")
        del case["steps"][3]  # "Exit without saving, when the planner asks": the step OFFL-003 c2 had lost
        put(self.root, "qa/cases/TERR-901.json", dict(case, steps=numbered(case["steps"])))
        self.assertEqual(self.refused(rid, ["TERR-901"]),
                         "TERR-901 opens a page (step 3 taps testid:btn-sites) but does not begin with the 5 steps of "
                         f"the start block: its step 4 is not that block's step 4 ({WHEN_DIFFERS}). {FIX}")
        put(self.root, "qa/cases/TERR-901.json", self.version("TERR-901"))
        done = self.answer_plan(rid, ["TERR-901"], claim=False)
        self.assertEqual([(c["id"], c["new"]) for c in done["result"]["plan_cases"]], [("TERR-901", True)])

    def test_every_case_of_the_plan_is_checked_and_a_case_without_a_sites_tap_passes(self):
        put(self.root, "qa/cases/TERR-020.json", old_case())
        rid = self.intake()["id"]
        worker.claim(self.root, rid)
        self.assertTrue(self.refused(rid, ["TERR-004", "TERR-020"]).startswith("TERR-020 opens a page (step 1 taps"))
        done = self.answer_plan(rid, ["TERR-004"], claim=False)  # the fixture's read-only case taps no Sites button
        self.assertEqual([c["id"] for c in done["result"]["plan_cases"]], ["TERR-004"])

    def test_the_write_guard_and_the_hands_off_check_still_answer_first(self):
        case = old_case("TERR-021")
        case["steps"].insert(2, {"text": "Delete", "gates": [], "helper": "act.py {target} tap text:Delete --expect x"})
        put(self.root, "qa/cases/TERR-021.json", dict(case, steps=numbered(case["steps"])))
        put(self.root, "qa/cases/TERR-022.json", dict(old_case("TERR-022"), status="draft"))
        rid = self.intake()["id"]
        worker.claim(self.root, rid)
        self.assertTrue(self.refused(rid, ["TERR-021"]).startswith("TERR-021 step 3 writes through text:Delete"))
        self.assertTrue(self.refused(rid, ["TERR-022"]).startswith("TERR-022 is not hands-off"))

    def test_a_today_check_names_no_plan_cases_and_answers_as_before(self):
        put(self.root, "qa/cases/TERR-004.c1.json", dict(old_case("TERR-004"), created=LATER))
        done = self.answer(self.check()["id"])  # the Today check of 8.3: device rows only
        self.assertEqual(done["state"], "done")


class RunTests(test_intake.Base):
    """Never at Run: a plan the worker answered before the rule (its cases pinned) still starts."""

    def test_start_does_not_look_at_the_blocks(self):
        put(self.root, "qa/cases/TERR-020.json", old_case())
        with mock.patch.object(worker, "start_block_problem", return_value=None):  # a plan from before 2026-11-10
            done = self.planned(plan_cases=[self.plan_case("TERR-020")])
        with mock.patch.object(worker, "start_block_problem", side_effect=AssertionError("looked at Run")):
            status, start = self._req("POST", "/api/worker/start", {"check": done["id"], "targets": ["TA5"]})
        self.assertEqual((status, start.get("engine_cases")), (201, ["TERR-020"]), start)
        # the same case in a new plan is refused when the worker answers
        worker.cancel(self.root, start["id"])
        status, chk = self.intake_check()
        self.assertEqual(status, 201, chk)
        with self.assertRaises(worker.WorkerError) as e:
            self.answer_plan(chk["id"], ["TA5"], [self.plan_case("TERR-020")])
        self.assertEqual(e.exception.status, 400)
        self.assertTrue(e.exception.message.startswith("TERR-020 opens a page (step 1 taps testid:btn-sites) but does "
                                                       "not begin with the 5 steps of the start block"))
        self.assertEqual(worker.load_request(self.root, chk["id"])["state"], "claimed")


if __name__ == "__main__":
    unittest.main()
