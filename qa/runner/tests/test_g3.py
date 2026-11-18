"""G3 (test-data location and side effects, 2026-10-22): run.refuse_case, run.har_side_effects and act.py's
cloud-write record, plus the review fixes around them (crash paths, event ids, handbacks). No device, no real
record: side_effect, the ledger and records are replaced by recorders or point at temporary folders. The real
cases and engine.json are only read.

python3 -I qa/runner/tests/test_g3.py
"""
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import act  # noqa: E402
import records  # noqa: E402
import run  # noqa: E402

FAILS = []
N = [0]


def check(cond, what):
    N[0] += 1
    if not cond:
        FAILS.append(what)
        print("FAIL", what)


def case(steps, **kw):
    c = {"id": "PLAN-900", "status": "active", "needs": {"link": "none"}, "steps": steps}
    c.update(kw)
    return c


def step(n, helper=None, gates=()):
    return {"n": n, "text": f"step {n}", "helper": helper, "gates": list(gates)}


PENDING = {"api_base": "https://api.staging.company.example/api", "account": "shared-stag", "env": "staging",
           "test_data": {"site": {"id": None, "env": "staging"}, "mock_drone": {"vehicle_id": None}}}
READY = {"api_base": "https://api.staging.company.example/api", "account": "shared-stag", "env": "staging",
         "test_data": {"site": {"id": "site-qa-1", "env": "staging"}, "mock_drone": {"vehicle_id": 4242}}}


def test_refuse_case():
    read_only = case([step(1, "act.py {target} tap testid:btn-sites --expect path:/sites")])
    check(run.refuse_case(read_only, PENDING) is None, "read-only case runs while test data is pending")
    check("status draft" in (run.refuse_case(dict(read_only, status="draft"), PENDING) or ""), "draft refused")
    check("status retired" in (run.refuse_case(dict(read_only, status="retired"), PENDING) or ""), "retired refused")
    for helper, why in (("act.py {target} tap text:Save --expect path:/x", "label Save"),
                        ("act.py {target} tap 'aria:Log out' --expect path:/login", "aria Log out"),
                        ("act.py {target} tap testid:btn-wrapper-copy-share-link --expect path:/x", "wrapper test id"),
                        ("act.py {target} tap testid:sidebar-delete-item-button --expect path:/x", "delete test id")):
        r = run.refuse_case(case([step(1, helper)]), PENDING)
        check(r is not None and "no test-data site" in r, f"cloud write by {why} refused while the site is pending")
    r = run.refuse_case(case([step(2, "act.py {target} tap testid:x --expect path:/y", gates=["cloud_write"])]), PENDING)
    check(r is not None and "steps [2]" in r, "cloud_write gate refused, step named")
    r = run.refuse_case(case([step(1, "act.py {target} tap testid:btn-sites --expect path:/sites")],
                             cloud_writes=[{"step": 3, "what": "cloud_create", "object": "flight"}]), PENDING)
    check(r is not None and "no test-data site" in r, "declared cloud_writes refused while the site is pending")
    writes = case([step(1, "act.py {target} tap text:Save --expect path:/x")])
    check("must be site-qa-1" in (run.refuse_case(writes, READY) or ""), "undeclared location refused once the site exists")
    check(run.refuse_case(dict(writes, test_data={"site_id": "site-qa-1"}), READY) is None, "declared site runs")
    check("must be site-qa-1" in (run.refuse_case(dict(writes, test_data={"site_id": "other"}), READY) or ""),
          "another site refused")
    ota = case([step(1, "act.py {target} tap 'text:Update connected drone' --expect path:/plane")], needs={"link": "mocklink"})
    r = run.refuse_case(ota, PENDING)
    check(r is not None and "no dedicated mock drone" in r, "MockLink OTA refused while the mock drone is pending")
    check("does not declare the mock drone" in (run.refuse_case(ota, READY) or ""), "undeclared vehicle refused")
    check(run.refuse_case(dict(ota, test_data={"vehicle_id": 4242}), READY) is None, "declared mock drone runs")
    gate_ota = case([step(1, "act.py {target} tap testid:x --expect path:/y", gates=["mocklink_ota"])], needs={"link": "mocklink"})
    check(run.refuse_case(gate_ota, PENDING) is not None, "mocklink_ota gate refused")
    no_ota = case([step(1, "act.py {target} tap testid:btn-sites --expect path:/sites")], needs={"link": "mocklink"})
    r = run.refuse_case(no_ota, PENDING)
    check(r is not None and "connects a MockLink" in r and "no dedicated mock drone" in r,
          "a MockLink case without OTA steps is refused too while the mock drone is pending (every connect writes)")
    check("does not declare the mock drone" in (run.refuse_case(no_ota, READY) or ""),
          "a MockLink case without OTA steps must declare the mock drone")
    check(run.refuse_case(dict(no_ota, test_data={"vehicle_id": 4242}), READY) is None,
          "a MockLink case without OTA steps runs once it declares the mock drone")
    for helper in ("act.py {target} tap 'text:Add now' --expect path:/fleet",
                   "act.py {target} tap testid:btn-wrapper-activate-connected-drone --expect path:/fleet"):
        r = run.refuse_case(case([step(1, helper)]), PENDING)
        check(r is not None and "no test-data site" in r, f"adding a drone counts as a cloud write: {helper}")
    ota_no_mocklink = case([step(1, "act.py {target} tap 'text:Update drone' --expect path:/x")])
    check(run.refuse_case(ota_no_mocklink, PENDING) is None, "OTA labels without a MockLink link are not a MockLink OTA")


