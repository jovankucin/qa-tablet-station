"""The 2026-11-02 reliability fixes (tests/2026-11-02-qa-reliability-audit, items 7, 10, 11, 13, 30; critic C, D),
against fakes only: no adb, no ssh, no CDP, no ledger or record writes.

python3 -I qa/runner/tests/test_reliability_fixes.py      (or unittest discover)

F1 a step's act.py call is cut at its own waits plus 30 s (90 s to 15 min), not at a fixed 90 s;
F2 ready closes the app's own dialogs with Escape (tablet and W044; never Back, 2026-11-02 review), at most 3 presses
   and never a tap;
F3 ready on a tablet: the app's offline indicator blocks the run;
F4 setup problems before the first case step are BLOCKED with what to do, a failure from the first step on keeps
   today's verdict; the clock and the HAR start after ready; the W044 ssh calls keep the link alive;
F5 needs.flags "Name: on|off", and an expired session (not just an expired access token) blocks the run;
F7 the end screenshot is taken before the clean-up steps.
Review (2026-11-02): no clean-up after a run stopped before step 1; F6's -s rule still denies every run of adb without
-s (gate_fixtures_review.jsonl through gate.decide; the hook entry runs them in test_gate.py).
"""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import act  # noqa: E402
import run  # noqa: E402
import test_progress as tp  # noqa: E402

SERIAL = tp.SERIAL
ENG = {"env": "staging", "web_origin": "web.staging.company.example", "account": "shared-stag"}
DIALOG = 'A dialog stays open on QA9: "Abort optimization?". Close it, then start again.'
_FRESH = []


