"""Case record validation (qa/schema/SCHEMA.md sections 1 and 9, qa/app/DESIGN.md section 4).

validate_case(obj) returns a list of problems ("<field>: <what is wrong>"); an empty list means valid. It accepts
the engine's cases (active or retired, with helpers) and the drafts the app writes (status "draft", steps with
helper null, a top-level "expected"). Only an active case must have at least one step and a helper or read_js on
every step: a draft is not runnable yet and a retired case is never run (the app may retire a draft). Unknown
top-level and step keys are allowed: SCHEMA.md grows by addition.

requirement_sha256(text): sha256 of the NFC-normalised text with trailing whitespace stripped from every line,
lines joined with "\n" and nothing added or removed at the ends. This reproduces requirement.sha256 of every case
in qa/cases and qa/runner/tests/cases.
"""
import hashlib
import os
import re
import shlex
import sys
import unicodedata

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)
sys.path.insert(0, os.path.join(os.path.dirname(APP_DIR), "runner"))

AREAS = ("UPG", "PLAN", "FLY", "PLANE", "DEV", "SAK", "MAP", "SYNC", "OFFL", "TERR", "FLEET", "SET", "LTA", "PROC")
HARNESS_AREA = "HARNESS"
TARGETS = ("tablet", "web", "both")
STATUSES = ("active", "retired", "draft")
AUTHORS = ("runner", "claude", "user")
SOURCE_TYPES = ("clickup_task", "clickup_comment", "spec_doc", "user_chat")
GATES = ("write_list", "account_wide", "handover", "install", "clickup_write", "cloud_write", "mocklink_ota")
LINKS = ("none", "mocklink", "usb_radio", "udp_sitl", "real_drone")
ASSERT_KINDS = {"scripted": "expr", "judged": "question"}
TITLE_MAX = 80

CASE_ID_RE = re.compile(r"^([A-Z]{2,10})-(\d{3})\Z")
TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z\Z")
HEX64_RE = re.compile(r"^[0-9a-f]{64}\Z")
# where the engine copies a fixture on a tablet: the same rule as qa/runner/fixtures.py tablet_dest
DEVICE_PATH_RE = re.compile(r"^/sdcard/Download/(?:[A-Za-z0-9][A-Za-z0-9._-]{0,150}/){0,5}[A-Za-z0-9][A-Za-z0-9._-]{0,150}\Z")
HEX40_RE = re.compile(r"^[0-9a-f]{40}\Z")
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\Z")
PRECOND_ID_RE = re.compile(r"^P\d+\Z")
CASE_FILE_RE = re.compile(r"^qa/(cases|runner/tests/cases)/[A-Z]{2,10}-\d{3}(\.c\d+)?\.json\Z")


def normalise_requirement(text):
    t = unicodedata.normalize("NFC", text)
    return "\n".join(line.rstrip() for line in t.split("\n"))


def requirement_sha256(text):
    return hashlib.sha256(normalise_requirement(text).encode("utf-8")).hexdigest()


def _is_int(x):
    return isinstance(x, int) and not isinstance(x, bool)


def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _nonempty_str(x):
    return isinstance(x, str) and x.strip() != ""


def _str_list(x):
    return isinstance(x, list) and all(isinstance(i, str) for i in x)


def _check_header(obj, p):
    if obj.get("kind") != "case":
        p.append("kind: must be \"case\"")
    if obj.get("schema") != 1 or not _is_int(obj.get("schema")):
        p.append("schema: must be 1")
    cid = obj.get("id")
    if not isinstance(cid, str) or not CASE_ID_RE.match(cid):
        p.append("id: must look like AREA-nnn")
    if not isinstance(obj.get("created"), str) or not TIME_RE.match(obj["created"]):
        p.append("created: must be an ISO-8601 UTC time ending in Z")
    if obj.get("author") not in AUTHORS:
        p.append(f"author: must be one of {', '.join(AUTHORS)}")
    if "supersedes" not in obj:
        p.append("supersedes: missing (null for a first version)")
    sup = obj.get("supersedes")
    if isinstance(sup, str):
        if sup != cid:
            p.append("supersedes: must name the same case id")
    elif isinstance(sup, dict):
        if sup.get("id") != cid:
            p.append("supersedes.id: must be the same case id")
        if not isinstance(sup.get("file"), str) or not CASE_FILE_RE.match(sup["file"]):
            p.append("supersedes.file: must be a case file under qa/cases or qa/runner/tests/cases")
        if not isinstance(sup.get("file_sha256"), str) or not HEX64_RE.match(sup["file_sha256"]):
            p.append("supersedes.file_sha256: must be 64 lowercase hex")
        if not _nonempty_str(sup.get("why")):
            p.append("supersedes.why: must say why")
    elif sup is not None:
        p.append("supersedes: must be null, a case id or {id, file, file_sha256, why}")


