"""QA worker requests and heartbeat (qa/app/DESIGN.md section 8.3).

The QA app asks; the QA worker answers. The worker is a Claude session started by the scheduled task "QA worker"
(skill qa/plugin/wingtra-qa/skills/qa-worker/SKILL.md). A request is mutable workflow state, not a hashed record:
qa/runner/state/worker/W-<yyyymmdd>-<nnn>.json. The heartbeat is qa/runner/state/worker/heartbeat.json. Both are
written only through writes.Writer (kind "worker").

Request types:
  check   the device check of one ClickUp test's ready cases (from today's pre-test notes) on some targets; from the
          Run form ("prepare": true) it carries the person's "guidance" and its answer must hold a "summary";
          from New test ("intake": true) the ClickUp task, the description and the devices, and its answer also
          holds "plan_cases" (the hands-off case versions it will run), "not_covered" and "needs" (2026-11-04: what
          the worker needs from the person, at most 8 questions answered with a choice, a file or a text, _needs),
          and an optional "title" (2026-11-05, PLAN_TITLE_MAX); a case written or rewritten for the plan is checked
          then (_check_written: case_schema.validate_case and the quotes of its parts), and the ClickUp lines no part
          quotes are added to not_covered (uncovered_task_lines, 2026-11-05).
          Since 2026-11-10 the request also carries "clickup_description", the task's ClickUp text as saved from its
          link (clickup_description; "" when there is none), so the worker plans from the task's own words, its test
          setup included; and every case of the plan that opens a page (a step taps testid:btn-sites), written for
          the plan or reused, begins with the start block of qa/playbooks/README.md and leaves a planner in its
          clean-up only through that README's clean-up block (start_block_problem; checked when the worker answers,
          never at Run).
          The person's answers on the Plan screen (serve.py POST /api/worker/answer) make a follow-up check of the
          same test, devices, description and files with "follows" (the answered check's id) and "answers"
          ([{need, choice | text | file}], validate_answers): the worker reads the old plan (show <follows>),
          applies the answers and answers with a new plan, which may hold new needs. Run waits while a blocking
          need is open (blocking_needs)
  start   the user pressed Start after a check; the app created the qa task the worker runs (qa-run app mode). When
          it ends failed (fail, expire) while its task is still running, the task moves on: results_ready with runs,
          else queued (writes.Writer.settle_started_task, 2026-11-05)
  write   the user greenlit a task in the app and picked the outputs (summary, comment)
  file    the user pressed File ticket on a Results card and approved it with Touch ID (approvals.py, 2026-11-03):
          {ticket_dir, target, approval_id, task, run}. The worker checks the device is online (act.py <tag> ready,
          its "offline" field); offline, it fails the request with OFFLINE_NOTE; online, it files the draft with the
          sak-submit skill (every act.py tap prefixed QA_TICKET=<ticket_dir>), writes submitted.json and answers done
  draft   the user pressed Report issue on a Results card (2026-11-03): {task, case, run}, the card's newest failed run
          without a ticket draft. The worker drafts the Swiss Army Knife ticket with the bug-draft skill and answers
          done once tests/<campaign>/tickets/<n>/ holds title.txt, description.txt and draft.json whose run_record
          names that run (draft_dir_for_run); filing it is a later "file" request
States: new -> claimed -> done | failed; new -> cancelled (the user, in the app).

python3 -I qa/app/worker.py wait [--until HH:MM] [--link claude://...]   heartbeat every 10 s; prints the oldest new
                            request and exits 0, or {"stop": ...} at the end time; exit 5 when another worker waits
python3 -I qa/app/worker.py claim <W-id> [--link claude://...]
python3 -I qa/app/worker.py note <W-id> <text>
python3 -I qa/app/worker.py answer-check <W-id> < result.json      {targets: [...], note, summary[, plan_cases,
                                                                    not_covered, needs, title]}
python3 -I qa/app/worker.py done <W-id> [--note T]       a file request: only after <ticket_dir>/submitted.json exists;
                                                         a draft request: only after its draft folder exists
python3 -I qa/app/worker.py fail <W-id> --note T
python3 -I qa/app/worker.py show <W-id> | list

Prints JSON. A refused write prints {"error", "status"} and exits 1.
"""
import argparse
import datetime
import fcntl
import json
import os
import re
import shlex
import sys
import time
import unicodedata

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)
sys.path.insert(0, os.path.join(os.path.dirname(APP_DIR), "runner"))

import records  # noqa: E402
import redact  # noqa: E402

DEFAULT_ROOT = os.path.dirname(os.path.dirname(APP_DIR))
WORKER_REL = "qa/runner/state/worker"
HEARTBEAT_REL = f"{WORKER_REL}/heartbeat.json"
REQ_ID_RE = re.compile(r"^W-(\d{8})-(\d{3})\Z")
LINK_RE = re.compile(r"^claude://claude\.ai/epitaxy/local_[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
TYPES = ("check", "start", "write", "file", "draft")
STATES = ("new", "claimed", "done", "failed", "cancelled")
OPEN = ("new", "claimed")
OUTPUTS = ("summary", "comment")
TAG_ORDER = ("TA5", "ET65", "W044", "TA3")

BEAT_S = 10        # the waiter rewrites the heartbeat this often
READY_S = 30       # a heartbeat older than this is not a waiting worker
WAKING_S = 120     # how long "waking" (request printed, not yet claimed) counts as busy
POLL_S = 2
CHECK_FRESH_S = 30 * 60       # a device check older than this cannot start a run
COPIED_FRESH_S = 20 * 60      # a follow-up plan copies device rows (devices_copied) at most this old (qa-worker 1b)
STALE_NEW_S = {"check": 30 * 60, "start": 2 * 3600, "file": 30 * 60}  # file: the approval's life (approvals.VALID_S)
STUCK_S = 20 * 60             # a claim with no note and no run progress this long: the worker may have stopped
IDLE_S = 120                  # after a request ends, "idle" until the session waits again
PLAN_HOURS_MAX = 12           # an end time further ahead than this (after midnight) is not today's end
NOTE_MAX, NOTES_MAX = 300, 200
SUMMARY_MAX, SUMMARY_LINES = 1500, 30  # the worker's plain summary of what a Prepare request will run
# a New test plan's "not_covered" (2026-11-03): the parts of the person's description the plan leaves out, in their
# words; the Plan screen lists them above Run, which then says "Run anyway"
NOT_COVERED_MAX, NOT_COVERED_TEXT = 10, 120
# a longer line is cut at NOT_COVERED_TEXT with '…' instead of refused (2026-11-05, wingmate-debug FINDINGS #2), so a
# task line can be listed in the task's own words; a line longer than this is no task line and still refused
NOT_COVERED_IN = 1000
# a New test plan's "needs" (2026-11-04): what the worker needs from the person to plan the rest, instead of "Nothing to
# run" or a not_covered line an answer could fix; the Plan screen shows them in a "Needs your input" card and the
# person's answers make a follow-up check (serve.py POST /api/worker/answer)
NEEDS_MAX, NEED_TEXT, NEED_OPTIONS, OPTION_LABEL, ANSWER_TEXT = 8, 300, 4, 40, 1000
NEED_KINDS = ("choice", "file", "text")
# a New test plan's "title" (2026-11-05, SPEC "TITLE RULE"): one line from the person's description that names the test
# in the app (the Plan screen shows it, so Run approves it); writes._start_intake copies it to the start request and
# the task
PLAN_TITLE_MAX = 80
QUOTE_SHOWN = 120  # characters of a refused quote the refusal repeats
NEED_ID_RE = re.compile(rf"^N[1-{NEEDS_MAX}]\Z")
OPTION_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,19}\Z")
EVENING_WAIT_S = 3 * 3600     # a worker started after its end time waits this long
INTAKE_GATES = ("write_list", "cloud_write")  # the only gates a New test case may carry (autonomy's standing writes)
# The only controls a New test case may write through (2026-11-02, fail closed): creating or processing QA_ data in the
# QA engine site, and the standing Next/Continue; since 2026-11-03 (the person's decision for the offline-sites
# campaign) also the "Enable offline use" toggle, saving a new site the case names QA_ and saving that site's details
# again (SITE_CREATE_TEXT, SITE_UPDATE_TEXT), and saving a flight the case created in that site (own_flight_save).
# A write that act.py lists (G3) or the case gates is refused unless it is one of those. A Confirm tap is refused
# whatever the gates say, listed or not (2026-11-05, wingmate-debug FINDINGS #5): the existing-map Confirm adds or
# removes the open flight's terrain datasets in the cloud (ImportFromExistingDataset.tsx:91-105) and the Edit flight
# Confirm moves the flight to another site (CONFIRM_TEXT, CONFIRM_TESTID). Delete, archive, share, invite, add drone,
# join, send, submit and every other listed or gated write are refused before Run; they need the person in a chat run.
# The guard reads the selectors and the gates: a write act.py does not list and the case does not gate stays a read.
NEW_TEST_WRITE_TEXT = {"import", "create", "process", "reprocess", "next", "continue",
                       # site_list.offline_use.title, WingtraCloud 82e23ff4a SiteOfflineUseToggle.tsx:13-19: the toggle
                       # in site creation and Site details (SiteDetails.tsx:176) and in the site card's ... menu
                       # (SiteCard.tsx:62-66, which starts the download at once: SiteCard.tsx:153-157)
                       "enable offline use"}
# the "file" request (2026-11-03): one Swiss Army Knife ticket draft, approved by the person with Touch ID
FILE_FIELDS = ("type", "ticket_dir", "target", "approval_id", "task", "run")
FILE_TARGETS = ("TA5", "ET65", "W044")
TICKET_DIR_RE = re.compile(r"^tests/\d{4}-\d{2}-\d{2}-[a-z0-9-]+/tickets/\d+\Z", re.A)  # ASCII digits, as the gate
APPROVAL_ID_RE = re.compile(r"^A-\d{14}-[0-9a-f]{6}\Z", re.A)
TASK_RE = re.compile(r"^T-\d{8}-\d{3}\Z")
RUN_RE = re.compile(r"^R-[A-Za-z0-9]{1,16}(?:-[A-Za-z0-9]{1,16}){1,8}\Z")
# the "draft" request (2026-11-03): Report issue on a Results card, one ticket draft for one failed run of the card
DRAFT_FIELDS = ("type", "task", "case", "run")
CASE_RE = re.compile(r"^[A-Z][A-Z0-9]{1,11}-\d{3,6}\Z", re.A)
DRAFT_FILES = ("title.txt", "description.txt", "draft.json")
DRAFT_JSON_MAX = 256 * 1024
OFFLINE_NOTE = "Connect {tag} to the internet, then press File ticket again."
OFFLINE_NOTE_RE = re.compile(r"^Connect [A-Za-z0-9]{1,16} to the internet, then press File ticket again\.")
EXPIRED_NOTE = "The approval expired before Hover took it (30 minutes). Press File ticket again."
# a check or start no Hover session took in time (expire); a check gets its own words (2026-11-05, wingmate-debug
# FINDINGS #27): its devices were never checked, and the screen offers Prepare again (a New test plan) or Check again
CHECK_EXPIRED_NOTE = {
    True: "Hover did not take this plan within 30 minutes (it may be off). Press Prepare again once Hover runs.",
    False: "Hover did not take this device check within 30 minutes (it may be off). Check again once Hover runs."}
START_EXPIRED_NOTE = "Too old to start now (devices may have changed). Start again in the app."
NEW_TEST_WRITE_TESTID = {"import-flight-dialog-submit-button", "btn-create-duplicate-flight", "magic-planning-entry-point",
                         "processing-overview-modal-process", "btn-start-processing-modal"}