def test_har_side_effects():
    seen = []
    real = run.side_effect
    run.side_effect = lambda campaign, fields, session="-": seen.append((campaign, fields, session)) or f"E-T-{len(seen):03d}"
    try:
        with tempfile.TemporaryDirectory() as d:
            api = "https://api.staging.company.example/api/v1"
            entries = [
                {"request": {"method": "GET", "url": f"{api}/sites"}, "response": {"status": 200}},
                {"request": {"method": "OPTIONS", "url": f"{api}/flights"}, "response": {"status": 204}},
                {"request": {"method": "POST", "url": f"{api}/sites/a0000002-0000-4000-8000-000000000002/flights"},
                 "response": {"status": 201}},
                {"request": {"method": "PUT", "url": f"{api}/tenants/tables-sort-types"}, "response": {"status": 200}},
                {"request": {"method": "DELETE", "url": f"{api}/flights/12345"}, "response": {"status": 204}},
                {"request": {"method": "PATCH", "url": f"{api}/flights/1"}, "unfinished": True},
                {"request": {"method": "POST", "url": f"{api}/flights"}, "response": {"status": 500}},
                {"request": {"method": "POST", "url": f"{api}/flights"}, "failed": {"error": "net::ERR_FAILED"}},
                {"request": {"method": "POST", "url": "https://o1.ingest.sentry.io/api/1/envelope/"}, "response": {"status": 200}},
            ]
            path = os.path.join(d, "R-1.har")
            json.dump({"log": {"entries": entries}}, open(path, "w"))
            ids = run.har_side_effects(path, READY, case([]), "R-1", "2026-10-22-x", "W044")
    finally:
        run.side_effect = real
    check(ids == ["E-T-001", "E-T-002", "E-T-003", "E-T-004", "E-T-005"], f"five API writes recorded, got {ids}")
    whats = [f["what"] for _, f, _ in seen]
    check(whats == ["cloud_create", "cloud_update", "cloud_delete", "cloud_update", "cloud_create"], f"methods mapped, got {whats}")
    last = seen[4][1]["object"]["outcome"] if len(seen) > 4 else None
    check(last == "unknown (request failed: net::ERR_FAILED)", f"a failed request that may have been sent is recorded as unknown, got {last}")
    first = seen[0][1]
    check(first["object"]["route"] == "/api/v1/sites/:id/flights", f"route templated, got {first['object']['route']}")
    check(first["object"]["path"].endswith("a0000002-0000-4000-8000-000000000002/flights"), "the real path is kept")
    check(seen[2][1]["object"]["route"] == "/api/v1/flights/:id", "numeric id templated")
    check(seen[3][1]["object"]["outcome"].startswith("unknown"), "an unfinished request is recorded as unknown outcome")
    check(all(f["declared"] is False for _, f, _ in seen), "undeclared writes are marked declared false")
    check(all(f["src"]["pick"].startswith("log.entries[") and f["observed_by"] == "har" for _, f, _ in seen), "HAR pointer")
    check(all(s == "R-1" and c == "2026-10-22-x" for c, _, s in seen), "campaign and session")


