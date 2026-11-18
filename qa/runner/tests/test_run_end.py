"""run.py after the verdict (audit finding 12, 2026-10-28) and W044's ready at the start of an attempt (finding 3),
without devices, ledger or records: the test_progress stubs, and a HAR recorder that records nothing.

python3 -I qa/runner/tests/test_run_end.py

- act(): an act.py call that times out is a failed call (code 3), not an exception that ends the run;
- a decided FAIL, then a clean-up step and end-of-run evidence that time out or raise: the record keeps the FAIL, and
  each missing piece of evidence is a note on the attempt;
- main(): the run ids are printed before the differential, and a differential that cannot be written is an error
  after them, not a traceback in their place;
- W044: act.py ready runs first, like on a tablet; a tab that stays hidden (Fail 2) blocks the run, it is not a FAIL.
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import run  # noqa: E402
import test_progress as tp  # noqa: E402

REAL_ACT = run.act
FAILS = []
N = [0]
ENG = {"env": "staging", "web_origin": "web.staging.company.example"}
HIDDEN = "W044: the WingtraCloud tab is behind another tab; bring it to the front (close the ClickUp tab)"


def check(cond, what):
    N[0] += 1
    if not cond:
        FAILS.append(what)
        print("FAIL", what)


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


def timed_out(verb):
    return {"ok": False, "code": 3, "error": f"act.py {verb} timed out after 90 s", "exit": None}


class Dev(tp.Device):
    """test_progress's act.py stand-in, with chosen answers per verb (a dict, or an exception to raise)."""

    def __init__(self, answers=None):
        super().__init__()
        self.answers = answers or {}

    def __call__(self, target, args, run_id, timeout=90, env=None):
        key = "end shot" if args[0] == "shot" and args[1].endswith("_end.png") else args[0]
        if key in self.answers:
            self.seen.append((args[0], args[1] if len(args) > 1 else None, None))
            a = self.answers[key]
            if isinstance(a, Exception):
                raise a
            return dict(a, ledger={"day": "2026-10-28", "seq": 99})
        return super().__call__(target, args, run_id, timeout, env)


def raises_at_end(e):
    """A clock, battery or marker stand-in (serial, at or text, run id) that answers at the start and raises e at the end."""
    def f(serial, at, rid):
        if at == "end" or at.endswith(" a1 end"):
            raise e
        return {"at": at, "offset_ms": -37, "target_raw": "0", "text": at}
    return f


def logs_hang(serial, clock, ended):
    raise subprocess.TimeoutExpired(["adb", "logcat"], 120)


def test_act_timeout():
    def hang(argv, **kw):
        raise subprocess.TimeoutExpired(argv, kw.get("timeout"))
    with patched(run.subprocess, run=hang):
        out = REAL_ACT("TA5", ["ready"], "R-1", timeout=7)
    check(out == {"ok": False, "code": 3, "error": "act.py ready timed out after 7 s", "exit": None},
          f"a hung act.py is a failed call with the act() shape, not an exception: {out}")


def test_fail_survives_the_end():
    records = []
    tp.stub_module(records)
    with tempfile.TemporaryDirectory() as tmp:
        run.PROGRESS = os.path.join(tmp, "progress")
        r, _ = tp.make_run(tmp)
        r.case["steps"][2]["assert"]["expr"] = "v['answer'] == 41"  # the deciding step FAILs
        r.case["steps"].append({"n": 4, "text": "Back to Sites", "helper": "act.py {target} key back --expect path:/sites",
                                "gates": [], "always": True})
        dev = Dev({"key": timed_out("key"), "end shot": timed_out("shot")})
        hung = subprocess.TimeoutExpired(["adb"], 10)
        r.tablet_logs = logs_hang
        with patched(run, act=dev, marker=raises_at_end(hung), battery=raises_at_end(hung), tablet_clock=raises_at_end(hung)):
            r.run()
        rec = records[-1] if records else {}
        check(r.error is None, f"no crash after the verdict: {r.error}")
        check(rec.get("verdict", {}).get("v") == "FAIL", f"the FAIL is kept: {rec.get('verdict')}")
        att = (rec.get("attempts") or [{}])[0]
        notes = att.get("notes") or []
        for what in ("end shot: act.py shot timed out after 90 s", "end marker: TimeoutExpired", "end battery: TimeoutExpired",
                     "end clock: TimeoutExpired", "tablet logs: TimeoutExpired"):
            check(any(n.startswith(what) for n in notes), f"note {what!r} in {notes}")
        check([c.get("error") for c in att.get("cleanup") or []] == ["act.py key timed out after 90 s"],
              f"the clean-up step that timed out is recorded, not raised: {att.get('cleanup')}")
        check("end" not in (att.get("markers") or {}) and len(rec.get("battery") or []) == 1 and len(rec.get("clock") or []) == 1,
              "missing end readings are left out, not invented")
        fin = tp.progress_file()
        check(fin and fin["state"] == "finished" and fin["verdict"] == "FAIL", f"progress after the end failures: {fin}")