# Saving a site, WingtraCloud 82e23ff4a SiteCreateSidebar.tsx:244-256 (one button, testid btn-save-site-<loading>,
# labelled by whether the route has a site id, :255): "Save new site" creates a site (useSiteSaving.ts:106), "Save site"
# on Site details updates the open site, its name, area, base locations and checkpoints (useSiteSaving.ts:62-104); with
# the toggle on, either then starts the offline download (useSiteSaving.ts:113-116). From New test, by its label only
# (the test id is the same for both, so it cannot tell a new site from a rewrite of an existing one):
# - Save new site: for a site the case names QA_ (engine.json test_data.name_prefix), so it stays recognisable in the CT
#   account: the last step before the save that types into the site name field (input-site-name,
#   SiteDetails.tsx:140-146) types one --text starting with QA_;
# - Save site (review 2026-11-03): only on the site the case created itself: an allowed Save new site comes earlier and
#   no step types anything in between, so the save never renames a site or rewrites the QA engine site or another one.
# Both are listed in the case's cloud_writes.
SITE_CREATE_TEXT = "save new site"
SITE_UPDATE_TEXT = "save site"
SITE_SAVE_TESTID = {"btn-save-site-false", "btn-save-site-true"}
SITE_NAME_SEL = "testid:input-site-name"
QA_NAME_PREFIX = "QA_"
# Saving a flight (2026-11-03, the person's OK for the offline-sites campaign: "save the flight and make sure it's
# available offline"): run.FLIGHT_SAVE_TEXT (act.py's labels: Save, Save & exit, Save as draft), which all save the flight
# open in the planner (WingtraCloud 82e23ff4a useFlightPlanMenu.ts:159-196; offline into the tablet's own database,
# useUpdateFlight.ts:22-35, sent on the next sync, useOfflineFlightSync.ts:15-19). From New test by the text: label only
# (act.py lets them through only as run.py's standing step), for a flight the case created in the QA_ site it created
# itself (own_flight_save), gated write_list and cloud_write and listed in cloud_writes. By test id they are refused.
FLIGHT_CREATE_TEXT = "create"  # the New Flight form's Create (general.create, NewFlightForm.tsx:230-232, no test id)
FLIGHT_SAVE_TESTID = {"btn-save-and-exit", "btn-save-with-missing-components"}
# What can put the flight in another site before its save: the New Flight form's site field (NewFlightForm.tsx:176-184)
# and the Confirm of the planner's Edit flight dialog (EditFlightDetailsModal.tsx:45-60, the planner's only Confirm),
# whose site the save then moves the flight to (useFlightPlanMenu.ts:184-195). That Confirm has no test id of its own:
# the shared Button then renders data-testid "none" and "btn-wrapper-none" (Button.tsx:65, :246-248; review 2026-11-03)
FLIGHT_SITE_TESTID = {"new-flight-site-input", "none"}
FLIGHT_SITE_TEXT = {"confirm"}
# A Confirm tap from New test (2026-11-05): by its label (text:, aria:, a sys-tap) or by the shared Button's test id of
# the Edit flight Confirm ("none", "btn-wrapper-none"); refused whatever the case's gates say (see above)
CONFIRM_TEXT = {"confirm"}
CONFIRM_TESTID = {"none"}
CONFIRM_VERBS = ("tap", "sys-tap")
# Page JS that may act (review 2026-11-03). A step's read_js, when_js, read helper and js: readbacks (run.step_js) and a
# case's differential.api_js run in the app's page under the runner, where no gate reads them (the gate's JS_ACTION
# covers only act.py lines in a shell): a .click() there taps Save, Export flight or the Edit flight Confirm unseen, and
# a location write opens another flight under the own site's route. From New test page JS only reads: refused are the
# gate's acting names, DOM events, a component's handler (onClick...), save/update/mutate calls, navigation, storage
# writes, code built at run time or reached through a computed name, native plugins other than Filesystem and
# CapacitorHttp.get, fetch other than fetch(<url>[, {headers: <name>}])
# (the GET reads of the PLAN cases), and run.JS_WRITE (an HTTP write). Matched on the code with its string literals
# emptied (gate.JS_STRING). Plain assignments, IIFEs, fetch/CapacitorHttp GET reads and Filesystem reads (all in
# qa/cases on 2026-11-03: none of their 907 expressions is refused) stay allowed. Text rules cannot prove JS harmless:
# this stops what a plan would plausibly write, not every obfuscation.
JS_STRING = re.compile(r"'(?:\\.|[^'\\\n])*'|\"(?:\\.|[^\"\\\n])*\"|`(?:\\.|[^`\\])*`")
NEW_TEST_JS_ACTS = re.compile(
    r"\.\s*(?:click|submit|requestSubmit|dispatchEvent|setAttribute\w*|removeAttribute\w*|toggleAttribute|setItem|"
    r"removeItem|execCommand|sendBeacon|postMessage|pushState|replaceState|showModal|nativePromise|nativeCallback|"
    r"toNative)\b"
    r"|\.\s*(?:focus|blur|select|toggle\w*|clear|set|delete|add|remove|append\w*|prepend|insert\w*|send\w*|open|close|"
    r"assign|reload|replace(?:With|Children|Child)|go|back|forward|call|apply|bind|define\w*|write\w*|deleteFile|mkdir|"
    r"rmdir|rename|copy|request|put|post|patch|save\w*|update\w*|upsert\w*|mutate\w*)\s*[(`]"
    # a component's own handler (React props reached through the element, e.g. onClick, onSubmit), called or held;
    # navigator.onLine is a read
    r"|\.\s*on(?!Line\b)[A-Z]\w*|\.\s*on(?:click|submit|change|input|keydown|keyup|pointerdown|pointerup|mousedown|"
    r"mouseup|touchstart|touchend)\b"
    r"|\b(?:eval|Function|setTimeout|setInterval|requestAnimationFrame|requestIdleCallback|queueMicrotask|import|"
    r"XMLHttpRequest|WebSocket|EventSource|Reflect|Proxy|Worker|indexedDB|history|prototype|globalThis)\b"
    r"|\bnew\s+\w*Event\b|\bwith\s*\(|\\u|\]\s*\)*\s*[(`]|\b(?:window|self|document|top|parent|frames)\s*\["
    r"|\b(?:location|localStorage|sessionStorage|cookie)\b[\w$.\s]*(?:[-+*/%&|^]|\*\*|<<|>>>?)?=(?![=>])"
    r"|\blocation\s*\.\s*(?:replace|assign|reload)\b"
    r"|\bPlugins\b(?!\s*(?:\?\s*)?\.\s*(?:Filesystem|CapacitorHttp)\b|\s*&&)|\bCapacitorHttp\b(?!\s*\.\s*get\b)"
    r"|\bfetch\b(?!\s*\(\s*[^,()]*(?:,\s*\{\s*headers\s*:\s*[\w$]+\s*\}\s*)?\))")


def js_acts(js, run):
    """True when this page JS may act (NEW_TEST_JS_ACTS or run.JS_WRITE), see above."""
    return isinstance(js, str) and bool(run.JS_WRITE.search(js) or NEW_TEST_JS_ACTS.search(JS_STRING.sub("''", js)))


def _typed_text(step):
    """The one --text an act.py type step types, or None: no --text or more than one (act.py types the last),
    --text-from or --secret, a helper that cannot be split."""
    try:
        args = shlex.split(step.get("helper") or "")
    except ValueError:
        return None
    texts, other, i = [], False, 0
    while i < len(args):
        name, eq, val = args[i].partition("=")
        if name == "--text":
            if not eq:
                i += 1
                val = args[i] if i < len(args) else None
            texts.append(val)
        elif name in ("--text-from", "--secret"):
            other = True
        i += 1
    return texts[0] if len(texts) == 1 and isinstance(texts[0], str) and not other else None


def _label(sel):
    """The control a selector names: ("text", normalised label) for text: and aria:, ("testid", id), else (kind, None)."""
    kind, _, val = (sel or "").partition(":")
    if kind in ("text", "aria"):
        return "text", " ".join(val.split()).lower()
    if kind == "testid":
        return "testid", val[len("btn-wrapper-"):] if val.startswith("btn-wrapper-") else val
    return kind, None


def _earlier(step, case):
    """The case's steps before this one, by n."""
    n = step.get("n")
    return sorted((s for s in case.get("steps") or [] if isinstance(s, dict) and isinstance(s.get("n"), int)
                   and s["n"] < n), key=lambda s: s["n"])


def _listed(step, case):
    return any(isinstance(w, dict) and w.get("step") == step.get("n") for w in case.get("cloud_writes") or [])


def qa_site_named(step, run, case):
    """True when this Save new site stores a site the case names QA_...: the last earlier step (by n) that types into the
    site name field types one --text starting with QA_ and runs whenever this save runs (_runs_with, review 2026-11-03:
    not skipped by when_js, not replaced by read_js, not on the other platform), and the case's cloud_writes lists this
    step."""
    if not isinstance(case, dict) or not isinstance(step.get("n"), int) or not _listed(step, case):
        return False
    earlier = _earlier(step, case)
    typed = [k for k, s in enumerate(earlier) if run.helper_call(s)[:2] == ("type", SITE_NAME_SEL)]
    if not typed or not _runs_with(earlier[typed[-1]], step):
        return False
    # review 2026-11-03: no type step after the QA_ name that may reach the name field another way: text:Site name finds
    # the field's <label>, whose tap focuses the field (TextField.tsx:114-141), so act.py types over the QA_ name
    if any(not _types_by_testid(s, run) for s in earlier[typed[-1] + 1:]):
        return False
    name = _typed_text(earlier[typed[-1]])
    return isinstance(name, str) and name.startswith(QA_NAME_PREFIX)


def _types_by_testid(s, run):
    """False for a type step whose selector is not a test id of its own (text:, aria:, css:, or the shared Button's
    "none"): it can land in any field."""
    verb, sel, _ = run.helper_call(s)
    kind, val = _label(sel)
    return verb != "type" or (kind == "testid" and val not in ("", "none"))


def own_site_update(step, run, case):
    """True when this Save site updates the site the case created itself: an earlier Save new site that qa_site_named
    allows, no type step of any kind between the two (the open site's name stays as it is), and the case's cloud_writes
    lists this step. The guard reads the case, not the screen: the case must stay on that site (playbook)."""
    if not isinstance(case, dict) or not isinstance(step.get("n"), int) or not _listed(step, case):
        return False
    earlier = _earlier(step, case)
    created = [k for k, s in enumerate(earlier) if run.helper_call(s)[0] == "tap"
               and _label(run.helper_call(s)[1]) == ("text", SITE_CREATE_TEXT) and qa_site_named(s, run, case)]
    return bool(created) and not any(run.helper_call(s)[0] == "type" for s in earlier[created[-1] + 1:])


def own_flight_save(step, run, case):
    """True when this flight save (run.FLIGHT_SAVE_TEXT) saves a flight the case created in the QA_ site it created
    itself: an earlier Save new site that qa_site_named allows (the app then opens that site, useSiteSaving.ts:120-127),
    a tap on text:Create after it (the New Flight form creates the flight in the open site and opens it in the planner,
    NewFlightForm.tsx:59, :79-106), no step from the site save on that touches a site field (FLIGHT_SITE_TESTID,
    FLIGHT_SITE_TEXT, by any verb, sys-tap too), and the case's cloud_writes lists this step. The site save and Create
    count only when they run wherever and whenever the save runs (_runs_with: no when_js, no read_js, the same
    platforms). run.standing_step reads the same rule. It reads the case, not the screen: at run time act.py also taps
    the save only in the planner of a flight in the site the run created (review 2026-11-03, act.own_site_planner)."""
    if not isinstance(case, dict) or not isinstance(step.get("n"), int) or not _listed(step, case):
        return False
    earlier = _earlier(step, case)
    created = [k for k, s in enumerate(earlier) if _runs_with(s, step) and run.helper_call(s)[0] == "tap"
               and _label(run.helper_call(s)[1]) == ("text", SITE_CREATE_TEXT) and qa_site_named(s, run, case)]
    if not created:
        return False
    after = earlier[created[-1] + 1:]
    for s in after:
        verb, sel, _ = run.helper_call(s)
        lab = _label(f"text:{sel or ''}" if verb == "sys-tap" else sel)
        if lab[0] == "testid" and lab[1] in FLIGHT_SITE_TESTID or lab[0] == "text" and lab[1] in FLIGHT_SITE_TEXT:
            return False
    return any(_runs_with(s, step) and run.helper_call(s)[0] == "tap"
               and _label(run.helper_call(s)[1]) == ("text", FLIGHT_CREATE_TEXT) for s in after)


