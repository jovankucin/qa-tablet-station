"""Tests of the QA app (qa/app/DESIGN.md): request guards, /files/ policy, redaction, write scope, write flow,
and a read-only pass over the real workspace.

python3 -I qa/app/tests/test_app.py [-v]

Servers are started in-process with serve.build_server(root, 0) in a thread. Every test that writes uses a fixture
workspace in a temp folder; the real workspace is only read. Since 2026-11-05 (tests/2026-11-05-wingmate-debug
FINDINGS #17) that is checked on this process itself, so other processes that write to qa/ and tests/ meanwhile (a
Hover run, make_dsm.py, the ledger hook of every session) no longer fail the run: an audit hook records every write
this process makes under the real qa/ or tests/ (a file opened for writing, a rename, a removal, a new folder) and
every program it starts. The run fails when this process wrote there, or when it started a program that could have
(anything but a read-only git -C <clone> and tasks.py --root <a temp workspace>) while the snapshot of qa/ and tests/
(paths, sizes, mtimes; qa/runner/state and __pycache__ left out; a ledger day file may only grow) changed; a change
with neither is another process's, and is reported on stderr.
Planted raw values are built by concatenation so that this file itself holds no e-mail, token or share link.
Failure messages name the path and the kind of value, never a response body.
"""
import codecs
import contextlib
import fcntl
import hashlib
import http.client
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(TESTS_DIR)
QA_DIR = os.path.dirname(APP_DIR)
REAL_ROOT = os.path.dirname(QA_DIR)
sys.path.insert(0, APP_DIR)
sys.path.insert(0, os.path.join(QA_DIR, "runner"))

import autonomy  # noqa: E402
import case_schema  # noqa: E402
import data as data_model  # noqa: E402
import records  # noqa: E402
import redact  # noqa: E402
import security  # noqa: E402
import serve  # noqa: E402
import writes  # noqa: E402

TASKS_PY = os.path.join(APP_DIR, "tasks.py")
TOKEN_HEADER = "X-QA-Token"

EMAIL = "planted.person" + "@" + "example.org"
JWT = "ey" + "JhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" + "." + "eyJzdWIiOiJwbGFudGVkIn0" + "." + "c2lnbmF0dXJlLXBsYW50ZWQ"
SHARE = "https://app.company.example/" + "shared/abcdef123"
SECRET = "FAKE-SECRET-" + "VALUE-42"
PLANT = f"contact {EMAIL} token {JWT} link {SHARE} pw {SECRET} end"
# the raw values and distinctive fragments of them; none may reach a response
RAW = {"e-mail": EMAIL, "jwt": JWT, "share link": SHARE, "secret": SECRET, "e-mail local part": "planted.person@",
       "jwt signature": "c2lnbmF0dXJlLXBsYW50ZWQ", "share id": "abcdef123", "secret fragment": "FAKE-SECRET"}
PATTERNS = {
    "e-mail": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "jwt": re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    "share link": re.compile(r"https?://[^\s\"']*(?:share|sharing|/s/|token=)[^\s\"']*", re.I),
}

SLUG = "2026-10-21-fixture"
RUN = "R-20261021-TA5-PLAN-001-01"
TODAY = records.now_utc()[:10].replace("-", "")
RUN_HB = f"R-{TODAY}-TA5-PLAN-002-01"
DIFF = "D-PLAN-001-20261021-01"
RUN_BT = "R-20261016-TA5-PLAN-003-01"
BT_SHOT = f"tests/{SLUG}/shots/backtest_after.png"
TASK = "T-20261020-001"
SERIAL = "SER123"
PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")
SHOT = f"tests/{SLUG}/TA5/shots/{RUN}_end.png"
UNREF_SHOT = f"tests/{SLUG}/TA5/shots/unreferenced.png"
LIVE_SHOT = f"tests/{SLUG}/TA5/shots/R-20261021-TA5-PLAN-001-02_step1.png"
QT_LOG = f"tests/{SLUG}/TA5/logs/{RUN}_qt.txt"
TICKET_DIR = f"tests/{SLUG}/tickets/1"
LEDGER_FILE = re.compile(r"^qa/ledger/[^/]+\.jsonl\Z")  # append-only: the no-file-changed check lets them grow

LOG = []
_SNAPSHOT = None
_SECRETS_BEFORE = None
# what this process writes under the real qa/ and tests/ and which programs it starts (the audit hook, setUpModule)
AUDIT = {"on": False, "tops": (), "writes": [], "spawned": []}
_HOOKED = []
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
# audit event -> (indexes of its path arguments, index of its dir_fd argument or None)
WRITE_EVENTS = {"os.rename": ((0, 1), 2), "os.remove": ((0,), 1), "os.rmdir": ((0,), 1), "os.mkdir": ((0,), 2),
                "os.truncate": ((0,), None), "os.link": ((0, 1), 2), "os.symlink": ((1,), 2), "os.chmod": ((0,), 2),
                "os.utime": ((0,), 3), "shutil.rmtree": ((0,), 1), "shutil.move": ((0, 1), None),
                "shutil.copyfile": ((1,), None), "shutil.copytree": ((1,), None)}
SPAWN_EVENTS = ("subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.fork", "os.forkpty")


# ---- helpers ------------------------------------------------------------------------------------------------------

def sha256_head(path, n):
    """sha256 of the first n bytes of a file; None when it cannot be read."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            h.update(f.read(n))
    except OSError:
        return None
    return h.hexdigest()


def snapshot(root):
    """{rel: (size, mtime_ns)} of every file under qa/ and tests/, without qa/runner/state, __pycache__, .DS_Store.
    A ledger day file also keeps the sha256 of its content, so that an append can be told from a rewrite."""
    out = {}
    for top in ("qa", "tests"):
        for dirpath, dirnames, filenames in os.walk(os.path.join(root, top)):
            rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
            dirnames[:] = [d for d in dirnames if d != "__pycache__" and f"{rel_dir}/{d}" != "qa/runner/state"]
            for name in filenames:
                if name == ".DS_Store":  # Finder writes these on its own
                    continue
                p = os.path.join(dirpath, name)
                try:
                    st = os.lstat(p)
                except OSError:
                    continue
                rel = f"{rel_dir}/{name}"
                out[rel] = (st.st_size, st.st_mtime_ns) + ((sha256_head(p, st.st_size),) if LEDGER_FILE.match(rel)
                                                           else ())
    return out


def appended(root, rel, old, new):
    """True when a ledger day file only grew by ledger-hook lines: its old bytes are still the start of it, and every
    line after them is an entry of another session's hook (src "hook"), not something this test run wrote."""
    path = os.path.join(root, *rel.split("/"))
    if not (LEDGER_FILE.match(rel) and old[2] and new[0] >= old[0] and sha256_head(path, old[0]) == old[2]):
        return False
    with open(path, "rb") as f:
        f.seek(old[0])
        tail = f.read()
    try:
        return all(json.loads(line).get("src") == "hook" for line in tail.splitlines() if line.strip())
    except ValueError:
        return False


def snapshot_diff(before, after, root=None):
    """With root, a ledger day file that only grew counts as unchanged: the ledger hook of every Claude session in
    the workspace appends to it while the tests run. A rewrite or a truncation still counts."""
    changed = sorted(k for k in before.keys() & after.keys()
                     if before[k] != after[k] and not (root and appended(root, k, before[k], after[k])))
    return {"added": sorted(after.keys() - before.keys()), "removed": sorted(before.keys() - after.keys()),
            "changed": changed}


def watched_tops(root):
    """The folders this run must not write in: the real qa/ and tests/, as written and as resolved."""
    return tuple(sorted({os.path.join(base, top) + os.sep for base in (os.path.abspath(root), os.path.realpath(root))
                         for top in ("qa", "tests")}))


def under(path, tops):
    """True when path (str or bytes, absolute or relative to this process's folder) lies in one of tops, outside
    __pycache__ (Python's own bytecode, which the snapshot leaves out too)."""
    if isinstance(path, bytes):
        path = os.fsdecode(path)
    if not isinstance(path, str):
        return False
    p = os.path.normpath(os.path.abspath(path))
    return "__pycache__" not in p.split(os.sep) and any((p + os.sep).startswith(t) for t in tops)


def audit_hook(event, args):
    """sys.addaudithook: while AUDIT["on"], note each write of this process under AUDIT["tops"] and each program it
    starts. Pure string work (no file call, so no event of its own), and never raises into the audited call."""
    if not AUDIT["on"]:
        return
    try:
        tops = AUDIT["tops"]
        if event == "open":
            if isinstance(args[2], int) and args[2] & WRITE_FLAGS and under(args[0], tops):
                AUDIT["writes"].append((event, os.fsdecode(args[0])))
        elif event in WRITE_EVENTS:
            idx, fd_at = WRITE_EVENTS[event]
            dir_fd = args[fd_at] if fd_at is not None and len(args) > fd_at else None
            for i in idx:
                p = args[i] if len(args) > i else None
                # a name relative to an open folder (shutil.rmtree's inner steps): its rmtree event names the folder
                if isinstance(dir_fd, int) and dir_fd >= 0 and isinstance(p, (str, bytes)) and not os.path.isabs(p):
                    continue
                if under(p, tops):
                    AUDIT["writes"].append((event, os.fsdecode(p)))
        elif event in SPAWN_EVENTS:
            argv = args[1] if event == "subprocess.Popen" else args[-2] if event in ("os.exec", "os.posix_spawn",
                                                                                     "os.spawn") else args
            argv = [os.fsdecode(a) if isinstance(a, (str, bytes)) else str(a) for a in argv] \
                if isinstance(argv, (list, tuple)) else [str(argv)]
            AUDIT["spawned"].append((event, argv))
    except Exception:  # noqa: BLE001 (the hook must never break the call it watches)
        AUDIT["writes"].append(("audit", "the audit hook could not read an event"))


def harmless_spawn(entry, root):
    """True for a program this test run starts that cannot write in the real qa/ or tests/: a read-only git in a clone
    (data.py: git -C <clone> with GIT_OPTIONAL_LOCKS=0), or tasks.py on a temp workspace (--root outside root)."""
    event, argv = entry
    if event != "subprocess.Popen" or not argv:
        return False
    after = {a: argv[i + 1] for i, a in enumerate(argv[:-1])}
    real = os.path.abspath(root) + os.sep
    if os.path.basename(argv[0]) == "git" and "-C" in after:
        return not under(after["-C"], watched_tops(root))
    if any(os.path.basename(a) == "tasks.py" for a in argv) and "--root" in after:
        return not (os.path.abspath(after["--root"]) + os.sep).startswith(real)
    return False


def real_workspace_problem(before, after, root, audit):
    """None when this test run left the real workspace as it was, else why. The run failed it when it wrote under
    qa/ or tests/ itself (audit["writes"]), or when it started a program that could have (audit["spawned"],
    harmless_spawn) and the snapshot changed (snapshot_diff). A changed snapshot with neither is another process's
    write (a Hover run, make_dsm.py, a ledger hook): reported on stderr, not failed."""
    if audit["writes"]:
        return f"the test run wrote in the real workspace: {sorted(set(audit['writes']))[:10]}"
    d = snapshot_diff(before, after, root)
    if not any(d.values()):
        return None
    risky = [e for e in audit["spawned"] if not harmless_spawn(e, root)]
    if risky:
        return f"the test run changed files in the real workspace: {d}; it started {risky[:3]}"
    sys.stderr.write(f"\n[test_app] qa/ or tests/ changed while the tests ran, by another process (this run wrote "
                     f"nothing there): {d}\n")
    return None


def put(root, rel, content):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if isinstance(content, (dict, list)):
        content = records.canonical(content)
    with open(path, "wb") as f:
        f.write(content.encode("utf-8") if isinstance(content, str) else content)
    return path


def sv(v):
    """A sourced value as the engine writes it."""
    return {"v": v, "st": "observed", "src": [], "why": None}


def case_record(cid, status, title, req_text, steps=None, created="2026-10-21T08:00:00.000Z", **extra):
    area = cid.split("-")[0]
    steps = steps or [{"n": 1, "text": "Open Flight planning", "helper": None, "gates": []}]
    rec = {
        "kind": "case", "schema": 1, "id": cid, "created": created, "author": "claude", "supersedes": None,
        "title": title, "area": area,
        "requirement": {"text": req_text, "sha256": case_schema.requirement_sha256(req_text),
                        "source": {"type": "user_chat", "ref": "chat 2026-10-21"}},
        "targets": "tablet", "routing": [], "expected_differences": [],
        "preconditions": [{"id": "P1", "text": "Logged in with the CT account", "check": None, "applies_to": "tablet"}],
        "steps": steps, "expected": "The plan opens", "fixtures": [],
        "covered_paths": ["apps/src/pages/FlightPlanning/**"],
        "needs": {"link": "none", "usb_port": False, "account_lock": False, "flags": [], "drone_hw": None},
        "status": status,
    }
    rec.update(extra)
    problems = case_schema.validate_case(rec)
    assert not problems, problems
    return rec


