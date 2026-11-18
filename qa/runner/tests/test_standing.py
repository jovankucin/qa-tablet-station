"""Engine piece 5 (2026-10-26): the runner's standing rule for write steps. No device.

python3 -I qa/runner/tests/test_standing.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.dirname(HERE), HERE]
import act  # noqa: E402
import autonomy  # noqa: E402
import gate  # noqa: E402
import run  # noqa: E402
import test_progress as tp  # noqa: E402

fails = checks = 0
ENG = run.engine()
SITE = (ENG.get("test_data") or {}).get("site", {}).get("id")
REAL_ACT = run.act  # run.py's own act() (the run-level checks below replace it with a fake device)


def check(cond, what):
    global fails, checks
    checks += 1
    if not cond:
        fails += 1
        print("FAIL", what)


def step(n, helper, gates):
    return {"n": n, "text": f"step {n}", "helper": helper, "gates": gates}


NEXT = step(2, "act.py {target} tap text:Next --expect js:true", ["write_list"])
NEXT_CW = step(3, "act.py {target} tap text:Next --expect js:true", ["write_list", "cloud_write"])
DELETE = step(4, "act.py {target} tap text:Delete --expect js:true", ["write_list", "cloud_write"])
ENTER = step(5, "act.py {target} key enter --expect js:true", ["write_list"])
OTA = step(6, "act.py {target} tap text:Continue --expect js:true", ["write_list", "mocklink_ota"])


def case(steps, cloud_writes=(3, 4), status="active"):
    return {"id": "QA-950", "kind": "case", "status": status, "targets": "tablet", "needs": {"link": "none"},
            "steps": steps, "test_data": {"site_id": SITE},
            "cloud_writes": [{"step": n, "what": "x", "object": "y"} for n in cloud_writes]}


c = case([NEXT, NEXT_CW, DELETE, ENTER, OTA])
check(run.refuse_case(c, ENG) is None, f"the test case is G3-clean ({run.refuse_case(c, ENG)})")
check(run.standing_step(c, NEXT, ENG), "Next (write_list) runs in the runner")
check(run.standing_step(c, NEXT_CW, ENG), "Next (write_list + declared cloud_write) runs in the runner")
check(not run.standing_step(case([NEXT_CW], cloud_writes=()), NEXT_CW, ENG), "an undeclared cloud_write is handed back")
check(not run.standing_step(c, DELETE, ENG), "Delete is handed back")
check(not run.standing_step(c, ENTER, ENG), "the Enter key is handed back")
check(not run.standing_step(c, OTA, ENG), "a mocklink_ota step is handed back")
check(not run.standing_step(case([NEXT], status="draft"), NEXT, ENG), "a draft case is handed back")
check(not run.standing_step(dict(c, test_data={"site_id": "someone-elses-site"}), NEXT, ENG), "a case outside the QA site is handed back")

# act.py: under the runner, Next passes the request check only with QA_STANDING_STEP; Delete and Enter never
a_next = act.parser().parse_args(["ET65", "tap", "text:Next", "--expect", "js:true"])
a_del = act.parser().parse_args(["ET65", "tap", "text:Delete", "--expect", "js:true"])
a_enter = act.parser().parse_args(["ET65", "key", "enter", "--expect", "js:true"])
check(act.write_list_request(a_next, True) is not None, "runner without the standing step: Next refused")
os.environ["QA_STANDING_STEP"] = "2"
check(act.write_list_request(a_next, True) is None, "runner with the standing step: Next passes the request check")
check(act.write_list_request(a_del, True) is not None and act.write_list_request(a_enter, True) is not None,
      "the standing step never lets Delete or Enter through")
el = {"text": "Next", "label": "Next", "inDialog": True}
check(act.is_write_list(el, "text:Next", "Report bug") is not None, "Next in the bug reporter is still refused after the find")
check(act.is_write_list(el, "text:Next", "Import file") is None, "Next in Import file passes the element check")
del os.environ["QA_STANDING_STEP"]

# the gate: nobody but run.py sets QA_STANDING_STEP
for cmd in ("QA_STANDING_STEP=2 QA_RUNNER=1 python3 -I qa/runner/act.py ET65 tap text:Next --expect js:true",
            "export QA_STANDING_STEP=2; python3 -I qa/runner/act.py ET65 tap text:Next --expect js:true"):
    check(gate.decide("Bash", {"command": cmd})[0] == "deny", f"gate denies {cmd[:40]}")

# autonomy: a Next step is no blocker any more, Delete still is
check([b["code"] for b in autonomy.check(case([step(1, "act.py {target} tap testid:x --expect js:true", []), NEXT]),
                                          ENG)["blockers"]] == [], "autonomy: Next is hands-off")
check("write_step" in [b["code"] for b in autonomy.check(case([DELETE]), ENG)["blockers"]], "autonomy: Delete blocks")

# run.py: the standing step runs with QA_STANDING_STEP; Delete is handed back; {run} is filled in page reads


class Dev(tp.Device):
    def __call__(self, target, args, run_id, timeout=90, env=None):
        self.envs = getattr(self, "envs", []) + [(args[0], args[1] if len(args) > 1 else None, dict(env or {}))]
        return super().__call__(target, args, run_id, timeout, env)


with tempfile.TemporaryDirectory() as tmp:
    records = []
    tp.stub_module(records)
    run.PROGRESS = os.path.join(tmp, "progress")
    r, _ = tp.make_run(tmp)
    r.eng = ENG  # the real engine settings: the QA test site the standing rule needs
    r.case.update(test_data={"site_id": SITE}, status="active", cloud_writes=[{"step": 3, "what": "x", "object": "y"}],
                  steps=[{"n": 1, "text": "read", "read_js": "'QA_{run}'", "save_as": "name", "gates": [],
                          "assert": {"kind": "scripted", "expr": "v['name'] == 'QA_' + run and target == 'QA9'"}},
                         dict(NEXT_CW, n=2), dict(DELETE, n=3)])
    r.case["cloud_writes"] = [{"step": 2, "what": "x", "object": "y"}, {"step": 3, "what": "x", "object": "y"}]
    dev = Dev()
    run.act = dev
    real_read = dev.__class__.__call__

    def device(target, args, run_id, timeout=90, env=None):
        out = real_read(dev, target, args, run_id, timeout, env)
        if args[0] == "read" and args[1].startswith("'QA_"):
            out["value"] = args[1].strip("'")
        return out
    run.act = device
    r.run()
    rec = records[-1]
    taps = [(a1, e) for v, a1, e in dev.envs if v == "tap"]
    check(rec["attempts"][0]["steps"][0].get("assert", {}).get("result") is True, "{run} filled in read_js; run and target in the assert")
    check(taps and taps[0][0] == "text:Next" and taps[0][1].get("QA_STANDING_STEP") == "2" and taps[0][1].get("QA_CLOUD_WRITE") == "1",
          f"the Next step ran with QA_STANDING_STEP and QA_CLOUD_WRITE ({taps})")
    check(not [a1 for a1, _ in taps if a1 == "text:Delete"], "the Delete step was not run")
    check(rec["verdict"]["v"] == "BLOCKED" and rec["verdict"]["by"]["kind"] == "handover", f"Delete was handed back ({rec['verdict']})")

# 2026-11-03 (the person's OK): saving a flight the case created in the QA_ site it created itself is a standing step
# (worker.own_flight_save, the New test guard's rule); every other save stays handed back
WL_CW = ["write_list", "cloud_write"]
NAME_QA = step(1, "act.py {target} type testid:input-site-name --text 'QA_{target} offline {time}' --expect js:true", [])
SITE_SAVE = step(2, "act.py {target} tap 'text:Save new site' --expect js:true", ["cloud_write"])
CREATE = step(3, "act.py {target} tap text:Create --expect js:true", ["cloud_write"])
TOOL_SAVE = step(4, "act.py {target} tap text:Save --expect js:true", WL_CW)
SAVE_EXIT = step(5, "act.py {target} tap 'text:Save & exit' --expect js:true", WL_CW)
SAVE_DRAFT = step(6, "act.py {target} tap 'text:Save as draft' --expect js:true", WL_CW)
fc = case([NAME_QA, SITE_SAVE, CREATE, TOOL_SAVE, SAVE_EXIT, SAVE_DRAFT], cloud_writes=(2, 3, 4, 5, 6))
check(run.refuse_case(fc, ENG) is None, f"the flight case is G3-clean ({run.refuse_case(fc, ENG)})")
for s in (TOOL_SAVE, SAVE_EXIT, SAVE_DRAFT):
    check(run.standing_step(fc, s, ENG), f"own-site flight save runs in the runner: {s['helper']}")
check(run.standing_step(fc, dict(SAVE_EXIT, helper="act.py {target} tap 'text:Save  &  EXIT' --expect js:true"), ENG),
      "the label as act.py matches it (spacing and case)")
not_qa = step(1, "act.py {target} type testid:input-site-name --text 'Offline site' --expect js:true", [])
handed_back = {
    "no own site (the flight in the open site)": (case([NAME_QA, CREATE, TOOL_SAVE, SAVE_EXIT], (3, 4, 5)), SAVE_EXIT),
    "a site not named QA_": (case([not_qa, SITE_SAVE, CREATE, SAVE_EXIT], (2, 3, 5)), SAVE_EXIT),
    "no Create": (case([NAME_QA, SITE_SAVE, TOOL_SAVE, SAVE_EXIT], (2, 4, 5)), SAVE_EXIT),
    "Create before the site save": (case([dict(CREATE, n=0), NAME_QA, SITE_SAVE, SAVE_EXIT], (0, 2, 5)), SAVE_EXIT),
    "not in cloud_writes": (case([NAME_QA, SITE_SAVE, CREATE, TOOL_SAVE, SAVE_EXIT], (2, 3, 4)), SAVE_EXIT),
    "cloud_write only (no write_list)": (fc, dict(SAVE_EXIT, gates=["cloud_write"])),
    "write_list only (no cloud_write)": (fc, dict(SAVE_EXIT, gates=["write_list"])),
    "a third gate": (fc, dict(SAVE_EXIT, gates=WL_CW + ["mocklink_ota"])),
    "by aria:": (fc, dict(SAVE_EXIT, helper="act.py {target} tap 'aria:Save & exit' --expect js:true")),
    "by test id": (fc, dict(SAVE_EXIT, helper="act.py {target} tap testid:btn-save-and-exit --expect js:true")),
    "the Edit flight Confirm before the save": (case([NAME_QA, SITE_SAVE, CREATE, step(4, "act.py {target} tap "
                                                      "text:Confirm --expect js:true", []), SAVE_EXIT], (2, 3, 5)), SAVE_EXIT),
    "the New Flight site field": (case([NAME_QA, SITE_SAVE, step(3, "act.py {target} tap testid:new-flight-site-input "
                                        "--expect js:true", []), dict(CREATE, n=4), SAVE_EXIT], (2, 4, 5)), SAVE_EXIT),
    "a draft case": (dict(fc, status="draft"), SAVE_EXIT),
    "a case outside the QA site": (dict(fc, test_data={"site_id": "someone-elses-site"}), SAVE_EXIT),
}
for label in ("Delete", "Share", "Rename", "Save site", "Sync & save", "Log out"):
    handed_back[f"label {label}"] = (fc, dict(SAVE_EXIT, helper=f"act.py {{target}} tap 'text:{label}' --expect js:true"))
for why, (cs, s) in handed_back.items():
    check(not run.standing_step(cs, s, ENG), f"flight save handed back: {why}")

# act.py: under the runner a flight save passes the request check only with QA_STANDING_STEP and QA_CLOUD_WRITE=1, by
# text:; after the find only on one of the planner's save controls
args = {x: act.parser().parse_args(["ET65", "tap", x, "--expect", "js:true"])
        for x in ("text:Save", "text:Save & exit", "text:Save as draft", "aria:Save", "testid:btn-save-and-exit",
                  "testid:btn-wrapper-btn-save-with-missing-components", "text:Delete", "text:Share", "text:Save site")}
check(act.write_list_request(args["text:Save"], True) is not None, "runner, no standing step: Save refused")
os.environ["QA_STANDING_STEP"] = "5"
check(act.write_list_request(args["text:Save"], True) is not None, "standing step without QA_CLOUD_WRITE: Save refused")
os.environ["QA_CLOUD_WRITE"] = "1"
for x in ("text:Save", "text:Save & exit", "text:Save as draft"):
    check(act.write_list_request(args[x], True) is None, f"standing flight save: {x} passes the request check")
for x in ("aria:Save", "testid:btn-save-and-exit", "testid:btn-wrapper-btn-save-with-missing-components",
          "text:Delete", "text:Share"):
    check(act.write_list_request(args[x], True) is not None, f"standing flight save: {x} still refused")
check(act.write_list_request(args["text:Save"], False) is not None, "outside the runner Save still needs --approved")
check(act.write_list_request(a_next, True) is None, "Next still passes as before")
TITLE = "Exiting the flight planning Do you want to save the flight plan?"
planner = [  # (selector, element found, top dialog's text): WingtraCloud 82e23ff4a
    ("text:Save", {"label": "Save", "testids": ["tool-button"]}, ""),                      # FlightPlanEditDone.tsx:183-189
    ("text:Save", {"label": "Save", "inDialog": True, "testids": ["btn-save-with-missing-components",
                   "btn-wrapper-btn-save-with-missing-components"]}, "Missing basic components"),
    ("text:Save & exit", {"label": "Save & exit", "inDialog": True, "testids": ["btn-save-and-exit",
                          "btn-wrapper-btn-save-and-exit"]}, "Flight overview 11/03/2026 - QA_flight"),
    ("text:Save", {"label": "Save", "inDialog": True, "testids": ["btn-dialog-action", "btn-wrapper-btn-dialog-action"]},
     TITLE),                                                                                  # Blocker.tsx:143-160
    ("text:Save as draft", {"label": "Save as draft", "inDialog": True, "testids": ["btn-dialog-action"]}, TITLE),
]
others = [
    ("text:Save", {"label": "Save", "testids": ["btn-save-default-flight"]}, ""),             # another write-list Save
    ("text:Save", {"label": "Save", "testids": ["btn-save-oprn", "tool-button"]}, ""),
    ("text:Save", {"label": "Save", "testids": []}, ""),                                      # an unnamed Save
    ("text:Save", {"label": "Save", "inDialog": True, "testids": ["btn-dialog-action"]}, "Rename flight"),
    ("text:Save", {"label": "Save", "inDialog": False, "testids": ["btn-dialog-action"]}, TITLE),
    ("text:Save & exit", {"label": "Save & exit", "inDialog": True, "testids": ["btn-dialog-action"]}, TITLE),
    ("text:Save & exit", {"label": "Save & exit", "testids": ["tool-button"]}, ""),           # another label's control
    ("text:Save", {"label": "Save site", "testids": ["btn-save-site-true"]}, ""),
    ("text:Save site", {"label": "Save site", "testids": ["btn-save-site-true"]}, ""),
]
for sel, el, title in planner:
    check(act.flight_save_control(el, sel, title) is None, f"planner control passes: {sel} {el['testids']} {title[:20]!r}")
for sel, el, title in others:
    check(act.flight_save_control(el, sel, title) is not None, f"not a planner control: {sel} {el['testids']} {title!r}")


# review 2026-11-03: the site the run created (run.py reads it after Save new site and passes it as QA_OWN_SITE) and
# the planner of a flight in it (WingtraCloud 82e23ff4a: /sites/<siteId>/planning/<flightId>, SiteOverviewRoutesEnum.ts)
OWN = "0f3c2a51-7d4e-4b8a-9c1d-2e5f6a7b8c9d"
FLIGHT = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"
PLANNER = f"/sites/{OWN}/planning/{FLIGHT}"


class FakeTablet:
    """Just enough of act.Tablet for act.run's tap path: no adb, no CDP."""
    serial = "QA-STUB"

    def __init__(self, path=PLANNER):
        self.taps, self.sess, self.path = [], self, path

    def connect(self):
        return {}

    def require_app_focus(self):
        pass

    def eval(self, expr):
        return self.path if expr == "location.pathname" else None

    def tap_css(self, x, y):
        self.taps.append((x, y))
        return {"x": x, "y": y}


