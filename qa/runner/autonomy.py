"""Hands-off check: can the engine run a case from Start to its verdict with nobody touching a device?

python3 -I qa/runner/autonomy.py <CASE> [<CASE> ...] | --all

Engine code, so the answer is the same every time. It reads the newest version of the case, the engine settings
(engine.json) and CAN, what the engine can do on its own today. A case is hands-off only when it has no blocker.
Device checks are what the device check must confirm on each target before Start (engine account, build, app in front,
test files); they never make a case hands-off by themselves.

Each finding: {code, step, text, fix}. fix names who or what removes it: "the person", or the engine piece that is
not built yet (the 2026-10-26 hands-off plan: 1 ready devices, 2 deliver test files, 3 file picker, 4 web file
chooser, 5 write steps in runs).
"""
import argparse
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fixtures  # noqa: E402
import run  # noqa: E402

# What the engine does without a person today. A switch turns on only when its piece is built and tested.
CAN = {
    "ready_devices": True,       # piece 1 (built 2026-10-26): wake the screen, dismiss system popups, bring WingtraApp to the front
    "deliver_fixtures": True,    # piece 2 (built 2026-10-26): copy a case's test files to the devices, checked by sha256
    "file_picker": True,         # piece 3 (built 2026-10-26): sys-tap in the file browser (focus readbacks)
    "web_file_chooser": True,    # piece 4 (built 2026-10-26): act.py choose-file on W044 (Chrome's chooser interception)
    "run_write_steps": True,     # piece 5 (built 2026-10-26): Next/Continue write steps of a G3-clean case (run.standing_step); since 2026-11-03 also the save of the case's own flight in its own QA_ site
}
PIECE = {"ready_devices": "engine piece 1 (ready the devices)", "deliver_fixtures": "engine piece 2 (deliver test files)",
         "file_picker": "engine piece 3 (Android file picker)", "web_file_chooser": "engine piece 4 (web file chooser)",
         "run_write_steps": "engine piece 5 (write steps in runs)"}
FILE_STEP = re.compile(r"file[- ]?(picker|browser|chooser)|pick (the )?file|choose (a |the )?file|drop[- ]?zone", re.I)
FILE_DROP = "file-drop-modal-drop-zone"


def finding(code, text, fix, step=None):
    return {"code": code, "step": step, "text": text, "fix": fix}


def _uses_file_drop(case, platform):
    for s in case.get("steps") or []:
        if s.get("on", "both") not in ("both", platform):
            continue
        if run.helper_call(s)[0] == "choose-file":
            continue  # piece 4: the engine gives the file chooser its file
        if any(k == "testid" and run.bare_testid(v) == FILE_DROP for k, v in run.step_selectors(s)):
            return s.get("n")
    return None


