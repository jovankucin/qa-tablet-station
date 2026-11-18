"""status.py's tablet rows against a fake adb and a temporary state folder: no device. A lock file
state/locks/<serial>.json makes the tablet BUSY only while a run holds the flock on <serial>.lock; one left behind by a
killed run.py is ignored with a note (audit findings 1-2, 2026-10-28: the tablet stayed BUSY for good).

python3 -I qa/runner/tests/test_status.py
"""
import fcntl
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import status  # noqa: E402

SERIAL = "QATEST0001"
APP = "mCurrentFocus=Window{1 u0 com.wingtra.cloud.app/com.wingtra.cloud.app.MainActivity}"
USB = "mCurrentFocus=Window{2 u0 com.android.settings/com.android.settings.Settings$UsbDetailsActivity}"
LOCK = {"name": SERIAL, "holder": "R-20261028-QA9-TERR-004-01", "case": "TERR-004", "since": "2026-10-28T10:00:00.000Z"}
fails = 0
checks = 0


def check(cond, what):
    global fails, checks
    checks += 1
    if not cond:
        fails += 1
        print("FAIL", what)


class Adb:
    """The answers of the fake adb: connected or not, and the window in front."""
    connected, focus = True, APP


def fake_sh(argv, timeout=10):
    if argv[1:] == ["devices", "-l"]:
        return 0, "List of devices attached\n" + (f"{SERIAL}  device usb:1-1 model:QA_Stub\n" if Adb.connected else "")
    if "mCurrentFocus" in argv[-1]:
        return 0, Adb.focus
    if "pidof" in argv[-1]:
        return 0, "4242\n"
    return 0, ""


status.sh = fake_sh
status.KNOWN = {}
status.profiles = lambda: {SERIAL: {"kind": "device", "serial": SERIAL, "tag": "QA9"}}


def row(tmp, meta=None, lock_file=True, connected=True, focus=APP):
    """The QA9 row with the given lock files in a fresh state folder."""
    status.STATE_DIR = tmp
    Adb.connected, Adb.focus = connected, focus
    locks = os.path.join(tmp, "locks")
    os.makedirs(locks, exist_ok=True)
    for f in os.listdir(locks):
        os.remove(os.path.join(locks, f))
    if lock_file:
        open(os.path.join(locks, f"{SERIAL}.lock"), "w").close()
    if meta:
        with open(os.path.join(locks, f"{SERIAL}.json"), "w") as m:
            json.dump(meta, m)
    return [r for r in status.tablets() if r["serial"] == SERIAL][0]


def main():
    with tempfile.TemporaryDirectory() as tmp:
        # 1. no lock: READY
        r = row(tmp)
        check(r["state"] == "READY" and r["lock"] is None and r["why"] == "", f"no lock: READY ({r['state']}, {r['why']!r})")

        # 2. a run holds the lock: BUSY, named
        r = row(tmp, LOCK)
        with open(os.path.join(tmp, "locks", f"{SERIAL}.lock"), "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)  # what run.Lock does
            r = status.tablets()[0]
            check(r["state"] == "BUSY" and r["lock"] == LOCK["holder"] and r["case"] == "TERR-004" and
                  r["why"] == f"lock held by {LOCK['holder']}", f"held lock: BUSY ({r['state']}, {r['why']!r})")
            check(status.lock_held(SERIAL), "lock_held while the flock is taken")
        check(not status.lock_held(SERIAL), "lock_held after the flock is released")

        # 3. the run was killed: its .json stays, nobody holds the flock -> READY with a note, the file left as it is
        r = row(tmp, LOCK)
        check(r["state"] == "READY" and r["lock"] is None and r["case"] is None, f"stale lock: READY ({r['state']}, lock {r['lock']})")
        check(r["why"] == f"stale lock from {LOCK['holder']} ignored", f"stale lock note: {r['why']!r}")
        meta_path = os.path.join(tmp, "locks", f"{SERIAL}.json")
        check(json.load(open(meta_path)) == LOCK and os.path.getsize(os.path.join(tmp, "locks", f"{SERIAL}.lock")) == 0,
              "status.py changes neither lock file (read-only)")

        # 4. a .json without its .lock file: no run can hold it
        r = row(tmp, LOCK, lock_file=False)
        check(r["state"] == "READY" and "stale lock" in r["why"], f"json without .lock: READY ({r['state']}, {r['why']!r})")

        # 5. the note joins the row's own reason
        r = row(tmp, LOCK, connected=False)
        check(r["state"] == "LOST" and r["why"] == f"not connected; stale lock from {LOCK['holder']} ignored",
              f"stale lock on a LOST tablet: {r['state']}, {r['why']!r}")
        r = row(tmp, LOCK, focus=USB)
        check(r["state"] == "READY" and r["why"] == f"USB preferences screen has focus; stale lock from {LOCK['holder']} ignored",
              f"stale lock with the USB screen in front: {r['why']!r}")

        # 6. a handover still wins over a stale lock
        os.makedirs(os.path.join(tmp, "handover"), exist_ok=True)
        with open(os.path.join(tmp, "handover", f"{SERIAL}.json"), "w") as h:
            json.dump({"reason": "handed over to the tester"}, h)
        r = row(tmp, LOCK)
        check(r["state"] == "HANDED_OVER", f"handover with a stale lock: {r['state']}")

    print(f"status.py locks: {checks} checks, {fails} failures")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
