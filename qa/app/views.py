"""Result cards and quality numbers for the Results and History pages (2026-11-02).

result_cards(data) answers GET /api/results/cards: one card per test (task and case), newest first, with what was
tested, what happened and why, built from the run records, the case versions and the tasks that data.Data reads.
Since 2026-11-03 the answer also holds "running" (the tests the person started that have not ended, with each device's
live progress) and each card "report" (whether Report issue applies) and "comment" (where the results comment for
the ClickUp test stands: none, drafting, draft, posting, posted or failed); comment_draft(data, task) is the comment as
the card shows it and Post comment posts it, and card_entries(data, task, case) the runs of one test's card.
Since 2026-11-04 (tests/2026-11-04-results-redesign, SPEC.md and API.md): result_cards(data, task=, case=) answers one
test (?task=T-…|current, ?case=X): {test, cards, running}, the header, that test's cards with a step or part table per
device, and its live state; tests(data) answers GET /api/results/tests, History's 'Your tests' (Ongoing, Tested, Done);
test_title(ctx, task) names a test everywhere, and request_title(req) a New test request on GET /api/worker.
Since 2026-11-05 (tests/2026-11-05-wingmate-debug/FINDINGS.md): ?case=X answers X's newest run, a chat run too; each
ticket says whether File ticket can send it (fileable, fileable_why); Report issue, File ticket and the unreported
counts follow file_rule (the verified engine account, a filing device), the rule serve.run_fileable applies; and the
test carries plan_again.newer, open_write, a done note marked when a correction came after it, and null live states
for a device that does not run a case.
quality_stats(data, days) answers GET /api/stats: what the tests say about WingtraApp (QA) and how reliable the test
system itself is (reliability engineering), as exact counts from the records with day, week, device and area series.
fixture_allowed(data, rel) is the /files/ rule for a flight plan download: a .flightplan that a case under qa/cases
lists as a test file.

Only engine runs count (mode "live"; the October hand and back-test records stay in the old History) and the
engine's self-test cases (area HARNESS, SELFTEST-*) are left out everywhere. A correction <id>c1 stands in for the run
it corrects. Pure reads, like data.py: nothing here writes a file.

python3 -I qa/app/views.py [--root <workspace>] cards | stats [--days N]     prints the JSON for that workspace
"""
import argparse
import ast
import collections
import datetime
import hashlib
import json
import operator
import os
import re
import stat
import statistics
import sys
import urllib.parse
import zoneinfo

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)
sys.path.insert(0, os.path.join(os.path.dirname(APP_DIR), "runner"))

import clickup_post  # noqa: E402  (task_url only: views never posts)
import data as data_mod  # noqa: E402
import redact  # noqa: E402
import security  # noqa: E402
import worker  # noqa: E402  (reads only: whether Hover is off, its open work, the filing devices, plan ages)

DEFAULT_ROOT = os.path.dirname(os.path.dirname(APP_DIR))
ZURICH = zoneinfo.ZoneInfo("Europe/Zurich")
DAYS_MAX = 120

TAG_ORDER = ("TA5", "ET65", "W044", "TA3")
THREE = ("TA5", "ET65", "W044")  # the three devices a card names when one did not run
VERDICTS = ("PASS", "FAIL", "PARTIAL", "BLOCKED", "ERROR")
APP_VERDICTS = ("PASS", "FAIL", "PARTIAL")
DECIDED = ("PASS", "FAIL", "PARTIAL", "BLOCKED")
CAUSES = ("test case", "engine", "device not ready", "W044 browser", "other")

# card verdicts: the code, its label, and the order in which one device's result decides the card. The code
# not_decided (an ERROR run) reads "Test error" since 2026-11-04 (results redesign, API.md section 6)
LABELS = {"failed": "Failed", "failed_suspected": "Failed, suspected", "blocked": "Blocked",
          "not_decided": "Test error", "partly": "Partly passed", "passed": "Passed", "not_tested": "Not tested"}
SEVERITY = ("failed", "failed_suspected", "blocked", "not_decided", "partly", "passed", "not_tested")
PLAIN = {"PASS": "passed", "PARTIAL": "partly", "BLOCKED": "blocked", "ERROR": "not_decided",
         "NOT_TESTED": "not_tested"}
WORDS = {"failed": "failed", "failed_suspected": "failed (suspected)", "blocked": "blocked",
         "not_decided": "test error", "partly": "partly passed", "passed": "passed", "not_tested": "not tested"}
WORDS_MANY = dict(WORDS, not_decided="test errors")  # after a count above 1: "5 test errors", "2 passed"
# the fixed line under a test's result (API.md section 6)
MEANINGS = {"failed": "The app did not do what the test expects, and the failure is confirmed.",
            "failed_suspected": "The app did not do what the test expects. It is not confirmed yet.",
            "blocked": "The run was handed back to you before it ended.",
            "not_decided": "The test itself stopped before it could check the app. This says nothing about the app.",
            "partly": "Only part of the test could be checked, so this is not a full pass.",
            "passed": None, "not_tested": "No device ran this test."}
APP_RESULTS = ("passed", "failed", "failed_suspected", "partly")  # a device verdict that says something about the app

CARDS_NEWEST, CARDS_MAX = 12, 60
EXPLAIN_KEYS = ("tested", "happened", "why", "not_tested")
EXPLAIN_MAX = 500
COMMENT_MAX = 1000
TESTED_MAX = 300
PROOF_MAX = 12
POLYGONS_MAX, POINTS_MAX = 50, 2000
TIMES_MAX = 15
NOT_IN_TEST = "not in this test"
NO_RUN = "no run recorded"

W_RE = re.compile(r"^W-\d{8}-\d{3}\.json\Z", re.A)
OPEN_REQ = ("new", "claimed")  # worker.OPEN
# Report issue (2026-11-03, the person's request): active only when something went wrong in the card's runs
REPORT_VERDICTS = ("failed", "failed_suspected", "partly")
REPORT_WHY = {"blocked": "Nothing failed in the app: the run was handed back to you.",
              "not_decided": "Nothing failed in the app: the test itself stopped, which says nothing about the app.",
              "passed": "Nothing went wrong: the test passed on every device that ran it.",
              "not_tested": "No device ran this test."}
# the results comment the worker writes for a task (qa-worker section 3) and its posted marker next to it
COMMENT_FILE_RE = re.compile(r"^tests/\d{4}-\d{2}-\d{2}-[a-z0-9-]+/COMMENT_[A-Za-z0-9-]{1,40}_(T-\d{8}-\d{3})\.md\Z",
                             re.A)
COMMENT_READ_MAX = 64 * 1024
COMMENT_STATES = ("none", "drafting", "draft", "posting", "posted", "failed")
NOTE_MAX = 300
OFFLINE_NOTE_RE = re.compile(r"^Connect [A-Za-z0-9]{1,16} to the internet")  # worker.OFFLINE_NOTE
FILE_STALE_S = 30 * 60  # worker.STALE_NEW_S["file"]: a new file request this old holds an expired approval
CL_ID_RE = re.compile(r"^CL-\d{4,6}\Z")
NOT_TESTED_MARK = "Not tested in this run:"
TAG_ALT = "|".join(TAG_ORDER)
# "TA5, ET65, TA3: not requested." / "W044 (web): the case is tablet only." in a plan summary
NOT_TESTED_RE = re.compile(rf"((?:{TAG_ALT})(?: \([^)]*\))?(?:\s*(?:,|and)\s*(?:{TAG_ALT})(?: \([^)]*\))?)*)\s*:\s*"
                           r"([^.\n;]{1,200})")
# "TA5, ET65 and TA3 (web feature, only W044 requested)"
NOT_TESTED_PAREN_RE = re.compile(rf"((?:{TAG_ALT})(?:\s*(?:,|and)\s*(?:{TAG_ALT}))*)\s*\(([^()]{{1,200}})\)(?!\s*:)")
SINGLE_QUOTED = re.compile(r"(?<![\w'])'([^'\n]{1,160}?)'(?![\w])")
PARENS = re.compile(r"\s*\([^()]*\)")
CAPS_FAIL = re.compile(r"\bFAIL(s)?\b")
CAPS_PASS = re.compile(r"\bPASS(ES)?\b")
CAPS_ERROR = re.compile(r"\bERROR([Ss])?\b")
STEP_IN_SENTENCE = 140
DEVICE_NOT_READY = re.compile(r"\bnot ready\b|\bready\b|\bLOST\b|\bUSB\b|\blocked\b|\bnot connected\b", re.I)
BROWSER = re.compile(r"Chrome|identity|\btab\b|timed out|TimeoutError|browser", re.I)

# one test on Results and History's 'Your tests' (2026-11-04, tests/2026-11-04-results-redesign/API.md)
TITLE_HEADER, TITLE_LIST, TITLE_LINE = 160, 90, 48  # the Results header, lists, one line of a cut description
TEXT_NAME_RE = re.compile(r"^Run from (?:New test|Today): (.+?)\. Cases ")  # the ClickUp name in a task's text
LIST_MARK_RE = re.compile(r"^\s*(?:[-*•·–—]\s*|\d+[.)]\s+)")
# Hover's 'why' without its leading verdict clause ("Test error, says nothing about the app: the site lookup ..." ->
# "the site lookup ...")
WHY_LEAD_RE = re.compile(r"^\s*(?:test error|not decided|failed(?:, suspected)?|failed \(suspected\)|blocked|"
                         r"partly passed|passed|not tested)\b[^:]{0,80}:\s*", re.I)
PART_TEXT_MAX = 100  # case-rule data: a part's text
RESULT_STATES = ("results_ready", "greenlit", "drafted")  # a task with its results in (Add result: writes.request_comment)
CLOSE_STATES = ("queued",) + RESULT_STATES  # the task states tasks.TRANSITIONS lets the person close (Mark done)
FILE_STATES = ("draft", "waiting_online", "failed")  # a drafted ticket File ticket can send
# "PLAN-001 c2: " at the start of an older plan's case line: case ids and c0/c1 labels stay in Run records
CASE_LEAD_RE = re.compile(r"^\s*[A-Z][A-Z0-9]{1,11}-\d{3,6}(?: c\d+)?\s*:\s*")
WORK_TYPES = worker.WORK_TYPES  # Hover's work on a test's results: a comment, a ticket draft, filing it
PRESS = {"file_ticket": "File ticket", "post_comment": "Post comment", "report_issue": "Report issue",
         "plan_again": "Plan again", "add_result": "Add result"}
MARK_WHY = {"ongoing": "The test is still running.",
            "chat": "A run from a chat is not a test, so it cannot be marked done.",
            "done": "It is in Done already.", "posting": "The comment is being posted.",
            "work": worker.WORK_OPEN_WHY,
            "state": "Only a test with results can be marked done; this one is {state}."}
# tickets are filed on these devices only, from runs on the verified engine account (file_rule, serve.run_fileable)
FILE_TARGETS = worker.FILE_TARGETS
# File ticket cannot send a draft (2026-11-05, wingmate-debug FINDINGS #3 and #8): the ticket's fileable_why
NOT_FILEABLE = {"corrected": "Not filed: its run was corrected to a test error, so there is nothing to file.",
                "not_failed": "Not filed: its run did not fail in its newest record, so there is nothing to file.",
                "no_run": "Not filed: its run is not among the run records."}
# a done note Hover wrote before a correction of one of the test's runs (2026-11-05, FINDINGS #23)
NOTE_BEFORE = " (written before the correction of {date})"
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
SHOWN, ROWS_MAX = 5, 100  # History's 'Your tests': rows shown before 'n more', rows sent at most per group


class NotFound(Exception):
    """A one-test answer for a test or case that does not exist: serve.py answers 404 with this message."""

DEFINITIONS = {
    "pass_rate": "Of the runs that ended with a result about the app (passed, failed or partly passed), the share "
                 "that passed; runs where the test itself stopped are left out.",
    "decided_rate": "Of all engine runs, the share that ended with a result about the app (passed, failed, partly "
                    "passed or blocked) instead of the test itself stopping; the goal is 90%.",
    "false_fails": "Failures that were corrected later because the test case was wrong, not the app.",
    "hands_off": "Runs started from a test in this app (New test, Start), out of all engine runs.",
    "times": "Middle value over the app's tests: prepare is from asking for a plan to the plan being ready, run is "
             "from Start to results, result is from asking for a plan to results.",
    "coverage": "Runs with a result about the app on each device, and the cases that passed at least once out of "
                "the active cases.",
    "fails": "A failure is confirmed when this version of the test has passed once on the same kind of device and "
             "the failure repeated (twice on the same build, or on two devices); otherwise it is suspected.",
}
BACKTEST_NOTE = "October hand and back-test records: shown in the old History, not counted here"


# ---- small helpers -------------------------------------------------------------------------------------------------

def _pct(part, whole):
    return round(100.0 * part / whole, 1) if whole else None


def _secs(a, b):
    """Whole seconds from ISO time a to ISO time b, or None."""
    ta, tb = data_mod.parse_time(a), data_mod.parse_time(b)
    if not ta or not tb or tb < ta:
        return None
    return int(round((tb - ta).total_seconds()))


def _day(iso):
    """The Zurich calendar day of an ISO time, or None."""
    t = data_mod.parse_time(iso)
    return t.astimezone(ZURICH).date() if t else None


def _as_date(x):
    if isinstance(x, datetime.datetime):
        return x.astimezone(ZURICH).date() if x.tzinfo else x.date()
    if isinstance(x, datetime.date):
        return x
    if isinstance(x, str):
        try:
            return datetime.date.fromisoformat(x[:10])
        except ValueError:
            return None
    return None