def _runs_with(s, step):
    """True when step s runs whenever step does (review 2026-11-03): its own helper (no read_js), unconditionally (no
    when_js), on every platform step runs on (run.TargetRun.steps)."""
    return not s.get("read_js") and not s.get("when_js") and s.get("on", "both") in ("both", step.get("on", "both"))


def new_test_write_problem(step, run, case=None):
    """Why a New test plan may not run this step's write, or None (a read, or an allowed write). run: the engine's run
    module (helper_call and the act.py G3 control lists). case: the whole case, which a site save needs (its earlier
    steps and cloud_writes); without it a site save is refused."""
    gates = set(step.get("gates") or [])
    verb, sel, _ = run.helper_call(step)
    acting = [js for js in run.step_js(step) if js_acts(js, run)]
    if acting:  # review 2026-11-03: whatever the gates, before the write rules (page JS is no tap the lists check)
        return (f"step {step.get('n')} runs page JS that may act ({acting[0][:80]!r}): a click, submit, event, "
                "navigation, storage or HTTP write, a native plugin, or code built at run time. From New test page JS "
                "only reads; act.py taps and types by selector, which the write lists check")
    check = step.get("assert")
    bad = run.assert_problem(check.get("expr")) if isinstance(check, dict) and check.get("kind") == "scripted" else None
    if bad:  # review 2026-11-03: the runner evaluates it in its own process (run.ASSERT_NODES)
        return (f"step {step.get('n')} asserts with Python that is not a plain check ({bad}): compare v[...] values "
                "with literals, and/or/not, any/all/len/sorted/set; no attributes or lambdas")
    # aria: names a control by its label, and sys-tap taps a label, as act.py and run.step_selectors read them
    kind, val = _label(f"text:{sel or ''}" if verb == "sys-tap" else sel)
    text = val if kind == "text" else None
    tid = val if kind == "testid" else None
    n = step.get("n")
    if verb in CONFIRM_VERBS and (text in CONFIRM_TEXT or tid in CONFIRM_TESTID):
        return (f"step {n} taps {sel or verb}, a Confirm, which writes to the cloud (the existing-map Confirm adds or "
                "removes the flight's terrain, the Edit flight Confirm moves the flight to another site) and never "
                "runs from New test, whatever the gates say; it needs the person in a chat run")
    writes = bool(gates & set(INTAKE_GATES)) or (text in run.G3_TEXT) or (tid in run.G3_TESTID)
    if not writes:
        return None
    if verb == "tap" and tid in SITE_SAVE_TESTID:
        return (f"step {n} saves a site through {sel}, the same test id for a new site and for Site details: tap it by "
                "its label, text:Save new site or text:Save site")
    if verb == "tap" and text == SITE_CREATE_TEXT:
        if qa_site_named(step, run, case):
            return None
        return (f"step {n} saves a site through {sel}, which never runs from New test unless the case names the site "
                f"{QA_NAME_PREFIX}...: type a name starting with {QA_NAME_PREFIX} into {SITE_NAME_SEL} (act.py type "
                f"--text, once) in an earlier step and list step {n} in cloud_writes")
    if verb == "tap" and text == SITE_UPDATE_TEXT:
        if own_site_update(step, run, case):
            return None
        return (f"step {n} saves a site through {sel}, which rewrites the open site: from New test only on the site the "
                "case created itself (an earlier text:Save new site of a QA_ name, with no type step in between), "
                "listed in cloud_writes; never on the QA engine site or another existing site")
    if verb == "tap" and tid in FLIGHT_SAVE_TESTID:
        return (f"step {n} saves a flight through {sel}: from New test tap it by its label (text:Save & exit, "
                "text:Save), only for a flight the case created in its own QA_ site")
    if verb == "tap" and text in run.FLIGHT_SAVE_TEXT:
        if sel.startswith("text:") and {"write_list", "cloud_write"} <= gates and own_flight_save(step, run, case):
            return None
        return (f"step {n} saves a flight through {sel}, which from New test runs only for a flight the case created in "
                "the QA_ site it created itself: an earlier text:Save new site of a QA_ name, then text:Create (the "
                "New Flight form), both without when_js or read_js and on the save's platforms, no site field or Edit "
                "flight Confirm in between; tapped by text:, gates write_list "
                f"and cloud_write, step {n} listed in cloud_writes. Never another flight, the QA engine site or "
                "another existing site")
    if verb == "tap" and (text in NEW_TEST_WRITE_TEXT or tid in NEW_TEST_WRITE_TESTID):
        return None
    return (f"step {step.get('n')} writes through {sel or verb}, which never runs from New test (only import, "
            "create, process, Optimize, Next, Continue, offline use and QA_ site creation)")


# ---- the start block and the clean-up block (2026-11-10, the person's OK) --------------------------------------------
# After ClickUp test 0000fake009 failed (tests/2026-11-10-custom-terrain-offline/diagnosis): on ET65 a case had dropped
# the start block's "Exit without saving" step and stopped at its step 3, behind the dialog of a planner a crashed run
# had left open; on TA5 the person's own unsaved flight was open in the planner later that day, and a start that taps
# "Exit without saving" would have dropped it. No code checked how a case starts. Now, when the worker answers a New
# test plan (_intake_plan; never at Run), every case of the plan, written for it or reused:
#   a) that opens a page (a step taps testid:btn-sites) begins with the 5 steps of START_BLOCK, in order;
#   b) carries the clean-up block's when_js on every always: true step that taps testid:btn-sites (CLEANUP_BLOCK[0])
#      or "Exit without saving" (CLEANUP_BLOCK[1]).
# A case with neither (the UPG cases, which read the app's files on whatever page shows) is not checked.
# The rule in one sentence: Hover leaves a planner only when it can read that the open flight's name starts with QA_
# (OWN_FLIGHT_JS: the planner's name label, or its input while the name is edited; WingtraCloud 8203f39 and 82e23ff,
# apps/src/pages/FlightPlanning/components/RightSidebar/FlightNameEditableLabel.tsx). Start step 2 stops the run (a
# test error, which says nothing about the app) when the planner shows any other flight: a person's flight is never
# left or dropped. That step has not run on a device yet.
# These constants are the one source the app checks against; qa/playbooks/README.md holds the same steps as JSON for
# the worker to copy (tests/test_start_block.py compares the two). A step is a block step by its when_js and its
# helper, compared as the runner splits it, so {target} stays a placeholder and the step text is free; and only when
# the runner would run that helper: no read_js (run.py runs it in the helper's place), no gate (a write_list step is
# handed back, not run) and no "on" that leaves out a platform the case runs on.
OWN_FLIGHT_JS = ("(((document.querySelector('[data-testid=flight-name-editable-label]')||{}).innerText||"
                 "(document.querySelector('[data-testid=flight-name-editable-label-input]')||{}).value||'')"
                 ".trim().startsWith('QA_'))")
IN_PLANNER_JS = "location.pathname.includes('/planning/')"
EXIT_SETUP_JS = "/Do you really want to exit\\?/.test(document.body.innerText)"
EXIT_UNSAVED_JS = "/Exit without saving/.test(document.body.innerText)"
SITES_TAP = ("act.py {target} tap testid:btn-sites --expect \"js:/Exit without saving|Do you really want to exit\\?/"
             ".test(document.body.innerText) || location.pathname === '/sites'\" --timeout 20")
EXIT_UNSAVED_TAP = ("act.py {target} tap \"text:Exit without saving\" --expect \"js:location.pathname === '/sites'\" "
                    "--timeout 20")
EXIT_SETUP_TAP = "act.py {target} tap text:Exit --expect \"js:location.pathname === '/sites'\" --timeout 20"
START_BLOCK = (
    # 1 leave a flight setup's "Do you want to exit?" dialog when one is open
    {"when_js": EXIT_SETUP_JS, "helper": "act.py {target} tap text:Exit --expect \"js:!/Do you really want to exit\\?/"
                                         ".test(document.body.innerText)\" --timeout 20"},
    # 2 the guard: in a planner, stop unless the open flight is Hover's own
    {"when_js": IN_PLANNER_JS, "helper": "act.py {target} wait --expect \"js:" + OWN_FLIGHT_JS + "\" --timeout 5"},
    # 3 Top bar: Sites; 4 Exit without saving, when the planner asks; 5 Exit, when a flight setup asks
    {"when_js": None, "helper": SITES_TAP},
    {"when_js": EXIT_UNSAVED_JS, "helper": EXIT_UNSAVED_TAP},
    {"when_js": EXIT_SETUP_JS, "helper": EXIT_SETUP_TAP})
CLEANUP_BLOCK = (  # every step always: true
    # 1 leave the planner, never one that shows a flight that is not Hover's own
    {"when_js": "!" + IN_PLANNER_JS + " || " + OWN_FLIGHT_JS, "helper": SITES_TAP},
    # 2 Exit without saving when asked, only for Hover's own flight; 3 Exit, when a flight setup asks
    {"when_js": EXIT_UNSAVED_JS + " && " + OWN_FLIGHT_JS, "helper": EXIT_UNSAVED_TAP},
    {"when_js": EXIT_SETUP_JS, "helper": EXIT_SETUP_TAP})
SITES_BUTTON = ("testid", "btn-sites")               # as _label names the control a step taps
EXIT_UNSAVED_LABEL = ("text", "exit without saving")
BLOCKS_FIX = ("Write a new case version that copies the start block and the clean-up block of qa/playbooks/README.md "
              "whole: each step's when_js and helper exactly as written there ({target} stays), no read_js and no "
              "gate on them; only the step text is free. The app checks that version as a case written for this "
              "plan (parts and quotes too), so a case without parts also gets its parts.")


def _argv(helper):
    """A helper as the runner splits it; None when it is no text or cannot be split."""
    try:
        return shlex.split(helper) if isinstance(helper, str) else None
    except ValueError:
        return None


def _when(step):
    """A step's when_js without the spaces around it; None when it has none."""
    w = step.get("when_js")
    return (w.strip() or None) if isinstance(w, str) else None


def _tapped(step, run):
    """The control a step's helper taps, as _label names it (a sys-tap by its label); (None, None) for a step that
    taps nothing or whose helper is no text or cannot be split."""
    if not isinstance(step.get("helper"), str):
        return None, None
    try:
        verb, sel, _ = run.helper_call(step)
    except ValueError:
        return None, None
    if verb == "sys-tap":
        return _label(f"text:{sel or ''}")
    return _label(sel) if verb == "tap" else (None, None)


def _block_step_problem(step, block, targets):
    """Why a step is not this block step as the runner would run it (see above), or None."""
    mine = _when(step)
    if mine != block["when_js"]:
        return ("it has no when_js" if not mine else "its when_js differs" if block["when_js"] else
                "it has a when_js, the block's step has none")
    if _argv(step.get("helper")) != _argv(block["helper"]):
        return "its helper differs"
    if step.get("read_js"):
        return "it has read_js, which runs in the helper's place"
    if step.get("gates"):
        return "it has a gate"
    if step.get("on", "both") not in ("both", targets):
        return "it does not run on every device the case runs on"
    return None