def _check_area(obj, p):
    area, cid, harness = obj.get("area"), obj.get("id"), obj.get("harness")
    if "harness" in obj and not isinstance(harness, bool):
        p.append("harness: must be true or false")
    if harness is True or area == HARNESS_AREA:
        if area != HARNESS_AREA or harness is not True:
            p.append("area: a runner self-test has area HARNESS and harness true")
        return
    if area not in AREAS:
        p.append(f"area: must be one of {', '.join(AREAS)}")
    elif isinstance(cid, str) and CASE_ID_RE.match(cid) and CASE_ID_RE.match(cid).group(1) != area:
        p.append("id: prefix must equal the area")


def _check_requirement(req, p):
    if not isinstance(req, dict):
        p.append("requirement: must be {text, sha256, source}")
        return
    text = req.get("text")
    if not _nonempty_str(text):
        p.append("requirement.text: must be non-empty text")
    elif req.get("sha256") != requirement_sha256(text):
        p.append("requirement.sha256: does not match requirement.text")
    src = req.get("source")
    if not isinstance(src, dict):
        p.append("requirement.source: must be {type, ref, locator?, requester?}")
        return
    if src.get("type") not in SOURCE_TYPES:
        p.append(f"requirement.source.type: must be one of {', '.join(SOURCE_TYPES)}")
    if not _nonempty_str(src.get("ref")):
        p.append("requirement.source.ref: must be non-empty")
    for k in ("locator", "requester", "cache", "custom_id"):
        if k in src and src[k] is not None and not isinstance(src[k], str):
            p.append(f"requirement.source.{k}: must be text")
    if src.get("fetched") is not None and not (isinstance(src["fetched"], str) and TIME_RE.match(src["fetched"])):
        p.append("requirement.source.fetched: must be an ISO-8601 UTC time")


def _check_routing(items, p):
    if not isinstance(items, list):
        p.append("routing: must be a list")
        return
    for i, r in enumerate(items):
        where = f"routing[{i}]"
        if not isinstance(r, dict):
            p.append(f"{where}: must be a git pointer")
            continue
        if r.get("t") != "git":
            p.append(f"{where}.t: must be \"git\"")
        if not isinstance(r.get("ref"), str) or not HEX40_RE.match(r["ref"]):
            p.append(f"{where}.ref: must be a 40-hex commit")
        if not _nonempty_str(r.get("path")):
            p.append(f"{where}.path: must be non-empty")
        if not _is_int(r.get("line")) or r["line"] < 1:
            p.append(f"{where}.line: must be a line number")
        if "symbol" in r and not isinstance(r["symbol"], str):
            p.append(f"{where}.symbol: must be text")


def _check_expected_differences(items, p):
    if not isinstance(items, list):
        p.append("expected_differences: must be a list")
        return
    for i, d in enumerate(items):
        if not isinstance(d, dict) or not all(isinstance(d.get(k), str) for k in ("what", "tablet", "web")):
            p.append(f"expected_differences[{i}]: must be {{what, tablet, web, src}}")
        elif "src" not in d:
            p.append(f"expected_differences[{i}].src: missing")


def _check_preconditions(items, p):
    if not isinstance(items, list):
        p.append("preconditions: must be a list")
        return
    seen = set()
    for i, c in enumerate(items):
        where = f"preconditions[{i}]"
        if not isinstance(c, dict):
            p.append(f"{where}: must be {{id, text, check, applies_to}}")
            continue
        cid = c.get("id")
        if not isinstance(cid, str) or not PRECOND_ID_RE.match(cid):
            p.append(f"{where}.id: must look like P1")
        elif cid in seen:
            p.append(f"{where}.id: {cid} is used twice")
        seen.add(cid)
        if not _nonempty_str(c.get("text")):
            p.append(f"{where}.text: must be non-empty")
        if c.get("check") is not None and not isinstance(c["check"], str):
            p.append(f"{where}.check: must be text or null")
        if c.get("applies_to") not in TARGETS:
            p.append(f"{where}.applies_to: must be one of {', '.join(TARGETS)}")