def test_act_cloud_write_record():
    check(act.cloud_write({"label": "Save"}) == "save", "label Save is a cloud write")
    check(act.cloud_write({"text": "Delete", "label": ""}) == "delete", "text Delete is a cloud write")
    check(act.cloud_write({"testid": "btn-wrapper-log-out-btn"}) == "testid:log-out-btn", "wrapper test id")
    check(act.cloud_write({"label": "Settings"}) is None, "an ordinary control is not")
    check([act.side_effect_what(x) for x in ("delete", "testid:log-out-btn", "share", "save", "rename")]
          == ["cloud_delete", "logout", "cloud_create", "cloud_update", "cloud_update"], "what per control")
    seen = []
    real = (act.run, act.ledger, act.side_effect)
    act.run = lambda a: {"target": a.target, "verb": a.verb, "cloud_write": {"control": "save", "route": "/sites/1"}}
    act.ledger = lambda *a, **k: {"t": "2026-10-22T10:00:00.000Z", "seq": 7}
    act.side_effect = lambda campaign, fields, session="-": seen.append((campaign, fields)) or "E-20261022-001"
    os.environ["QA_CAMPAIGN"] = "2026-10-22-qa-site"
    devnull, out = open(os.devnull, "w"), sys.stdout
    try:
        sys.stdout = devnull
        rc = act.main(["TA5", "tap", "text:Save", "--expect", "path:/x", "--approved"])
    finally:
        sys.stdout = out
        act.run, act.ledger, act.side_effect = real
        os.environ.pop("QA_CAMPAIGN", None)
    check(rc == 0 and len(seen) == 1, "one side_effect record after an approved cloud-write tap")
    if seen:
        campaign, f = seen[0]
        check(campaign == "2026-10-22-qa-site", "campaign from QA_CAMPAIGN")
        check(f["what"] == "cloud_update" and f["object"]["control"] == "save" and f["ledger"] == {"day": "2026-10-22", "seq": 7},
              "fields of the act.py side effect")


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


def refused(steps, eng=PENDING, **kw):
    return run.refuse_case(case(steps, **kw), eng)


def test_refuse_more():
    """Review fixes in refuse_case: other create/change controls, page JS writes, look-only steps, OTA labels,
    the site's env and cases without steps."""
    for helper in ("act.py {target} tap testid:btn-create-duplicate-flight --expect path:/x",
                   "act.py {target} tap testid:btn-wrapper-import-flight-dialog-submit-button --expect path:/x",
                   "act.py {target} tap 'text:Archive flight' --expect path:/flights",
                   "act.py {target} tap text:Create --expect path:/x",
                   "act.py {target} tap 'text:Save new site' --timeout 20 --expect path:/sites",
                   "act.py {target} tap --timeout 20 text:Invite --expect path:/x"):
        r = refused([step(1, helper)])
        check(r is not None and "no test-data site" in r, f"G3 control refused while the site is pending: {helper}")
    for label in ("Create", "Archive flight", "Import", "Invite"):
        a = act_args(["TA5", "tap", f"text:{label}", "--expect", "path:/x"])
        check(act.write_list_request(a, True) is None, f"G3 control {label} is not on the write list (no handback)")
    check(not (act.CLOUD_CHANGE_TEXT & act.WRITE_LIST_TEXT) and not (act.CLOUD_CHANGE_TESTID & act.WRITE_LIST_TESTID),
          "the G3 detection set is separate from the write list")
    check(act.WRITE_LIST_TEXT >= {"delete", "share", "save", "rename", "log out"}, "the G2 labels still ask")

    js_writes = (
        dict(step(1), read_js="fetch(api + '/v1/sites', {method: 'POST', body: '{}'}).then(r => r.status)"),
        dict(step(1), read_js="Capacitor.Plugins.CapacitorHttp.post({url: u, data: {}})"),
        dict(step(1, "act.py {target} tap testid:btn-sites --expect path:/sites"), when_js="fetch(u, {\"method\": \"delete\"})"),
        step(1, "act.py {target} wait --expect \"js:fetch(u, {method: 'PATCH'}) && true\""),
        step(1, "act.py {target} read \"window.qc.getMutationCache().build(window.qc, {}).mutate()\""),
    )
    for s in js_writes:
        r = refused([s])
        check(r is not None and "no test-data site" in r and "steps [1]" in r, f"page JS write refused: {json.dumps(s)[:90]}")
    api_post = {"values": [], "api_js": "fetch(b + '/v1/flights', {headers: h, method: 'POST'})"}
    r = refused([step(1, "act.py {target} tap testid:btn-sites --expect path:/sites")], differential=api_post)
    check(r is not None and "differential.api_js" in r, "a writing differential.api_js is refused")
    reads = {"values": [], "api_js": "(async () => (await fetch(b + '/sites/1', {headers: h})).json())()"}
    check(refused([dict(step(1), read_js="[...document.querySelectorAll('x')].map(e => e.innerText)")], differential=reads) is None,
          "GET-only page JS is read-only")

    for helper in ("act.py {target} wait --expect text:Save", "act.py {target} find text:Delete",
                   "act.py {target} tap testid:btn-sites --expect 'text:Share'", "act.py {target} find 'text:Archive flight'"):
        check(refused([step(1, helper)]) is None, f"a step that only looks at a write control is read-only: {helper}")

    for helper in ("act.py {target} tap 'text:Update now' --expect path:/plane",
                   "act.py {target} tap text:Continue --expect path:/plane"):
        r = refused([step(1, helper)], needs={"link": "mocklink"})
        check(r is not None and "no dedicated mock drone" in r, f"MockLink OTA by its label refused: {helper}")

    develop = dict(READY, env="develop")
    writes = [step(1, "act.py {target} tap text:Save --expect path:/x")]
    r = refused(writes, eng=develop, test_data={"site_id": "site-qa-1"})
    check(r is not None and "belongs to env staging" in r, f"a site of another env is refused, got {r}")
    check(refused(writes, eng=READY, test_data={"site_id": "site-qa-1"}) is None, "the site of the engine's env runs")

    r = refused([])
    check(r is not None and "nothing to run" in r, "a case without steps is refused")
    check("nothing to run" in (run.refuse_case({"id": "UPG-900", "status": "active"}, PENDING) or ""), "missing steps refused")


