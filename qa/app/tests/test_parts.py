"""case_schema's parts rule, "Steps from the task" (2026-11-05, tests/2026-11-04-results-redesign/SPEC.md "CASE RULE"):
a case written or rewritten from New test lists 1-8 parts first, every step but clean-up names its part, and only a
check part's steps read the page or assert. Checked under the settle rule's guard; every older case stays as it was.
The quotes are checked against the request at plan time (test_worker.py PlanTitleAndPartsTests).

python3 -I qa/app/tests/test_parts.py
"""
import copy
import glob
import json
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(HERE)
QA_DIR = os.path.dirname(APP_DIR)  # this copy's qa folder (qa, or a staging copy), never another one
sys.path.insert(0, APP_DIR)
import case_schema  # noqa: E402
import worker  # noqa: E402 (the start block and the clean-up block: the app's one source of them)

DAY = "2026-11-05T09:00:00.000Z"  # the rule's first day
GUIDANCE = "Map import accepts any resolution - try with both high and low resolution here"
# where parts_case's own steps sit in its "steps" list, after the 5 steps of the start block: the Maps tap, the read,
# and the first clean-up step (the step numbers are these plus 1)
MAPS, READ, CLEAN = 5, 6, 7


def block_steps(block, **extra):
    """worker.START_BLOCK or worker.CLEANUP_BLOCK as a case writes its steps (without n): the block's when_js and
    helper, a text of its own, no gate, plus extra (part=1 for the start block, always=True for the clean-up block)."""
    out = []
    for k, b in enumerate(block, 1):
        s = dict({"text": f"Block step {k}", "gates": [], "helper": b["helper"]}, **extra)
        if b["when_js"]:
            s["when_js"] = b["when_js"]
        out.append(s)
    return out


def parts_case(cid="TERR-901", created=DAY, ref="86abcnew1"):
    """A hands-off tablet case written from New test by the rule: the start block (since 2026-11-10 a case that taps
    Sites begins with it; its steps belong to part 1), part 1 opens Maps (steps[MAPS]), part 2 checks the page (the
    read and its assert, steps[READ]), then the clean-up block (from steps[CLEAN] on), which has no part."""
    steps = block_steps(worker.START_BLOCK, part=1) + [
        {"text": "Top bar: Maps", "gates": [], "part": 1,
         "helper": "act.py {target} tap testid:btn-maps --expect path:/maps --timeout 20"},
        {"text": "Read the page", "gates": [], "part": 2, "save_as": "maps",
         "read_js": "({maps: location.pathname === '/maps'})",
         "assert": {"kind": "scripted", "expr": "v['maps']['maps'] is True"}}] + \
        block_steps(worker.CLEANUP_BLOCK, always=True)
    for n, s in enumerate(steps, 1):
        s["n"] = n
    return {
        "kind": "case", "schema": 1, "id": cid, "created": created, "author": "claude", "supersedes": None,
        "title": "Maps page opens for the import", "area": cid.split("-")[0],
        "requirement": {"text": GUIDANCE, "sha256": case_schema.requirement_sha256(GUIDANCE),
                        "source": {"type": "user_chat", "ref": ref, "custom_id": None,
                                   "locator": "New test description, W-20261105-001", "requester": "Owner"}},
        "targets": "tablet", "routing": [], "expected_differences": [], "preconditions": [],
        "parts": [{"n": 1, "text": "Open Maps", "quote": "Map import", "check": False},
                  {"n": 2, "text": "Maps page: the import takes any resolution", "quote": ["accepts any resolution"],
                   "check": True}],
        "steps": steps,
        "fixtures": [], "covered_paths": [], "status": "active",
        "needs": {"link": "none", "usb_port": False, "account_lock": False, "flags": [], "drone_hw": None}}


def parts_problems(case):
    return [p for p in case_schema.validate_case(case) if p.startswith("parts") or ".part" in p or "part " in p]


