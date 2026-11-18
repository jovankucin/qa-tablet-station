"""run.py's progress file (qa/app/DESIGN.md 6), without devices, ledger or records.

python3 -I qa/runner/tests/test_progress.py

TargetRun.run() goes through a stub case on a stub tablet: act.py, the clock, battery, markers, the account
check, the build pin, logs, locks, ledger events and record writes are all replaced in-process, and
run.PROGRESS points at a temporary folder. The fake act.py snapshots the progress file at every call, so the
checks see what the app would have seen while the run was on each step.
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import run  # noqa: E402

FAILS = []
CHECKS = 0
SERIAL = "QATEST0001"
KEYS = ["attempt", "case", "clock_offset_ms", "last_shot", "run", "started", "state", "step", "steps_total", "target",
        "updated", "verdict"]


def check(ok, what):
    global CHECKS
    CHECKS += 1
    if not ok:
        FAILS.append(what)
        print("FAIL " + what)


class FakeLock:
    busy = False

    def __init__(self, name, holder, case):
        self.name = name

    def __enter__(self):
        if FakeLock.busy:
            raise RuntimeError(f"{self.name} is locked by another run")
        return self

    def __exit__(self, *exc):
        pass


class Device:
    """act.py stand-in: answers every verb and keeps a copy of the progress file as it was at each call."""

    def __init__(self, crash_on_step=None):
        self.seen, self.crash_on_step, self.seq = [], crash_on_step, 0

    def __call__(self, target, args, run_id, timeout=90, env=None):
        self.seq += 1
        p = os.path.join(run.PROGRESS, f"{SERIAL}.json")
        snap = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None
        self.seen.append((args[0], args[1] if len(args) > 1 else None, snap))
        if snap and snap["step"] and snap["step"]["n"] == self.crash_on_step:
            raise OSError("stub device fell over")
        out = {"ok": True, "ledger": {"day": "2026-10-22", "seq": self.seq},
               "identity": {"port": 9341, "ua_model": "QA-Stub", "dpr": 1.5, "origin": "web.staging.company.example"}}
        if args[0] == "shot":
            out["shot"] = {"path": args[1], "sha256": "0" * 64}
        elif args[0] == "read" and args[1] == run.BUILD_JS:
            out["value"] = {"git_hash": "a" * 40, "version": "1.74.0", "env": "staging"}
        elif args[0] == "read" and args[1] == run.FLAGS_JS:
            out["value"] = ["OfflineSync"]
        elif args[0] == "read":
            out["value"] = 42
        return out


def make_run(tmp, crash_on_step=None):
    case = {"kind": "case", "id": "QA-900", "targets": "tablet", "needs": {"link": "none"}, "steps": [
        {"n": 1, "text": "Top bar: Sites", "helper": "act.py {target} tap testid:btn-sites --expect path:/sites", "gates": []},
        {"n": 2, "text": "Screenshot", "helper": "act.py {target} shot {shots}/{run}_x.png", "gates": []},
        {"n": 3, "text": "Read the answer", "read_js": "6 * 7", "save_as": "answer", "gates": [],
         "assert": {"kind": "scripted", "expr": "v['answer'] == 42"}},
    ]}
    case_path = os.path.join(tmp, "QA-900.json")
    prof_path = os.path.join(tmp, f"{SERIAL}.v1.json")
    for p, obj in ((case_path, case), (prof_path, {"serial": SERIAL})):
        with open(p, "w") as f:
            json.dump(obj, f)
    prof = {"serial": SERIAL, "tag": "QA9", "id": f"{SERIAL}.v1", "_path": prof_path, "user_agent_model": "QA-Stub", "dpr": 1.5}
    r = run.TargetRun(case, case_path, "QA9", "2026-10-22-progress-test", "01", prof, {"env": "staging", "web_origin": "web.staging.company.example"})
    r.dir = os.path.join(tmp, "tests", "QA9")
    r.pred_entry = {"t": "2026-10-22T00:00:00.000Z", "seq": 1, "ref": {"file_sha256": "f" * 64}}
    r.pred_path = os.path.join(tmp, "PR-none.json")
    r.build_record = lambda b, serial: {"env": {"v": "staging"}, "hash7": {"v": "aaaaaaa"}}
    r.tablet_logs = lambda serial, clock, ended: []
    dev = Device(crash_on_step)
    return r, dev


def stub_module(records):
    run.Lock = FakeLock
    run.tablet_clock = lambda serial, at, rid: {"at": at, "offset_ms": -37, "target_raw": "0"}
    run.battery = lambda serial, at, rid: {"at": at, "level": 80}
    run.marker = lambda serial, text, rid: {"text": text}
    run.check_account = lambda tag, rid, eng: (True, "logged in as shared-stag", {"day": "2026-10-22", "seq": 1})
    run.engine_account = lambda eng: {"label": "shared-stag"}
    run.runner_event = lambda *a, **kw: {"t": "2026-10-22T00:00:00.000Z", "seq": 1}
    run.write_record = lambda path, obj, overwrite=False: records.append(obj)
    run.hash_record = lambda path, session="-", note=None: None


def progress_file():
    p = os.path.join(run.PROGRESS, f"{SERIAL}.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


def main():
    records = []
    stub_module(records)
    with tempfile.TemporaryDirectory() as tmp:
        run.PROGRESS = os.path.join(tmp, "state", "progress")

        # a clean run: running on each step, then finished with the verdict
        r, dev = make_run(tmp)
        run.act = dev
        r.run()
        check(r.error is None, f"run crashed: {r.error}")
        check(records and records[-1]["verdict"]["v"] == "PASS", "the stub run did not PASS")
        by_step = {snap["step"]["n"]: snap for verb, _, snap in dev.seen if snap and snap["step"]}
        check(sorted(by_step) == [1, 2, 3], f"progress named steps {sorted(by_step)} during the step actions")
        for n, snap in by_step.items():
            check(sorted(snap) == KEYS, f"step {n} progress keys {sorted(snap)}")
            check(snap["state"] == "running" and snap["verdict"] is None, f"step {n}: state {snap['state']} verdict {snap['verdict']}")
            check(snap["step"]["text"] == r.case["steps"][n - 1]["text"] and snap["step"]["started"], f"step {n} text/started {snap['step']}")
            check(snap["run"] == "R-20261022-QA9-QA-900-01" and snap["case"] == "QA-900" and snap["target"] == "QA9",
                  f"step {n} identity {snap['run']} {snap['case']} {snap['target']}")
            check(snap["attempt"] == 1 and snap["steps_total"] == 3 and snap["clock_offset_ms"] == -37, f"step {n} counters {snap}")
        shot2 = run.rel(os.path.join(r.dir, "shots", f"{r.run_id}_x.png"))
        check(by_step.get(2, {}).get("last_shot", "missing") is None, "last_shot set before the first shot")
        check(by_step.get(3, {}).get("last_shot") == shot2, f"step 3 last_shot, expected {shot2}")
        pre = [snap for verb, _, snap in dev.seen if verb == "probe"]
        check(pre and pre[0] and pre[0]["state"] == "running" and pre[0]["step"] is None and pre[0]["clock_offset_ms"] == -37,
              f"progress at the probe: {pre}")
        end_shot = [snap for verb, arg, snap in dev.seen if verb == "shot" and arg.endswith("_end.png")]
        check(end_shot and end_shot[0] and end_shot[0]["step"] is None, f"step not cleared before the end shot: {end_shot}")
        fin = progress_file()
        check(fin and fin["state"] == "finished" and fin["verdict"] == "PASS", f"final progress {fin}")
        check(fin and fin["last_shot"] == run.rel(os.path.join(r.dir, "shots", f"{r.run_id}_end.png")), f"final last_shot {fin and fin['last_shot']}")
        check(fin and fin["started"] <= fin["updated"], "updated before started")
        check(not [f for f in os.listdir(run.PROGRESS) if f.endswith(".tmp")], "a .tmp file was left behind")

        # a crash mid-run: the runner's ERROR record, and the progress file says finished ERROR
        records.clear()
        r, dev = make_run(tmp, crash_on_step=2)
        run.act = dev
        r.run()
        check(r.error and "stub device fell over" in r.error, f"crash not recorded: {r.error}")
        fin = progress_file()
        check(fin and fin["state"] == "finished" and fin["verdict"] == "ERROR", f"progress after a crash {fin}")
        check(records and records[-1]["verdict"]["v"] == "ERROR", "crash record is not ERROR")

        # the lock is held elsewhere: the other run's progress file is left as it is
        with open(os.path.join(run.PROGRESS, f"{SERIAL}.json"), "w") as f:
            f.write('{"run": "R-other", "state": "running"}\n')
        FakeLock.busy = True
        r, dev = make_run(tmp)
        run.act = dev
        r.run()
        FakeLock.busy = False
        check(progress_file() == {"run": "R-other", "state": "running"}, f"a run without the lock rewrote progress: {progress_file()}")
        check(dev.seen == [], "a run without the lock reached the device")

        # progress cannot be written: the run goes on exactly as before
        blocker = os.path.join(tmp, "blocker")
        open(blocker, "w").close()
        run.PROGRESS = os.path.join(blocker, "progress")
        check(run.write_progress("x", {"a": 1}) is False, "write_progress did not report the failure")
        records.clear()
        r, dev = make_run(tmp)
        run.act = dev
        r.run()
        check(r.error is None, f"an unwritable progress dir broke the run: {r.error}")
        check(records and records[-1]["verdict"]["v"] == "PASS", "an unwritable progress dir changed the verdict")
        check(not os.path.exists(run.PROGRESS), "progress written under a file")
    print(f"{CHECKS} progress checks, {len(FAILS)} failures")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