def _cut(text, limit, clause=False):
    """text redacted, then shortened to limit characters at a word boundary (clause: at the last "; " when there is
    one in the second half), with an ellipsis when cut. Redacted first: a cut through a secret or an address leaves a
    piece that the redaction of the whole answer no longer recognises."""
    text = " ".join(redact.redact(str(text)).split())
    if len(text) <= limit:
        return text
    cut = text[:limit - 1]
    semi = cut.rfind("; ") if clause else -1
    if semi > limit // 2:
        return cut[:semi] + "; …"
    sp = cut.rfind(" ")
    return (cut[:sp] if sp > limit // 2 else cut).rstrip(" ,;:") + "…"


def _quotes(text):
    """Text from a record in the tester's words: on-screen messages in straight double quotes ('Split areas.' ->
    "Split areas."; apostrophes stay), and no PASS, FAIL or ERROR in capitals (ERROR reads "test error" since
    2026-11-04). Redacted first, since a reworded secret no longer matches its value."""
    if not isinstance(text, str):
        return text
    text = SINGLE_QUOTED.sub(r'"\1"', redact.redact(text))
    text = CAPS_ERROR.sub(lambda m: "test error" + ("s" if m.group(1) else ""), text)
    return CAPS_PASS.sub(lambda m: "pass" + (m.group(1) or "").lower(),
                         CAPS_FAIL.sub(lambda m: "failure" + ("s" if m.group(1) else ""), text))


def _count_word(n, code):
    """'5 test errors', '2 passed', '1 failed (suspected)': a count and its verdict word."""
    return f"{n} {(WORDS_MANY if n > 1 else WORDS)[code]}"


def _join(items):
    items = list(items)
    return data_mod.join_and(items) if items else ""


def _tag_key(tag):
    return (TAG_ORDER.index(tag) if tag in TAG_ORDER else len(TAG_ORDER), tag or "")


def _attempt(rec):
    att = [a for a in rec.get("attempts") or [] if isinstance(a, dict)]
    return att[-1] if att else {}


def _vobj(rec):
    """A record's verdict object ({} when it is missing or a plain string)."""
    return rec.get("verdict") if isinstance(rec.get("verdict"), dict) else {}


def _handback(rec):
    hb = _vobj(rec).get("handback")
    return hb if isinstance(hb, dict) else {}


def _counter_row(counts, **head):
    return dict(head, **{v: counts.get(v, 0) for v in VERDICTS})


# ---- one context per answer: the records read once ---------------------------------------------------------------

class _Ctx:
    def __init__(self, data):
        self.data = data
        self.idx = data._runs()
        self.cases = data._cases()
        self.tasks = data._tasks()
        self.requests = {os.path.basename(p)[:-5]: obj for p, _, obj in data._state_dir("worker", W_RE)}
        self.task_of = self._task_of_runs()
        self.entries = self._entries()
        self.confirmed = self._fail_status()
        self.by_task = collections.defaultdict(list)  # {task id: its runs, newest first}
        for e in self.entries:
            if e["task"]:
                self.by_task[e["task"]].append(e)
        self.live = None  # (progress files, shot paths), read when an answer needs them
        self.off = None  # whether Hover is off, asked when an answer needs it
        self.by_run = None  # the ticket drafts by run (_ticket_drafts), read when an answer needs them
        self.engine_account = data._engine().get("account")  # tickets are filed from its runs only (file_rule)

    # case versions

    def version(self, cid, sha):
        """The case version a run used (by its case file's sha256), or None."""
        c = self.cases.get(cid)
        return next((v for v in c["versions"] if v["sha256"] == sha), None) if c and sha else None

    def case_rec(self, cid, sha=None):
        """That version's case record, else the newest version's ({} when the case is unknown)."""
        v = self.version(cid, sha)
        c = self.cases.get(cid)
        return (v or (c["latest"] if c else {})).get("rec") or {}

    def selftest(self, cid, rec=None):
        if not isinstance(cid, str) or cid.startswith("SELFTEST"):
            return True
        c = self.cases.get(cid)
        if c and (c["harness"] or c["latest"]["rec"].get("area") == "HARNESS"):
            return True
        return bool(rec and rec.get("harness_case"))

    # runs

    def _root(self, rid):
        """The run a record corrects, followed back to the first record (a plain run is its own root)."""
        runs, seen = self.idx["runs"], {rid}
        while rid in runs:
            old = data_mod.sup_id(runs[rid]["rec"].get("supersedes"))
            if not old or old not in runs or old in seen:
                break
            rid = old
            seen.add(rid)
        return rid

    def _task_of_runs(self):
        """{run id: task id}: the newest task that lists the run (or a correction of it)."""
        out = {}
        for tid in sorted(self.tasks, key=lambda t: (self.tasks[t].get("created") or "", t)):
            for r in self.data._task_runs(self.tasks[tid]):
                out[self._root(r)] = tid
        return out

    def _entries(self):
        """One entry per run (corrections fold into the run they correct), back-test and self-test runs left out."""
        runs, succ = self.idx["runs"], self.idx["run_succ"]
        out = []
        for rid, item in runs.items():
            rec = item["rec"]
            if self._root(rid) != rid or rec.get("mode") == "backtest" or self.selftest(rec.get("case"), rec):
                continue
            eff = data_mod.newest(rid, succ)
            eff = eff if eff in runs else rid
            erec, base = runs[eff]["rec"], runs[eff]["base"]
            started = item["base"]["started"]
            ev = _vobj(erec)
            out.append({
                "id": rid, "eff": eff, "rec": rec, "erec": erec, "case": rec.get("case"),
                "tag": item["base"]["target"], "kind": item["base"]["target_class"], "mode": rec.get("mode"),
                "campaign": item["campaign"], "verdict": base["verdict"] or "ERROR",
                "raw": item["base"]["verdict"], "corrected": eff != rid, "sha": rec.get("case_file_sha256"),
                "build": base["build"], "started": started, "date": _day(started),
                "decided": ev.get("decided") or erec.get("created") or started, "task": self.task_of.get(rid),
            })
        out.sort(key=lambda e: (e["started"] or "", e["id"]), reverse=True)
        return out

    def _fail_status(self):
        """{run id: True when confirmed} for every run whose result is a failure (QA practice in CLAUDE.md): the case
        version passed once on the same kind of device, and the failure repeated (2+ on one build, or 2 devices)."""
        passed = {(e["case"], e["sha"], e["kind"]) for e in self.entries if e["verdict"] == "PASS" and e["sha"]}
        groups = collections.defaultdict(list)
        for e in self.entries:
            if e["verdict"] == "FAIL":
                groups[(e["case"], e["sha"])].append(e)
        out = {}
        for g in groups.values():
            tags = {e["tag"] for e in g}
            for e in g:
                b = _build_key(e["build"])
                same_build = b is not None and sum(1 for x in g if _build_key(x["build"]) == b) >= 2
                out[e["id"]] = bool(e["sha"]) and (e["case"], e["sha"], e["kind"]) in passed and (
                    same_build or len(tags) >= 2)
        return out

    def not_confirmed_why(self, e):
        validated = any(x["verdict"] == "PASS" and x["case"] == e["case"] and x["sha"] == e["sha"] and e["sha"]
                        and x["kind"] == e["kind"] for x in self.entries)
        if not validated:
            return f"this version of the test has not passed on a {e['kind']} device yet"
        return "the failure has not repeated yet"

    def verdict(self, e):
        if e["verdict"] == "FAIL":
            return "failed" if self.confirmed.get(e["id"]) else "failed_suspected"
        return PLAIN.get(e["verdict"], "not_decided")

    # files under tests/

    def tests_file(self, rel):
        return tests_path(self.data, rel)


def tests_path(d, rel):
    """The path of a tests/... file the cards read inline (explain, comment, flight plan), else None. The /files/
    rule applies (security.resolve_read: no hard link, nothing denied), and more: no link anywhere in the path and
    at most 2 MiB, like a download."""
    rel = d._clean_rel(rel)
    if not rel or not rel.startswith("tests/"):
        return None
    try:
        full = security.resolve_read(d.root, urllib.parse.quote(rel, safe="/"), None, True)
        if full != os.path.join(os.path.realpath(d.root), *rel.split("/")) or \
                os.path.getsize(full) > security.TEXT_CAP:
            return None
    except (security.ReadError, OSError):
        return None
    return full


def _build_key(b):
    return b.get("hash7") or b.get("about_label") or None


# ---- why a run was not decided ------------------------------------------------------------------------------------

def _harness(e):
    h = _vobj(e["erec"]).get("harness")
    h = h if isinstance(h, dict) else {}
    return str(h.get("component") or ""), str(h.get("detail") or "")


def cause(e):
    """Why a run that is not decided stopped, as one of CAUSES."""
    comp, detail = _harness(e)
    if comp == "case" or e["corrected"]:
        return "test case"
    if comp == "act.py" and e["tag"] == data_mod.WEB_TAG and BROWSER.search(detail):
        return "W044 browser"
    if DEVICE_NOT_READY.search(detail):
        return "device not ready"
    if comp in ("runner", "act.py", "run.py"):
        return "engine"
    return "other"


def plain_reason(e):
    """The reason a run stopped, in the tester's words."""
    c = cause(e)
    _, detail = _harness(e)
    low = detail.lower()
    if c == "test case":
        return "the test case was wrong, not the app"
    if c == "W044 browser":
        return "the browser on W044 did not answer in time" if "timed out" in low or "timeout" in low else \
            "the browser on W044 was not ready"
    if c == "device not ready":
        return "the device was not ready"
    if "expected change did not happen" in low:
        return "the expected change did not show in time"
    if low.startswith("covered:"):
        return "a button was covered by something else on the screen"
    if low.startswith("not found:"):
        return "an expected item was not on the screen"
    m = re.search(r"system text .*: (\d+) matches", detail)
    if m:
        return "an expected text was not on the screen" if m.group(1) == "0" else \
            "an expected text was on the screen more than once"
    if "timed out" in low or "timeout" in low:
        return "the device did not answer in time"
    return "an error in the test engine" if c == "engine" else "an unknown error"


# ---- steps ----------------------------------------------------------------------------------------------------------

def _missed(s):
    a = s.get("assert") if isinstance(s.get("assert"), dict) else {}
    rb = s.get("readback") if isinstance(s.get("readback"), dict) else {}
    return a.get("result") is False or rb.get("ok") is False


def steps_of(ctx, e):
    """[{n, text, outcome, cleanup, part}] of a run's last attempt, laid over the steps its case version has for the
    device. part: the case step's "part" (case-rule data, 2026-11-04), else None; a clean-up step has none."""
    rec = e["erec"] if _attempt(e["erec"]).get("steps") else e["rec"]
    rsteps = {s["n"]: s for s in _attempt(rec).get("steps") or [] if isinstance(s, dict) and data_mod.is_int(s.get("n"))}
    crec = ctx.case_rec(e["case"], e["sha"])
    csteps = {s["n"]: s for s in crec.get("steps") or []
              if isinstance(s, dict) and data_mod.is_int(s.get("n")) and s.get("on", "both") in ("both", e["kind"])}
    v = e["verdict"]
    hb_step = _handback(e["erec"]).get("step")
    out = []
    for n in sorted(set(rsteps) | set(csteps)):
        s, cs = rsteps.get(n), csteps.get(n) or {}
        if s is None or s.get("skipped"):
            outcome = "stopped" if v == "BLOCKED" and n == hb_step else "not_run"
        elif s.get("error"):
            outcome = "stopped"
        elif _missed(s):
            outcome = "not_as_expected" if v in ("FAIL", "PARTIAL", "PASS") else "stopped"
        else:
            outcome = "as_expected"
        text = (s or {}).get("text") or cs.get("text") or ""
        part = cs.get("part") if data_mod.is_int(cs.get("part")) and not cs.get("always") else None
        out.append({"n": n, "text": _quotes(text), "outcome": outcome, "cleanup": bool(cs.get("always")),
                    "part": part, "_s": s})
    return out


def failed_step(steps):
    s = next((s for s in steps if s["outcome"] in ("not_as_expected", "stopped")), None)
    return {"n": s["n"], "text": s["text"]} if s else None


# ---- what a check compared ------------------------------------------------------------------------------------------

class _Unknown(Exception):
    pass


CMP = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt,
       ast.GtE: operator.ge, ast.Is: operator.is_, ast.IsNot: operator.is_not,
       ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b}


def _ev(node, env):
    """A small, safe evaluator for the check expressions cases use (values, keys, len, comparisons, and/or/not)."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in env:
            return env[node.id]
        raise _Unknown
    if isinstance(node, ast.Subscript):
        base, key = _ev(node.value, env), _ev(node.slice, env)
        try:
            return base[key]
        except (KeyError, IndexError, TypeError):
            raise _Unknown from None
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "len" \
            and len(node.args) == 1 and not node.keywords:
        try:
            return len(_ev(node.args[0], env))
        except TypeError:
            raise _Unknown from None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        try:
            return _ev(node.left, env) + _ev(node.right, env)
        except TypeError:
            raise _Unknown from None
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not _ev(node.operand, env)
    if isinstance(node, ast.BoolOp):
        vals = [_ev(v, env) for v in node.values]
        return all(vals) if isinstance(node.op, ast.And) else any(vals)
    if isinstance(node, ast.Compare):
        left = _ev(node.left, env)
        for op, comp in zip(node.ops, node.comparators):
            right = _ev(comp, env)
            fn = CMP.get(type(op))
            if fn is None:
                raise _Unknown
            try:
                if not fn(left, right):
                    return False
            except TypeError:
                raise _Unknown from None
            left = right
        return True
    raise _Unknown


def _name(node):
    """A plain name for a value the check reads: v['after']['split_toast'] -> after split toast."""
    keys = []
    while isinstance(node, ast.Subscript):
        if not (isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, (str, int))):
            return None
        keys.append(str(node.slice.value).replace("_", " "))
        node = node.value
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "len" and node.args:
        inner = _name(node.args[0])
        return f"the number of {inner}" if inner else None
    if isinstance(node, ast.Name) and keys:
        return " ".join(reversed(keys))
    if isinstance(node, ast.Name) and node.id in ("run", "target"):
        return "the run name" if node.id == "run" else "the device"
    return None


def _show(x):
    if isinstance(x, bool):
        return "true" if x else "false"
    if x is None:
        return "nothing"
    if isinstance(x, str):
        return '"' + _cut(x.replace("\n", " / "), 60) + '"'
    if isinstance(x, (int, float)):
        return str(x)
    if isinstance(x, (list, tuple)):
        return f"a list of {len(x)}"
    return "a set of values"


OP_WORDS = {ast.Eq: "should be", ast.NotEq: "should not be", ast.Gt: "should be more than", ast.GtE: "should be at least",
            ast.Lt: "should be less than", ast.LtE: "should be at most", ast.Is: "should be", ast.IsNot: "should not be"}


def _clause_text(node, env):
    """One failed part of a check in plain words, or None when it cannot be said simply."""
    if not (isinstance(node, ast.Compare) and len(node.ops) == 1):
        return None
    op, left, right = node.ops[0], node.left, node.comparators[0]
    try:
        lv, rv = _ev(left, env), _ev(right, env)
    except _Unknown:
        return None
    if isinstance(op, (ast.In, ast.NotIn)):
        where = _name(right)
        if not where or not isinstance(lv, str):
            return None
        return f'{where} should {"" if isinstance(op, ast.In) else "not "}contain {_show(lv)}'
    what = _name(left)
    if not what or type(op) not in OP_WORDS:
        return None
    other = _name(right)
    target = f"{other} ({_show(rv)})" if other else _show(rv)
    return f"{what} {OP_WORDS[type(op)]} {target}, was {_show(lv)}"


def check_found(s, rec):
    """'after split toast should be true, was false; ...' for a step whose check did not hold, or None."""
    a = s.get("assert") if isinstance(s, dict) and isinstance(s.get("assert"), dict) else {}
    expr = a.get("expr")
    if a.get("result") is not False or not isinstance(expr, str) or len(expr) > 4000:
        return None
    try:
        tree = ast.parse(expr, mode="eval").body
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return None
    parts = list(tree.values) if isinstance(tree, ast.BoolOp) and isinstance(tree.op, ast.And) else [tree]
    build = rec.get("build") if isinstance(rec.get("build"), dict) else {}
    env = {"v": rec.get("values") if isinstance(rec.get("values"), dict) else {}, "run": rec.get("id"),
           "target": None, "build": {k: data_mod.val(x) for k, x in build.items()}}
    found = []
    try:
        for p in parts:
            try:
                if _ev(p, env):
                    continue
            except _Unknown:
                continue
            t = _clause_text(p, env)
            if t:
                found.append(t)
    except RecursionError:  # a check nested too deep to evaluate here: say nothing rather than break the page
        return None
    return "; ".join(found[:3]) or None


# ---- one card -------------------------------------------------------------------------------------------------------

def _tested(crec):
    """What the test checks: the case's expected result, shortened (asides left out), else its title."""
    exp = crec.get("expected")
    if isinstance(exp, str) and exp.strip():
        return _cut(_quotes(PARENS.sub("", redact.redact(exp))), TESTED_MAX, clause=True)
    return _quotes(crec.get("title") or "")