def build_fixture(root, outside):
    """A small workspace with every kind of file the app reads, raw values planted in each of them."""
    put(root, "qa/runner/engine.json", {"env": "staging", "account": "ct-label", "web_origin": "https://cloud-stag.example"})
    put(root, "qa/secrets.env", f"QA_TEST_PASSWORD={SECRET}\n")
    put(root, "qa/cache/clickup/CL-12345.json", {"title": PLANT})
    put(root, f"qa/devices/{SERIAL}.v1.json", {"kind": "device", "tag": "TA5", "serial": SERIAL, "model": "SM-X350"})
    ledger = [
        {"seq": 1, "ref": {"kind": "case", "id": "PLAN-001", "path": "qa/cases/PLAN-001.json"}},
        {"seq": 2, "ref": {"kind": "case", "id": "PLAN-007", "path": "qa/cases/PLAN-007.json"}},
        {"seq": 3, "ref": {"kind": "case", "id": "PLAN-002", "path": "qa/cases/PLAN-002.c1.json"}},
        {"seq": 4, "ref": {"kind": "task", "path": "qa/tasks/T-20261021-009.json"}},
        {"seq": 5, "ref": {"kind": "run", "id": RUN, "path": f"qa/runs/{SLUG}/{RUN}.json"}, "note": PLANT},
    ]
    put(root, "qa/ledger/2026-10-21.jsonl", "".join(json.dumps(x) + "\n" for x in ledger))

    put(root, "qa/cases/PLAN-001.json", case_record(
        "PLAN-001", "active", "Plan opens", f"The plan opens. Asked by {PLANT}",
        steps=[{"n": 1, "text": "Open the plan", "helper": "tools/act.py {target} tap testid:open", "gates": []}],
        needs={"link": "mocklink", "usb_port": False, "account_lock": True, "flags": ["Mocklink"], "drone_hw": 4},
        flags_watch=["DevReporting"]))
    put(root, "qa/cases/PLAN-002.json", case_record("PLAN-002", "draft", "Plan saves", "The plan saves."))
    put(root, "qa/cases/PLAN-003.json", case_record("PLAN-003", "draft", "Plan deletes", "The plan deletes."))
    put(root, "qa/runner/tests/cases/SAK-005.json", case_record("SAK-005", "draft", "Reporter opens", "It opens."))

    attempt = {
        "n": 1, "started": "2026-10-21T09:00:00.000Z", "ended": "2026-10-21T09:02:00.000Z",
        "steps": [{"n": 1, "text": "Open the plan", "actions": [{"day": "2026-10-21", "seq": 5}],
                   "readback": {"expected": ["open"], "got": {"open": PLANT}, "ok": False}}],
        "evidence": [{"kind": "shot", "path": SHOT}, {"kind": "qt_log", "path": QT_LOG}],
        "verdict": {"v": "FAIL", "evidence_kind": "device", "note": PLANT},
    }
    put(root, f"qa/runs/{SLUG}/{RUN}.json", {
        "kind": "run", "schema": 1, "id": RUN, "created": "2026-10-21T09:03:00.000Z", "author": "runner",
        "supersedes": None, "mode": "live", "case": "PLAN-001",
        "target": {"class": "tablet", "serial": SERIAL, "tag": "TA5"},
        "build": {"hash7": sv("84d3721"), "about_label": sv("1.74.0-staging-84d3721"), "semver": sv("1.74.0"),
                  "env": sv("staging"), "version_code": sv(1234)},
        "attempts": [attempt], "verdict": {"v": "FAIL", "evidence_kind": "device", "note": PLANT},
        "values": {f"key {EMAIL}": PLANT}, "differential": DIFF, "judgement_pending": [],
        "prediction": {"id": f"PR-{RUN}", "ledger": {"day": "2026-10-21", "seq": 4}},
    })
    put(root, f"qa/runs/{SLUG}/pred/PR-{RUN}.json", {"kind": "prediction", "id": f"PR-{RUN}", "text": PLANT})
    put(root, f"qa/runs/{SLUG}/{RUN_HB}.json", {
        "kind": "run", "schema": 1, "id": RUN_HB, "created": records.now_utc(), "author": "runner",
        "supersedes": None, "mode": "live", "case": "PLAN-002",
        "target": {"class": "tablet", "serial": SERIAL, "tag": "TA5"}, "build": {"hash7": sv("84d3721")},
        "attempts": [], "verdict": {"v": "BLOCKED", "by": {"kind": "handover", "ref": PLANT},
                                    "handback": {"step": 1, "reason": PLANT, "command": PLANT}},
    })
    put(root, f"qa/runs/{SLUG}/{DIFF}.json", {
        "kind": "differential", "schema": 1, "id": DIFF, "created": "2026-10-21T09:05:00.000Z", "case": "PLAN-001",
        "result": "consistent", "runs": [RUN], "builds": ["84d3721"], "compared": {"note": PLANT},
    })

    bt = {"kind": "run", "schema": 1, "id": RUN_BT, "created": "2026-10-20T10:00:00.000Z", "author": "claude",
          "supersedes": None, "mode": "backtest", "case": "PLAN-003", "build": {"hash7": sv("1234567")},
          "attempts": [{"n": 1, "started": "2026-10-16T09:00:00.000Z", "steps": [], "evidence": []}],
          "verdict": {"v": "PASS", "evidence_kind": "device"},
          "evidence": [{"kind": "file", "ptr": {"t": "file", "path": BT_SHOT, "sha256": "0" * 64}}]}
    put(root, f"qa/runs/{SLUG}/{RUN_BT}.json", bt)
    put(root, f"qa/runs/{SLUG}/{RUN_BT}c1.json", dict(bt, id=RUN_BT + "c1", supersedes=RUN_BT,
                                                       created="2026-10-20T11:00:00.000Z",
                                                       verdict={"v": "PARTIAL", "evidence_kind": "device"}))
    put(root, BT_SHOT, PNG)
    put(root, SHOT, PNG)
    put(root, UNREF_SHOT, PNG)
    put(root, LIVE_SHOT, PNG)
    put(root, QT_LOG, f"09:00:01 D qt: login as {PLANT}\n")
    put(root, f"tests/{SLUG}/.hidden.txt", PLANT)
    put(root, f"tests/{SLUG}/notes.env", PLANT)
    put(root, f"tests/{SLUG}/build.apk", b"PK")

    put(root, f"{TICKET_DIR}/title.txt", f"[CT] Plan: does not open for {EMAIL}\n")
    put(root, f"{TICKET_DIR}/description.txt",
        f"Issue Summary\n{PLANT}\n\nSteps to Reproduce\n1. open\n\nExpected Result\nopens\n\nActual Result\n{PLANT}\n")
    put(root, f"{TICKET_DIR}/draft.json", {"run_record": f"qa/runs/{SLUG}/{RUN}.json", "note": PLANT})
    put(root, f"{TICKET_DIR}/comment_CL-12345.md", f"Seen again: {PLANT}\n")
    put(root, f"{TICKET_DIR}/pack/shot.png", PNG)
    put(root, f"{TICKET_DIR}/pack/notes.txt", PLANT)
    put(root, f"tests/{SLUG}/tickets/ticket1_title.txt", f"Old ticket {EMAIL}")
    put(root, f"tests/{SLUG}/tickets/ticket1_short.md", PLANT)
    put(root, f"tests/{SLUG}/triage/TR-1/triage.json", {"classification": "app_bug", "summary": PLANT})

    put(root, f"qa/tasks/{TASK}.json", {
        "kind": "task", "schema": 1, "id": TASK, "created": "2026-10-20T08:00:00.000Z", "author": "user",
        "text": PLANT, "clickup": None, "targets": ["TA5"], "priority": "normal", "cases": ["PLAN-001"],
        "state": "results_ready", "history": [{"at": "2026-10-20T08:00:00.000Z", "from": None, "to": "queued",
                                               "by": "user", "via": "app", "note": PLANT}],
        "runs": [RUN, "R-20261021-TA5-PLAN-001-09"], "campaign": SLUG,
        "outputs": {"summary": None, "tickets": [], "comments": []},
    })
    now = records.now_utc()
    put(root, "qa/runner/state/status.json", {"refreshed": now, "note": PLANT})
    put(root, f"qa/runner/state/progress/{SERIAL}.json", {
        "run": RUN, "case": "PLAN-001", "target": "TA5", "attempt": 1,
        "step": {"n": 1, "text": PLANT, "started": now}, "steps_total": 1, "started": now, "updated": now,
        "state": "running", "verdict": None, "last_shot": LIVE_SHOT, "clock_offset_ms": 12})
    put(root, f"qa/runner/state/locks/{SERIAL}.json", {"name": SERIAL, "holder": PLANT, "case": "PLAN-001",
                                                       "since": now})
    put(root, "qa/runner/state/handover/TA5.json", {"reason": PLANT, "since": now})

    # links that lead out of the root or into denied places
    put(outside, "outside.txt", PLANT)
    put(outside, "ticket/title.txt", "[CT] outside")
    os.symlink(os.path.join(outside, "outside.txt"), os.path.join(root, "tests", SLUG, "out.txt"))
    os.symlink(outside, os.path.join(root, "tests", SLUG, "outdir"))
    os.symlink(os.path.join("..", "..", "qa", "secrets.env"), os.path.join(root, "tests", SLUG, "sec.txt"))
    os.symlink(os.path.join("..", "..", "qa", "ledger"), os.path.join(root, "tests", SLUG, "ledgerlink"))
    os.symlink(os.path.join(outside, "ticket"), os.path.join(root, "tests", SLUG, "tickets", "2"))


class Fixture:
    """A temp workspace (<tmp>/ws) next to a folder outside it (<tmp>/outside)."""

    def __init__(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="qa-app-test-"))
        self.root = os.path.join(self.tmp, "ws")
        self.outside = os.path.join(self.tmp, "outside")
        os.makedirs(self.root)
        os.makedirs(self.outside)
        build_fixture(self.root, self.outside)

    def path(self, rel):
        return os.path.join(self.root, *rel.split("/"))

    def exists(self, rel):
        return os.path.lexists(self.path(rel))

    def read(self, rel):
        with open(self.path(rel), "rb") as f:
            return f.read()

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class Resp:
    def __init__(self, status, headers, body):
        self.status, self.headers, self.body = status, headers, body

    def header(self, name):
        return next((v for k, v in self.headers if k.lower() == name.lower()), None)

    def json(self):
        return json.loads(self.body.decode("utf-8"))

    @property
    def text(self):
        return self.body.decode("utf-8", "replace")


class Server:
    """serve.build_server(root, 0) running in a thread, with a raw request helper."""

    def __init__(self, root):
        self.srv = serve.build_server(root, 0)
        self.token, self.port = self.srv.qa_token, self.srv.qa_port
        self.thread = threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()

    @property
    def host(self):
        return f"127.0.0.1:{self.port}"

    def request(self, method, path, headers=None, body=None, host=True):
        """host=True sends the right Host header, a string sends that value, None sends none."""
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        try:
            c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            h = []
            if host is True:
                h.append(("Host", self.host))
            elif host is not None:
                h.append(("Host", host))
            h += list((headers or {}).items())
            if body is not None and not any(k.lower() in ("content-length", "transfer-encoding") for k, _ in h):
                h.append(("Content-Length", str(len(body))))
            for k, v in h:
                c.putheader(k, v)
            c.endheaders(body)
            r = c.getresponse()
            return Resp(r.status, r.getheaders(), r.read())
        finally:
            c.close()

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def write_headers(self, **over):
        h = {TOKEN_HEADER: self.token, "Origin": f"http://{self.host}", "Content-Type": "application/json"}
        for k, v in over.items():
            k = k.replace("_", "-")
            if v is None:
                h.pop(k, None)
            else:
                h[k] = v
        return h

    def send(self, method, path, obj, **over):
        body = obj if isinstance(obj, bytes) else json.dumps(obj).encode("utf-8")
        return self.request(method, path, headers=self.write_headers(**over), body=body)


def iter_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from iter_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from iter_strings(v)


def setUpModule():
    global _SNAPSHOT, _SECRETS_BEFORE
    _SNAPSHOT = snapshot(REAL_ROOT)
    _SECRETS_BEFORE = redact._SECRETS
    serve.Handler.log_message = lambda self, fmt, *args: LOG.append(fmt % args if args else fmt)
    AUDIT.update(tops=watched_tops(REAL_ROOT), writes=[], spawned=[], on=True)
    if not _HOOKED:  # an audit hook stays for the life of the process: added once
        sys.addaudithook(audit_hook)
        _HOOKED.append(True)


def tearDownModule():
    redact._SECRETS = _SECRETS_BEFORE
    AUDIT["on"] = False
    why = real_workspace_problem(_SNAPSHOT, snapshot(REAL_ROOT), REAL_ROOT, AUDIT)
    if why:
        raise AssertionError(why)


class FixtureCase(unittest.TestCase):
    """One fixture workspace and one server per class; the fake secret is installed in-process."""

    @classmethod
    def setUpClass(cls):
        redact._SECRETS = [SECRET]
        cls.fx = Fixture()
        cls.server = Server(cls.fx.root)

    @classmethod
    def tearDownClass(cls):
        cls.server.close()
        cls.fx.close()
        redact._SECRETS = _SECRETS_BEFORE

    def assertStatus(self, resp, status, what):
        self.assertEqual(resp.status, status, f"{what}: got {resp.status}")

    def assertRefused(self, resp, what, statuses=(400, 403)):
        self.assertIn(resp.status, statuses, f"{what}: got {resp.status}")

    def assertClean(self, data, where):
        """No planted raw value, no unmasked e-mail, JWT or share link in data (bytes or str)."""
        text = data.decode("utf-8", "replace") if isinstance(data, bytes) else data
        for label, raw in RAW.items():
            self.assertFalse(raw in text, f"{where}: raw {label} in the response")
        for label, rx in PATTERNS.items():
            self.assertIsNone(rx.search(text), f"{where}: unmasked {label} in the response")


# ---- request guards -----------------------------------------------------------------------------------------------

