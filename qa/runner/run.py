"""The QA runner: one case on several targets in parallel, every device action through act.py.

python3 -I qa/runner/run.py <case id> --targets TA5,ET65,W044 --campaign <YYYY-MM-DD-slug> [--nn 01]

Before anything starts, every run's prediction qa/runs/<campaign>/pred/PR-<RUN>.json must be hashed in
the ledger; without it nothing runs. A case refuse_case rejects (not active, no steps, G3 test data) and a run
whose record already exists do not start either. Then, per target in its own thread:
  1. exclusive lock on the target (and on the account when the case needs it); the pane shows it BUSY
  2. clock offset and battery; start marker (tablets: logcat tag WTQA); web: HAR and console recorder
  3. identity probe, build pin (bundle version, git hash, env, native versionCode), flag snapshot
  4. the case's steps for that platform: act.py calls under QA_RUNNER=1, each with its identity checks,
     readback, and ledger reference
  5. end screenshot (before the clean-up steps, so it shows where the run stopped), the clean-up steps, then the Wi-Fi
     safety net (2026-11-03): a tablet run that called act.py wifi off in any step calls wifi on, whatever the verdict
     and also when the attempt stopped early or crashed, and a failure is a note on the attempt; end marker, clock and
     battery; evidence: tablet logcat since the start and the Qt log, web HAR and console; a sha256 manifest; the run
     record, hashed; unlock
Step 2 comes after act.py ready (2026-11-02): a target that is not ready is BLOCKED by ready, not a crash in a clock read.
For a case with targets "both", a differential record compares the runs after all of them end.
While a target runs, state/progress/<lock name>.json says which step it is on (qa/app/DESIGN.md 6), for the
app's Live view; it is written atomically and a failing write never touches the run.

Verdicts (SCHEMA.md 4): a failed identity check, readback or tool call is ERROR (the harness says nothing
about the app); a write-list step act.py refuses under the runner is BLOCKED by a handover, with the
command handed back to Claude; a failed scripted assertion is FAIL; a judged step leaves the verdict
open ("judgement_pending") for Claude to decide in a superseding record. Before the first case step only, a
setup problem the person fixes (act.py's "setup": true, a device not ready, the account, a deciding flag) is
BLOCKED with what to do; from the first case step on, the rules above apply unchanged.
"""
import argparse
import ast
import datetime
import fcntl
import glob
import json
import math
import os
import re
import shlex
import subprocess
import sys
import threading
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from records import QA_DIR, WORKSPACE, append, canonical, hash_record, iter_ledger, now_utc, rel, sha256_bytes, sha256_file, side_effect, write_record  # noqa: E402
from act import (CLOUD_WRITE_TESTID, CLOUD_WRITE_TEXT, FLIGHT_SAVE_TEXT, G3_TESTID, G3_TEXT, OTA_TESTID, OTA_TEXT,  # noqa: E402
                 READBACK_TIMEOUT_S, SITE_PATH, WIFI_TIMEOUT_S, bare_testid, handback_env, norm_label,
                 parser as act_parser)
from redact import redact_obj  # noqa: E402

STATE = os.path.join(QA_DIR, "runner", "state")
PROGRESS = os.path.join(STATE, "progress")
ACT = os.path.join(QA_DIR, "runner", "act.py")
CASE_DIRS = (os.path.join(QA_DIR, "cases"), os.path.join(QA_DIR, "runner", "tests", "cases"))
WINGTRACLOUD = os.path.join(WORKSPACE, "WingtraCloud")
PKG = "com.wingtra.cloud.app"

# Build identity read from the running bundle, no navigation (selector research at 84d3721: the entry
# chunk inlines the 40-hex GIT_HASH in ReportingModal's "App info"; window.env carries the env;
# LiveUpdate.getVersionCode is the About page's "native layer version", Android only).
BUILD_JS = r"""(async () => {
  const s = document.querySelector('script[type=module][src]');
  const t = s ? await (await fetch(s.src)).text() : '';
  const h = (t.match(/"Git hash":\s*"([0-9a-f]{40})"/) || [])[1] || null;
  const v = (t.match(/"App info":\s*\{\s*Version:\s*"([^"]+)"/) || [])[1] || null;
  const platform = window.Capacitor && window.Capacitor.getPlatform ? window.Capacitor.getPlatform() : 'web';
  let vc = null;
  if (platform === 'android') vc = (await window.Capacitor.Plugins.LiveUpdate.getVersionCode()).versionCode;
  return {entry: s ? s.src.split('/').pop() : null, version: v, git_hash: h,
          env: window.env ? window.env.VITE_APP_ENVIRONMENT : null, version_code: vc, platform};
})()"""
# Who is logged in, without the identity leaving the page: the access token's email, hashed in the page,
# and its tenant. Compared with the engine account (email from qa/secrets.env, tenant from the accounts record).
# F5 (2026-11-02): whether the session has expired, from the access token's exp claim; only true or false leaves the
# page. An access token past its exp is not yet an expired session: the app refreshes it itself while it
# holds a refresh token issued less than REFRESH_TOKEN_LIFETIME days ago (WingtraCloud 261b2d9 apolloClient.ts
# forceRefreshToken, axiosProvider.ts refreshAuthLogic; 14 days unless the build sets VITE_REFRESH_TOKEN_LIFETIME,
# variables.ts:70). So the session counts as expired when the access token is and the app cannot refresh it.
ACCOUNT_JS = r"""(async () => {
  const t = localStorage.getItem('accessToken') || '';
  const p = t.split('.')[1];
  if (!p) return {loggedIn: false};
  const j = JSON.parse(atob(p.replace(/-/g, '+').replace(/_/g, '/')));
  const bytes = new TextEncoder().encode(String(j.email || '').trim().toLowerCase());
  const d = await crypto.subtle.digest('SHA-256', bytes);
  const exp = Number(j.exp) || 0;
  const tokenExpired = exp > 0 && exp * 1000 <= Date.now();
  const issued = Number(localStorage.getItem('refreshTokenIssuedAt')) || Date.now();
  const days = Number((window.env || {}).VITE_REFRESH_TOKEN_LIFETIME) || 14;
  const refreshable = !!localStorage.getItem('refreshToken') && issued + days * 86400000 > Date.now();
  return {loggedIn: true, email_sha256: [...new Uint8Array(d)].map(b => b.toString(16).padStart(2, '0')).join(''),
          tenantId: j.tenantId || null, sessionExpired: tokenExpired && !refreshable};
})()"""
FLAGS_JS = r"""(() => { try { return JSON.parse(localStorage.getItem('unleashFlags') || 'null')
  .filter(f => f.enabled).map(f => f.name).sort(); } catch (e) { return null; } })()"""


def load_json(p, default=None):
    try:
        return json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        return default


def engine():
    return load_json(os.path.join(QA_DIR, "runner", "engine.json"), {})


def profiles():
    out = {}
    for f in sorted(glob.glob(os.path.join(QA_DIR, "devices", "*.json")),
                    key=lambda f: int((re.search(r"\.v(\d+)\.json$", f) or [0, 0])[1])):
        d = load_json(f, {})
        if d.get("kind") == "device":
            out[d["tag"]] = dict(d, _path=f)
    return out


def engine_account(eng):
    """The engine account: its record (newest accounts version) plus the sha256 of its email from qa/secrets.env."""
    files = sorted(glob.glob(os.path.join(QA_DIR, "devices", "accounts*.json")),
                   key=lambda f: int((re.search(r"accounts\.v(\d+)\.json$", f) or [0, 0])[1]))
    acc = load_json(files[-1], {}) if files else {}
    email = None
    try:
        for line in open(os.path.join(QA_DIR, "secrets.env"), encoding="utf-8"):
            if line.strip().startswith(acc.get("email_var", "CT_TEST_EMAIL") + "="):
                email = line.split("=", 1)[1].strip()
    except OSError:
        pass
    return {"label": acc.get("label"), "id": acc.get("id"), "tenant_id": acc.get("tenant_id"),
            "email_sha256": sha256_bytes(email.lower().encode()) if email else None}


def check_account(tag, run_id, eng):
    """(ok, detail, ledger ref): is the target logged in as the engine account? Nothing identifying is returned."""
    want = engine_account(eng)
    out = act(tag, ["read", ACCOUNT_JS], run_id)
    v = out.get("value") or {}
    if not out.get("ok"):
        return None, f"account read failed: {out.get('error')}", out.get("ledger")
    if not v.get("loggedIn"):
        return False, "nobody is logged in", out.get("ledger")
    if not want["email_sha256"]:
        return None, "the engine account's email is not in qa/secrets.env", out.get("ledger")
    same_user = v.get("email_sha256") == want["email_sha256"]
    same_tenant = want["tenant_id"] is None or v.get("tenantId") == want["tenant_id"]
    if same_user and same_tenant and v.get("sessionExpired") is True:
        return False, f"Log in again on {tag} (the session expired)", out.get("ledger")
    if same_user and same_tenant:
        return True, f"logged in as {want['label']}", out.get("ledger")
    return False, ("logged in as another user, not " + str(want["label"])) if not same_user else "the engine account in another tenant", out.get("ledger")