def fresh_run():
    """A second copy of run.py, for the functions the script-style tests replace in run for good when unittest
    discover imports them (test_progress.stub_module: run.act, run.check_account)."""
    if not _FRESH:
        spec = importlib.util.spec_from_file_location("run_fresh", os.path.join(os.path.dirname(HERE), "run.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        _FRESH.append(m)
    return _FRESH[0]


@contextlib.contextmanager
def patched(obj, **attrs):
    """Set attributes on a module or object for the block, then put the old values back."""
    old = {k: getattr(obj, k) for k in attrs}
    for k, v in attrs.items():
        setattr(obj, k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            setattr(obj, k, v)


# ---------------------------------------------------------------- act.py: ready on a fake tablet and a fake W044

AWAKE = ("  mWakefulness=Awake\n    isKeyguardShowing=false\n"
         "  mCurrentFocus=Window{1 u0 com.wingtra.cloud.app/com.wingtra.cloud.app.MainActivity}\n4242")


class FakePage:
    """The app page: its open dialogs (titles, the last on top), the offline indicator over time, an update dialog.
    A key press closes the top dialog when closes is true; every Input call is recorded."""

    def __init__(self, dialogs=(), closes=True, offline=(False,), update=None):
        self.open, self.closes, self.offline, self.update = list(dialogs), closes, list(offline), update
        self.keys, self.mouse = [], []

    def eval(self, expr):
        if expr == act.DIALOGS_JS:
            return list(self.open)
        if expr == act.OFFLINE_JS:
            return self.offline.pop(0) if len(self.offline) > 1 else self.offline[0]
        if expr.startswith(act.UPDATE_DIALOG_JS):
            return self.update
        if expr == "document.visibilityState":
            return "visible"
        return "/sites"

    def call(self, method, params=None):
        if method == "Input.dispatchKeyEvent" and params["type"] == "keyDown":
            self.press(params["key"])
        elif method == "Input.dispatchMouseEvent":
            self.mouse.append(params)
        return {}

    def press(self, key):
        self.keys.append(key)
        if self.closes and self.open:
            self.open.pop()


class FakeTablet(act.Tablet):
    """A tablet that is awake with the app in front; its page is a FakePage. Every other adb call is recorded."""

    def __init__(self, page, conn="device"):
        super().__init__("FAKE", {"cdp_port": 1, "tag": "QA9", "dpr": 1})
        self.page, self.conn, self.calls = page, conn, []

    def adb(self, *args, timeout=20, check=False):
        if args == ("get-state",):
            return self.conn
        if args[:1] == ("shell",) and "mWakefulness" in args[1]:
            return AWAKE
        self.calls.append(args)
        if args[:3] == ("shell", "input", "keyevent"):
            self.page.press({"KEYCODE_BACK": "Back", "KEYCODE_ESCAPE": "Escape"}[args[3]])
        return ""

    def connect(self):
        self.sess = self.page
        return {"path": "/sites"}

    def taps(self):
        return [c for c in self.calls if c[:3] == ("shell", "input", "tap")]


class FakeWeb(act.Web):
    def __init__(self, page):
        super().__init__()
        self.page = page

    def connect(self):
        self.sess = self.page
        return {"origin": "web.staging.company.example"}


def ready(t):
    """(result, Fail) of t.ready()."""
    try:
        return t.ready(), None
    except act.Fail as e:
        return None, e


class ActReadyBase(unittest.TestCase):
    def setUp(self):
        self._saved = (act.READY_SETTLE_S, act.DIALOG_SETTLE_S, act.OFFLINE_SETTLE_S)
        act.READY_SETTLE_S = act.DIALOG_SETTLE_S = act.OFFLINE_SETTLE_S = 0

    def tearDown(self):
        act.READY_SETTLE_S, act.DIALOG_SETTLE_S, act.OFFLINE_SETTLE_S = self._saved


class F2DialogReset(ActReadyBase):
    def test_tablet_escape_closes_a_leftover_dialog_and_records_it(self):
        t = FakeTablet(FakePage(["Abort optimization?"]))
        r, e = ready(t)
        self.assertIsNone(e)
        self.assertEqual(r["ready"]["actions"], ['pressed Escape to close the app\'s dialog "Abort optimization?"'])
        self.assertEqual(t.calls, [("shell", "input", "keyevent", "KEYCODE_ESCAPE")], "one Escape, nothing else")
        self.assertEqual(t.taps(), [], "never a tap")

    def test_two_stacked_dialogs_take_two_presses(self):
        t = FakeTablet(FakePage(["Import file", "Discard changes?"]))
        r, _ = ready(t)
        self.assertEqual(r["ready"]["actions"], ['pressed Escape to close the app\'s dialog "Discard changes?"',
                                                 'pressed Escape to close the app\'s dialog "Import file"'])

    def test_no_dialog_no_key(self):
        t = FakeTablet(FakePage())
        r, _ = ready(t)
        self.assertEqual((r["ready"]["actions"], t.calls), ([], []))

    def test_a_dialog_that_stays_open_blocks_after_three_presses(self):
        t = FakeTablet(FakePage(["Abort optimization?"], closes=False))
        _, e = ready(t)
        self.assertEqual((e.code, e.msg), (2, DIALOG))
        self.assertEqual(len(e.extra["ready"]["actions"]), 3, "the three presses are in ready.actions")
        self.assertEqual(t.page.keys, ["Escape"] * 3)
        self.assertEqual(t.taps(), [])

    def test_never_back_on_a_tablet(self):
        """Review 2026-11-02: Back is the WebView's history back in this app (it leaves the planner, which aborts a
        running optimisation, or sends the app to the background): ready never presses it."""
        self.assertEqual(act.TABLET_DIALOG_KEY, "escape")
        t = FakeTablet(FakePage(["Abort optimization?", "Import file"], closes=False))
        ready(t)
        self.assertNotIn(("shell", "input", "keyevent", "KEYCODE_BACK"), t.calls)
        self.assertEqual({c for c in t.calls}, {("shell", "input", "keyevent", "KEYCODE_ESCAPE")})

    def test_reset_presses_no_other_key(self):
        for key in ("back", "enter", "tab"):
            t = FakeTablet(FakePage(["Abort optimization?"]))
            t.sess = t.page
            with self.assertRaises(act.Fail) as c:
                act.reset_dialogs(t, "QA9", key, [], lambda: {})
            self.assertEqual(c.exception.code, 4)
            self.assertEqual((t.calls, t.page.keys), ([], []), f"{key}: nothing pressed")

    def test_web_escape_never_a_click(self):
        w = FakeWeb(FakePage(["Abort optimization?"]))
        r, e = ready(w)
        self.assertIsNone(e)
        self.assertEqual(r["ready"]["actions"], ['pressed Escape to close the app\'s dialog "Abort optimization?"'])
        self.assertEqual((w.page.keys, w.page.mouse), (["Escape"], []))
        w = FakeWeb(FakePage(["Abort optimization?"], closes=False))
        _, e = ready(w)
        self.assertEqual((e.code, e.msg), (2, DIALOG.replace("QA9", "W044")))
        self.assertEqual(w.page.mouse, [], "no click")

    def test_update_dialog_keeps_its_own_handling(self):
        page = FakePage(update="Update WingtraApp on this tablet?")
        t = FakeTablet(page)

        def not_now(sess, sel, nth, timeout):
            page.update = None  # Not now closes it
            return {"cx": 10, "cy": 10}
        with patched(act, find_waiting=not_now), patched(act.Tablet, webview_origin=lambda self: (0, 0, 100, 100)):
            r, e = ready(t)
        self.assertIsNone(e)
        self.assertEqual(len(t.taps()), 1, "the decline is the one tap ready makes, as before")
        self.assertTrue(r["ready"]["actions"][0].startswith("declined the app's dialog"))
        self.assertEqual(page.keys, [], "no Back for the update dialog")


class F3Offline(ActReadyBase):
    def test_offline_is_reported_not_a_stop(self):
        # run.py decides (needs_network): a case that only reads local files runs on
        t = FakeTablet(FakePage(offline=(True,)))
        r, e = ready(t)
        self.assertIsNone(e)
        self.assertIs(r["offline"], True)
        self.assertIn("the app shows it is offline (no network): check the Wi-Fi", r["ready"]["actions"])

    def test_online_is_not_offline(self):
        r, e = ready(FakeTablet(FakePage(offline=(False,))))
        self.assertIsNone(e)
        self.assertIs(r["offline"], False)

    def test_a_status_that_turns_online_is_not_offline(self):
        act.OFFLINE_SETTLE_S = 2
        t = FakeTablet(FakePage(offline=(True, False)))
        r, e = ready(t)
        self.assertIsNone(e)

    def test_reads_the_apps_own_indicator(self):
        self.assertIn('[data-testid="offline-status-indicator"]', act.OFFLINE_JS)


class F4ForcedUpdate(ActReadyBase):
    def test_forced_update_without_not_now_is_the_persons(self):
        t = FakeTablet(FakePage(update="To continue using the app install the latest version"))

        def missing(sess, sel, nth, timeout):
            raise act.Fail(3, f"not found: {sel}")
        with patched(act, find_waiting=missing):
            _, e = ready(t)
        self.assertEqual(e.code, 2)
        self.assertTrue(e.msg.startswith("Install the app update on QA9 by hand"), e.msg)
        self.assertEqual(t.taps(), [], "never Install")
        self.assertIn("ready", e.extra)


QA_ARGS = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome --user-data-dir=/Users/owner/qa-chrome "
           "--remote-debugging-port=9222 https://web.staging.company.example\n")
STAG = {"type": "page", "id": "APP", "url": "https://web.staging.company.example/sites",
        "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/APP"}
DEV = dict(STAG, id="DEV", url="https://web.dev.company.example/sites")


def web_connect(pages=(STAG,), ps=QA_ARGS, version_exc=None):
    """Web.connect against fakes; returns (result or Fail, ssh argv lists)."""
    sshs = []

    def urlopen(url, timeout=None):
        if url.endswith("/json/version"):
            if version_exc:
                raise version_exc
            return io.BytesIO(json.dumps({"Browser": "Chrome/141",
                                          "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/0b1d0000"}).encode())
        if url.endswith("/json/list"):
            return io.BytesIO(json.dumps(list(pages)).encode())
        return io.BytesIO(b"Target activated")

    def sh(argv, timeout=20, check=False):
        sshs.append(argv)
        return ps
    with patched(act.urllib.request, urlopen=urlopen), patched(act, sh=sh, Session=lambda ws: FakePage(),
                                                               engine_env=lambda: "staging"), \
            patched(act.Web, tunnel=lambda self: "up"):
        try:
            return act.Web().connect(), sshs
        except act.Fail as e:
            return e, sshs


class F4WebSetup(unittest.TestCase):
    def test_qa_chrome_not_running(self):
        e, _ = web_connect(ps="QA_NONE\n", version_exc=urllib.error.URLError(ConnectionResetError("reset")))
        self.assertEqual((e.code, e.extra.get("setup")), (3, True))
        self.assertTrue(e.msg.startswith("Start the QA Chrome on W044: sh ~/qa-files/start-qa-chrome.sh"), e.msg)

    def test_another_browser_on_the_port(self):
        e, _ = web_connect(ps=QA_ARGS.replace("/qa-chrome", "/Library/Application Support/Google/Chrome"))
        self.assertTrue(e.extra.get("setup") and e.msg.startswith(act.QA_CHROME_START) and "not the QA Chrome" in e.msg)

    def test_webpc044_away(self):
        e, _ = web_connect(ps="", version_exc=urllib.error.URLError(OSError("refused")))
        self.assertTrue(e.extra.get("setup") and "not reachable over SSH" in e.msg, e.msg)

    def test_qa_chrome_up_but_tunnel_silent_is_the_engines(self):
        e, _ = web_connect(version_exc=urllib.error.URLError(TimeoutError("timed out")))
        self.assertEqual(e.code, 3)
        self.assertFalse(e.extra.get("setup"), "a tunnel problem stays an ERROR")

    def test_tab_on_another_environment(self):
        e, _ = web_connect(pages=(DEV,))
        self.assertTrue(e.extra.get("setup"))
        self.assertTrue(e.msg.startswith("Open cloud-stag in the QA Chrome on W044"), e.msg)
        self.assertIn("web.dev.company.example", e.msg)

    def test_no_tab_and_two_tabs(self):
        e, _ = web_connect(pages=())
        self.assertTrue(e.extra.get("setup") and e.msg.startswith("Open cloud-stag in the QA Chrome on W044"))
        e, _ = web_connect(pages=(STAG, dict(STAG, id="APP2")))
        self.assertTrue(e.extra.get("setup") and e.msg.startswith("Close the extra cloud-stag tabs"), e.msg)

    def test_one_tab_connects(self):
        r, sshs = web_connect()
        self.assertEqual(r["origin"], "web.staging.company.example")
        self.assertTrue(all(" ".join(act.SSH_KEEPALIVE) in " ".join(a) for a in sshs) and sshs, sshs)

    def test_tunnel_keepalive(self):
        spawned, tries = [], []

        def conn(addr, timeout=None):
            tries.append(addr)
            if len(tries) == 1:
                raise OSError("no tunnel yet")
            return io.BytesIO()
        with patched(act.socket, create_connection=conn), \
                patched(act.subprocess, Popen=lambda argv, **kw: spawned.append(argv)):
            self.assertEqual(act.Web().tunnel(), "started")
        argv = spawned[0]
        for opt in ("ServerAliveInterval=15", "ServerAliveCountMax=4", "ConnectTimeout=10"):
            self.assertIn(opt, argv)
        self.assertEqual(argv[-1], "webpc044")

    def test_tablet_not_connected(self):
        t = FakeTablet(FakePage(), conn="")
        with self.assertRaises(act.Fail) as c:
            act.Tablet.connect(t)
        self.assertEqual((c.exception.code, c.exception.extra.get("setup")), (3, True))
        self.assertIn("connect it and allow USB debugging", c.exception.msg)


# ---------------------------------------------------------------- run.py on stubs

class FakeAct:
    """act.py stand-in: answers by selector or verb (a dict, or a function of args); records every call with its time
    limit, and the order of act calls, clock reads and the HAR start in events."""

    def __init__(self, events, answers=None, flags=("OfflineSync",)):
        self.events, self.answers, self.flags, self.calls = events, answers or {}, list(flags), []

    def __call__(self, target, args, run_id, timeout=90, env=None):
        key = "end shot" if args[0] == "shot" and args[1].endswith("_end.png") else args[0]
        self.calls.append((key, args[1] if len(args) > 1 else None, timeout))
        self.events.append(("act", key))
        a = self.answers.get(args[1] if len(args) > 1 else None) or self.answers.get(key)
        if callable(a):
            a = a(args)
        if a is not None:
            return dict(a, ledger={"day": "2026-11-02", "seq": len(self.calls)})
        out = {"ok": True, "ledger": {"day": "2026-11-02", "seq": len(self.calls)},
               "identity": {"port": 9341, "ua_model": "QA-Stub", "dpr": 1.5, "origin": "web.staging.company.example"}}
        if args[0] == "shot":
            out["shot"] = {"path": args[1], "sha256": "0" * 64}
        elif args[0] == "read" and args[1] == run.BUILD_JS:
            out["value"] = {"git_hash": "a" * 40, "version": "1.74.0", "env": "staging"}
        elif args[0] == "read" and args[1] == run.FLAGS_JS:
            out["value"] = self.flags
        elif args[0] == "read":
            out["value"] = 42
        return out


class FakeHar:
    events = None

    def __init__(self, har_path, console_path):
        pass

    def start(self):
        FakeHar.events.append(("har", "start"))

    def stop(self):
        pass

    def files(self):
        return []


def steps(*extra):
    return [{"n": 1, "text": "Top bar: Sites", "helper": "act.py {target} tap testid:btn-sites --expect path:/sites", "gates": []},
            {"n": 2, "text": "Read the answer", "read_js": "6 * 7", "save_as": "answer", "gates": [],
             "assert": {"kind": "scripted", "expr": "v['answer'] == 42"}}] + list(extra)


def go(case_steps, answers=None, tag="QA9", needs_flags=(), flags=("OfflineSync",), account=None, cloud_writes=None):
    """One TargetRun on stubs; returns (run, fake act, record, events)."""
    records, events = [], []
    case = {"kind": "case", "id": "QA-950", "targets": "web" if tag == "W044" else "tablet",
            "needs": {"link": "none", "flags": list(needs_flags)}, "steps": case_steps}
    if cloud_writes:
        case["cloud_writes"] = cloud_writes
    with tempfile.TemporaryDirectory() as tmp:
        cp = os.path.join(tmp, "QA-950.json")
        pp = os.path.join(tmp, f"{SERIAL}.v1.json")
        for p, obj in ((cp, case), (pp, {"serial": SERIAL})):
            with open(p, "w") as f:
                json.dump(obj, f)
        prof = None if tag == "W044" else {"serial": SERIAL, "tag": tag, "id": f"{SERIAL}.v1", "_path": pp,
                                           "user_agent_model": "QA-Stub", "dpr": 1.5}
        r = run.TargetRun(case, cp, tag, "2026-11-02-fixes-test", "01", prof, ENG)
        r.dir = os.path.join(tmp, "tests", tag)
        r.pred_entry = {"t": "2026-11-02T00:00:00.000Z", "seq": 1, "ref": {"file_sha256": "f" * 64}}
        r.pred_path = os.path.join(tmp, "PR-none.json")
        r.build_record = lambda b, serial: {"env": {"v": "staging"}, "hash7": {"v": "aaaaaaa"}}
        r.tablet_logs = lambda serial, clock, ended: events.append(("logs", None)) or []
        dev = FakeAct(events, answers, flags)

        def clock(*a):
            events.append(("clock", a[-2]))
            return {"at": a[-2], "offset_ms": -37, "target_raw": "0"}
        FakeHar.events = events
        har_mod = type(sys)("har")
        har_mod.HarRecorder = FakeHar
        acc = account or (lambda t, rid, eng: (True, "logged in as shared-stag", {"day": "2026-11-02", "seq": 1}))
        stubs = dict(Lock=tp.FakeLock, act=dev, tablet_clock=clock, web_clock=clock, PROGRESS=os.path.join(tmp, "progress"),
                     battery=lambda serial, at, rid: events.append(("battery", at)) or {"at": at, "level": 80},
                     marker=lambda serial, text, rid: events.append(("marker", text)) or {"text": text},
                     check_account=acc, engine_account=lambda eng: {"label": "shared-stag"},
                     runner_event=lambda *a, **kw: {"t": "2026-11-02T00:00:00.000Z", "seq": 1},
                     write_record=lambda path, obj, overwrite=False: records.append(obj),
                     hash_record=lambda path, session="-", note=None: None,
                     har_side=lambda *a: {"observed_by": "har", "ids": []})
        saved = sys.modules.get("har")
        sys.modules["har"] = har_mod
        try:
            with patched(run, **stubs):
                r.run()
        finally:
            if saved is None:
                sys.modules.pop("har", None)
            else:
                sys.modules["har"] = saved
    return r, dev, (records[-1] if records else {}), events


def blocked_ref(rec):
    v = rec.get("verdict") or {}
    return v.get("v"), (v.get("by") or {}).get("kind"), (v.get("by") or {}).get("ref")


class F1StepLimit(unittest.TestCase):
    def test_limits(self):
        L = run.step_limit
        self.assertEqual(L(["read", "1"]), 90, "a read keeps the old 90 s")
        self.assertEqual(L(["tap", "testid:x", "--expect", "path:/x"]), 90, "defaults: 8 + 10 + 30 is under the floor")
        self.assertEqual(L(["wait", "--expect", "js:x", "--timeout", "600"]), 600 + 10 + 30)
        self.assertEqual(L(["tap", "testid:x", "--find-timeout", "30", "--expect", "js:x", "--timeout=80"]), 30 + 80 + 30)
        self.assertEqual(L(["wait", "--expect", "js:x", "--timeout", "5000"]), 15 * 60, "never above 15 minutes")
        self.assertEqual(L(["wait", "--timeout", "nan"]), 90)
        self.assertEqual(L(["type", "testid:x", "--text", "--timeout"]), 90, "a typed text is not a wait")
        self.assertEqual(L(["ready"]), run.READY_LIMIT_S)

    def test_each_step_gets_its_limit(self):
        long_wait = {"n": 3, "text": "Wait for the optimisation", "gates": [],
                     "helper": "act.py {target} wait --expect \"js:window.done\" --timeout 600"}
        r, dev, rec, _ = go(steps(long_wait))
        self.assertEqual(rec["verdict"]["v"], "PASS")
        lim = {k: t for k, _, t in dev.calls}
        self.assertEqual((lim["wait"], lim["tap"], lim["ready"]), (640, 90, run.READY_LIMIT_S))

    def test_a_wait_that_runs_out_stays_an_error(self):
        long_wait = {"n": 3, "text": "Wait", "gates": [], "helper": "act.py {target} wait --expect js:x --timeout 600"}
        r, dev, rec, _ = go(steps(long_wait), answers={"wait": {"ok": False, "code": 3,
                                                               "error": "readback: expected change did not happen"}})
        v = rec["verdict"]
        self.assertEqual((v["v"], v["harness"]["component"]), ("ERROR", "act.py"))

    def test_act_passes_the_limit_to_the_call(self):
        seen = []

        def fake_run(argv, **kw):
            seen.append(kw["timeout"])
            return subprocess.CompletedProcess(argv, 0, stdout='{"ok": true}', stderr="")
        r = fresh_run()
        with patched(r.subprocess, run=fake_run):
            r.act("QA9", ["wait", "--timeout", "600"], "R-1", timeout=r.step_limit(["wait", "--timeout", "600"]))
        self.assertEqual(seen, [640])


class F2F3F4RunVerdicts(unittest.TestCase):
    def test_ready_dialog_blocks_with_the_text_and_the_presses(self):
        presses = ['pressed Escape to close the app\'s dialog "Abort optimization?"'] * 3
        r, dev, rec, ev = go(steps(), answers={"ready": {"ok": False, "code": 2, "error": DIALOG,
                                                         "ready": {"actions": presses}}})
        self.assertEqual(blocked_ref(rec), ("BLOCKED", "precondition", f"device not ready: {DIALOG}"))
        self.assertEqual(rec["attempts"][0]["identity"][0]["actions"], presses)
        self.assertNotIn("tap", [k for k, _, _ in dev.calls], "no case step ran")

    def test_offline_blocks_a_case_that_writes_to_the_cloud(self):
        msg = "QA9 is offline: check its Wi-Fi, then start again."
        offline = {"ready": {"ok": True, "offline": True, "ready": {"actions": []}}}
        _, dev, rec, _ = go(steps(), answers=offline, cloud_writes=[{"step": 1, "what": "x", "object": "y"}])
        self.assertEqual(blocked_ref(rec), ("BLOCKED", "precondition", f"device not ready: {msg}"))
        self.assertNotIn("tap", [k for k, _, _ in dev.calls], "no case step ran")

    def test_offline_lets_a_read_only_case_run(self):
        offline = {"ready": {"ok": True, "offline": True, "ready": {"actions": ["the app shows it is offline"]}}}
        _, _, rec, _ = go(steps(), answers=offline)
        self.assertEqual(rec["verdict"]["v"], "PASS")
        self.assertIn("the app shows it is offline", rec["attempts"][0]["identity"][0]["actions"])

    def test_needs_network(self):
        self.assertFalse(run.needs_network({"needs": {"link": "none"}}))
        self.assertTrue(run.needs_network({"cloud_writes": [{"step": 1}]}))
        self.assertTrue(run.needs_network({"needs": {"network": True}}))
        self.assertFalse(run.needs_network({"cloud_writes": [{"step": 1}], "needs": {"network": False}}))

    def test_setup_failure_at_ready_blocks(self):
        msg = "Start the QA Chrome on W044: sh ~/qa-files/start-qa-chrome.sh (nothing listens on webpc044's port 9222)"
        r, dev, rec, ev = go(steps(), tag="W044", answers={"ready": {"ok": False, "code": 3, "error": msg, "setup": True}})
        self.assertEqual(blocked_ref(rec), ("BLOCKED", "precondition", f"device not ready: {msg}"))
        self.assertNotIn(("har", "start"), ev, "no HAR for a run stopped at ready")
        self.assertNotIn("clock", [k for k, _ in ev], "no clock read for a run stopped at ready")
        self.assertEqual(rec["clock"], [])
        self.assertIn("no HAR", rec["side_effects"]["note"])
        self.assertIsNone(r.error)

    def test_setup_failure_at_the_probe_blocks(self):
        msg = "QATEST0001 is not reachable over USB (adb state: unauthorized, offline or unplugged): connect it and allow USB debugging"
        _, _, rec, ev = go(steps(), answers={"probe": {"ok": False, "code": 3, "error": msg, "setup": True}})
        self.assertEqual(blocked_ref(rec), ("BLOCKED", "precondition", f"device not ready: {msg}"))

    def test_pre_step_failure_without_setup_stays_an_error(self):
        _, _, rec, _ = go(steps(), answers={"probe": {"ok": False, "code": 3, "error": "page evaluation failed: x"}})
        self.assertEqual((rec["verdict"]["v"], rec["verdict"]["harness"]["component"]), ("ERROR", "act.py"))
        _, _, rec, _ = go(steps(), answers={"ready": {"ok": False, "code": 3, "error": "act.py ready timed out after 150 s"}})
        self.assertEqual((rec["verdict"]["v"], rec["verdict"]["harness"]["component"]), ("ERROR", "act.py ready"))

    def test_the_same_failure_at_case_step_5_keeps_todays_verdict(self):
        more = [{"n": n, "text": f"step {n}", "read_js": "1", "gates": []} for n in (3, 4)]
        step5 = {"n": 5, "text": "Tap", "helper": "act.py {target} tap testid:step5 --expect path:/x", "gates": []}
        msg = "QATEST0001 is not reachable over USB (adb state: unauthorized, offline or unplugged): connect it and allow USB debugging"
        _, _, rec, _ = go(steps(*more, step5), answers={"testid:step5": {"ok": False, "code": 3, "error": msg, "setup": True}})
        v = rec["verdict"]
        self.assertEqual((v["v"], v["harness"]["component"], v["harness"]["detail"]), ("ERROR", "act.py", msg))
        self.assertEqual([s["n"] for s in rec["attempts"][0]["steps"]], [1, 2, 3, 4, 5])

    def test_clock_and_har_start_after_ready(self):
        _, _, rec, ev = go(steps(), tag="W044")
        self.assertEqual(rec["verdict"]["v"], "PASS")
        self.assertLess(ev.index(("act", "ready")), ev.index(("clock", "start")))
        self.assertLess(ev.index(("act", "ready")), ev.index(("har", "start")))
        self.assertLess(ev.index(("har", "start")), ev.index(("act", "probe")))
        _, _, rec, ev = go(steps())
        self.assertEqual([e for e in ev if e[0] != "act"][:3], [("clock", "start"), ("battery", "start"),
                                                                 ("marker", "R-20261102-QA9-QA-950-01 a1 start")])
        self.assertLess(ev.index(("act", "ready")), ev.index(("clock", "start")))
        self.assertIn(("logs", None), ev)

    def test_tablet_blocked_at_ready_reads_no_end_evidence_but_takes_the_end_shot(self):
        _, dev, rec, ev = go(steps(), answers={"ready": {"ok": False, "code": 2, "error": "QA9 is offline: x"}})
        self.assertFalse({"clock", "battery", "marker", "logs"} & {k for k, _ in ev}, ev)
        self.assertIn("end shot", [k for k, _, _ in dev.calls])
        self.assertTrue(any(e["path"].endswith("_end.png") for e in rec["attempts"][0]["evidence"]))


class F5FlagsAndSession(unittest.TestCase):
    def test_flag_needs(self):
        case = {"needs": {"flags": ["A", "B: on", "C: off", "D:OFF", " E : On ", ""]}}
        self.assertEqual(run.flag_needs(case), [("A", True), ("B", True), ("C", False), ("D", False), ("E", True)])

    def test_flag_off_where_needed_on(self):
        _, _, rec, _ = go(steps(), needs_flags=["OfflineSync: on"], flags=[])
        self.assertEqual(blocked_ref(rec), ("BLOCKED", "precondition",
                                            "flags: OfflineSync is off for the engine account (shared-stag) on QA9; the test "
                                            "needs it on."))
        self.assertEqual(rec["flags"]["relevant"], {"OfflineSync": False})

    def test_flag_on_where_needed_off(self):
        _, _, rec, _ = go(steps(), needs_flags=["OfflineSync: off"], flags=["OfflineSync"])
        self.assertEqual(blocked_ref(rec)[2], "flags: OfflineSync is on for the engine account (shared-stag) on QA9; the test "
                         "needs it off.")

    def test_right_values_run(self):
        for need, have in ((["OfflineSync: off"], []), (["OfflineSync"], ["OfflineSync"]), (["OfflineSync: on"], ["OfflineSync"])):
            _, _, rec, _ = go(steps(), needs_flags=need, flags=have)
            self.assertEqual(rec["verdict"]["v"], "PASS", (need, have))

    def test_unreadable_flags_are_not_a_block(self):
        _, _, rec, _ = go(steps(), needs_flags=["OfflineSync: on"],
                          answers={run.FLAGS_JS: {"ok": False, "code": 3, "error": "page evaluation failed"}})
        self.assertEqual((rec["verdict"]["v"], rec["flags"]["st"]), ("PASS", "unverified"))

    def account(self, value):
        want = {"label": "shared-stag", "tenant_id": "t1", "email_sha256": "e" * 64}
        r = fresh_run()
        with patched(r, act=lambda tag, args, rid, timeout=90, env=None: {"ok": True, "value": value, "ledger": None},
                     engine_account=lambda eng: want):
            return r.check_account("TA5", "R-1", ENG)

    def test_account_session(self):
        me = {"loggedIn": True, "email_sha256": "e" * 64, "tenantId": "t1"}
        self.assertEqual(self.account(dict(me, sessionExpired=True))[:2], (False, "Log in again on TA5 (the session expired)"))
        self.assertEqual(self.account(dict(me, sessionExpired=False))[:2], (True, "logged in as shared-stag"))
        self.assertEqual(self.account(me)[:2], (True, "logged in as shared-stag"), "an older page script: no field")
        other = dict(me, email_sha256="0" * 64, sessionExpired=True)
        self.assertEqual(self.account(other)[:2], (False, "logged in as another user, not shared-stag"))

    def test_account_js_keeps_the_token_in_the_page(self):
        js = run.ACCOUNT_JS
        self.assertIn("j.exp", js)
        self.assertIn("refreshTokenIssuedAt", js)
        ret = js[js.index("return {loggedIn: true"):]
        self.assertNotRegex(ret, r"\b(t|p|exp|issued)\b\s*[,}]", "only true/false leaves the page about the session")

    def test_expired_session_blocks_the_run(self):
        acc = lambda t, rid, eng: (False, f"Log in again on {t} (the session expired)", {"day": "2026-11-02", "seq": 1})  # noqa: E731
        _, dev, rec, _ = go(steps(), account=acc)
        self.assertEqual(blocked_ref(rec), ("BLOCKED", "precondition", "account: Log in again on QA9 (the session expired)"))
        self.assertEqual(rec["account"]["st"], "unverified")


class F7EndShot(unittest.TestCase):
    def test_end_shot_before_the_clean_up(self):
        cleanup = {"n": 3, "text": "Back to Sites", "always": True, "gates": [],
                   "helper": "act.py {target} key back --expect path:/sites"}
        case_steps = steps(cleanup)
        case_steps[1]["assert"]["expr"] = "v['answer'] == 41"  # step 2 FAILs, so step 3 runs as clean-up
        _, dev, rec, _ = go(case_steps)
        self.assertEqual(rec["verdict"]["v"], "FAIL")
        order = [k for k, _, _ in dev.calls]
        self.assertLess(order.index("end shot"), order.index("key"), order)
        self.assertEqual([c["n"] for c in rec["attempts"][0]["cleanup"]], [3])

    def test_pass_keeps_the_end_shot(self):
        _, dev, rec, _ = go(steps())
        order = [k for k, _, _ in dev.calls]
        self.assertEqual((rec["verdict"]["v"], order[-1]), ("PASS", "end shot"))


class ReviewCleanUp(unittest.TestCase):
    """Review 2026-11-02: clean-up steps run only after a case step ran. A run stopped before step 1 changed nothing,
    and the screen it stopped on (a dialog ready would not close, the app offline) is the person's."""
    CLEANUP = {"n": 3, "text": "Exit without saving", "always": True, "gates": [],
               "helper": "act.py {target} tap \"text:Exit without saving\" --expect path:/sites"}

    def inputs(self, dev):
        return [(k, sel) for k, sel, _ in dev.calls if k in ("tap", "type", "key", "sys-tap", "choose-file")]

    def test_blocked_before_step_1_runs_no_clean_up(self):
        for tag, answers, kw in (
                ("QA9", {"ready": {"ok": False, "code": 2, "error": DIALOG}}, {}),
                ("QA9", {"ready": {"ok": False, "code": 2, "error": "QA9 is offline: check its Wi-Fi, then start again."}}, {}),
                ("W044", {"ready": {"ok": False, "code": 3, "error": "Open cloud-stag in the QA Chrome on W044", "setup": True}}, {}),
                ("QA9", {}, {"needs_flags": ["OfflineSync: on"], "flags": []})):
            _, dev, rec, _ = go(steps(self.CLEANUP), answers=answers, tag=tag, **kw)
            self.assertEqual(rec["verdict"]["v"], "BLOCKED", (tag, answers, kw))
            self.assertEqual(self.inputs(dev), [], f"no clean-up input after {rec['verdict']['by']['ref']}")
            self.assertEqual(rec["attempts"][0]["cleanup"], [])
            self.assertIn("end shot", [k for k, _, _ in dev.calls], "the end shot (a read) is still taken")

    def test_clean_up_runs_once_a_step_ran(self):
        _, dev, rec, _ = go(steps(self.CLEANUP), answers={"testid:btn-sites": {"ok": False, "code": 3, "error": "covered"}})
        self.assertEqual(rec["verdict"]["v"], "ERROR")
        self.assertEqual(self.inputs(dev), [("tap", "testid:btn-sites"), ("tap", "text:Exit without saving")])
        self.assertEqual([c["n"] for c in rec["attempts"][0]["cleanup"]], [3])


class ReviewGate(unittest.TestCase):
    """Review 2026-11-02 of F6 (audit item 20): every run of adb without -s that the live gate denies is still denied,
    wherever it stands; reads stay text. The rows (gate_fixtures_review.jsonl) go through gate.decide itself here, and
    through the hook entry in test_gate.py."""

    @classmethod
    def setUpClass(cls):
        import gate
        cls.gate = gate
        with open(os.path.join(HERE, "gate_fixtures_review.jsonl"), encoding="utf-8") as f:
            cls.rows = [json.loads(line) for line in f]

    def test_rows(self):
        self.assertGreater(len(self.rows), 80)
        for fx in self.rows:
            with self.subTest(cmd=fx["tool_input"]["command"][:80]):
                self.assertEqual(self.gate.decide(fx["tool_name"], fx["tool_input"])[0], fx["expect"])

    def test_every_review_run_is_one_the_live_reading_denies_or_a_serial_after_the_command(self):
        # the rows are runs, not stricter rules for text: each is denied by the old mention reading too, except the two
        # where -s/-d stand after adb's command (adb shell ls -s x), which the old reading took for a serial
        B = "a" + "db"
        for fx in self.rows:
            c = fx["tool_input"]["command"]
            if fx["category"] != "review_run" or c in (f"{B} shell ls -s foo", f"{B} shell ls -d /sdcard") \
                    or c.startswith(("=", "sort --compress", "`echo")):
                continue
            with self.subTest(cmd=c[:80]):
                self.assertTrue(any(not has and a.strip() for a, has in self.gate._old_reading(c, c)), c)

    def test_helpers(self):
        g = self.gate
        self.assertEqual(g._shell_code(["-c", "--", "x"]), "x")
        self.assertEqual(g._shell_code(["-ce", "x"]), "x")
        self.assertEqual(g._shell_code(["-c", "-x", "x"]), "x")
        self.assertEqual(g._shell_code(["-o", "pipefail", "-c", "x"]), "x")
        self.assertIsNone(g._shell_code(["script.sh"]))
        self.assertIsNone(g._shell_code([]))
        self.assertTrue(g._interp_stdin([]) and g._interp_stdin(["-I", "-"]) and g._interp_stdin(["/dev/stdin"]))
        self.assertFalse(g._interp_stdin(["-I", "-c", "x"]) or g._interp_stdin(["tool.py"]) or g._interp_stdin(["-m", "json.tool"]))
        self.assertTrue(g._adb_serial(["-s", "X", "shell"]) and g._adb_serial(["-P", "5037", "-s", "X"]) and g._adb_serial(["-d"]))
        self.assertFalse(g._adb_serial(["shell", "ls", "-s", "x"]) or g._adb_serial(["-s"]) or g._adb_serial(["-t", "1", "shell"]))


if __name__ == "__main__":
    unittest.main()
