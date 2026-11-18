"""act.py refuses every write-list action under QA_RUNNER=1 before any device call (done-check 2).

python3 -I qa/runner/tests/test_act_refusal.py

Runs act.main in-process with every way out of the process replaced by a recorder that raises:
subprocess.run, subprocess.Popen (adb, ssh), socket.create_connection (CDP, tunnel) and
urllib.request.urlopen (DevTools lists). The ledger append is captured, not written.
A write-list call passes when it exits 2 and the recorder saw nothing. A control call (a plain
Settings tap) must reach the recorder, which shows the recorder would have caught a device call.
The Enter key is a write-list key everywhere: outside the runner it is refused without --approved
and reaches the recorder with it (the gate then asks).
"""
import os
import socket
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import act  # noqa: E402

CALLS = []


class DeviceCall(Exception):
    pass


def recorder(name):
    def f(*args, **kw):
        CALLS.append((name, repr(args[0])[:80] if args else ""))
        raise DeviceCall(name)
    return f


def run(argv, runner=True):
    CALLS.clear()
    if runner:
        os.environ["QA_RUNNER"] = "1"
    else:
        os.environ.pop("QA_RUNNER", None)
    try:
        rc = act.main(argv)
    except DeviceCall:
        rc = "device"
    return rc, list(CALLS)