def act_tap(sel, el, title="", path=PLANNER):
    """act.run under the runner for one tap, the element found given: (exit code or 0, the taps sent, the result)."""
    t = FakeTablet(path)
    saved = act.load_profiles, act.resolve_target, act.check_lock, act.find_waiting, act.wait_all
    act.load_profiles, act.resolve_target, act.check_lock = (lambda: {}), (lambda name, p: t), (lambda name: None)
    act.find_waiting = lambda sess, s, nth, timeout: dict({"tag": "SPAN", "text": el["label"], "testid": None,
                                                         "inDialog": False, "cx": 10, "cy": 20, "dialogTitle": title}, **el)
    act.wait_all = lambda t, conds, timeout: [{"ok": True}]
    os.environ["QA_RUNNER"] = "1"
    try:
        return 0, t.taps, act.run(act.parser().parse_args(["ET65", "tap", sel, "--expect", "js:true"]))
    except act.Fail as f:
        return f.code, t.taps, dict(f.extra, error=f.msg)
    finally:
        act.load_profiles, act.resolve_target, act.check_lock, act.find_waiting, act.wait_all = saved
        del os.environ["QA_RUNNER"]


os.environ["QA_OWN_SITE"] = OWN
for sel, el, title in planner:
    code, taps, res = act_tap(sel, el, title)
    check(code == 0 and len(taps) == 1 and (res.get("cloud_write") or {}).get("control")
          and res.get("own_site") == {"site": OWN, "path": PLANNER},
          f"act.run taps the planner's {sel} and records the cloud write ({code}, {taps}, {res})")