def act_args(argv):
    return type("A", (), {"target": argv[0], "verb": argv[1], "selector": argv[2],
                          "expect": [argv[i + 1] for i, x in enumerate(argv) if x == "--expect"]})()


def test_act_g3_controls():
    """act.py: the G3 set and gated steps leave a side_effect; failures after the tap still leave one."""
    check(act.cloud_write({"label": "Archive flight", "testids": ["none"]}) == "archive flight", "Archive flight is a cloud change")
    check(act.cloud_write({"label": "", "testid": None, "testids": ["btn-create-duplicate-flight", "btn-wrapper-btn-create-duplicate-flight"]})
          == "testid:btn-create-duplicate-flight", "the create test id is found among the element's test ids")
    check(act.cloud_write({"label": "Next", "testids": []}) is None, "Next alone is not a cloud write")
    check(act.cloud_write({"label": "Next", "testids": []}, gated=True) == "next", "a gated step's tap is a cloud write")
    check([act.side_effect_what(x) for x in ("create", "archive flight", "testid:btn-add-drone-confirm", "save new site", "delete all")]
          == ["cloud_create", "cloud_update", "cloud_create", "cloud_create", "cloud_delete"], "what per G3 control")

    seen = []

    class Sess:
        def eval(self, expr):
            return "/sites/1" if expr == "location.pathname" else None

    class T(act.Tablet):
        def __init__(self, fail_in):
            self.serial, self.prof, self.sess, self.origin, self.fail_in = "S1", {"dpr": 1.5}, Sess(), None, fail_in

        def connect(self):
            return {"port": 1}

        def require_app_focus(self):
            return None

        def tap_css(self, cx, cy):
            if self.fail_in == "before-tap":
                raise act.Fail(3, "computed tap is outside the WebView")
            if self.fail_in == "tap":
                raise subprocess.TimeoutExpired(["adb", "shell", "input", "tap"], 20)
            return {"screen": [1, 1]}

        def cond(self, c):
            if self.fail_in == "readback-os":
                raise ConnectionError("closed")
            return True

    def element(label):
        return lambda sess, sel, nth, timeout: {"tag": "BUTTON", "text": label, "label": label, "testid": None, "testids": [],
                                                "inDialog": False, "cx": 10, "cy": 10, "dialogTitle": ""}

    def main_with(fail_in, label, argv, env=None, qa_dir=None):
        seen.clear()
        t = T(fail_in)
        stubs = dict(load_profiles=lambda: {}, check_lock=lambda name: None, find_waiting=element(label),
                     ledger=lambda *a, **k: {"t": "2026-10-22T10:00:00.000Z", "seq": 7}, resolve_target=lambda name, profiles: t,
                     side_effect=lambda campaign, fields, session="-": seen.append((campaign, fields)) or "E-20261022-001")
        if qa_dir:
            stubs["QA_DIR"] = qa_dir
        old_env = {k: os.environ.get(k) for k in ("QA_RUNNER", "QA_RUN_ID", "QA_CAMPAIGN", "QA_HANDBACK_RUN", "QA_CLOUD_WRITE")}
        for k in old_env:
            os.environ.pop(k, None)
        os.environ.update(env or {})
        devnull, out = open(os.devnull, "w"), sys.stdout
        try:
            sys.stdout = devnull
            with patched(act, **stubs):
                rc = act.main(argv)
        finally:
            sys.stdout = out
            devnull.close()
            for k, v in old_env.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v
        return rc

    tap = ["TA5", "tap", "text:Archive flight", "--expect", "path:/x", "--timeout", "0.3"]
    rc = main_with("none", "Archive flight", tap, env={"QA_RUNNER": "1", "QA_RUN_ID": "R-1", "QA_CAMPAIGN": "2026-10-22-c"})
    check(rc == 0 and len(seen) == 1 and seen[0][1]["object"]["control"] == "archive flight",
          "a G3 tap under the runner runs (not asked) and leaves a side_effect")
    check(seen and seen[0][0] == "2026-10-22-c" and seen[0][1]["run"] == "R-1", "under the runner: the run's campaign and id")
    rc = main_with("none", "Next", ["TA5", "tap", "testid:x", "--expect", "path:/x"],
                   env={"QA_RUNNER": "1", "QA_RUN_ID": "R-1", "QA_CLOUD_WRITE": "1"})
    check(rc == 0 and len(seen) == 1, "a tap of a cloud_write-gated step leaves a side_effect whatever its control")
    rc = main_with("readback-os", "Save", ["TA5", "tap", "text:Save", "--expect", "path:/x", "--approved", "--timeout", "0.3"])
    check(rc == 3 and len(seen) == 1, f"a readback ConnectionError after the tap still leaves a side_effect (rc {rc}, {len(seen)})")
    rc = main_with("tap", "Save", ["TA5", "tap", "text:Save", "--expect", "path:/x", "--approved"])
    check(rc == 3 and len(seen) == 1 and seen[0][1]["outcome"].startswith("unknown"),
          f"an adb timeout while tapping leaves a side_effect with outcome unknown (rc {rc}, {seen and seen[0][1]['outcome']})")
    rc = main_with("before-tap", "Save", ["TA5", "tap", "text:Save", "--expect", "path:/x", "--approved"])
    check(rc == 3 and not seen, "a tap refused before any input leaves no side_effect")
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "runner"))
        with open(os.path.join(d, "runner", "engine.json"), "w") as f:
            f.write("{not json")
        rc = main_with("none", "Save", ["TA5", "tap", "text:Save", "--expect", "path:/x", "--approved"], qa_dir=d)
    check(rc == 0 and len(seen) == 1 and seen[0][1]["account"] is None, "a malformed engine.json does not lose the side_effect")
    rc = main_with("none", "Save", ["TA5", "tap", "text:Save", "--expect", "path:/x", "--approved"],
                   env={"QA_CAMPAIGN": "2026-10-22-c", "QA_HANDBACK_RUN": "R-20261022-TA5-PLAN-900-01"})
    check(seen and seen[0][0] == "2026-10-22-c" and seen[0][1]["run"] == "R-20261022-TA5-PLAN-900-01",
          "a handed-back tap records the run it was handed back from")

    old = {k: os.environ.get(k) for k in ("QA_CAMPAIGN", "QA_RUN_ID")}
    os.environ.update(QA_CAMPAIGN="2026-10-22-c", QA_RUN_ID="R-1")
    try:
        cmd = act.hand_back(act_args(["TA5", "tap", "text:Save", "--expect", "path:/x"]))
    finally:
        for k, v in old.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
    check(cmd.startswith("QA_CAMPAIGN=2026-10-22-c QA_HANDBACK_RUN=R-1 python3 -I qa/runner/act.py TA5 tap"),
          f"the handed-back command carries the campaign and run, got {cmd[:80]}")