class GuardTests(FixtureCase):
    def test_host_guard(self):
        s = self.server
        self.assertStatus(s.get("/api/meta"), 200, "127.0.0.1:<port>")
        self.assertStatus(s.get("/api/meta", host=f"localhost:{s.port}"), 200, "localhost:<port>")
        for host in (f"127.0.0.1:{s.port + 1}", f"localhost:{s.port + 1}", "127.0.0.1", "localhost",
                     f"localhost:{s.port}.evil", f"evil.example:{s.port}", f"127.0.0.1:{s.port}@evil.example",
                     f"LOCALHOST:{s.port}", f"[::1]:{s.port}", ""):
            self.assertStatus(s.get("/api/meta", host=host), 403, f"Host {host!r}")
        self.assertStatus(s.get("/api/meta", host=None), 403, "no Host")
        self.assertStatus(s.get("/", host="evil.example"), 403, "Host on the page")
        self.assertStatus(s.get(f"/files/{QT_LOG}", host="evil.example"), 403, "Host on /files/")
        self.assertStatus(s.send("POST", "/api/tasks", {}, Origin="http://evil.example"), 403, "write, bad Origin")
        r = s.request("POST", "/api/tasks", headers=s.write_headers(), body=b"{}", host="evil.example")
        self.assertStatus(r, 403, "write with a foreign Host")

    def test_sec_fetch_site_guard(self):
        s = self.server
        for v in ("same-origin", "none"):
            self.assertStatus(s.get("/api/meta", headers={"Sec-Fetch-Site": v}), 200, f"Sec-Fetch-Site {v}")
        tasks_before = sorted(os.listdir(self.fx.path("qa/tasks")))
        for v in ("cross-site", "same-site", "bogus"):
            self.assertStatus(s.get("/api/meta", headers={"Sec-Fetch-Site": v}), 403, f"Sec-Fetch-Site {v}")
            self.assertStatus(s.get("/", headers={"Sec-Fetch-Site": v}), 403, f"page, Sec-Fetch-Site {v}")
            self.assertStatus(s.get(f"/files/{SHOT}", headers={"Sec-Fetch-Site": v}), 403, f"image, {v}")
            r = s.send("POST", "/api/tasks", {"text": "abc", "targets": ["TA5"]}, Sec_Fetch_Site=v)
            self.assertStatus(r, 403, f"write, Sec-Fetch-Site {v}")
        self.assertEqual(sorted(os.listdir(self.fx.path("qa/tasks"))), tasks_before)

    def test_page_token_and_headers(self):
        s = self.server
        r = s.get("/")
        self.assertStatus(r, 200, "/")
        m = re.search(r'<meta name="qa-token" content="([^"]+)">', r.text)
        self.assertIsNotNone(m, "token meta missing")
        self.assertEqual(m.group(1), s.token)
        self.assertIn("default-src 'self'", r.header("Content-Security-Policy") or "")
        self.assertIn("frame-ancestors 'none'", r.header("Content-Security-Policy") or "")
        for path in ("/", "/api/meta", f"/files/{QT_LOG}", "/static/app.js", "/api/nope"):
            r = s.get(path)
            self.assertEqual(r.header("X-Content-Type-Options"), "nosniff", path)
            self.assertEqual(r.header("X-Frame-Options"), "DENY", path)
            self.assertEqual(r.header("Cross-Origin-Resource-Policy"), "same-origin", path)
            self.assertEqual(r.header("Referrer-Policy"), "no-referrer", path)
            self.assertEqual(r.header("Cache-Control"), "no-store", path)
            self.assertFalse(any(k.lower().startswith("access-control-") for k, _ in r.headers), f"CORS on {path}")
        for path in ("/api/meta", "/api/live", "/api/tasks", "/api/results", "/api/cases", "/api/tickets"):
            self.assertFalse(s.token in s.get(path).text, f"token in {path}")
        self.assertStatus(s.get("/static/app.css"), 200, "app.css")
        for path in ("/static/../serve.py", "/static/serve.py", "/static/%2e%2e/serve.py", "/static/"):
            self.assertRefused(s.get(path), path, (400, 403, 404))

    def test_token_guard(self):
        s = self.server
        body = {"text": "a task", "targets": ["TA5"], "priority": "normal"}
        self.assertStatus(s.send("POST", "/api/tasks", body, X_QA_Token=None), 403, "no token")
        self.assertStatus(s.send("POST", "/api/tasks", body, X_QA_Token="x" * 43), 403, "wrong token")
        self.assertStatus(s.send("POST", "/api/tasks", body, X_QA_Token=s.token + "x"), 403, "token plus a char")
        self.assertStatus(s.send("POST", "/api/tasks", body, X_QA_Token=s.token.upper()), 403, "token in capitals")
        self.assertStatus(s.send("PUT", "/api/cases/PLAN-002", body, X_QA_Token=None), 403, "PUT without token")
        self.assertFalse(self.fx.exists("qa/cases/PLAN-002.c1.json"))

    def test_origin_guard(self):
        s = self.server
        body = {"text": "a task", "targets": ["TA5"]}
        for origin in (None, "null", "http://evil.example", f"http://127.0.0.1:{s.port + 1}",
                       f"https://127.0.0.1:{s.port}", f"http://localhost:{s.port}", f"http://127.0.0.1:{s.port}/"):
            self.assertStatus(s.send("POST", "/api/tasks", body, Origin=origin), 403, f"Origin {origin!r}")

    def test_content_type_and_body_guard(self):
        s = self.server
        body = {"text": "a task", "targets": ["TA5"]}
        for ctype in (None, "text/plain", "application/x-www-form-urlencoded", "multipart/form-data",
                      "application/jsonx"):
            self.assertStatus(s.send("POST", "/api/tasks", body, Content_Type=ctype), 415, f"Content-Type {ctype}")
        self.assertStatus(s.send("POST", "/api/tasks", body, Content_Type="application/json; charset=utf-8"), 201,
                          "JSON with a charset")
        big = json.dumps({"text": "x" * (64 * 1024)}).encode()
        self.assertStatus(s.send("POST", "/api/tasks", big), 413, "body over 64 KiB")
        self.assertStatus(s.send("POST", "/api/tasks", b"[1, 2]"), 400, "JSON array")
        self.assertStatus(s.send("POST", "/api/tasks", b"{not json"), 400, "broken JSON")
        self.assertStatus(s.send("POST", "/api/tasks", b'{"a": NaN}'), 400, "NaN")
        self.assertStatus(s.send("POST", "/api/tasks", b"\xff\xfe{}"), 400, "not UTF-8")
        r = s.request("POST", "/api/tasks", headers=s.write_headers())
        self.assertStatus(r, 411, "no Content-Length")
        r = s.request("POST", "/api/tasks", headers=dict(s.write_headers(), **{"Transfer-Encoding": "chunked"}),
                      body=b"2\r\n{}\r\n0\r\n\r\n")
        self.assertRefused(r, "chunked body", (400, 411))

    def test_methods_on_write_routes(self):
        s = self.server
        for method, path in (("GET", "/api/tickets/filed"), ("GET", f"/api/tasks/{TASK}/state"),
                             ("HEAD", "/api/tickets/filed"), ("DELETE", "/api/tasks"),
                             ("DELETE", f"/api/tasks/{TASK}"), ("DELETE", "/api/cases/PLAN-001"),
                             ("PATCH", "/api/cases/PLAN-001"), ("PUT", "/api/tasks"), ("POST", "/api/meta"),
                             ("POST", f"/files/{QT_LOG}"), ("OPTIONS", "/api/tasks"), ("TRACE", "/")):
            r = s.request(method, path, headers=s.write_headers(), body=b"{}")
            self.assertStatus(r, 405, f"{method} {path}")
            self.assertIsNotNone(r.header("Allow"), f"Allow header on {method} {path}")
        self.assertTrue(self.fx.exists("qa/cases/PLAN-001.json"))

    def test_error_messages_do_not_echo_the_body(self):
        s = self.server
        marker = "ECHO-MARKER-" + "7731"
        bodies = (
            ("POST", "/api/tasks", {"text": "a task", "targets": ["TA5"], "cases": [marker]}),
            ("POST", "/api/tasks", {"text": "a task", "targets": [marker]}),
            ("POST", "/api/tasks", {"text": "a task", "targets": ["TA5"], "priority": marker}),
            ("POST", "/api/tasks", {"text": "a task", "targets": ["TA5"], "clickup_url": f"https://{marker}"}),
            ("POST", "/api/tasks", {"text": marker * 400, "targets": ["TA5"]}),
            ("POST", f"/api/tasks/{TASK}/state", {"to": marker}),
            ("POST", "/api/cases", {"area": marker}),
            ("POST", "/api/cases", {"area": "PLAN", "title": "abc", "targets": marker,
                                    "requirement": {"text": "x", "source": {"type": marker, "ref": "r"}}}),
            ("POST", "/api/tickets/filed", {"dir": marker, "cl": marker}),
            ("POST", "/api/tickets/filed", {"dir": TICKET_DIR, "cl": marker}),
        )
        for method, path, body in bodies:
            r = s.send(method, path, body)
            self.assertRefused(r, f"{method} {path}", (400, 404, 409))
            self.assertFalse(marker in r.text, f"{method} {path} echoes the request body")
            self.assertIn("error", r.json())


# ---- /files/ ------------------------------------------------------------------------------------------------------

class FilesTests(FixtureCase):
    def test_traversal_is_refused(self):
        s = self.server
        for path in ("/files/../qa/secrets.env", "/files/%2e%2e/qa/secrets.env", "/files/tests/%2e%2e/qa/secrets.env",
                     "/files/tests/../qa/secrets.env", "/files/tests%2f..%2fqa%2fsecrets.env",
                     "/files/qa/runs/../secrets.env", "/files/tests/%2E%2E/%2E%2E/etc/passwd",
                     "/files//etc/passwd", "/files/%2fetc%2fpasswd", "/files/qa//secrets.env", "/files//qa/secrets.env",
                     "/files/tests\\..\\qa\\secrets.env", "/files/tests%5c..%5cqa%5csecrets.env",
                     f"/files/{QT_LOG}%00.png", f"/files/{QT_LOG}%00", "/files/qa/secrets.env%00.txt",
                     "/files/", "/files/%", "/files/%ff%fe", "/files/tests/" + "a/" * 600 + "x.txt"):
            r = s.get(path)
            self.assertRefused(r, path, (400, 403, 404))
            self.assertClean(r.body, path)

    def test_deny_list(self):
        s = self.server
        for rel in ("qa/secrets.env", "qa%2fsecrets.env", "qa/SECRETS.env", "qa/Secrets.Env", "qa/cache/clickup/CL-12345.json",
                    "qa/Cache/clickup/CL-12345.json", "qa/cache", "qa/ledger/2026-10-21.jsonl", "qa/LEDGER/2026-10-21.jsonl",
                    f"qa/runner/state/locks/{SERIAL}.json", f"tests/{SLUG}/.hidden.txt", f"tests/{SLUG}/notes.env",
                    f"tests/{SLUG}/build.apk", "QA/secrets.env", f"tests/{SLUG}/sec.txt",
                    f"tests/{SLUG}/ledgerlink/2026-10-21.jsonl", f"tests/{SLUG}/out.txt",
                    f"tests/{SLUG}/outdir/outside.txt", f"tests/{SLUG}/tickets/2/title.txt"):
            r = s.get("/files/" + rel)
            self.assertStatus(r, 403, rel)
            self.assertClean(r.body, rel)

    def test_images_only_as_evidence(self):
        s = self.server
        r = s.get(f"/files/{SHOT}")
        self.assertStatus(r, 200, "shot referenced by a run")
        self.assertEqual(r.header("Content-Type"), "image/png")
        self.assertEqual(r.body, PNG)
        self.assertStatus(s.get(f"/files/{TICKET_DIR}/pack/shot.png"), 200, "image in a ticket pack")
        self.assertStatus(s.get(f"/files/{BT_SHOT}"), 200, "image in a run's own evidence list")
        self.assertStatus(s.get(f"/files/{UNREF_SHOT}"), 403, "image no run references")
        self.assertStatus(s.get(f"/files/{SHOT.upper()}"), 403, "shot path in capitals")
        progress = self.fx.path(f"qa/runner/state/progress/{SERIAL}.json")
        self.assertStatus(s.get(f"/files/{LIVE_SHOT}"), 200, "last_shot of a run in progress")
        os.rename(progress, progress + ".off")
        try:
            self.assertStatus(s.get(f"/files/{LIVE_SHOT}"), 403, "last_shot once no progress file names it")
        finally:
            os.rename(progress + ".off", progress)
        os.makedirs(self.fx.path("qa/img"), exist_ok=True)
        put(self.fx.root, "qa/img/x.png", PNG)
        self.assertStatus(s.get("/files/qa/img/x.png"), 403, "image under qa/")

    def test_text_files_are_redacted(self):
        s = self.server
        for rel in (QT_LOG, f"{TICKET_DIR}/description.txt", f"{TICKET_DIR}/pack/notes.txt",
                    f"tests/{SLUG}/triage/TR-1/triage.json", f"qa/tasks/{TASK}.json", f"qa/runs/{SLUG}/{RUN}.json",
                    f"qa/runner/state/progress/{SERIAL}.json"):
            r = s.get("/files/" + rel)
            self.assertStatus(r, 200, rel)
            self.assertClean(r.body, rel)
            self.assertIn(redact.MASK, r.text, rel)
        self.assertStatus(s.get(f"/files/tests/{SLUG}/TA5/logs/missing.txt"), 404, "missing file")
        put(self.fx.root, f"tests/{SLUG}/data.bin", b"\x00\x01")
        self.assertStatus(s.get(f"/files/tests/{SLUG}/data.bin"), 404, "extension neither text nor image")
        self.assertStatus(s.get(f"/files/tests/{SLUG}/TA5"), 404, "a folder")

    def test_utf16_and_utf32_files_are_redacted(self):
        s = self.server
        text = f"09:00:01 D qt: login as {PLANT}\n"
        for name, raw in (("u16le.txt", text.encode("utf-16-le")), ("u16be.csv", text.encode("utf-16-be")),
                          ("u32le.log", text.encode("utf-32-le")), ("u32be.xml", text.encode("utf-32-be"))):
            bom = {"u16le": codecs.BOM_UTF16_LE, "u16be": codecs.BOM_UTF16_BE, "u32le": codecs.BOM_UTF32_LE,
                   "u32be": codecs.BOM_UTF32_BE}[name.split(".")[0]]
            rel = f"tests/{SLUG}/enc/{name}"
            put(self.fx.root, rel, bom + raw)
            r = s.get("/files/" + rel)
            self.assertStatus(r, 200, rel)
            self.assertFalse(b"\x00" in r.body, f"{rel}: NUL in the response")
            self.assertClean(r.body, rel)
            self.assertTrue(redact.MASK in r.text, f"{rel}: nothing masked")
        rel = f"tests/{SLUG}/enc/nobom.txt"
        put(self.fx.root, rel, text.encode("utf-16-le"))
        r = s.get("/files/" + rel)
        self.assertStatus(r, 415, "NUL bytes without a BOM")
        self.assertClean(r.body.replace(b"\x00", b""), rel)

    def test_a_value_at_the_cut_is_masked_whole(self):
        s = self.server
        cap = security.TEXT_CAP

        def straddle(value, before, tail):
            """One line of filler with value starting `before` characters ahead of the 2 MiB cut."""
            head = ("ab " * (cap // 3 + 2))[:cap - before - 1] + " "
            return head + value + " " + "cd " * tail

        jwt_before = len(JWT) - len(JWT.rsplit(".", 1)[1]) + 5  # header, payload and 5 signature characters
        for name, value, before, tail in (("secret.txt", SECRET, 14, 40000), ("secret_short.txt", SECRET, 14, 100),
                                          ("email.txt", EMAIL, 20, 40000), ("jwt.txt", JWT, jwt_before, 40000)):
            rel = f"tests/{SLUG}/long/{name}"
            put(self.fx.root, rel, straddle(value, before, tail))
            r = s.get("/files/" + rel)
            self.assertStatus(r, 200, rel)
            self.assertTrue("[truncated by the QA app" in r.text, f"{rel}: no truncation note")
            self.assertClean(r.body, rel)
            self.assertFalse(JWT.split(".")[1] in r.text, f"{rel}: JWT payload in the response")
            self.assertFalse(value[:before] in r.text, f"{rel}: the part of the value before the cut in the response")

    def test_hard_links_are_refused(self):
        s = self.server
        links = {"hl_ledger.jsonl": "qa/ledger/2026-10-21.jsonl", "hl_cache.json": "qa/cache/clickup/CL-12345.json",
                 "hl_lock.json": f"qa/runner/state/locks/{SERIAL}.json", "hl_log.txt": QT_LOG}
        made = []
        try:
            for name, target in links.items():
                os.link(self.fx.path(target), self.fx.path(f"tests/{SLUG}/{name}"))
                made.append(self.fx.path(f"tests/{SLUG}/{name}"))
            for name in links:
                r = s.get(f"/files/tests/{SLUG}/{name}")
                self.assertStatus(r, 403, f"hard link {name}")
                self.assertClean(r.body, name)
        finally:
            for p in made:
                os.unlink(p)
        self.assertStatus(s.get(f"/files/{QT_LOG}"), 200, "the log once its second link is gone")


# ---- redaction of every read ---------------------------------------------------------------------------------------

class RedactionTests(FixtureCase):
    ENDPOINTS = ("/", "/static/index.html", "/api/meta", "/api/live", "/api/tasks", f"/api/tasks/{TASK}",
                 "/api/results", "/api/results?case=PLAN-001", f"/api/results?task={TASK}", f"/api/runs/{RUN}",
                 f"/api/runs/{RUN_HB}", f"/api/runs/{RUN_BT}", "/api/history?case=PLAN-001&target=TA5", "/api/tickets", "/api/cases",
                 "/api/cases/PLAN-001", "/api/cases/PLAN-002", "/api/cases/SAK-005")

    def test_planted_values_reach_the_fixture(self):
        # guard against a vacuous crawl: the raw values are in the files the app reads
        for rel in ("qa/cases/PLAN-001.json", f"qa/runs/{SLUG}/{RUN}.json", f"{TICKET_DIR}/title.txt",
                    f"{TICKET_DIR}/description.txt", f"{TICKET_DIR}/comment_CL-12345.md",
                    f"tests/{SLUG}/triage/TR-1/triage.json", f"qa/tasks/{TASK}.json",
                    f"qa/runner/state/progress/{SERIAL}.json", QT_LOG):
            self.assertIn(EMAIL.encode(), self.fx.read(rel), rel)

    def test_every_get_is_redacted(self):
        s = self.server
        seen, files = {}, set()
        for path in self.ENDPOINTS:
            r = s.get(path)
            self.assertStatus(r, 200, path)
            self.assertClean(r.body, path)
            seen[path] = r
            if path.startswith("/api/"):
                for v in iter_strings(r.json()):
                    if v.startswith("/files/"):
                        files.add(v)
                    elif v.startswith(("qa/", "tests/")) and "\n" not in v and len(v) < 400:
                        files.add("/files/" + urllib.parse.quote(v, safe="/"))
        self.assertIn(f"/files/{QT_LOG}", files)
        self.assertIn(f"/files/{SHOT}", files)
        served = 0
        for url in sorted(files):
            r = s.get(url)
            self.assertNotEqual(r.status, 500, url)
            if r.status == 200:
                served += 1
                self.assertClean(r.body, url)
        self.assertGreaterEqual(served, 8, "too few /files/ URLs were served to prove anything")
        self.assertFalse(any("500" in line for line in LOG), "a request ended in 500")

    def test_planted_records_are_shown(self):
        s = self.server
        live = s.get("/api/live").json()
        self.assertEqual(len(live["progress"]), 1)
        self.assertNotIn("pending", live)
        self.assertEqual(len(live["locks"]), 1)
        self.assertEqual(len(live["handovers"]), 1)
        self.assertEqual([h["run"] for h in live["handbacks"]], [RUN_HB])
        self.assertIn(redact.MASK, live["progress"][0]["step"]["text"])
        tickets = s.get("/api/tickets").json()
        self.assertEqual([d["dir"] for d in tickets["drafts"]], [TICKET_DIR])
        draft = tickets["drafts"][0]
        self.assertEqual(len(draft["comments"]), 1)
        self.assertEqual({p["rel"].rsplit("/", 1)[1] for p in draft["pack"]}, {"shot.png", "notes.txt"})
        self.assertTrue(all(p["url"] for p in draft["pack"]))
        self.assertEqual(len(tickets["triage"]), 1)
        self.assertEqual(len(tickets["legacy"]), 1)
        run = s.get(f"/api/runs/{RUN}").json()
        self.assertEqual(run["case_title"], "Plan opens")
        self.assertEqual(run["prediction"]["path"], f"qa/runs/{SLUG}/pred/PR-{RUN}.json")
        self.assertEqual(run["differential"]["id"], DIFF)
        self.assertTrue(any(k.startswith("key ") and "[EMAIL]" in k for k in run["values"]), "keys are redacted")
        task = s.get(f"/api/tasks/{TASK}").json()
        self.assertEqual([r["run"] for r in task["runs"]], [RUN, "R-20261021-TA5-PLAN-001-09"])
        self.assertTrue(task["runs"][1].get("missing"))
        bt = s.get("/api/results").json()["backtest"]
        self.assertEqual((bt["runs"], bt["current"], bt["superseded"], bt["verdicts"]), (2, 1, 1, {"PARTIAL": 1}))
        rows = s.get("/api/results?case=PLAN-003").json()["cases"][0]["rows"]
        self.assertEqual({r["run"]: r["superseded_by"] for r in rows}, {RUN_BT: RUN_BT + "c1", RUN_BT + "c1": None})
        self.assertTrue(all(r["thumbs"] == [f"/files/{BT_SHOT}"] for r in rows))
        hist = s.get("/api/history?case=PLAN-003&target=TA5").json()["rows"]
        self.assertEqual([r["run"] for r in hist], [RUN_BT + "c1"], "history leaves superseded runs out")
        put(self.fx.root, "qa/tasks/T-20261020-002.json", dict(json.load(open(self.fx.path(f"qa/tasks/{TASK}.json"))),
                                                              id="T-20261020-002", runs=[RUN_BT]))
        [t2] = [t for t in s.get("/api/tasks").json()["tasks"] if t["id"] == "T-20261020-002"]
        self.assertEqual(({k: t2["run_summary"][0].get(k) for k in ("run", "corrects")}, t2["runs"]),
                         ({"run": RUN_BT + "c1", "corrects": RUN_BT}, [RUN_BT]), "the greenlight card shows the correction")
        meta = s.get("/api/meta").json()
        self.assertEqual([t["tag"] for t in meta["targets"]], ["TA5", "W044"])
        self.assertEqual(meta["ledger"]["seq"], 5)


class RedactModuleTests(unittest.TestCase):
    """redact.py itself: the secrets cache follows qa/secrets.env, and the e-mail pattern stays linear."""

    def setUp(self):
        self.saved = (redact.SECRETS_FILE, redact._SECRETS, getattr(redact, "RECHECK_S", 1.0))
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="qa-app-redact-"))
        redact.SECRETS_FILE, redact._SECRETS, redact.RECHECK_S = os.path.join(self.tmp, "secrets.env"), None, 0

    def tearDown(self):
        redact.SECRETS_FILE, redact._SECRETS, redact.RECHECK_S = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_secret(self, value):
        with open(redact.SECRETS_FILE, "w", encoding="utf-8") as f:
            f.write(f"QA_TEST_PASSWORD={value}\n")

    def test_secrets_follow_the_file(self):
        old, new, late = "Old" + "Passw0rd-111", "New" + "Passw0rd-2222", "Late" + "Passw0rd-33"
        self.write_secret(old)
        self.assertEqual(redact.redact(f"pw {old}"), "pw " + redact.MASK)
        self.write_secret(new)
        self.assertEqual(redact.redact(f"10:30 D login ok pw {new}"), "10:30 D login ok pw " + redact.MASK,
                         "a rotated value is masked without a restart")
        self.assertTrue(redact.contains_secret(f"pw {new}"))
        os.unlink(redact.SECRETS_FILE)
        redact._SECRETS = None
        self.assertEqual(redact.redact(f"pw {late}"), f"pw {late}")
        self.write_secret(late)
        self.assertEqual(redact.redact(f"pw {late}"), "pw " + redact.MASK, "a file created later is read")
        redact._SECRETS = ["Pinned" + "Value-9"]
        self.assertEqual(redact.redact("x Pinned" + "Value-9"), "x " + redact.MASK, "a list set by hand stays in use")

    def test_email_pattern_is_linear(self):
        import draft_check
        self.assertEqual(draft_check.EMAIL, redact._PATTERNS[5][0].pattern, "draft_check keeps redact's e-mail pattern")
        self.assertEqual(redact.redact(f"mail {EMAIL} end"), "mail [EMAIL] end")
        for run in ("0123456789abcdef" * 6250, "x" + "@" + "1" * 100000):
            t0 = time.monotonic()
            redact.redact(f"dump {run} end")
            self.assertLess(time.monotonic() - t0, 3.0, f"redact of a {len(run)}-character run without a match")