for sel, el, title in others[:3] + [others[-2]]:  # (Save site is no flight save: the New test guard and G3 cover it)
    code, taps, res = act_tap(sel, el, title)
    check(code == 2 and not taps and "command" in res, f"act.run refuses {sel} {el['testids']} with a hand-back ({code}, {taps})")
# review 2026-11-03: the case rule cannot see the screen (a case that left its flight, a step skipped or on the other
# platform, a duplicate's Create): act.py taps the save only in the planner of a flight in the run's own site
code, taps, res = act_tap(*planner[0], path=PLANNER + "/item/takeoff")
check(code == 0 and len(taps) == 1, f"a planner sub-route of the own flight is the planner ({code}, {res})")
elsewhere = {
    "the planner of a flight in another site (the QA engine site)":
        f"/sites/{SITE or 'a0000001-0000'}/planning/{FLIGHT}",
    "the planner of a flight in a site whose id starts like the own one": f"/sites/{OWN}0/planning/{FLIGHT}",
    "the own site's flight list": f"/sites/{OWN}/flights",
    "the own site without a flight id": f"/sites/{OWN}/planning/",
    "another page": "/sites",
    "a page that did not answer": None,
}
for why, path in elsewhere.items():
    for sel, el, title in planner:
        code, taps, res = act_tap(sel, el, title, path=path)
        check(code == 2 and not taps and "command" in res and "the site this run created" in res.get("error", ""),
              f"act.run refuses {sel} in {why} with a hand-back ({code}, {taps}, {res})")