def test_har_failures():
    """HAR entries that failed after they may have reached the server, and a listener that died."""
    seen = []
    api = "https://api.staging.company.example/api/v1"
    entries = [
        {"request": {"method": "POST", "url": f"{api}/sites/1/flights"}, "response": {"status": 201},
         "failed": {"error": "net::ERR_ABORTED", "canceled": True}},
        {"request": {"method": "POST", "url": f"{api}/flights"}, "failed": {"error": "net::ERR_ABORTED", "canceled": True}},
        {"request": {"method": "POST", "url": f"{api}/flights"}, "failed": {"error": "net::ERR_NAME_NOT_RESOLVED"}},
        {"request": {"method": "DELETE", "url": f"{api}/flights/2"}, "response": {"status": 404},
         "failed": {"error": "net::ERR_ABORTED"}},
    ]
    with tempfile.TemporaryDirectory() as d, patched(run, side_effect=lambda c, f, session="-": seen.append(f) or f"E-T-{len(seen):03d}"):
        path = os.path.join(d, "R-1.har")
        json.dump({"log": {"entries": entries, "_error": "ConnectionError: closed"}}, open(path, "w"))
        side = run.har_side(path, READY, case([]), "R-1", "2026-10-22-x", "W044")
    outs = [f["object"]["outcome"] for f in seen]
    check(outs == ["ok (body load failed: net::ERR_ABORTED)", "unknown (request failed: net::ERR_ABORTED)"],
          f"2xx then cancelled is a write, a cancel before the answer is unknown, never-sent and 404 are not, got {outs}")
    check(side.get("complete") is False and "ConnectionError" in (side.get("error") or ""),
          f"a dead HAR listener marks side_effects incomplete, got {side}")


