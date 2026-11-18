"""act.py wifi (2026-11-03, offline sites) against a fake tablet: no adb, no device, no ledger.

python3 -I qa/runner/tests/test_wifi.py

- off and on run cmd wifi set-wifi-enabled disabled|enabled through the tablet's adb helper (always with its serial);
- svc wifi is the fallback when the first is not supported (a non-zero exit, or an error in its output);
- the state is read back from the tablet; a readback that runs out is Fail 3 and keeps what was done;
- --until-offline also waits for the app's offline indicator (act.OFFLINE_JS); W044 is refused (Fail 4), and so are
  a bad selector, --until-offline with on and a tablet attached over Wi-Fi debugging (Fail 2), before any switch;
- the default --timeout is 20 s for wifi and 8 s for the other verbs; run.py's step limit knows it.
"""
import contextlib
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import act  # noqa: E402
import run  # noqa: E402

act.WIFI_POLL_S = 0
FAILS = []
N = [0]
SERIAL = "R52W70ABCDE"
OFF, ON = act.WIFI_SET["off"][0], act.WIFI_SET["on"][0]
SVC_OFF, SVC_ON = act.WIFI_SET["off"][1], act.WIFI_SET["on"][1]


def check(cond, what):
    N[0] += 1
    if not cond:
        FAILS.append(what)
        print("FAIL", what)


class FakeSess:
    def __init__(self, tablet):
        self.t = tablet

    def eval(self, expr):
        return self.t.app_offline  # COND_JS with the OFFLINE_JS expression, or any other js: readback


class WifiTablet(act.Tablet):
    """Wi-Fi as the tablet reports it; answers {command: (output, exit)}; a command that succeeds flips the state
    unless stuck; every adb call is recorded."""

    def __init__(self, serial=SERIAL, state="on", answers=None, stuck=False, conn="device", app_offline=True):
        super().__init__(serial, {"cdp_port": 1, "tag": "TA5"})
        self.state, self.answers, self.stuck, self.conn, self.app_offline = state, answers or {}, stuck, conn, app_offline
        self.calls, self.connected = [], False

    def adb(self, *args, timeout=20):
        self.calls.append(args)
        if args == ("get-state",):
            return self.conn
        if args == ("shell", act.WIFI_STATUS_CMD):
            return {"on": "Wifi is enabled\n", "off": "Wifi is disabled\n"}.get(self.state, "")
        assert args[0] == "shell" and args[1].endswith(" 2>&1; echo QA_RC=$?"), args
        cmd = args[1][:-len(" 2>&1; echo QA_RC=$?")]
        out, rc = self.answers.get(cmd, ("", 0))
        if rc == 0 and not act.WIFI_NOT_SUPPORTED.search(out) and not self.stuck:
            self.state = "off" if cmd.endswith(("disabled", "disable")) else "on"
        return f"{out}QA_RC={rc}\n"

    def connect(self):
        self.connected = True
        self.sess = FakeSess(self)
        return {"port": 1, "ua_model": "QA-Stub", "dpr": 1.5, "origin": "web.staging.company.example", "path": "/sites"}

    def switches(self):
        return [c[1].split(" 2>&1")[0] for c in self.calls if c[0] == "shell" and c[1] != act.WIFI_STATUS_CMD]


