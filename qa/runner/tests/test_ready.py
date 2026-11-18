"""act.py ready (engine piece 1, 2026-10-26) against a fake tablet: no adb, no device. Also W044's connect, which
every W044 call (ready too) goes through, against a fake QA Chrome: no ssh, no webpc044.

python3 -I qa/runner/tests/test_ready.py
"""
import io
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import act  # noqa: E402

act.READY_SETTLE_S = 0
APP = "mCurrentFocus=Window{1 u0 com.wingtra.cloud.app/com.wingtra.cloud.app.MainActivity}"
USB = "mCurrentFocus=Window{2 u0 com.android.settings/com.android.settings.Settings$UsbDetailsActivity}"
CAL = "mCurrentFocus=Window{3 u0 com.samsung.android.calendar/com.android.calendar.AlertActivity}"


def dump(wake="Awake", keyguard=False, focus=APP, pid="4242"):
    return "\n".join([f"  mWakefulness={wake}", f"    isKeyguardShowing={'true' if keyguard else 'false'}",
                      f"  {focus}", pid])


class FakeTablet(act.Tablet):
    """Plays back a list of state dumps; records every other adb call; connect fails a given number of times."""

    def __init__(self, states, connect_fails=0, connect_code=3, conn="device", connect_exc=None, am_timeout=False):
        super().__init__("FAKE", {"cdp_port": 1})
        self.states, self.calls, self.fails, self.code = list(states), [], connect_fails, connect_code
        self.conn, self.exc, self.am_timeout = conn, connect_exc, am_timeout

    def adb(self, *args, timeout=20):
        if args == ("get-state",):
            return self.conn
        if args[:1] == ("shell",) and "mWakefulness" in args[1]:
            return self.states.pop(0) if len(self.states) > 1 else self.states[0]
        self.calls.append(args)
        if self.am_timeout and args[:2] == ("shell", "am"):
            raise subprocess.TimeoutExpired("am start", 40)
        return ""

    def connect(self):
        if self.fails:
            self.fails -= 1
            if self.exc:
                raise self.exc
            raise act.Fail(self.code, "page not ready")
        return {"path": "/sites"}


fails = 0


def check(cond, what):
    global fails
    if not cond:
        fails += 1
        print("FAIL", what)


def expect_fail(t, code, words):
    try:
        t.ready()
    except act.Fail as e:
        t.failed = e
        return e.code == code and words in e.msg
    return False


# 1. ready already: no input at all
t = FakeTablet([dump()])
r = t.ready()
check(r["ready"]["actions"] == [] and t.calls == [], "a ready tablet gets no input")
check(r["identity"] == {"path": "/sites"}, "identity comes from connect")

# 2. asleep: wake, nothing else
t = FakeTablet([dump(wake="Asleep"), dump()])
r = t.ready()
check(r["ready"]["actions"] == ["woke the screen"], "asleep: woke the screen")
check(t.calls[0] == ("shell", "input", "keyevent", "KEYCODE_WAKEUP"), "the wake is KEYCODE_WAKEUP")

# 3. lock screen without a PIN: dismissed
t = FakeTablet([dump(wake="Asleep"), dump(keyguard=True), dump()])
r = t.ready()
check(r["ready"]["actions"] == ["woke the screen", "dismissed the lock screen"], "no PIN: dismissed")
check(("shell", "wm", "dismiss-keyguard") in t.calls, "dismiss-keyguard used")

# 4. lock screen with a PIN: the person's, nothing else is tried
t = FakeTablet([dump(keyguard=True), dump(keyguard=True)])
check(expect_fail(t, 2, "PIN"), "PIN: Fail 2 that names the PIN")
check(not any(c[:2] == ("shell", "am") for c in t.calls), "PIN: no app start attempted")

# 5. the USB-preferences screen on top: the app is brought to the front over it
t = FakeTablet([dump(focus=USB), dump()])
r = t.ready()
check(r["ready"]["actions"] == ["brought WingtraApp to the front over com.android.settings"], "USB screen: brought to front")
am = [c for c in t.calls if c[:2] == ("shell", "am")]
check(len(am) == 1 and "android.intent.category.LAUNCHER" in am[0] and "com.wingtra.cloud.app/.MainActivity" in am[0],
      "the launcher intent of the app's MainActivity")