# ---- write scope --------------------------------------------------------------------------------------------------

class WriteScopeTests(FixtureCase):
    def writer(self):
        return writes.Writer(self.fx.root)

    def assertWriteError(self, status, fn, *args):
        with self.assertRaises(writes.WriteError) as cm:
            fn(*args)
        self.assertEqual(cm.exception.status, status, f"{fn.__name__}{args[:1]}: {cm.exception.message}")

    def test_writer_scope(self):
        w = self.writer()
        before = snapshot(self.fx.root)
        data = b"{}\n"
        for rel, mode in (("qa/ledger/2026-10-21.jsonl", "replace"), ("qa/ledger/2026-10-22.jsonl", "create"),
                          ("qa/secrets.env", "replace"), ("qa/cases/PLAN-001.json", "create"),
                          ("qa/cases/PLAN-002.c1.json", "create"), ("qa/tasks/T-20261021-009.json", "replace"),
                          (f"qa/runs/{SLUG}/{RUN}.json", "create"), ("qa/app/serve.py", "replace"),
                          (f"{TICKET_DIR}/title.txt", "replace"), ("qa/cases/PLAN-001.json.tmp", "create"),
                          ("qa/cases/plan-009.json", "create"), ("qa/tasks/T-2026-001.json", "replace"),
                          ("qa/tasks/../../x.json", "replace"), ("../outside/x.json", "replace"),
                          (os.path.join(self.fx.outside, "x.json"), "replace"), ("/etc/x.json", "replace"),
                          ("qa/cases/PLAN-009.json", "replace"), ("qa/tasks/T-20261021-010.json", "create"),
                          (f"tests/{SLUG}/tickets/2/filed.json", "replace"),
                          (f"tests/{SLUG}/tickets/3/filed.json", "replace"), ("", "replace"),
                          ("qa/tasks/T-20261021-010.json\x00", "replace")):
            with self.assertRaises(writes.WriteError, msg=f"{rel!r} {mode}") as cm:
                w._write(rel, data, mode)
            self.assertIn(cm.exception.status, (403, 404), f"{rel!r}: {cm.exception.message}")
        self.assertWriteError(409, w._write, "qa/cases/PLAN-003.json", data, "create")
        self.assertEqual(snapshot_diff(before, snapshot(self.fx.root))["changed"], [])
        added = snapshot_diff(before, snapshot(self.fx.root))["added"]
        self.assertTrue(set(added) <= {"qa/tasks/.lock"}, added)
        self.assertEqual(os.listdir(os.path.join(self.fx.outside, "ticket")), ["title.txt"])
        self.assertFalse(os.path.exists(os.path.join(self.fx.outside, "x.json")))

    def test_id_is_never_reused(self):
        w = self.writer()
        self.assertEqual(w.next_case_id("PLAN"), "PLAN-008")  # PLAN-007 exists only in the ledger
        self.assertEqual(w.next_case_id("SAK"), "SAK-006")  # SAK-005 lives in the harness folder
        self.assertEqual(w.next_case_id("MAP"), "MAP-001")

    def test_symlinked_parent_is_refused(self):
        w = self.writer()
        self.assertWriteError(403, w.mark_filed, {"dir": f"tests/{SLUG}/tickets/2", "cl": "CL-12345"})
        self.assertEqual(os.listdir(os.path.join(self.fx.outside, "ticket")), ["title.txt"])
        other = Fixture()
        try:
            os.makedirs(os.path.join(other.outside, "tasks"))
            shutil.rmtree(other.path("qa/tasks"))
            os.symlink(os.path.join(other.outside, "tasks"), other.path("qa/tasks"))
            ow = writes.Writer(other.root)
            self.assertWriteError(403, ow.create_task, {"text": "a task", "targets": ["TA5"]})
            self.assertEqual(os.listdir(os.path.join(other.outside, "tasks")), [])
        finally:
            other.close()

    def test_http_write_scope(self):
        s = self.server
        before = snapshot(self.fx.root)
        edit = {"title": "Plan saves again", "requirement": {"text": "The plan saves.",
                                                             "source": {"type": "user_chat", "ref": "chat"}},
                "targets": "tablet", "preconditions": [], "steps": ["Save the plan"], "expected": "Saved",
                "covered_paths": [], "status": "draft", "base": "qa/cases/PLAN-002.json"}
        self.assertStatus(s.send("PUT", "/api/cases/PLAN-002", edit), 403, "edit whose next file is in the ledger")
        self.assertStatus(s.send("PUT", "/api/cases/SAK-005", edit), 403, "edit of a harness case")
        self.assertStatus(s.send("PUT", "/api/cases/PLAN-077", edit), 404, "edit of a missing case")
        self.assertStatus(s.send("POST", "/api/tickets/filed", {"dir": f"tests/{SLUG}/tickets/2", "cl": "CL-12345"}),
                          403, "filed marker in a symlinked folder")
        for d in ("tests/../qa/ledger", f"tests/{SLUG}/tickets/1/../../../../qa", "qa/cases", f"tests/{SLUG}/tickets/9",
                  f"/{TICKET_DIR}", f"{TICKET_DIR}/pack"):
            r = s.send("POST", "/api/tickets/filed", {"dir": d, "cl": "CL-12345"})
            self.assertRefused(r, f"filed in {d}", (400, 403, 404))
        self.assertStatus(s.send("POST", "/api/tasks/T-20261021-009/state", {"to": "closed"}), 404, "missing task")
        d = snapshot_diff(before, snapshot(self.fx.root))
        self.assertEqual(d["changed"], [])
        self.assertTrue(set(d["added"]) <= {"qa/tasks/.lock"}, d["added"])


# ---- write flow ---------------------------------------------------------------------------------------------------

