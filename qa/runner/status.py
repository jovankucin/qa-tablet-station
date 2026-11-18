"""Engine status snapshot for the pane: tablets, webpc044, locks, ledger head. Read-only.

python3 -I qa/runner/status.py [--print]

Writes qa/runner/state/status.json. Tablet state is one of READY, BUSY, HANDED_OVER,
OFFLINE_EXPECTED, LOST (SCHEMA.md 2a): BUSY when a run holds its lock, HANDED_OVER when a
handover file exists, OFFLINE_EXPECTED when listed as expected offline, LOST when a
registered tablet is missing or not authorized, READY otherwise.
"""
import datetime
import fcntl
import glob
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from records import QA_DIR, ledger_head  # noqa: E402

STATE_DIR = os.path.join(QA_DIR, "runner", "state")
PKG = "com.wingtra.cloud.app"
KNOWN = {"TA5SERIAL01": "TA5", "ET65SERIAL0001": "ET65"}


def sh(argv, timeout=10):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def lock_held(name):
    """True while a run holds state/locks/<name>.lock (act.check_lock's probe). A <name>.json without it is a stale
    lock from a run that was killed (SIGTERM, SIGKILL, a restart), so Lock.__exit__ never removed it."""
    path = os.path.join(STATE_DIR, "locks", f"{name}.lock")
    if not os.path.exists(path):
        return False
    with open(path, "a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_SH | fcntl.LOCK_NB)
            fcntl.flock(f, fcntl.LOCK_UN)
            return False
        except BlockingIOError:
            return True


def profiles():
    out = {}
    def version(f):
        m = re.search(r"\.v(\d+)\.json$", f)
        return int(m.group(1)) if m else 0
    for f in sorted(glob.glob(os.path.join(QA_DIR, "devices", "*.json")), key=version):
        d = load_json(f, {})
        if d.get("kind") == "device":
            out[d["serial"]] = d  # the highest version wins
    return out


def tablets():
    rc, out = sh(["adb", "devices", "-l"])
    seen = {}
    for line in out.splitlines()[1:]:
        m = re.match(r"(\S+)\s+(device|unauthorized|offline|no permissions)\b(.*)$", line)
        if m:
            seen[m.group(1)] = (m.group(2), m.group(3))
    profs = profiles()
    serials = sorted(set(seen) | set(profs) | set(KNOWN))
    locks = {d.get("name"): d for d in (load_json(f, {}) for f in glob.glob(os.path.join(STATE_DIR, "locks", "*.json")))}
    handovers = {os.path.basename(f)[:-5]: load_json(f, {}) for f in glob.glob(os.path.join(STATE_DIR, "handover", "*.json"))}
    offline = set(load_json(os.path.join(STATE_DIR, "offline_expected.json"), []))
    rows = []
    for s in serials:
        prof = profs.get(s, {})
        tag = prof.get("tag") or KNOWN.get(s) or s[-4:]
        adb_state, extra = seen.get(s, ("absent", ""))
        row = {"serial": s, "tag": tag, "model": prof.get("model") or (re.search(r"model:(\S+)", extra).group(1) if "model:" in extra else None),
               "android": (prof.get("android") or {}).get("release"), "adb": adb_state, "registered": bool(prof),
               "cdp_port": prof.get("cdp_port"), "focus": None, "app_pid": None, "forward": None,
               "lock": (locks.get(s) or {}).get("holder"), "case": (locks.get(s) or {}).get("case")}
        stale = None
        if s in locks and not lock_held(s):
            stale = f"stale lock from {row['lock']} ignored"
            row["lock"] = row["case"] = None
        if adb_state == "device":
            _, focus = sh(["adb", "-s", s, "shell", "dumpsys window | grep -m1 mCurrentFocus"])
            if PKG in focus:
                row["focus"] = "app"
            elif "UsbDetailsActivity" in focus:
                row["focus"] = "usb_prefs"
            else:
                m = re.search(r"u0 ([\w.]+)/", focus)
                row["focus"] = m.group(1) if m else "unknown"
            _, pid = sh(["adb", "-s", s, "shell", f"pidof {PKG}"])
            row["app_pid"] = pid.strip() or None
            if row["cdp_port"]:
                _, fl = sh(["adb", "forward", "--list"])
                row["forward"] = any(l.startswith(s + " ") and f"tcp:{row['cdp_port']} " in l + " " for l in fl.splitlines())
        if s in handovers:
            row["state"], row["why"] = "HANDED_OVER", handovers[s].get("reason", "handed over to the tester")
        elif row["lock"]:
            row["state"], row["why"] = "BUSY", f"lock held by {row['lock']}"
        elif adb_state == "device":
            row["state"], row["why"] = "READY", ("USB preferences screen has focus" if row["focus"] == "usb_prefs" else "")
        elif s in offline:
            row["state"], row["why"] = "OFFLINE_EXPECTED", "marked expected offline"
        else:
            row["state"] = "LOST"
            row["why"] = {"unauthorized": "USB debugging not allowed on the tablet: tap Allow",
                          "offline": "adb reports offline", "absent": "not connected"}.get(adb_state, adb_state)
        if stale:
            row["why"] = "; ".join(x for x in (row["why"], stale) if x)
        rows.append(row)
    return rows


def webpc044():
    full = load_json(os.path.join(STATE_DIR, "webpc044.json"), {})
    rc_ping, _ = sh(["ping", "-c", "1", "-t", "2", "192.0.2.2"], 5)
    rc, out = sh(["ssh", "-o", "ConnectTimeout=4", "webpc044", "nc -z 127.0.0.1 9222 && echo up || echo down"], 10)
    failing = [k for k, v in (full.get("checks") or {}).items() if not v.get("ok")]
    state = "READY" if rc_ping == 0 and rc == 0 and out.strip() == "up" else ("LOST" if rc_ping != 0 else "DEGRADED")
    return {"state": state, "link": rc_ping == 0, "ssh": rc == 0, "chrome_9222": out.strip() if rc == 0 else None,
            "full_check": {"at": full.get("checked"), "ok": full.get("ok"), "failing": failing,
                           "browser": (full.get("info") or {}).get("browser"),
                           "clock_offset_ms": (full.get("info") or {}).get("clock_offset_ms")}}


def main(argv):
    seq, head = ledger_head()
    snap = {"refreshed": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "tablets": tablets(), "webpc044": webpc044(),
            "ledger": {"seq": seq, "head": head[:12]}}
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = os.path.join(STATE_DIR, "status.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(snap, f, indent=2)
    os.replace(tmp, os.path.join(STATE_DIR, "status.json"))
    if "--print" in argv:
        print(json.dumps(snap, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