def start_block_problem(cid, case, run):
    """Why a New test plan may not run this case, or None: the start block and the clean-up block (see above). run: the
    engine's run module (helper_call). The refusal names the case and the step and says what to do."""
    steps = case.get("steps") if isinstance(case, dict) else None
    steps = [s for s in steps if isinstance(s, dict)] if isinstance(steps, list) else []
    opens = next((s for s in steps if _tapped(s, run) == SITES_BUTTON), None)
    if opens is not None:
        for k, block in enumerate(START_BLOCK):
            why = _block_step_problem(steps[k], block, case.get("targets")) if k < len(steps) else None
            if why or k >= len(steps):
                what = (f"its step {steps[k].get('n', k + 1)} is not that block's step {k + 1} ({why})" if why else
                        f"it has no step {k + 1}")
                return (f"{cid} opens a page (step {opens.get('n')} taps testid:btn-sites) but does not begin with the "
                        f"{len(START_BLOCK)} steps of the start block: {what}. " + BLOCKS_FIX)
    for s in steps:
        # review 2026-11-11: any truthy "always" is clean-up, as the runner reads it (run.py)
        tapped = _tapped(s, run) if s.get("always") else None
        want = {SITES_BUTTON: CLEANUP_BLOCK[0], EXIT_UNSAVED_LABEL: CLEANUP_BLOCK[1]}.get(tapped)
        if want is not None and _when(s) != want["when_js"]:
            what = "testid:btn-sites" if tapped == SITES_BUTTON else "Exit without saving"
            return (f"{cid} step {s.get('n')} taps {what} in clean-up (always: true) without the when_js that step "
                    "has in the clean-up block, so it could leave a planner that shows a person's flight (a name that "
                    "does not start with QA_). " + BLOCKS_FIX)
    # review 2026-11-11: "Exit without saving" drops the open flight's changes whichever control opened that dialog
    # (Sites, Maps, Flights, the logo, Back). Outside clean-up (checked above) it runs only as the start block's step 4,
    # after the guard, or with the clean-up block's guarded when_js, so only Hover's own QA_ flight is ever dropped.
    whole_start = len(steps) >= len(START_BLOCK) and not any(
        _block_step_problem(steps[k], block, case.get("targets")) for k, block in enumerate(START_BLOCK))
    for i, s in enumerate(steps):
        if s.get("always") or _tapped(s, run) != EXIT_UNSAVED_LABEL:
            continue
        if (i == 3 and whole_start) or _when(s) == CLEANUP_BLOCK[1]["when_js"]:
            continue
        return (f"{cid} step {s.get('n', i + 1)} taps Exit without saving outside the start block's step 4 and outside "
                "clean-up, without the clean-up block's guard (its second step's when_js), so it could drop the "
                "unsaved changes of a person's flight. " + BLOCKS_FIX)
    return None


class WorkerError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


# ---- reads ----------------------------------------------------------------------------------------------------------