def load_case(cid):
    """The newest version of a case: <id>.c<N>.json (highest N) supersedes <id>.json."""
    for d in CASE_DIRS:
        versions = sorted(glob.glob(os.path.join(d, f"{cid}.c*.json")),
                          key=lambda f: int((re.search(r"\.c(\d+)\.json$", f) or [0, 0])[1]))
        p = versions[-1] if versions else os.path.join(d, f"{cid}.json")
        if os.path.exists(p):
            return load_json(p), p
    return None, None


VERBS = {"probe", "ready", "read", "find", "tap", "type", "key", "sys-tap", "choose-file", "shot", "wait", "wifi",
         "map-tap"}
# deliver is not a step verb: the runner copies a case's test files itself, before the first step (engine piece 2)
# act.py deliver's worst case per file: 4 checksums (act.DELIVER_SUM_S), the copy (DELIVER_COPY_S) and mkdir, mv, rm
DELIVER_PER_FILE_S = 1440
VALUE_OPTS = {"--expect", "--timeout", "--find-timeout", "--text", "--secret", "--nth", "--file"}
# Page JS that writes: an HTTP method other than GET, an HTTP client's write call, or a react-query mutation.
JS_WRITE = re.compile(r"""['"]?\bmethod['"]?\s*:\s*['"`]\s*(?:post|put|patch|delete)\b"""
                      r"""|\.open\s*\(\s*['"`](?:post|put|patch|delete)\b|\.(?:post|put|patch|delete)\s*\("""
                      r"""|\bmutate(?:Async)?\s*\(""", re.I)
# The OTA start as labelled on screen: Update now (testid ota-update-now, OtaUpdateConfirmationModal.tsx:70) and
# Continue (ota-wifi-off-continue, TurnOffWifiModal.tsx:57). Not on act.py's write list as labels of their own.
MOCKLINK_OTA_TEXT = OTA_TEXT | {"update now", "continue"}
# A scripted assert is evaluated in this process (review 2026-11-03). An empty __builtins__ is no sandbox: attribute
# chains such as ().__class__.__base__.__subclasses__() or a generator's gi_frame.f_back.f_globals reach run.py's own
# globals (os, subprocess, act), so an assert could run anything, past every write list and the gate. An assert is
# therefore a plain check over v, build, target and run: comparisons, and/or/not, + and -, subscripts, literals, if-else,
# comprehensions, and calls to ASSERT_CALLS by name; no attribute, lambda, :=, f-string or name starting with _ (all
# asserts in qa/cases on 2026-11-03 are). Anything else is a harness ERROR before eval; New test refuses it before Run.
ASSERT_CALLS = {"any", "all", "len", "sorted", "set"}
ASSERT_NODES = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.USub, ast.UAdd, ast.BinOp,
                ast.Add, ast.Sub, ast.Compare, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Is, ast.IsNot,
                ast.In, ast.NotIn, ast.IfExp, ast.Constant, ast.Name, ast.Load, ast.Store, ast.Subscript, ast.Slice,
                ast.List, ast.Tuple, ast.Set, ast.Dict, ast.Call, ast.keyword, ast.GeneratorExp, ast.ListComp,
                ast.SetComp, ast.comprehension)


def assert_problem(expr):
    """Why a scripted assert's expression is not a plain check (ASSERT_NODES, ASSERT_CALLS), or None."""
    if not isinstance(expr, str):
        return "not text"
    try:
        tree = ast.parse(expr, mode="eval")
    except (SyntaxError, ValueError) as e:
        return f"does not parse ({type(e).__name__})"
    for node in ast.walk(tree):
        if not isinstance(node, ASSERT_NODES):
            return f"uses {type(node).__name__}"
        if isinstance(node, ast.Name) and node.id.startswith("_"):
            return f"names {node.id}"
        if isinstance(node, ast.Call) and not (isinstance(node.func, ast.Name) and node.func.id in ASSERT_CALLS):
            return f"calls something other than {', '.join(sorted(ASSERT_CALLS))}"
    return None


def helper_call(s):
    """(verb, selector, --expect values) of a step's act.py helper; (None, None, []) without one."""
    args = shlex.split(s.get("helper") or "")
    while args and args[0] not in VERBS:  # act.py and {target}
        args = args[1:]
    if not args:
        return None, None, []
    sel, expects, i = None, [], 1
    while i < len(args):
        name, eq, val = args[i].partition("=")
        if name in VALUE_OPTS:
            if not eq:
                i += 1
                val = args[i] if i < len(args) else ""
            if name == "--expect":
                expects.append(val)
        elif not args[i].startswith("--") and sel is None:
            sel = args[i]
        i += 1
    return args[0], sel, expects


# F1 (2026-11-02): an act.py call is cut at its own waits plus a margin, not at a fixed 90 s, which ended steps that
# waited longer by themselves (R-20261030-W044-PLAN-001-01/-02) and made cases chain 80 s waits
STEP_MARGIN_S = 30
STEP_LIMIT_MIN_S = 90        # the old fixed cap stays the floor: reads and taps on their defaults keep it
STEP_LIMIT_MAX_S = 15 * 60
# ready's worst case: am start 40 s, the page 25 s, the update dialog 5 s, offline 5 s, 3 dialog presses with their
# settle, adb and CDP calls in between
READY_LIMIT_S = 150
WAITS = {"--timeout": READBACK_TIMEOUT_S, "--find-timeout": act_parser().get_default("find_timeout")}


def needs_network(case):
    """F3: a case that writes to the cloud needs the network, as does one whose needs say network: true; a case that
    only reads (UPG-013's local log files) runs with the app offline. needs.network: false overrides."""
    net = (case.get("needs") or {}).get("network")
    return net if isinstance(net, bool) else bool(case.get("cloud_writes"))


def step_limit(args):
    """The time limit of one act.py call: its own waits (--timeout and --find-timeout, act.py's defaults when not
    given) plus STEP_MARGIN_S, between STEP_LIMIT_MIN_S and STEP_LIMIT_MAX_S (ready: at least READY_LIMIT_S). A step's
    pause_s is slept by the runner before the call, so it is not part of the call. A wait that runs out is still act.py's
    readback failure (exit 3, ERROR); the limit only stops a call that hangs. map-tap finds no element: only its
    --timeout counts."""
    waits, i = dict(WAITS, **({"--timeout": WIFI_TIMEOUT_S} if args[:1] == ["wifi"] else {})), 1
    if args[:1] == ["map-tap"]:
        del waits["--find-timeout"]
    while i < len(args):
        name, eq, val = args[i].partition("=")
        if name in VALUE_OPTS:
            if not eq:
                i += 1
                val = args[i] if i < len(args) else ""
            if name in waits:
                try:
                    waits[name] = float(val)
                except ValueError:
                    pass  # act.py refuses the call itself (usage)
        i += 1
    budget = sum(w for w in waits.values() if math.isfinite(w) and w > 0) + STEP_MARGIN_S
    floor = READY_LIMIT_S if args[:1] == ["ready"] else STEP_LIMIT_MIN_S
    return min(max(budget, floor), STEP_LIMIT_MAX_S)


# F5 (2026-11-02): a needs.flags entry is "Name" or "Name: on" (the flag must be on) or "Name: off" (it must be off)
FLAG_NEED = re.compile(r"\s*(.+?)\s*(?::\s*(on|off)\s*)?", re.I)


def flag_needs(case):
    """[(flag name, True when it must be on)] from the case's needs.flags."""
    out = []
    for e in (case.get("needs") or {}).get("flags") or []:
        m = FLAG_NEED.fullmatch(str(e))
        if m:
            out.append((m.group(1), (m.group(2) or "on").lower() == "on"))
    return out


def step_selectors(s):
    """(kind, value) of the control a step taps or types into (tap, type, sys-tap); finds, waits and --expect
    readbacks only look, so they are not counted."""
    verb, sel, _ = helper_call(s)
    if verb not in ("tap", "type", "sys-tap", "choose-file") or not sel:
        return []
    if verb == "sys-tap":
        return [("text", sel)]
    m = re.match(r"^(text|aria|testid):(.+)$", sel, re.S)
    return [(m.group(1), m.group(2))] if m else []


def step_js(s):
    """Every page expression a step evaluates: read_js, when_js, a helper read and its js: readbacks."""
    verb, sel, expects = helper_call(s)
    js = [s.get("read_js"), s.get("when_js"), sel if verb == "read" else None]
    return [x for x in js + [e[3:] for e in expects if e.startswith("js:")] if x]


def steps_hitting(case, gate, texts, testids, js=False):
    """Step numbers that declare the gate, tap a control from the given label and test-id sets, or (js) evaluate
    page JS that writes."""
    hits = []
    for s in case.get("steps") or []:
        sels = step_selectors(s)
        if gate in (s.get("gates") or []) or any(
                (k in ("text", "aria") and norm_label(v) in texts) or (k == "testid" and bare_testid(v) in testids)
                for k, v in sels) or (js and any(JS_WRITE.search(x) for x in step_js(s))):
            hits.append(s.get("n"))
    return hits