for own in ("", "create", "a/b", OWN + "/planning", "x" * 65):
    os.environ["QA_OWN_SITE"] = own
    code, taps, _ = act_tap(*planner[2], path=f"/sites/{own}/planning/{FLIGHT}")
    check(code == 2 and not taps, f"act.run refuses the save when the run's site is {own!r} ({code}, {taps})")
del os.environ["QA_OWN_SITE"]
code, taps, _ = act_tap(*planner[2])
check(code == 2 and not taps, "act.run refuses the save when run.py saw no own site (no QA_OWN_SITE)")
os.environ["QA_OWN_SITE"] = OWN
del os.environ["QA_CLOUD_WRITE"]
code, taps, _ = act_tap(*planner[0])
check(code == 2 and not taps, "without QA_CLOUD_WRITE the planner's Save is refused before the find")
del os.environ["QA_STANDING_STEP"]
code, taps, _ = act_tap(*planner[2])
check(code == 2 and not taps, "without the standing step the planner's Save & exit is refused (its test id is on the write list)")
del os.environ["QA_OWN_SITE"]


class PathDev(Dev):
    """Dev that answers run.py's page-path read after Save new site with path."""

    def __init__(self, path):
        super().__init__()
        self.path = path

    def __call__(self, target, args, run_id, timeout=90, env=None):
        out = super().__call__(target, args, run_id, timeout, env)
        if args[:2] == ["read", "location.pathname"]:
            out["value"] = self.path
        return out


