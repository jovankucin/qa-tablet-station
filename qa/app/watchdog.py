"""QA watchdog: tells the person on this Mac when Hover (the QA worker) died or never ran, or its page is down
(tests/2026-10-27-qa-status-review/ALERTS_PLAN.md, Part B; the morning prep it also watched was removed on
2026-10-29). It runs outside Claude, from the LaunchAgent com.wingtra.qa-watchdog, because in the misses it is for
(no internet, spend limit, app restart) no Claude session could report anything.

python3 -I qa/app/watchdog.py              check; a problem must still be there 10 min later before it alerts
python3 -I qa/app/watchdog.py --dry-run    one pass, print what it would alert; no wait, no notification, no write

Read-only except its own qa/runner/state/watchdog.json. Weekdays 08:45-19:00 Zurich time only, so a run that launchd
catches up at wake or login outside that window stays silent. To prove the launchd path, create
qa/runner/state/watchdog.test and kickstart the job: it sends one test alert and removes the file.
"""
import datetime
import fcntl
import json
import os
import socket
import subprocess
import sys
import time
import traceback
from zoneinfo import ZoneInfo

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(APP_DIR))
sys.path.insert(0, APP_DIR)
import worker  # noqa: E402

ZURICH = ZoneInfo("Europe/Zurich")
STATE_REL = "qa/runner/state/watchdog.json"
LOCK_REL = "qa/runner/state/watchdog.lock"
TEST_REL = "qa/runner/state/watchdog.test"
LOG_HINT = "~/Library/Logs/wingtra-qa-watchdog.log"
APP_ADDR = ("127.0.0.1", 8787)

WINDOW = ((8, 45), (19, 0))         # checks run only inside this, on weekdays
WORKER_END = (18, 45)               # the worker waits until 19:00; after this "off" is normal
WORKER_TRIES = tuple((h, 15) for h in range(8, 19))  # the scheduled task's cron, 15 8-18 * * 1-5 (2026-11-02)
RECHECK_S = 10 * 60
REMIND_S = 2 * 3600


def now_utc():
    return datetime.datetime.now(datetime.timezone.utc)


def local(now):
    return now.astimezone(ZURICH)


def parse(iso):
    try:
        return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def hhmm(iso):
    t = parse(iso)
    return local(t).strftime("%H:%M") if t else "?"


def in_window(now, end=WINDOW[1]):
    t = local(now)
    return t.weekday() < 5 and WINDOW[0] <= (t.hour, t.minute) < end


def next_try(tries, now):
    t = local(now)
    later = [f"{h:02d}:{m:02d}" for h, m in tries if (h, m) > (t.hour, t.minute)]
    return later[0] if later else "none today"