def _short_step(fs):
    """'step 9 (Wait for the result)': the step's text without its own asides, for use inside a sentence."""
    text = PARENS.sub("", fs["text"]).strip() or fs["text"]
    return f"step {fs['n']} ({_cut(text, STEP_IN_SENTENCE)})"


def _device_text(d):
    """What happened on one device that did not pass, without the device name (devices with the same text share one
    sentence)."""
    v, e, fs = d["v"], d["e"], d["failed_step"]
    if v in ("failed", "failed_suspected"):
        if not fs:
            return "the test failed."
        out = f"step {fs['n']} did not go as expected: {_cut(fs['text'], STEP_IN_SENTENCE)}."
        s = next((x["_s"] for x in d["steps"] if x["n"] == fs["n"]), None)
        found = check_found(s, e["erec"]) if s else None
        if found:
            out += f" The check found: {found}."
        elif s and (s.get("readback") or {}).get("ok") is False:
            out += " The screen did not show the expected change."
        return out
    if v == "not_decided":
        if e["corrected"]:
            first = WORDS.get(_record_verdict(e["raw"]), "decided")
            where = f" at {_short_step(fs)}" if fs else ""
            return f"it was first recorded as {first}{where}, then corrected: {plain_reason(e)}."
        where = f"at {_short_step(fs)}" if fs else "before the first step"
        return f"the test itself stopped {where}: {plain_reason(e)}."
    if v == "blocked":
        hb = _handback(e["erec"])
        n = hb.get("step")
        reason = _cut(str(hb.get("reason") or "it needs you"), 200)
        return "the run was handed back to you" + (f" at step {n}" if n is not None else "") + f": {reason}."
    if v == "partly":
        return "only part of the test could be checked."
    return None


def _record_verdict(raw):
    """The plain verdict code of a record's own verdict (a failure before its correction is just "failed")."""
    return "failed" if raw == "FAIL" else PLAIN.get(raw, "not_decided")


def _auto(ctx, verdict, devs, lead, crec, earlier):
    tested = _tested(crec)
    passed = [d["tag"] for d in devs if d["v"] == "passed"]
    untested = [d["tag"] for d in devs if d["v"] == "not_tested"]
    groups = collections.OrderedDict()
    for d in sorted(devs, key=lambda d: (SEVERITY.index(d["v"]), _tag_key(d["tag"]))):
        if d["v"] not in ("passed", "not_tested"):
            text = _device_text(d)
            if text:
                groups.setdefault(text, []).append(d["tag"])
    sentences = [f"On {_join(tags)}, {text}" for text, tags in groups.items()]
    if passed:
        steps = lead["steps"] if lead and lead["v"] == "passed" else \
            next(d for d in devs if d["v"] == "passed")["steps"]
        ran = sum(1 for s in steps if s["outcome"] == "as_expected")
        skipped = sum(1 for s in steps if s["outcome"] == "not_run")
        extra = f"; {skipped} more {'was' if skipped == 1 else 'were'} not needed this time" if skipped else ""
        if verdict == "passed" and not steps:
            sentences.insert(0, f"Recorded as passed on {_join(passed)}; the record has no step details.")
        elif verdict == "passed":
            sentences.insert(0, f"Every step that ran went as expected on {_join(passed)} ({ran} steps{extra}).")
        else:
            sentences.append(f"It passed on {_join(passed)}.")
    if untested:
        sentences.append(f"{_join(untested)} {'was' if len(untested) == 1 else 'were'} not tested.")
    if not devs:
        sentences.append("No device ran this test.")
    if earlier:
        counts = collections.Counter(earlier)
        parts = [_count_word(counts[v], v) for v in SEVERITY if counts.get(v)]
        sentences.append(f"Earlier runs of this test: {_join(parts)}.")
    happened = " ".join(sentences)

    fs = lead["failed_step"] if lead else None
    if verdict == "passed":
        why = "Every check passed."
    elif verdict in ("failed", "failed_suspected"):
        why = f"Step {fs['n']} did not happen as expected: {_cut(fs['text'], STEP_IN_SENTENCE)}." if fs else \
            "A check did not hold."
        if verdict == "failed_suspected":
            why += f" Not confirmed yet: {ctx.not_confirmed_why(lead['e'])}."
    elif verdict == "not_decided":
        where = f"at step {fs['n']}" if fs else "before the first step"
        why = f"The test itself stopped {where} ({plain_reason(lead['e'])}). This says nothing about the app."
    elif verdict == "blocked":
        n = _handback(lead["e"]["erec"]).get("step")
        why = "The run waits for you" + (f" at step {n}." if n is not None else ".")
    elif verdict == "partly":
        why = "Not every step could be checked, so this is not a full pass."
    else:
        why = "No device ran this test."
    return {"tested": tested, "happened": happened, "why": why}


def _clickup(ctx, task):
    if not task:
        return None
    ck = task.get("clickup") if isinstance(task.get("clickup"), dict) else {}
    if not ck:
        return None
    origin = task.get("origin") if isinstance(task.get("origin"), dict) else {}
    req_ck = {}
    for rid in (origin.get("start"), origin.get("check")):
        r = ctx.requests.get(rid) if isinstance(rid, str) else None
        if isinstance(r, dict) and isinstance(r.get("clickup"), dict) and r["clickup"].get("task_id") == ck.get("task_id"):
            req_ck = r["clickup"]
            break
    return {"task_id": ck.get("task_id"), "custom_id": ck.get("cl") or req_ck.get("custom_id"), "url": ck.get("url"),
            "title": req_ck.get("title")}


def _plan_text(ctx, task):
    if not task:
        return ""
    texts = [task.get("plan_summary")]
    start = ctx.requests.get((task.get("origin") or {}).get("start")) if isinstance(task.get("origin"), dict) else None
    if isinstance(start, dict):
        texts.append(start.get("plan_summary"))
    return "\n".join(t for t in texts if isinstance(t, str))


def _not_tested(ctx, task, ran):
    """[{tag, why}] for each of the three devices (and any other the task asked for) that has no run in the card."""
    text = redact.redact(_plan_text(ctx, task))  # before the reasons are cut out of it
    reasons = {}
    at = text.find(NOT_TESTED_MARK)
    if at >= 0:
        rest = text[at + len(NOT_TESTED_MARK):]
        for rx in (NOT_TESTED_RE, NOT_TESTED_PAREN_RE):
            for m in rx.finditer(rest):
                for tag in re.findall(TAG_ALT, m.group(1)):
                    reasons.setdefault(tag, _quotes(m.group(2).strip()))
    targets = set()
    if task:
        targets = {t for t in (task.get("targets") or []) + (task.get("plan_targets") or []) if isinstance(t, str)}
    tags = list(THREE) + sorted((t for t in targets if t not in THREE), key=_tag_key)
    return [{"tag": t, "why": reasons.get(t) or (NO_RUN if t in targets else NOT_IN_TEST)} for t in tags if t not in ran]


def _explain(ctx, task, case):
    if not task:
        return None
    rels = (task.get("outputs") or {}).get("explain") if isinstance(task.get("outputs"), dict) else None
    for rel in reversed([r for r in rels or [] if isinstance(r, str)]):
        full = ctx.tests_file(rel)
        obj = ctx.data._json(full) if full else None
        cases = obj.get("cases") if isinstance(obj, dict) else None
        entry = cases.get(case) if isinstance(cases, dict) else None
        if isinstance(entry, dict):
            return {k: (redact.redact(entry[k])[:EXPLAIN_MAX] if isinstance(entry.get(k), str) else None)
                    for k in EXPLAIN_KEYS}
    return None


def _outputs(ctx, task):
    out = task.get("outputs") if task and isinstance(task.get("outputs"), dict) else {}
    summary = out.get("summary") if isinstance(out.get("summary"), str) else None
    comment_file = comment_rel(task) if task else None  # the file the card's comment field reads (review 2026-11-03)
    text = None
    if comment_file:
        full = ctx.tests_file(comment_file)
        raw = ctx.data._text(full) if full else None
        if isinstance(raw, str) and raw.strip():
            text = clickup_post.TOKEN_IN_TEXT.sub(redact.MASK, redact.redact(raw))[:COMMENT_MAX]
    return {"summary": summary, "comment_text": text, "comment_file": comment_file}


def _proof(ctx, d):
    out = []
    e = d["e"]
    item = ctx.idx["runs"].get(e["eff"]) or ctx.idx["runs"][e["id"]]
    shots = item["base"]["_shots"] or ctx.idx["runs"][e["id"]]["base"]["_shots"]
    for p in shots:
        url = ctx.data._file_url(p, ctx.idx["shots"])
        if not url:
            continue
        name = os.path.basename(p)
        stem = os.path.splitext(name)[0]
        # "_after_<x>" is a case's own shot of the result too (TERR-005 c5 step 24: _after_import)
        kind = "result" if "_result" in stem or "_after_" in stem else "end" if "_end" in stem else "other"
        out.append({"url": url, "name": name, "kind": kind, "run": e["eff"]})
    return out


def _polygons(obj):
    fp = obj.get("flightPlan") if isinstance(obj, dict) else None
    items = fp.get("items") if isinstance(fp, dict) else None
    out = []
    for it in items if isinstance(items, list) else []:
        if not (isinstance(it, dict) and it.get("complexItemType") == "area" and isinstance(it.get("polygon"), list)):
            continue
        pts = []
        for p in it["polygon"][:POINTS_MAX]:
            if (isinstance(p, list) and len(p) >= 2 and all(isinstance(x, (int, float)) and not isinstance(x, bool)
                                                            and abs(x) <= 360 for x in p[:2])):
                pts.append([p[0], p[1]])
        if pts:
            out.append(pts)
        if len(out) >= POLYGONS_MAX:
            break
    return out


def fixture_paths(data):
    """The tests/... paths of the flight plans that some case version under qa/cases lists as a test file."""
    out = set()
    for c in data._cases().values():
        for v in c["versions"]:
            if not v["file"].startswith("qa/cases/"):
                continue
            for f in v["rec"].get("fixtures") or []:
                p = f.get("path") if isinstance(f, dict) else None
                if isinstance(p, str) and p.startswith("tests/") and p.lower().endswith(".flightplan") \
                        and data._clean_rel(p):
                    out.add(p)
    return out


def fixture_allowed(data, rel):
    """The /files/ rule for a .flightplan download: exactly a path some case under qa/cases lists in fixtures."""
    return isinstance(rel, str) and rel in fixture_paths(data)


def download_url(data, rel):
    """"/files/<rel>" when the /files/ route would serve this flight plan, else None."""
    if not data._clean_rel(rel):
        return None
    quoted = urllib.parse.quote(rel, safe="/")
    try:
        security.resolve_read(data.root, quoted, None, lambda r: fixture_allowed(data, r))
    except security.ReadError:
        return None
    return "/files/" + quoted


def _flight_plans(ctx, crec):
    out = []
    for f in crec.get("fixtures") or []:
        path = f.get("path") if isinstance(f, dict) else None
        if not (isinstance(path, str) and path.lower().endswith(".flightplan")):
            continue
        full = ctx.tests_file(path)
        obj = ctx.data._json(full) if full else None
        out.append({"name": f.get("name") if isinstance(f.get("name"), str) else os.path.basename(path),
                    "url": download_url(ctx.data, path), "polygons": _polygons(obj)})
    return out


def _build_label(b):
    return " ".join(str(x) for x in (b.get("semver"), b.get("env")) if x) or b.get("about_label") or None


def _native(runs):
    """The versionCode of the newest tablet run among runs (newest first), as "144"; None when no tablet ran."""
    tablet = next((e for e in runs if e["kind"] == "tablet"), None)
    native = data_mod.val(tablet["build"].get("version_code")) if tablet else None
    return str(native) if native is not None and str(native).isdigit() else None