def _check_step(s, i, draft, p):
    where = f"steps[{i}]"
    if not isinstance(s, dict):
        p.append(f"{where}: must be {{n, text, helper, gates}}")
        return
    if s.get("n") != i + 1 or not _is_int(s.get("n")):
        p.append(f"{where}.n: must be {i + 1}")
    if not _nonempty_str(s.get("text")):
        p.append(f"{where}.text: must be non-empty")
    helper = s.get("helper")
    if helper is not None and not _nonempty_str(helper):
        p.append(f"{where}.helper: must be a command or null")
    if not draft and helper is None and not _nonempty_str(s.get("read_js")):
        p.append(f"{where}: needs a helper or read_js unless the case is a draft or retired")
    gates = s.get("gates")
    if not isinstance(gates, list) or any(g not in GATES for g in gates):
        p.append(f"{where}.gates: must be a list of {', '.join(GATES)}")
    if "assert" in s:
        a = s["assert"]
        field = ASSERT_KINDS.get(a.get("kind")) if isinstance(a, dict) else None
        if field is None or not _nonempty_str(a.get(field)):
            p.append(f"{where}.assert: must be {{kind: scripted, expr}} or {{kind: judged, question}}")
    if "on" in s and s["on"] not in TARGETS:
        p.append(f"{where}.on: must be one of {', '.join(TARGETS)}")
    if "save_as" in s and not (isinstance(s["save_as"], str) and NAME_RE.match(s["save_as"])):
        p.append(f"{where}.save_as: must be a plain name")
    for k in ("read_js", "when_js"):
        if k in s and not _nonempty_str(s[k]):
            p.append(f"{where}.{k}: must be a non-empty expression")
    if "pause_s" in s and not (_is_num(s["pause_s"]) and 0 <= s["pause_s"] <= 600):
        p.append(f"{where}.pause_s: must be 0-600 seconds")
    if "always" in s and not isinstance(s["always"], bool):
        p.append(f"{where}.always: must be true or false")


def _check_steps(items, draft, p):
    if not isinstance(items, list):
        p.append("steps: must be a list")
        return
    if not draft and not items:
        p.append("steps: an active case needs at least one step")
    for i, s in enumerate(items):
        _check_step(s, i, draft, p)


# Steps that read or type must wait for the page first (2026-11-02): 6 false FAILs came from a read or a typed name
# right after a tap that waited for nothing (TERR-005 c1 runs, PLAN-001-03). Checked on cases created from this day on
# whose steps differ from the version they supersede; older versions, and edits that keep the steps, run as they are.
SETTLE_FROM = "2026-11-02"
VERBS = {"probe", "ready", "read", "find", "tap", "type", "key", "sys-tap", "choose-file", "shot", "wait",
         "wifi", "map-tap"}  # run.VERBS
ACTIONS = ("tap", "sys-tap", "key", "choose-file", "wifi", "map-tap")  # change the page
USES = ("type", "read")                             # plus any read_js step: depend on the page being ready
ALWAYS_TRUE = {"js:true", "js:1", "js:!0", "js:!!1", "js:!false"}  # expects that wait for nothing
UNTIL_OFFLINE = "until-offline"  # a wifi off step's --until-offline waits for the page (the app's offline indicator)


def _helper(s):
    """(verb, expects) of a step's act.py helper as run.helper_call splits it, (None, []) without one or when it is not
    a command. A wifi step's --until-offline counts as an expect (UNTIL_OFFLINE)."""
    h = s.get("helper")
    if not isinstance(h, str):
        return None, []
    try:
        args = shlex.split(h)
    except ValueError:
        return None, []
    while args and args[0] not in VERBS:  # act.py, its path, {target} and options before the verb
        args = args[1:]
    if not args:
        return None, []
    expects = [args[i + 1] for i, a in enumerate(args[:-1]) if a == "--expect"]
    expects += [a.split("=", 1)[1] for a in args if a.startswith("--expect=")]
    if args[0] == "wifi" and "--until-offline" in args:
        expects.append(UNTIL_OFFLINE)
    return args[0], expects


def _waits_for_nothing(expects):
    return not [e for e in expects if "".join(e.split()).lower() not in ALWAYS_TRUE]