# 6. a calendar alert on top of a sleeping tablet
t = FakeTablet([dump(wake="Dozing", focus=CAL), dump(focus=CAL), dump()])
r = t.ready()
check(r["ready"]["actions"] == ["woke the screen", "brought WingtraApp to the front over com.samsung.android.calendar"],
      "dozing + calendar: woke and brought to front")

# 7. the app does not run: started
t = FakeTablet([dump(focus="mCurrentFocus=Window{4 u0 com.sec.android.app.launcher/com.android.launcher3.Launcher}", pid=""), dump()])
r = t.ready()
a = r["ready"]["actions"]
check(len(a) == 1 and a[0].startswith("brought WingtraApp to the front (started it:") and "update" in a[0] and
      a[0].endswith("over com.sec.android.app.launcher"), "not running: started it, and says a pending update may apply")

# 8. something stays on top: the person's
t = FakeTablet([dump(focus=USB), dump(focus=USB)])
check(expect_fail(t, 2, "could not be brought to the front"), "a window that stays on top: Fail 2")

# 9. the page loads late after a start: connect is retried
t = FakeTablet([dump()], connect_fails=2)
check(t.ready()["identity"] == {"path": "/sites"}, "connect retried until the page answers")

# 10. a usage error is not retried
t = FakeTablet([dump()], connect_fails=1, connect_code=4)
check(expect_fail(t, 4, "page not ready"), "a non-3 failure is raised at once")

# 12. not reachable over USB: the person's, and no input is sent or recorded
t = FakeTablet([dump()], conn="")
check(expect_fail(t, 2, "not reachable over USB"), "unreachable: Fail 2 that says so")
check(t.calls == [] and t.failed.extra["ready"]["actions"] == [], "unreachable: no input, no actions")

# 13. an ANR dialog's title names the package but is not the app's window
anr = "mCurrentFocus=Window{5 u0 Application Not Responding: com.wingtra.cloud.app}"
t = FakeTablet([dump(focus=anr), dump()])
r = t.ready()
check(bool(r["ready"]["actions"]) and r["ready"]["actions"][0].startswith("brought WingtraApp to the front"),
      "an ANR dialog is not taken for the app")

# 14. a failure after acting keeps what ready did
t = FakeTablet([dump(wake="Asleep"), dump()], connect_fails=99)
act.READY_CONNECT_S = 0
check(expect_fail(t, 3, "page not ready"), "connect keeps failing: its Fail")
check(t.failed.extra["ready"]["actions"] == ["woke the screen"], "the failure carries the actions already taken")
act.READY_CONNECT_S = 25

# 15. a socket error right after a start is retried
t = FakeTablet([dump()], connect_fails=1, connect_exc=ConnectionResetError("reset"))
check(t.ready()["identity"] == {"path": "/sites"}, "OSError after a start is retried")

# 16. a start that hangs: Fail 3 with the actions so far
t = FakeTablet([dump(wake="Asleep", focus=USB), dump(focus=USB)], am_timeout=True)
check(expect_fail(t, 3, "timed out"), "am start timeout: Fail 3")
check(t.failed.extra["ready"]["actions"] == ["woke the screen"], "the timeout carries the wake")

# 17. W044: connect brings the app tab to the front (/json/activate) and needs it visible (audit finding 3: a ClickUp
# tab in front hid it, Chrome throttled it, taps took 7-11 s and the runs timed out)
BID = "0b1d0000-0000-0000-0000-000000000000"


class FakeSession:
    def __init__(self, states):
        self.states = list(states)

    def eval(self, expr):
        if expr == "document.visibilityState":
            return self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return "/sites"


QA_ARGS = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome --disable-background-timer-throttling "
           "--user-data-dir=/Users/owner/qa-chrome --remote-debugging-port=9222 https://web.staging.company.example\n")