class WriteFlowTests(FixtureCase):
    def cli(self, *args):
        """(exit code, parsed stdout) of tasks.py against the fixture root."""
        p = subprocess.run([sys.executable, "-I", "-B", TASKS_PY] + list(args), capture_output=True, text=True,
                           timeout=60, stdin=subprocess.DEVNULL)
        try:
            out = json.loads(p.stdout) if p.stdout.strip() else None
        except ValueError:
            out = None
        return p.returncode, out

    def state(self, task_id, to, note=None):
        body = {"to": to} if note is None else {"to": to, "note": note}
        return self.server.send("POST", f"/api/tasks/{task_id}/state", body)

    def test_task_flow(self):
        s, root = self.server, self.fx.root
        r = s.send("POST", "/api/tasks", {"text": f"Check plan export, ask {EMAIL}", "targets": ["TA5", "W044"],
                                          "priority": "high", "cases": ["PLAN-001"],
                                          "clickup_url": "https://app.clickup.com/t/86c1abcde?x=1#c"})
        self.assertStatus(r, 201, "create task")
        task = r.json()
        tid = task["id"]
        self.assertRegex(tid, rf"^T-{TODAY}-\d{{3}}$")
        self.assertEqual(task["state"], "queued")
        self.assertEqual(task["clickup"], {"url": "https://app.clickup.com/t/86c1abcde", "task_id": "86c1abcde",
                                           "cl": None})
        stored = self.fx.read(f"qa/tasks/{tid}.json")
        self.assertNotIn(EMAIL.encode(), stored)
        self.assertEqual(stored.decode(), records.canonical(json.loads(stored)))
        listed = s.get("/api/tasks").json()["tasks"]
        self.assertEqual(listed[0]["id"], tid, "newest first")
        self.assertIn(TASK, [t["id"] for t in listed])

        for to in ("running", "results_ready", "greenlit", "drafted", "queued"):
            self.assertStatus(self.state(tid, to), 409, f"user queued -> {to}")
        self.assertStatus(self.state(tid, "bogus"), 400, "unknown state")

        code, out = self.cli("--root", root, "next")
        self.assertEqual((code, out["id"]), (0, tid), "next is the high-priority queued task")
        code, out = self.cli("add-run", tid, RUN, "--root", root)
        self.assertEqual((code, out["status"]), (1, 409), "no runs while queued")
        code, out = self.cli("set", tid, "running", "--by", "claude", "--root", root)
        self.assertEqual((code, out["state"]), (0, "running"))
        code, out = self.cli("--root", root, "next")
        self.assertEqual((code, out), (3, None), "nothing queued")
        self.assertEqual(self.cli("add-run", tid, RUN, "--root", root)[0], 0)
        self.assertEqual(self.cli("add-run", tid, "R-bad", "--root", root)[1]["status"], 400)
        code, out = self.cli("set-campaign", tid, SLUG, "--root", root)
        self.assertEqual((code, out["campaign"], out["runs"]), (0, SLUG, [RUN]))
        self.assertEqual(self.cli("set", tid, "queued", "--by", "claude", "--root", root)[1]["status"], 409,
                         "back to queued after a run")
        self.assertStatus(self.state(tid, "results_ready"), 409, "user running -> results_ready")
        code, out = self.cli("set", tid, "results_ready", "--by", "claude", "--note", "3 runs", "--root", root)
        self.assertEqual((code, out["state"]), (0, "results_ready"))
        code, out = self.cli("set", tid, "greenlit", "--by", "claude", "--root", root)
        self.assertEqual((code, out["status"]), (1, 400), "claude greenlight without the user's words")
        self.assertEqual(self.cli("add-output", tid, "summary", f"{TICKET_DIR}/title.txt", "--root", root)[1]["status"],
                         409, "outputs only after the greenlight")

        r = self.state(tid, "greenlit", note="looks right")
        self.assertStatus(r, 200, "user greenlight")
        last = r.json()["history"][-1]
        self.assertEqual((last["by"], last["via"], last["from"], last["to"]), ("user", "app", "results_ready", "greenlit"))
        self.assertStatus(self.state(tid, "drafted"), 409, "user greenlit -> drafted")
        code, out = self.cli("set", tid, "drafted", "--by", "claude", "--root", root)
        self.assertEqual((code, out["state"]), (0, "drafted"))
        self.assertEqual(self.cli("add-output", tid, "tickets", TICKET_DIR, "--root", root)[0], 0)
        self.assertStatus(self.state(tid, "closed"), 200, "user closes")
        self.assertStatus(self.state(tid, "queued"), 409, "closed stays closed")
        code, out = self.cli("show", tid, "--root", root)
        self.assertEqual(code, 0)
        self.assertEqual([h["to"] for h in out["history"]],
                         ["queued", "running", "results_ready", "greenlit", "drafted", "closed"])
        self.assertEqual([h["by"] for h in out["history"]], ["user", "claude", "claude", "user", "claude", "user"])
        self.assertEqual(out["outputs"]["tickets"], [TICKET_DIR])
        detail = s.get(f"/api/tasks/{tid}").json()
        self.assertEqual([x["run"] for x in detail["runs"]], [RUN])
        self.assertEqual(detail["runs"][0]["verdict"], "FAIL")
        code, out = self.cli("list", "--state", "closed", "--root", root)
        self.assertIn(tid, [t["id"] for t in out])
        self.assertTrue(all(t["state"] == "closed" for t in out))

    def test_cancel_queued_task(self):
        r = self.server.send("POST", "/api/tasks", {"text": "cancel me", "targets": ["W044"], "priority": "low"})
        self.assertStatus(r, 201, "create task")
        self.assertStatus(self.state(r.json()["id"], "closed"), 200, "user cancels a queued task")

    def test_task_input_checks(self):
        s = self.server
        for body in ({"text": "ab", "targets": ["TA5"]}, {"text": "a task", "targets": []},
                     {"text": "a task", "targets": ["TA5", "TA5"]}, {"text": "a task", "targets": ["ET99"]},
                     {"text": "a task", "targets": "TA5"}, {"text": "a task", "targets": ["TA5"], "priority": "urgent"},
                     {"text": "a task", "targets": ["TA5"], "cases": ["PLAN-404"]},
                     {"text": "a task", "targets": ["TA5"], "clickup_url": "https://evil.example/t/abc"},
                     {"text": "x" * 4001, "targets": ["TA5"]}):
            self.assertStatus(s.send("POST", "/api/tasks", body), 400, json.dumps(body)[:60])

    def test_case_flow(self):
        s = self.server
        body = {"area": "PLAN", "title": "Plan imports KML",
                "requirement": {"text": f"KML import works.\nReported by {EMAIL}",
                                "source": {"type": "clickup_task", "ref": "86c1abcde", "locator": "description"}},
                "targets": "both", "preconditions": ["Logged in with the CT account", "A KML file on the device"],
                "steps": ["Open Flight planning", "Import the KML"], "expected": "The area shows on the map",
                "covered_paths": ["apps/src/pages/FlightPlanning/**"], "needs": {"link": "none", "flags": ["Kml"]}}
        r = s.send("POST", "/api/cases", body)
        self.assertStatus(r, 201, "create case")
        cid, rel = r.json()["id"], r.json()["file"]
        self.assertEqual(cid, "PLAN-008")
        c0 = json.loads(self.fx.read(rel))
        self.assertEqual(case_schema.validate_case(c0), [])
        self.assertEqual((c0["status"], c0["author"], c0["routing"]), ("draft", "user", []))
        self.assertEqual([st["helper"] for st in c0["steps"]], [None, None])
        self.assertNotIn(EMAIL, c0["requirement"]["text"])
        self.assertEqual(c0["requirement"]["sha256"], case_schema.requirement_sha256(c0["requirement"]["text"]))
        listed = {c["id"]: c for c in s.get("/api/cases").json()["cases"]}
        self.assertEqual((listed[cid]["status"], listed[cid]["versions"]), ("draft", 1))

        c0_bytes = self.fx.read(rel)
        body["title"] = "Plan imports KML files"
        body["steps"] = ["Open Flight planning", "Import the KML", "Zoom to the area"]
        body["base"] = rel
        r = s.send("PUT", f"/api/cases/{cid}", body)
        self.assertStatus(r, 201, "edit case")
        self.assertEqual(r.json(), {"id": cid, "file": f"qa/cases/{cid}.c1.json"})
        self.assertEqual(self.fx.read(rel), c0_bytes, "the first version is never rewritten")
        c1 = json.loads(self.fx.read(f"qa/cases/{cid}.c1.json"))
        self.assertEqual(case_schema.validate_case(c1), [])
        self.assertEqual(c1["supersedes"]["file"], rel)
        self.assertEqual(c1["supersedes"]["file_sha256"], records.sha256_bytes(c0_bytes))
        self.assertEqual(c1["status"], "draft")
        detail = s.get(f"/api/cases/{cid}").json()
        self.assertEqual(detail["latest_file"], f"qa/cases/{cid}.c1.json")
        self.assertEqual(detail["case"]["title"], "Plan imports KML files")
        self.assertEqual(len(detail["versions"]), 2)
        body["base"] = detail["latest_file"]
        self.assertStatus(s.send("PUT", f"/api/cases/{cid}", body), 409, "an edit that changes nothing")
        self.assertStatus(s.send("PUT", f"/api/cases/{cid}", dict(body, area="MAP")), 400, "area change")
        self.assertStatus(s.send("PUT", f"/api/cases/{cid}", dict(body, status="active", title="T2 x")), 400,
                          "the app cannot activate a draft")
        r = s.send("PUT", f"/api/cases/{cid}", dict(body, status="retired", title="Plan imports KML (old)"))
        self.assertStatus(r, 201, "retire")
        self.assertEqual(r.json()["file"], f"qa/cases/{cid}.c2.json")
        self.assertStatus(s.send("POST", "/api/cases", dict(body, area="XYZ")), 400, "unknown area")
        self.assertStatus(s.send("POST", "/api/cases", dict(body, status="active")), 400, "new case not a draft")
        self.assertStatus(s.send("POST", "/api/cases", dict(body, covered_paths=["../etc/*"])), 400, "bad glob")
        r = s.send("POST", "/api/cases", dict(body, title="Plan imports GeoJSON"))
        self.assertEqual(r.json()["id"], "PLAN-009")

    def test_edit_keeps_engine_fields(self):
        s = self.server
        shown = s.get("/api/cases/PLAN-001").json()  # what the Edit form loads: redacted, plus latest_file
        body = {"title": "Plan opens fast", "requirement": {"text": shown["case"]["requirement"]["text"],
                                                            "source": {"type": "user_chat", "ref": "chat"}},
                "targets": "tablet", "preconditions": ["Logged in with the CT account"], "steps": ["Open the plan"],
                "expected": "The plan opens", "covered_paths": ["apps/src/pages/FlightPlanning/**"],
                "base": shown["latest_file"]}
        r = s.send("PUT", "/api/cases/PLAN-001", body)
        self.assertStatus(r, 201, "edit an active case without changing its steps")
        c1 = json.loads(self.fx.read(r.json()["file"]))
        self.assertEqual(c1["status"], "active")
        self.assertEqual(c1["steps"][0]["helper"], "tools/act.py {target} tap testid:open")
        self.assertEqual(c1["preconditions"][0]["id"], "P1")
        # the form sends needs only when link or flags changed; without it the engine's needs stay as they were
        self.assertEqual(c1["needs"], {"link": "mocklink", "usb_port": False, "account_lock": True,
                                       "flags": ["Mocklink"], "drone_hw": 4})
        self.assertEqual(c1["flags_watch"], ["DevReporting"])
        r = s.send("PUT", "/api/cases/PLAN-001", dict(body, steps=["Open the plan twice"], base=r.json()["file"]))
        self.assertStatus(r, 400, "a reworded step of an active case needs status draft")

    def test_mark_filed(self):
        s = self.server
        r = s.send("POST", "/api/tickets/filed", {"dir": TICKET_DIR, "cl": "CL-12345"})
        self.assertStatus(r, 201, "mark filed")
        marker = r.json()
        self.assertEqual((marker["cl"], marker["by"], marker["via"]), ("CL-12345", "user", "app"))
        self.assertEqual(marker["run_record"], f"qa/runs/{SLUG}/{RUN}.json")
        self.assertEqual(marker["history"], [])
        self.assertEqual(json.loads(self.fx.read(f"{TICKET_DIR}/filed.json"))["cl"], "CL-12345")
        self.assertStatus(s.send("POST", "/api/tickets/filed", {"dir": TICKET_DIR, "cl": "CL-12346"}), 409,
                          "another CL without replace")
        r = s.send("POST", "/api/tickets/filed", {"dir": TICKET_DIR, "cl": "CL-12346", "replace": True})
        self.assertStatus(r, 201, "replace")
        self.assertEqual([h["cl"] for h in r.json()["history"]], ["CL-12345"])
        self.assertStatus(s.send("POST", "/api/tickets/filed", {"dir": TICKET_DIR, "cl": "CL-1"}), 400, "bad CL")
        drafts = s.get("/api/tickets").json()["drafts"]
        self.assertEqual(drafts[0]["filed"]["cl"], "CL-12346")
        self.assertFalse(self.fx.exists(f"{TICKET_DIR}/filed.json.tmp"))