def read_json(root, rel):
    try:
        with open(os.path.join(root, rel), encoding="utf-8") as f:
            obj = json.load(f)
    except (OSError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def worker_problem(root, now, status):
    """(key, text) for the QA worker, or None."""
    st = status(root, now)
    if st.get("state") == "stuck":
        rid = st.get("request")
        req = next((r for r in worker.list_requests(root) if r.get("id") == rid), {})
        cases = [c for c in req.get("engine_cases") or [] if isinstance(c, str)]
        what = ", ".join(cases) or rid
        since = hhmm(st.get('last_activity') or st.get('since'))
        return ("worker_stuck", rid), f"Hover went quiet on {what} since {since}. See its page."
    if st.get("state") == "off" and in_window(now, end=WORKER_END):
        return ("worker_off", st.get("heartbeat_at")), (
            f"Hover is off duty, so Prepare plan and Run will wait. Next try {next_try(WORKER_TRIES, now)}; "
            "or Claude app > Scheduled > Hover > Run now.")
    return None


def app_up():
    try:
        socket.create_connection(APP_ADDR, timeout=3).close()
        return True
    except OSError:
        return False


def problems(root, now, status=worker.status, app=app_up):
    """{key: text} of what is wrong now; empty outside the window."""
    if not in_window(now):
        return {}
    found = [worker_problem(root, now, status)]
    if not app():
        found.append((("app_down",), "Hover's page is not reachable. Restart: launchctl kickstart -k gui/501/com.wingtra.qa-app"))
    return {json.dumps(k): text for k, text in (f for f in found if f)}


def load_state(root, day):
    st = read_json(root, STATE_REL) or {}
    if st.get("day") != day:
        st = {"day": day, "sent": {}}
    st.setdefault("sent", {})
    return st


def save_state(root, st):
    path = os.path.join(root, STATE_REL)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def due(st, key, now):
    """'new', 'remind' or None: one alert per key, plus one reminder after REMIND_S if it is still there."""
    s = st["sent"].get(key)
    if not s:
        return "new"
    last = parse(s.get("last"))
    if s.get("count", 0) < 2 and last and (now - last).total_seconds() >= REMIND_S:
        return "remind"
    return None


def notify(text):
    quoted = '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    try:
        r = subprocess.run(["osascript", "-e", f'display notification {quoted} with title "Hover"'],
                           capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def send(root, st, key, text, now, send_fn):
    kind = due(st, key, now)
    if not kind:
        return None
    msg = ("Still: " + text) if kind == "remind" else text
    ok = send_fn(msg)
    s = st["sent"].setdefault(key, {"first": now.isoformat(), "count": 0})
    s.update(last=now.isoformat(), count=s["count"] + 1, text=msg, delivered=ok)
    st["last_alert"] = {"at": now.isoformat(), "text": msg, "delivered": ok}
    print(f"{now.isoformat()} alert ({kind}, delivered={ok}): {msg}", flush=True)
    return msg


def run(root, clock=now_utc, sleep=time.sleep, status=worker.status, app=app_up, send_fn=None):
    """One watchdog run: find problems, wait RECHECK_S, alert those still there with the same key."""
    send_fn = send_fn or notify
    first = problems(root, clock(), status, app)
    if first:
        sleep(RECHECK_S)
    now = clock()
    second = problems(root, now, status, app) if first else {}
    st = load_state(root, local(now).strftime("%Y-%m-%d"))
    sent = [m for m in (send(root, st, k, t, now, send_fn) for k, t in second.items() if k in first) if m]
    st["last_run"] = now.isoformat()
    save_state(root, st)
    return sent


def test_alert(root, now, send_fn=None):
    """The launchd path test: one alert, recorded, and the trigger file removed."""
    os.remove(os.path.join(root, TEST_REL))
    msg = "QA watchdog test: alerts from the watchdog reach this Mac."
    ok = (send_fn or notify)(msg)
    st = load_state(root, local(now).strftime("%Y-%m-%d"))
    st["last_alert"] = {"at": now.isoformat(), "text": msg, "delivered": ok, "test": True}
    save_state(root, st)
    print(f"{now.isoformat()} test alert (delivered={ok})", flush=True)


def main(argv, root=ROOT):
    if argv[1:] == ["--dry-run"]:
        now = now_utc()
        print(json.dumps({"now_local": local(now).isoformat(), "in_window": in_window(now),
                          "would_alert_if_still_there_in_10_min": list(problems(root, now).values())}, indent=1))
        return 0
    if argv[1:]:
        print(__doc__)
        return 2
    os.makedirs(os.path.join(root, os.path.dirname(LOCK_REL)), exist_ok=True)
    with open(os.path.join(root, LOCK_REL), "a") as lk:
        try:
            fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0  # another watchdog run is still waiting for its recheck
        try:
            if os.path.exists(os.path.join(root, TEST_REL)):
                test_alert(root, now_utc())
            else:
                run(root)
        except Exception as e:  # noqa: BLE001 (the watchdog must say when it is broken)
            traceback.print_exc()
            notify(f"QA watchdog error: {type(e).__name__}. See {LOG_HINT}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