def test_pass_survives_a_raising_end_shot():
    records = []
    tp.stub_module(records)
    with tempfile.TemporaryDirectory() as tmp:
        run.PROGRESS = os.path.join(tmp, "progress")
        r, _ = tp.make_run(tmp)
        with patched(run, act=Dev({"end shot": OSError("stub device fell over")})):
            r.run()
        rec = records[-1] if records else {}
        check(r.error is None and rec.get("verdict", {}).get("v") == "PASS", f"an end shot that raises keeps the PASS: {r.error}")
        check((rec.get("attempts") or [{}])[0].get("notes") == ["end shot: OSError: stub device fell over"],
              f"the end shot is a note: {(rec.get('attempts') or [{}])[0].get('notes')}")
        r, _ = tp.make_run(tmp)
        with patched(run, act=Dev()):
            r.run()
        check("notes" not in records[-1]["attempts"][0], "a run with all its evidence has no notes")


def run_main(diff):
    """run.main on a 'both' case with two stub runs that PASS; returns (rc, stdout, stdout when differential was called)."""
    with tempfile.TemporaryDirectory() as tmp:
        c = {"kind": "case", "id": "QA-912", "status": "active", "targets": "both", "needs": {"link": "none"},
             "steps": [{"n": 1, "text": "read", "read_js": "1", "gates": []}]}
        prof = {"kind": "device", "serial": tp.SERIAL, "tag": "QA9", "id": f"{tp.SERIAL}.v1"}
        buf, out, at_diff = io.StringIO(), sys.stdout, []

        def differential(case, runs, campaign, nn):
            at_diff.append(buf.getvalue())
            return diff(runs)

        def finish(self):
            self.rec = {"verdict": {"v": "PASS"}, "judgement_pending": []}
        stubs = dict(QA_DIR=tmp, load_case=lambda cid: (c, os.path.join(tmp, "QA-912.json")), engine=lambda: ENG,
                     refuse_case=lambda case, eng: None, profiles=lambda: {"QA9": prof},
                     prediction_for=lambda campaign, rid: ({"ref": {"file_sha256": "x"}}, os.devnull),
                     sha256_file=lambda p: "x", differential=differential)
        try:
            sys.stdout = buf
            with patched(run, **stubs), patched(run.TargetRun, start=finish, join=lambda self, timeout=None: None):
                rc = run.main(["QA-912", "--targets", "QA9,W044", "--campaign", "2026-10-28-x"])
        finally:
            sys.stdout = out
        return rc, buf.getvalue(), (at_diff or [None])[0]


def objects(text):
    """The JSON objects printed one after the other."""
    dec, i, out = json.JSONDecoder(), 0, []
    while i < len(text):
        if text[i].isspace():
            i += 1
            continue
        o, i = dec.raw_decode(text, i)
        out.append(o)
    return out


