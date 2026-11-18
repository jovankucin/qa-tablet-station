"""Engine piece 4 (2026-10-26): act.py choose-file on W044 against a fake browser session. No device, no network.

python3 -I qa/runner/tests/test_choose_file.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.dirname(HERE), HERE]
import act  # noqa: E402
import autonomy  # noqa: E402
import gate  # noqa: E402
import run  # noqa: E402

fails = checks = 0
NAME = "Z11_test.tif"


def check(cond, what):
    global fails, checks
    checks += 1
    if not cond:
        fails += 1
        print("FAIL", what)


class FakeSess:
    """Records CDP calls; a click opens the file chooser (an event) unless no_chooser."""

    def __init__(self, no_chooser=False):
        self.calls, self.events, self.no_chooser, self.intercept = [], [], no_chooser, False

    def call(self, method, params=None):
        assert method in act.Session.ALLOWED, method
        self.calls.append((method, params or {}))
        if method == "Page.setInterceptFileChooserDialog":
            self.intercept = params["enabled"]
        if method == "Input.dispatchMouseEvent" and params["type"] == "mouseReleased" and not self.no_chooser:
            assert self.intercept, "the click came before the interception was on"
            self.events.append({"method": "Page.fileChooserOpened", "params": {"backendNodeId": 77, "mode": "selectSingle"}})
        return {}

    def wait_event(self, method, timeout):
        for m in self.events:
            if m["method"] == method:
                return m["params"]
        raise act.Fail(3, f"no {method} within {timeout:g} s")

    def eval(self, expr):
        return None  # no update dialog; readbacks are faked below


class FakeWeb(act.Web):
    def __init__(self, sess, on_disk=True):
        super().__init__()
        self.sess, self.on_disk, self.origin = sess, on_disk, "web.staging.company.example"

    def connect(self):
        return {"origin": self.origin}

    def remote(self, cmd, timeout=act.DELIVER_CMD_S):
        assert cmd == f"test -f qa-fixtures/{NAME} && echo $HOME", cmd
        return "/Users/qa\n" if self.on_disk else ""


def run_choose(web, args):
    real = act.resolve_target, act.check_lock, act.load_profiles, act.find_waiting, act.wait_all
    act.resolve_target, act.check_lock, act.load_profiles = (lambda n, p: web), (lambda n: None), (lambda: {})
    act.find_waiting = lambda sess, sel, nth, timeout: {"tag": "div", "text": "", "testid": "file-drop-modal-drop-zone",
                                                        "label": "", "inDialog": True, "cx": 100, "cy": 200}
    act.wait_all = lambda t, conds, timeout: {c["kind"] + ":" + c["value"]: True for c in conds}
    try:
        return act.run(act.parser().parse_args(["W044", "choose-file", "testid:file-drop-modal-drop-zone"] + args)), None
    except act.Fail as e:
        return None, e
    finally:
        act.resolve_target, act.check_lock, act.load_profiles, act.find_waiting, act.wait_all = real


ok_args = ["--file", NAME, "--expect", f"text:{NAME}"]
s = FakeSess()
res, e = run_choose(FakeWeb(s), ok_args)
methods = [m for m, _ in s.calls]
check(e is None and res["file"]["path"] == f"/Users/qa/qa-fixtures/{NAME}", f"the file is given ({e and e.msg})")
check(("DOM.setFileInputFiles", {"files": [f"/Users/qa/qa-fixtures/{NAME}"], "backendNodeId": 77}) in s.calls,
      "DOM.setFileInputFiles on the chooser's input")
check(methods.index("Page.setInterceptFileChooserDialog") < methods.index("Input.dispatchMouseEvent"),
      "interception is on before the click")
check(s.calls[-1] == ("Page.setInterceptFileChooserDialog", {"enabled": False}), "interception is off at the end")

s = FakeSess(no_chooser=True)
res, e = run_choose(FakeWeb(s), ok_args)
check(e and e.code == 3 and "fileChooserOpened" in e.msg, "no chooser: Fail 3")
check(s.calls[-1] == ("Page.setInterceptFileChooserDialog", {"enabled": False}), "interception is off after a failure")

s = FakeSess()
res, e = run_choose(FakeWeb(s, on_disk=False), ok_args)
check(e and e.code == 2 and "delivered first" in e.msg and not s.calls, "file not on W044: Fail 2, nothing clicked")
for bad in ("../x.tif", "a b.tif", "", NAME + ".qa-partial"):
    s = FakeSess()
    res, e = run_choose(FakeWeb(s), ["--file", bad, "--expect", "text:x"])
    check(e and e.code == 4 and not s.calls, f"--file {bad!r}: Fail 4, nothing clicked")
res, e = run_choose(FakeWeb(FakeSess()), ["--file", NAME])
check(e and e.code == 4, "choose-file needs --expect")

# a tablet refuses choose-file; a write-list control is refused before any device call
t = act.Tablet("SER1", {"cdp_port": 1})
check(act.write_list_request(act.parser().parse_args(["W044", "choose-file", "text:Delete", "--file", NAME,
                                                       "--expect", "js:true"]), False) is not None,
      "choose-file on a write-list control is refused")

# run.py and the gate know the verb
check(run.helper_call({"helper": f"act.py {{target}} choose-file testid:x --file {NAME} --expect js:true"})[:2] ==
      ("choose-file", "testid:x"), "run.py parses choose-file helpers (the --file value is not the selector)")
check(gate.decide("Bash", {"command": "F=x.tif; python3 -I qa/runner/act.py W044 choose-file testid:x --file $F "
                                      "--expect js:true"})[0] == "ask", "the gate asks for choose-file with a shell expansion")
check(gate.decide("Bash", {"command": f"python3 -I qa/runner/act.py W044 choose-file testid:x --file {NAME} "
                                      "--expect js:true"})[0] == "pass", "the gate passes a literal choose-file")

# autonomy: a web step that taps the drop zone blocks; choose-file does not
ENG = run.engine()
SITE = ENG["test_data"]["site"]["id"]
base = {"id": "X-001", "kind": "case", "status": "active", "targets": "both", "needs": {}, "test_data": {"site_id": SITE}}
tap = {"n": 1, "text": "open the chooser", "gates": [], "helper": "act.py {target} tap testid:file-drop-modal-drop-zone --expect js:true"}
cf = {"n": 2, "on": "web", "text": "choose", "gates": [],
      "helper": f"act.py {{target}} choose-file testid:file-drop-modal-drop-zone --file {NAME} --expect js:true"}
check("web_file" in [b["code"] for b in autonomy.check(dict(base, steps=[tap]), ENG)["blockers"]], "a web drop-zone tap blocks")
check(autonomy.check(dict(base, steps=[dict(tap, on="tablet"), cf]), ENG)["hands_off"], "choose-file on the web leg is hands-off")

print(f"choose-file: {checks} checks, {fails} failures")
sys.exit(1 if fails else 0)