def cloud_write_steps(case):
    """G3: the steps that create or change cloud data, gated, declared, by control or by page JS."""
    return sorted(set(steps_hitting(case, "cloud_write", G3_TEXT, G3_TESTID, js=True)
                      + [w.get("step") for w in case.get("cloud_writes") or [] if w.get("step") is not None]))


STANDING_LABELS = {"next", "continue"}


def _app_worker():
    """This qa copy's app/worker.py, the New test guard's rules (it imports only records and redact from the runner)."""
    app = os.path.join(QA_DIR, "app")
    if app not in sys.path:
        sys.path.insert(1, app)
    import worker  # noqa: E402
    return worker


def own_flight_save(case, s):
    """The New test guard's rule (qa/app/worker.py own_flight_save), so the plan and the run read one rule: this flight
    save saves a flight the case created in the QA_ site it created itself."""
    return _app_worker().own_flight_save(s, sys.modules[__name__], case)


def flight_save_step(s):
    """True for a tap on a flight-save label (FLIGHT_SAVE_TEXT) by text:, as standing_step and act.py read it."""
    verb, sel, _ = helper_call(s)
    m = re.match(r"^text:(.+)$", sel or "", re.S)
    return verb == "tap" and bool(m) and " ".join(m.group(1).split()).lower() in FLIGHT_SAVE_TEXT


def own_site_step(case, s):
    """Review 2026-11-03: True for the case's own Save new site (a tap worker.qa_site_named allows) in a case that saves
    a flight. After it the runner reads which site the app opened (TargetRun.own_site, QA_OWN_SITE), and act.py taps a
    standing flight save only in the planner of a flight in that site: the case rule cannot see the screen."""
    verb, sel, _ = helper_call(s)
    m = re.match(r"^(?:text|aria):(.+)$", sel or "", re.S)  # the label as worker._label reads it (SITE_CREATE_TEXT)
    if verb != "tap" or not m or " ".join(m.group(1).split()).lower() != "save new site":
        return False
    return (any(flight_save_step(x) for x in case.get("steps") or [] if isinstance(x, dict))
            and _app_worker().qa_site_named(s, sys.modules[__name__], case))


def standing_step(case, s, eng):
    """Engine piece 5 (2026-10-26): a write_list step the runner runs itself instead of handing it back. Only a tap on
    Next or Continue (write-list only because of where they can appear: act.py still refuses them in the bug reporter
    and the OTA Wi-Fi dialog), with no gate beyond write_list and cloud_write, a cloud_write step declared in the
    case's cloud_writes, in an active case that passes the G3 rules (refuse_case). Since 2026-11-03 (the person's OK
    for the offline-sites campaign) also a tap on a flight-save label (FLIGHT_SAVE_TEXT: Save, Save & exit, Save as
    draft) gated write_list and cloud_write, when own_flight_save holds; act.py then taps only one of the flight
    planner's save controls."""
    gates = set(s.get("gates") or [])
    if "write_list" not in gates or gates - {"write_list", "cloud_write"}:
        return False
    verb, sel, _ = helper_call(s)
    m = re.match(r"^text:(.+)$", sel or "", re.S)
    label = " ".join(m.group(1).split()).lower() if m else None
    if verb != "tap" or label not in STANDING_LABELS | FLIGHT_SAVE_TEXT:
        return False
    if "cloud_write" in gates and s.get("n") not in {w.get("step") for w in case.get("cloud_writes") or []}:
        return False
    if label in FLIGHT_SAVE_TEXT and ("cloud_write" not in gates or not own_flight_save(case, s)):
        return False
    return case.get("status") == "active" and refuse_case(case, eng) is None


def refuse_case(case, eng):
    """Why the runner must not start this case (G3 and the app's draft cases), or None. Checked before any device call."""
    cid, status = case.get("id"), case.get("status", "active")
    if status != "active":
        return f"case {cid} has status {status}: only active cases run (/qa-run completes a draft first)"
    if not case.get("steps"):
        return f"case {cid} has no steps: nothing to run (/qa-run completes it from source first)"
    td = eng.get("test_data") or {}
    site = td.get("site") or {}
    mock = (td.get("mock_drone") or {}).get("vehicle_id")
    declared = case.get("test_data") or {}
    try:
        writes = cloud_write_steps(case)
    except ValueError as e:
        return f"case {cid}: a step's helper cannot be split ({e})"
    found = [f"steps {writes}"] if writes else []
    if JS_WRITE.search((case.get("differential") or {}).get("api_js") or ""):
        found.append("differential.api_js")
    if found or case.get("cloud_writes"):
        where = " and ".join(found) or "declared in cloud_writes"
        if not site.get("id"):
            return (f"case {cid} creates cloud data ({where}) and no test-data site "
                    "is authorized yet: qa/runner/engine.json test_data.site is pending (the 'QA engine' site in the CT "
                    "org on staging is created only with the user's OK). Read-only cases run as usual.")
        if site.get("env") != eng.get("env"):
            return (f"case {cid} creates cloud data ({where}) but the authorized test-data site {site['id']} belongs to "
                    f"env {site.get('env')}, not the engine's {eng.get('env')}")
        if declared.get("site_id") != site["id"]:
            return (f"case {cid} creates cloud data ({where}) but does not declare the authorized site: "
                    f"its test_data.site_id must be {site['id']}")
    if (case.get("needs") or {}).get("link") == "mocklink":
        # Every MockLink connect writes to the fleet (license and drone-info PATCHes for its code, the fleet-wide
        # firmware status check), OTA or not, so no MockLink case runs without the dedicated mock drone.
        ota = steps_hitting(case, "mocklink_ota", MOCKLINK_OTA_TEXT, OTA_TESTID)
        what = f"runs a MockLink OTA (steps {ota})" if ota else "connects a MockLink"
        if not mock:
            return (f"case {cid} {what} and no dedicated mock drone is authorized yet: qa/runner/engine.json "
                    "test_data.mock_drone is pending. A MockLink connect writes to the CT fleet, so existing fleet "
                    "drones are not used.")
        if str(declared.get("vehicle_id")) != str(mock):
            return f"case {cid} {what} but does not declare the mock drone {mock} (test_data.vehicle_id)"
    return None


ROUTE_ID = re.compile(r"/(?:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|[0-9a-fA-F]{24}|\d+)(?=/|$)")
SIDE_EFFECT_METHOD = {"POST": "cloud_create", "PUT": "cloud_update", "PATCH": "cloud_update", "DELETE": "cloud_delete"}
# Chrome errors that mean the request never reached the server; any other failure may have changed cloud data.
NEVER_SENT = re.compile(r"ERR_(?:NAME_NOT_RESOLVED|BLOCKED_BY_CLIENT|BLOCKED_BY_ADMINISTRATOR|INTERNET_DISCONNECTED|"
                        r"ADDRESS_UNREACHABLE|CONNECTION_REFUSED|PROXY_CONNECTION_FAILED|NETWORK_ACCESS_DENIED|CERT_\w+|SSL_\w+)\b")


def har_outcome(e, status):
    """What a HAR entry says about the write: None when it changed nothing (an error status, never sent)."""
    if status and status >= 400:
        return None
    failed = e.get("failed")
    if failed:
        err = failed.get("error") or "no error text"
        if status:
            return f"ok (body load failed: {err})"
        return None if NEVER_SENT.search(err) else f"unknown (request failed: {err})"
    return "unknown (no response seen)" if e.get("unfinished") else "ok"


def har_side_effects(har_path, eng, case, run_id, campaign, tag):
    """G3: one side_effect record per non-GET request to the cloud API in a web run's HAR that may have changed
    cloud data (answered below 400, still open, or failed after it may have been sent). Returns the record ids."""
    api = urllib.parse.urlsplit(eng.get("api_base") or "").netloc
    har = load_json(har_path, {}) or {}
    ids = []
    for i, e in enumerate((har.get("log") or {}).get("entries") or []):
        req = e.get("request") or {}
        what = SIDE_EFFECT_METHOD.get((req.get("method") or "").upper())
        url = urllib.parse.urlsplit(req.get("url") or "")
        status = (e.get("response") or {}).get("status")
        outcome = har_outcome(e, status)
        if not what or not api or url.netloc != api or outcome is None:
            continue
        ids.append(side_effect(campaign, {
            "target": tag, "what": what, "run": run_id, "case": case.get("id"),
            "object": {"method": req.get("method"), "path": url.path, "route": ROUTE_ID.sub("/:id", url.path),
                       "status": status, "outcome": outcome},
            "declared": bool(case.get("cloud_writes")) or bool(steps_hitting(case, "cloud_write", CLOUD_WRITE_TEXT, CLOUD_WRITE_TESTID)),
            "account": eng.get("account"), "observed_by": "har",
            "src": {"t": "file", "path": rel(har_path), "sha256": sha256_file(har_path), "pick": f"log.entries[{i}]"},
            "reverted_by": None}, session=run_id))
    return ids