class FakeHar:
    """har.HarRecorder stand-in: stop() writes a HAR with one POST 201 (a flight the page created)."""
    stopped = 0

    def __init__(self, har_path, console_path):
        self.har_path, self.console_path = har_path, console_path

    def start(self):
        pass

    def stop(self):
        FakeHar.stopped += 1
        entries = [{"request": {"method": "POST", "url": "https://api.staging.company.example/api/v1/sites/1/flights"},
                    "response": {"status": 201}}]
        json.dump({"log": {"entries": entries}}, open(self.har_path, "w"))
        open(self.console_path, "w").close()

    def files(self):
        return []


def device(order, envs, crash_on=None):
    def act_stub(target, args, run_id, timeout=90, env=None):
        order.append(args[0])
        envs.append((args[0], dict(env or {})))
        if args[0] == crash_on:
            raise OSError("stub device fell over")
        out = {"ok": True, "ledger": {"day": "2026-10-22", "seq": 1},
               "identity": {"browser_id": "abcd", "origin": "web.staging.company.example", "port": 1, "ua_model": "QA-Stub", "dpr": 1.5}}
        if args[0] == "shot":
            out["shot"] = {"path": args[1], "sha256": "0" * 64}
        elif args[0] == "read" and args[1] == run.BUILD_JS:
            out["value"] = {"git_hash": "a" * 40, "version": "1.74.0", "env": "staging"}
        elif args[0] == "read" and args[1] == run.FLAGS_JS:
            out["value"] = []
        elif args[0] == "read":
            out["value"] = 1
        return out
    return act_stub


def target_run(tmp, steps, tag, write_record=None, end_clock=None, crash_on=None):
    """One TargetRun on stubs: no device, ledger or real record; returns (run, call order, act envs, records, side effects)."""
    order, envs, records, sides = [], [], [], []

    class Lk:
        def __init__(self, name, holder, case):
            pass

        def __enter__(self):
            order.append("lock")
            return self

        def __exit__(self, *exc):
            order.append("unlock")

    def clock(*a):
        at = a[-2]
        if at == "end" and end_clock:
            raise end_clock
        return {"at": at, "offset_ms": 3, "target_raw": "1"}

    real_wp = run.write_progress
    c = {"kind": "case", "id": "QA-910", "targets": "web" if tag == "W044" else "tablet", "needs": {"link": "none"}, "steps": steps}
    cp = os.path.join(tmp, "QA-910.json")
    json.dump(c, open(cp, "w"))
    prof = {"serial": "QATEST0002", "tag": tag, "id": "QATEST0002.v1", "_path": cp, "user_agent_model": "QA-Stub", "dpr": 1.5}
    eng = {"env": "staging", "web_origin": "web.staging.company.example", "api_base": "https://api.staging.company.example/api",
           "account": "shared-stag"}
    r = run.TargetRun(c, cp, tag, "2026-10-22-g3-test", "01", prof, eng)
    r.dir = os.path.join(tmp, "tests", tag)
    r.pred_entry = {"t": "2026-10-22T00:00:00.000Z", "seq": 1, "ref": {"file_sha256": "f" * 64}}
    r.pred_path = os.path.join(tmp, "PR-none.json")
    r.build_record = lambda b, serial: {"env": {"v": "staging"}, "hash7": {"v": "aaaaaaa"}}
    r.tablet_logs = lambda serial, clock, ended: []
    stubs = dict(Lock=Lk, act=device(order, envs, crash_on), web_clock=clock, tablet_clock=clock,
                 battery=lambda serial, at, rid: {"at": at}, marker=lambda serial, text, rid: {"text": text},
                 check_account=lambda tag, rid, eng: (True, "ok", {"day": "2026-10-22", "seq": 1}),
                 engine_account=lambda eng: {"label": "shared-stag"},
                 runner_event=lambda *a, **kw: {"t": "2026-10-22T00:00:00.000Z", "seq": 1},
                 write_record=write_record or (lambda path, obj, overwrite=False: records.append(obj)),
                 hash_record=lambda path, session="-", note=None: None,
                 side_effect=lambda campaign, fields, session="-": sides.append((campaign, fields)) or f"E-T-{len(sides):03d}",
                 write_progress=lambda name, obj: order.append(f"progress:{obj['state']}:{obj['verdict']}") or real_wp(name, obj),
                 PROGRESS=os.path.join(tmp, "progress"), QA_DIR=os.path.join(tmp, "qa"))
    real_har = sys.modules.get("har")
    sys.modules["har"] = type(sys)("har")
    sys.modules["har"].HarRecorder = FakeHar
    try:
        with patched(run, **stubs):
            r.run()
    finally:
        if real_har is None:
            sys.modules.pop("har", None)
        else:
            sys.modules["har"] = real_har
    return r, order, envs, records, sides