# run.py: the own-site flight save runs with QA_STANDING_STEP, QA_CLOUD_WRITE and the site the app opened after the
# case's Save new site (QA_OWN_SITE, review 2026-11-03); without the own site it is handed back
for steps, writes, want, path in (
        ([NAME_QA, SITE_SAVE, CREATE, TOOL_SAVE, SAVE_EXIT], (2, 3, 4, 5), "ran", f"/sites/{OWN}/flights/flights"),
        ([NAME_QA, SITE_SAVE, CREATE, TOOL_SAVE, SAVE_EXIT], (2, 3, 4, 5), "no site seen", "/sites/create"),
        ([NAME_QA, SITE_SAVE, CREATE], (2, 3), "no flight save", f"/sites/{OWN}/flights"),
        ([NAME_QA, CREATE, TOOL_SAVE, SAVE_EXIT], (3, 4, 5), "handed back", None)):
    with tempfile.TemporaryDirectory() as tmp:
        records = []
        tp.stub_module(records)
        run.PROGRESS = os.path.join(tmp, "progress")
        r, _ = tp.make_run(tmp)
        r.eng = ENG
        r.case.update(test_data={"site_id": SITE}, status="active", steps=[dict(s) for s in steps],
                      cloud_writes=[{"step": n, "what": "x", "object": "y"} for n in writes])
        dev = PathDev(path)
        run.act = dev
        r.run()
        rec = records[-1]
        taps = {a1: e for v, a1, e in dev.envs if v == "tap"}
        reads = [a1 for v, a1, e in dev.envs if v == "read" and a1 == "location.pathname"]
        site_step = [x for x in rec["attempts"][0]["steps"] if x["n"] == 2]
        if want in ("ran", "no site seen"):
            site = OWN if want == "ran" else ""
            check(all(taps.get(x, {}).get("QA_STANDING_STEP") == str(n) and taps[x].get("QA_CLOUD_WRITE") == "1"
                      and taps[x].get("QA_OWN_SITE") == site for x, n in (("text:Save", 4), ("text:Save & exit", 5))),
                  f"{want}: the planner's Save and Save & exit ran as standing steps, QA_OWN_SITE {site!r} ({taps})")
            check(reads == ["location.pathname"] and site_step and site_step[0].get("own_site") ==
                  {"path": path, "site": site or None} and len(site_step[0]["actions"]) == 2,
                  f"{want}: one page-path read after Save new site, kept on its step ({reads}, {site_step})")
            check(not any("QA_OWN_SITE" in taps.get(x, {}) for x in ("text:Save new site", "text:Create")),
                  "QA_OWN_SITE goes only with a flight save")
            check(want != "ran" or rec["verdict"]["v"] != "BLOCKED",
                  f"the own-site flight save was not handed back ({rec['verdict']})")
        elif want == "no flight save":
            check(not reads and "own_site" not in site_step[0],
                  f"a case that saves no flight gets no extra read ({reads})")
        else:
            check("text:Save" not in taps and "text:Save & exit" not in taps, f"no flight save tap ran ({taps})")
            check(rec["verdict"]["v"] == "BLOCKED" and rec["verdict"]["by"]["kind"] == "handover"
                  and "text:Save" in rec["verdict"]["by"]["ref"], f"the save was handed back ({rec['verdict']})")