def har_side(har_path, eng, case, run_id, campaign, tag):
    """The run's side_effects from its HAR. When the listener died mid-run (log._error), later requests were not
    captured: the error is kept and complete is false."""
    try:
        side = {"observed_by": "har", "ids": har_side_effects(har_path, eng, case, run_id, campaign, tag)}
    except (OSError, ValueError) as e:
        return {"observed_by": "har", "ids": [], "error": f"{type(e).__name__}: {e}"[:200], "complete": False}
    err = ((load_json(har_path, {}) or {}).get("log") or {}).get("_error")
    if err:
        side.update(error=f"HAR listener stopped: {err}"[:200], complete=False)
    return side


def ledger_ref(e):
    return {"day": e["t"][:10], "seq": e["seq"]} if e else None


def runner_event(session, target, tool, argv, rc, out=""):
    return append({"src": "runner", "session": session, "target": target, "tool": tool, "argv": argv, "exit": rc,
                   "out_sha256": sha256_bytes(out.encode()) if out else None, "ref": None})


class Lock:
    """Exclusive flock on state/locks/<name>.lock; <name>.json says who holds it (the pane's BUSY)."""

    def __init__(self, name, holder, case):
        os.makedirs(os.path.join(STATE, "locks"), exist_ok=True)
        self.path = os.path.join(STATE, "locks", f"{name}.lock")
        self.meta = os.path.join(STATE, "locks", f"{name}.json")
        self.name, self.holder, self.case = name, holder, case
        self.f = None

    def __enter__(self):
        self.f = open(self.path, "a")
        try:
            fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.f.close()
            raise RuntimeError(f"{self.name} is locked by {load_json(self.meta, {}).get('holder')}")
        with open(self.meta, "w") as m:
            json.dump({"name": self.name, "holder": self.holder, "case": self.case, "since": now_utc()}, m)
        runner_event(self.holder, self.name, "lock", ["acquire", self.case], 0)
        return self

    def __exit__(self, *exc):
        try:
            os.remove(self.meta)
        except OSError:
            pass
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()
        runner_event(self.holder, self.name, "lock", ["release", self.case], 0)


def write_progress(name, obj):
    """Replace state/progress/<name>.json atomically (tmp + os.replace). Returns False instead of raising:
    the progress file is a view for the app, the run record is the result."""
    tmp = None
    try:
        os.makedirs(PROGRESS, exist_ok=True)
        path = os.path.join(PROGRESS, f"{name}.json")
        tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(canonical(redact_obj(obj)))
        os.replace(tmp, path)
        return True
    except Exception:  # noqa: BLE001
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass
        return False


def prediction_for(campaign, run_id):
    """The hashed prediction of this run: (ledger entry, path) or (None, path)."""
    p = os.path.join(QA_DIR, "runs", campaign, "pred", f"PR-{run_id}.json")
    want = rel(p)
    found = None
    for _, raw in iter_ledger():
        e = json.loads(raw)
        if e.get("src") == "record" and (e.get("ref") or {}).get("path") == want:
            found = e
    return found, p


def act(target, args, run_id, timeout=90, env=None):
    """One act.py call under the runner; env adds QA_CAMPAIGN (where its side effects go) and QA_CLOUD_WRITE."""
    full = dict(os.environ, QA_RUNNER="1", QA_RUN_ID=run_id)
    for k in ("QA_CLOUD_WRITE", "QA_STANDING_STEP", "QA_OWN_SITE"):  # review 2026-11-03: only from the step's env
        full.pop(k, None)
    full.update(env or {})
    try:
        p = subprocess.run([sys.executable, "-I", ACT, target, *args], capture_output=True, text=True, timeout=timeout, env=full)
    except subprocess.TimeoutExpired:
        # a failed call like any other: during clean-up it stays a note, it never throws the decided verdict away
        return {"ok": False, "code": 3, "error": f"act.py {args[0]} timed out after {timeout} s", "exit": None}
    try:
        out = json.loads(p.stdout)
    except ValueError:
        out = {"ok": False, "code": 3, "error": (p.stderr or p.stdout)[-300:]}
    out["exit"] = p.returncode
    return out


def clock_entry(at, t0, t1, raw, target_ms, method, e):
    """One clock reading; output that does not parse gives offset_ms None and an error instead of a crash."""
    mid = (t0 + t1) / 2
    d = {"at": at, "host": datetime.datetime.fromtimestamp(mid, datetime.timezone.utc).isoformat(timespec="milliseconds"),
         "target_raw": raw.strip(), "offset_ms": None, "rtt_ms": round((t1 - t0) * 1000), "method": method,
         "src": {"t": "ledger", **ledger_ref(e)}}
    try:
        d["offset_ms"] = round(target_ms(raw) - mid * 1000)
    except ValueError as x:
        d["error"] = f"unreadable clock output: {x}"[:200]
    return d


def tablet_clock(serial, at, run_id):
    t0 = time.time()
    p = subprocess.run(["adb", "-s", serial, "shell", "date", "+%s%3N"], capture_output=True, text=True, timeout=10)
    t1 = time.time()
    e = runner_event(run_id, serial, "adb", ["shell", "date", "+%s%3N"], p.returncode, p.stdout)
    return clock_entry(at, t0, t1, p.stdout, lambda raw: int(raw.strip()), "adb shell date +%s%3N", e)


def web_clock(at, run_id):
    cmd = "perl -MTime::HiRes=time -e 'printf \"%.3f\\n\", time'"
    t0 = time.time()
    p = subprocess.run(["ssh", "webpc044", cmd], capture_output=True, text=True, timeout=20)
    t1 = time.time()
    e = runner_event(run_id, "webpc044", "ssh", [cmd], p.returncode, p.stdout)
    return clock_entry(at, t0, t1, p.stdout, lambda raw: float(raw) * 1000, "ssh perl -MTime::HiRes", e)


def battery(serial, at, run_id):
    p = subprocess.run(["adb", "-s", serial, "shell", "dumpsys", "battery"], capture_output=True, text=True, timeout=10)
    runner_event(run_id, serial, "adb", ["shell", "dumpsys", "battery"], p.returncode, p.stdout)
    g = lambda k: (re.search(rf"^\s*{k}: (\S+)", p.stdout, re.M) or [None, None])[1]
    temp = g("temperature")
    return {"at": at, "level": int(g("level") or -1), "status": g("status"), "temp_c": int(temp) / 10 if temp else None,
            "usb_powered": g("USB powered") == "true"}


def marker(serial, text, run_id):
    p = subprocess.run(["adb", "-s", serial, "shell", "log", "-t", "WTQA", shlex.quote(text)], capture_output=True, text=True, timeout=10)
    e = runner_event(run_id, serial, "adb", ["shell", "log", "-t", "WTQA", text], p.returncode)
    return {"text": text, "at_host": e["t"], "ledger": ledger_ref(e)}


def sourced(v, st, src, why=None):
    d = {"v": v, "st": st, "src": src}
    if why:
        d["why"] = why
    return d