def _step_actions(steps):
    return [(s.get("helper"), s.get("read_js"), s.get("always")) if isinstance(s, dict) else s for s in steps]


def _same_steps_as_superseded(obj):
    """True when the case supersedes a version under qa/ whose steps act the same (an edit of the title, texts or
    preconditions only)."""
    sup = obj.get("supersedes")
    f = sup.get("file") if isinstance(sup, dict) else None
    if not isinstance(f, str) or not CASE_FILE_RE.match(f):
        return False
    try:
        import json
        with open(os.path.join(os.path.dirname(os.path.dirname(APP_DIR)), f)) as fh:
            old = json.load(fh)
    except (OSError, ValueError):
        return False
    return isinstance(old, dict) and _step_actions(old.get("steps") or []) == _step_actions(obj.get("steps") or [])


def _uses(s, verb):
    return verb in USES or _nonempty_str(s.get("read_js"))


def _check_settled(items, p):
    prev = None  # (n, verb, expects) of the last step that is not a screenshot
    # clean-up does not decide the verdict
    live = [(i, s) for i, s in enumerate(items) if isinstance(s, dict) and not s.get("always")]
    for k, (i, s) in enumerate(live):
        verb, expects = _helper(s)
        if _uses(s, verb) and prev and prev[1] in ACTIONS and _waits_for_nothing(prev[2]):
            p.append(f"steps[{i}] (step {s.get('n')}): {'types' if verb == 'type' else 'reads'} right after step {prev[0]} ({prev[1]}), "
                     "which waits for nothing; put a wait before it for what this step needs: the element and its "
                     "value (e.g. the form's default name), not only the element")
        if verb == "wifi" and _waits_for_nothing(expects):
            # 2026-11-03: wifi waits only for the tablet's Wi-Fi state, not for the app; without an --expect (or
            # --until-offline) the next step that is not a screenshot must be a wait. A read or type there is reported
            # by the rule above.
            nxt = next((x for x in live[k + 1:] if _helper(x[1])[0] != "shot"), None)
            nverb, nexp = _helper(nxt[1]) if nxt else (None, [])
            if not (nverb == "wait" and not _waits_for_nothing(nexp)) and not (nxt and _uses(nxt[1], nverb)):
                p.append(f"steps[{i}] (step {s.get('n')}): wifi waits only for the tablet's Wi-Fi state, not for the app; "
                         "give it an --expect for what the next steps need (or --until-offline), or put a wait right "
                         "after it")
        if verb != "shot":
            prev = (s.get("n"), verb, expects)


# Steps from the task (2026-11-05, qa-worker 1b step 5; tests/2026-11-04-results-redesign/SPEC.md "CASE RULE"): a case
# written or rewritten from New test lists its "parts" first, the test as a person would do it by hand, in order: 1-8
# {n, text (at most 100 characters), quote (the task's words the part carries out: a text or a list of texts), check
# (true when it checks a result its quote states)}. Every step but always:true clean-up names its "part"; only a check
# part's steps read the page (read_js, an act.py read) or assert. Checked under the settle rule's guard on active cases
# from New test (requirement.source user_chat, locator "New test description, ..."); since 2026-11-05 (wingmate-debug
# FINDINGS #6 and #7, parts_due) also a version that only adds parts, and at plan time every case written or rewritten
# for a New test plan whatever its source (validate_case for_new_test); worker._intake_plan also checks each quote
# against what the person gave.
PARTS_FROM = "2026-11-05"
PARTS_MAX, PART_TEXT_MAX = 8, 100
NEW_TEST_LOCATOR = "New test description"


def _written_since(obj, day):
    """The settle rule's guard: an active case created from day on whose steps differ from the version it supersedes."""
    return (obj.get("status") == "active" and isinstance(obj.get("created"), str) and obj["created"] >= day
            and isinstance(obj.get("steps"), list) and not _same_steps_as_superseded(obj))


def parts_due(obj):
    """The parts rule's guard: an active case created from PARTS_FROM on whose steps differ from the version it
    supersedes, or that lists "parts" or names a "part" on a step (2026-11-05, wingmate-debug FINDINGS #7: a version
    that only adds parts or part numbers keeps the step actions and is checked too); an edit of the title or the texts
    only is not."""
    if not (obj.get("status") == "active" and isinstance(obj.get("created"), str) and obj["created"] >= PARTS_FROM
            and isinstance(obj.get("steps"), list)):
        return False
    return "parts" in obj or any(isinstance(s, dict) and "part" in s for s in obj["steps"]) \
        or not _same_steps_as_superseded(obj)