class ChainDev(PathDev):
    """PathDev whose standing taps go through act.py's own-site check (act.own_site_planner) with the env run.py passes,
    on a tablet showing page (where the case left it)."""

    def __init__(self, path, page):
        super().__init__(path)
        self.page = page

    def __call__(self, target, args, run_id, timeout=90, env=None):
        if args[0] == "tap" and (env or {}).get("QA_STANDING_STEP"):
            saved = dict(os.environ)
            os.environ.update(env, QA_RUNNER="1")
            try:
                act.own_site_planner(FakeTablet(self.page), act.parser().parse_args([target] + args))
            except act.Fail as f:
                self.envs.append((args[0], args[1], dict(env, refused=f.msg)))
                return dict(f.extra, ok=False, code=f.code, error=f.msg, ledger={"day": "2026-11-03", "seq": 1})
            finally:
                os.environ.clear()
                os.environ.update(saved)
        return super().__call__(target, args, run_id, timeout, env)


# the chain, run.py to act.py: a case that leaves its flight after Create (to the QA engine site's planner, by taps the
# case rule cannot see) is stopped at the save with a hand-back; staying in its own flight, the saves run
NAV = [step(4, "act.py {target} tap testid:btn-sites --expect js:true", []),
       step(5, "act.py {target} tap 'text:Some older flight' --expect js:true", [])]