def _card(ctx, key, runs):
    runs = sorted(runs, key=lambda e: (e["started"] or "", e["id"]), reverse=True)
    newest = runs[0]
    task = ctx.tasks.get(newest["task"]) if newest["task"] else None
    by_tag = {}
    for e in runs:
        by_tag.setdefault(e["tag"], e)
    devs = []
    for tag in sorted(by_tag, key=_tag_key):
        e = by_tag[tag]
        steps = steps_of(ctx, e)
        v = ctx.verdict(e)
        devs.append({"tag": tag, "v": v, "e": e, "steps": steps,
                     "failed_step": failed_step(steps) if v not in ("passed", "not_tested") else None})
    present = [d for d in devs if d["v"] != "not_tested"] or devs
    verdict = min((d["v"] for d in present), key=SEVERITY.index) if present else "not_tested"
    lead = next((d for d in devs if d["v"] == verdict), None)
    earlier = [ctx.verdict(e) for e in runs if by_tag.get(e["tag"]) is not e]
    same = all(ctx.verdict(e) == ctx.verdict(by_tag[e["tag"]]) for e in runs)
    lead_e = lead["e"] if lead else newest
    crec = ctx.case_rec(newest["case"], newest["sha"])
    ver = ctx.version(newest["case"], newest["sha"])
    att = _attempt(newest["erec"]) or _attempt(newest["rec"])
    proof = []
    for d in devs:
        proof += _proof(ctx, d)
    proof.sort(key=lambda p: ("result", "end", "other").index(p["kind"]))
    outputs = _outputs(ctx, task)
    actions = (["write_drafts"] if task and task.get("state") == "results_ready" else []) + \
        (["copy_comment"] if outputs["comment_text"] else []) + ["details"]
    clickup = _clickup(ctx, task)
    return {
        "key": "|".join(str(k) for k in key if k is not None), "task": newest["task"], "clickup": clickup,
        "case": newest["case"], "case_version": f"c{ver['n']}" if ver else None,
        "title": (clickup or {}).get("title") or crec.get("title") or newest["case"],
        "case_title": crec.get("title"), "area": crec.get("area") or str(newest["case"]).split("-")[0],
        "verdict": verdict, "verdict_label": LABELS[verdict], "when": newest["decided"], "runs": len(runs),
        "same_result": same, "build": _build_label(newest["build"]), "native": _native(runs),
        "duration_s": _secs(att.get("started"), att.get("ended")),
        "devices": [{"tag": d["tag"], "verdict": d["v"], "verdict_label": LABELS[d["v"]],
                     "failed_step": d["failed_step"]} for d in devs],
        "not_tested_devices": _not_tested(ctx, task, set(by_tag)),
        "explain": _explain(ctx, task, newest["case"]),
        "auto": _auto(ctx, verdict, devs, lead, crec, earlier),
        "steps": [{k: s[k] for k in ("n", "text", "outcome", "cleanup")} for s in (lead or devs[0])["steps"]]
        if devs else [],
        "proof": proof[:PROOF_MAX],
        "flight_plans": _flight_plans(ctx, crec),
        "state": task.get("state") if task else None, "outputs": outputs, "actions": actions,
        "run_ids": [e["eff"] for e in runs], "tasks": [newest["task"]] if newest["task"] else [],
        "_sha": newest["sha"], "_dv": tuple((d["tag"], d["v"]) for d in devs),
        "_clickup_id": (clickup or {}).get("task_id"), "_lead": lead_e["id"], "_entries": list(runs),
        "_devs": devs,  # each device's newest run and its steps, in the order of "devices" (the one-test card)
    }


def _merge(cards):
    """Merge consecutive cards of the same ClickUp test and case with the same case file and per-device results; the
    newest card keeps its task, texts and actions."""
    out, open_ = [], {}
    for c in cards:
        k = (c["_clickup_id"], c["case"]) if c["_clickup_id"] and c["task"] else None
        prev = open_.get(k) if k else None
        if prev is not None and prev["_sha"] == c["_sha"] and prev["_dv"] == c["_dv"]:
            prev["runs"] += c["runs"]
            prev["same_result"] = prev["same_result"] and c["same_result"]
            prev["run_ids"] += c["run_ids"]
            prev["_entries"] += c["_entries"]
            prev["tasks"] += [t for t in c["tasks"] if t not in prev["tasks"]]
            continue
        out.append(c)
        if k:
            open_[k] = c
    return out


def _cards(ctx, limit=CARDS_MAX):
    """The merged cards, newest first, still with their internal keys (_entries: the runs, newest first)."""
    groups = collections.OrderedDict()
    for e in ctx.entries:
        key = (e["task"], e["case"]) if e["task"] else ("tests/" + e["campaign"], e["case"], e["sha"])
        groups.setdefault(key, []).append(e)
    cards = [_card(ctx, k, runs) for k, runs in groups.items()]
    cards.sort(key=lambda c: (c["when"] or "", c["key"]), reverse=True)
    return _merge(cards)[:limit]


def _tickets_of(by_run, entries):
    """The ticket drafts of a card's runs. Drafts are keyed by the run they name (a correction <id>c1 counts for <id>);
    a card's runs are its entries' ids and their newest records."""
    rids = list(dict.fromkeys([e["id"] for e in entries] + [e["eff"] for e in entries]))
    return [t for rid in rids for t in by_run.get(rid, [])]


def _public(card):
    for k in [k for k in card if k.startswith("_")]:
        del card[k]
    return card


def result_cards(data, newest=CARDS_NEWEST, limit=CARDS_MAX, posting=(), task=None, case=None):
    """{"cards": the newest cards, "older": the rest up to limit cards in all, "running": the tests started and not
    ended (running_tasks)}, newest first. posting: the task ids whose results comment the app server is posting now (the
    Touch ID prompt open or the post on its way), shown as comment state "posting".
    With task (a task id or "current") or case (a case id): one test, {"test", "cards", "running"} (2026-11-04, API.md
    section 2); NotFound when that test or a run of that case does not exist."""
    ctx = _Ctx(data)
    if task is not None or case is not None:
        return _one_test(ctx, task, case, posting)
    cards = _cards(ctx, limit)
    by_run = _ticket_drafts(data)
    for c in cards:
        if len(c["tasks"]) > 1:
            c["auto"]["happened"] += f" {c['runs']} runs in a row gave this result."
        # a failed run gets a Swiss Army Knife ticket draft (2026-11-03, the person's rule): shown on its card
        c["tickets"] = _tickets_of(by_run, c["_entries"])
        c["report"] = report_field(c)
        c["comment"] = comment_state(ctx, ctx.tasks.get(c["task"]) if c["task"] else None, posting)
        _public(c)
    return {"cards": cards[:newest], "older": cards[newest:], "running": running_tasks(ctx)}


def card_entries(data, task, case):
    """The runs of one test's card (this task and case), newest first, as _Ctx entries ({id: the run, eff: its newest
    record, tag, rec, erec, verdict, ...}); None when the test has no run of the case. Any test, not only the newest
    merged cards (2026-11-04), so Report issue works on a test opened from History."""
    ctx = _Ctx(data)
    return [e for e in ctx.by_task.get(task, []) if e["case"] == case] or None


# ---- Report issue and the results comment (2026-11-03) --------------------------------------------------------------

def report_field(card, ctx=None):
    """Report issue: enabled only when a device of the card failed (confirmed or suspected) or partly passed; when
    disabled, why, in plain words. With ctx (the one-test answer, 2026-11-05, wingmate-debug FINDINGS #8) such a
    device counts only when its run may be filed (file_rule: the verified engine account, a filing device), as
    POST /api/tickets/draft then checks; else why, e.g. 'ran on the earlier engine account shared-stag; not filed'."""
    failed = [d for d in card.get("_devs") or card["devices"] if (d.get("v") or d.get("verdict")) in REPORT_VERDICTS]
    if failed and ctx is not None and card.get("_devs"):
        blocks = [run_block(ctx, d["e"]) for d in failed]
        if all(blocks):
            return {"enabled": False, "why": block_why(blocks[0])}
    if failed:
        return {"enabled": True, "why": None}
    return {"enabled": False, "why": REPORT_WHY.get(card["verdict"], REPORT_WHY["not_tested"])}


def file_rule(rec, target, engine):
    """The account and target rule of filing a ticket (serve.run_fileable; Report issue, File ticket, the next press
    and the unreported counts): None when a run whose newest record is rec, on target, may be filed; else ("account",
    the run's account label or None) when it did not run on the verified engine account (engine.json's account), or
    ("target", target) when tickets are not filed on that device."""
    acc = rec.get("account") if isinstance(rec.get("account"), dict) else {}
    label = acc.get("v") if isinstance(acc.get("v"), str) else None
    if acc.get("st") != "verified" or not isinstance(engine, str) or not engine or label != engine:
        return ("account", label if acc.get("st") == "verified" else None)
    if target not in FILE_TARGETS:
        return ("target", target)
    return None


def run_block(ctx, e):
    """file_rule for one of ctx's run entries (its newest record and its device)."""
    return file_rule(e["erec"], e["tag"], ctx.engine_account)


def _block_words(block):
    if block[0] == "target":
        return f"ran on {block[1]}, and tickets are filed on {_join(FILE_TARGETS)} only"
    if block[1]:
        return f"ran on the earlier engine account {block[1]}"
    return "ran without the verified engine account"


def block_why(block):
    """file_rule's answer in the page's words, as report.why ('ran on the earlier engine account shared-stag; not
    filed')."""
    return _block_words(block) + "; not filed"


def comment_rel(task):
    """The task's results comment file: its first outputs.comments entry that is this task's COMMENT file (review
    2026-11-03: a wrongly named first entry no longer hides the right one the worker wrote after it, and a later
    second COMMENT file never replaces one that may be posted), else its first entry, else None."""
    out = task.get("outputs") if isinstance(task, dict) and isinstance(task.get("outputs"), dict) else {}
    rels = [c for c in out.get("comments") or [] if isinstance(c, str)]
    mine = [c for c in rels if (m := COMMENT_FILE_RE.match(c)) and m.group(1) == task.get("id")]
    return (mine or rels or [None])[0]


def posted_rel(rel):
    """The posted marker of a comment file: COMMENT_<id>_<task>.posted.json next to COMMENT_<id>_<task>.md (one per
    task, since several tasks can share a campaign folder)."""
    return rel[:-len(".md")] + ".posted.json"