def _from_new_test(obj):
    req = obj.get("requirement")
    src = req.get("source") if isinstance(req, dict) else None
    return isinstance(src, dict) and src.get("type") == "user_chat" and isinstance(src.get("locator"), str) \
        and src["locator"].startswith(NEW_TEST_LOCATOR)


def _reads(s):
    """True for a step that reads the page or asserts: read_js, an act.py read, an assert."""
    return _nonempty_str(s.get("read_js")) or "assert" in s or _helper(s)[0] == "read"


def _check_parts(obj, p):
    parts = obj.get("parts")
    if not isinstance(parts, list) or not 1 <= len(parts) <= PARTS_MAX:
        p.append(f"parts: a case from New test lists 1-{PARTS_MAX} parts first, the test as a person would do it by "
                 "hand: {n, text, quote, check}")
        return
    checks = set()
    for i, part in enumerate(parts):
        where = f"parts[{i}] (part {i + 1})"
        if not isinstance(part, dict):
            p.append(f"{where}: must be {{n, text, quote, check}}")
            continue
        if part.get("n") != i + 1 or not _is_int(part.get("n")):
            p.append(f"{where}.n: must be {i + 1}")
        if not _nonempty_str(part.get("text")) or len(part["text"]) > PART_TEXT_MAX:
            p.append(f"{where}.text: 1-{PART_TEXT_MAX} characters, what a person does in this part")
        q = part.get("quote")
        if not (_nonempty_str(q) or isinstance(q, list) and q and all(_nonempty_str(x) for x in q)):
            p.append(f"{where}.quote: the task's own words this part carries out, a text or a list of texts")
        if not isinstance(part.get("check"), bool):
            p.append(f"{where}.check: true or false")
        elif part["check"]:
            checks.add(i + 1)
    for i, s in enumerate(obj["steps"]):
        if not isinstance(s, dict):
            continue
        where, n = f"steps[{i}] (step {s.get('n')})", s.get("part")
        if s.get("always") is True:
            if n is not None:
                p.append(f"{where}.part: clean-up (always: true) belongs to no part")
            elif _reads(s):
                p.append(f"{where}: clean-up (always: true) never reads the page or asserts; only a check part does")
        elif not _is_int(n) or not 1 <= n <= len(parts):
            p.append(f"{where}.part: the part it belongs to, 1-{len(parts)}; only clean-up (always: true) has none")
        elif _reads(s) and n not in checks:
            p.append(f"{where}: reads the page or asserts in part {n}, which is not a check part; only a check part's "
                     "steps read or assert, and only what its quote states")


def _check_wifi_targets(obj, p):
    """act.py wifi is for tablets (W044 refuses it): in a case that also runs on web, a wifi step says on: tablet."""
    if obj.get("targets") == "tablet" or not isinstance(obj.get("steps"), list):
        return
    for i, s in enumerate(obj["steps"]):
        if isinstance(s, dict) and s.get("on", "both") != "tablet" and _helper(s)[0] == "wifi":
            p.append(f"steps[{i}].on: wifi is for tablets; in a case that also runs on web the step says on: tablet")


def _check_fixtures(items, p):
    if not isinstance(items, list):
        p.append("fixtures: must be a list")
        return
    for i, f in enumerate(items):
        if not isinstance(f, dict) or not _nonempty_str(f.get("name")) or not _nonempty_str(f.get("path")):
            p.append(f"fixtures[{i}]: must be {{name, path, sha256, delivery}}")
        elif not isinstance(f.get("sha256"), str) or not HEX64_RE.match(f["sha256"]):
            p.append(f"fixtures[{i}].sha256: must be 64 lowercase hex")
        elif "device_path" in f and not (isinstance(f["device_path"], str) and DEVICE_PATH_RE.match(f["device_path"])
                                         and f["device_path"].endswith("/" + f["name"])):
            # where the engine copies it on a tablet (qa/runner/fixtures.py, engine piece 2)
            p.append(f"fixtures[{i}].device_path: must lie under /sdcard/Download/ and end in /{f['name']}")