for page, want in ((f"/sites/{SITE or 'a0000001-0000'}/planning/{FLIGHT}", "BLOCKED"), (PLANNER, "ran")):
    with tempfile.TemporaryDirectory() as tmp:
        records = []
        tp.stub_module(records)
        run.PROGRESS = os.path.join(tmp, "progress")
        r, _ = tp.make_run(tmp)
        r.eng = ENG
        r.case.update(test_data={"site_id": SITE}, status="active",
                      steps=[dict(NAME_QA), dict(SITE_SAVE), dict(CREATE)] + [dict(s) for s in NAV]
                      + [dict(TOOL_SAVE, n=6), dict(SAVE_EXIT, n=7)],
                      cloud_writes=[{"step": n, "what": "x", "object": "y"} for n in (2, 3, 6, 7)])
        check(run.standing_step(r.case, r.case["steps"][5], ENG),
              "the case rule alone lets the save after the detour run")
        dev = ChainDev(f"/sites/{OWN}/flights", page)
        run.act = dev
        r.run()
        rec = records[-1]
        refused = [e for v, a1, e in dev.envs if v == "tap" and "refused" in e]
        if want == "BLOCKED":
            check(rec["verdict"]["v"] == "BLOCKED" and rec["verdict"]["by"]["kind"] == "handover"
                  and "text:Save" in rec["verdict"]["by"]["ref"] and len(refused) == 1
                  and "the site this run created" in refused[0]["refused"],
                  f"a save after leaving the flight is stopped at act.py's page check ({rec['verdict']}, {refused})")
        else:
            check(rec["verdict"]["v"] != "BLOCKED" and not refused,
                  f"the saves in the own flight ran ({rec['verdict']})")


# review 2026-11-03: run.py's act() takes QA_CLOUD_WRITE, QA_STANDING_STEP and QA_OWN_SITE only from the step's own env,
# never from the environment run.py itself was started with
class Sub:
    def __call__(self, argv, **kw):
        self.env = kw["env"]
        return type("P", (), {"stdout": '{"ok": true}', "stderr": "", "returncode": 0})()


RUNNER_ENV = ("QA_CLOUD_WRITE", "QA_STANDING_STEP", "QA_OWN_SITE")
sub, real_sub = Sub(), run.subprocess.run
run.subprocess.run = sub
os.environ.update({"QA_CLOUD_WRITE": "1", "QA_STANDING_STEP": "9", "QA_OWN_SITE": OWN})
try:
    REAL_ACT("QA9", ["tap", "text:Save", "--expect", "js:true"], "R-20261103-QA9-QA-950-01")
    check(not set(RUNNER_ENV) & set(sub.env),
          f"inherited runner env dropped ({[k for k in RUNNER_ENV if k in sub.env]})")
    REAL_ACT("QA9", ["tap", "text:Save", "--expect", "js:true"], "R-20261103-QA9-QA-950-01",
             env={"QA_CLOUD_WRITE": "1", "QA_STANDING_STEP": "4", "QA_OWN_SITE": "s1"})
    check([sub.env.get(k) for k in RUNNER_ENV] == ["1", "4", "s1"], "the step's own env is passed")
finally:
    run.subprocess.run = real_sub
    for k in RUNNER_ENV:
        os.environ.pop(k, None)

print(f"standing rule: {checks} checks, {fails} failures")
sys.exit(1 if fails else 0)