def test_main_prints_runs_before_the_differential():
    ids = ["R-20261028-QA9-QA-912-01", "R-20261028-W044-QA-912-01"]

    def exists(runs):
        raise FileExistsError("D-QA-912-20261028-01.json exists; records are immutable")
    rc, text, at_diff = run_main(exists)
    check(at_diff is not None and all(i in at_diff for i in ids), f"run ids printed before the differential: {at_diff!r}")
    objs = objects(text)
    check(rc == 0 and len(objs) == 2, f"rc {rc}, {len(objs)} objects printed")
    check(objs and [objs[0]["runs"][t]["id"] for t in ("QA9", "W044")] == ids, f"the per-target summary: {objs[:1]}")
    d = (objs[-1] if objs else {}).get("differential") or {}
    check(d.get("id") == "D-QA-912-20261028-01" and "FileExistsError" in (d.get("error") or ""),
          f"a differential that cannot be written is an error after the run ids: {d}")

    rc, text, _ = run_main(lambda runs: {"id": runs[0].differential, "result": "consistent", "values": {}})
    objs = objects(text)
    check(rc == 0 and objs[-1] == {"differential": {"id": "D-QA-912-20261028-01", "result": "consistent", "values": {}}},
          f"a written differential follows the summary: {objs}")


class FakeHar:
    def __init__(self, har_path, console_path):
        pass

    def start(self):
        pass

    def stop(self):
        pass

    def files(self):
        return []


def web_run(answers):
    """One W044 TargetRun on the stubs; returns (run, device, record)."""
    records = []
    tp.stub_module(records)
    with tempfile.TemporaryDirectory() as tmp:
        run.PROGRESS = os.path.join(tmp, "progress")
        c = {"kind": "case", "id": "QA-913", "targets": "web", "needs": {"link": "none"},
             "steps": [{"n": 1, "text": "Read the answer", "read_js": "6 * 7", "save_as": "answer", "gates": []}]}
        cp = os.path.join(tmp, "QA-913.json")
        json.dump(c, open(cp, "w"))
        r = run.TargetRun(c, cp, "W044", "2026-10-28-ready-test", "01", None, ENG)
        r.dir = os.path.join(tmp, "tests", "W044")
        r.pred_entry = {"t": "2026-10-28T00:00:00.000Z", "seq": 1, "ref": {"file_sha256": "f" * 64}}
        r.pred_path = os.path.join(tmp, "PR-none.json")
        r.build_record = lambda b, serial: {"env": {"v": "staging"}, "hash7": {"v": "aaaaaaa"}}
        dev = Dev(answers)
        real_har = sys.modules.get("har")
        sys.modules["har"] = type(sys)("har")
        sys.modules["har"].HarRecorder = FakeHar
        try:
            with patched(run, act=dev, web_clock=lambda at, rid: {"at": at, "offset_ms": 3, "target_raw": "1"},
                         har_side=lambda *a: {"observed_by": "har", "ids": []}):
                r.run()
        finally:
            if real_har is None:
                sys.modules.pop("har", None)
            else:
                sys.modules["har"] = real_har
        return r, dev, (records[-1] if records else {})


def test_web_ready():
    r, dev, rec = web_run({})
    verbs = [v for v, _, _ in dev.seen]
    check(verbs[:2] == ["ready", "probe"], f"W044: ready before the probe: {verbs[:3]}")
    check(rec.get("verdict", {}).get("v") == "PASS" and rec["attempts"][0]["identity"][0]["verb"] == "ready",
          f"W044 ready ok: the run goes on ({rec.get('verdict')})")

    r, dev, rec = web_run({"ready": {"ok": False, "code": 2, "error": HIDDEN}})
    v = rec.get("verdict") or {}
    check(v.get("v") == "BLOCKED" and (v.get("by") or {}).get("kind") == "precondition" and
          "behind another tab" in (v.get("by") or {}).get("ref", ""), f"a hidden tab blocks the run, it is not a FAIL: {v}")
    check(not any(a == "6 * 7" for _, a, _ in dev.seen), "a blocked run reaches no step")

    r, dev, rec = web_run({"ready": timed_out("ready")})
    v = rec.get("verdict") or {}
    check(v.get("v") == "ERROR" and (v.get("harness") or {}).get("component") == "act.py ready",
          f"a ready that times out is a harness ERROR: {v}")


def main():
    test_act_timeout()
    test_fail_survives_the_end()
    test_pass_survives_a_raising_end_shot()
    test_main_prints_runs_before_the_differential()
    test_web_ready()
    print(f"run.py end of run: {N[0]} checks, {len(FAILS)} failures")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