def test_target_runs():
    """TargetRun: a crash after the HAR started, progress before the unlock, a record that cannot be written,
    write_list gates handed back, and the campaign passed to act.py."""
    read = [{"n": 1, "text": "read", "read_js": "1", "gates": []}]
    with tempfile.TemporaryDirectory() as tmp:
        FakeHar.stopped = 0
        r, order, _, records, sides = target_run(tmp, read, "W044", crash_on="probe")
        check(r.error and "OSError" in r.error and records and records[-1]["verdict"]["v"] == "ERROR", f"crash recorded: {r.error}")
        check(FakeHar.stopped == 1 and len(sides) == 1, f"a crash after har.start stops the HAR and records its POST ({FakeHar.stopped}, {len(sides)})")
        check(records and (records[-1].get("side_effects") or {}).get("ids") == ["E-T-001"], "the crash record names the side effects")
        fin = [i for i, x in enumerate(order) if x.startswith("progress:finished")]
        check(fin and fin[-1] < order.index("unlock"), f"finished progress is written before the unlock: {order[-4:]}")

        # an end clock that cannot be read (finding 12): a note on the attempt, the verdict and the HAR's side effects stay
        FakeHar.stopped = 0
        r, order, _, records, sides = target_run(tmp, read, "W044", end_clock=ValueError("could not convert string to float: ''"))
        rec = records[-1] if records else {}
        check(r.error is None and rec.get("verdict", {}).get("v") == "PASS", f"an unreadable end clock keeps the PASS: {r.error}")
        check(any(n.startswith("end clock: ValueError") for n in rec["attempts"][0].get("notes", [])) if rec else False,
              f"the end clock's failure is a note: {rec and rec['attempts'][0].get('notes')}")
        check(FakeHar.stopped == 1 and len(sides) == 1, f"the HAR is still stopped once and its POST recorded ({FakeHar.stopped}, {len(sides)})")

        def exists(path, obj, overwrite=False):
            raise FileExistsError(f"{path} exists; records are immutable")
        old = os.path.join(tmp, "qa", "runs", "2026-10-22-g3-test", "R-20261022-W044-QA-910-01.json")
        os.makedirs(os.path.dirname(old))
        open(old, "w").close()  # an earlier attempt's record with the same --nn
        r, order, _, records, _ = target_run(tmp, read, "W044", write_record=exists)
        prog = [x for x in order if x.startswith("progress:finished")]
        check(r.rec and r.rec["verdict"]["v"] == "ERROR" and "FileExistsError" in (r.error or ""),
              f"a run whose record cannot be written is ERROR, not PASS ({r.rec and r.rec['verdict']['v']})")
        check(prog == ["progress:finished:ERROR"], f"its progress says finished ERROR, got {prog}")

        gated = [{"n": 1, "text": "Import the plan", "helper": "act.py {target} tap testid:btn-import --expect path:/x",
                  "gates": ["write_list"]},
                 {"n": 2, "text": "Save", "helper": "act.py {target} tap testid:x --expect path:/y", "gates": ["cloud_write"]}]
        r, order, envs, records, _ = target_run(tmp, gated, "TA5")
        v = records[-1]["verdict"] if records else {}
        check(v.get("v") == "BLOCKED" and (v.get("by") or {}).get("kind") == "handover", f"a write_list-gated step is handed back: {v}")
        cmd = (v.get("handback") or {}).get("command") or ""
        check("QA_CAMPAIGN=2026-10-22-g3-test" in cmd and "act.py TA5 tap testid:btn-import" in cmd and cmd.endswith("--approved"),
              f"the handback command: {cmd}")
        check("tap" not in order, "the gated step never reached act.py")

        r, order, envs, records, _ = target_run(tmp, gated[1:], "TA5")
        taps = [e for verb, e in envs if verb == "tap"]
        check(taps == [{"QA_CAMPAIGN": "2026-10-22-g3-test", "QA_CLOUD_WRITE": "1"}],
              f"a step's act.py call carries the campaign, and QA_CLOUD_WRITE when gated cloud_write: {taps}")

    calls = []
    with patched(run.subprocess, run=lambda argv, **kw: calls.append(kw.get("env")) or
                 type("P", (), {"stdout": '{"ok": true}', "stderr": "", "returncode": 0})()):
        old = os.environ.get("QA_CLOUD_WRITE")
        os.environ["QA_CLOUD_WRITE"] = "1"
        try:
            run.act("TA5", ["probe"], "R-1")
        finally:
            os.environ.pop("QA_CLOUD_WRITE")
            if old is not None:
                os.environ["QA_CLOUD_WRITE"] = old
    check(calls and "QA_CLOUD_WRITE" not in calls[0] and calls[0]["QA_RUNNER"] == "1", "a stale QA_CLOUD_WRITE never reaches act.py")

    with patched(run.subprocess, run=lambda argv, **kw: type("P", (), {"stdout": "", "returncode": 255})()), \
            patched(run, runner_event=lambda *a, **kw: {"t": "2026-10-22T00:00:00.000Z", "seq": 1}):
        try:
            c = run.web_clock("end", "R-1")
        except ValueError as e:
            c = {"raised": f"{type(e).__name__}: {e}"}
    check(c.get("offset_ms", 0) is None and "unreadable" in c.get("error", ""), f"an empty ssh clock read is an error entry, not a crash: {c}")


