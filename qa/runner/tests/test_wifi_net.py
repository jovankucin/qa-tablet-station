"""run.py's Wi-Fi safety net (2026-11-03): a tablet run in which a step called act.py wifi off calls wifi on at its end,
after the clean-up steps and before the lock is released, whatever the verdict. Without devices, ledger or records:
the test_progress stubs.

python3 -I qa/runner/tests/test_wifi_net.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import run  # noqa: E402
import test_progress as tp  # noqa: E402

FAILS = []
N = [0]
ORDER = []
NOTE = "Wi-Fi could not be turned back on: turn it on by hand on QA9"


def check(cond, what):
    N[0] += 1
    if not cond:
        FAILS.append(what)
        print("FAIL", what)


class OrderLock(tp.FakeLock):
    def __exit__(self, *exc):
        ORDER.append("unlock")


class Dev(tp.Device):
    """test_progress's act.py stand-in; answers chosen per (verb, selector) or verb (a dict, or an exception to raise)."""

    def __init__(self, answers=None):
        super().__init__()
        self.answers = answers or {}

    def __call__(self, target, args, run_id, timeout=90, env=None):
        key = " ".join(args[:2])
        ORDER.append("end shot" if args[0] == "shot" and args[1].endswith("_end.png") else key)
        a = self.answers.get(key, self.answers.get(args[0]))
        if a is not None:
            self.seen.append((args[0], args[1] if len(args) > 1 else None, None))
            if isinstance(a, Exception):
                raise a
            return dict(a, ledger={"day": "2026-11-03", "seq": 77})
        return super().__call__(target, args, run_id, timeout, env)


WIFI_OFF = {"n": 2, "text": "Wi-Fi off", "helper": "act.py {target} wifi off --until-offline", "gates": []}
BACK = {"n": 9, "text": "Back to Sites", "helper": "act.py {target} key back --expect path:/sites", "gates": [], "always": True}


def go(steps, answers=None, web=False):
    """One stub tablet run of a case with these steps; returns (record, ORDER, run)."""
    records = []
    tp.stub_module(records)
    run.Lock = OrderLock
    ORDER.clear()
    with tempfile.TemporaryDirectory() as tmp:
        run.PROGRESS = os.path.join(tmp, "progress")
        r, _ = tp.make_run(tmp)
        r.case["steps"] = [dict(s) for s in steps]
        if web:
            r.is_web = True
        run.act = Dev(answers)
        r.run()
    return (records[-1] if records else {}), list(ORDER), r


def steps(*extra):
    tap = {"n": 1, "text": "Top bar: Sites", "helper": "act.py {target} tap testid:btn-sites --expect path:/sites", "gates": []}
    return [tap, *extra]


def read(n, expr="v['answer'] == 42"):
    return {"n": n, "text": "Read the answer", "read_js": "6 * 7", "save_as": "answer", "gates": [],
            "assert": {"kind": "scripted", "expr": expr}}


def wifi_ons(order):
    return [i for i, x in enumerate(order) if x == "wifi on"]


def test_after_a_fail():
    rec, order, r = go(steps(WIFI_OFF, read(3, "v['answer'] == 41"), BACK))
    check(rec.get("verdict", {}).get("v") == "FAIL", f"the FAIL stays: {rec.get('verdict')}")
    on = wifi_ons(order)
    check(len(on) == 1, f"wifi on once after a FAIL: {order}")
    check(on and order.index("key back") < on[0] < order.index("unlock"),
          f"after the clean-up step, before the unlock: {order}")
    check(order.index("end shot") < on[0], f"the end shot shows where the run stopped, before the net: {order}")
    idn = [x for x in rec["attempts"][0]["identity"] if x.get("verb") == "wifi"]
    check(idn and idn[-1]["actions"] == ["turned Wi-Fi back on (a step switched it off)"] and idn[-1]["ledger"],
          f"the net's call is in the record with its ledger ref: {idn}")
    check("notes" not in rec["attempts"][0], "a net that worked leaves no note")


def test_after_an_early_stop():
    rec, order, _ = go(steps(WIFI_OFF, {"n": 3, "text": "Open site", "helper": "act.py {target} tap text:Site --expect path:/x",
                                        "gates": []}, read(4), BACK),
                       {"tap text:Site": {"ok": False, "code": 3, "error": "not found: text:Site"}})
    check(rec.get("verdict", {}).get("v") == "ERROR", f"the stop is an ERROR: {rec.get('verdict')}")
    check(len(wifi_ons(order)) == 1 and "6 * 7" not in order, f"stopped early: step 4 not run, wifi on called: {order}")
    # the wifi off call itself fails (the readback ran out): Wi-Fi may be off all the same
    rec, order, _ = go(steps(WIFI_OFF, read(3)), {"wifi off": {"ok": False, "code": 3, "error": "readback: Wi-Fi on QA9 reads on"}})
    check(rec.get("verdict", {}).get("v") == "ERROR" and len(wifi_ons(order)) == 1,
          f"a wifi off that fails still gets wifi on: {rec.get('verdict')} {order}")
    # BLOCKED by a handover after Wi-Fi went off
    rec, order, _ = go(steps(WIFI_OFF, {"n": 3, "text": "Delete", "helper": "act.py {target} tap text:Delete --expect path:/x",
                                        "gates": ["write_list"]}))
    check(rec.get("verdict", {}).get("v") == "BLOCKED" and len(wifi_ons(order)) == 1,
          f"BLOCKED after wifi off: wifi on: {rec.get('verdict', {}).get('v')} {order}")


