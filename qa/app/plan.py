"""Today view: Colleague's office tests for Owner, read from ClickUp (qa/app/DESIGN.md section 8).

refresh(root, writer) reads ClickUp (GET only, clickup_read), keeps the tests in "up next" or "ongoing" whose
LOCATION is OFFICE, orders them by Colleague's priority, splits each description into its test cases and writes one
redacted snapshot per Europe/Zurich day: qa/cache/plan/<yyyy-mm-dd>.json. load(root) returns the newest snapshot.
Office tests with LOCATION empty or set to a field site are not listed; only their counts are kept.
resolve_intake(root, writer, ref) reads the one task a link names, for the New test page, and keeps a redacted copy:
qa/cache/intake/<task id>.json.
"""
import datetime
import hashlib
import json
import os
import re
import sys
import zoneinfo

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)
sys.path.insert(0, os.path.join(os.path.dirname(APP_DIR), "runner"))
import autonomy  # noqa: E402  the hands-off check (engine code)
import clickup_read  # noqa: E402
import redact  # noqa: E402

TEAM_ID = "1919191"
ASSIGNEE_ID = "91919191"  # Owner
ASSIGNEE_LABEL = "Owner"
FOLDER_ID = "919191919104"  # CT Test Planner
STATUSES = ("up next", "ongoing")
LOCATION_FIELD = "a0000006-0000-4000-8000-000000000006"
OFFICE_OPTION = "a0000007-0000-4000-8000-000000000007"
SW_ENV_FIELD = "a0000008-0000-4000-8000-000000000008"
NEVER_READ = {"CL-80008", "0000fake005"}  # holds a pasted credential; never fetched, listed or shown
PRIORITY_RANK = {"urgent": 1, "high": 2, "normal": 3, "low": 4}
STATUS_RANK = {"ongoing": 0, "up next": 1}
ZURICH = zoneinfo.ZoneInfo("Europe/Zurich")
PLAN_DIR = os.path.join("qa", "cache", "plan")
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\Z")

CL_LINK = re.compile(r"app\.clickup\.com/t/(?:\d+/)?(CL-\d{3,6})", re.I)
CL_BARE = re.compile(r"\bCL-\d{3,6}\b")
IMAGE_MD = re.compile(r"!\[[^\]]*\]\([^)]*\)")
LINK_MD = re.compile(r"\[([^\]]*)\]\(([^)\s]*)\)")
URL = re.compile(r"https?://\S+")
ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|>~=])")
LIST_ITEM = re.compile(r"^( *)(?:[*+-]|\d+[.)])\s+(.*)$")
MATRIX_HEAD = re.compile(r"^(different\s+hw|hardware|hw|android\s+versions?|devices?)\s*:?\s*$", re.I)
FIELD_MARK = re.compile(r"field[\s-]?test", re.I)
DRONE_MARK = re.compile(r"connected to the drone|only manually|real drone|with (?:a|the) drone", re.I)