def test_main_refuses_recorded_run():
    with tempfile.TemporaryDirectory() as tmp:
        c = {"kind": "case", "id": "QA-911", "status": "active", "targets": "web", "needs": {"link": "none"},
             "steps": [{"n": 1, "text": "read", "read_js": "1", "gates": []}]}
        os.makedirs(os.path.join(tmp, "runs", "2026-10-22-x"))
        open(os.path.join(tmp, "runs", "2026-10-22-x", "R-20261022-W044-QA-911-01.json"), "w").close()
        started = []
        buf, out = io.StringIO(), sys.stdout
        stubs = dict(QA_DIR=tmp, load_case=lambda cid: (c, os.path.join(tmp, "QA-911.json")), engine=lambda: READY,
                     profiles=lambda: {}, prediction_for=lambda campaign, rid: ({"ref": {"file_sha256": "x"}}, os.devnull),
                     sha256_file=lambda p: "x")
        try:
            sys.stdout = buf
            with patched(run, **stubs), patched(run.TargetRun, start=lambda self: started.append(self.run_id),
                                                join=lambda self, timeout=None: None):
                rc = run.main(["QA-911", "--targets", "W044", "--campaign", "2026-10-22-x"])
        finally:
            sys.stdout = out
        check(rc == 2 and "already recorded" in buf.getvalue() and not started,
              f"a run whose record exists is refused before any thread starts (rc {rc}, started {started})")


def test_event_ids():
    """Two writers in different campaigns at the same moment get different event ids."""
    shim = type(os)("os_shim")
    shim.__dict__.update(os.__dict__)

    def makedirs(path, *a, **k):
        os.makedirs(path, *a, **k)
        if path.endswith("event"):
            time.sleep(0.3)  # both writers have scanned before either reserves, unless the scan is locked
    shim.makedirs = makedirs
    got = {}
    with tempfile.TemporaryDirectory() as tmp, patched(records, QA_DIR=tmp, os=shim):
        os.makedirs(os.path.join(tmp, "runs", "2026-10-22-a", "event"))
        open(os.path.join(tmp, "runs", "2026-10-22-a", "event", "E-20261022-004.json"), "w").close()
        ts = [threading.Thread(target=lambda c=c: got.__setitem__(c, records._reserve_event(c, "20261022")[0]))
              for c in ("2026-10-22-a", "2026-10-22-side-effects")]
        ts[0].start()
        time.sleep(0.1)
        ts[1].start()
        for t in ts:
            t.join()
    check(sorted(got.values()) == ["E-20261022-005", "E-20261022-006"], f"parallel writers never share an id: {got}")


def real_cases():
    """refuse_case over every real case (its newest version) with the real engine.json: TERR-001 and SELFTEST-001 run;
    a case without steps is refused as having nothing to run, and a case with steps is never refused for that reason
    (UPG-013 has had steps since its c1, 2026-10-28; the other UPG cases have none)."""
    eng = run.engine()
    for d in run.CASE_DIRS:
        ids = sorted({re.sub(r"(\.c\d+)?\.json$", "", f) for f in os.listdir(d) if f.endswith(".json")})
        for cid in ids:
            c, path = run.load_case(cid)
            r = run.refuse_case(c, eng)
            if cid in ("TERR-001", "SELFTEST-001"):
                check(r is None, f"{cid} must still run, refused: {r}")
            elif not c.get("steps"):
                check(r is not None and ("nothing to run" in r or c.get("status", "active") != "active"),
                      f"{cid} ({os.path.basename(path)}) has no steps and is refused: {r}")
            else:
                check(r is None or "nothing to run" not in r,
                      f"{cid} ({os.path.basename(path)}) has steps and is not refused for having none: {r}")


def main():
    test_refuse_case()
    test_har_side_effects()
    test_act_cloud_write_record()
    test_refuse_more()
    test_act_g3_controls()
    test_har_failures()
    test_target_runs()
    test_main_refuses_recorded_run()
    test_event_ids()
    real_cases()
    print(f"G3 checks: {N[0] - len(FAILS)}/{N[0]} pass")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