def test_failed_net_is_a_note():
    rec, order, _ = go(steps(WIFI_OFF, {"n": 3, "text": "Wait", "helper": "act.py {target} wait --expect js:true", "gates": []},
                             read(4)),
                       {"wifi on": {"ok": False, "code": 3, "error": "readback: Wi-Fi on QA9 reads off, not on"}})
    notes = rec.get("attempts", [{}])[0].get("notes") or []
    check(rec.get("verdict", {}).get("v") == "PASS", f"a net that fails never changes the verdict: {rec.get('verdict')}")
    check(len(notes) == 1 and notes[0].startswith(NOTE) and "reads off, not on" in notes[0], f"the note: {notes}")
    rec, order, _ = go(steps(WIFI_OFF, read(3)), {"wifi on": OSError("stub device fell over")})
    notes = rec.get("attempts", [{}])[0].get("notes") or []
    check(rec.get("verdict", {}).get("v") == "PASS" and notes and notes[0].startswith(NOTE),
          f"a net call that raises is a note too: {rec.get('verdict')} {notes}")


def test_crash_after_wifi_off():
    records = []
    tp.stub_module(records)
    run.Lock = OrderLock
    ORDER.clear()
    with tempfile.TemporaryDirectory() as tmp:
        run.PROGRESS = os.path.join(tmp, "progress")
        r, _ = tp.make_run(tmp)
        r.case["steps"] = steps(WIFI_OFF, read(3))
        run.act = Dev({"read 6 * 7": OSError("stub device fell over"),
                       "wifi on": {"ok": False, "code": 3, "error": "QA9 is not reachable over USB"}})
        r.run()
    rec = records[-1] if records else {}
    check(r.error and "stub device fell over" in r.error and rec.get("verdict", {}).get("v") == "ERROR", f"crash: {r.error}")
    on = wifi_ons(ORDER)
    check(len(on) == 1 and on[0] < ORDER.index("unlock"), f"a crash still gets wifi on, before the unlock: {ORDER}")
    check((rec.get("notes") or [""])[0].startswith(NOTE), f"the crash record carries the note: {rec.get('notes')}")


def test_not_without_wifi_off():
    rec, order, _ = go(steps(read(2), BACK))
    check(rec.get("verdict", {}).get("v") == "PASS" and not [x for x in order if x.startswith("wifi")],
          f"no wifi off, no wifi call: {order}")
    rec, order, _ = go(steps(read(2, "v['answer'] == 41")))
    check(rec.get("verdict", {}).get("v") == "FAIL" and not [x for x in order if x.startswith("wifi")],
          f"a FAIL without wifi off: no wifi call: {order}")
    on_only = {"n": 2, "text": "Wi-Fi on", "helper": "act.py {target} wifi on", "gates": []}
    rec, order, _ = go(steps(on_only, read(3)))
    check([x for x in order if x.startswith("wifi")] == ["wifi on"], f"a case that only turns Wi-Fi on: no net: {order}")
    skipped = dict(WIFI_OFF, when_js="false")
    rec, order, _ = go(steps(skipped, read(3)), {"read false": {"ok": True, "value": False}})
    check(not [x for x in order if x.startswith("wifi")], f"a wifi off step skipped by when_js: no net: {order}")
    rec, order, _ = go([{"n": 1, "text": "Wi-Fi off", "helper": "act.py {target} wifi off", "gates": []}],
                       {"ready": {"ok": False, "code": 2, "error": "the lock screen has a PIN"}})
    check(rec.get("verdict", {}).get("v") == "BLOCKED" and not [x for x in order if x.startswith("wifi")],
          f"a run stopped at ready runs no step and no net: {order}")
    r = run.TargetRun({"id": "QA-1", "steps": []}, os.devnull, "W044", "2026-11-03-x", "01", None, {})
    r.wifi_off = True
    seen = []
    saved = run.act
    run.act = lambda *a, **kw: seen.append(a) or {"ok": True}
    try:
        check(r.restore_wifi() is None and seen == [], "W044 never gets the net (act.py refuses wifi there)")
    finally:
        run.act = saved


def main():
    test_after_a_fail()
    test_after_an_early_stop()
    test_failed_net_is_a_note()
    test_crash_after_wifi_off()
    test_not_without_wifi_off()
    print(f"run.py Wi-Fi safety net: {N[0]} checks, {len(FAILS)} failures")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