def main():
    subprocess.run = recorder("subprocess.run")
    subprocess.Popen = recorder("subprocess.Popen")
    socket.create_connection = recorder("socket.create_connection")
    urllib.request.urlopen = recorder("urlopen")
    act.ledger = lambda *a, **k: None
    devnull = open(os.devnull, "w")
    real_stdout = sys.stdout

    cases = []
    for target in ("TA5", "ET65", "W044"):
        for label in sorted(act.WRITE_LIST_TEXT):
            title = label.title()
            cases.append(([target, "tap", f"text:{title}", "--expect", "path:/x"], "text"))
            cases.append(([target, "tap", f"aria:{title}", "--expect", "path:/x", "--approved"], "aria + --approved"))
            cases.append(([target, "tap", f"text:  {label.upper()} ", "--expect", "path:/x"], "case and spacing"))
        for tid in sorted(act.WRITE_LIST_TESTID):
            cases.append(([target, "tap", f"testid:{tid}", "--expect", "path:/x", "--approved"], "testid"))
            cases.append(([target, "tap", f"testid:btn-wrapper-{tid}", "--expect", "path:/x"], "testid wrapper"))
        for tid in sorted(act.WRITE_LIST_CONTEXT_TESTID):
            cases.append(([target, "tap", f"testid:btn-wrapper-{tid}", "--expect", "path:/x", "--approved"], "context testid"))
        cases.append(([target, "key", "enter", "--expect", "path:/x", "--approved"], "key enter"))
        cases.append(([target, "key", "enter", "--expect", "path:/x"], "key enter, no --approved"))
        for label in sorted(act.WRITE_LIST_CONTEXT):
            cases.append(([target, "tap", f"text:{label.title()}", "--expect", "path:/x", "--approved"], "context label"))
        cases.append(([target, "tap", "css:button.anything", "--expect", "path:/x"], "css under runner"))
    for target in ("TA5", "ET65"):
        for label in ("Install", "Install anyway", "Update now"):
            cases.append(([target, "sys-tap", label, "--expect", "focus:app", "--approved"], "sys-tap"))

    failures = 0
    for argv, why in cases:
        sys.stdout = devnull
        rc, calls = run(argv)
        sys.stdout = real_stdout
        if rc != 2 or calls:
            failures += 1
            print(f"FAIL {why}: {argv[:3]} -> rc {rc}, device calls {calls}")

    # review 2026-11-03: the same calls as run.py sends a standing step (QA_STANDING_STEP, QA_CLOUD_WRITE=1). Only a tap
    # by text: on Next or Continue (piece 5) and on Save (the own-site flight save) gets past the request check, on to
    # the element and page checks (test_standing.py); every other write-list call is still refused before any device
    # call
    os.environ.update({"QA_STANDING_STEP": "5", "QA_CLOUD_WRITE": "1"})
    standing, passed = 0, []
    try:
        for argv, why in cases:
            sys.stdout = devnull
            rc, calls = run(argv)
            sys.stdout = real_stdout
            standing += 1
            if rc != 2 or calls:
                passed.append(argv)
    finally:
        for k in ("QA_STANDING_STEP", "QA_CLOUD_WRITE"):
            os.environ.pop(k, None)
    # the flight-save labels (Save, and since the second review of 2026-11-03 Save & exit and Save as draft, now on the
    # write list too: 2 text: calls each per target), Next and Continue (1 each per target)
    may_pass = [a for a, _ in cases if a[1] == "tap" and a[2].startswith("text:")
                and act.norm_label(a[2][5:]) in {"next", "continue"} | act.FLIGHT_SAVE_TEXT]
    if passed != may_pass or len(may_pass) != 24:
        failures += 1
        print(f"FAIL standing step: past the request check {[a[:3] for a in passed]}, "
              f"expected {[a[:3] for a in may_pass]}")

    # outside the runner, a named write-list control or the Enter key without --approved is refused
    # before any device call too
    outside = 0
    for target in ("TA5", "ET65", "W044"):
        for argv in ([target, "tap", "text:Update now", "--expect", "path:/x"],
                     [target, "key", "enter", "--expect", "path:/x"],
                     [target, "key", " Enter ", "--expect", "text:Report sent"]):
            sys.stdout = devnull
            rc, calls = run(argv, runner=False)
            sys.stdout = real_stdout
            outside += 1
            if rc != 2 or calls:
                failures += 1
                print(f"FAIL outside runner without --approved: {argv[:3]} -> rc {rc}, device calls {calls}")

    # controls: allowed actions must reach the recorder (exit 3 from the caught OSError, or the recorder itself)
    controls = 0
    for argv, runner in ((["TA5", "tap", "text:Settings", "--expect", "path:/settings"], True),
                         (["W044", "tap", "text:Sites", "--expect", "path:/sites"], True),
                         (["ET65", "tap", "text:Update now", "--expect", "path:/x", "--approved"], False),
                         (["TA5", "key", "enter", "--expect", "path:/x", "--approved"], False),
                         (["W044", "key", "enter", "--expect", "path:/x", "--approved"], False),
                         (["TA5", "key", "back", "--expect", "path:/x"], True)):
        sys.stdout = devnull
        rc, calls = run(argv, runner)
        sys.stdout = real_stdout
        controls += 1
        if not calls:
            failures += 1
            print(f"FAIL control {argv[:3]} made no device call (the recorder is not catching calls)")

    # the second check, on the element act.py found (reads only so far): labels and test ids the selector did not name
    elements = [
        ({"label": "Update now", "testids": ["btn-dialog-action", "btn-wrapper-btn-dialog-action"]}, "", True),
        ({"label": "", "testids": ["btn-wrapper-ota-update-now"]}, "", True),
        ({"label": "Next", "inDialog": True, "testids": ["none"]}, "Report Title Description", True),
        ({"label": "Next", "inDialog": True, "testids": ["none"]}, "Create site Name", False),
        ({"label": "Continue", "inDialog": True, "testids": ["btn-wrapper-ota-wifi-off-continue"]}, "Turn off Wi-Fi", True),
        ({"label": "Continue", "inDialog": False, "testids": []}, "", False),
        ({"label": "Attach more files in the next step", "inDialog": True}, "Report", False),
        ({"label": "Settings", "testids": ["btn-settings"]}, "", False),
    ]
    for el, title, want in elements:
        got = bool(act.is_write_list(el, "css:x", title))
        if got != want:
            failures += 1
            print(f"FAIL element check {el.get('label')!r} in {title!r}: write-list {got}, expected {want}")

    print(f"{len(cases)} write-list calls under QA_RUNNER=1, {standing} again as a standing step, "
          f"{outside} outside it, "
          f"{controls} controls, {len(elements)} element checks; "
          f"{failures} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