class TargetRun(threading.Thread):
    def __init__(self, case, case_path, tag, campaign, nn, prof, eng):
        super().__init__(name=tag)
        self.case, self.case_path, self.tag, self.campaign, self.prof, self.eng = case, case_path, tag, campaign, prof, eng
        self.pred_entry, self.pred_path = None, None
        self.is_web = tag == "W044"
        self.run_id = f"R-{campaign[:10].replace('-', '')}-{tag}-{case['id']}-{nn}"
        # {time} in helpers (2026-11-03): the run's local start as "MM-DD HH:MM", for short names that stay unique, as
        # the app refuses a site name that exists ("QA_ET65 Offline sites 11-03 14:30")
        self.time_label = time.strftime("%m-%d %H:%M")
        self.dir = os.path.join(WORKSPACE, "tests", campaign, tag)
        self.rec, self.error, self.values = None, None, {}
        self.differential = None
        self.lock_name = "webpc044" if self.is_web else prof["serial"]
        self.prog = None
        self.har, self.har_path, self.side = None, None, None
        # the Wi-Fi safety net: a step called wifi off (wifi_off), the wifi on call's outcome, its note when it failed
        self.wifi_off, self.wifi_restore, self.wifi_note = False, None, None
        # review 2026-11-03: the site id the app opened after this run's own Save new site (own_site_step), else None;
        # a standing flight save passes it to act.py as QA_OWN_SITE
        self.own_site = None

    def progress(self, **kw):
        """Update and rewrite this attempt's progress file; a no-op before the attempt holds its lock."""
        if self.prog is not None:
            self.prog.update(kw, updated=now_utc())
            write_progress(self.lock_name, self.prog)

    def steps(self):
        plat = "web" if self.is_web else "tablet"
        return [s for s in self.case.get("steps", []) if s.get("on", "both") in ("both", plat)]

    def run(self):
        try:
            self._run()
        except Exception as e:  # noqa: BLE001 (a busy lock, or a crash while recording a crash)
            self.crashed(e)

    def crashed(self, e):
        """The runner's ERROR record for a run that raised (with the HAR's side effects when there were any), and the
        finished progress. _run calls it while the locks are still held."""
        self.error = f"{type(e).__name__}: {e}"
        ev = runner_event(self.run_id, self.tag, "runner", ["crash"], 1, self.error)
        if self.rec is None:
            # the run still gets a record: ERROR by the runner, never a verdict about the app
            v = {"v": "ERROR", "evidence_kind": "device", "judged": "script", "decided": now_utc(),
                 "harness": {"component": "runner", "detail": self.error[:300], "ledger": ledger_ref(ev)}}
            self.rec = {"kind": "run", "schema": 1, "id": self.run_id, "created": now_utc(), "author": "runner",
                        "supersedes": None, "mode": "live", "case": self.case["id"], "case_file_sha256": sha256_file(self.case_path),
                        "harness_case": bool(self.case.get("harness")), "target": {"tag": self.tag}, "build": {}, "flags": {},
                        "values": self.values, "judgement_pending": [], "verdict": v, "attempts": [], "issues": [],
                        "differential": self.differential, "evidence_dir": rel(self.dir), "manifest_sha256": None}
            if self.side is not None:
                self.rec["side_effects"] = self.side
            if self.wifi_note:
                self.rec["notes"] = [self.wifi_note]
            p = os.path.join(QA_DIR, "runs", self.campaign, f"{self.run_id}.json")
            if not os.path.exists(p):
                write_record(p, self.rec)
                hash_record(p, session=self.run_id, note="runner run record (runner crash)")
        if self.prog is not None and self.prog["state"] != "finished":
            self.progress(state="finished", verdict=self.rec["verdict"]["v"] if self.rec else "ERROR")

    def stop_har(self):
        """Stop the web run's HAR recorder once and record the cloud writes it saw (G3), on every path out of the
        attempt. Returns the recorder, or None when there is none or it was already stopped."""
        har, self.har = self.har, None
        if har is None:
            return None
        try:
            har.stop()
        except (OSError, ValueError) as e:
            self.side = {"observed_by": "har", "ids": [], "error": f"HAR not written: {type(e).__name__}: {e}"[:200],
                         "complete": False}
            return har
        self.side = har_side(self.har_path, self.eng, self.case, self.run_id, self.campaign, self.tag)
        return har

    def restore_wifi(self):
        """The Wi-Fi safety net (2026-11-03): a tablet run in which a step called wifi off turns Wi-Fi back on, once,
        whatever the verdict. Called after the clean-up steps and, for an attempt that crashed before that, from
        _attempt; both while the target's lock is held. Returns the note when it could not, else None."""
        if self.is_web or not self.wifi_off or self.wifi_restore is not None:
            return None
        try:
            out = act(self.tag, ["wifi", "on"], self.run_id, timeout=step_limit(["wifi", "on"]))
        except Exception as e:  # noqa: BLE001 (the net never ends the run)
            out = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        self.wifi_restore = {"ledger": out.get("ledger"), "ok": bool(out.get("ok")), "wifi": out.get("wifi")}
        if not out.get("ok"):
            self.wifi_note = (f"Wi-Fi could not be turned back on: turn it on by hand on {self.tag} "
                              f"(act.py wifi on: {out.get('error')})")[:300]
        return self.wifi_note

    def fill(self, a):
        # quoted: the workspace path has a space, and helpers are shell-split afterwards
        return (a.replace("{target}", self.tag).replace("{run}", self.run_id).replace("{time}", self.time_label)
                 .replace("{shots}/{run}", shlex.quote(os.path.join(self.dir, "shots", self.run_id)))
                 .replace("{shots}", shlex.quote(os.path.join(self.dir, "shots"))))

    def identity_checks(self, out, verb):
        idn = out.get("identity") or {}
        if verb == "wifi" and not idn:  # switched over USB; the page is read only for --until-offline and --expect
            return {k: "n/a" for k in ("forward_ok", "ua_model_ok", "dpr_ok", "origin_ok", "focus_ok")}
        if self.is_web:
            return {"browser_id_ok": bool(idn.get("browser_id")), "tab_origin_ok": idn.get("origin") == self.eng.get("web_origin")}
        return {"forward_ok": bool(idn.get("port")), "ua_model_ok": idn.get("ua_model") == self.prof.get("user_agent_model"),
                "dpr_ok": idn.get("dpr") == self.prof.get("dpr"), "origin_ok": idn.get("origin") == self.eng.get("web_origin"),
                "focus_ok": True if verb in ("tap", "type", "key", "map-tap") and out.get("ok") else "n/a"}

    def _run(self):
        for sub in ("shots", "logs"):
            os.makedirs(os.path.join(self.dir, sub), exist_ok=True)
        serial = None if self.is_web else self.prof["serial"]
        locks = [Lock(self.lock_name, self.run_id, self.case["id"])]
        if (self.case.get("needs") or {}).get("account_lock"):
            locks.append(Lock(f"account-{self.eng.get('account')}", self.run_id, self.case["id"]))
        held = []
        try:
            for lk in locks:
                held.append(lk.__enter__())
            try:
                self._attempt(serial)
            except Exception as e:  # noqa: BLE001
                self.crashed(e)  # while the locks are held, so no progress write follows the unlock
        finally:
            for lk in reversed(held):
                lk.__exit__(None, None, None)

    def _attempt(self, serial):
        """One attempt; the HAR recorder is stopped and its side effects recorded, and Wi-Fi a step switched off is
        turned back on (restore_wifi), however the attempt ends."""
        try:
            self._attempt_steps(serial)
        finally:
            try:
                self.stop_har()
            finally:
                self.restore_wifi()  # a no-op when _attempt_steps did it (or no step switched Wi-Fi off)

    def _attempt_steps(self, serial):
        rid = self.run_id
        started = now_utc()
        self.prog = {"run": rid, "case": self.case["id"], "target": self.tag, "attempt": 1, "step": None,
                     "steps_total": len(self.steps()), "started": started, "updated": started, "state": "running",
                     "verdict": None, "last_shot": None, "clock_offset_ms": None}
        self.progress()
        clock, batt, markers = [], [], {}
        identity, steps, evidence = [], [], []
        verdict = {"v": None}
        pending = []
        build, flags = {}, {}

        def stop(v, **kw):
            verdict.update(v=v, **kw)

        def harness(component, out):
            stop("ERROR", harness={"component": component, "detail": (out.get("error") or "")[:300], "ledger": out.get("ledger")})

        def not_ready(component, out):
            """F4: a failed call before the first case step. A setup problem act.py marks (a device not connected or not
            authorised, W044 or its QA Chrome away, no or several app tabs) is the person's: BLOCKED with what to do.
            Only used before the first case step; do_step keeps its own rules."""
            if out.get("setup"):
                stop("BLOCKED", by={"kind": "precondition", "ref": f"device not ready: {(out.get('error') or '')[:200]}"})
            else:
                harness(component, out)

        # engine piece 1: wake the tablet and bring WingtraApp to the front (W044: the app tab to the front); a PIN, a
        # window that stays on top, a tab that stays hidden, a forced app update, the app offline or a dialog that stays
        # open blocks the run (precondition), it is not a verdict about the app
        rdy = act(self.tag, ["ready"], rid, timeout=step_limit(["ready"]))
        identity.append({"ledger": rdy.get("ledger"), "verb": "ready", "checks": [],
                         "actions": (rdy.get("ready") or {}).get("actions") or []})
        if not rdy.get("ok"):
            if rdy.get("code") == 2:  # not reachable, a PIN, a window on top or a hidden tab: the person's
                stop("BLOCKED", by={"kind": "precondition", "ref": f"device not ready: {(rdy.get('error') or '')[:200]}"})
            else:
                not_ready("act.py ready", rdy)
        elif rdy.get("offline") and needs_network(self.case):  # F3: the app offline, and the case needs the network
            stop("BLOCKED", by={"kind": "precondition",
                                "ref": f"device not ready: {self.tag} is offline: check its Wi-Fi, then start again."})
        if verdict["v"] is None:
            # F4: the clock, battery, start marker and HAR start once the target is ready, so a target that is not
            # ready ends BLOCKED by ready instead of a crash in a clock read or the HAR's connect before it
            clock.append(web_clock("start", rid) if self.is_web else tablet_clock(serial, "start", rid))
            self.progress(clock_offset_ms=clock[0]["offset_ms"])
            if not self.is_web:
                batt.append(battery(serial, "start", rid))
                markers["start"] = marker(serial, f"{rid} a1 start", rid)
            else:
                from har import HarRecorder
                har_path = os.path.join(self.dir, "logs", f"{rid}.har")
                har = HarRecorder(har_path, os.path.join(self.dir, "logs", f"{rid}_console.jsonl"))
                har.start()
                self.har, self.har_path = har, har_path
        if self.case.get("fixtures") and verdict["v"] is None:
            # engine piece 2: the case's test files onto this target, sha256-checked; a source that does not match the
            # case or a different file at the destination blocks the run (precondition), the engine never overwrites
            dl = act(self.tag, ["deliver", self.case["id"], "--case-file", rel(self.case_path)], rid,
                     timeout=120 + DELIVER_PER_FILE_S * len(self.case["fixtures"]))
            d = dl.get("deliver") or {}
            identity.append({"ledger": dl.get("ledger"), "verb": "deliver", "checks": [],
                             "fixtures": d.get("fixtures") or [], "actions": d.get("actions") or []})
            if not dl.get("ok"):
                if dl.get("code") == 2:
                    stop("BLOCKED", by={"kind": "precondition", "ref": f"test file: {(dl.get('error') or '')[:200]}"})
                else:
                    harness("act.py deliver", dl)
            elif d.get("version_file") != rel(self.case_path):
                harness("act.py deliver", dict(dl, error=f"delivered the files of {d.get('version_file')}, the run is on "
                                                         f"{rel(self.case_path)}"))
        probe = act(self.tag, ["probe"], rid)
        identity.append({"ledger": probe.get("ledger"), "verb": "probe", "checks": self.identity_checks(probe, "probe")})
        if not probe.get("ok") and verdict["v"] is None:
            not_ready("act.py", probe)
        account = sourced(self.eng.get("account"), "unverified", [], "not checked")
        if verdict["v"] is None:
            ok, detail, ref = check_account(self.tag, rid, self.eng)
            account = sourced(engine_account(self.eng)["label"], "verified" if ok else "unverified",
                              [{"t": "ledger", **ref}] if ref else [], detail)
            if ok is False:
                stop("BLOCKED", by={"kind": "precondition", "ref": f"account: {detail}"})
            elif ok is None:
                harness("runner", {"error": detail, "ledger": ref})
        if verdict["v"] is None:
            b = act(self.tag, ["read", BUILD_JS], rid)
            identity.append({"ledger": b.get("ledger"), "verb": "read", "checks": self.identity_checks(b, "read")})
            if not b.get("ok") or not (b.get("value") or {}).get("git_hash"):
                not_ready("act.py", dict(b, error=b.get("error") or "build pin: no git hash in the entry chunk"))
            else:
                build = self.build_record(b, serial)
                if build["env"]["v"] != self.eng.get("env"):
                    stop("ERROR", harness={"component": "runner", "detail": f"build env {build['env']['v']} is not the engine's {self.eng.get('env')}",
                                           "ledger": b.get("ledger")})
        if verdict["v"] is None:
            f = act(self.tag, ["read", FLAGS_JS], rid)
            names = f.get("value") if f.get("ok") else None
            need = flag_needs(self.case)
            watch = sorted({n for n, _ in need} | set(self.case.get("flags_watch") or []))
            flags = {"src": "localStorage unleashFlags" if names is not None else "none", "read": f.get("ledger"),
                     "sha256": sha256_bytes(canonical(names).encode()) if names is not None else None, "enabled": names,
                     "relevant": {n: (n in names) if names is not None else None for n in watch},
                     "st": "verified" if names is not None else "unverified"}
            # F5: a deciding flag with the wrong value (on where the case needs it off, or off where it needs it on)
            # blocks the run; a flag that could not be read is left unverified, as before
            wrong = [(n, on) for n, on in need if flags["relevant"].get(n) is (not on)]
            if wrong:
                stop("BLOCKED", by={"kind": "precondition", "ref": "flags: " + " ".join(
                    f"{n} is {'off' if on else 'on'} for the engine account ({self.eng.get('account')}) on {self.tag}; the "
                    f"test needs it "
                    f"{'on' if on else 'off'}." for n, on in wrong)})
        cleanup_log = []

        def do_step(s, is_cleanup):
            """One case step. Returns False when the attempt stops. In clean-up a failure is only recorded."""
            log = cleanup_log if is_cleanup else steps
            self.progress(step={"n": s["n"], "text": s["text"], "started": now_utc()})
            if s.get("pause_s"):
                time.sleep(float(s["pause_s"]))
            if s.get("when_js"):
                w = act(self.tag, ["read", s["when_js"]], rid)
                identity.append({"ledger": w.get("ledger"), "verb": "read", "checks": self.identity_checks(w, "read")})
                if not w.get("ok"):
                    log.append({"n": s["n"], "text": s["text"], "actions": [w.get("ledger")], "when": {"js": s["when_js"], "error": w.get("error")}})
                    if not is_cleanup:
                        harness("act.py", w)
                    return is_cleanup
                if not w.get("value"):
                    log.append({"n": s["n"], "text": s["text"], "actions": [w.get("ledger")], "skipped": True,
                                "when": {"js": s["when_js"], "value": w.get("value")}})
                    return True
            if s.get("read_js"):
                args = ["read", s["read_js"].replace("{target}", self.tag).replace("{run}", self.run_id)
                        .replace("{time}", self.time_label)]
            else:
                args = shlex.split(self.fill(s["helper"]))
                if args and args[0].endswith("act.py"):
                    args = args[1:]
                if args and args[0] == self.tag:
                    args = args[1:]
            gates = s.get("gates") or []
            standing = bool(args) and standing_step(self.case, s, self.eng)
            if "write_list" in gates and args and not standing:
                # the case marks this step a write-list action: handed back like act.py's own refusals, never run here
                cmd = handback_env(self.campaign, rid) + "python3 -I qa/runner/act.py " + shlex.join([self.tag, *args, "--approved"])
                log.append({"n": s["n"], "text": s["text"], "actions": [], "skipped": True, "handback": cmd})
                if is_cleanup:
                    return True
                stop("BLOCKED", by={"kind": "handover", "ref": cmd},
                     handback={"step": s["n"], "reason": "the case gates this step write_list", "command": cmd})
                return False
            env = {"QA_CAMPAIGN": self.campaign}
            if "cloud_write" in gates:
                env["QA_CLOUD_WRITE"] = "1"
            if standing:
                env["QA_STANDING_STEP"] = str(s["n"])  # piece 5: act.py lets this Next/Continue (or flight save) through  # act.py records a side_effect for the tap whatever its control
                if flight_save_step(s):  # review 2026-11-03: act.py checks the page is a planner in this site
                    env["QA_OWN_SITE"] = self.own_site or ""
            if not self.is_web and not s.get("read_js") and helper_call(s)[:2] == ("wifi", "off"):
                self.wifi_off = True  # before the call: one that fails or hangs may still have switched Wi-Fi off
            out = act(self.tag, args, rid, timeout=step_limit(args), env=env) if args else {"ok": True}
            verb = args[0] if args else None
            identity.append({"ledger": out.get("ledger"), "verb": verb, "checks": self.identity_checks(out, verb)})
            expected = [args[i + 1] for i, x in enumerate(args) if x == "--expect" and i + 1 < len(args)]
            st = {"n": s["n"], "text": s["text"], "actions": [out.get("ledger")],
                  "readback": {"expected": expected, "got": out.get("readback"), "ok": bool(out.get("ok"))}}
            if (out.get("element") or {}).get("scrolled_into_view"):
                st["scrolled_into_view"] = out["element"]["scrolled_into_view"]
            if out.get("shot"):
                sp = out["shot"]["path"]
                evidence.append({"kind": "shot", "path": rel(sp) if os.path.isabs(sp) else sp, "sha256": out["shot"]["sha256"], "captured": now_utc()})
                self.progress(last_shot=evidence[-1]["path"])
            if not out.get("ok"):
                st["error"] = out.get("error")
                log.append(st)
                if is_cleanup:
                    return True
                if out.get("code") == 2 and out.get("command"):
                    stop("BLOCKED", by={"kind": "handover", "ref": out["command"]},
                         handback={"step": s["n"], "reason": out.get("error"), "command": out["command"]})
                elif out.get("code") == 2 and out.get("device_state"):
                    stop("BLOCKED", by={"kind": "device_state", "ref": out["device_state"]})
                else:
                    harness("act.py", out)
                return False
            if args[:1] == ["tap"] and own_site_step(self.case, s):
                # review 2026-11-03: the site this run created, as the app opened it after the save (useSiteSaving.ts:
                # 120-127); a read that fails or another page leaves none, so a flight save is then handed back
                p = act(self.tag, ["read", "location.pathname"], rid)
                path = p.get("value") if p.get("ok") and isinstance(p.get("value"), str) else None
                m = SITE_PATH.fullmatch(path) if path else None
                self.own_site = m.group(1) if m else None
                st["actions"].append(p.get("ledger"))
                st["own_site"] = {"path": path, "site": self.own_site}
            if s.get("save_as"):
                self.values[s["save_as"]] = out.get("value")
            a = s.get("assert") or {}
            if a.get("kind") == "scripted" and not is_cleanup:
                scope = {"__builtins__": {}, "any": any, "all": all, "len": len, "sorted": sorted, "set": set,
                         "v": self.values, "build": {k: x.get("v") for k, x in build.items()},
                         "target": self.tag, "run": self.run_id}
                try:
                    bad = assert_problem(a.get("expr"))
                    if bad:  # review 2026-11-03: never eval what is not a plain check (ASSERT_NODES)
                        raise ValueError(f"not a plain check: {bad}")
                    ok = bool(eval(a["expr"], scope))  # noqa: S307 (the engine's own case files)
                except Exception as e:  # noqa: BLE001 (a broken assert is a harness error, not a FAIL)
                    st["assert"] = {"kind": "scripted", "expr": a["expr"], "result": None, "by": "script", "error": f"{type(e).__name__}: {e}"}
                    log.append(st)
                    harness("runner", {"error": f"assert could not be evaluated: {type(e).__name__}: {e}", "ledger": out.get("ledger")})
                    return False
                st["assert"] = {"kind": "scripted", "expr": a["expr"], "result": ok, "by": "script"}
                if not ok:
                    log.append(st)
                    stop("FAIL")
                    return False
            elif a.get("kind") == "judged" and not is_cleanup:
                st["assert"] = {"kind": "judged", "question": a["question"], "result": None, "by": "claude"}
                pending.append(s["n"])
            log.append(st)
            return True

        todo = self.steps() if verdict["v"] is None else []
        ran = set()
        for s in todo:
            ran.add(s["n"])
            if not do_step(s, False):
                break
        self.progress(step=None)
        notes = []

        def keep(what, fn):
            """End-of-run evidence: a failure (a USB hiccup, a timeout) is a note on the attempt, never the verdict's end."""
            try:
                return fn()
            except Exception as e:  # noqa: BLE001
                notes.append(f"{what}: {type(e).__name__}: {e}"[:300])
                return None

        # F7 (2026-11-02): the end shot before the clean-up steps, so a FAIL, ERROR or BLOCKED shows the screen where
        # the run stopped, not the screen the clean-up left
        shot_path = os.path.join(self.dir, "shots", f"{rid}_end.png")
        end = keep("end shot", lambda: act(self.tag, ["shot", shot_path], rid)) or {}
        if end.get("ok"):
            evidence.append({"kind": "shot", "path": rel(shot_path), "sha256": end["shot"]["sha256"], "captured": now_utc()})
            self.progress(last_shot=rel(shot_path))
        elif end:
            notes.append(f"end shot: {end.get('error')}"[:300])
        # clean-up: steps marked "always" that did not run (the attempt stopped early), e.g. leaving planning. Only after
        # a case step ran (2026-11-02 review): a run stopped before step 1 changed nothing to clean up, and the screen it
        # stopped on (a dialog ready would not close, the app offline) is the person's, so no clean-up tap goes there
        for s in self.steps() if ran else []:
            if s.get("always") and s["n"] not in ran and verdict["v"] is not None:
                do_step(s, True)
        self.progress(step=None)
        # the Wi-Fi safety net (2026-11-03): after the clean-up, whatever the verdict; a failure is a note, never the verdict
        if self.wifi_off:
            note = self.restore_wifi()
            identity.append({"ledger": self.wifi_restore["ledger"], "verb": "wifi", "checks": [],
                             "actions": ["turned Wi-Fi back on (a step switched it off)" if not note else
                                         "could not turn Wi-Fi back on"]})
            if note:
                notes.append(note)
        if verdict["v"] is None and not pending:
            stop("PASS")
        # the end readings pair with the start readings, which a run stopped at ready does not have (F4)
        if clock and not self.is_web:
            m = keep("end marker", lambda: marker(serial, f"{rid} a1 end", rid))
            if m:
                markers["end"] = m
            b = keep("end battery", lambda: battery(serial, "end", rid))
            if b:
                batt.append(b)
        if clock:
            c = keep("end clock", lambda: web_clock("end", rid) if self.is_web else tablet_clock(serial, "end", rid))
            if c:
                clock.append(c)
        ended = now_utc()
        if self.is_web:
            har = self.stop_har()
            evidence += [dict(e, captured=ended) for e in har.files()] if har else []
            side = self.side if har else {"observed_by": None, "ids": [],
                                          "note": "no HAR: the run stopped at ready, before the recorder started"}
        else:
            if clock:
                evidence += keep("tablet logs", lambda: self.tablet_logs(serial, clock[0], ended)) or []
            side = {"observed_by": None, "ids": [],
                    "note": "tablet network is not captured; taps on cloud-write controls are recorded by act.py"}
        manifest = {"run": rid, "files": {e["path"]: e["sha256"] for e in evidence}}
        mp = os.path.join(self.dir, f"{rid}.manifest.json")
        with open(mp, "w") as f:
            f.write(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        pred = load_json(self.pred_path, {})
        v = {"v": verdict["v"], "evidence_kind": "device",
             "judged": "script" if verdict["v"] else "pending",
             "assertion_sha256": sha256_bytes(canonical(pred.get("deciding_assertion")).encode()) if pred.get("deciding_assertion") else None,
             "decided": now_utc() if verdict["v"] else None}
        for k in ("by", "harness", "handback"):
            if k in verdict:
                v[k] = verdict[k]
        idn = probe.get("identity") or {}
        rec = {
            "kind": "run", "schema": 1, "id": rid, "created": now_utc(), "author": "runner", "supersedes": None,
            "mode": "live", "case": self.case["id"], "case_file_sha256": sha256_file(self.case_path),
            "harness_case": bool(self.case.get("harness")),
            "target": ({"class": "web", "host": "webpc044", "browser": idn.get("browser"), "browser_id": idn.get("browser_id"),
                        "tab_origin": idn.get("origin"), "profile_sha256": None} if self.is_web else
                       {"class": "tablet", "serial": self.prof["serial"], "tag": self.tag, "profile": self.prof["id"],
                        "profile_sha256": sha256_file(self.prof["_path"])}),
            "build": build,
            "link": {"kind": (self.case.get("needs") or {}).get("link", "none"), "simulated": None, "src": "case needs; no link read"},
            "flags": flags,
            "account": account,
            "clock": clock, "battery": batt,
            "prediction": {"id": f"PR-{rid}", "file_sha256": self.pred_entry["ref"]["file_sha256"],
                           "assertion_sha256": v["assertion_sha256"], "ledger": ledger_ref(self.pred_entry)},
            "attempts": [{"n": 1, "started": started, "ended": ended, "markers": markers, "identity": identity,
                          "steps": steps, "cleanup": cleanup_log, "evidence": evidence, "verdict": v}],
            "values": self.values, "judgement_pending": pending, "side_effects": side,
            "verdict": v, "issues": [], "differential": self.differential,
            "evidence_dir": rel(self.dir), "manifest_sha256": sha256_file(mp), "process": None,
        }
        if notes:
            rec["attempts"][0]["notes"] = notes  # end-of-run evidence that is missing, and why
        p = os.path.join(QA_DIR, "runs", self.campaign, f"{rid}.json")
        write_record(p, rec)  # when it raises, the run is not recorded: crashed() reports it as ERROR, never this verdict
        self.rec = rec
        hash_record(p, session=rid, note="runner run record")
        self.progress(state="finished", verdict=v["v"])

    def build_record(self, b, serial):
        val, ref = b["value"], {"t": "ledger", **(b.get("ledger") or {})}
        h = val["git_hash"]
        about = f"{val['version']}-{val['env']}" + ("" if val["env"] == "production" else f"-{h[:7]}")
        desc = subprocess.run(["git", "-C", WINGTRACLOUD, "describe", "--tags", h], capture_output=True, text=True, timeout=20).stdout.strip()
        rec = {
            "about_label": sourced(about, "derived", [ref, {"t": "git", "repo": "WingtraCloud", "ref": h,
                                                           "path": "apps/src/pages/Settings/screens/About.tsx", "line": 25}],
                                   "About builds its label from the same APP_VERSION, APP_ENVIRONMENT and GIT_HASH_SHORT the entry chunk carries"),
            "semver": sourced(val["version"], "verified", [ref]),
            "env": sourced(val["env"], "verified", [ref], "window.env.VITE_APP_ENVIRONMENT"),
            "hash7": sourced(h[:7], "verified", [ref]),
            "git_hash": sourced(h, "verified", [ref], f"40-hex GIT_HASH inlined in the entry chunk {val.get('entry')}"),
            "release_ref": sourced(desc or None, "inferred", [{"t": "git", "repo": "WingtraCloud", "cmd": f"describe --tags {h}"}]),
            "entry_chunk": sourced(val.get("entry"), "verified", [ref]),
        }
        if self.is_web:
            rec.update(version_code=sourced("web", "verified", [ref]), apk_sha256=sourced(None, "n/a", []),
                       bundle_source=sourced("served", "verified", [ref]))
        else:
            cmd = f"dumpsys package {PKG} | grep -m1 versionCode"
            p = subprocess.run(["adb", "-s", serial, "shell", cmd], capture_output=True, text=True, timeout=20)
            e = runner_event(self.run_id, serial, "adb", ["shell", cmd], p.returncode, p.stdout)
            vc = (re.search(r"versionCode=(\d+)", p.stdout) or [None, None])[1]
            rec["version_code"] = sourced(vc, "verified", [{"t": "ledger", **ledger_ref(e)}, ref],
                                          f"dumpsys package; LiveUpdate.getVersionCode says {val.get('version_code')}")
            rec["apk_sha256"] = sourced(None, "not_read", [])
            rec["bundle_source"] = sourced(None, "unknown", [], "LiveUpdate bundleId is not read")
        return rec

    def tablet_logs(self, serial, start_clock, ended):
        out = []
        start = (int(start_clock["target_raw"]) / 1000 if start_clock.get("offset_ms") is not None
                 else datetime.datetime.fromisoformat(start_clock["host"]).timestamp())  # host time when the tablet's was unreadable
        since = datetime.datetime.fromtimestamp(start - 2).strftime("%m-%d %H:%M:%S.000")
        lp = os.path.join(self.dir, "logs", f"{self.run_id}_logcat.txt")
        argv = ["logcat", "-d", "-b", "all", "-v", "threadtime", "-T", since]
        with open(lp, "wb") as f:
            p = subprocess.run(["adb", "-s", serial, *argv], stdout=f, timeout=120)
        runner_event(self.run_id, serial, "adb", argv, p.returncode)
        out.append({"kind": "logcat", "path": rel(lp), "sha256": sha256_file(lp), "captured": ended})
        cmd = "ls -t /storage/emulated/0/Documents/WingtraCloud/Logs/*.txt | head -1"
        ls = subprocess.run(["adb", "-s", serial, "shell", cmd], capture_output=True, text=True, timeout=20)
        runner_event(self.run_id, serial, "adb", ["shell", cmd], ls.returncode, ls.stdout)
        src = ls.stdout.strip()
        if src.endswith(".txt"):
            qp = os.path.join(self.dir, "logs", f"{self.run_id}_qt_{os.path.basename(src)}")
            p = subprocess.run(["adb", "-s", serial, "pull", src, qp], capture_output=True, text=True, timeout=120)
            runner_event(self.run_id, serial, "adb", ["pull", src], p.returncode)
            if p.returncode == 0:
                out.append({"kind": "qt_log", "path": rel(qp), "sha256": sha256_file(qp), "captured": ended, "device_path": src})
        return out


def differential(case, runs, campaign, nn):
    """Compare the case's saved values across the runs; a difference counts only at equal builds and flags."""
    spec = case.get("differential") or {}
    names = spec.get("values", [])
    did = f"D-{case['id']}-{campaign[:10].replace('-', '')}-{nn}"
    # only runs that reached a verdict about the app and saved every compared value take part
    by_run = {r.run_id: r for r in runs if r.rec and r.rec["verdict"]["v"] not in ("ERROR", "BLOCKED")
              and all(r.values.get(n) is not None for n in spec.get("values", []))}
    excluded = {r.run_id: (r.rec["verdict"]["v"] if r.rec else f"runner error: {r.error}") for r in runs if r.run_id not in by_run}
    vals = {rid: {n: r.values.get(n) for n in names} for rid, r in by_run.items()}
    builds = {rid: (r.rec["build"].get("hash7") or {}).get("v") for rid, r in by_run.items()}
    fl = {rid: r.rec["flags"].get("sha256") for rid, r in by_run.items()}
    # flag skew is judged on the flags the case names for it; Android-only flags that are always off on web
    # (e.g. OfflineSync) belong in expected_differences instead
    fnames = spec.get("flags", [])
    relevant = {rid: {n: (r.rec["flags"].get("relevant") or {}).get(n) for n in fnames} for rid, r in by_run.items()}
    watched = {rid: r.rec["flags"].get("relevant") for rid, r in by_run.items()}
    web = [rid for rid, r in by_run.items() if r.is_web]
    tab = [rid for rid, r in by_run.items() if not r.is_web]
    if len(by_run) < 2 or not web or not tab:
        result = "undecidable_missing_run"
    elif len(set(builds.values())) > 1:
        result = "undecidable_build_skew"
    elif len({canonical(x) for x in relevant.values()}) > 1:
        result = "undecidable_flag_skew"
    else:
        tv = {canonical(vals[r]) for r in tab}
        wv = {canonical(vals[r]) for r in web}
        if len(tv | wv) == 1:
            result = "consistent"
        elif len(tv) == 1 and tv != wv:
            result = "platforms_differ"
        else:
            result = "all_differ"
    api = None
    if spec.get("api_js") and web:
        # the third oracle: a direct cloud API read (GET only) through the web page, after the runs
        out = act("W044", ["read", spec["api_js"]], did)
        api = {"endpoint": spec.get("api_endpoint"), "ledger": out.get("ledger"), "ok": bool(out.get("ok")),
               "error": out.get("error"), "value": out.get("value"),
               "response_sha256": sha256_bytes(canonical(out.get("value")).encode()) if out.get("ok") else None}
    rec = {"kind": "differential", "schema": 1, "id": did, "created": now_utc(), "author": "runner", "supersedes": None,
           "case": case["id"], "runs": {"tablet": tab, "web": web[0] if web else None}, "excluded": excluded,
           "compared": names, "values": vals,
           "api": api, "builds": builds, "flags_sha256": fl, "flags_compared": relevant, "flags_watched": watched,
           "expected_differences_applied": case.get("expected_differences", []), "result": result}
    p = os.path.join(QA_DIR, "runs", campaign, f"{did}.json")
    write_record(p, rec)
    hash_record(p, session=did, note="runner differential")
    return rec


def main(argv):
    if argv[:1] == ["--check-account"]:
        eng = engine()
        rc = 0
        for tag in argv[1:]:
            ok, detail, ref = check_account(tag, "account-check", eng)
            print(json.dumps({"target": tag, "ok": ok, "detail": detail, "ledger": ref}))
            rc = rc or (0 if ok else 2)
        return rc
    ap = argparse.ArgumentParser()
    ap.add_argument("case")
    ap.add_argument("--targets", required=True)
    ap.add_argument("--campaign", required=True)
    ap.add_argument("--nn", default="01")
    a = ap.parse_args(argv)
    case, case_path = load_case(a.case)
    if not case:
        print(f"no case {a.case}")
        return 4
    eng = engine()
    why = refuse_case(case, eng)
    if why:
        print("refused: " + why)
        return 2
    profs = profiles()
    tags = a.targets.split(",")
    for tag in tags:
        if tag != "W044" and tag not in profs:
            print(f"unknown target {tag}")
            return 4
    if (case.get("targets") == "tablet" and "W044" in tags) or (case.get("targets") == "web" and set(tags) - {"W044"}):
        print(f"case {a.case} is routed to {case.get('targets')}; targets {a.targets} do not fit")
        return 4
    runs = [TargetRun(case, case_path, tag, a.campaign, a.nn, profs.get(tag), eng) for tag in tags]
    done = [rel(p) for p in (os.path.join(QA_DIR, "runs", a.campaign, f"{r.run_id}.json") for r in runs) if os.path.exists(p)]
    if done:
        print("refused: these runs are already recorded (records are immutable; use the next --nn):")
        for d in done:
            print("  " + d)
        return 2
    missing = []
    for r in runs:
        r.pred_entry, r.pred_path = prediction_for(a.campaign, r.run_id)
        if not r.pred_entry or sha256_file(r.pred_path) != r.pred_entry["ref"]["file_sha256"]:
            missing.append(rel(r.pred_path))
    if missing:
        print("refused: these predictions are not hashed in the ledger (write and hash them before the first action):")
        for m in missing:
            print("  " + m)
        return 2
    if case.get("targets") == "both":
        for r in runs:
            r.differential = f"D-{case['id']}-{a.campaign[:10].replace('-', '')}-{a.nn}"
    for r in runs:
        r.start()
    for r in runs:
        r.join()
    # the run ids first: they are recorded whatever the differential does
    print(json.dumps({"case": a.case,
                      "runs": {r.tag: {"id": r.run_id, "verdict": r.rec["verdict"]["v"] if r.rec else None,
                                       "pending": r.rec["judgement_pending"] if r.rec else None,
                                       "error": r.error or (r.rec["verdict"].get("harness") if r.rec else None),
                                       "handback": r.rec["verdict"].get("handback") if r.rec else None} for r in runs}},
                     indent=1), flush=True)
    if case.get("targets") == "both":
        try:
            diff = differential(case, runs, a.campaign, a.nn)
            out = {"id": diff["id"], "result": diff["result"], "values": diff["values"]}
        except Exception as e:  # noqa: BLE001 (e.g. the D record of this --nn already exists)
            out = {"id": runs[0].differential, "error": f"{type(e).__name__}: {e}"[:300]}
        print(json.dumps({"differential": out}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