def _check_covered_paths(items, p):
    if not _str_list(items):
        p.append("covered_paths: must be a list of globs")
        return
    for i, g in enumerate(items):
        segs = g.split("/")
        if not g.startswith(("apps/", "packages/")) or ".." in segs or "\x00" in g or "\\" in g or g != g.strip():
            p.append(f"covered_paths[{i}]: must be a glob under apps/ or packages/")


def _check_needs(n, p):
    if not isinstance(n, dict):
        p.append("needs: must be {link, usb_port, account_lock, flags, drone_hw}")
        return
    if n.get("link") not in LINKS:
        p.append(f"needs.link: must be one of {', '.join(LINKS)}")
    for k in ("usb_port", "account_lock"):
        if not isinstance(n.get(k), bool):
            p.append(f"needs.{k}: must be true or false")
    if not _str_list(n.get("flags")):
        p.append("needs.flags: must be a list of flag names")
    if "drone_hw" not in n or n["drone_hw"] not in (4, 5, None) or isinstance(n["drone_hw"], bool):
        p.append("needs.drone_hw: must be 4, 5 or null")


def _check_optional(obj, p):
    if "expected" in obj and not _nonempty_str(obj["expected"]):
        p.append("expected: must be non-empty text")
    for k in ("steps_note",):
        if k in obj and not isinstance(obj[k], str):
            p.append(f"{k}: must be text")
    for k in ("flags_watch", "forbidden"):
        if k in obj and not _str_list(obj[k]):
            p.append(f"{k}: must be a list of text")
    if "differential" in obj:
        d = obj["differential"]
        if not isinstance(d, dict) or not _str_list(d.get("values")):
            p.append("differential: must be {values: [names], flags?, api_js?, api_endpoint?}")
        else:
            if "flags" in d and not _str_list(d["flags"]):
                p.append("differential.flags: must be a list of flag names")
            for k in ("api_js", "api_endpoint"):
                if k in d and not _nonempty_str(d[k]):
                    p.append(f"differential.{k}: must be non-empty text")


REQUIRED = ("kind", "schema", "id", "created", "author", "title", "area", "requirement", "targets", "routing",
            "expected_differences", "preconditions", "steps", "fixtures", "covered_paths", "needs", "status")


def validate_case(obj, for_new_test=False):
    """Problems with a case record, [] when it is valid. for_new_test: the case was written or rewritten for a New test
    plan (worker._check_written), so the parts rule applies whatever its requirement source says (2026-11-05,
    wingmate-debug FINDINGS #6: a rewrite of a ClickUp-sourced case too); else only to a case from New test."""
    if not isinstance(obj, dict):
        return ["case: must be a JSON object"]
    p = [f"{k}: missing" for k in REQUIRED if k not in obj]
    _check_header(obj, p)
    _check_area(obj, p)
    title = obj.get("title")
    if not _nonempty_str(title) or len(title) > TITLE_MAX:
        p.append(f"title: must be 1-{TITLE_MAX} characters")
    if obj.get("status") not in STATUSES:
        p.append(f"status: must be one of {', '.join(STATUSES)}")
    if obj.get("targets") not in TARGETS:
        p.append(f"targets: must be one of {', '.join(TARGETS)}")
    _check_requirement(obj.get("requirement"), p)
    _check_routing(obj.get("routing", []), p)
    _check_expected_differences(obj.get("expected_differences", []), p)
    _check_preconditions(obj.get("preconditions", []), p)
    # a draft is not runnable yet and a retired case is never run: only an active case needs steps and helpers
    _check_steps(obj.get("steps", []), obj.get("status") in ("draft", "retired"), p)
    settled = _written_since(obj, SETTLE_FROM)
    if settled:
        _check_settled(obj["steps"], p)
    if parts_due(obj) and (for_new_test or _from_new_test(obj)):
        _check_parts(obj, p)
    _check_wifi_targets(obj, p)
    _check_fixtures(obj.get("fixtures", []), p)
    _check_covered_paths(obj.get("covered_paths", []), p)
    _check_needs(obj.get("needs"), p)
    _check_optional(obj, p)
    return p


if __name__ == "__main__":
    import json

    bad = 0
    for path in sys.argv[1:]:
        with open(path, encoding="utf-8") as f:
            problems = validate_case(json.load(f))
        print(json.dumps({"file": path, "problems": problems}, ensure_ascii=False))
        bad += bool(problems)
    sys.exit(1 if bad else 0)