def _read_comment(full):
    """The comment file's text (UTF-8, errors replaced, a leading BOM dropped), at most COMMENT_READ_MAX bytes and one
    more, so an over-long file still reads as too long; None when it is not a plain file."""
    try:
        fd = os.open(full, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            return None
        raw = f.read(COMMENT_READ_MAX + 1)
    return raw.decode("utf-8", "replace").removeprefix("﻿")


def comment_draft(data, task):
    """The task's results comment as the Results card shows it and Post comment posts it, or None when the task lists
    no comment file: {rel, posted_rel, text, sha256, problem, posted}. text: the file's text with its line ends unified,
    stripped and redacted, so what the person reads is exactly what is posted; sha256: of its UTF-8 bytes. problem: why
    it cannot be posted (not the file name the worker writes for this task, a file the /files/ rule would not serve,
    empty, longer than COMMENT_MAX characters, holding a ClickUp token), else None. posted: the posted marker's
    {comment_id, posted_at, sha256, task_id} when one exists (any file there counts, so a comment is never offered for
    a second post), else None."""
    rel = comment_rel(task)
    if rel is None:
        return None
    out = {"rel": rel, "posted_rel": None, "text": None, "sha256": None, "problem": None, "posted": None}
    m = COMMENT_FILE_RE.match(rel)
    if not m or m.group(1) != task.get("id"):
        return dict(out, problem="The comment file is not tests/<campaign>/COMMENT_<ClickUp id>_<task>.md of this "
                                 "test, so the app does not post it.")
    out["posted_rel"] = posted_rel(rel)
    if os.path.lexists(os.path.join(data.root, *out["posted_rel"].split("/"))):
        full = tests_path(data, out["posted_rel"])
        marker = data._json(full) if full else None
        marker = marker if isinstance(marker, dict) else {}
        out["posted"] = {k: marker.get(k) if isinstance(marker.get(k), str) else None
                         for k in ("comment_id", "posted_at", "sha256", "task_id")}
    full = tests_path(data, rel)
    raw = _read_comment(full) if full else None
    if raw is None:
        return dict(out, problem="The comment file could not be read (missing, a link, or not a plain file).")
    text = redact.redact(raw.replace("\r\n", "\n").strip())
    if clickup_post.TOKEN_IN_TEXT.search(text):  # review 2026-11-03: never posted, and masked where the card shows it
        return dict(out, text=clickup_post.TOKEN_IN_TEXT.sub(redact.MASK, text),
                    problem=clickup_post.TOKEN_TEXT_PROBLEM)
    out.update(text=text, sha256=hashlib.sha256(text.encode("utf-8")).hexdigest())
    if not text:
        out["problem"] = "The comment file is empty."
    elif len(text) > COMMENT_MAX:
        out["problem"] = (f"The comment has {len(text)} characters; at most {COMMENT_MAX} can be posted. Press Add "
                          "result for a shorter one.")
    return out


def comment_state(ctx, task, posting=()):
    """A card's "comment": {state, text, sha256, posted_at, error, clickup_url}. state: "posted" (a posted marker),
    "posting" (the app server is posting it now), "drafting" (a write request with the comment is open), "draft" (a
    comment the person can post: text and its sha256, which Post comment sends back), "failed" (the draft cannot be
    posted, or the last write request failed: error says why) or "none"."""
    out = {"state": "none", "text": None, "sha256": None, "posted_at": None, "error": None, "clickup_url": None}
    if not isinstance(task, dict):
        return out
    ck = task.get("clickup") if isinstance(task.get("clickup"), dict) else {}
    out["clickup_url"] = clickup_post.task_url(ck.get("task_id"))
    d = comment_draft(ctx.data, task)
    asks = [r for _, r in sorted(ctx.requests.items()) if r.get("type") == "write" and r.get("task") == task.get("id")
            and isinstance(r.get("outputs"), list) and "comment" in r["outputs"]]
    last = asks[-1] if asks else None
    text = d["text"][:COMMENT_MAX] if d and d["text"] else None
    if d and d["posted"] is not None:
        return dict(out, state="posted", text=text, sha256=d["sha256"], posted_at=d["posted"]["posted_at"])
    if task.get("id") in posting and d and text and not d["problem"]:
        return dict(out, state="posting", text=text, sha256=d["sha256"])
    if last and last.get("state") in OPEN_REQ:
        return dict(out, state="drafting")
    if d and text and not d["problem"]:
        return dict(out, state="draft", text=text, sha256=d["sha256"])
    if d and d["problem"]:
        return dict(out, state="failed", text=text, error=d["problem"])
    if last and last.get("state") == "failed":
        return dict(out, state="failed", error=_cut(_last_note(last) or "Hover did not write the comment.",
                                                    NOTE_MAX))
    return out


# ---- test titles (2026-11-04, API.md section 1.3) -------------------------------------------------------------------

def _list(x):
    return x if isinstance(x, list) else []


def _strs(x):
    """The strings of a list ([] for anything else)."""
    return [v for v in _list(x) if isinstance(v, str)]


def _first_text(*xs):
    return next((x for x in xs if isinstance(x, str) and x.strip()), None)


def _req_of(ctx, rid):
    """A worker request by id ({} when there is none)."""
    r = ctx.requests.get(rid) if isinstance(rid, str) else None
    return r if isinstance(r, dict) else {}


def _pinned_rec(ctx, task, cid):
    """The case record of the version the task's start pinned (task.case_versions), else the newest version's."""
    for v in _list(task.get("case_versions")):
        if isinstance(v, dict) and v.get("id") == cid and isinstance(v.get("sha256"), str):
            ver = ctx.version(cid, v["sha256"])
            if ver:
                return ver.get("rec") or {}
    return ctx.case_rec(cid)


def _parts(crec):
    """A case version's parts (case-rule data, 2026-11-04): [{n, text}] in part order, each valid part once; [] when
    it has none."""
    out, seen = [], set()
    for p in _list(crec.get("parts") if isinstance(crec, dict) else None):
        if isinstance(p, dict) and data_mod.is_int(p.get("n")) and p["n"] not in seen \
                and isinstance(p.get("text"), str) and p["text"].strip():
            seen.add(p["n"])
            out.append({"n": p["n"], "text": _cut(p["text"], PART_TEXT_MAX)})
    return sorted(out, key=lambda p: p["n"])


def description_title(text, limit=TITLE_LIST):
    """A test's title cut from the person's description (API.md 1.3 rule 3), or None when nothing is left. Redacted,
    then per line: a leading list mark, asides in parentheses and label lines ending in ':' ('Test cases:') dropped.
    One line stays whole (cut only at limit); several lines are each cut at TITLE_LINE characters on a word and joined
    with ' · ', as many whole lines as fit in limit, with '…' when lines were left out."""
    if not isinstance(text, str):
        return None
    lines = []
    for line in redact.redact(text).splitlines():
        line, before = LIST_MARK_RE.sub("", line, count=1), None
        while before != line:
            before, line = line, PARENS.sub("", line)
        line = " ".join(line.split())
        if line and not line.endswith(":"):
            lines.append(line)
    if len(lines) <= 1:
        return _cut(lines[0], limit) if lines else None
    parts = [_cut(x, TITLE_LINE) for x in lines]
    out, kept = _cut(parts[0], limit), 1
    for p in parts[1:]:
        if len(out) + 3 + len(p) > limit:
            break
        out, kept = out + " · " + p, kept + 1
    if kept < len(parts) and not out.endswith("…"):
        out = out + "…" if len(out) < limit else _cut(out + "…", limit)
    return out


def _clickup_name(ctx, task):
    """The ClickUp name of a task's test: its request's clickup.title (_clickup), else the name in task.text."""
    title = (_clickup(ctx, task) or {}).get("title")
    if isinstance(title, str) and title.strip():
        return title
    m = TEXT_NAME_RE.match(task.get("text")) if isinstance(task.get("text"), str) else None
    return m.group(1) if m else None


def _clickup_named(ctx, task):
    """_clickup with its title the ClickUp name (the page shows it grey, 'ClickUp: <name> ↗'); None without one."""
    ck = _clickup(ctx, task)
    return dict(ck, title=_clickup_name(ctx, task)) if ck else None


def test_title(ctx, task, limit=TITLE_LIST):
    """The name of a test everywhere (API.md 1.3): the plan's title (task.title, else the start request's title, else
    the check request's result.title); else, for a test of one case, the title of the case version that ran (before
    any run, the version the start pinned); else, for a New test task only, its description cut (description_title; a
    Today task's guidance is an instruction to Hover, not a description); else the ClickUp name; else the task id.
    limit: TITLE_HEADER for the Results header, TITLE_LIST for lists."""
    origin = task.get("origin") if isinstance(task.get("origin"), dict) else {}
    start, check = _req_of(ctx, origin.get("start")), _req_of(ctx, origin.get("check"))
    result = check.get("result") if isinstance(check.get("result"), dict) else {}
    plan_title = _first_text(task.get("title"), start.get("title"), result.get("title"))
    if plan_title:
        return _cut(plan_title, limit)
    cases = _strs(task.get("cases"))
    if len(cases) == 1:
        ran = [e for e in ctx.by_task.get(task.get("id"), []) if e["case"] == cases[0]]
        crec = ctx.case_rec(cases[0], ran[0]["sha"]) if ran else _pinned_rec(ctx, task, cases[0])
        if _first_text(crec.get("title")):
            return _cut(crec["title"], limit)
    if task.get("intake") is True:
        title = description_title(task.get("guidance"), limit)
        if title:
            return title
    name = _clickup_name(ctx, task)
    return _cut(name, limit) if name else str(task.get("id") or "")


def request_title(req):
    """The title of a New test request on GET /api/worker (API.md section 5): its plan title (a check's result.title,
    a start's own title copied at Start), else its description cut (description_title, list form), else the ClickUp
    name, the ClickUp task id or the request id; None for any other request."""
    if not isinstance(req, dict) or req.get("intake") is not True or req.get("type") not in ("check", "start"):
        return None
    result = req.get("result") if isinstance(req.get("result"), dict) else {}
    plan_title = _first_text(result.get("title") if req["type"] == "check" else req.get("title"))
    if plan_title:
        return _cut(plan_title, TITLE_LIST)
    title = description_title(req.get("guidance"), TITLE_LIST)
    if title:
        return title
    ck = req.get("clickup") if isinstance(req.get("clickup"), dict) else {}
    name = _first_text(ck.get("title"), ck.get("task_id"), req.get("id"))
    return _cut(name, TITLE_LIST) if name else None


# ---- the tests the person started that have not ended (2026-11-03) --------------------------------------------------

def _live(ctx):
    """(run.py's progress files, the shot paths /files/ serves), read once per answer."""
    if ctx.live is None:
        progress = [obj for _, _, obj in ctx.data._state_dir("progress")]
        ctx.live = (progress, ctx.idx["shots"] | {p["last_shot"] for p in progress
                                                 if isinstance(p.get("last_shot"), str)})
    return ctx.live


def _ongoing(ctx, task):
    """(ongoing, its start request or {}). Ongoing: a task the person started with Run whose start request is still new
    or claimed (the worker has not finished it), whatever the task's state: Hover moves the task to results_ready
    before it writes its done note and ends the start (SPEC.md 'the test's start request is new or claimed'; review
    2026-11-04), so the result box comes with that note. Or a task /qa-run is running from a chat (no start request).
    A run that ended before results (start failed or cancelled) and a task queued in a chat are not ongoing."""
    origin = task.get("origin") if isinstance(task.get("origin"), dict) else {}
    req = _req_of(ctx, origin.get("start"))
    if origin.get("start") is not None:
        return req.get("state") in OPEN_REQ, req
    return task.get("state") == "running", req


def _pick_progress(tag, cases, since, progress):
    """The newest run.py progress file of this target for one of the task's cases that started at or after since."""
    mine = [p for p in progress if p.get("target") == tag and p.get("case") in cases
            and str(p.get("started") or "") >= since]
    return max(mine, key=lambda p: (str(p.get("started") or ""), str(p.get("updated") or "")), default=None)


def _device_progress(ctx, tag, cases, since, progress, shots, pins=None):
    """One device of a running test, from its newest progress file (_pick_progress): {tag, state, step, of, text,
    shot_url, updated} (and case, run, verdict); "waiting" when none has started yet. pins ({case: the case record the
    start pinned}, the one-test answer) adds part ({n, of, text} of the part the running step carries, else None) and
    waits_at (the handback step of a run that finished BLOCKED, else None)."""
    p = _pick_progress(tag, cases, since, progress)
    if p is None:
        out = {"tag": tag, "state": "waiting", "step": None, "of": None, "text": None, "shot_url": None,
               "updated": None, "case": None, "run": None, "verdict": None}
    else:
        total = p.get("steps_total") if data_mod.is_int(p.get("steps_total")) else None
        step = p.get("step") if isinstance(p.get("step"), dict) else {}
        finished = p.get("state") == "finished"
        n = total if finished else (step.get("n") if data_mod.is_int(step.get("n")) else None)
        text = _cut(_quotes(step["text"]), STEP_IN_SENTENCE) if isinstance(step.get("text"), str) and not finished \
            else None
        shot = p.get("last_shot")
        out = {"tag": tag, "state": "finished" if finished else "running", "step": n, "of": total, "text": text,
               "shot_url": ctx.data._file_url(shot, shots) if isinstance(shot, str) else None,
               "updated": p.get("updated") if isinstance(p.get("updated"), str) else None,
               "case": p.get("case"), "run": p.get("run") if isinstance(p.get("run"), str) else None,
               "verdict": p.get("verdict") if finished and isinstance(p.get("verdict"), str) else None}
    if pins is not None:
        out.update(part=_part_now(out, pins), waits_at=_waits_at(ctx, out))
    return out


def _part_now(d, pins):
    """{n, of, text}: the part that the device's running step carries in the case version the start pinned, else None.
    The page writes 'Step 3 of 6: <text>' (SPEC 'Step p of P: <part>')."""
    if d["state"] != "running" or not data_mod.is_int(d["step"]):
        return None
    crec = pins.get(d["case"]) or {}
    cs = next((s for s in _list(crec.get("steps")) if isinstance(s, dict) and data_mod.is_int(s.get("n"))
               and s["n"] == d["step"]), None)
    n = cs.get("part") if cs and not cs.get("always") and data_mod.is_int(cs.get("part")) else None
    parts = _parts(crec)
    hit = next((p for p in parts if p["n"] == n), None) if n is not None else None
    return {"n": n, "of": len(parts), "text": hit["text"]} if hit else None


def _waits_at(ctx, d):
    """The handback step of the device's run when its progress finished BLOCKED ('TA5 waits for your OK at step n')."""
    if d["state"] != "finished" or d["verdict"] != "BLOCKED" or d["run"] not in ctx.idx["runs"]:
        return None
    eff = data_mod.newest(d["run"], ctx.idx["run_succ"])
    n = _handback((ctx.idx["runs"].get(eff) or ctx.idx["runs"][d["run"]])["rec"]).get("step")
    return n if data_mod.is_int(n) else None


def _wingmate(ctx, req):
    """"working" (the start request claimed, or a chat run), else "off" when Hover is off, else "waiting"."""
    if not req.get("id") or req.get("state") != "new":
        return "working"
    if ctx.off is None:
        try:
            ctx.off = worker.status(ctx.data.root).get("state") == "off"
        except Exception:  # noqa: BLE001 (the worker's state never takes the Results page down)
            ctx.off = False
    return "off" if ctx.off else "waiting"


def _case_row(ctx, cid, crec, devices, since, on=None):
    """One case of a running test: {case, title, devices: {tag: "running", "done", "waiting" or None}}. on: the devices
    the start runs this case on (its cases[].targets); a device that does not run it is None (2026-11-05,
    wingmate-debug FINDINGS #25: the page writes '–'); without it every device runs every case."""
    states = {}
    for d in devices:
        if on is not None and d["tag"] not in on:
            states[d["tag"]] = None
        elif d["case"] == cid and d["state"] == "running":
            states[d["tag"]] = "running"
        elif (d["case"] == cid and d["state"] == "finished") or any(
                e["case"] == cid and e["tag"] == d["tag"] and str(e["started"] or "") >= since for e in ctx.entries):
            states[d["tag"]] = "done"
        else:
            states[d["tag"]] = "waiting"
    return {"case": cid, "title": _first_text(crec.get("title")) or cid, "devices": states}


def _running_row(ctx, tid, task, full=False):
    """The running row of one ongoing test (_ongoing), else None: {task, title, cases, started, devices, state,
    request, note}. full (the one-test answer and History, API.md 2.5) adds wingmate ("waiting", "off" or "working"),
    current_case ({n, of, id} of the case a device runs now, else None), case_rows ([{case, title, devices}] in the
    task's case order) and each device's part and waits_at."""
    ongoing, req = _ongoing(ctx, task)
    if not ongoing:
        return None
    progress, shots = _live(ctx)
    cases = _strs(task.get("cases"))
    tags = sorted(dict.fromkeys(_strs(req.get("targets")) or _strs(task.get("targets"))), key=_tag_key)
    since = str(req.get("claimed_at") or req.get("created") or task.get("created") or "")
    note = _last_note(req) if req else None
    pins = {c: _pinned_rec(ctx, task, c) for c in cases} if full else None
    row = {"task": tid, "title": test_title(ctx, task, TITLE_LIST), "cases": cases,
           "started": req.get("created") or task.get("created"),
           "devices": [_device_progress(ctx, tag, cases, since, progress, shots, pins) for tag in tags],
           "state": task.get("state"),
           "request": {"id": req["id"], "state": req.get("state")} if req.get("id") else None,
           "note": _cut(note, NOTE_MAX) if note else None}
    if full:
        now = [p for p in (_pick_progress(tag, cases, since, progress) for tag in tags)
               if p and p.get("state") != "finished"]
        cur = max(now, key=lambda p: (str(p.get("started") or ""), str(p.get("updated") or "")), default=None)
        on = _case_targets(req)
        row.update(wingmate=_wingmate(ctx, req),
                   current_case={"n": cases.index(cur["case"]) + 1, "of": len(cases), "id": cur["case"]} if cur
                   else None,
                   case_rows=[_case_row(ctx, c, pins[c], row["devices"], since, on.get(c)) for c in cases])
    return row


def _case_targets(req):
    """{case id: the devices the start request runs it on}, from its cases[] (each narrowed to its own devices at
    Start: writes._start_intake, start_run); a case the request does not list is left out (every device runs it)."""
    out = {}
    for c in _list(req.get("cases")):
        if isinstance(c, dict) and isinstance(c.get("targets"), list):
            for cid in _strs(c.get("engine_cases")):
                out.setdefault(cid, set()).update(_strs(c["targets"]))
    return out


def running_tasks(ctx):
    """[{task, title, cases, started, devices: [{tag, state, step, of, text, shot_url, updated, ...}], state, request,
    note}] newest first: the ongoing tests (_ongoing). Each device's progress comes from run.py's progress files, the
    data the New test progress view reads (a target's runs of the task's cases since the worker took the request).
    title: test_title in list form (2026-11-04)."""
    out = [row for row in (_running_row(ctx, tid, t) for tid, t in ctx.tasks.items()) if row]
    out.sort(key=lambda r: (str(r["started"] or ""), r["task"]), reverse=True)
    return out


def _ticket_drafts(data):
    """{run id: [{title, description, dir, n, filed, state, cl_id, offline, request, error, target, run}]}: the ticket
    drafts (tests/<campaign>/tickets/<n>/) whose draft.json names that run's record, with where File ticket stands
    (ticket_state); and, for a run with no draft folder yet, the newest "draft" request the person asked for with Report
    issue (2026-11-03): state "drafting" while it is open, "failed" (error: the worker's note) when it failed, with dir,
    n and title empty."""
    out = collections.defaultdict(list)
    try:
        drafts = data.tickets().get("drafts") or []
        runs = data._runs()["runs"]
        reqs = collections.defaultdict(list)
        asks = collections.defaultdict(list)
        for _, _, r in sorted(data._state_dir("worker", W_RE), key=lambda x: os.path.basename(x[0])):
            if r.get("kind") == "worker_request" and r.get("type") == "file" and isinstance(r.get("ticket_dir"), str):
                reqs[r["ticket_dir"]].append(r)
            if r.get("kind") == "worker_request" and r.get("type") == "draft" and isinstance(r.get("run"), str):
                asks[r["run"]].append(r)
    except Exception:  # noqa: BLE001 (a broken draft folder never takes the Results page down)
        return out
    for d in drafts:
        rr = d.get("run_record")
        if not isinstance(rr, str) or not rr.endswith(".json"):
            continue
        named = os.path.basename(rr)[:-5]
        rid = named[:-2] if re.search(r"c\d$", named) else named  # a draft from a correction record belongs to the run
        item = runs.get(named) or runs.get(rid) or {}
        offline = any(ready_offline(runs.get(r, {}).get("rec") or {}) for r in (named, rid))
        st = ticket_state(d, reqs.get(d.get("dir"), []), offline)
        out[rid].append(dict({"title": d.get("title") or "", "description": d.get("description") or "",
                              "dir": d.get("dir"), "n": d.get("n"), "filed": bool(d.get("filed") or d.get("submitted")),
                              "target": (item.get("base") or {}).get("target"), "run": rid}, **st))
    for rid, rs in asks.items():
        live = [r for r in rs if r.get("state") != "cancelled"]
        last = live[-1] if live else None
        if rid in out or not last or last.get("state") not in OPEN_REQ + ("failed",):
            continue  # a draft folder shows its own state; a done request without one shows nothing
        item = runs.get(rid) or {}
        failed = last.get("state") == "failed"
        error = _cut(_last_note(last) or "The ticket was not drafted.", NOTE_MAX) if failed else None
        out[rid].append({"title": "", "description": "", "dir": None, "n": None, "filed": False,
                         "target": (item.get("base") or {}).get("target"), "run": rid,
                         "state": "failed" if failed else "drafting", "cl_id": None,
                         "offline": ready_offline(item.get("rec") or {}),
                         "request": {"id": last.get("id"), "state": last.get("state")}, "error": error})
    return out


def ready_offline(rec):
    """True when the run's ready step saw the app offline (its identity "ready" actions say so, act.py F3)."""
    for a in rec.get("attempts") or []:
        for i in (a.get("identity") or []) if isinstance(a, dict) else []:
            if isinstance(i, dict) and i.get("verb") == "ready" and \
                    any(isinstance(x, str) and "offline" in x for x in i.get("actions") or []):
                return True
    return False


def _age_s(iso, now):
    try:
        t = datetime.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (now - t).total_seconds() if t.tzinfo else None


def _last_note(req):
    notes = [n.get("text") for n in req.get("notes") or [] if isinstance(n, dict) and isinstance(n.get("text"), str)]
    return notes[-1] if notes else None


def ticket_state(draft, reqs, offline, now=None):
    """{state, cl_id, offline, request, error} of one ticket draft. state: "filed" (submitted.json or filed.json),
    "filing" (the worker took the file request), "approved" (Touch ID given, the worker has not taken it yet),
    "waiting_online" (the device was offline: connect it, then File ticket again), "failed" (the last file request
    failed, error says why) or "draft". reqs: the folder's file requests, oldest first."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    sub = draft.get("submitted") if isinstance(draft.get("submitted"), dict) else None
    filed = draft.get("filed") if isinstance(draft.get("filed"), dict) else None
    out = {"state": "draft", "cl_id": None, "offline": bool(offline), "request": None, "error": None}
    if sub or filed:
        cl = next((c for c in ((sub or {}).get("cl_id"), (filed or {}).get("cl")) if isinstance(c, str)
                   and CL_ID_RE.match(c)), None)
        return dict(out, state="filed", cl_id=cl)
    live = [r for r in reqs if r.get("state") != "cancelled"]
    last = live[-1] if live else None
    if last and last.get("state") == "new" and (_age_s(last.get("created"), now) or 0) > FILE_STALE_S:
        return dict(out, state="failed", error="The approval expired before Hover took it (30 minutes). "
                                               "Press File ticket again.")
    if last and last.get("state") in ("new", "claimed"):
        return dict(out, state="approved" if last["state"] == "new" else "filing",
                    request={"id": last.get("id"), "state": last.get("state")})
    if last and last.get("state") == "failed":
        note = _last_note(last)
        waiting = isinstance(note, str) and OFFLINE_NOTE_RE.match(note)
        return dict(out, state="waiting_online" if waiting else "failed", error=note or "The ticket was not filed.")
    if last and last.get("state") == "done":
        return dict(out, state="failed", error="Hover finished without a submitted.json; check ClickUp for the "
                                               "ticket before filing again.")
    return dict(out, state="waiting_online" if offline else "draft")


# ---- one test on Results, History's 'Your tests' (2026-11-04, API.md sections 1, 2 and 4) ---------------------------

def _worst(codes):
    """The worst verdict code, not_tested left out unless it is the only one (_card's rule); None for none."""
    codes = list(codes)
    present = [v for v in codes if v != "not_tested"] or codes
    return min(present, key=SEVERITY.index) if present else None


def _ts(iso):
    t = data_mod.parse_time(iso)
    return t.timestamp() if t else 0.0


def _newest_time(times):
    """The newest of some ISO times, as written; None when none is a time."""
    return max((t for t in times if data_mod.parse_time(t)), key=_ts, default=None)


def _run_press(s):
    """The order of tests by Run press: task.created, then the task id."""
    return _ts(s["created"]), s["id"]


def _hist(task):
    return [h for h in _list(task.get("history")) if isinstance(h, dict)]


def _open_work(ctx, tid, types):
    """The task's worker requests of these types that are new or claimed (worker.open_work: a new file request whose
    approval expired is no work, 2026-11-05, wingmate-debug FINDINGS #16)."""
    return worker.open_work(ctx.requests.values(), tid, types)


def _press(code, case=None):
    return {"press": code, "label": PRESS[code], "case": case}


def _summary(ctx, tid, task, ongoing, start, posting):
    """One test's state from its runs (API.md section 1): each device's verdict per case (dv), the case verdicts and
    the test verdict, its devices, times, comment, tickets, failures and Plan again body. _summaries adds group,
    done_how and next."""
    runs = ctx.by_task.get(tid, [])
    origin = task.get("origin") if isinstance(task.get("origin"), dict) else {}
    by_case, newest = collections.OrderedDict(), {}
    for e in runs:  # newest first, so by_case lists the case of the newest run first
        by_case.setdefault(e["case"], []).append(e)
        newest.setdefault((e["case"], e["tag"]), e)
    dv = {k: ctx.verdict(e) for k, e in newest.items()}
    case_v = {c: _worst(v for (cc, _), v in dv.items() if cc == c) for c in by_case}
    listed = list(dict.fromkeys(c for c in _strs(task.get("cases")) if c in by_case))
    order = listed + [c for c in by_case if c not in listed]
    tags = sorted({e["tag"] for e in runs if isinstance(e["tag"], str)}, key=_tag_key)
    if not tags and ongoing:
        tags = sorted(dict.fromkeys(_strs(start.get("targets")) or _strs(task.get("targets"))), key=_tag_key)
    history = _hist(task)
    comment = comment_state(ctx, task, posting)
    failures = {k for k, v in dv.items() if v in REPORT_VERDICTS}
    tickets = {c: _tickets_of(ctx.by_run, es) for c, es in by_case.items()}
    # the failures a ticket can be filed for: Report issue and the unreported counts skip a run on an earlier engine
    # account or a device tickets are not filed on (run_block, 2026-11-05, wingmate-debug FINDINGS #8)
    fail_cases = [c for c in order if any(k[0] == c and not run_block(ctx, newest[k]) for k in failures)]
    ck = task.get("clickup") if isinstance(task.get("clickup"), dict) else {}
    s = {"id": tid, "task": task, "created": str(task.get("created") or ""), "ongoing": ongoing, "start": start,
         "check": _req_of(ctx, origin.get("check")), "runs": runs, "by_case": by_case, "order": order, "dv": dv,
         "case_v": case_v, "verdict": _worst(case_v.values()), "devices": tags,
         "results_at": None if ongoing else next((h["at"] for h in history if h.get("to") == "results_ready"
                                                 and isinstance(h.get("at"), str)), None),
         "last_change": _newest_time([history[-1].get("at") if history else None, start.get("done_at"),
                                      comment.get("posted_at")]),
         "stopped_early": not ongoing and bool(runs) and start.get("state") in ("failed", "cancelled"),
         "comment": comment, "failures": failures, "covered": set(dv), "tickets": tickets, "fail_cases": fail_cases,
         "unreported": sum(1 for c in fail_cases if not any(t.get("state") == "filed" for t in tickets[c])),
         "app_result": any(v in APP_RESULTS for v in dv.values()), "cu": ck.get("task_id")}
    s["plan_again"] = _plan_again(ctx, s)
    return s


def _plan_again(ctx, s):
    """The body Plan again sends to POST /api/worker/check: the Plan screen's Prepare again body (the description, the
    devices and, for an answered plan, its answers). None while the test runs, for a test not from New test, without
    its New test check request (or one without a ClickUp task id or a description), and when any case gave an app
    result (API.md 2.3): the page shows Plan again exactly when this is not None. newer (2026-11-05, wingmate-debug
    FINDINGS #21): a newer plan of the same ClickUp task (_newer_plan), else None; the page then links that plan
    instead of an orange Plan again, and drops "newer" from the body it sends."""
    chk, task = s["check"], s["task"]
    cu = chk.get("clickup") if isinstance(chk.get("clickup"), dict) else {}
    if s["ongoing"] or s["app_result"] or task.get("intake") is not True or chk.get("type") != "check" \
            or chk.get("intake") is not True or not _first_text(cu.get("task_id")) \
            or not _first_text(chk.get("guidance")):
        return None
    answered = bool(chk.get("follows")) and bool(_list(chk.get("answers")))
    return {"intake": True, "prepare": True, "task_id": cu["task_id"], "guidance": chk["guidance"],
            "targets": _strs(chk.get("targets")), "answers_from": chk.get("id") if answered else None,
            "newer": _newer_plan(ctx, chk)}


def _newer_plan(ctx, chk, now=None):
    """{id, fresh} of the newest New test plan of the same ClickUp task asked for after chk (Plan again, Prepare again
    or Edit made it; a cancelled or failed one does not count), else None. fresh while it can still lead to a run:
    Hover is making it (new, at most worker.STALE_NEW_S["check"] old, or claimed) or its device check is at most
    worker.CHECK_FRESH_S old (Run's own limit, worker.check_usable)."""
    cu = (chk.get("clickup") or {}).get("task_id") if isinstance(chk.get("clickup"), dict) else None
    after = _ts(chk.get("created"))
    newer = [r for r in ctx.requests.values() if r.get("type") == "check" and r.get("intake") is True
             and isinstance(r.get("clickup"), dict) and r["clickup"].get("task_id") == cu and cu
             and r.get("id") != chk.get("id") and _ts(r.get("created")) > after
             and r.get("state") not in ("cancelled", "failed")]
    r = max(newer, key=lambda x: (_ts(x.get("created")), str(x.get("id"))), default=None)
    if r is None:
        return None
    if r.get("state") == "new":
        age = worker.age_s(r.get("created"), now)
        fresh = age is not None and age <= worker.STALE_NEW_S["check"]
    elif r.get("state") == "claimed":
        fresh = True
    else:
        res = r.get("result") if isinstance(r.get("result"), dict) else {}
        age = worker.age_s(res.get("checked_at"), now)
        fresh = r.get("state") == "done" and age is not None and age <= worker.CHECK_FRESH_S
    return {"id": r.get("id"), "fresh": fresh}


def _summaries(ctx, posting=()):
    """{task id: _summary} of every test: a task that is ongoing or has runs. Zombie queued tasks (no runs, no open
    start) and runs without a task (chat runs) are no tests. Each with its group, done_how and next press."""
    if ctx.by_run is None:
        ctx.by_run = _ticket_drafts(ctx.data)
    out = {}
    for tid, t in ctx.tasks.items():
        ongoing, start = _ongoing(ctx, t)
        if ongoing or ctx.by_task.get(tid):
            out[tid] = _summary(ctx, tid, t, ongoing, start, posting)
    _groups(out)
    for s in out.values():
        s["next"] = _next(ctx, s)
    return out


def _groups(S):
    """Each test's group and done_how (API.md 1.4). Done: its comment is posted, or the person marked it done, or a
    newer test of the same ClickUp task has results, ran every case and device this one ran and failed again wherever
    this one failed (the newest such test is named: "failed_again" when this one had failures, else "replaced").
    Otherwise tested (ongoing while it runs). Nothing moves by age, so a failure never leaves view by itself."""
    by_cu = collections.defaultdict(list)
    for s in S.values():
        s["group"], s["done_how"] = ("ongoing" if s["ongoing"] else "tested"), None
        if not s["ongoing"] and s["cu"]:
            by_cu[s["cu"]].append(s)
    for s in S.values():
        if s["ongoing"]:
            continue
        cm, how = s["comment"], None
        if cm["state"] == "posted":
            how = {"how": "posted", "at": cm["posted_at"], "by_task": None, "url": cm["clickup_url"]}
        elif s["task"].get("state") == "closed":
            at = next((h.get("at") for h in reversed(_hist(s["task"])) if h.get("to") == "closed"), None)
            how = {"how": "marked", "at": at, "by_task": None, "url": None}
        else:
            newer = [o for o in by_cu.get(s["cu"], []) if _run_press(o) > _run_press(s)
                     and s["covered"] <= o["covered"] and s["failures"] <= o["failures"]]
            o = max(newer, key=_run_press, default=None)
            if o:
                how = {"how": "failed_again" if s["failures"] else "replaced",
                       "at": o["results_at"] or o["last_change"], "by_task": o["id"], "url": None}
        if how:  # a test in Done offers no Plan again: the person treats it as done (review 2026-11-04)
            s["group"], s["done_how"], s["plan_again"] = "done", how, None


def run_failed(rec):
    """A FAIL, or a PARTIAL or BLOCKED run with a step whose check or readback failed (_missed): the rule File ticket and
    Report issue file by (serve.run_fileable)."""
    v = _vobj(rec).get("v")
    if v == "FAIL":
        return True
    return v in ("PARTIAL", "BLOCKED") and any(_missed(s) for s in _attempt(rec).get("steps") or []
                                               if isinstance(s, dict))


def _fileable(ctx, t):
    """A ticket draft File ticket can send: a draft folder in a FILE_STATES state whose run still failed in its newest
    record (a correction may have made the run a test error since) on the verified engine account and a filing device
    (file_rule): the rule serve.run_fileable applies."""
    return _file_check(ctx, t)[0]


def _file_check(ctx, t):
    """(fileable, why) of one ticket of a card (2026-11-05, wingmate-debug FINDINGS #3 and #8): fileable when File
    ticket can send it now (_fileable); why, for a draft folder in a FILE_STATES state that File ticket cannot send,
    in plain words (NOT_FILEABLE, or the account and target rule), else None. A ticket being drafted, approved, filed
    or filing is not fileable and has no why: the page shows its own state."""
    if not (t.get("dir") and t.get("state") in FILE_STATES):
        return False, None
    rid = t.get("run")
    item = ctx.idx["runs"].get(rid) if isinstance(rid, str) else None
    if not item:
        return False, NOT_FILEABLE["no_run"]
    eff = data_mod.newest(rid, ctx.idx["run_succ"])
    rec = (ctx.idx["runs"].get(eff) or item)["rec"]
    if not run_failed(rec):
        return False, NOT_FILEABLE["corrected" if eff != rid and _vobj(rec).get("v") == "ERROR" else "not_failed"]
    block = file_rule(rec, item["base"]["target"], ctx.engine_account)
    if block:
        return False, f"Not filed: its run {_block_words(block)}."
    return True, None


def _next(ctx, s):
    """The next press of a tested test, in the page's button order (API.md 1.5); None for an ongoing or done test and
    when nothing applies."""
    if s["group"] != "tested":
        return None
    for c in s["order"]:
        if any(_fileable(ctx, t) for t in s["tickets"][c]):
            return _press("file_ticket", c)
    if s["comment"]["state"] == "draft":
        return _press("post_comment")
    for c in s["fail_cases"]:
        if not any(t.get("dir") or t.get("state") == "drafting" for t in s["tickets"][c]):
            return _press("report_issue", c)
    if s["plan_again"] and s["plan_again"].get("newer") is None:  # a newer plan of the test: Plan again is secondary
        return _press("plan_again")
    task = s["task"]
    ck = task.get("clickup") if isinstance(task.get("clickup"), dict) else {}
    if s["comment"]["state"] in ("none", "failed") and task.get("state") in RESULT_STATES \
            and (ck.get("task_id") or ck.get("cl")) and not _open_work(ctx, s["id"], ("write",)):
        return _press("add_result")
    return None


def _result(case_v, dv):
    """The result box (API.md 2.3): the test verdict, its words, the case counts and one sentence ("Test error in 5 of
    5 cases, on TA5 and ET65", "Passed: 2 of 2 cases, on W044", "Failed (suspected) in 1 of 3 cases, on W044; 2
    passed")."""
    if not case_v:
        return None
    v = _worst(case_v.values())
    counts = {code: sum(1 for x in case_v.values() if x == code) for code in SEVERITY}
    those = {c for c, x in case_v.items() if x == v}
    tags = sorted({tag for (c, tag), x in dv.items() if c in those and x == v and isinstance(tag, str)}, key=_tag_key)
    total = len(case_v)
    head = f"{counts[v]} of {total} {'case' if total == 1 else 'cases'}" + (f", on {_join(tags)}" if tags else "")
    if v == "passed":
        sentence = "Passed: " + head
    else:
        sentence = f"{WORDS[v][:1].upper()}{WORDS[v][1:]} in {head}"
        others = [_count_word(counts[x], x) for x in SEVERITY if x != v and counts[x]]
        if others:
            sentence += "; " + ", ".join(others)
    return {"verdict": v, "verdict_label": LABELS[v], "word": WORDS[v], "counts": counts, "case_count": total,
            "sentence": sentence, "meaning": MEANINGS[v]}


def _results_words(case_v):
    """The results in words: the case verdict counts in severity order ('5 test errors', '1 failed (suspected)')."""
    counts = collections.Counter(case_v.values())
    return ", ".join(_count_word(counts[x], x) for x in SEVERITY if counts[x]) or None


def _what_it_checks(ctx, task, cid, fallback):
    """The plan's line for the case (task.plan_cases[].text), else the start request's case line that runs it (a Today
    test), else what the case checks (auto.tested). A leading "<CASE-ID> cN: " of an older plan's line is left out:
    case ids and c0/c1 labels stay in Run records (SPEC 'Gone')."""
    lines = []
    if isinstance(task, dict):
        lines += [pc.get("text") for pc in _list(task.get("plan_cases")) if isinstance(pc, dict) and pc.get("id") == cid]
        origin = task.get("origin") if isinstance(task.get("origin"), dict) else {}
        lines += [sc.get("text") for sc in _list(_req_of(ctx, origin.get("start")).get("cases"))
                  if isinstance(sc, dict) and cid in _strs(sc.get("engine_cases"))]
    for text in lines:
        if _first_text(text) and CASE_LEAD_RE.sub("", text, count=1).strip():
            return CASE_LEAD_RE.sub("", text, count=1)
    return fallback or None


def _rows(ctx, card, tags, parts):
    """The step × device table of a card (API.md 2.4): one row per part of the case version that ran, else its check
    steps (steps with an assert, clean-up left out) plus each device's stop step. A cell is that device's outcome
    (for a part: stopped, else not_as_expected, else as_expected, else not_run over the part's steps); None when the
    device did not run the case or has no such step or part."""
    devs = {d["tag"]: d for d in card["_devs"]}
    rows = []
    if parts:
        for p in parts:
            cells = {}
            for tag in tags:
                mine = [s["outcome"] for s in devs[tag]["steps"] if s["part"] == p["n"]] if tag in devs else []
                cells[tag] = next((o for o in ("stopped", "not_as_expected", "as_expected") if o in mine),
                                  "not_run") if mine else None
            rows.append({"n": p["n"], "text": p["text"], "cells": cells})
        return rows
    csteps = {s["n"]: s for s in _list(ctx.case_rec(card["case"], card["_sha"]).get("steps"))
              if isinstance(s, dict) and data_mod.is_int(s.get("n"))}
    ns = {n for n, s in csteps.items() if s.get("assert") and not s.get("always")}
    ns |= {d["failed_step"]["n"] for d in card["_devs"] if d["failed_step"]}
    for n in sorted(ns):
        steps = {tag: next((s for s in devs[tag]["steps"] if s["n"] == n), None) if tag in devs else None
                 for tag in tags}
        text = next((s["text"] for s in steps.values() if s), None)
        rows.append({"n": n, "text": text if text is not None else _quotes((csteps.get(n) or {}).get("text") or ""),
                     "cells": {tag: s["outcome"] if s else None for tag, s in steps.items()}})
    return rows


def _one_card(ctx, card, tags, task, open_case):
    """A card of the one-test answer (API.md 2.4): titled by its case; each device with its word, run time, run count
    and newest record; what it checks; one line of Hover's why; the step or part table; whether the row opens."""
    card["title"] = card["case_title"] or card["case"]
    for d, dev in zip(card["devices"], card["_devs"]):
        e = dev["e"]
        att = _attempt(e["erec"]) or _attempt(e["rec"])
        started, ended = (att.get(k) if isinstance(att.get(k), str) else None for k in ("started", "ended"))
        d.update(word=WORDS[d["verdict"]], duration_s=_secs(started, ended),
                 runs=sum(1 for x in card["_entries"] if x["tag"] == d["tag"]), started=started, ended=ended,
                 run=e["eff"])
    why = None
    if card["verdict"] not in ("passed", "not_tested"):
        src = (card["explain"] or {}).get("why") or card["auto"]["why"]
        if isinstance(src, str):
            why = WHY_LEAD_RE.sub("", src, count=1).strip() or None
    parts = _parts(ctx.case_rec(card["case"], card["_sha"]))
    # each ticket says whether File ticket can send it, else why not (2026-11-05, wingmate-debug FINDINGS #3): the page
    # offers File ticket only where the server would file it
    for t in card["tickets"]:
        t["fileable"], t["fileable_why"] = _file_check(ctx, t)
    # the row opens where something went wrong, where a ticket waits for File ticket (the next press may be there),
    # and for the ?case= case
    card.update(what_it_checks=_what_it_checks(ctx, task, card["case"], card["auto"]["tested"]), why_line=why,
                rows_from="parts" if parts else "checks", rows=_rows(ctx, card, tags, parts),
                open=any(d["verdict"] in REPORT_VERDICTS for d in card["devices"])
                or any(t["fileable"] for t in card["tickets"]) or card["case"] == open_case)
    card.pop("steps", None)  # every engine step stays behind 'Run records ›' (GET /api/results); the page reads rows
    return card


def _also_running(ctx, S, s):
    """The other ongoing tests, newest Run press first: [{task, title, started}]."""
    others = sorted((o for o in S.values() if o["ongoing"] and o is not s), key=_run_press, reverse=True)
    return [{"task": o["id"], "title": test_title(ctx, o["task"], TITLE_LIST),
             "started": o["start"].get("created") or o["task"].get("created")} for o in others]


def _header(ctx, S, s):
    """The one-test answer's "test" (API.md 2.3)."""
    task, tid, start = s["task"], s["id"], s["start"]
    result = s["check"].get("result") if isinstance(s["check"].get("result"), dict) else {}
    note = _last_note(start) if start and not s["ongoing"] else None
    open_write = bool(_open_work(ctx, tid, ("write",)))
    if s["ongoing"]:
        why = MARK_WHY["ongoing"]
    elif s["group"] == "done":
        why = MARK_WHY["done"]
    elif s["comment"]["state"] == "posting":
        why = MARK_WHY["posting"]
    elif _open_work(ctx, tid, WORK_TYPES):
        why = MARK_WHY["work"]
    elif task.get("state") not in CLOSE_STATES:
        why = MARK_WHY["state"].format(state=task.get("state") or "without a state")
    else:
        why = None
    return {
        "task": tid, "state": task.get("state"), "group": s["group"], "title": test_title(ctx, task, TITLE_HEADER),
        "clickup": _clickup_named(ctx, task), "devices": s["devices"],
        "not_tested_devices": _not_tested(ctx, task, set(s["devices"])),
        "build": _build_label(s["runs"][0]["build"]) if s["runs"] else None, "native": _native(s["runs"]),
        "run_at": task.get("created"), "results_at": s["results_at"], "stopped_early": s["stopped_early"],
        "result": None if s["ongoing"] else _result(s["case_v"], s["dv"]),
        "done_note": _done_note(s, start) if note else None,
        "asked": {"guidance": _first_text(task.get("guidance"), start.get("guidance")),
                  "plan_summary": _first_text(task.get("plan_summary"), start.get("plan_summary")),
                  "not_covered": _strs(result.get("not_covered"))},
        "comment": s["comment"], "outputs": _outputs(ctx, task), "plan_again": s["plan_again"], "next": s["next"],
        "can_mark_done": why is None, "mark_done_why": why, "done_how": s["done_how"],
        # a write request of the test is open (2026-11-05, wingmate-debug FINDINGS #31): Add result is off meanwhile,
        # as writes.request_comment refuses a second one
        "open_write": open_write,
        "waiting": sum(1 for o in S.values() if o["group"] == "tested" and o is not s),
        "also_running": _also_running(ctx, S, s)}


def _done_note(s, start):
    """Hover's done note (the start's last note, quoted and cut at NOTE_MAX). When a run of the test was corrected
    after that note (a <run>c1 written later, the usual order), the note says so (2026-11-05, wingmate-debug FINDINGS
    #23): ' (written before the correction of <date>)', the newest such correction's Zurich day."""
    notes = [n for n in start.get("notes") or [] if isinstance(n, dict) and isinstance(n.get("text"), str)]
    at = _ts(notes[-1].get("at")) if notes else 0.0
    later = [e["erec"].get("created") for e in s["runs"] if at and e["corrected"]
             and data_mod.parse_time(e["erec"].get("created")) and _ts(e["erec"].get("created")) > at]
    text = _quotes(notes[-1]["text"]) if notes else ""
    if not later:
        return _cut(text, NOTE_MAX)
    day = data_mod.parse_time(max(later, key=_ts)).astimezone(ZURICH)
    suffix = NOTE_BEFORE.format(date=f"{day.day} {MONTHS[day.month - 1]}")
    return _cut(text, NOTE_MAX - len(suffix)) + suffix


def _test_answer(ctx, S, s, open_case):
    """{test, cards, running} of one test. Its cards (none while it runs: verdicts show when the test ends) follow the
    task's case order; each is built from this test's runs only, with no merge across tests."""
    task, tid = s["task"], s["id"]
    cards = []
    if not s["ongoing"]:
        for c in s["order"]:
            card = _card(ctx, (tid, c), s["by_case"][c])
            card["tickets"] = s["tickets"][c]
            card["report"] = report_field(card, ctx)
            card["comment"] = s["comment"]
            cards.append(_public(_one_card(ctx, card, s["devices"], task, open_case)))
    return {"test": _header(ctx, S, s), "cards": cards,
            "running": _running_row(ctx, tid, task, full=True) if s["ongoing"] else None}


def _chat_runs(ctx, case):
    """X's runs without a task (chat runs), newest start first."""
    return [e for e in ctx.entries if not e["task"] and e["case"] == case]


def _chat_test(ctx, S, case):
    """?case=X answered by X's chat runs (API.md 2.6): the card group (campaign, case version) of X's newest chat run,
    as a chat run; NotFound when X has no run without a task."""
    groups = collections.OrderedDict()
    for e in _chat_runs(ctx, case):
        groups.setdefault(("tests/" + e["campaign"], e["case"], e["sha"]), []).append(e)
    if not groups:
        raise NotFound(f"no run of {case}")
    # the group of the newest run (2026-11-05: ?case=X answers X's newest run), not the newest decided card
    key, runs = max(groups.items(), key=lambda kv: (max(_ts(e["started"]) for e in kv[1]), "|".join(map(str, kv[0]))))
    card = _card(ctx, key, runs)
    card["tickets"] = _tickets_of(ctx.by_run, card["_entries"])
    card["report"] = report_field(card, ctx)
    card["comment"] = comment_state(ctx, None)
    tags = [d["tag"] for d in card["devices"] if isinstance(d["tag"], str)]
    _one_card(ctx, card, tags, None, case)  # the chat card opens: it is the ?case= card
    starts = [e["started"] for e in card["_entries"] if data_mod.parse_time(e["started"])]
    filing = any(_fileable(ctx, t) for t in card["tickets"])
    test = {"task": None, "state": None, "group": None, "title": _cut(card["title"], TITLE_HEADER), "clickup": None,
            "devices": tags, "not_tested_devices": card["not_tested_devices"], "build": card["build"],
            "native": card["native"], "run_at": min(starts, key=_ts) if starts else None,
            "results_at": card["when"], "stopped_early": False,
            "result": _result({case: card["verdict"]}, {(case, d["tag"]): d["verdict"] for d in card["devices"]}),
            "done_note": None, "asked": None, "comment": None, "outputs": None, "plan_again": None,
            "next": _press("file_ticket", case) if filing else None, "can_mark_done": False,
            "mark_done_why": MARK_WHY["chat"], "done_how": None, "open_write": False,
            "waiting": sum(1 for o in S.values() if o["group"] == "tested"),
            "also_running": _also_running(ctx, S, None)}
    return {"test": test, "cards": [_public(card)], "running": None}


def _one_test(ctx, task, case, posting):
    """GET /api/results/cards?task=…|current[&case=…] (API.md 2.1): task "current" is the newest test by Run press
    (none: test None); a task id that is no test, and a case no test or chat run ran, raise NotFound; a case alone
    answers X's newest run (2026-11-05, wingmate-debug FINDINGS #1): the newest test by Run press that ran X, unless X's
    newest chat run started after that test's newest run of X, then that chat run (_chat_test), so a failure never
    hides behind an older pass. The case's card opens."""
    S = _summaries(ctx, posting)
    if task == "current":
        s = max(S.values(), key=_run_press, default=None)
        if s is None:
            return {"test": None, "cards": [], "running": None}
    elif task is not None:
        s = S.get(task)
        if s is None:
            raise NotFound(f"no test {task}: it has no runs and is not running")
    else:
        s = max((x for x in S.values() if case in x["by_case"]), key=_run_press, default=None)
        chat = max((_ts(e["started"]) for e in _chat_runs(ctx, case)), default=None)
        if s is None or (chat is not None and chat > max(_ts(e["started"]) for e in s["by_case"][case])):
            return _chat_test(ctx, S, case)
    return _test_answer(ctx, S, s, case)


def _history_row(ctx, s):
    """One row of History's 'Your tests' (API.md section 4)."""
    task, ongoing = s["task"], s["ongoing"]
    v = None if ongoing else s["verdict"]
    return {"task_id": s["id"], "title": test_title(ctx, task, TITLE_LIST), "clickup": _clickup_named(ctx, task),
            "devices": s["devices"], "created": task.get("created"), "last_change": s["last_change"], "verdict": v,
            "verdict_label": LABELS[v] if v else None, "results": None if ongoing else _results_words(s["case_v"]),
            "next": s["next"], "stopped_early": s["stopped_early"], "unreported": s["unreported"],
            "done_how": s["done_how"], "running": _running_row(ctx, s["id"], task, full=True) if ongoing else None}


def tests(data, posting=()):
    """GET /api/results/tests, History's 'Your tests' (API.md section 4): {ongoing, tested, done: {count, rows}, shown,
    rows_max}; rows at most ROWS_MAX per group. Ongoing rows newest Run press first, Tested and Done rows newest last
    change first (ties: task id, newest first). tested.more_unreported: the Tested tests after the first SHOWN with a
    failure no filed ticket covers. posting: as result_cards."""
    ctx = _Ctx(data)
    S = _summaries(ctx, posting)
    groups = {g: [s for s in S.values() if s["group"] == g] for g in ("ongoing", "tested", "done")}
    groups["ongoing"].sort(key=_run_press, reverse=True)
    for g in ("tested", "done"):
        groups[g].sort(key=lambda s: (_ts(s["last_change"]), s["id"]), reverse=True)
    out = {g: {"count": len(ss), "rows": [_history_row(ctx, s) for s in ss[:ROWS_MAX]]} for g, ss in groups.items()}
    out["tested"]["more_unreported"] = sum(1 for s in groups["tested"][SHOWN:] if s["unreported"] > 0)
    out.update(shown=SHOWN, rows_max=ROWS_MAX)
    return out


# ---- quality numbers ------------------------------------------------------------------------------------------------

def _bugs(data, start, end):
    reg = data._json(os.path.join(data.qa, "history", "bugs.json"))
    bugs = reg.get("bugs") if isinstance(reg, dict) else None
    found = filed = linked = fixed = 0
    for b in bugs if isinstance(bugs, list) else []:
        d = _as_date(b.get("found")) if isinstance(b, dict) else None
        if not d or not start <= d <= end:
            continue
        status = str(b.get("status") or "").lower()
        found += 1
        filed += status.startswith("filed")
        linked += "linked" in status
        fixed += "fixed" in status
    return {"found": found, "filed": filed, "fixed": fixed, "linked": linked}


def _active_cases(ctx):
    n = 0
    for cid, c in ctx.cases.items():
        latest = c["latest"]
        if latest["file"].startswith("qa/cases/") and not ctx.selftest(cid) and latest["rec"].get("status") == "active":
            n += 1
    return n


def _task_times(ctx, start, end):
    """[{task, case, day, prepare_s, run_s, result_s}] of app tasks in the window whose plan and Start both finished,
    oldest first."""
    out = []
    for tid, t in ctx.tasks.items():
        origin = t.get("origin") if isinstance(t.get("origin"), dict) else {}
        chk, st = ctx.requests.get(origin.get("check")), ctx.requests.get(origin.get("start"))
        if not (isinstance(chk, dict) and isinstance(st, dict)) or chk.get("state") != "done" or st.get("state") != "done":
            continue
        day = _day(chk.get("created"))
        prep, run, res = (_secs(chk.get("created"), chk.get("done_at")), _secs(st.get("created"), st.get("done_at")),
                          _secs(chk.get("created"), st.get("done_at")))
        if day is None or not start <= day <= end or None in (prep, run, res):
            continue
        out.append({"task": tid, "case": ", ".join(c for c in t.get("cases") or [] if isinstance(c, str)),
                    "day": day.isoformat(), "prepare_s": prep, "run_s": run, "result_s": res,
                    "_at": chk.get("created")})
    out.sort(key=lambda x: (x["_at"], x["task"]))
    for x in out:
        del x["_at"]
    return out


def _median(xs):
    return int(round(statistics.median(xs))) if xs else None


def quality_stats(data, days=30, today=None):
    days = max(1, min(DAYS_MAX, int(days)))
    end = _as_date(today) or datetime.datetime.now(ZURICH).date()
    start = end - datetime.timedelta(days=days - 1)
    ctx = _Ctx(data)
    live = [e for e in ctx.entries if e["mode"] == "live" and e["verdict"] != "NOT_TESTED" and e["date"]
            and start <= e["date"] <= end]
    count = collections.Counter(e["verdict"] for e in live)
    passed, failed, partly = count["PASS"], count["FAIL"], count["PARTIAL"]
    fails = [e for e in live if e["verdict"] == "FAIL"]
    confirmed = sum(1 for e in fails if ctx.confirmed.get(e["id"]))
    devices = collections.Counter(e["tag"] for e in live if e["verdict"] in APP_VERDICTS)
    decided = sum(count[v] for v in DECIDED)
    raw_fails = [e for e in live if e["raw"] == "FAIL"]
    corrected = sum(1 for e in raw_fails if e["corrected"] and e["verdict"] == "ERROR")
    causes = collections.Counter(cause(e) for e in live if e["verdict"] == "ERROR")
    from_app = sum(1 for e in live if e["task"])
    times = _task_times(ctx, start, end)

    window = [start + datetime.timedelta(days=i) for i in range(days)]
    per_day = {d: collections.Counter() for d in window}
    for e in live:
        per_day[e["date"]][e["verdict"]] += 1
    weeks = collections.OrderedDict()
    for d in window:
        y, w, _ = d.isocalendar()
        wk = weeks.setdefault(f"{y}-W{w:02d}", {"from": d.isoformat(), "decided": 0, "runs": 0})
        c = per_day[d]
        wk["decided"] += sum(c[v] for v in DECIDED)
        wk["runs"] += sum(c[v] for v in VERDICTS)
    per_device = collections.defaultdict(collections.Counter)
    per_area = collections.defaultdict(collections.Counter)
    for e in live:
        per_device[e["tag"]][e["verdict"]] += 1
        area = ctx.case_rec(e["case"]).get("area") or str(e["case"]).split("-")[0]
        per_area[area][e["verdict"]] += 1
    tags = list(TAG_ORDER) + sorted((t for t in per_device if t not in TAG_ORDER), key=str)
    backtest = sum(1 for it in ctx.idx["runs"].values() if it["rec"].get("mode") == "backtest")

    return {
        "window": {"days": days, "from": start.isoformat(), "to": end.isoformat()},
        "app": {
            "tests": {"runs": len(live), "cases": len({e["case"] for e in live}), "days": len({e["date"] for e in live})},
            "pass_rate": {"value": _pct(passed, passed + failed + partly), "passed": passed, "failed": failed,
                          "partly": partly, "basis": "runs with a result about the app: passed, failed or partly passed"},
            "fails": {"confirmed": confirmed, "suspected": len(fails) - confirmed},
            "bugs": _bugs(data, start, end),
            "coverage": {"devices": {t: devices.get(t, 0) for t in tags},
                         "cases_passed": len({e["case"] for e in live if e["verdict"] == "PASS"}),
                         "cases_active": _active_cases(ctx)},
        },
        "engine": {
            "decided_rate": {"value": _pct(decided, len(live)), "decided": decided, "runs": len(live), "target": 90},
            "false_fails": {"corrected": corrected, "fails_before_correction": len(raw_fails),
                            "value": _pct(corrected, len(raw_fails))},
            "not_decided_causes": [{"cause": c, "n": causes.get(c, 0)} for c in CAUSES],
            "hands_off": {"runs_from_app": from_app, "runs": len(live), "value": _pct(from_app, len(live))},
            "times": {"prepare_median_s": _median([t["prepare_s"] for t in times]),
                      "run_median_s": _median([t["run_s"] for t in times]),
                      "result_median_s": _median([t["result_s"] for t in times]), "samples": len(times)},
        },
        "series": {
            "per_day": [_counter_row(per_day[d], day=d.isoformat()) for d in window],
            "decided_per_week": [{"week": k, "from": w["from"], "decided": w["decided"], "runs": w["runs"],
                                  "rate": _pct(w["decided"], w["runs"])} for k, w in weeks.items()],
            "per_device": [_counter_row(per_device.get(t, {}), tag=t) for t in tags],
            "per_area": [_counter_row(per_area[a], area=a)
                         for a in sorted(per_area, key=lambda a: (-sum(per_area[a].values()), a))],
            "times": [{k: t[k] for k in ("task", "case", "day", "prepare_s", "run_s", "result_s")}
                      for t in times[-TIMES_MAX:]],
        },
        "backtest": {"records": backtest, "note": BACKTEST_NOTE},
        "definitions": dict(DEFINITIONS),
    }


# ---- CLI ------------------------------------------------------------------------------------------------------------

def main(argv):
    ap = argparse.ArgumentParser(prog="views.py", description="Result cards and quality numbers, as the app serves them.")
    ap.add_argument("--root", default=DEFAULT_ROOT, help="workspace folder holding qa/ and tests/")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("cards")
    p = sub.add_parser("stats")
    p.add_argument("--days", type=int, default=30)
    a = ap.parse_args(argv)
    if a.cmd == "stats" and not 1 <= a.days <= DAYS_MAX:
        print(f"views.py: --days must be 1 to {DAYS_MAX}", file=sys.stderr)
        return 2
    d = data_mod.Data(os.path.abspath(a.root))
    obj = result_cards(d) if a.cmd == "cards" else quality_stats(d, days=a.days)
    print(json.dumps(redact.redact_obj(obj), indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