class PartsRuleTests(unittest.TestCase):
    def test_it_checks_this_copy(self):
        self.assertEqual(os.path.dirname(os.path.abspath(case_schema.__file__)), APP_DIR)

    def test_a_case_written_by_the_rule_is_valid(self):
        self.assertEqual(case_schema.validate_case(parts_case()), [])
        c = parts_case()
        c["parts"] = [dict(n=k, text="t" * 100, quote=["a", "b"], check=k == 8) for k in range(1, 9)]
        c["steps"][MAPS]["part"], c["steps"][READ]["part"] = 3, 8  # the limits themselves pass
        self.assertEqual(case_schema.validate_case(c), [])

    def test_no_parts_or_more_than_eight(self):
        nine = [dict(n=k, text="t", quote="q", check=True) for k in range(1, 10)]
        for parts in (None, [], "Open Maps", {"n": 1}, nine):
            c = parts_case()
            if parts is None:
                del c["parts"]
            else:
                c["parts"] = parts
            got = parts_problems(c)
            self.assertEqual(len(got), 1, (parts, got))
            self.assertTrue(got[0].startswith("parts: a case from New test lists 1-8 parts first"), got)

    def test_a_part_without_text_quote_or_check(self):
        bad = [({"n": 3}, "parts[1] (part 2).n: must be 2"), ({"n": "2"}, "parts[1] (part 2).n: must be 2"),
               ({"text": ""}, "parts[1] (part 2).text: 1-100 characters"),
               ({"text": "t" * 101}, "parts[1] (part 2).text: 1-100 characters"),
               ({"text": None}, "parts[1] (part 2).text"),
               ({"quote": None}, "parts[1] (part 2).quote: the task's own words"),
               ({"quote": "  "}, "parts[1] (part 2).quote"),
               ({"quote": []}, "parts[1] (part 2).quote"), ({"quote": ["ok", ""]}, "parts[1] (part 2).quote"),
               ({"quote": 5}, "parts[1] (part 2).quote"), ({"check": None}, "parts[1] (part 2).check: true or false"),
               ({"check": "yes"}, "parts[1] (part 2).check: true or false")]
        for change, want in bad:
            c = parts_case()
            c["parts"][1].update(change)
            got = case_schema.validate_case(c)
            self.assertTrue(any(p.startswith(want) for p in got), (change, got))
        c = parts_case()
        c["parts"][1] = "the check"
        self.assertIn("parts[1] (part 2): must be {n, text, quote, check}", case_schema.validate_case(c))

    def test_every_step_but_clean_up_names_a_valid_part(self):
        for value in (None, 0, 3, "1", True, 1.0):
            c = parts_case()
            if value is None:
                del c["steps"][MAPS]["part"]
            else:
                c["steps"][MAPS]["part"] = value
            self.assertEqual(parts_problems(c), [f"steps[{MAPS}] (step {MAPS + 1}).part: the part it belongs to, 1-2; "
                                                 "only clean-up (always: true) has none"], value)
        c = parts_case()
        c["steps"][CLEAN]["part"] = 2
        self.assertEqual(parts_problems(c), [f"steps[{CLEAN}] (step {CLEAN + 1}).part: clean-up (always: true) belongs "
                                             "to no part"])

    def test_only_a_check_parts_steps_read_or_assert(self):
        want = (f"steps[{READ}] (step {READ + 1}): reads the page or asserts in part 1, which is not a check part; "
                "only a check part's steps read or assert, and only what its quote states")
        c = parts_case()
        c["steps"][READ]["part"] = 1  # the read and its assert under part 1, which checks nothing
        self.assertEqual(parts_problems(c), [want])
        c["steps"][READ].pop("read_js")
        c["steps"][READ]["helper"] = "act.py {target} wait --expect path:/maps"  # an assert alone
        self.assertEqual(parts_problems(c), [want])
        c["steps"][READ].pop("assert")
        c["steps"][READ]["helper"] = "act.py {target} read \"location.pathname\""  # an act.py read
        self.assertEqual(parts_problems(c), [want])
        c["steps"][READ]["helper"] = "act.py {target} wait --expect \"js:location.pathname === '/maps'\""  # a wait
        self.assertEqual(parts_problems(c), [])
        d = parts_case()
        d["steps"][CLEAN]["read_js"] = "document.title"
        self.assertEqual(parts_problems(d), [f"steps[{CLEAN}] (step {CLEAN + 1}): clean-up (always: true) never reads "
                                             "the page or asserts; only a check part does"])

    def test_the_date_guard(self):
        c = parts_case(created="2026-11-04T23:59:59.999Z")  # before the rule: no parts needed
        del c["parts"]
        self.assertEqual(case_schema.validate_case(c), [])
        c["created"] = DAY
        self.assertEqual(len(parts_problems(c)), 1)
        for status in ("draft", "retired"):  # never run: not checked
            self.assertEqual(parts_problems(dict(c, status=status)), [], status)

    def test_only_cases_written_from_new_test(self):
        for source in ({"type": "clickup_task", "ref": "0000fake007", "locator": "description bullet 1"},
                       {"type": "user_chat", "ref": "0000fake007", "locator": "chat with the person"},
                       {"type": "user_chat", "ref": "0000fake007"}, {"type": "spec_doc", "ref": "x",
                                                                    "locator": "New test description, W-1"}):
            c = parts_case()
            del c["parts"]
            c["requirement"]["source"] = source
            self.assertEqual(case_schema.validate_case(c), [], source)

    def test_an_edit_that_keeps_the_steps_is_not_checked(self):
        # TERR-007 (2026-11-02, from New test): a new version from the rule's day that keeps its steps runs as it is
        with open(os.path.join(QA_DIR, "cases", "TERR-007.json")) as f:
            c = json.load(f)
        c["created"], c["title"] = DAY, "Map import takes a coarse 30 m DSM"
        c["supersedes"] = {"id": "TERR-007", "file": "qa/cases/TERR-007.json", "file_sha256": "0" * 64, "why": "title"}
        self.assertEqual(parts_problems(c), [])
        c["steps"][0]["helper"] += " --timeout 30"  # different steps: a rewrite, which the rule covers
        got = parts_problems(c)
        self.assertTrue(got and got[0].startswith("parts: a case from New test lists 1-8 parts"), got)

    def test_every_case_of_this_copy_validates_as_before(self):
        # the rule adds no problem to any case written before it (qa/cases is checked the same way in the build report)
        files = sorted(glob.glob(os.path.join(QA_DIR, "cases", "*.json")))
        self.assertGreater(len(files), 10)
        for path in files:
            with open(path, encoding="utf-8") as f:
                case = json.load(f)
            with mock.patch.object(case_schema, "PARTS_FROM", "9999"):
                before = case_schema.validate_case(copy.deepcopy(case))
            self.assertEqual(case_schema.validate_case(case), before, os.path.basename(path))


if __name__ == "__main__":
    unittest.main()