def zurich_day(now=None):
    """Today's date in Switzerland as yyyy-mm-dd (the plan day)."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return now.astimezone(ZURICH).strftime("%Y-%m-%d")


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# ---- text ----------------------------------------------------------------------------------------------------------

def clean_text(s):
    """Markdown line to plain text: images and links reduced to their words, other URLs dropped, escapes undone."""
    s = IMAGE_MD.sub("[image]", s)

    def _link(m):
        words, url = m.group(1).strip(), m.group(2)
        cl = CL_LINK.search(url)
        if cl:
            return words if words and not URL.match(words) else cl.group(1).upper()
        if not words or URL.match(words):
            return "[link]"
        return words

    s = LINK_MD.sub(_link, s)
    s = URL.sub("[link]", s)
    s = ESCAPE.sub(r"\1", s)
    s = s.replace("**", "").replace("__", "")
    return " ".join(s.split())


def dev_tickets(markdown):
    """CL ids the description links or names, in order of first mention, CL-80008 left out."""
    out = []
    for m in list(CL_LINK.finditer(markdown or "")) + list(CL_BARE.finditer(markdown or "")):
        cl = m.group(1 if m.re is CL_LINK else 0).upper()
        if cl not in out and cl not in NEVER_READ:
            out.append(cl)
    return out


def markers(text, notes):
    joined = " ".join([text] + list(notes))
    return {"field": bool(FIELD_MARK.search(joined)), "drone": bool(DRONE_MARK.search(joined)), "green": False}


def parse_cases(markdown):
    """(cases, matrix, intro) from a ClickUp markdown description.

    A case is a list item at indent 0, numbered in order of appearance (ClickUp writes "1." for every item of some
    ordered lists). Deeper items are its notes. Items listed under a "Different HW:" or "Android versions:" line are
    the device matrix, not cases. A description with no list is one case made of its paragraphs.
    """
    cases, matrix, paras = [], [], []
    cur, under_matrix, last_para = None, False, ""
    for raw in (markdown or "").replace("\r\n", "\n").split("\n"):
        if not raw.strip():
            continue
        m = LIST_ITEM.match(raw.expandtabs(4))
        if m:
            indent, text = len(m.group(1)), clean_text(m.group(2))
            if not text:
                continue
            if indent == 0:
                if MATRIX_HEAD.match(last_para):
                    under_matrix, cur = True, None
                if under_matrix:
                    matrix.append(text)
                    continue
                cur = {"n": len(cases) + 1, "text": text, "notes": []}
                cases.append(cur)
            elif cur is not None:
                cur["notes"].append(text)
            continue
        line = clean_text(raw.strip().lstrip("#").strip())
        if line:
            paras.append(line)
            last_para, cur, under_matrix = line, None, False
    if not cases and paras:
        body = [p for p in paras if not re.fullmatch(r"(?:CL-\d+|\[link\]|\[image\])(?:\s+(?:CL-\d+|\[link\]))*", p)]
        if body:
            cases.append({"n": 1, "text": body[0], "notes": body[1:]})
    for c in cases:
        c["markers"] = markers(c["text"], c["notes"])
    used = set((cases[0]["text"],) + tuple(cases[0]["notes"])) if cases else set()
    intro = [p for p in paras if p not in used and not MATRIX_HEAD.match(p)
             and not re.fullmatch(r"(?:CL-\d+|\[link\]|\[image\])(?:\s+(?:CL-\d+|\[link\]|\[image\]))*", p)][:6]
    return cases, matrix, intro


# ---- ClickUp task fields -------------------------------------------------------------------------------------------

def _field(task, field_id):
    for f in task.get("custom_fields") or []:
        if isinstance(f, dict) and f.get("id") == field_id:
            return f
    return None


def dropdown(task, field_id):
    """(option id, option name) of a drop-down custom field, (None, None) when unset."""
    f = _field(task, field_id)
    if not f or f.get("value") in (None, ""):
        return None, None
    value = f["value"]
    options = ((f.get("type_config") or {}).get("options")) or []
    for o in options:
        if not isinstance(o, dict):
            continue
        if value == o.get("id") or (str(value).isdigit() and o.get("orderindex") is not None
                                    and int(value) == int(o["orderindex"])):
            return o.get("id"), o.get("name")
    return (value if isinstance(value, str) else None), None


def in_folder(task):
    ids = [((task.get("folder") or {}).get("id"))]
    ids += [loc.get("id") for loc in task.get("locations") or [] if isinstance(loc, dict)]
    return FOLDER_ID in [str(i) for i in ids if i is not None] or not task.get("folder")


def location_class(task):
    """office | unset | field."""
    option_id, _ = dropdown(task, LOCATION_FIELD)
    if option_id is None:
        return "unset"
    return "office" if option_id == OFFICE_OPTION else "field"


def _status(task):
    return str((task.get("status") or {}).get("status") or "").lower()


def _priority(task):
    p = task.get("priority")
    return str(p.get("priority")).lower() if isinstance(p, dict) and p.get("priority") else None


def _task_url(task_id):
    return f"https://app.clickup.com/t/{task_id}"


def cases_sig(cases):
    """Short fingerprint of a test's numbered case texts, so notes written for one version are not shown on another."""
    raw = json.dumps([[c.get("n"), c.get("text")] for c in cases], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def test_entry(task):
    md = task.get("markdown_description") or task.get("text_content") or ""
    cases, matrix, intro = parse_cases(md)
    _, sw_env = dropdown(task, SW_ENV_FIELD)
    try:
        updated = int(task.get("date_updated") or 0)
    except (TypeError, ValueError):
        updated = 0
    return {
        "task_id": str(task.get("id")),
        "custom_id": task.get("custom_id"),
        "title": clean_text(str(task.get("name") or "")),
        "url": _task_url(task.get("id")),
        "status": _status(task),
        "priority": _priority(task),
        "sw_env": sw_env,
        "dev_tickets": dev_tickets(md),
        "date_updated_ms": updated,
        "cases": cases,
        "cases_sig": cases_sig(cases),
        "matrix": matrix,
        "intro": intro,
    }


def order_key(t):
    return (PRIORITY_RANK.get(t["priority"] or "", 9), STATUS_RANK.get(t["status"], 5), -t["date_updated_ms"])


def build_snapshot(tasks, fetched, day):
    listed, hidden = [], {"location_unset": 0, "not_office": 0}
    for task in tasks:
        tid, cid = str(task.get("id") or ""), str(task.get("custom_id") or "")
        if not tid or tid in NEVER_READ or cid in NEVER_READ:
            continue
        if _status(task) not in STATUSES or not in_folder(task):
            continue
        cls = location_class(task)
        if cls == "unset":
            hidden["location_unset"] += 1
        elif cls == "field":
            hidden["not_office"] += 1
        else:
            listed.append(test_entry(task))
    listed.sort(key=order_key)
    snap = {"kind": "today_plan", "schema": 1, "day": day, "fetched": fetched,
            "source": {"workspace": TEAM_ID, "folder": "CT Test Planner", "assignee": ASSIGNEE_LABEL,
                       "statuses": list(STATUSES), "location": "OFFICE"},
            "tests": listed, "hidden": hidden}
    return redact.redact_obj(snap)


# ---- refresh and load ------------------------------------------------------------------------------------------------

def snapshot_rel(day):
    if not DAY_RE.match(day):
        raise ValueError("bad day")
    return f"{PLAN_DIR}/{day}.json"


def load(root, day=None):
    """The snapshot of day (default: the newest one on disk), or None."""
    d = os.path.join(root, PLAN_DIR)
    if day is None:
        try:
            days = sorted(f[:-5] for f in os.listdir(d) if f.endswith(".json") and DAY_RE.match(f[:-5]))
        except OSError:
            return None
        if not days:
            return None
        day = days[-1]
    try:
        with open(os.path.join(root, snapshot_rel(day)), encoding="utf-8") as f:
            obj = json.load(f)
    except (OSError, ValueError):
        return None
    return obj if isinstance(obj, dict) and obj.get("kind") == "today_plan" else None


# ---- pre-test notes written by the background engine (qa-prep skill), checked here ----------------------------------

PREP_STATUSES = ("ready", "needs_input", "blocked", "not_office", "covered")
PREP_STATES = ("running", "done", "failed")
DEVICE_TAGS = ("TA5", "ET65", "W044", "TA3")
CASE_ID_RE = re.compile(r"^[A-Z]{2,10}-\d{3}\Z")
CL_ID_RE = re.compile(r"^CL-\d{3,6}\Z")
TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z\Z")
HEX_RE = re.compile(r"^[0-9a-f]{7,40}\Z")
BRANCHES = ("sky", "sky-staging", "sky-preproduction", "sky-production")
ENVS = ("develop", "staging", "preproduction", "production")


class PrepError(ValueError):
    pass


def _txt(v, where, hi=300, allow_none=False):
    if v is None and allow_none:
        return None
    if not isinstance(v, str) or not v.strip() or len(v) > hi:
        raise PrepError(f"{where}: must be text of 1-{hi} characters")
    if URL.search(v) or "\x00" in v:
        raise PrepError(f"{where}: must not hold a link")
    return redact.redact(" ".join(v.split()))


def _txts(v, where, max_items=6, hi=300):
    if v is None:
        return []
    if not isinstance(v, list) or len(v) > max_items:
        raise PrepError(f"{where}: must be a list of at most {max_items} texts")
    return [_txt(x, f"{where}[{i}]", hi) for i, x in enumerate(v)]


def _dev_ticket(d, where):
    if not isinstance(d, dict) or not CL_ID_RE.match(str(d.get("cl") or "")) or d.get("cl") in NEVER_READ:
        raise PrepError(f"{where}.cl: must be CL-<n>")
    out = {"cl": d["cl"], "status": _txt(d.get("status"), f"{where}.status", 60, True),
           "env": d.get("env") if d.get("env") in ENVS else None, "note": _txt(d.get("note"), f"{where}.note", 300, True)}
    fix = d.get("fix")
    if fix is not None:
        if not isinstance(fix, dict):
            raise PrepError(f"{where}.fix: must be {{commits, in}}")
        commits = fix.get("commits") or []
        if not isinstance(commits, list) or not all(isinstance(c, str) and HEX_RE.match(c) for c in commits):
            raise PrepError(f"{where}.fix.commits: must be git hashes")
        inb = fix.get("in") or {}
        if not isinstance(inb, dict) or not all(k in BRANCHES and isinstance(v, bool) for k, v in inb.items()):
            raise PrepError(f"{where}.fix.in: must map env branches to true/false")
        out["fix"] = {"commits": commits[:5], "pr": _txt(fix.get("pr"), f"{where}.fix.pr", 20, True), "in": inb}
    return out


def _prep_case(c, where):
    if not isinstance(c, dict) or not isinstance(c.get("n"), int) or c["n"] < 1:
        raise PrepError(f"{where}.n: must be the case number (1, 2, ...)")
    if c.get("status") not in PREP_STATUSES:
        raise PrepError(f"{where}.status: must be one of {', '.join(PREP_STATUSES)}")
    engine = c.get("engine_cases") or []
    if not isinstance(engine, list) or not all(isinstance(x, str) and CASE_ID_RE.match(x) for x in engine):
        raise PrepError(f"{where}.engine_cases: must be case ids")
    targets = c.get("targets") or []
    if not isinstance(targets, list) or not all(t in DEVICE_TAGS for t in targets):
        raise PrepError(f"{where}.targets: must be device tags ({', '.join(DEVICE_TAGS)})")
    missing = []
    for i, m in enumerate(c.get("missing_devices") or []):
        if not isinstance(m, dict) or m.get("tag") not in DEVICE_TAGS:
            raise PrepError(f"{where}.missing_devices[{i}]: must be {{tag, why}}")
        missing.append({"tag": m["tag"], "why": _txt(m.get("why"), f"{where}.missing_devices[{i}].why", 200)})
    req = c.get("required") or {}
    if not isinstance(req, dict):
        raise PrepError(f"{where}.required: must be {{env, build_note}}")
    est = c.get("estimate_min")
    if est is not None and (not isinstance(est, int) or not 0 < est <= 480):
        raise PrepError(f"{where}.estimate_min: must be minutes (1-480) or null")
    if not isinstance(c.get("green", False), bool):
        raise PrepError(f"{where}.green: must be true or false")
    return {"n": c["n"], "status": c["status"], "why": _txts(c.get("why"), f"{where}.why"),
            "green": c.get("green", False), "engine_cases": engine, "proposal": _txt(c.get("proposal"),
            f"{where}.proposal", 300, True), "targets": targets, "missing_devices": missing,
            "required": {"env": req.get("env") if req.get("env") in ENVS else None,
                         "build_note": _txt(req.get("build_note"), f"{where}.required.build_note", 300, True)},
            "needs": _txts(c.get("needs"), f"{where}.needs"),
            "side_effects": _txts(c.get("side_effects"), f"{where}.side_effects"),
            "estimate_min": est, "note": _txt(c.get("note"), f"{where}.note", 400, True)}


def validate_prep(obj, day):
    """A clean copy of a prep object for day, else PrepError naming the first bad field."""
    if not isinstance(obj, dict) or obj.get("kind") != "today_prep" or obj.get("schema") != 1:
        raise PrepError("kind/schema: must be today_prep, schema 1")
    if obj.get("day") != day:
        raise PrepError("day: must equal the day being written")
    if not TIME_RE.match(str(obj.get("created") or "")):
        raise PrepError("created: must be an ISO-8601 UTC time")
    tests = obj.get("tests")
    if not isinstance(tests, list) or len(tests) > 50:
        raise PrepError("tests: must be a list")
    out_tests = []
    for i, t in enumerate(tests):
        w = f"tests[{i}]"
        if not isinstance(t, dict) or not isinstance(t.get("task_id"), str) or not t["task_id"].strip():
            raise PrepError(f"{w}.task_id: must be the ClickUp task id")
        if t["task_id"] in NEVER_READ:
            raise PrepError(f"{w}.task_id: refused")
        cases = t.get("cases")
        if not isinstance(cases, list) or len(cases) > 60:
            raise PrepError(f"{w}.cases: must be a list")
        out_tests.append({"task_id": t["task_id"], "summary": _txt(t.get("summary"), f"{w}.summary", 400, True),
                          "dev_tickets": [_dev_ticket(d, f"{w}.dev_tickets[{j}]")
                                          for j, d in enumerate(t.get("dev_tickets") or [])],
                          "cases": [_prep_case(c, f"{w}.cases[{j}]") for j, c in enumerate(cases)]})
    branches = {}
    for b, v in (obj.get("branches") or {}).items():
        if b in BRANCHES and isinstance(v, dict):
            branches[b] = {"head": v.get("head") if HEX_RE.match(str(v.get("head") or "")) else None,
                           "describe": _txt(v.get("describe"), f"branches.{b}.describe", 60, True)}
    return redact.redact_obj({"kind": "today_prep", "schema": 1, "day": day, "created": obj["created"],
                              "author": "claude", "based_on": _txt(obj.get("based_on"), "based_on", 60, True),
                              "engine_env": obj.get("engine_env") if obj.get("engine_env") in ENVS else None,
                              "branches": branches, "tests": out_tests})


def prep_rel(day, status=False):
    snapshot_rel(day)  # checks the day
    return f"{PLAN_DIR}/{day}.prep{'-status' if status else ''}.json"


def _dump(obj):
    return (json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8")


def put_prep(root, writer, day, obj):
    clean = validate_prep(obj, day)
    snap = load(root, day)
    if snap is None:
        raise PrepError(f"no snapshot for {day}: refresh the Today view first")
    # The fingerprint of the list the notes were written against; the page hides notes whose test has changed since.
    sigs = {t.get("task_id"): t.get("cases_sig") for t in snap.get("tests", [])}
    for t in clean["tests"]:
        t["cases_sig"] = sigs.get(t["task_id"])
    writer._write(prep_rel(day), _dump(clean), "replace")
    return clean


def set_prep_status(root, writer, day, state, note=None):
    if state not in PREP_STATES:
        raise PrepError(f"state: must be one of {', '.join(PREP_STATES)}")
    obj = {"kind": "today_prep_status", "day": day, "state": state, "at": utc_now(),
           "note": _txt(note, "note", 300, True)}
    writer._write(prep_rel(day, status=True), _dump(obj), "replace")
    return obj


def _read_json(root, rel):
    try:
        with open(os.path.join(root, rel), encoding="utf-8") as f:
            obj = json.load(f)
    except (OSError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def load_prep(root, day):
    """(prep or None, status or None) for day."""
    try:
        prep = _read_json(root, prep_rel(day))
        status = _read_json(root, prep_rel(day, status=True))
    except ValueError:
        return None, None
    return (prep if prep and prep.get("kind") == "today_prep" else None,
            status if status and status.get("kind") == "today_prep_status" else None)


def notes_for(prep, snap, test):
    """(notes, why): the pre-test notes of test, while its cases are still the ones they were written for."""
    if not prep or not snap or prep.get("day") != snap.get("day"):
        return None, "there are no pre-test notes for today's list"
    pt = next((x for x in prep.get("tests", []) if x.get("task_id") == test.get("task_id")), None)
    if pt is None:
        return None, "this test came into the list after the pre-test notes"
    if pt.get("cases_sig") and test.get("cases_sig"):
        changed = pt["cases_sig"] != test["cases_sig"]
    else:
        changed = len(pt.get("cases", [])) != len(test.get("cases", []))
    if changed:
        return None, "this test changed in ClickUp after the pre-test notes"
    return pt, None


def prep_fresh(root, day):
    """{fresh, why}: today's notes are done, newer than every case file, and cover today's list as it is now. A prep
    run started again later in the day (a retry or Run now) prepares again only when they are not."""
    prep, status = load_prep(root, day)
    if not prep or not status or status.get("state") != "done":
        return {"fresh": False, "why": [f"no finished pre-test notes for {day}"]}
    why = []
    try:
        at = os.stat(os.path.join(root, prep_rel(day))).st_mtime
        cdir = os.path.join(root, "qa", "cases")
        newer = sorted(f for f in os.listdir(cdir) if f.endswith(".json") and os.stat(os.path.join(cdir, f)).st_mtime > at)
    except OSError:
        newer = []
    if newer:
        why.append("case files changed after the notes: " + ", ".join(newer[:8]))
    snap = load(root, day)
    for t in (snap or {}).get("tests", []):
        if notes_for(prep, snap, t)[0] is None:
            why.append(f"{t.get('task_id')}: " + notes_for(prep, snap, t)[1])
    return {"fresh": not why, "why": why}


RUN_TAG_ORDER = ("TA5", "ET65", "W044", "TA3")


def case_versions(root, ids):
    """[{id, file, sha256}] of the version of each engine case the engine runs now (its newest, autonomy.load_newest),
    so a Prepare summary and the Start after it name the same case file. file and sha256 are None if none is found."""
    out = []
    for cid in dict.fromkeys(i for i in ids if isinstance(i, str)):
        try:
            _, path = autonomy.load_newest(cid, root)
            with open(path, "rb") as f:
                sha = hashlib.sha256(f.read()).hexdigest()
            out.append({"id": cid, "file": os.path.relpath(path, root), "sha256": sha})
        except (OSError, TypeError, ValueError):
            out.append({"id": cid, "file": None, "sha256": None})
    return out


def in_todays_list(root, task_id, day=None):
    snap = load(root, day or zurich_day())
    return bool(snap) and any(t.get("task_id") == task_id for t in snap.get("tests", []))


def run_plan(root, task_id, day=None, with_pending=False):
    """What a Run of ClickUp test task_id would run today, from today's list and pre-test notes: the ready cases,
    their engine cases and targets. PrepError carries the reason the app shows when nothing can run.
    with_pending (the person attached test files on the Run form): also the cases that need input, as "pending", for
    the worker to complete with those files; then a test with only such cases is not an error."""
    day = day or zurich_day()
    snap = load(root, day)
    if snap is None:
        raise PrepError("there is no list for today yet: refresh Today")
    test = next((t for t in snap.get("tests", []) if t.get("task_id") == task_id), None)
    if test is None:
        raise PrepError("that test is not in today's list")
    prep, _ = load_prep(root, day)
    pt, why = notes_for(prep, snap, test)
    if pt is None:
        raise PrepError(why)
    texts = {c.get("n"): c.get("text") for c in test.get("cases", [])}
    ready = [c for c in pt.get("cases", []) if c.get("status") == "ready"]
    pending = [c for c in pt.get("cases", []) if c.get("status") == "needs_input" and c.get("engine_cases")] \
        if with_pending else []
    engine = list(dict.fromkeys(e for c in ready for e in c.get("engine_cases") or []))
    if (not ready or not engine) and not pending:
        raise PrepError("nothing in this test is ready to run today")
    # Only hands-off cases start: nothing that would need the person once it runs (autonomy.py).
    verdicts = {e: autonomy.check_id(e, root=root) for e in engine}
    held = [c for c in ready if not all(verdicts[e]["hands_off"] for e in c.get("engine_cases") or [])]
    ready = [c for c in ready if c not in held]
    if not ready and not pending:
        first = next((b for e in engine for b in verdicts[e]["blockers"]), None)
        raise PrepError("nothing in this test can run hands-off yet" +
                        (f": {first['text']} (needs {first['fix']})" if first else ""))
    engine = list(dict.fromkeys(e for c in ready for e in c.get("engine_cases") or []))
    targets = sorted({t for c in ready + pending for t in c.get("targets") or []},
                     key=lambda t: RUN_TAG_ORDER.index(t) if t in RUN_TAG_ORDER else 99)
    fixes = list(dict.fromkeys(c for d in pt.get("dev_tickets", []) for c in (d.get("fix") or {}).get("commits") or []))

    def row(c):
        return {"n": c.get("n"), "text": texts.get(c.get("n")), "engine_cases": c.get("engine_cases") or [],
                "targets": c.get("targets") or [], "required": c.get("required"),
                "estimate_min": c.get("estimate_min"), "side_effects": c.get("side_effects") or [],
                "needs": c.get("needs") or []}
    out = {"clickup": {"task_id": test["task_id"], "title": test.get("title"), "url": test.get("url"),
                       "priority": test.get("priority")},
           "cases": [row(c) for c in ready], "engine_cases": engine, "targets": targets, "fix_commits": fixes,
           "day": day}
    if with_pending:
        out["pending"] = [dict(row(c), why=(c.get("why") or [None])[0]) for c in pending]
    return out


def refresh(root, writer, opener=None, now=None):
    """{ok: true, snapshot} after a fresh read, or {ok: false, kind, message, snapshot: <last one or None>}."""
    secrets_path = os.path.join(root, "qa", "secrets.env")
    token = clickup_read.read_token(secrets_path)
    try:
        tasks = clickup_read.team_tasks(TEAM_ID, ASSIGNEE_ID, STATUSES, token, opener)
    except clickup_read.ClickUpError as e:
        message = clickup_read.no_token_message(secrets_path) if e.kind == "no_token" else e.message
        return {"ok": False, "kind": e.kind, "message": message, "snapshot": load(root)}
    day = zurich_day(now)
    snap = build_snapshot(tasks, utc_now(), day)
    data = (json.dumps(snap, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8")
    writer._write(snapshot_rel(day), data, "replace")
    return {"ok": True, "snapshot": snap}


# ---- New test: one ClickUp task read from the link the person drops (DESIGN.md "Simple flow") ----------------------

INTAKE_DIR = os.path.join("qa", "cache", "intake")
INTAKE_KEYS = ("task_id", "custom_id", "title", "status", "priority", "url", "list", "cases")
DESC_MAX = 12000  # characters of the task description kept in the saved copy (= worker.CLICKUP_TEXT_MAX)
TASK_KEY_RE = re.compile(r"^[A-Za-z0-9-]{3,40}\Z")  # a task id as a file name (as uploads.TASK_RE)
TASK_LINK = re.compile(r"^(?:https?://)?app\.clickup\.com/t/(?:(\d+)/)?([A-Za-z0-9-]{3,40})/?\Z", re.I)
CUSTOM_REF = re.compile(r"^[A-Za-z][A-Za-z0-9]*-[1-9]\d*\Z")
BARE_REF = re.compile(r"^[a-z0-9]{4,40}\Z")
BAD_REF = "url: a ClickUp task link, a task id or a custom id like CL-80002"


def task_ref(text):
    """(task id, None) or (None, custom id) from a ClickUp task link, a bare task id or a custom id; PrepError when
    it names no task."""
    if not isinstance(text, str) or not text.strip() or len(text) > 300:
        raise PrepError(BAD_REF)
    s = re.split(r"[?#]", text.strip(), maxsplit=1)[0]
    m = TASK_LINK.match(s)
    ident = m.group(2) if m else s
    if CUSTOM_REF.match(ident):
        return None, ident.upper()
    if BARE_REF.match(ident):
        return ident, None
    raise PrepError(BAD_REF)


def intake_rel(task_id):
    if not isinstance(task_id, str) or not TASK_KEY_RE.match(task_id):
        raise ValueError("bad task id")
    return f"{INTAKE_DIR}/{task_id}.json"


def intake_entry(task):
    """What the New test page shows of a task and the worker gets: its cases are parsed as for the Today list."""
    cases, _, _ = parse_cases(task.get("markdown_description") or task.get("text_content") or "")
    tid = str(task.get("id") or "")
    cid = task.get("custom_id")
    lst = task.get("list") if isinstance(task.get("list"), dict) else {}
    return {"task_id": tid, "custom_id": cid if isinstance(cid, str) else None,
            "title": clean_text(str(task.get("name") or "")), "status": _status(task), "priority": _priority(task),
            "url": _task_url(tid), "list": clean_text(str(lst.get("name") or "")) or None,
            "cases": [{"n": c["n"], "text": c["text"]} for c in cases]}


def _for_link(msg):
    """A ClickUp message worded for the New test link field, which has no Refresh button."""
    for old, new in (("Then press Refresh.", "Then paste the link again."), ("then press Refresh", "then paste the link again"),
                     ("Try Refresh again later.", "Check the link, or try again later."),
                     ("Try Refresh in a minute.", "Try again in a minute."), ("Try Refresh again.", "Try again.")):
        msg = msg.replace(old, new)
    return msg


def resolve_intake(root, writer, ref, opener=None):
    """Read the one task ref names (GET only, clickup_read), save a redacted copy at qa/cache/intake/<task id>.json and
    return its INTAKE_KEYS. PrepError: ref names no task, or a task that is never read (refused before any request).
    clickup_read.ClickUpError: no token (kind no_token, with the no-token message) or what ClickUp answered."""
    tid, cid = task_ref(ref)
    if tid in NEVER_READ or cid in NEVER_READ:
        raise PrepError("that task is never read")
    secrets_path = os.path.join(root, "qa", "secrets.env")
    params = [("include_markdown_description", "true")]
    if cid:
        params += [("custom_task_ids", "true"), ("team_id", TEAM_ID)]
    try:
        task = clickup_read.get_json(f"/task/{tid or cid}", params, clickup_read.read_token(secrets_path), opener)
    except clickup_read.ClickUpError as e:
        msg = clickup_read.no_token_message(secrets_path) if e.kind == "no_token" else e.message
        if e.kind == "auth":  # a link from another workspace answers 401 too
            msg = "ClickUp would not give this task: check that the link is from the Wingtra workspace. " + msg
        raise clickup_read.ClickUpError(e.kind, _for_link(msg)) from None
    entry = intake_entry(task)
    if not TASK_KEY_RE.match(entry["task_id"]):
        raise clickup_read.ClickUpError("bad_data", "ClickUp sent a task without an id. Try again.")
    if entry["task_id"] in NEVER_READ or entry["custom_id"] in NEVER_READ:
        raise PrepError("that task is never read")
    # the description stays in the saved copy only (not shown on the page): the worker plans from it instead of reading
    # ClickUp again, whose shared daily MCP limit ran out on 2026-10-30 and 11-02
    desc = redact.redact(str(task.get("markdown_description") or task.get("text_content") or "").strip())
    if len(desc) > DESC_MAX:  # review 2026-11-11: a cut text ends with '…', so the worker can tell (it was cut silently)
        desc = desc[:DESC_MAX - 1].rstrip() + "…"
    rec = redact.redact_obj(dict(entry, description=desc, kind="intake", schema=1, fetched=utc_now()))
    writer._write(intake_rel(entry["task_id"]), _dump(rec), "replace")
    return {k: rec[k] for k in INTAKE_KEYS}


def load_intake(root, task_id):
    """The saved copy of a task read from its link on the New test page, or None."""
    if not isinstance(task_id, str) or not TASK_KEY_RE.match(task_id) or task_id in NEVER_READ:
        return None
    obj = _read_json(root, intake_rel(task_id))
    return obj if obj and obj.get("kind") == "intake" and obj.get("task_id") == task_id else None


# ---- CLI for the background engine (qa-prep skill) -------------------------------------------------------------------

USAGE = """python3 -I qa/app/plan.py [--root <workspace>] refresh              read ClickUp (GET only), write today's list
python3 -I qa/app/plan.py show [--day D]                                  today's (or day D's) list, as JSON
python3 -I qa/app/plan.py status <day> running|done|failed [--note T]     the pre-test notes' progress, shown in the app
python3 -I qa/app/plan.py put-prep <day> < prep.json                      check and save the pre-test notes
python3 -I qa/app/plan.py prep-fresh <day>                                whether the notes still fit (cases, list)"""


def main(argv):
    import argparse
    import writes
    ap = argparse.ArgumentParser(prog="plan.py", description="Today list and pre-test notes.", epilog=USAGE,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=os.path.dirname(os.path.dirname(APP_DIR)))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("refresh")
    p = sub.add_parser("show")
    p.add_argument("--day")
    p = sub.add_parser("status")
    p.add_argument("day")
    p.add_argument("state", choices=PREP_STATES)
    p.add_argument("--note")
    p = sub.add_parser("put-prep")
    p.add_argument("day")
    p = sub.add_parser("prep-fresh")
    p.add_argument("day")
    a = ap.parse_args(argv)
    root = os.path.abspath(a.root)
    writer = writes.Writer(root)
    try:
        if a.cmd == "refresh":
            out = refresh(root, writer)
            snap = out.get("snapshot") or {}
            res = {"ok": out["ok"], "kind": out.get("kind"), "message": out.get("message"), "day": snap.get("day"),
                   "tests": [{"task_id": t["task_id"], "title": t["title"], "cases": len(t["cases"])}
                             for t in snap.get("tests", [])], "hidden": snap.get("hidden")}
        elif a.cmd == "show":
            res = load(root, a.day) or {"error": "no list for that day", "status": 404}
        elif a.cmd == "status":
            res = set_prep_status(root, writer, a.day, a.state, a.note)
        elif a.cmd == "prep-fresh":
            snapshot_rel(a.day)  # checks the day
            res = prep_fresh(root, a.day)
        else:
            obj = json.loads(sys.stdin.read())
            clean = put_prep(root, writer, a.day, obj)
            res = {"ok": True, "day": a.day, "tests": len(clean["tests"]),
                   "cases": sum(len(t["cases"]) for t in clean["tests"])}
    except (PrepError, ValueError) as e:
        print(json.dumps({"error": str(e), "status": 400}))
        return 1
    except writes.WriteError as e:
        print(json.dumps({"error": e.message, "status": e.status}))
        return 1
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0 if not (isinstance(res, dict) and res.get("error")) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
