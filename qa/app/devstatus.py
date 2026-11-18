"""The New test page's live device status (DESIGN.md 8.14): the engine's own status snapshot (qa/runner/status.py
--print, read only), read at most once every TTL_S seconds and shared by every page that asks, as one row per device
with what the person has to do. The engine account and the app build are checked by Hover at Prepare plan."""
import datetime
import json
import os
import subprocess
import sys
import threading
import time

TTL_S = 8  # a status read takes a few seconds (adb per tablet, ping and ssh for W044): pages share one
# the LaunchAgent starts the app server with PATH /usr/bin:/bin:/usr/sbin:/sbin, where the Android platform tools
# (Homebrew) are missing: without them status.py sees no tablet at all (2026-10-29)
TOOL_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")


def tool_env(env=None):
    """The environment for status.py: env (default os.environ) with TOOL_DIRS added to PATH when missing."""
    env = dict(os.environ if env is None else env)
    have = [d for d in env.get("PATH", "").split(os.pathsep) if d]
    env["PATH"] = os.pathsep.join(have + [d for d in TOOL_DIRS if d not in have])
    return env


ORDER = ("TA5", "ET65", "W044", "TA3")


class Cache:
    def __init__(self, root, run=None, ttl=TTL_S):
        self.root, self.ttl = root, ttl
        self.run = run or self._run
        self.lock = threading.Lock()
        self.at, self.snap, self.err = 0.0, None, None

    def _run(self):
        p = subprocess.run([sys.executable, "-I", os.path.join(self.root, "qa", "runner", "status.py"), "--print"],
                           capture_output=True, text=True, timeout=40, cwd=self.root, env=tool_env())
        if p.returncode:
            tail = (p.stderr or p.stdout).strip().splitlines()
            raise RuntimeError(tail[-1][:200] if tail else f"status.py exit {p.returncode}")
        return json.loads(p.stdout)

    def get(self, now=None):
        """(snapshot or None, error text or None, read time): one read at a time, the others wait and share it."""
        with self.lock:
            now = time.time() if now is None else now
            if (self.snap is None and self.err is None) or now - self.at >= self.ttl:
                try:
                    self.snap, self.err = self.run(), None
                except Exception as e:  # noqa: BLE001 (any failure is shown as "could not read", never raised)
                    self.snap, self.err = None, f"{type(e).__name__}: {e}"[:200]
                self.at = now
            return self.snap, self.err, self.at


def iso(ts):
    """A read time (epoch seconds) as UTC ISO text, or None before the first read."""
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).isoformat(timespec="seconds") if ts else None


def _tablet(tag, row):
    if not row:
        return "LOST", f"Not connected: plug {tag} in by USB"
    state = row.get("state")
    if state == "READY":
        return state, "Ready"
    if state == "BUSY":
        return state, f"Busy: a run is using it ({row.get('case') or row.get('lock') or 'a run'})"
    if state == "HANDED_OVER":
        return state, f"Handed over: {row.get('why') or 'to the tester'}"
    if state == "OFFLINE_EXPECTED":
        return state, "Marked as expected offline"
    adb = row.get("adb")
    if adb == "unauthorized":
        return "LOST", f"USB debugging: unlock {tag} and tap Allow"
    if adb == "offline":
        return "LOST", f"Not answering over USB: plug {tag} out and in again"
    return "LOST", f"Not connected: plug {tag} in by USB"


def _web(w):
    if w.get("state") == "READY":
        return "READY", "Ready (QA Chrome running)"
    if not w.get("link"):
        return "LOST", "Not reachable: check that W044 is on and connected"
    if not w.get("ssh"):
        return "LOST", "W044 answers, but not over SSH: check W044"
    return "LOST", "QA Chrome not running: on W044 run sh ~/qa-files/start-qa-chrome.sh"


def rows(snap, err, tags):
    """One row per device in ORDER, {tag, kind, ready, state, text}; tags: the devices set up on this Mac."""
    tabs = {t.get("tag"): t for t in (snap or {}).get("tablets") or [] if isinstance(t, dict)}
    out = []
    for tag in ORDER:
        kind = "web" if tag == "W044" else "tablet"
        if tag not in tags:
            state, text = "NOT_SET_UP", "Not set up on this Mac"
        elif err or not isinstance(snap, dict):
            state, text = "UNKNOWN", "Could not read the devices just now"
        elif tag == "W044":
            state, text = _web(snap.get("webpc044") or {})
        else:
            state, text = _tablet(tag, tabs.get(tag))
        out.append({"tag": tag, "kind": kind, "ready": state == "READY", "state": state.lower(), "text": text})
    return out