class CaseRuleTests(FixtureCase):
    """Case validation and the PUT /api/cases rules: base version, active cases, refused globs and flags, source."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        text = "Sites can be renamed."
        put(cls.fx.root, "qa/cases/PLAN-005.json", case_record(
            "PLAN-005", "draft", "Plan renames", text,
            requirement={"text": text, "sha256": case_schema.requirement_sha256(text),
                         "source": {"type": "clickup_task", "ref": "86c1abcde", "locator": "description bullet 7",
                                    "requester": "Some One", "cache": "qa/cache/clickup/CL-12345.json",
                                    "fetched": "2026-10-21T08:00:00.000Z", "custom_id": "CL-12345"}}))
        helper = "tools/act.py {target} tap testid:x"
        put(cls.fx.root, "qa/cases/PLAN-006.json", case_record(
            "PLAN-006", "active", "Plan export", "Plans export.",
            steps=[{"n": 1, "text": "Export the plan", "helper": helper, "gates": []},
                   {"n": 2, "text": "Read the export list", "helper": helper, "gates": [], "save_as": "exports",
                    "assert": {"kind": "scripted", "expr": "v['exports']"}}]))

    def form_body(self, cid):
        """The PUT body the Edit form builds from GET /api/cases/<id>, unchanged."""
        d = self.server.get(f"/api/cases/{cid}").json()
        c = d["case"]
        src = {k: v for k, v in c["requirement"]["source"].items() if k in ("type", "ref", "locator", "requester")}
        return {"title": c["title"], "requirement": {"text": c["requirement"]["text"], "source": src},
                "targets": c["targets"], "preconditions": [p["text"] for p in c["preconditions"]],
                "steps": [st["text"] for st in c["steps"]], "expected": c.get("expected") or "x",
                "covered_paths": c["covered_paths"], "base": d["latest_file"]}

    def case_files(self):
        return sorted(os.listdir(self.fx.path("qa/cases")))

    def test_g3_gates_and_active_steps(self):
        rec = case_record("PLAN-001", "draft", "Plan opens", "The plan opens.", steps=[
            {"n": 1, "text": "Import the dataset", "helper": None, "gates": ["write_list", "cloud_write"]},
            {"n": 2, "text": "Start the OTA", "helper": None, "gates": ["mocklink_ota"]}])
        self.assertEqual(case_schema.validate_case(rec), [])
        for status in ("active", "draft", "retired"):
            problems = case_schema.validate_case(dict(rec, status=status, steps=[]))
            self.assertEqual("steps: an active case needs at least one step" in problems, status == "active", status)

    def test_active_case_keeps_its_run_fields(self):
        s = self.server
        before = self.case_files()
        body = self.form_body("PLAN-006")
        steps = body["steps"]
        for what, change in (("the assert step removed", {"steps": steps[:1]}), ("no steps", {"steps": []}),
                             ("steps reordered", {"steps": steps[::-1]}), ("targets", {"targets": "both"}),
                             ("expected", {"expected": "The plan exports fast"}),
                             ("requirement", {"requirement": dict(body["requirement"], text="Plans export fast.")}),
                             ("needs", {"needs": {"link": "mocklink", "flags": []}})):
            r = s.send("PUT", "/api/cases/PLAN-006", dict(body, title="Plan exports again", **change))
            self.assertStatus(r, 400, f"active case, {what}")
            self.assertIn("status draft", r.json()["error"], what)
        self.assertEqual(self.case_files(), before, "nothing was written")
        r = s.send("PUT", "/api/cases/PLAN-006", dict(body, targets="both", steps=steps[:1], status="draft"))
        self.assertStatus(r, 201, "the same edit saved as a draft")
        self.assertEqual(json.loads(self.fx.read(r.json()["file"]))["status"], "draft")
        put(self.fx.root, "qa/cases/PLAN-004.json", dict(
            case_record("PLAN-004", "draft", "Plan stepless", "Back-test case."), status="active", steps=[]))
        body = dict(self.form_body("PLAN-004"), steps=[], title="Plan stepless renamed")
        self.assertStatus(s.send("PUT", "/api/cases/PLAN-004", body), 400, "an active case without steps")
        self.assertFalse(self.fx.exists("qa/cases/PLAN-004.c1.json"))

    def test_title_edit_keeps_an_active_case_active(self):
        r = self.server.send("PUT", "/api/cases/PLAN-001", dict(self.form_body("PLAN-001"), title="Plan opens again"))
        self.assertStatus(r, 201, "title only")
        c1 = json.loads(self.fx.read(r.json()["file"]))
        self.assertEqual((c1["status"], c1["steps"][0]["helper"]), ("active", "tools/act.py {target} tap testid:open"))

    def test_edit_needs_the_version_it_was_opened_on(self):
        s = self.server
        req = {"text": "KML import works.", "source": {"type": "user_chat", "ref": "c"}}
        new = {"area": "PLAN", "title": "Plan imports KML", "requirement": req, "targets": "both",
               "steps": ["Open Flight planning", "Import the KML"], "expected": "The area shows"}
        r = s.send("POST", "/api/cases", new)
        self.assertStatus(r, 201, "create draft")
        cid, c0_rel = r.json()["id"], r.json()["file"]
        form = self.form_body(cid)
        self.assertEqual(form["base"], c0_rel)
        c1 = json.loads(self.fx.read(c0_rel))  # /qa-run completes the draft while the form is open
        c1.update(status="active", targets="tablet", covered_paths=["apps/src/pages/FlightPlanning/**"],
                  supersedes={"id": cid, "file": c0_rel, "file_sha256": "0" * 64, "why": "completed"},
                  steps=[dict(st, helper="tools/act.py {target} tap testid:x") for st in c1["steps"]])
        put(self.fx.root, f"qa/cases/{cid}.c1.json", c1)
        self.assertStatus(s.send("PUT", f"/api/cases/{cid}", dict(form, title="Plan imports KML files")), 409,
                          "a form opened on c0")
        no_base = {k: v for k, v in form.items() if k != "base"}
        self.assertStatus(s.send("PUT", f"/api/cases/{cid}", dict(no_base, title="Plan imports KML files")), 400,
                          "no base")
        self.assertFalse(self.fx.exists(f"qa/cases/{cid}.c2.json"))
        r = s.send("PUT", f"/api/cases/{cid}", dict(self.form_body(cid), title="Plan imports KML files"))
        self.assertStatus(r, 201, "a form reloaded on c1")
        c2 = json.loads(self.fx.read(r.json()["file"]))
        self.assertEqual((c2["status"], c2["targets"], c2["covered_paths"]),
                         ("active", "tablet", ["apps/src/pages/FlightPlanning/**"]))

    def test_flag_values_in_needs(self):
        """needs.flags may give the value a deciding flag needs, "Name: on" or "Name: off" (qa/runner/run.py
        flag_needs, 2026-11-02); anything else stays refused."""
        s = self.server
        req = {"text": "Terrain works with OfflineSync.", "source": {"type": "user_chat", "ref": "c"}}
        flags = ["OfflineSync: on", "CloudSyncToggle: off", "Kml"]
        new = {"area": "TERR", "title": "Terrain with OfflineSync", "requirement": req, "targets": "tablet",
               "steps": ["Open Maps"], "expected": "The terrain shows", "needs": {"link": "none", "flags": flags}}
        r = s.send("POST", "/api/cases", new)
        self.assertStatus(r, 201, "flag values")
        c0 = json.loads(self.fx.read(r.json()["file"]))
        self.assertEqual((c0["needs"]["flags"], case_schema.validate_case(c0)), (flags, []))
        before = self.case_files()
        for bad in ("OfflineSync: maybe", "OfflineSync on", "Offline Sync: on", "OfflineSync: on: off"):
            self.assertStatus(s.send("POST", "/api/cases", dict(new, needs={"link": "none", "flags": [bad]})), 400, bad)
        self.assertEqual(self.case_files(), before, "nothing was written")

    def test_globs_and_flags_with_secrets_are_refused(self):
        s = self.server
        short_jwt = "ey" + "JhbGciOiJ" + "." + "eyJzdWIiOiJ" + "." + "c2lnbmF0dXJl"
        self.assertTrue(writes.FLAG_RE.match(short_jwt), "the planted flag passes the name check")
        before = self.case_files()
        req = {"text": "KML import works.", "source": {"type": "user_chat", "ref": "c"}}
        new = {"area": "PLAN", "title": "Plan imports KML", "requirement": req, "targets": "both",
               "steps": ["Open Flight planning"], "expected": "The area shows"}
        for what, change in (("e-mail in a glob", {"covered_paths": [f"apps/{EMAIL}/**"]}),
                             ("JWT in a glob", {"covered_paths": [f"apps/{short_jwt}"]}),
                             ("secret in a glob", {"covered_paths": [f"apps/{SECRET}/**"]}),
                             ("JWT as a flag", {"needs": {"link": "none", "flags": [short_jwt]}})):
            r = s.send("POST", "/api/cases", dict(new, **change))
            self.assertStatus(r, 400, what)
            self.assertClean(r.body, what)
        body = dict(self.form_body("PLAN-002"), covered_paths=[f"apps/{EMAIL}/**"], status="draft")
        self.assertStatus(s.send("PUT", "/api/cases/PLAN-002", body), 400, "e-mail in a glob, edit")
        self.assertEqual(self.case_files(), before, "nothing was written")

    def test_cleared_locator_stays_cleared(self):
        body = self.form_body("PLAN-005")
        self.assertEqual(body["requirement"]["source"].get("locator"), "description bullet 7")
        body["requirement"]["source"] = {"type": "clickup_task", "ref": "86c1abcde"}
        r = self.server.send("PUT", "/api/cases/PLAN-005", dict(body, title="Plan renames sites"))
        self.assertStatus(r, 201, "locator and requester cleared")
        src = json.loads(self.fx.read(r.json()["file"]))["requirement"]["source"]
        self.assertEqual(src, {"type": "clickup_task", "ref": "86c1abcde", "cache": "qa/cache/clickup/CL-12345.json",
                               "fetched": "2026-10-21T08:00:00.000Z", "custom_id": "CL-12345"})


class PlanCaseReadTests(FixtureCase):
    """What the Plan screen reads for a plan case (static/app.js caseSteps): the newest record from /api/cases/<id>,
    or the file the plan pinned from /files/. Both carry the case's preconditions whole, the check included: since
    2026-11-10 the Plan screen lists them and marks a line whose check is "person" (test_plan_pre.py). A route that
    left them out would show as a case without preconditions, so it is checked here."""

    PRE = [{"id": "P1", "text": "qa-stag account logged in", "check": "run.py --check-account", "applies_to": "tablet"},
           {"id": "P2", "text": "Flight Demo has the map chosen as custom terrain (set by the person)",
            "check": "person", "applies_to": "tablet"}]

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        put(cls.fx.root, "qa/cases/PLAN-008.json", case_record(
            "PLAN-008", "draft", "Plan keeps its terrain", "The plan keeps its terrain.", preconditions=cls.PRE))

    def test_both_routes_carry_the_preconditions(self):
        s = self.server
        d = s.get("/api/cases/PLAN-008").json()
        self.assertEqual((d["latest_file"], d["case"]["preconditions"]), ("qa/cases/PLAN-008.json", self.PRE))
        r = s.get(f"/files/{d['latest_file']}")
        self.assertStatus(r, 200, "the case file a plan pins")
        self.assertEqual(r.json()["preconditions"], self.PRE)


class TaskStateTests(FixtureCase):
    """Runs and the campaign change only in the states before the user's greenlight; .tmp is never written through."""

    def test_runs_and_campaign_before_the_greenlight(self):
        w = writes.Writer(self.fx.root)
        tid = w.create_task({"text": "a task", "targets": ["TA5"]})["id"]
        w.set_task_state(tid, "running", by="claude", via="qa-run")
        w.set_campaign(tid, SLUG)
        w.add_run(tid, RUN)
        w.set_task_state(tid, "results_ready", by="claude", via="qa-run")
        w.add_run(tid, "R-20261021-TA5-PLAN-001-02")  # a re-run before the greenlight
        with self.assertRaises(writes.WriteError) as cm:
            w.set_campaign(tid, "2026-10-22-other")
        self.assertEqual(cm.exception.status, 409, "campaign after the results")
        w.set_task_state(tid, "greenlit", by="user", via="app")
        for state in ("greenlit", "drafted"):
            for fn, arg in ((w.add_run, "R-20261022-TA5-PLAN-001-03"), (w.set_campaign, "2026-10-22-other")):
                with self.assertRaises(writes.WriteError, msg=f"{fn.__name__} in {state}") as cm:
                    fn(tid, arg)
                self.assertEqual(cm.exception.status, 409, f"{fn.__name__} in {state}")
            if state == "greenlit":
                w.set_task_state(tid, "drafted", by="claude", via="qa-run")
        task = json.loads(self.fx.read(f"qa/tasks/{tid}.json"))
        self.assertEqual((task["runs"], task["campaign"]), ([RUN, "R-20261021-TA5-PLAN-001-02"], SLUG))

    def test_hard_linked_tmp_is_not_written_through(self):
        w = writes.Writer(self.fx.root)
        tid = w.create_task({"text": "a task", "targets": ["TA5"]})["id"]
        record = f"qa/runs/{SLUG}/{RUN}.json"
        before = self.fx.read(record)
        os.link(self.fx.path(record), self.fx.path(f"qa/tasks/{tid}.json.tmp"))
        w.set_task_state(tid, "closed")
        self.assertTrue(self.fx.read(record) == before, "the linked run record was changed")
        self.assertEqual(json.loads(self.fx.read(f"qa/tasks/{tid}.json"))["state"], "closed")
        self.assertFalse(self.fx.exists(f"qa/tasks/{tid}.json.tmp"))


# ---- fixes from the audit of 2026-10-28 ----------------------------------------------------------------------------

class AuditFixTests(FixtureCase):
    """Stale runner locks, corrected runs on the task screens and in tasks.py show, the case version of the last runs,
    and the startup line."""

    cli = WriteFlowTests.cli
    MISSING = "R-20261021-TA5-PLAN-001-09"

    def test_a_lock_is_held_only_while_its_lock_file_is_flocked(self):
        d = data_model.Data(self.fx.root)
        self.assertEqual([x["stale"] for x in d.live()["locks"]], [True], "a json with no .lock beside it")
        lock = self.fx.path(f"qa/runner/state/locks/{SERIAL}.lock")
        with open(lock, "a") as f:
            self.assertIs(d.live()["locks"][0]["stale"], True, "a .lock no run holds")
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertIs(d.live()["locks"][0]["stale"], False, "a run holds it")
            self.assertIs(self.server.get("/api/live").json()["locks"][0]["stale"], False, "over HTTP")
            fcntl.flock(f, fcntl.LOCK_UN)
        self.assertIs(d.live()["locks"][0]["stale"], True, "the run was stopped and left its json")
        os.unlink(lock)

    def put_task(self, tid):
        put(self.fx.root, f"qa/tasks/{tid}.json", dict(json.loads(self.fx.read(f"qa/tasks/{TASK}.json")), id=tid,
                                                        runs=[RUN_BT, RUN, self.MISSING]))

    def test_the_task_screen_shows_the_correction(self):
        self.put_task("T-20261020-003")
        task = self.server.get("/api/tasks/T-20261020-003").json()
        self.assertEqual(task["run_ids"], [RUN_BT, RUN, self.MISSING])
        self.assertEqual([(r["run"], r["verdict"], r.get("corrects")) for r in task["runs"]],
                         [(RUN_BT + "c1", "PARTIAL", RUN_BT), (RUN, "FAIL", None), (self.MISSING, None, None)])
        self.assertIsNone(task["runs"][0]["superseded_by"])

    def test_tasks_show_names_the_newest_record_of_each_run(self):
        self.put_task("T-20261020-004")
        before = self.fx.read("qa/tasks/T-20261020-004.json")
        code, out = self.cli("show", "T-20261020-004", "--root", self.fx.root)
        self.assertEqual(code, 0)
        self.assertEqual(out["current"], [{"run": RUN_BT, "newest": RUN_BT + "c1", "verdict": "PARTIAL"},
                                          {"run": RUN, "newest": RUN, "verdict": "FAIL"},
                                          {"run": self.MISSING, "newest": self.MISSING, "verdict": None}])
        self.assertEqual(out["runs"], [RUN_BT, RUN, self.MISSING])
        self.assertTrue(self.fx.read("qa/tasks/T-20261020-004.json") == before, "show changed the task file")

    def test_last_runs_name_their_case_version(self):
        old = records.sha256_file(self.fx.path("qa/cases/PLAN-001.json"))
        rid = "R-20261022-ET65-PLAN-001-01"
        put(self.fx.root, f"qa/runs/{SLUG}/{rid}.json", {
            "kind": "run", "schema": 1, "id": rid, "created": "2026-10-22T09:00:00.000Z", "author": "runner",
            "supersedes": None, "mode": "live", "case": "PLAN-001", "case_file_sha256": old,
            "target": {"class": "tablet", "serial": "ET1", "tag": "ET65"}, "attempts": [],
            "verdict": {"v": "PASS", "evidence_kind": "device"}})
        d = data_model.Data(self.fx.root)

        def case():
            return next(c for c in d.cases()["cases"] if c["id"] == "PLAN-001")
        c = case()
        self.assertEqual(c["current_sha256"], old)
        self.assertEqual({t: (x["case_sha256"], x["on_current"]) for t, x in c["last"].items()},
                         {"TA5": (None, False), "ET65": (old, True)}, "the TA5 record names no case version")
        put(self.fx.root, "qa/cases/PLAN-001.c1.json", dict(json.loads(self.fx.read("qa/cases/PLAN-001.json")),
                                                            supersedes="PLAN-001"))
        new = records.sha256_file(self.fx.path("qa/cases/PLAN-001.c1.json"))
        c = case()
        self.assertEqual((c["current_sha256"], c["last"]["ET65"]["case_sha256"], c["last"]["ET65"]["on_current"]),
                         (new, old, False), "a PASS on the older version")
        self.assertEqual(records.sha256_file(autonomy.load_newest("PLAN-001", self.fx.root)[1]), new,
                         "current_sha256 is the file the runner loads")

    def test_the_startup_line_has_the_utc_time(self):
        self.assertRegex(serve.startup_line(8787),
                         r"^http://127\.0\.0\.1:8787/ started \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z\Z")


class AuditHookTests(unittest.TestCase):
    """The real-workspace check (2026-11-05, wingmate-debug FINDINGS #17): this process's own writes under qa/ or
    tests/ fail the run, whatever else changed; another process's writes meanwhile do not (before, a Hover run or
    make_dsm.py failed the suite: 'added: tests/2026-11-04-big/files/max.tif'); a program the run started that could
    write makes a changed snapshot count again. A temp folder stands for the real workspace."""

    PART = "tests/2026-11-04-big/files/.make_dsm-0t2017yy.part"

    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="qa-app-audit-"))
        put(self.root, "qa/cases/PLAN-001.json", "{}\n")
        put(self.root, "qa/ledger/2026-11-05.jsonl", '{"seq": 1, "src": "hook"}\n')
        put(self.root, self.PART, "x")
        self.saved = dict(AUDIT)
        # the real workspace stays watched while these tests run: what they record there goes back to the run's log
        AUDIT.update(tops=self.saved["tops"] + watched_tops(self.root), writes=[], spawned=[], on=True)
        if not _HOOKED:
            sys.addaudithook(audit_hook)
            _HOOKED.append(True)
        self.before = snapshot(self.root)

    def tearDown(self):
        mine = [w for w in AUDIT["writes"] if under(w[1], self.saved["tops"])]
        AUDIT.clear()
        AUDIT.update(self.saved)
        AUDIT["writes"] += mine
        shutil.rmtree(self.root, ignore_errors=True)

    def other_process(self, fn):
        """fn's file changes, as another process makes them: not seen by the hook."""
        AUDIT["on"] = False
        try:
            fn()
        finally:
            AUDIT["on"] = True

    def problem(self):
        with contextlib.redirect_stderr(io.StringIO()):  # the note about another process's writes
            return real_workspace_problem(self.before, snapshot(self.root), self.root, AUDIT)

    def test_another_processes_writes_meanwhile_are_no_failure(self):
        def make_dsm_and_a_run():
            os.replace(os.path.join(self.root, *self.PART.split("/")),
                       os.path.join(self.root, "tests", "2026-11-04-big", "files", "max.tif"))
            put(self.root, "qa/cases/PLAN-001.c1.json", "{}\n")
            with open(os.path.join(self.root, "qa", "ledger", "2026-11-05.jsonl"), "ab") as f:
                f.write(b'{"seq": 2, "src": "runner"}\n')
        self.other_process(make_dsm_and_a_run)
        self.assertTrue(any(snapshot_diff(self.before, snapshot(self.root), self.root).values()), "it did change")
        self.assertIsNone(self.problem())

    def test_this_processes_own_writes_fail(self):
        with open(os.path.join(self.root, "qa", "cases", "PLAN-001.json"), encoding="utf-8") as f:
            f.read()  # reading is no write
        os.makedirs(os.path.join(self.root, "qa", "__pycache__"), exist_ok=True)  # bytecode is no write either
        self.assertEqual(AUDIT["writes"], [])
        self.assertIsNone(self.problem())
        for write in (lambda: put(self.root, "qa/tasks/T-20261105-001.json", "{}"),
                      lambda: os.replace(os.path.join(self.root, *self.PART.split("/")),
                                         os.path.join(self.root, "tests", "2026-11-04-big", "files", "max.tif")),
                      lambda: os.remove(os.path.join(self.root, "qa", "cases", "PLAN-001.json")),
                      lambda: shutil.rmtree(os.path.join(self.root, "tests"))):
            AUDIT["writes"] = []
            write()
            self.assertTrue(AUDIT["writes"], write)
            self.assertTrue(self.problem().startswith("the test run wrote in the real workspace: "), write)
        AUDIT["writes"] = []
        outside = os.path.realpath(tempfile.mkdtemp(prefix="qa-app-audit-out-"))
        try:
            put(outside, "qa/tasks/T-20261105-001.json", "{}")  # a fixture workspace elsewhere
        finally:
            shutil.rmtree(outside, ignore_errors=True)
        self.assertEqual(AUDIT["writes"], [])

    def test_a_program_that_could_write_makes_a_change_count(self):
        self.other_process(lambda: put(self.root, "qa/cases/PLAN-001.c1.json", "{}\n"))
        AUDIT["spawned"] = [("subprocess.Popen", ["git", "-C", os.path.join(self.root, "WingtraCloud"), "rev-parse"]),
                            ("subprocess.Popen", [sys.executable, "-I", "-B", TASKS_PY, "show", TASK, "--root",
                                                  os.path.join(tempfile.gettempdir(), "qa-app-test-x", "ws")])]
        self.assertIsNone(self.problem(), "a read-only git and tasks.py on a temp workspace cannot have written it")
        for argv in ([sys.executable, "-I", "-B", TASKS_PY, "set", TASK, "closed", "--by", "user"],
                     [sys.executable, "-I", "-B", TASKS_PY, "show", TASK, "--root", self.root],
                     ["git", "-C", os.path.join(self.root, "qa"), "status"], ["sh", "-c", "touch x"]):
            AUDIT["spawned"] = [("subprocess.Popen", argv)]
            why = self.problem()
            self.assertTrue(why and why.startswith("the test run changed files in the real workspace: "), argv)