def check(case, eng=None, can=None, root=None):
    """{case, version_file, hands_off, blockers, checks, notes} for one case record. root: the workspace whose test
    files are checked (default: this one)."""
    eng = eng if eng is not None else run.engine()
    can = dict(CAN, **(can or {}))
    cid = case.get("id")
    blockers, checks, notes = [], [], []
    status = case.get("status", "active")
    if status == "draft":
        blockers.append(finding("draft", "the case is a draft: its steps are not written for the engine yet",
                                "complete the case from source (the worker or a chat session)"))
    elif status != "active":
        blockers.append(finding("not_active", f"the case is {status}", "the person"))
    steps = case.get("steps") or []
    if not steps:
        blockers.append(finding("no_steps", "the case has no steps", "complete the case from source"))
    for s in steps:
        n, text, gates = s.get("n"), str(s.get("text") or ""), s.get("gates") or []
        if status == "active" and not (s.get("helper") or s.get("read_js")):
            blockers.append(finding("step_in_words", f"step {n} has no engine action: {text[:80]}",
                                    "complete the step from source", n))
        if "handover" in gates:  # the step waits for a person, whatever the engine can do
            fix = ("the person" if not FILE_STEP.search(text) else PIECE["file_picker"] if not can["file_picker"]
                   else "write the file pick as sys-tap steps (a new case version)")
            blockers.append(finding("hand_step", f"step {n} needs a person: {text[:100]}", fix, n))
        if "write_list" in gates and not (can["run_write_steps"] and run.standing_step(case, s, eng)):
            blockers.append(finding("write_step", f"step {n} is a write step the runner hands back for an OK: "
                                    f"{text[:80]}", PIECE["run_write_steps"] if not can["run_write_steps"] else
                                    "the person (only Next/Continue in a G3-clean case run without a hand-back)", n))
        if ("install" in gates or "clickup_write" in gates):
            blockers.append(finding("never_hands_off", f"step {n} installs or writes to ClickUp", "the person", n))
    if status == "active" and steps:
        why = run.refuse_case(case, eng)
        if why:
            blockers.append(finding("refused", why, "the person (engine settings)"))
    needs = case.get("needs") or {}
    if needs.get("link") in ("real_drone", "usb_radio") or needs.get("usb_port"):
        blockers.append(finding("hardware", "needs a real drone or the USB telemetry radio connected", "the person"))
    targets = case.get("targets")
    if targets in ("web", "both"):
        n = _uses_file_drop(case, "web")
        if n is not None:
            blockers.append(finding("web_file", f"step {n}: the web leg taps the file drop zone, so the browser's own "
                                    "file dialog would wait for a person", PIECE["web_file_chooser"] if not
                                    can["web_file_chooser"] else "write the web step as act.py choose-file", n))
    fxs = case.get("fixtures") or []
    names = [f["name"] if isinstance(f, dict) and isinstance(f.get("name"), str) else "a test file" for f in fxs]
    if fxs and can["deliver_fixtures"]:
        try:  # what act.py deliver refuses, found before Start (the file is not read here; deliver checks its sha256)
            fixtures.plan(case, root or run.WORKSPACE, digest=False,
                          platforms=fixtures.PLATFORMS.get(targets, ("tablet", "web")))
        except fixtures.FixtureError as e:
            blockers.append(finding("fixture_source", f"test file: {e}", "the person (put the file in place on this "
                                    "Mac, or correct the case)"))
        else:
            for name in names:
                notes.append(f"the engine copies {name} to each device first (sha256 checked; it never overwrites a "
                             "different file)")
            checks.append(finding("fixture_free", f"no other file sits where {', '.join(names)} goes on a device (the "
                                  "engine never overwrites one)", "the person (move the other file)"))
    else:
        for name in names:
            checks.append(finding("fixture_on_device", f"{name} must already be on each device "
                                  "(sha256 checked)", PIECE["deliver_fixtures"]))
    checks.append(finding("ct_account", f"the engine account ({eng.get('account')}) is signed in on each device",
                          "the person (sign-in)"))  # id kept from the CT days; the account is engine.json's
    checks.append(finding("build", f"each device runs the {eng.get('env')} build the test needs",
                          "the person (install)"))
    if can["ready_devices"]:
        notes.append("the engine wakes the tablets and brings WingtraApp to the front")
        checks.append(finding("no_pin", "each tablet either stays on while charging or has no PIN lock (the engine "
                              "cannot unlock a PIN)", "the person (one-time tablet setting)"))
    else:
        checks.append(finding("app_in_front", "each device is awake and unlocked with WingtraApp in front",
                              PIECE["ready_devices"]))
    if targets == "tablet":
        notes.append("Android only: the third device is TA3, which is not set up yet (2 of 3 devices)")
    return {"case": cid, "status": status, "hands_off": not blockers, "blockers": blockers, "checks": checks,
            "notes": notes}


def load_newest(cid, root=None):
    """(case, path) of the newest version under root (default: this workspace), or (None, None)."""
    if root is None:
        return run.load_case(cid)
    for d in (os.path.join(root, "qa", "cases"), os.path.join(root, "qa", "runner", "tests", "cases")):
        versions = sorted(glob.glob(os.path.join(d, f"{cid}.c*.json")),
                          key=lambda f: int((re.search(r"\.c(\d+)\.json$", f) or [0, 0])[1]))
        p = versions[-1] if versions else os.path.join(d, f"{cid}.json")
        if os.path.exists(p):
            return run.load_json(p), p
    return None, None


def check_id(cid, eng=None, can=None, root=None):
    case, path = load_newest(cid, root)
    if not isinstance(case, dict):
        return {"case": cid, "hands_off": False, "blockers": [finding("missing", f"no case {cid}", "the person")],
                "checks": [], "notes": []}
    out = check(case, eng, can, root)
    out["version_file"] = os.path.relpath(path, root or run.WORKSPACE)
    return out


def all_ids():
    ids = set()
    for d in run.CASE_DIRS:
        for f in glob.glob(os.path.join(d, "*.json")):
            m = re.match(r"^([A-Z][A-Z0-9]*-\d+)(?:\.c\d+)?\.json$", os.path.basename(f))
            if m:
                ids.add(m.group(1))
    return sorted(ids)


def main(argv):
    ap = argparse.ArgumentParser(prog="autonomy.py")
    ap.add_argument("cases", nargs="*")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args(argv)
    ids = all_ids() if a.all else a.cases
    if not ids:
        ap.error("name a case or --all")
    eng = run.engine()
    print(json.dumps([check_id(c, eng) for c in ids], indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