def _parse_time(s):
    try:
        return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def age_s(iso, now=None):
    t = _parse_time(iso)
    if t is None:
        return None
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return max(0.0, (now - t).total_seconds())


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            obj = json.load(f)
    except (OSError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def request_rel(rid):
    if not isinstance(rid, str) or not REQ_ID_RE.match(rid):
        raise WorkerError(404, "no such request")
    return f"{WORKER_REL}/{rid}.json"


def load_request(root, rid):
    obj = _read(os.path.join(root, request_rel(rid)))
    return obj if obj and obj.get("kind") == "worker_request" else None


def list_requests(root):
    d = os.path.join(root, WORKER_REL)
    try:
        names = sorted((n for n in os.listdir(d) if REQ_ID_RE.match(n[:-5]) and n.endswith(".json")), reverse=True)
    except OSError:
        return []
    out = []
    for n in names:
        obj = _read(os.path.join(d, n))
        if obj and obj.get("kind") == "worker_request":
            out.append(obj)
    return out


def heartbeat(root):
    obj = _read(os.path.join(root, HEARTBEAT_REL))
    return obj if obj and obj.get("kind") == "worker_heartbeat" else None


def pid_alive(pid):
    if not isinstance(pid, int) or pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _progress(root):
    d = os.path.join(root, "qa", "runner", "state", "progress")
    try:
        names = [n for n in os.listdir(d) if n.endswith(".json")]
    except OSError:
        return []
    return [p for p in (_read(os.path.join(d, n)) for n in names) if p]


def last_activity(req, progress=()):
    """The newest sign of work on a request: its claim, its notes, and run.py progress of its cases and targets."""
    times = [req.get("claimed_at")] + [n.get("at") for n in req.get("notes") or [] if isinstance(n, dict)]
    cases, targets, since = set(req.get("engine_cases") or []), set(req.get("targets") or []), req.get("claimed_at") or ""
    times += [p.get("updated") for p in progress
              if p.get("case") in cases and p.get("target") in targets and str(p.get("started") or "") >= since]
    return max((t for t in times if isinstance(t, str)), default=None)


def is_stuck(req, progress=(), now=None):
    if req.get("state") != "claimed":
        return False
    idle = age_s(last_activity(req, progress), now)
    return idle is None or idle > STUCK_S


def status(root, now=None):
    """{state: ready|busy|off, ...}: ready while a waiter process is alive, busy while a request is being handled."""
    hb = heartbeat(root) or {}
    reqs = list_requests(root)
    claimed = [r for r in reqs if r.get("state") == "claimed"]
    age = age_s(hb.get("at"), now)
    link = hb.get("link") if isinstance(hb.get("link"), str) and LINK_RE.match(hb["link"]) else None
    base = {"heartbeat_at": hb.get("at"), "link": link, "until": hb.get("until"),
            "queued": [r["id"] for r in reversed(reqs) if r.get("state") == "new"]}
    if claimed:
        r = claimed[0]
        prog = _progress(root)
        last = last_activity(r, prog)
        return dict(base, state="stuck" if is_stuck(r, prog, now) else "busy", request=r["id"],
                    since=r.get("claimed_at"), last_activity=last)
    if hb.get("state") == "waking" and age is not None and age <= WAKING_S:
        return dict(base, state="busy", request=hb.get("request"), since=hb.get("at"), last_activity=hb.get("at"))
    if hb.get("state") == "idle" and age is not None and age <= IDLE_S:
        return dict(base, state="busy", request=None, since=hb.get("at"), last_activity=hb.get("at"))
    if hb.get("state") == "ready" and age is not None and age <= READY_S and pid_alive(hb.get("pid")):
        return dict(base, state="ready")
    return dict(base, state="off")


def view(root, now=None):
    """GET /api/worker: the worker's state and the requests of the last two days plus every open one."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    cutoff = (now - datetime.timedelta(days=2)).strftime("%Y%m%d")
    reqs = [r for r in list_requests(root)
            if r.get("state") in OPEN or (REQ_ID_RE.match(r.get("id", "")) and r["id"][2:10] >= cutoff)]
    return {"worker": status(root, now), "requests": reqs[:100], "now": records.now_utc()}


def run_requests(reqs):
    return [r for r in reqs if r.get("type") in ("check", "start")]


def open_for(root, clickup_task_id, types=TYPES):
    return [r for r in list_requests(root) if r.get("state") in OPEN and r.get("type") in types
            and (r.get("clickup") or {}).get("task_id") == clickup_task_id]


def file_requests(root, ticket_dir):
    """The "file" requests of one ticket folder, oldest first."""
    return [r for r in reversed(list_requests(root)) if r.get("type") == "file" and r.get("ticket_dir") == ticket_dir]


def file_expired(req, now=None):
    """A new file request older than the approval's life: no gate lets its approval pass any more."""
    return req.get("type") == "file" and req.get("state") == "new" and \
        (age_s(req.get("created"), now) or 0) > STALE_NEW_S["file"]


def submitted(root, ticket_dir):
    """True when sak-submit wrote <ticket_dir>/submitted.json (a plain file)."""
    try:
        return os.path.isfile(os.path.join(root, ticket_dir, "submitted.json")) and \
            not os.path.islink(os.path.join(root, ticket_dir, "submitted.json"))
    except (TypeError, ValueError):
        return False


def draft_open(root, task, case):
    """The open "draft" requests of one Results card (task and case)."""
    return [r for r in list_requests(root) if r.get("type") == "draft" and r.get("state") in OPEN
            and r.get("task") == task and r.get("case") == case]


# Hover's work on a test's results: a comment (write), a ticket draft, filing a ticket. While one of them is open the
# test is not marked done (views.MARK_WHY "work", and writes.set_task_state refuses the app's close, 2026-11-05)
WORK_TYPES = ("write", "draft", "file")
WORK_OPEN_WHY = "Hover is still working on this test (a comment, a ticket draft or filing a ticket)."


def open_work(reqs, task, types=WORK_TYPES, now=None):
    """The requests of these types for the task that are new or claimed. A new file request older than the approval's
    life is left out (file_expired: its approval expired, so nobody works on it; review 2026-11-05)."""
    return [r for r in reqs if isinstance(r, dict) and r.get("task") == task and r.get("type") in types
            and r.get("state") in OPEN and not file_expired(r, now)]


def _subdirs(path):
    """Non-hidden subfolders of path; a symlinked folder is skipped."""
    try:
        with os.scandir(path) as it:
            return sorted(e.name for e in it if not e.name.startswith(".") and e.is_dir(follow_symlinks=False))
    except OSError:
        return []


def _plain_file(path):
    return os.path.isfile(path) and not os.path.islink(path)


def draft_dir_for_run(root, run):
    """tests/<campaign>/tickets/<n> of a ticket draft whose draft.json run_record names this run's record (or one of its
    corrections, <run>c<k>), with title.txt, description.txt and draft.json as plain files; None when there is none."""
    if not isinstance(run, str) or not RUN_RE.match(run):
        return None
    want = re.compile(rf"^{re.escape(run)}(?:c\d+)?\.json\Z")
    tests = os.path.join(root, "tests")
    for camp in _subdirs(tests):
        for n in _subdirs(os.path.join(tests, camp, "tickets")):
            rel = f"tests/{camp}/tickets/{n}"
            d = os.path.join(root, *rel.split("/"))
            if not TICKET_DIR_RE.match(rel) or not all(_plain_file(os.path.join(d, f)) for f in DRAFT_FILES) or \
                    os.path.getsize(os.path.join(d, "draft.json")) > DRAFT_JSON_MAX:
                continue
            rr = (_read(os.path.join(d, "draft.json")) or {}).get("run_record")
            if isinstance(rr, str) and want.match(os.path.basename(rr)):
                return rel
    return None


# ---- validation -----------------------------------------------------------------------------------------------------

def _text(value, name, hi, required=True):
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > hi or "\x00" in value:
        raise WorkerError(400, f"{name}: text of 1-{hi} characters")
    return redact.redact(" ".join(value.split()))


def _summary(value, required):
    """Multi-line text (line breaks kept, at most SUMMARY_LINES lines), redacted; None when absent and optional."""
    if value is None and not required:
        return None
    v = value.replace("\r\n", "\n").strip() if isinstance(value, str) else ""
    if not v or len(v) > SUMMARY_MAX or v.count("\n") >= SUMMARY_LINES or "\x00" in v:
        raise WorkerError(400, f"summary: text of 1-{SUMMARY_MAX} characters in at most {SUMMARY_LINES} lines")
    return redact.redact(v)


def _bool_or_none(value, name):
    if value is not None and not isinstance(value, bool):
        raise WorkerError(400, f"{name}: true, false or null")
    return value


def validate_check_result(obj, req, root=None):
    """The worker's answer to a check request: one row per requested target. root: the workspace whose case files a
    New test plan (plan_cases) is checked against (default: this one)."""
    if not isinstance(obj, dict) or not isinstance(obj.get("targets"), list):
        raise WorkerError(400, "result: {targets: [...], note}")
    want = list(req.get("targets") or [])
    rows, seen = [], set()
    for t in obj["targets"]:
        if not isinstance(t, dict) or t.get("tag") not in want or t["tag"] in seen:
            raise WorkerError(400, f"targets: one row per requested target ({', '.join(want)})")
        seen.add(t["tag"])
        ok = t.get("ok")
        if not isinstance(ok, bool):
            raise WorkerError(400, f"{t['tag']}.ok: true or false")
        row = {"tag": t["tag"], "ok": ok,
               "connected": _bool_or_none(t.get("connected"), f"{t['tag']}.connected"),
               "account": _bool_or_none(t.get("account"), f"{t['tag']}.account"),
               "app_ok": _bool_or_none(t.get("app_ok"), f"{t['tag']}.app_ok"),
               "app": _text(t.get("app"), f"{t['tag']}.app", 120, required=False),
               "needs": _text(t.get("needs"), f"{t['tag']}.needs", NOTE_MAX, required=False)}
        if not ok and not row["needs"]:
            raise WorkerError(400, f"{t['tag']}.needs: say what the person has to do")
        if ok and False in (row["connected"], row["account"], row["app_ok"]):
            raise WorkerError(400, f"{t['tag']}: ok while a check failed")
        rows.append(row)
    if seen != set(want):
        raise WorkerError(400, f"targets: missing {', '.join(sorted(set(want) - seen))}")
    rows.sort(key=lambda r: TAG_ORDER.index(r["tag"]) if r["tag"] in TAG_ORDER else 99)
    intake = req.get("intake") is True
    if intake and obj.get("complete"):
        raise WorkerError(400, "complete: not used for a test from New test; plan_cases names the cases")
    if not intake and obj.get("not_covered"):
        raise WorkerError(400, "not_covered: only for a test from New test; the summary says what is not tested")
    if not intake and obj.get("needs"):
        raise WorkerError(400, "needs: only for a test from New test")
    if not intake and obj.get("title") is not None:
        raise WorkerError(400, "title: only for a test from New test")
    complete, plan_files = ([], None) if intake else _file_plan(obj, req)
    if intake:
        recs = {}
        plan_cases, plan_files = _intake_plan(obj, req, root or DEFAULT_ROOT, recs)
        not_covered = _not_covered(obj.get("not_covered"))
        # the task lines no part quotes go to not_covered by themselves (2026-11-05, wingmate-debug FINDINGS #2)
        not_covered += uncovered_task_lines(req, root or DEFAULT_ROOT, [recs[pc["id"]] for pc in plan_cases],
                                            not_covered)
        needs = _needs(obj.get("needs"))
        title = _text(obj.get("title"), "title", PLAN_TITLE_MAX, required=False)
    out = {"targets": rows, "note": _text(obj.get("note"), "note", NOTE_MAX, required=False),
           "summary": _summary(obj.get("summary"), required=req.get("prepare") is True or intake),
           "complete": complete, "plan_files": plan_files, "checked_at": records.now_utc()}
    # a follow-up plan may copy the answered plan's device rows instead of checking again (2026-11-04); it says so,
    # and then keeps that plan's check time, so a copy never looks newer than the real check. The copied check may be
    # at most COPIED_FRESH_S old (2026-11-05, FINDINGS #15): an older one would make a plan Run refuses at once
    if obj.get("devices_copied") is True:
        copied_at = _copied_check_time(req, rows, root or DEFAULT_ROOT)
        if copied_at is None:
            raise WorkerError(400, "devices_copied: copy the rows exactly from the plan this one follows")
        age = age_s(copied_at)
        if age is None or age > COPIED_FRESH_S:
            raise WorkerError(400, "devices_copied: the copied device check is older than 20 minutes; check the "
                                   "devices again")
        out["checked_at"], out["devices_copied"] = copied_at, True
    if intake:
        out["plan_cases"], out["not_covered"], out["needs"], out["title"] = plan_cases, not_covered, needs, title
    return out


def _not_covered(value):
    """A New test plan's "not_covered": the parts of the person's description the plan does not test, at most
    NOT_COVERED_MAX lines of plain text (whitespace collapsed, redacted), each once; a line longer than
    NOT_COVERED_TEXT characters is cut there with '…' (2026-11-05: the task's own words, not a paraphrase that fits);
    [] when absent (all of the description is covered)."""
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > NOT_COVERED_MAX:
        raise WorkerError(400, f"not_covered: a list of at most {NOT_COVERED_MAX} short texts")
    out, whole = [], []
    for i, v in enumerate(value):
        line = _text(v, f"not_covered[{i}]", NOT_COVERED_IN)
        if line in whole:
            raise WorkerError(400, f"not_covered[{i}]: the same text more than once")
        whole.append(line)
        cut = _cut_line(line)
        if cut not in out:  # two long lines that start alike: listed once
            out.append(cut)
    return out


def _cut_line(text):
    """text whole when it fits NOT_COVERED_TEXT, else cut on a word with '…' (at most NOT_COVERED_TEXT characters)."""
    if len(text) <= NOT_COVERED_TEXT:
        return text
    cut = text[:NOT_COVERED_TEXT - 1]
    sp = cut.rfind(" ")
    return (cut[:sp] if sp > NOT_COVERED_TEXT // 2 else cut).rstrip(" ,;:-") + "…"


def _needs(value):
    """A New test plan's "needs" (2026-11-04): what the worker needs from the person to test the rest, the most
    important first; [] when absent. At most NEEDS_MAX {id, kind, text, blocking, options[, accept]}: id N1 to N8, each
    once; kind choice, file or text; text 1-NEED_TEXT characters (whitespace collapsed, redacted), what is needed and
    why; blocking true when the plan cannot test that part without the answer; options the answer buttons (_options), 2
    or more for a choice; accept, for a file need only and required there, the extensions it takes (some of
    uploads.EXTENSIONS, each once). Any other key is dropped."""
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > NEEDS_MAX:
        raise WorkerError(400, f"needs: a list of at most {NEEDS_MAX} questions")
    import uploads  # noqa: E402 (late: uploads imports writes, which imports this module)
    out = []
    for i, n in enumerate(value):
        name = f"needs[{i}]"
        if not isinstance(n, dict):
            raise WorkerError(400, f"{name}: {{id, kind, text, blocking, options}}")
        nid = n.get("id")
        if not isinstance(nid, str) or not NEED_ID_RE.match(nid):
            raise WorkerError(400, f"{name}.id: N1 to N{NEEDS_MAX}")
        if any(x["id"] == nid for x in out):
            raise WorkerError(400, f"{name}.id: {nid} more than once")
        kind = n.get("kind")
        if kind not in NEED_KINDS:
            raise WorkerError(400, f"{name}.kind: choice, file or text")
        need = {"id": nid, "kind": kind, "text": _text(n.get("text"), f"{name}.text", NEED_TEXT)}
        if not isinstance(n.get("blocking"), bool):
            raise WorkerError(400, f"{name}.blocking: true or false")
        need["blocking"] = n["blocking"]
        need["options"] = _options(n.get("options"), name, 2 if kind == "choice" else 0)
        accept = n.get("accept")
        if kind == "file":
            if (not isinstance(accept, list) or not accept or any(x not in uploads.EXTENSIONS for x in accept)
                    or len(set(accept)) != len(accept)):
                raise WorkerError(400, f"{name}.accept: some of {', '.join(uploads.EXTENSIONS)}, each once")
            need["accept"] = list(accept)
        elif accept is not None:
            raise WorkerError(400, f"{name}.accept: only for a file")
        out.append(need)
    return out


def _options(value, name, lo):
    """A need's answer buttons: lo to NEED_OPTIONS {id, label} (none when absent); id ^[a-z][a-z0-9_-]{0,19}$ and each
    once in the need, label 1-OPTION_LABEL characters (whitespace collapsed, redacted). Any other key is dropped."""
    value = [] if value is None else value
    if not isinstance(value, list) or not lo <= len(value) <= NEED_OPTIONS:
        raise WorkerError(400, f"{name}.options: {lo}-{NEED_OPTIONS} choices" if lo else
                          f"{name}.options: at most {NEED_OPTIONS} choices")
    out = []
    for k, o in enumerate(value):
        oid = o.get("id") if isinstance(o, dict) else None
        if not isinstance(oid, str) or not OPTION_ID_RE.match(oid):
            raise WorkerError(400, f"{name}.options[{k}].id: 1-20 of a-z, 0-9, _ and -, starting with a letter")
        if any(x["id"] == oid for x in out):
            raise WorkerError(400, f"{name}.options[{k}].id: {oid} more than once")
        out.append({"id": oid, "label": _text(o.get("label"), f"{name}.options[{k}].label", OPTION_LABEL)})
    return out


def validate_answers(value, needs, files):
    """The person's answers to a plan's needs (serve.py POST /api/worker/answer), else WorkerError(400): 1 to NEEDS_MAX
    {need, choice | text | file}, each need of the plan once, with exactly one value (null counts as absent; other keys
    are dropped). choice: an option id of that need; text: only for a text need, 1-ANSWER_TEXT characters (line breaks
    kept, runs of spaces collapsed, redacted); file: only for a file need, {name, sha256} of a file attached to the test
    now (files: as uploads.list_files lists them) with an extension the need accepts."""
    if not isinstance(value, list) or not 1 <= len(value) <= NEEDS_MAX:
        raise WorkerError(400, f"answers: 1-{NEEDS_MAX} answers")
    by_id = {n["id"]: n for n in needs or [] if isinstance(n, dict) and isinstance(n.get("id"), str)}
    out = []
    for i, a in enumerate(value):
        name = f"answers[{i}]"
        nid = a.get("need") if isinstance(a, dict) else None
        need = by_id.get(nid) if isinstance(nid, str) else None
        if need is None:
            raise WorkerError(400, "an answer belongs to a question this plan does not ask; reload the page")
        if any(x["need"] == nid for x in out):
            raise WorkerError(400, "a question was answered twice; reload the page")
        given = [k for k in ("choice", "text", "file") if a.get(k) is not None]
        if len(given) != 1:
            raise WorkerError(400, "each answer is one choice, one text or one file")
        kind, val = given[0], a[given[0]]
        if kind == "choice":
            if val not in [o.get("id") for o in need.get("options") or [] if isinstance(o, dict)]:
                raise WorkerError(400, "a choice is not one this question offers; reload the page")
            out.append({"need": nid, "choice": val})
            continue
        if need.get("kind") != kind:
            raise WorkerError(400, "an answer does not fit its question; reload the page")
        if kind == "text":
            out.append({"need": nid, "text": _answer_text(val, f"{name}.text")})
            continue
        fname, sha = (val.get("name"), val.get("sha256")) if isinstance(val, dict) else (None, None)
        if not isinstance(fname, str) or not isinstance(sha, str):
            raise WorkerError(400, "choose one of the attached files")
        now = [f for f in files or [] if isinstance(f, dict) and f.get("name") == fname]
        if not now:
            raise WorkerError(400, f"{fname} is no longer attached to this test. Attach it again, then send.")
        if now[0].get("sha256") != sha:
            raise WorkerError(400, f"{fname} changed after you chose it. Choose it again, then send.")
        accept = [x for x in need.get("accept") or [] if isinstance(x, str)]
        if os.path.splitext(fname)[1].lower() not in accept:
            raise WorkerError(400, f"this question takes only {' or '.join(accept) or 'no'} files")
        out.append({"need": nid, "file": {"name": fname, "sha256": sha}})
    return out


def _answer_text(value, name):
    """A text answer: line breaks kept (CR LF and CR become LF), runs of spaces in a line collapsed, ends stripped,
    redacted; 1-ANSWER_TEXT characters."""
    v = value.replace("\r\n", "\n").replace("\r", "\n") if isinstance(value, str) else ""
    v = "\n".join(" ".join(line.split()) for line in v.split("\n")).strip()
    if not v or len(v) > ANSWER_TEXT or "\x00" in v:
        raise WorkerError(400, f"an answer needs 1 to {ANSWER_TEXT} characters")
    return redact.redact(v)


def blocking_needs(check):
    """The blocking needs of a New test plan ([] for any other request): Run waits until the person has answered them,
    which makes a follow-up plan (serve.py api_worker_start refuses with 409 before start_run)."""
    if not isinstance(check, dict) or check.get("intake") is not True or not isinstance(check.get("result"), dict):
        return []
    needs = check["result"].get("needs")
    return [n for n in needs if isinstance(n, dict) and n.get("blocking") is True] if isinstance(needs, list) else []


def _copied_check_time(req, rows, root):
    """The answered plan's checked_at when req follows a plan and every device row equals that plan's row for the
    device (the worker copied them, devices_copied), so the 30-minute Run window counts from the real check; None
    otherwise."""
    prev = load_request(root, req.get("follows")) if isinstance(req.get("follows"), str) and \
        REQ_ID_RE.match(req["follows"]) else None
    res = (prev or {}).get("result")
    if not isinstance(res, dict) or not isinstance(res.get("checked_at"), str):
        return None
    keys = ("tag", "ok", "connected", "account", "app_ok", "app", "needs")
    old = {r.get("tag"): tuple(r.get(k) for k in keys) for r in res.get("targets") or [] if isinstance(r, dict)}
    return res["checked_at"] if rows and all(old.get(r["tag"]) == tuple(r.get(k) for k in keys) for r in rows) \
        else None


def first_plan_created(root, req):
    """When the first plan of a chain of answered plans was asked for (req itself when it follows none), so a case the
    worker wrote for that first plan still counts as new in each follow-up."""
    since, seen, prev = req.get("created"), set(), req.get("follows")
    while isinstance(prev, str) and REQ_ID_RE.match(prev) and prev not in seen and len(seen) < 50:
        seen.add(prev)
        p = load_request(root, prev) or {}
        since, prev = p.get("created") or since, p.get("follows")
    return since


def newer_plan(root, check):
    """A New test plan of the same test asked for after check that does not answer it (Prepare again or Edit made it)
    and was not cancelled or failed: check is then old and its questions are not answered any more; else None."""
    tid = (check.get("clickup") or {}).get("task_id")
    for r in list_requests(root):
        if r.get("type") == "check" and r.get("intake") is True and (r.get("clickup") or {}).get("task_id") == tid \
                and r.get("id") != check.get("id") and str(r.get("created") or "") > str(check.get("created") or "") \
                and r.get("follows") != check.get("id") and r.get("state") not in ("cancelled", "failed"):
            return r
    return None


def followers(root, rid):
    """The follow-up checks that answer the plan rid, newest first, without the cancelled or failed ones: their answers
    never reached a plan, so the person may answer that plan again."""
    return [r for r in list_requests(root) if r.get("follows") == rid and r.get("state") not in ("cancelled", "failed")]


def _intake_plan(obj, req, root, recs=None):
    """From a New test check: the cases the worker will run ({id, file, sha256, new, changed_in_plan, targets, text}),
    each the newest version of a hands-off case under qa/cases on devices the request names, with the start block and
    the clean-up block when it opens a page (start_block_problem, 2026-11-10; one written or rewritten for this plan
    also passes _check_written), and which attached file goes to which of them ([{name, case}]). No case
    is allowed: the summary then says what is missing. recs (a dict): filled with {case id: the case record that runs}."""
    import autonomy  # noqa: E402 (engine code, through plan's path; imported late to keep this module light)
    import plan  # noqa: E402
    import writes  # noqa: E402
    want = list(req.get("targets") or [])
    cases = obj.get("plan_cases")
    if not isinstance(cases, list) or len(cases) > 10:
        raise WorkerError(400, "plan_cases: a list of at most 10 {id, file, sha256, new, targets, text}")
    out, ids = [], []
    for i, c in enumerate(cases):
        if not isinstance(c, dict) or not isinstance(c.get("id"), str) or not plan.CASE_ID_RE.match(c["id"]):
            raise WorkerError(400, f"plan_cases[{i}].id: a case id")
        cid = c["id"]
        if cid in ids:
            raise WorkerError(400, f"plan_cases: {cid} more than once")
        ids.append(cid)
        if not isinstance(c.get("new"), bool):
            raise WorkerError(400, f"{cid}.new: true or false")
        on = c.get("targets")
        if (not isinstance(on, list) or not on or any(not isinstance(t, str) or t not in want for t in on)
                or len(set(on)) != len(on)):
            raise WorkerError(400, f"{cid}.targets: some of {', '.join(want)}")
        # the case file the run will use: its newest version now, the same file Start checks again
        [v] = plan.case_versions(root, [cid])
        newest = (v["file"], v["sha256"])
        if not (v["file"] or "").startswith("qa/cases/") or (c.get("file"), c.get("sha256")) != newest:
            raise WorkerError(400, f"{cid}: file and sha256 must be its newest version under qa/cases "
                                   f"({v['file'] or 'none found'})")
        case = autonomy.load_newest(cid, root)[0] or {}
        # defence in depth beside autonomy (which does not look at log-ins): no log-in and no gate beyond the
        # standing Next/Continue writes ever runs from New test
        for s in case.get("steps") or []:
            s = s if isinstance(s, dict) else {}
            if "--secret" in str(s.get("helper") or ""):
                raise WorkerError(400, f"{cid} step {s.get('n')}: a log-in (--secret) never runs from New test")
            other = [g for g in s.get("gates") or [] if g not in INTAKE_GATES]
            if other:
                raise WorkerError(400, f"{cid} step {s.get('n')}: gate {other[0]} never runs from New test "
                                       f"(only {' and '.join(INTAKE_GATES)})")
            why = new_test_write_problem(s, autonomy.run, case)
            if why:
                raise WorkerError(400, f"{cid} {why}")
        diff = case.get("differential")
        if isinstance(diff, dict) and js_acts(diff.get("api_js"), autonomy.run):  # review 2026-11-03: page JS too
            raise WorkerError(400, f"{cid} differential.api_js runs page JS that may act: from New test page JS only "
                                   "reads")
        verdict = autonomy.check_id(cid, root=root)
        if not verdict["hands_off"]:
            first = (verdict["blockers"] or [{}])[0]
            raise WorkerError(400, f"{cid} is not hands-off: {first.get('text')} (needs {first.get('fix')})")
        kind = case.get("targets")
        web = [t for t in on if t == writes.WEB_TAG]
        if (kind == "tablet" and web) or (kind == "web" and len(web) != len(on)):
            raise WorkerError(400, f"{cid}.targets: a {kind} case runs on "
                                   f"{'W044 only' if kind == 'web' else 'the tablets only'}")
        # 2026-11-10: the start block and the clean-up block, on every case of the plan (a reused one too)
        why = start_block_problem(cid, case, autonomy.run)
        if why:
            raise WorkerError(400, why)
        # new and changed are the server's, from the case files' own times (the worker's "new" is not used): new when
        # the case's first version came after the request, changed when an older case got its newest version since
        since = first_plan_created(root, req)
        new = _created_after(root, _first_version(root, cid), since)
        changed = not new and _created_after(root, v["file"], since)
        if new or changed:
            _check_written(cid, case, req, root)
        if recs is not None:
            recs[cid] = case
        out.append({"id": cid, "file": v["file"], "sha256": v["sha256"], "new": new, "changed_in_plan": changed,
                    "targets": sorted(on, key=lambda t: TAG_ORDER.index(t) if t in TAG_ORDER else 99),
                    "text": _text(c.get("text"), f"{cid}.text", 200)})
    names = {f.get("name") for f in req.get("files") or [] if isinstance(f, dict)}
    plan_files = obj.get("plan_files") or []
    if not isinstance(plan_files, list) or len(plan_files) > 40:
        raise WorkerError(400, "plan_files: [{name, case}]")
    files = []
    for x in plan_files:
        if not isinstance(x, dict) or not isinstance(x.get("name"), str) or not isinstance(x.get("case"), str) \
                or x["name"] not in names or x["case"] not in ids:
            raise WorkerError(400, "plan_files: each {name, case} names an attached file and a case in plan_cases")
        files.append({"name": x["name"], "case": x["case"]})
    return out, files


def _check_written(cid, case, req, root):
    """A case written or rewritten for this plan (new or changed_in_plan): refused as case_schema.validate_case would
    refuse its write, with the parts rule of "Steps from the task" whatever its requirement source says (2026-11-05,
    wingmate-debug FINDINGS #6: a rewrite of a ClickUp-sourced case too); when a part quotes words that are not in the
    description, the ClickUp text or a question the person answered (case, spaces and punctuation ignored); and when a
    check part quotes a ClickUp item with result lines under it but no part of the case quotes one of those lines
    (FINDINGS #4, _heading_problem). The refusal names the case and the part."""
    import case_schema  # noqa: E402
    problems = case_schema.validate_case(case, for_new_test=True)
    if problems:
        more = f" (and {len(problems) - 3} more)" if len(problems) > 3 else ""
        raise WorkerError(400, f"{cid}: {'; '.join(problems[:3])}{more}")
    parts = case.get("parts")
    if not isinstance(parts, list) or not parts:
        return
    words = [_squash(t) for t in _task_words(req, root)]
    for i, part in enumerate(parts):
        quotes = part.get("quote") if isinstance(part, dict) else None
        for q in quotes if isinstance(quotes, list) else [quotes]:
            shown = redact.redact(q) if isinstance(q, str) else None  # the sources are stored redacted
            if shown is not None and not (_squash(shown) and any(_squash(shown) in w for w in words)):
                raise WorkerError(400, f"{cid} part {i + 1}: the quote {shown[:QUOTE_SHOWN]!r} is not in the "
                                       "description, the ClickUp text or a question the person answered (case, "
                                       "spaces and punctuation are ignored); quote the task's own words, as a list "
                                       "when they are not together")
    why = _heading_problem(cid, parts, task_items(_intake_text(req, root)))
    if why:
        raise WorkerError(400, why)


# ---- the task's lines and the plan's parts (2026-11-05, tests/2026-11-05-wingmate-debug/FINDINGS.md #2 and #4) -------
# A ClickUp item that a part carries out brings its expected results with it (SPEC.md "CASE RULE"): the lines listed
# under it in the ClickUp description. At plan time (validate_check_result, a New test plan whose cases all have
# parts) each task line that no part quotes and not_covered does not hold yet is added to not_covered in the task's own
# words (uncovered_task_lines, never a refusal), and _check_written refuses a check part that quotes an item with
# result lines under it while no part of its case quotes one of them (_heading_problem, which names them sentence by
# sentence and takes the lines under an 'e.g.' item for examples, review 2026-11-09). Matched as the quote check
# compares (_squash); a text counts as quoted when the quotes cover at least QUOTED_SHARE of its letters and digits
# (the prototype the debug pass tested on the plans of 2026-11-04 and 2026-11-05: scratchpad debug/planflow).
QUOTED_SHARE = 0.5
TASK_ITEM_RE = re.compile(r"^(\s*)(?:[*+-]|\d+[.)])\s+(.*\S)\s*$")
LIST_MARK_RE = re.compile(r"^\s*(?:[*+-]|\d+[.)])\s+")
LINK_ONLY_RE = re.compile(r"^\s*(?:!?\[[^\]]*\]\([^)]*\)|<?https?://\S+>?)\s*$")  # a link or an image alone
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
PLACEHOLDERS = frozenset({"", "etc", "eg", "andsoon", "andmore"})  # squashed: '…', 'etc.', 'e.g.', 'and so on'
PLATFORM_WORDS = frozenset({"android", "tablet", "tablets", "web", "browser"})  # a bare platform word is no task line
ADDED_MAX = 10  # task lines added to not_covered at most; the last one then says how many more there are
# an item that ends in 'e.g.', 'for example' or 'such as' lists examples under it, not expected results (review
# 2026-11-09: 'Test with terrain from various latitudes ... e.g.' over 'Alaska', 'Antarctica', ...)
EXAMPLES_RE = re.compile(r"(?:\be\.\s?g\b\.?|\beg\.|\bfor (?:example|instance)|\bsuch as)\s*[:.,…]*\s*$", re.I)


def _md_text(text):
    """A line of the ClickUp description in plain words: bold marks and markdown escapes dropped, spaces collapsed."""
    return " ".join(re.sub(r"\*\*|__|\\(?=[-*_])", "", text).split())


def task_items(description):
    """The list items of a ClickUp description in order: [{text, below}], below the texts of every item listed under
    it (nested deeper, before the next item at its level or above)."""
    items, stack = [], []
    for line in (description or "").splitlines():
        m = TASK_ITEM_RE.match(line)
        if not m:
            continue
        indent = len(m.group(1).replace("\t", "    "))
        while stack and stack[-1][1] >= indent:
            stack.pop()
        item = {"text": _md_text(m.group(2)), "below": []}
        for parent, _ in stack:
            parent["below"].append(item["text"])
        items.append(item)
        stack.append((item, indent))
    return items


def _sentences(text):
    return [s.strip() for s in SENTENCE_RE.split(_md_text(text)) if s.strip()]


def _no_task_line(text):
    """True for a line that asks nothing of the app: a link or an image alone, a label ending in ':', a placeholder
    ('…', 'etc.'), a short question to the tester ('Anything else?') or a bare platform word ('Android')."""
    t, s = _md_text(text), _squash(text)
    if LINK_ONLY_RE.match(t) or t.endswith(":") or s in PLACEHOLDERS or s in PLATFORM_WORDS:
        return True
    return t.endswith("?") and len(t.split()) <= 3 and not re.search(r"\d", t)


def _quoted_share(text, quotes):
    """How much of text (its letters and digits) the quotes cover, 0.0 to 1.0; 1.0 when one quote holds all of it."""
    u = _squash(text)
    if not u:
        return 1.0
    hit = [False] * len(u)
    for q in quotes:
        sq = _squash(q)
        if not sq:
            continue
        if u in sq:
            return 1.0
        at = u.find(sq)
        while at >= 0:
            hit[at:at + len(sq)] = [True] * len(sq)
            at = u.find(sq, at + 1)
    return sum(hit) / len(u)


def _part_quotes(parts):
    """The quotes of some parts (a text or a list of texts each), redacted as the sources are stored."""
    out = []
    for p in parts if isinstance(parts, list) else []:
        q = p.get("quote") if isinstance(p, dict) else None
        out += [redact.redact(x) for x in (q if isinstance(q, list) else [q]) if isinstance(x, str)]
    return out


def _intake_text(req, root):
    """The ClickUp description of the request's task (qa/cache/intake/<task id>.json), "" when there is none."""
    import plan  # noqa: E402
    cu = req.get("clickup") if isinstance(req.get("clickup"), dict) else {}
    desc = (plan.load_intake(root, cu.get("task_id")) or {}).get("description")
    return desc if isinstance(desc, str) else ""


CLICKUP_TEXT_MAX = 12000  # characters of the task's ClickUp text that a New test check request carries


def clickup_description(root, task_id):
    """The "clickup_description" of a New test check request (2026-11-10, serve.intake_fields): the task's ClickUp text
    as saved from its link (the text _intake_text reads), redacted like every text a request holds and cut at
    CLICKUP_TEXT_MAX characters with '…'; "" when there is none. Rounds 1 and 2 of ClickUp test 0000fake009 were
    BLOCKED because the worker planned without opening that saved copy, whose "Test setup" line asks for the flags
    off; now the request it takes holds the text itself."""
    text = redact.redact(_intake_text({"clickup": {"task_id": task_id}}, root).replace("\x00", "").strip())
    return text if len(text) <= CLICKUP_TEXT_MAX else text[:CLICKUP_TEXT_MAX - 1].rstrip() + "…"


def _heading_problem(cid, parts, items):
    """The refusal of a check part that quotes a ClickUp item with result lines under it while no part of the case
    quotes one of those lines (FINDINGS #4), naming the lines; None when there is none. The lines are named sentence
    by sentence (_result_sentences), the unit uncovered_task_lines counts (review 2026-11-09): a part that quotes the
    sentences it checks leaves the others to not_covered, where a quote of a whole line would count each of its
    sentences as checked. The lines under an item that ends in 'e.g.', 'for example' or 'such as' are examples, not
    results (EXAMPLES_RE): they never make a refusal."""
    parts = [p for p in parts if isinstance(p, dict)]
    own = _part_quotes(parts)
    for i, p in enumerate(parts):
        if p.get("check") is not True:
            continue
        mine = _part_quotes([p])
        for it in items:
            if EXAMPLES_RE.search(it["text"]):
                continue
            results = _result_sentences(it["below"])
            if results and _quoted_share(it["text"], mine) >= QUOTED_SHARE and \
                    not any(_quoted_share(s, own) >= QUOTED_SHARE for s in results):
                lines = ", ".join(repr(_cut_line(s)) for s in results)
                return (f"{cid} part {i + 1}: it checks, but quotes the ClickUp item "
                        f"{_cut_line(redact.redact(it['text']))!r}, whose expected results are the lines under it, "
                        f"sentence by sentence: {lines}; quote the result lines this part checks; the others go to "
                        "not_covered")
    return None


def _result_sentences(lines):
    """The sentences of an item's result lines in order, redacted as the quotes are, each once (compared with
    _squash); a line or sentence that is no task line (_no_task_line) is left out."""
    out, seen = [], set()
    for b in lines:
        if _no_task_line(b):
            continue
        for s in _sentences(redact.redact(b)):
            k = _squash(s)
            if k and k not in seen and not _no_task_line(s):
                seen.add(k)
                out.append(s)
    return out


def _in_not_covered(text, entries):
    """True when a not_covered entry holds the line: its words contain the line's, or they are at least QUOTED_SHARE
    of the line and the line contains them (an entry cut with '…')."""
    u = _squash(text)
    for x in entries:
        e = _squash(x)
        if e and (u in e or (e in u and len(e) >= QUOTED_SHARE * len(u))):
            return True
    return False


def uncovered_task_lines(req, root, cases, not_covered):
    """The task lines a New test plan leaves out and not_covered does not hold yet (FINDINGS #2), to add to not_covered:
    each sentence of a ClickUp item that a part carries out (its quotes cover at least QUOTED_SHARE of it) and of the
    items listed under it, and of each line of the person's description that is also a ClickUp item, unless it is no
    task line (_no_task_line) or the plan's quotes cover at least QUOTED_SHARE of it. In the task's words, redacted, each
    cut at NOT_COVERED_TEXT with '…'; at most ADDED_MAX. [] unless the plan has cases and every one has parts (a case
    written before the rule has no quotes to compare)."""
    if not cases or any(not isinstance(c.get("parts"), list) or not c["parts"] for c in cases):
        return []
    quotes = [q for c in cases for q in _part_quotes(c["parts"])]
    items = task_items(_intake_text(req, root))
    lines, seen = [], set()

    def count(text):
        for s in _sentences(redact.redact(text)):
            k = _squash(s)
            if k and k not in seen:
                seen.add(k)
                lines.append(s)
    for it in items:
        if _quoted_share(it["text"], quotes) >= QUOTED_SHARE:
            for t in [it["text"]] + it["below"]:
                count(t)
    listed = {_squash(it["text"]) for it in items} - {""}
    guidance = req.get("guidance") if isinstance(req.get("guidance"), str) else ""
    for line in guidance.splitlines():
        sl = _squash(LIST_MARK_RE.sub("", line))
        if sl and any(k == sl or (k in sl and len(k) >= QUOTED_SHARE * len(sl))
                      or (sl in k and len(sl) >= QUOTED_SHARE * len(k)) for k in listed):
            count(LIST_MARK_RE.sub("", line))
    out = [_cut_line(s) for s in lines if not _no_task_line(s) and _quoted_share(s, quotes) < QUOTED_SHARE
           and not _in_not_covered(s, not_covered)]
    out = list(dict.fromkeys(x for x in out if x not in not_covered))
    if len(out) > ADDED_MAX:
        out = out[:ADDED_MAX - 1] + [f"{len(out) - ADDED_MAX + 1} more lines of the ClickUp task that no part quotes"]
    return out


def _squash(text):
    """text as the quote check compares it: NFKC, case folded, only its letters and digits."""
    return "".join(c for c in unicodedata.normalize("NFKC", text).casefold() if c.isalnum())


def _task_words(req, root):
    """What a part may quote: the person's description (guidance), the ClickUp text (the test's name and listed cases on
    the request, and the name, cases and description in qa/cache/intake/<task id>.json) and the questions the person
    answered, with their answers (_answered)."""
    import plan  # noqa: E402
    cu = req.get("clickup") if isinstance(req.get("clickup"), dict) else {}
    saved = plan.load_intake(root, cu.get("task_id")) or {}
    words = [req.get("guidance"), cu.get("title"), saved.get("title"), saved.get("description")]
    words += [c.get("text") for x in (req.get("clickup_cases"), saved.get("cases")) if isinstance(x, list) for c in x
              if isinstance(c, dict)]
    return [w for w in words + _answered(root, req) if isinstance(w, str)]


def _list(x):
    return x if isinstance(x, list) else []


def _answered(root, req):
    """Along the chain of answered plans (follows): the text of each question the person answered, the label of the
    option they chose and their text answer."""
    out, seen, cur = [], set(), req
    while isinstance(cur.get("follows"), str) and REQ_ID_RE.match(cur["follows"]) and cur["follows"] not in seen \
            and len(seen) < 50:
        seen.add(cur["follows"])
        prev = load_request(root, cur["follows"]) or {}
        res = prev.get("result") if isinstance(prev.get("result"), dict) else {}
        needs = {n["id"]: n for n in _list(res.get("needs")) if isinstance(n, dict) and isinstance(n.get("id"), str)}
        for a in _list(cur.get("answers")):
            need = needs.get(a["need"]) if isinstance(a, dict) and isinstance(a.get("need"), str) else None
            if isinstance(need, dict):
                out += [need.get("text"), a.get("text")] + [o.get("label") for o in _list(need.get("options"))
                                                            if isinstance(o, dict) and o.get("id") == a.get("choice")]
        cur = prev
    return [t for t in out if isinstance(t, str)]


def _first_version(root, cid):
    """qa/cases/<cid>.json, else its lowest qa/cases/<cid>.c<N>.json; None when there is neither."""
    if os.path.exists(os.path.join(root, "qa", "cases", f"{cid}.json")):
        return f"qa/cases/{cid}.json"
    try:
        found = [(int(m.group(1)), n) for n in os.listdir(os.path.join(root, "qa", "cases"))
                 for m in [re.match(rf"^{re.escape(cid)}\.c(\d+)\.json\Z", n)] if m]
    except OSError:
        return None
    return f"qa/cases/{min(found)[1]}" if found else None


def _created_after(root, rel, since):
    """True when the case version at rel says it was created after since; a version without a readable time counts as
    after (a case the server cannot date is never shown as an old one)."""
    t = _parse_time(((_read(os.path.join(root, rel)) if rel else None) or {}).get("created"))
    s = _parse_time(since)
    return t is None or s is None or t.tzinfo is None or s.tzinfo is None or t > s


def _file_plan(obj, req):
    """From a prepare check with attached files: which engine cases of the pending cases the files complete, and which
    file goes to which engine case. Both must name only what the request holds."""
    offered = {e for c in req.get("pending") or [] if isinstance(c, dict) for e in c.get("engine_cases") or []}
    names = {f.get("name") for f in req.get("files") or [] if isinstance(f, dict)}
    complete = obj.get("complete") or []
    if not isinstance(complete, list) or any(not isinstance(e, str) for e in complete) \
            or len(set(complete)) != len(complete) or any(e not in offered for e in complete):
        raise WorkerError(400, "complete: engine case ids of the request's pending cases")
    plan_files = obj.get("plan_files") or []
    if not isinstance(plan_files, list) or len(plan_files) > 40:
        raise WorkerError(400, "plan_files: [{name, case}]")
    out = []
    for x in plan_files:
        # a file goes only to a case it completes: ready cases keep their own pinned files
        if not isinstance(x, dict) or not isinstance(x.get("name"), str) or not isinstance(x.get("case"), str) \
                or x["name"] not in names or x["case"] not in complete:
            raise WorkerError(400, "plan_files: each {name, case} names an attached file and a case in complete")
        out.append({"name": x["name"], "case": x["case"]})
    bare = [e for e in complete if e not in {x["case"] for x in out}]
    if bare:
        raise WorkerError(400, f"complete: {', '.join(bare)} has no attached file in plan_files")
    return complete, out


def validate_file_request(fields):
    """The fields of a new "file" request, exactly FILE_FIELDS, else WorkerError(400)."""
    if not isinstance(fields, dict) or sorted(fields) != sorted(FILE_FIELDS) or fields.get("type") != "file":
        raise WorkerError(400, "a file request holds exactly " + ", ".join(FILE_FIELDS))
    d = fields["ticket_dir"]
    if not isinstance(d, str) or not TICKET_DIR_RE.match(d):
        raise WorkerError(400, "ticket_dir: tests/<yyyy-mm-dd-slug>/tickets/<n>")
    if fields["target"] not in FILE_TARGETS:
        raise WorkerError(400, f"target: one of {', '.join(FILE_TARGETS)}")
    if not isinstance(fields["approval_id"], str) or not APPROVAL_ID_RE.match(fields["approval_id"]):
        raise WorkerError(400, "approval_id: A-<YYYYMMDDHHMMSS>-<6 hex>")
    if fields["task"] is not None and (not isinstance(fields["task"], str) or not TASK_RE.match(fields["task"])):
        raise WorkerError(400, "task: a task id or null")
    if not isinstance(fields["run"], str) or not RUN_RE.match(fields["run"]):
        raise WorkerError(400, "run: a run id")
    return dict(fields)


def validate_draft_request(fields):
    """The fields of a new "draft" request, exactly DRAFT_FIELDS (task, case and run of one Results card), else
    WorkerError(400)."""
    if not isinstance(fields, dict) or sorted(fields) != sorted(DRAFT_FIELDS) or fields.get("type") != "draft":
        raise WorkerError(400, "a draft request holds exactly " + ", ".join(DRAFT_FIELDS))
    if not isinstance(fields["task"], str) or not TASK_RE.match(fields["task"]):
        raise WorkerError(400, "task: a task id")
    if not isinstance(fields["case"], str) or not CASE_RE.match(fields["case"]):
        raise WorkerError(400, "case: a case id")
    if not isinstance(fields["run"], str) or not RUN_RE.match(fields["run"]):
        raise WorkerError(400, "run: a run id")
    return dict(fields)


def check_usable(check, now=None):
    """The passing targets of a done, fresh check request, else WorkerError(409)."""
    if not check or check.get("type") != "check":
        raise WorkerError(404, "no such device check")
    if check.get("state") != "done" or not isinstance(check.get("result"), dict):
        raise WorkerError(409, "the device check has not finished")
    a = age_s(check["result"].get("checked_at"), now)
    if a is None or a > CHECK_FRESH_S:
        # a New test plan is made again with Prepare again (2026-11-05, wingmate-debug FINDINGS #27)
        raise WorkerError(409, "the device check of this plan is older than 30 minutes; prepare again"
                          if check.get("intake") is True else "the device check is older than 30 minutes; check again")
    return [r["tag"] for r in check["result"].get("targets") or [] if r.get("ok")]


# ---- writes through writes.Writer -----------------------------------------------------------------------------------

def _writer(root):
    import writes  # noqa: E402 (writes imports this module's neighbours; imported late to keep this module light)
    return writes.Writer(root)


def _err(e):
    return WorkerError(getattr(e, "status", 400), getattr(e, "message", str(e)))


def claim(root, rid, link=None):
    if link is not None and not LINK_RE.match(link):
        raise WorkerError(400, "link: the session link from get_session")

    def change(req):
        if req.get("state") != "new":
            raise WorkerError(409, f"{rid} is {req.get('state')}, not new")
        req["state"], req["claimed_at"], req["link"] = "claimed", records.now_utc(), link
    w = _writer(root)
    req = w.update_request(rid, change)
    w.write_heartbeat({"state": "busy", "request": rid, "pid": None, "until": None, "link": link})
    return req


def note(root, rid, text):
    text = _text(text, "note", NOTE_MAX)

    def change(req):
        if req.get("state") != "claimed":
            raise WorkerError(409, f"{rid} is {req.get('state')}, not claimed")
        notes = req.setdefault("notes", [])
        if len(notes) >= NOTES_MAX:
            raise WorkerError(409, "too many notes on one request")
        notes.append({"at": records.now_utc(), "text": text})
    return _writer(root).update_request(rid, change)


def answer_check(root, rid, obj):
    def change(req):
        if req.get("state") != "claimed" or req.get("type") != "check":
            raise WorkerError(409, f"{rid} is not a claimed device check")
        req["result"] = validate_check_result(obj, req, root)
        req["state"], req["done_at"] = "done", records.now_utc()
    w = _writer(root)
    req = w.update_request(rid, change)
    w.write_heartbeat({"state": "idle", "request": None, "pid": None, "until": None, "link": req.get("link")})
    return req


def finish(root, rid, state, text=None):
    if state not in ("done", "failed"):
        raise WorkerError(400, "state: done or failed")
    text = _text(text, "note", NOTE_MAX, required=state == "failed")

    def change(req):
        if req.get("state") != "claimed":
            raise WorkerError(409, f"{rid} is {req.get('state')}, not claimed")
        if state == "done" and req.get("type") == "check":
            raise WorkerError(409, "a device check ends with answer-check")
        if state == "done" and req.get("type") == "file" and not submitted(root, req.get("ticket_dir") or ""):
            raise WorkerError(409, "a ticket request ends done only after submitted.json is written (sak-submit 3.3)")
        if state == "done" and req.get("type") == "draft" and not draft_dir_for_run(root, req.get("run")):
            raise WorkerError(409, "a draft request ends done only after its draft folder is written: tests/<campaign>/"
                                   "tickets/<n> with title.txt, description.txt and draft.json whose run_record names "
                                   f"{req.get('run')}")
        if text:
            req.setdefault("notes", []).append({"at": records.now_utc(), "text": text})
        req["state"], req["done_at"] = state, records.now_utc()
    w = _writer(root)
    req = w.update_request(rid, change)
    if state == "failed":
        _settle(w, req, f"Start {rid} failed: {text}")
    w.write_heartbeat({"state": "idle", "request": None, "pid": None, "until": None, "link": req.get("link")})
    return req


def _settle(w, req, why):
    """After a start request ended failed (Hover's fail, or expire): its task, when still running, moves on as Claude
    (writes.Writer.settle_started_task, 2026-11-05, wingmate-debug FINDINGS #9). Never stops the caller."""
    if req.get("type") != "start":
        return
    try:
        w.settle_started_task(req, why, by="claude", via="qa-run")
    except Exception:  # noqa: BLE001 (the request has ended either way; the task can still be moved by hand)
        pass


def cancel(root, rid, now=None):
    """The person's Cancel: a check, start, file or draft request not yet taken, or one whose worker shows no sign of
    work (stuck)."""
    prog = _progress(root)

    def change(req):
        if req.get("type") not in ("check", "start", "file", "draft"):
            raise WorkerError(409, "drafts are not cancelled; they are written after the greenlight")
        if req.get("state") == "new":
            req["state"], text = "cancelled", "Cancelled in the app."
        elif is_stuck(req, prog, now):
            req["state"], text = "failed", "Released in the app: Hover showed no sign of work for 20 minutes."
        else:
            raise WorkerError(409, "Hover is working on this request")
        req["done_at"] = records.now_utc()
        req.setdefault("notes", []).append({"at": records.now_utc(), "text": text})
    return _writer(root).update_request(rid, change)


def expire(root, now=None, link=None):
    """Fail what no worker can still answer: old new checks, starts and file requests (an expired approval), and
    claims left by a stopped worker."""
    w = _writer(root)
    prog = _progress(root)
    out = []
    for r in list_requests(root):
        rid, st, typ = r.get("id"), r.get("state"), r.get("type")
        why = None
        if st == "new" and typ in STALE_NEW_S and (age_s(r.get("created"), now) or 0) > STALE_NEW_S[typ]:
            why = EXPIRED_NOTE if typ == "file" else CHECK_EXPIRED_NOTE[r.get("intake") is True] if typ == "check" \
                else START_EXPIRED_NOTE
        elif st == "claimed" and ((link and r.get("link") == link) or is_stuck(r, prog, now)):
            # this worker's own leftover, or one with no sign of work for 20 minutes (a live worker notes more often)
            why = "Hover stopped while handling this request."
        if why:
            def change(req, why=why, st=st):
                if req.get("state") != st:
                    raise WorkerError(409, "changed meanwhile")
                req["state"], req["done_at"] = "failed", records.now_utc()
                req.setdefault("notes", []).append({"at": records.now_utc(), "text": why})
            try:
                req = w.update_request(rid, change)
                out.append(rid)
            except Exception:  # noqa: BLE001 (another worker or the app changed it first)
                continue
            _settle(w, req, f"Start {rid} failed: {why}")
    return out


def _until(hhmm, now):
    m = re.match(r"^([01]\d|2[0-3]):([0-5]\d)\Z", hhmm or "")
    if not m:
        raise WorkerError(400, "until: HH:MM")
    local = now.astimezone()
    end = local.replace(hour=int(m[1]), minute=int(m[2]), second=0, microsecond=0)
    if end <= local or end - local > datetime.timedelta(hours=PLAN_HOURS_MAX):
        # started after the end time (or after midnight): a few hours, never the whole next day
        end = local + datetime.timedelta(seconds=EVENING_WAIT_S)
    return end


def wait(root, until="19:00", link=None, out=sys.stdout, sleep=time.sleep, clock=None):
    """Wait for the oldest new request. Holds an exclusive lock for its whole life, so only one worker waits."""
    if link is not None and not LINK_RE.match(link):
        raise WorkerError(400, "link: the session link from get_session")
    clock = clock or (lambda: datetime.datetime.now(datetime.timezone.utc))
    d = os.path.join(root, WORKER_REL)
    os.makedirs(d, exist_ok=True)
    fd = os.open(os.path.join(d, ".waiter.lock"), os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise WorkerError(5, "another QA worker is already waiting") from None
        w = _writer(root)
        end = _until(until, clock())
        expire(root, clock(), link)
        try:
            return _wait_loop(root, w, end, link, out, sleep, clock)
        except BaseException:
            # interrupted or killed from Python: say so, so the app does not show a ready worker
            w.write_heartbeat({"state": "off", "request": None, "pid": None, "until": None, "link": link})
            raise
    finally:
        os.close(fd)


def _wait_loop(root, w, end, link, out, sleep, clock):
    beat = None
    while True:
        now = clock()
        if now >= end:
            w.write_heartbeat({"state": "off", "request": None, "pid": None, "until": None, "link": link})
            out.write(json.dumps({"stop": "end of the worker's hours", "at": records.now_utc()}) + "\n")
            return 0
        new = [r for r in reversed(list_requests(root)) if r.get("state") == "new"]
        if new:
            req = new[0]
            w.write_heartbeat({"state": "waking", "request": req["id"], "pid": None, "until": None, "link": link})
            out.write(json.dumps(req, ensure_ascii=False) + "\n")
            return 0
        if beat is None or (now - beat).total_seconds() >= BEAT_S:
            w.write_heartbeat({"state": "ready", "request": None, "pid": os.getpid(),
                               "until": end.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                               "link": link})
            beat = now
        sleep(POLL_S)


# ---- CLI -------------------------------------------------------------------------------------------------------------

def main(argv):
    ap = argparse.ArgumentParser(prog="worker.py")
    ap.add_argument("--root", default=DEFAULT_ROOT)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("wait")
    p.add_argument("--until", default="19:00")
    p.add_argument("--link")
    p = sub.add_parser("claim")
    p.add_argument("id")
    p.add_argument("--link")
    p = sub.add_parser("note")
    p.add_argument("id")
    p.add_argument("text")
    p = sub.add_parser("answer-check")
    p.add_argument("id")
    for name in ("done", "fail"):
        p = sub.add_parser(name)
        p.add_argument("id")
        p.add_argument("--note", required=name == "fail")
    p = sub.add_parser("show")
    p.add_argument("id")
    sub.add_parser("list")
    a = ap.parse_args(argv)
    root = os.path.abspath(a.root)
    try:
        if a.cmd == "wait":
            return wait(root, a.until, a.link)
        if a.cmd == "claim":
            res = claim(root, a.id, a.link)
        elif a.cmd == "note":
            res = note(root, a.id, a.text)
        elif a.cmd == "answer-check":
            try:
                obj = json.load(sys.stdin)
            except ValueError:
                raise WorkerError(400, "stdin: one JSON object") from None
            res = answer_check(root, a.id, obj)
        elif a.cmd in ("done", "fail"):
            res = finish(root, a.id, "done" if a.cmd == "done" else "failed", a.note)
        elif a.cmd == "show":
            res = load_request(root, a.id)
            if res is None:
                raise WorkerError(404, f"no request {a.id}")
        else:
            res = view(root)
    except WorkerError as e:
        print(json.dumps({"error": e.message, "status": e.status}))
        return 5 if e.status == 5 else 1
    except Exception as e:  # noqa: BLE001 (a writes.WriteError: same shape)
        if hasattr(e, "status") and hasattr(e, "message"):
            print(json.dumps({"error": e.message, "status": e.status}))
            return 1
        raise
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