class SnapshotTests(unittest.TestCase):
    """The no-file-changed check: a ledger day file may only grow; any other change still counts."""

    LEDGER = "qa/ledger/2026-10-28.jsonl"

    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="qa-app-snap-"))
        put(self.root, self.LEDGER, '{"seq": 1}\n')
        put(self.root, "qa/cases/PLAN-001.json", "{}\n")
        self.before = snapshot(self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def changed(self, rel, content, mode="ab", root=True):
        with open(os.path.join(self.root, *rel.split("/")), mode) as f:
            f.write(content)
        return snapshot_diff(self.before, snapshot(self.root), self.root if root else None)["changed"]

    def test_an_append_to_the_ledger_is_no_change(self):
        self.assertEqual(self.changed(self.LEDGER, b'{"seq": 2, "src": "hook"}\n'), [])

    def test_an_appended_line_that_is_not_a_hook_entry_is_a_change(self):
        self.assertEqual(self.changed(self.LEDGER, b'{"seq": 2, "src": "runner"}\n'), [self.LEDGER])

    def test_a_rewrite_of_the_ledger_is_a_change(self):
        self.assertEqual(self.changed(self.LEDGER, b'{"seq": 9}\n', "wb"), [self.LEDGER], "same size, other bytes")

    def test_a_longer_rewrite_of_the_ledger_is_a_change(self):
        self.assertEqual(self.changed(self.LEDGER, b'{"seq": 9}\n{"seq": 2}\n', "wb"), [self.LEDGER])

    def test_a_truncated_ledger_is_a_change(self):
        self.assertEqual(self.changed(self.LEDGER, b'{"seq"', "wb"), [self.LEDGER])

    def test_an_append_elsewhere_is_a_change(self):
        self.assertEqual(self.changed("qa/cases/PLAN-001.json", b"\n"), ["qa/cases/PLAN-001.json"])

    def test_without_the_root_an_append_is_a_change(self):
        self.assertEqual(self.changed(self.LEDGER, b'{"seq": 2}\n', root=False), [self.LEDGER])


# ---- launches: case x device matrix and the concurrency line ------------------------------------------------------

LCAMP = "2026-10-20-launch-fixture"
BTCAMP = "2026-10-16-backtest-fixture"
LDAY = "2026-10-20"
LTASK = "T-20261020-001"


def lt(hms):
    return f"{LDAY}T{hms}Z"


def lrid(tag, case, nn, date="20261020"):
    return f"R-{date}-{tag}-{case}-{nn}"


# ledger seq -> t; every deciding time and every "finished before" note below follows from these and the windows
LEDGER_T = {
    11: lt("10:00:01.000"), 21: lt("10:00:27.400"), 31: lt("10:00:35.000"),  # launch 01, TA5 steps 1-3
    12: lt("10:00:06.000"), 22: lt("10:00:20.000"), 32: lt("10:00:24.000"),  # launch 01, ET65 steps 1-3
    51: lt("10:30:05.000"),  # launch 02, TA5
    42: lt("11:00:05.000"), 43: lt("11:00:08.000"),  # launch 03, TA5 steps 2-3
    48: lt("11:00:06.000"), 44: lt("11:00:09.500"),  # launch 03, ET65 steps 2-3
    47: lt("11:00:24.000"), 45: lt("11:00:28.000"),  # launch 03, W044 steps 2-3
}


def lsteps(*pairs):
    return [{"n": n, "text": f"Step {n}", "actions": [{"day": LDAY, "seq": seq}]} for n, seq in pairs]


def lrun(rid, case, tag, mode, window, steps=(), verdict="PASS", pred=None, supersedes=None, created=None):
    att = [{"n": 1, "started": window[0], "ended": window[1], "steps": list(steps), "evidence": []}] if window else []
    rec = {"kind": "run", "schema": 1, "id": rid, "created": created or (window[1] if window else lt("09:00:00.000")),
           "author": "runner", "supersedes": supersedes, "mode": mode, "case": case,
           "target": {"class": "web" if tag == "W044" else "tablet", "tag": tag}, "build": {"hash7": sv("84d3721")},
           "attempts": att, "verdict": {"v": verdict, "evidence_kind": "device"}}
    if pred:
        rec["prediction"] = {"id": pred}
    return rec


def build_launch_fixture(root, hold_back=()):
    """Launches of MAP-001 (both), MAP-002 (web only) and MAP-003 (tablet only) with planted run windows, predictions
    and ledger lines, so overlap, spread and the "finished before" notes are exact. hold_back leaves ledger seqs out."""
    put(root, "qa/runner/engine.json", {"env": "staging", "account": "ct-label"})
    put(root, f"qa/ledger/{LDAY}.jsonl", "".join(json.dumps({"seq": s, "t": LEDGER_T[s], "src": "act"}) + "\n"
                                               for s in sorted(LEDGER_T) if s not in hold_back))
    steps = [{"n": 1, "text": "Open the map", "helper": None, "gates": []},
             {"n": 2, "text": "Read the layers", "helper": None, "gates": [],
              "assert": {"kind": "scripted", "expr": "v['layers']"}},
             {"n": 3, "text": "Read the layers again", "helper": None, "gates": [],
              "assert": {"kind": "scripted", "expr": "v['layers_again']"}},
             {"n": 4, "text": "Close the map", "helper": None, "gates": [],
              "assert": {"kind": "judged", "question": "Closed?"}}]
    put(root, "qa/cases/MAP-001.json", case_record("MAP-001", "draft", "Map layers", "Layers show.", steps=steps,
                                                   targets="both"))
    put(root, "qa/cases/MAP-002.json", case_record("MAP-002", "draft", "Map on web", "Web map.", targets="web"))
    put(root, "qa/cases/MAP-003.json", case_record("MAP-003", "draft", "Map on tablet", "Tablet map."))
    runs = {
        # launch 01: TA5 and ET65, W044 not run; prediction says step 2; ET65's record is corrected by -01c1
        lrid("TA5", "MAP-001", "01"): lrun(lrid("TA5", "MAP-001", "01"), "MAP-001", "TA5", "live",
                                           (lt("10:00:00.000"), lt("10:00:40.000")), lsteps((1, 11), (2, 21), (3, 31))),
        lrid("ET65", "MAP-001", "01"): lrun(lrid("ET65", "MAP-001", "01"), "MAP-001", "ET65", "live",
                                            (lt("10:00:05.250"), lt("10:00:25.000")), lsteps((1, 12), (2, 22), (3, 32)),
                                            verdict="FAIL", pred=f"PR-{lrid('ET65', 'MAP-001', '01')}"),
        lrid("ET65", "MAP-001", "01c1"): lrun(lrid("ET65", "MAP-001", "01c1"), "MAP-001", "ET65", "live",
                                              (lt("10:00:05.250"), lt("10:00:25.000")),
                                              lsteps((1, 12), (2, 22), (3, 32)), verdict="PASS",
                                              pred=f"PR-{lrid('ET65', 'MAP-001', '01')}",
                                              supersedes=lrid("ET65", "MAP-001", "01"), created=lt("10:05:00.000")),
        # launch 02: TA5 alone
        lrid("TA5", "MAP-001", "02"): lrun(lrid("TA5", "MAP-001", "02"), "MAP-001", "TA5", "live",
                                           (lt("10:30:00.000"), lt("10:30:20.000")), lsteps((2, 51))),
        # launch 03: all three, no prediction (deciding step = the case's last scripted step, 3), windows apart
        lrid("TA5", "MAP-001", "03"): lrun(lrid("TA5", "MAP-001", "03"), "MAP-001", "TA5", "live",
                                           (lt("11:00:00.000"), lt("11:00:10.000")), lsteps((2, 42), (3, 43))),
        lrid("ET65", "MAP-001", "03"): lrun(lrid("ET65", "MAP-001", "03"), "MAP-001", "ET65", "live",
                                            (lt("11:00:02.000"), lt("11:00:12.000")), lsteps((2, 48), (3, 44))),
        lrid("W044", "MAP-001", "03"): lrun(lrid("W044", "MAP-001", "03"), "MAP-001", "W044", "live",
                                            (lt("11:00:20.000"), lt("11:00:30.000")), lsteps((2, 47), (3, 45))),
        # web-only case: W044 alone; tablet-only case: TA3 plus a web run that is not one of its three devices
        lrid("W044", "MAP-002", "01"): lrun(lrid("W044", "MAP-002", "01"), "MAP-002", "W044", "live",
                                            (lt("12:00:00.000"), lt("12:00:10.000"))),
        lrid("TA3", "MAP-003", "01"): lrun(lrid("TA3", "MAP-003", "01"), "MAP-003", "TA3", "live",
                                           (lt("12:00:00.000"), lt("12:00:10.000"))),
        lrid("W044", "MAP-003", "01"): lrun(lrid("W044", "MAP-003", "01"), "MAP-003", "W044", "live",
                                            (lt("12:00:05.000"), lt("12:00:30.000"))),
    }
    for rid, rec in runs.items():
        put(root, f"qa/runs/{LCAMP}/{rid}.json", rec)
    for rid in (lrid("TA5", "MAP-001", "01"), lrid("ET65", "MAP-001", "01")):
        put(root, f"qa/runs/{LCAMP}/pred/PR-{rid}.json", {"kind": "prediction", "id": f"PR-{rid}",
                                                         "deciding_assertion": {"step": 2, "expr": "v['layers']"}})
    # back-test launch: two TA5 records of run 01 from different days (the newer one wins), one ET65
    for date, tag in (("20261016", "TA5"), ("20261017", "TA5"), ("20261016", "ET65")):
        rid = lrid(tag, "MAP-001", "01", date)
        day = f"{date[:4]}-{date[4:6]}-{date[6:]}"
        put(root, f"qa/runs/{BTCAMP}/{rid}.json", dict(
            lrun(rid, "MAP-001", tag, "backtest", (f"{day}T09:00:00.000Z", f"{day}T09:10:00.000Z")),
            created="2026-10-19T08:00:00.000Z"))
    put(root, f"qa/tasks/{LTASK}.json", {"kind": "task", "schema": 1, "id": LTASK, "created": lt("09:00:00.000"),
                                         "author": "user", "text": "MAP check", "targets": ["TA5"],
                                         "priority": "normal", "cases": [], "state": "results_ready", "history": [],
                                         "runs": [lrid("TA5", "MAP-001", "01")], "campaign": LCAMP,
                                         "outputs": {"summary": None, "tickets": [], "comments": []}})


def launch_of(case, campaign, nn):
    return next(x for x in case["launches"] if x["campaign"] == campaign and x["nn"] == nn)


class LaunchTests(unittest.TestCase):
    """results(): devices, launches (matrix cells) and the concurrency line, on a planted fixture."""

    @classmethod
    def setUpClass(cls):
        redact._SECRETS = [SECRET]
        cls.tmp = os.path.realpath(tempfile.mkdtemp(prefix="qa-app-launch-"))
        cls.root = os.path.join(cls.tmp, "ws")
        build_launch_fixture(cls.root)
        cls.data = data_model.Data(cls.root)
        cls.res = {c["case"]: c for c in cls.data.results()["cases"]}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        redact._SECRETS = _SECRETS_BEFORE

    def test_launches_newest_first(self):
        c = self.res["MAP-001"]
        self.assertEqual(c["devices"], ["TA5", "ET65", "W044"])
        self.assertEqual(c["extra_devices"], [])
        self.assertEqual([(x["campaign"], x["nn"]) for x in c["launches"]],
                         [(LCAMP, "03"), (LCAMP, "02"), (LCAMP, "01"), (BTCAMP, "01")])

    def test_two_of_three_parallel_launch(self):
        x = launch_of(self.res["MAP-001"], LCAMP, "01")
        self.assertEqual((x["mode"], x["builds"]), ("live", ["84d3721"]))
        self.assertEqual(x["devices_ran"], "2 of 3 (W044 not run)")
        self.assertEqual(x["cells"]["W044"], {"verdict": "NOT_TESTED", "why": "no run in this launch"})
        ta5 = x["cells"]["TA5"]
        self.assertEqual((ta5["run"], ta5["verdict"], ta5["evidence_kind"], ta5["attempts"], ta5["older"]),
                         (lrid("TA5", "MAP-001", "01"), "PASS", "device", 1, []))
        self.assertIn("v", ta5["stale"])
        conc = x["concurrency"]
        self.assertEqual(conc["kind"], "parallel launch")
        self.assertEqual(conc["windows"], {"TA5": {"start": lt("10:00:00.000"), "end": lt("10:00:40.000")},
                                           "ET65": {"start": lt("10:00:05.250"), "end": lt("10:00:25.000")}})
        self.assertEqual(conc["overlap_s"], 19.75)
        self.assertEqual(conc["common_window"], {"start": lt("10:00:05.250"), "end": lt("10:00:25.000")})
        # TA5 names no prediction id: its PR-<run>.json is found by name; ET65's -01c1 names its base's prediction
        self.assertEqual((conc["deciding_step"], conc["deciding_step_from"]), (2, "prediction"))
        self.assertEqual(conc["deciding_times"], {"TA5": lt("10:00:27.400"), "ET65": lt("10:00:20.000")})
        self.assertEqual(conc["spread_s"], 7.4)
        self.assertEqual(conc["finished_before"], [{"tag": "ET65", "before": "TA5", "step": 2}])
        self.assertIs(conc["simultaneous_verified"], False)
        self.assertEqual(conc["text"], "Parallel launch: windows overlap 20 s; deciding step 2 read 7.4 s apart; "
                                       "simultaneous not verified; ET65 finished before TA5 reached step 2")

    def test_correction_replaces_its_base(self):
        base = lrid("ET65", "MAP-001", "01")
        cell = launch_of(self.res["MAP-001"], LCAMP, "01")["cells"]["ET65"]
        self.assertEqual((cell["run"], cell["verdict"], cell["older"]), (base + "c1", "PASS", [base]))
        rows = {r["run"]: r for r in self.res["MAP-001"]["rows"]}
        self.assertEqual(rows[base]["superseded_by"], base + "c1", "the per-target rows keep both records")

    def test_no_overlap_and_case_fallback(self):
        x = launch_of(self.res["MAP-001"], LCAMP, "03")
        self.assertEqual(x["devices_ran"], "3 of 3")
        conc = x["concurrency"]
        self.assertIsNone(conc["overlap_s"])
        self.assertIsNone(conc["common_window"])
        self.assertEqual((conc["deciding_step"], conc["deciding_step_from"]), (3, "case"))
        self.assertEqual(conc["deciding_times"], {"TA5": lt("11:00:08.000"), "ET65": lt("11:00:09.500"),
                                                  "W044": lt("11:00:28.000")})
        self.assertEqual(conc["spread_s"], 20.0)
        self.assertEqual(conc["finished_before"], [{"tag": "TA5", "before": "W044", "step": 3},
                                                   {"tag": "ET65", "before": "W044", "step": 3}])
        self.assertEqual(conc["text"], "Parallel launch: no overlap of the run windows; deciding step 3 read 20 s "
                                       "apart; simultaneous not verified; TA5 finished before W044 reached step 3; "
                                       "ET65 finished before W044 reached step 3")

    def test_single_device(self):
        x = launch_of(self.res["MAP-001"], LCAMP, "02")
        self.assertEqual(x["devices_ran"], "1 of 3 (ET65, W044 not run)")
        self.assertEqual(x["concurrency"], {"kind": "single device", "text": "single device (TA5)"})

    def test_backtest_launch(self):
        x = launch_of(self.res["MAP-001"], BTCAMP, "01")
        self.assertEqual(x["mode"], "backtest")
        self.assertEqual(x["concurrency"], {"kind": "not applicable", "text": "back-test (reconstructed from logs)"})
        self.assertEqual(x["devices_ran"], "2 of 3 (W044 not run)")
        cell = x["cells"]["TA5"]
        self.assertEqual((cell["run"], cell["older"]), (lrid("TA5", "MAP-001", "01", "20261017"),
                                                        [lrid("TA5", "MAP-001", "01", "20261016")]))

    def test_web_only_and_tablet_cases(self):
        web = self.res["MAP-002"]
        self.assertEqual(web["devices"], ["TA5", "ET65", "W044"])
        x = web["launches"][0]
        for tag in ("TA5", "ET65"):
            self.assertEqual(x["cells"][tag], {"verdict": "NOT_TESTED", "why": "not routed (web-only case)"})
        self.assertEqual(x["devices_ran"], "1 of 3 (TA5, ET65 not routed: web-only case)")
        self.assertEqual(x["concurrency"]["text"], "single device (W044)")
        tab = self.res["MAP-003"]
        self.assertEqual((tab["devices"], tab["extra_devices"]), (["TA5", "ET65", "TA3"], ["W044"]))
        x = tab["launches"][0]
        self.assertEqual(x["devices_ran"], "1 of 3 (TA5, ET65 not run; W044 also ran (not one of the three))")
        self.assertEqual(x["cells"]["W044"]["run"], lrid("W044", "MAP-003", "01"))
        conc = x["concurrency"]
        self.assertEqual((conc["overlap_s"], conc["deciding_step"], conc["spread_s"]), (5.0, None, None))
        self.assertEqual(conc["text"], "Parallel launch: windows overlap 5.0 s; deciding step unknown; "
                                       "simultaneous not verified")

    def test_task_filter_keeps_whole_launches(self):
        c = {x["case"]: x for x in self.data.results(task=LTASK)["cases"]}["MAP-001"]
        self.assertEqual([r["run"] for r in c["rows"]], [lrid("TA5", "MAP-001", "01")])
        self.assertEqual([(x["campaign"], x["nn"]) for x in c["launches"]], [(LCAMP, "01")])
        self.assertEqual(c["launches"][0]["cells"]["ET65"]["run"], lrid("ET65", "MAP-001", "01c1"))

    def test_simultaneous_is_never_claimed(self):
        texts = 0
        for c in self.res.values():
            for x in c["launches"]:
                conc = x["concurrency"]
                text = conc["text"]
                self.assertEqual(text.count("simultaneous"), text.count("simultaneous not verified"), text)
                if conc["kind"] == "parallel launch":
                    self.assertIs(conc["simultaneous_verified"], False)
                    texts += 1
        self.assertEqual(texts, 3)

    def test_http_results_carry_launches(self):
        server = Server(self.root)
        try:
            r = server.get("/api/results?case=MAP-001")
            self.assertEqual(r.status, 200)
            got = r.json()["cases"][0]
            self.assertEqual(got["devices"], ["TA5", "ET65", "W044"])
            self.assertEqual(got["launches"], json.loads(json.dumps(self.res["MAP-001"]["launches"])))
        finally:
            server.close()

    def test_ledger_index_follows_appends(self):
        tmp = os.path.realpath(tempfile.mkdtemp(prefix="qa-app-ledger-"))
        try:
            root = os.path.join(tmp, "ws")
            build_launch_fixture(root, hold_back={44})
            d = data_model.Data(root)

            def launch03():
                c = {x["case"]: x for x in d.results(case="MAP-001")["cases"]}["MAP-001"]
                return launch_of(c, LCAMP, "03")["concurrency"]

            conc = launch03()
            self.assertIsNone(conc["deciding_times"]["ET65"])
            self.assertIn("deciding step 3 read 20 s apart (not reached by ET65)", conc["text"])
            ledger = os.path.join(root, "qa", "ledger", f"{LDAY}.jsonl")
            line = json.dumps({"seq": 44, "t": LEDGER_T[44], "src": "act"})
            with open(ledger, "a", encoding="utf-8") as f:
                f.write(line)  # half written: no newline yet
            self.assertIsNone(launch03()["deciding_times"]["ET65"], "a line without its newline is not read")
            with open(ledger, "a", encoding="utf-8") as f:
                f.write("\n")
            conc = launch03()
            self.assertEqual(conc["deciding_times"]["ET65"], LEDGER_T[44])
            self.assertEqual(conc["spread_s"], 20.0)
            self.assertEqual(d._ledger_idx[LDAY]["offset"], os.path.getsize(ledger))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ---- the real workspace, read-only ---------------------------------------------------------------------------------

@unittest.skipUnless(os.path.isdir(os.path.join(REAL_ROOT, "qa", "runs")), "no real workspace next to qa/app")
class RealWorkspaceTests(unittest.TestCase):
    """GETs only. The real secrets.env is loaded by redact.py internally; nothing is printed from a response."""

    TERR_RUNS = ("R-20261021-ET65-TERR-001-01", "R-20261021-ET65-TERR-001-02", "R-20261021-TA5-TERR-001-01",
                 "R-20261021-TA5-TERR-001-02", "R-20261021-W044-TERR-001-01", "R-20261021-W044-TERR-001-02")

    @classmethod
    def setUpClass(cls):
        redact._SECRETS = None  # the real redactor, with the real secret list
        cls.before = snapshot(REAL_ROOT)
        cls.server = Server(REAL_ROOT)
        cls.cache = {}

    @classmethod
    def tearDownClass(cls):
        cls.server.close()
        redact._SECRETS = _SECRETS_BEFORE

    def get_json(self, path):
        if path not in self.cache:
            r = self.server.get(path)
            self.assertEqual(r.status, 200, f"{path}: got {r.status}")
            self.assertEqual(r.header("Content-Type"), "application/json; charset=utf-8", path)
            for label, rx in PATTERNS.items():
                self.assertIsNone(rx.search(r.text), f"{path}: unmasked {label} in the response")
            self.cache[path] = r.json()
        return self.cache[path]

    def test_endpoints_answer_clean(self):
        paths = ["/api/meta", "/api/live", "/api/tasks", "/api/results", "/api/results?case=TERR-001",
                 "/api/history?case=TERR-001&target=TA5", "/api/tickets", "/api/cases", "/api/cases/TERR-001",
                 "/api/cases/UPG-003"] + [f"/api/runs/{r}" for r in self.TERR_RUNS]
        for p in paths:
            self.get_json(p)
        meta = self.get_json("/api/meta")
        self.assertTrue({"TA5", "ET65", "W044"} <= {t["tag"] for t in meta["targets"]})
        live = self.get_json("/api/live")
        self.assertTrue({"status", "progress", "locks", "handovers", "handbacks"} <= set(live))
        self.assertIsInstance(self.get_json("/api/tasks")["tasks"], list)
        tickets = self.get_json("/api/tickets")
        self.assertTrue({"drafts", "triage", "legacy"} <= set(tickets))
        self.assertEqual(self.get_json("/api/cases/UPG-003")["case"]["id"], "UPG-003")

    def test_terr_001_results(self):
        res = self.get_json("/api/results?case=TERR-001")
        self.assertEqual([c["case"] for c in res["cases"]], ["TERR-001"])
        rows = res["cases"][0]["rows"]
        self.assertTrue(set(self.TERR_RUNS) <= {r["run"] for r in rows})
        passes = [r for r in rows if r["verdict"] == "PASS" and r["build"]["hash7"] == "84d3721"]
        self.assertEqual(len(passes), 3)
        self.assertEqual({r["target"] for r in passes}, {"TA5", "ET65", "W044"})
        self.assertTrue(res["cases"][0]["differentials"], "TERR-001 has differentials")
        for rid in self.TERR_RUNS:
            run = self.get_json(f"/api/runs/{rid}")
            self.assertEqual((run["id"], run["case"]), (rid, "TERR-001"))
            self.assertTrue(run["case_title"])
            self.assertTrue(run["steps"])
        hist = self.get_json("/api/history?case=TERR-001&target=TA5")["rows"]
        self.assertEqual([r["run"] for r in hist][:2], ["R-20261021-TA5-TERR-001-01", "R-20261021-TA5-TERR-001-02"],
                         "oldest first")

    def test_terr_001_launches(self):
        c = self.get_json("/api/results?case=TERR-001")["cases"][0]
        self.assertEqual(c["devices"], ["TA5", "ET65", "W044"])
        camp = "2026-10-21-terrain-selector-both"
        self.assertEqual([(x["campaign"], x["nn"]) for x in c["launches"]][:2], [(camp, "02"), (camp, "01")])
        x = c["launches"][0]
        self.assertEqual(x["devices_ran"], "3 of 3")
        self.assertEqual({t: (cell["run"], cell["verdict"]) for t, cell in x["cells"].items()},
                         {t: (f"R-20261021-{t}-TERR-001-02", "PASS") for t in ("TA5", "ET65", "W044")})
        conc = x["concurrency"]
        self.assertEqual(conc["kind"], "parallel launch")
        sys.stderr.write(f"\n[TERR-001 launch 02] windows={conc['windows']} overlap_s={conc['overlap_s']} "
                         f"deciding_step={conc['deciding_step']} ({conc['deciding_step_from']}) "
                         f"deciding_times={conc['deciding_times']} spread_s={conc['spread_s']} "
                         f"finished_before={conc['finished_before']}\n[TERR-001 launch 02] {conc['text']}\n")
        self.assertEqual(conc["windows"], {
            "TA5": {"start": "2026-10-21T11:49:48.990Z", "end": "2026-10-21T11:50:27.614Z"},
            "ET65": {"start": "2026-10-21T11:49:48.987Z", "end": "2026-10-21T11:50:28.398Z"},
            "W044": {"start": "2026-10-21T11:49:48.985Z", "end": "2026-10-21T11:50:22.002Z"}})
        self.assertEqual(conc["overlap_s"], 33.012)
        self.assertEqual(conc["common_window"], {"start": "2026-10-21T11:49:48.990Z", "end": "2026-10-21T11:50:22.002Z"})
        self.assertEqual((conc["deciding_step"], conc["deciding_step_from"]), (12, "prediction"))
        self.assertEqual(conc["deciding_times"], {"TA5": "2026-10-21T11:50:22.330Z", "ET65": "2026-10-21T11:50:23.193Z",
                                                  "W044": "2026-10-21T11:50:18.359Z"})
        self.assertEqual(conc["spread_s"], 4.834)
        self.assertEqual(conc["finished_before"], [{"tag": "W044", "before": "TA5", "step": 12},
                                                   {"tag": "W044", "before": "ET65", "step": 12}])
        self.assertIs(conc["simultaneous_verified"], False)
        self.assertEqual(conc["text"], "Parallel launch: windows overlap 33 s; deciding step 12 read 4.8 s apart; "
                                       "simultaneous not verified; W044 finished before TA5 and ET65 reached step 12")
        upg = {c["case"]: c for c in self.get_json("/api/results")["cases"]}["UPG-001"]
        self.assertEqual(upg["devices"], ["TA5", "ET65", "TA3"])
        self.assertEqual(upg["launches"][0]["concurrency"]["kind"], "not applicable")
        # a NOT_TESTED record is a device that was not tested, never one that ran (manifesto rule 5)
        upg5 = self.get_json("/api/results?case=UPG-005")["cases"][0]
        run01 = [x for x in upg5["launches"] if x["nn"] == "01"][0]
        self.assertEqual(run01["cells"]["TA3"]["verdict"], "NOT_TESTED")
        self.assertEqual(run01["devices_ran"], "2 of 3 (TA3 not tested, the record says NOT_TESTED)")

    def test_terr_001_evidence_is_served(self):
        rows = self.get_json("/api/results?case=TERR-001")["cases"][0]["rows"]
        thumbs = [u for r in rows for u in r["thumbs"]]
        self.assertTrue(thumbs, "TERR-001 rows have thumbnails")
        for url in thumbs[:3]:
            r = self.server.get(url)
            self.assertEqual((r.status, r.header("Content-Type")), (200, "image/png"), url)
        texts = [e["url"] for r in rows for e in r["evidence"] if e["url"] and not e["url"].endswith(".png")]
        self.assertTrue(texts, "TERR-001 rows have text evidence")
        for url in texts[:3]:
            r = self.server.get(url)
            self.assertEqual(r.status, 200, url)
            for label, rx in PATTERNS.items():
                self.assertIsNone(rx.search(r.text), f"{url}: unmasked {label}")

    def test_cases(self):
        cases = {c["id"]: c for c in self.get_json("/api/cases")["cases"]}
        for n in range(1, 18):
            self.assertIn(f"UPG-{n:03d}", cases)
        self.assertEqual(cases["TERR-001"]["latest_file"], "qa/cases/TERR-001.c2.json")
        self.assertEqual(cases["TERR-001"]["versions"], 3)
        self.assertEqual(cases["TERR-001"]["last"]["TA5"]["verdict"], "PASS")
        detail = self.get_json("/api/cases/TERR-001")
        self.assertEqual(detail["latest_file"], "qa/cases/TERR-001.c2.json")
        self.assertEqual([v["file"] for v in detail["versions"]],
                         ["qa/cases/TERR-001.json", "qa/cases/TERR-001.c1.json", "qa/cases/TERR-001.c2.json"])
        self.assertEqual(detail["case"]["id"], "TERR-001")

    def test_backtest(self):
        bt = self.get_json("/api/results")["backtest"]
        self.assertEqual(bt["runs"], 62)
        self.assertEqual(bt["runs"], bt["current"] + bt["superseded"])
        self.assertEqual(sum(bt["verdicts"].values()), bt["current"])
        self.assertTrue(bt["disagreements_md"])
        self.assertTrue(all(isinstance(v, int) for v in bt["claims"].values()))
        listed = {c["case"] for c in self.get_json("/api/results")["cases"]}
        self.assertTrue({f"UPG-{n:03d}" for n in range(1, 18)} | {"TERR-001"} <= listed)

    def test_zz_no_file_changed(self):
        # named to run last in this class; tearDownModule checks the whole test run again. This process wrote nothing
        # in the real qa/ or tests/ (the audit hook, 2026-11-05); another process may have meanwhile
        self.assertTrue(AUDIT["on"], "the audit hook watches this run")
        self.assertIsNone(real_workspace_problem(self.before, snapshot(REAL_ROOT), REAL_ROOT, AUDIT))


if __name__ == "__main__":
    unittest.main()