def web_connect(states, args=QA_ARGS):
    """Web.connect against a QA Chrome with a ClickUp tab and the app tab; returns (result or Fail, calls in order).
    args: what ps prints for the process listening on webpc044's port 9222."""
    calls = []
    pages = [{"type": "page", "id": "CLICKUP", "url": "https://app.clickup.com/t/1",
              "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/CLICKUP"},
             {"type": "page", "id": "APP", "url": "https://web.staging.company.example/sites",
              "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/APP"}]

    def urlopen(url, timeout=None):
        calls.append(url.replace(f"http://127.0.0.1:{act.WEB_TUNNEL_PORT}", ""))
        if url.endswith("/json/version"):
            return io.BytesIO(json.dumps({"Browser": "Chrome/141",
                                          "webSocketDebuggerUrl": f"ws://127.0.0.1:9222/devtools/browser/{BID}"}).encode())
        if url.endswith("/json/list"):
            return io.BytesIO(json.dumps(pages).encode())
        return io.BytesIO(b"Target activated")

    def session(ws):
        calls.append("session " + ws)
        return FakeSession(states)
    saved = (act.urllib.request.urlopen, act.sh, act.Session, act.engine_env, act.Web.tunnel)
    act.urllib.request.urlopen, act.Session = urlopen, session
    act.sh = lambda argv, timeout=20, check=False: args
    act.engine_env, act.Web.tunnel = (lambda: "staging"), (lambda self: "up")
    try:
        return act.Web().connect(), calls
    except act.Fail as e:
        return e, calls
    finally:
        act.urllib.request.urlopen, act.sh, act.Session, act.engine_env, act.Web.tunnel = saved


act.WEB_VISIBLE_S = 0.5
r, calls = web_connect(["visible"])
check(isinstance(r, dict) and r["origin"] == "web.staging.company.example" and r["path"] == "/sites", f"W044 visible: connected {r}")
check(calls == ["/json/version", "/json/list", "/json/activate/APP", f"session ws://127.0.0.1:{act.WEB_TUNNEL_PORT}/devtools/page/APP"],
      f"W044: the app tab (not ClickUp) is activated before the session: {calls}")
r, calls = web_connect(["hidden", "visible"])
check(isinstance(r, dict), f"W044: a tab that turns visible a moment after the activate connects ({r})")
r, calls = web_connect(["hidden"])
check(isinstance(r, act.Fail) and r.code == 2 and "stays hidden" in r.msg and r.extra.get("device_state"),
      f"W044: a tab that stays hidden is Fail 2 (the person's, run.py blocks), not a timeout later ({r})")
check("/json/activate/APP" in calls, "W044: the activate was tried before giving up")
# the QA Chrome is told by its profile, so a Chrome update's relaunch (new browser id, not in ~/qa-chrome.log) still counts
r, calls = web_connect(["visible"], QA_ARGS.replace("--disable-background-timer-throttling", "--restart"))
check(isinstance(r, dict), f"W044: the QA Chrome relaunched by an update is still the QA Chrome ({r})")
r, calls = web_connect(["visible"], QA_ARGS.replace("/qa-chrome", "/Library/Application Support/Google/Chrome"))
check(isinstance(r, act.Fail) and r.code == 3 and "not the QA Chrome" in r.msg and calls == ["/json/version"],
      f"W044: another Chrome on port 9222 is refused before any page call ({r}, {calls})")
r, calls = web_connect(["visible"], QA_ARGS.replace("--user-data-dir=/Users/owner/qa-chrome ", ""))
check(isinstance(r, act.Fail) and r.code == 3, f"W044: a Chrome without the QA profile is refused ({r})")
r, calls = web_connect(["visible"], "")
check(isinstance(r, act.Fail) and r.code == 3, f"W044: nothing listening on port 9222 is refused ({r})")
act.WEB_VISIBLE_S = 2

# 11. the verb is known to act.py's parser and to run.py
check(act.parser().parse_args(["TA5", "ready"]).verb == "ready", "act.py parses ready")
print(f"act.py ready: {fails} failures")
sys.exit(1 if fails else 0)