@contextlib.contextmanager
def patched(obj, **attrs):
    old = {k: getattr(obj, k) for k in attrs}
    for k, v in attrs.items():
        setattr(obj, k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            setattr(obj, k, v)


def call(t, argv):
    """act.main(argv) with t as the target, no lock file and no ledger; (exit, printed result)."""
    buf = io.StringIO()
    with patched(act, resolve_target=lambda name, profiles: t, check_lock=lambda name: None, ledger=lambda *a: None,
                 load_profiles=lambda: {}), contextlib.redirect_stdout(buf):
        rc = act.main(argv)
    return rc, json.loads(buf.getvalue())


def test_off_and_on():
    t = WifiTablet(state="on")
    rc, res = call(t, ["TA5", "wifi", "off"])
    w = res.get("wifi") or {}
    check(rc == 0 and res["ok"], f"wifi off: exit {rc} {res.get('error')}")
    check(t.switches() == [OFF], f"wifi off runs {OFF!r} only: {t.switches()}")
    check(w.get("command") == OFF and w.get("before") == "on" and w.get("after") == "off" and "waited_s" in w,
          f"wifi off records what it did: {w}")
    check(t.calls[0] == ("get-state",), f"the USB link is checked before anything else: {t.calls[:2]}")
    check(not t.connected and "identity" not in res and "readback" not in res, "a plain wifi call reads no app page")
    t = WifiTablet(state="off")
    rc, res = call(t, ["TA5", "wifi", "on"])
    check(rc == 0 and t.switches() == [ON] and res["wifi"]["after"] == "on", f"wifi on runs {ON!r}: {t.switches()} {res}")
    # the helper act.py uses for its other device shell calls: adb -s <serial> shell ...
    seen = []
    tab = act.Tablet(SERIAL, {"cdp_port": 1, "tag": "TA5"})
    with patched(act, sh=lambda argv, timeout=20, check=False: seen.append(argv) or "QA_RC=0\n"):
        tab.adb("shell", OFF + " 2>&1; echo QA_RC=$?")
    check(seen and seen[0][:4] == ["adb", "-s", SERIAL, "shell"], f"the shell call carries the serial: {seen}")


def test_fallback():
    for first in (("Unknown command: set-wifi-enabled", 255), ("cmd: Can't find service: wifi", 20),
                  ("java.lang.SecurityException: not allowed", 0)):
        t = WifiTablet(state="on", answers={OFF: first})
        rc, res = call(t, ["TA5", "wifi", "off"])
        check(rc == 0 and t.switches() == [OFF, SVC_OFF], f"{first}: falls back to {SVC_OFF!r}: {t.switches()}")
        tried = res["wifi"]["tried"]
        check(res["wifi"]["command"] == SVC_OFF and tried[0]["rc"] == first[1] and first[0][:20] in tried[0]["output"],
              f"{first}: the record names both tries: {res['wifi']}")
    t = WifiTablet(state="off", answers={ON: ("Unknown command: set-wifi-enabled", 255)})
    rc, res = call(t, ["TA5", "wifi", "on"])
    check(rc == 0 and t.switches() == [ON, SVC_ON], f"wifi on falls back to {SVC_ON!r}: {t.switches()}")
    t = WifiTablet(state="on", answers={OFF: ("Unknown command", 255), SVC_OFF: ("Error: no wifi", 1)})
    rc, res = call(t, ["TA5", "wifi", "off"])
    check(rc == 3 and "could not be switched off" in res["error"] and len(res["wifi"]["tried"]) == 2,
          f"neither command: Fail 3 with both tries: {rc} {res}")


def test_readback_timeout():
    t = WifiTablet(state="on", stuck=True)
    t0 = time.time()
    rc, res = call(t, ["TA5", "wifi", "off", "--timeout", "0.3"])
    check(rc == 3 and res["error"].startswith("readback: Wi-Fi on TA5 reads on, not off"), f"readback timeout: {rc} {res}")
    check(res.get("wifi", {}).get("command") == OFF and res["wifi"]["after"] == "on", f"the switch is kept: {res.get('wifi')}")
    check(time.time() - t0 < 5, "the readback stops at --timeout")
    t = WifiTablet(state="unknown", stuck=True)
    rc, res = call(t, ["TA5", "wifi", "on", "--timeout", "0"])
    check(rc == 3 and "reads unknown" in res["error"], f"a state the tablet does not report: {rc} {res.get('error')}")


def test_until_offline_and_expect():
    t = WifiTablet(state="on", app_offline=True)
    rc, res = call(t, ["TA5", "wifi", "off", "--until-offline"])
    check(rc == 0 and t.connected and res["wifi"].get("app_offline") is True and res.get("identity"),
          f"--until-offline waits for the app's indicator: {rc} {res}")
    t = WifiTablet(state="on", app_offline=False)
    rc, res = call(t, ["TA5", "wifi", "off", "--until-offline", "--timeout", "0.2"])
    check(rc == 3 and "does not show its offline indicator" in res["error"] and res["wifi"]["after"] == "off",
          f"no indicator: Fail 3, the switch kept: {rc} {res}")
    t = WifiTablet(state="off", app_offline=True)
    rc, res = call(t, ["TA5", "wifi", "on", "--expect", "js:true"])
    check(rc == 0 and t.connected and res.get("readback") == {"js:true": True}, f"--expect after the switch: {res}")
    t = WifiTablet(state="off", app_offline=False)
    rc, res = call(t, ["TA5", "wifi", "on", "--expect", "js:navigator.onLine", "--timeout", "0.2"])
    check(rc == 3 and res["error"].startswith("readback") and res.get("wifi", {}).get("after") == "on",
          f"an --expect that does not come: Fail 3 with the switch: {rc} {res}")


def test_refusals():
    rc, res = call(act.Web(), ["W044", "wifi", "off"])
    check(rc == 4 and res["error"] == "wifi is for tablets", f"W044: Fail 4: {rc} {res}")
    for argv, words in ((["TA5", "wifi", "down"], "wifi takes off or on"), (["TA5", "wifi"], "wifi takes off or on"),
                        (["TA5", "wifi", "on", "--until-offline"], "--until-offline goes with wifi off"),
                        (["TA5", "wifi", "off", "--expect", "nope"], "bad --expect")):
        t = WifiTablet()
        rc, res = call(t, argv)
        check(rc == 4 and words in res["error"] and t.calls == [], f"{argv}: Fail 4 before any adb call: {rc} {res} {t.calls}")
    for serial in ("192.168.1.20:5555", "adb-R52W70ABCDE-x1y2z3._adb-tls-connect._tcp"):
        t = WifiTablet(serial=serial)
        rc, res = call(t, ["TA5", "wifi", "off"])
        check(rc == 2 and "Wi-Fi debugging" in res["error"] and t.calls == [], f"{serial}: Fail 2, nothing sent: {res}")
    t = WifiTablet(conn="")
    rc, res = call(t, ["TA5", "wifi", "off"])
    check(rc == 3 and res.get("setup") is True and t.switches() == [], f"not on USB: a setup Fail 3: {res}")
    check(act.write_list_request(act.parser().parse_args(["TA5", "wifi", "off"]), True) is None,
          "wifi is not on the write list (a device action, not a cloud write)")


def test_errors_after_the_switch_keep_the_record():
    # review 2026-11-03: an adb call that hangs, or a page that cannot be read, after the switch is still exit 3 with
    # what was switched (act.main's generic handler would drop it)
    class Hangs(WifiTablet):
        def wifi_state(self):
            if self.switches():
                raise act.subprocess.TimeoutExpired(["adb"], 20)
            return super().wifi_state()
    t = Hangs(state="on")
    rc, res = call(t, ["TA5", "wifi", "off"])
    check(rc == 3 and "TimeoutExpired" in res["error"] and res.get("wifi", {}).get("command") == OFF,
          f"a readback that hangs: exit 3 with the switch: {rc} {res}")

    class NoPage(WifiTablet):
        def connect(self):
            raise OSError("urlopen error [Errno 61] Connection refused")
    t = NoPage(state="on")
    rc, res = call(t, ["TA5", "wifi", "off", "--until-offline"])
    check(rc == 3 and "Connection refused" in res["error"] and res.get("wifi", {}).get("after") == "off",
          f"no page after the switch: exit 3 with the switch: {rc} {res}")
    for argv in (["TA5", "wait", "--until-offline", "--expect", "js:true"], ["TA5", "tap", "testid:x", "--until-offline"]):
        t = WifiTablet()
        rc, res = call(t, argv)
        check(rc == 4 and "--until-offline goes with wifi off" in res["error"] and t.calls == [],
              f"{argv}: --until-offline on another verb is refused before any device call: {rc} {res}")


def test_timeouts():
    seen = {}

    def fake_wifi(self, want, deadline):
        seen["budget"] = deadline - time.time()
        return {"want": want, "after": want}
    with patched(WifiTablet, wifi=fake_wifi):
        call(WifiTablet(), ["TA5", "wifi", "off"])
    check(19 < seen.get("budget", 0) <= act.WIFI_TIMEOUT_S, f"wifi's default --timeout is 20 s: {seen}")
    a = act.parser().parse_args(["TA5", "wait", "--expect", "js:true"])
    check(a.timeout is None, "the parser leaves --timeout open; run() sets it per verb")
    with patched(act, resolve_target=lambda name, profiles: (_ for _ in ()).throw(act.Fail(4, "stop")),
                 load_profiles=lambda: {}):
        try:
            act.run(a)
        except act.Fail:
            pass
    check(a.timeout == act.READBACK_TIMEOUT_S == 8.0, f"the other verbs keep 8 s: {a.timeout}")
    check(run.step_limit(["wifi", "off"]) == run.STEP_LIMIT_MIN_S, "wifi on its default fits the 90 s floor")
    check(run.step_limit(["wifi", "off", "--timeout", "100"]) == 100 + 10 + run.STEP_MARGIN_S,
          f"wifi --timeout 100: {run.step_limit(['wifi', 'off', '--timeout', '100'])}")
    check(run.step_limit(["tap", "x", "--timeout", "100"]) == 140, "other verbs unchanged")
    check(run.helper_call({"helper": "act.py {target} --timeout 30 wifi off --until-offline"})[:2] == ("wifi", "off"),
          "run.helper_call reads a wifi step")
    for flag in ("--u", "--until", "--until-off"):
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                act.parser().parse_args(["TA5", "wifi", "off", flag])
            check(False, f"{flag} was taken for --until-offline")
        except SystemExit:
            pass


def main():
    test_off_and_on()
    test_fallback()
    test_readback_timeout()
    test_until_offline_and_expect()
    test_refusals()
    test_errors_after_the_switch_keep_the_record()
    test_timeouts()
    print(f"act.py wifi: {N[0]} checks, {len(FAILS)} failures")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
