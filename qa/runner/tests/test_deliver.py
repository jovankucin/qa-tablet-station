"""act.py deliver and fixtures.py (engine piece 2, 2026-10-26) against fake tablets and a fake webpc044: no adb, no
ssh, no device. Also run.py's deliver step at the start of an attempt (the test_progress stubs).

python3 -I qa/runner/tests/test_deliver.py
"""
import hashlib
import json
import os
import posixpath
import re
import shlex
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.dirname(HERE), HERE]
import act  # noqa: E402
import autonomy  # noqa: E402
import fixtures  # noqa: E402
import run  # noqa: E402
import test_progress as tp  # noqa: E402

fails = 0
checks = 0
NAME = "Z11_test_1m.tif"
DATA = b"GeoTIFF stand-in " * 1000
SHA = hashlib.sha256(DATA).hexdigest()
OTHER = b"someone else's file"
CID = "QA-901"


def check(cond, what):
    global fails, checks
    checks += 1
    if not cond:
        fails += 1
        print("FAIL", what)


class FS:
    """A target's files as the deliver commands see them, with the real semantics of a copy into a folder and of
    mv with and without -T (both move into a folder that is at the destination unless -T is given)."""

    def __init__(self, files=None, dirs=(), links=(), mv_fails=False, garbage=None):
        self.files, self.dirs, self.links = dict(files or {}), set(dirs), set(links)
        self.mv_fails, self.garbage = mv_fails, garbage

    def put(self, dest, data):
        if dest in self.dirs:  # adb push and scp put the file inside a folder that has the destination's name
            dest = posixpath.join(dest, NAME + fixtures.PARTIAL)
        self.files[dest] = data

    def run(self, cmd, t):
        m = re.fullmatch(r"if \[ -L (\S+) \]; then echo QA_LINK; elif \[ -d (\S+) \]; then echo QA_DIR; "
                         r"elif \[ -e (\S+) \]; then (.+) (\S+); else echo QA_MISSING; fi", cmd)
        if m:
            paths = {shlex.split(m.group(i))[0] for i in (1, 2, 3, 5)}
            assert len(paths) == 1 and m.group(4) == t.EXPECT_SHA, cmd
            p = paths.pop()
            if self.garbage is not None:
                return self.garbage
            if p in self.links:
                return "QA_LINK\n"
            if p in self.dirs:
                return "QA_DIR\n"
            if p in self.files:
                return f"{hashlib.sha256(self.files[p]).hexdigest()}  {p}\n"
            return "QA_MISSING\n"
        w = shlex.split(cmd)
        if w[:2] == ["mkdir", "-p"] and len(w) == 3:
            self.dirs.add(w[2])
            return ""
        if w[:2] == ["rm", "-f"] and len(w) == 3:
            assert w[2].endswith(fixtures.PARTIAL), f"deliver removed something that is not its partial copy: {cmd}"
            self.files.pop(w[2], None)
            return ""
        if w[0] == "mv" and w[-2:] and " ".join(w[:-2]) == t.EXPECT_MV:
            src, dest = w[-2], w[-1]
            if self.mv_fails:
                return ""
            if dest in self.dirs:
                if "-T" in w:
                    return ""  # toybox mv -T: refuses to move onto a folder
                dest = posixpath.join(dest, posixpath.basename(src))
            if dest not in self.files:
                self.files[dest] = self.files.pop(src)
            return ""
        raise AssertionError(f"unexpected remote command {cmd!r}")


class FakeTablet(act.Tablet):
    EXPECT_SHA, EXPECT_MV = "sha256sum", "mv -n -T"

    def __init__(self, fs=None, conn="device", corrupt=False, race=None, race_dir=None, copy_error=None):
        super().__init__("FAKE01", {"cdp_port": 1})
        self.fs, self.conn, self.corrupt, self.calls = fs or FS(), conn, corrupt, []
        self.race, self.race_dir, self.copy_error = race, race_dir, copy_error

    def adb(self, *args, timeout=20, check=False):
        self.calls.append(("adb",) + args)
        if args == ("get-state",):
            return self.conn
        raise AssertionError(f"unexpected adb call {args}")

    def connect(self):
        raise AssertionError("deliver needs no page")

    def remote(self, cmd, timeout=act.DELIVER_CMD_S):
        self.calls.append(("remote", cmd))
        return self.fs.run(cmd, self)

    def copy(self, src, dest):
        self.calls.append(("copy", src, dest))
        if self.copy_error:
            self.fs.put(dest, b"half")
            raise self.copy_error
        self.fs.put(dest, open(src, "rb").read() + (b"!" if self.corrupt else b""))
        if self.race:
            self.fs.files[self.race] = OTHER
        if self.race_dir:
            self.fs.dirs.add(self.race_dir)


class FakeWeb(act.Web):
    EXPECT_SHA, EXPECT_MV = "shasum -a 256", "mv -n"

    def __init__(self, fs=None, up=True):
        super().__init__()
        self.fs, self.up, self.calls = fs or FS(), up, []

    def reachable(self):
        self.calls.append(("reachable",))
        if not self.up:
            raise act.Fail(2, "webpc044 is not reachable over SSH: switch it on and check the network")

    def remote(self, cmd, timeout=act.DELIVER_CMD_S):
        self.calls.append(("remote", cmd))
        return self.fs.run(cmd, self)

    def copy(self, src, dest):
        self.calls.append(("copy", src, dest))
        self.fs.put(dest, open(src, "rb").read())


WS = tempfile.mkdtemp()
RWS = os.path.realpath(WS)
for d in ("tests/t1/files", "tests/t1/files/folder.tif", "qa/runner", f"qa/cases/fixtures/{CID}", "qa/cases",
          "elsewhere"):
    os.makedirs(os.path.join(WS, *d.split("/")), exist_ok=True)
for p in (f"tests/t1/files/{NAME}", f"tests/t1/{NAME}", "qa/runner/act.py", f"qa/cases/fixtures/{CID}/{NAME}",
          f"elsewhere/{NAME}"):
    with open(os.path.join(WS, *p.split("/")), "wb") as fh:
        fh.write(DATA)
os.symlink(os.path.join(WS, "tests", "t1", "files", NAME), os.path.join(WS, "tests", "t1", "files", "link.tif"))
os.symlink(os.path.join(WS, "elsewhere"), os.path.join(WS, "tests", "t1", "files", "via"))
act.WORKSPACE = WS
CASES = {}
run.load_case = lambda cid: (CASES.get(cid), os.path.join(WS, "qa", "cases", f"{cid}.c2.json") if cid in CASES else None)
DEST = f"/sdcard/Download/WingtraTerrainTests/{NAME}"
PART = DEST + fixtures.PARTIAL
SRC = os.path.join(RWS, "tests", "t1", "files", NAME)


def fx(**kw):
    f = {"name": NAME, "path": f"tests/t1/files/{NAME}", "sha256": SHA, "delivery": "engine", "device_path": DEST}
    f.update(kw)
    return {k: v for k, v in f.items() if v is not None}


def case(*fxs, status="active", targets="both"):
    CASES[CID] = {"id": CID, "status": status, "targets": targets, "fixtures": list(fxs)}
    return CID


def deliver(t, check_only=False, case_file=None):
    """(result, None) or (None, Fail)."""
    try:
        return act.deliver(t, CID, check_only, case_file), None
    except act.Fail as e:
        return None, e


def wrote(t):
    """Every call that writes: a copy, or a remote command that is not the read-only probe."""
    return [c for c in t.calls if c[0] == "copy" or (c[0] == "remote" and not c[1].startswith("if "))]


# ---- tablets
case(fx())
t = FakeTablet()
r, e = deliver(t)
check(e is None and r["fixtures"][0]["state"] == "copied", f"missing: copied ({e and e.msg})")
check(t.fs.files.get(DEST) == DATA and PART not in t.fs.files, "the file is in place, no partial left")
check(("copy", SRC, PART) in t.calls, "copied to the partial name first")
check(("remote", f"mv -n -T {PART} {DEST}") in t.calls, "moved into place with mv -n -T")
check(r and r["actions"] == [f"copied {NAME} to {DEST} ({len(DATA)} bytes, sha256 checked)"], f"actions {r and r['actions']}")
check(r and r["version_file"].endswith(f"{CID}.c2.json") and r["check_only"] is False, "version_file and check_only")
check(("remote", "mkdir -p /sdcard/Download/WingtraTerrainTests") in t.calls, "the folder is made")

t = FakeTablet(FS({DEST: DATA}))
r, e = deliver(t)
check(e is None and r["fixtures"][0]["state"] == "already there" and r["actions"] == [], "already there: nothing done")
check(wrote(t) == [], "already there: no write of any kind")

t = FakeTablet(FS({DEST: OTHER}))
r, e = deliver(t)
check(e and e.code == 2 and "different file" in e.msg and "never overwrites" in e.msg, f"different file: Fail 2 ({e and e.msg})")
check(t.fs.files[DEST] == OTHER and wrote(t) == [], "different file: untouched, nothing written")
check(e and e.extra["deliver"]["fixtures"] == [] and e.extra["deliver"]["actions"] == [], "the failure carries deliver")

for kind, fs in (("folder", FS(dirs={DEST})), ("link", FS(links={DEST}))):
    t = FakeTablet(fs)
    r, e = deliver(t)
    check(e and e.code == 2 and f"a {kind} is at" in e.msg and wrote(t) == [], f"a {kind} at the path: Fail 2, nothing written")

t = FakeTablet()
r, e = deliver(t, check_only=True)
check(e is None and r["fixtures"][0]["state"].startswith("missing") and wrote(t) == [], "--check: reports missing, writes nothing")
t = FakeTablet(FS({DEST: OTHER}))
r, e = deliver(t, check_only=True)
check(e and e.code == 2 and wrote(t) == [], "--check: a different file is Fail 2 too")

t = FakeTablet(corrupt=True)
r, e = deliver(t)
check(e and e.code == 3 and "does not match" in e.msg, f"a corrupt copy: Fail 3 ({e and e.msg})")
check(DEST not in t.fs.files and PART not in t.fs.files, "a corrupt copy: removed, nothing in place")

t = FakeTablet(race=DEST)
r, e = deliver(t)
check(e and e.code == 2 and "appeared" in e.msg, f"a file appears during the copy: Fail 2 ({e and e.msg})")
check(t.fs.files.get(DEST) == OTHER and PART not in t.fs.files, "the other file is kept, the partial removed")

t = FakeTablet(race_dir=DEST)
r, e = deliver(t)
check(e and e.code == 2 and "a folder is at" in e.msg, f"a folder appears during the copy: Fail 2 ({e and e.msg})")
check(not any(p.startswith(DEST + "/") for p in t.fs.files) and PART not in t.fs.files,
      "mv -T: nothing moved into the person's folder, the partial removed")

t = FakeTablet(FS(mv_fails=True))
r, e = deliver(t)
check(e and e.code == 3 and "moving" in e.msg and PART not in t.fs.files, "a failed move: Fail 3, partial removed")

t = FakeTablet(FS({PART: b"half"}))
r, e = deliver(t)
check(e is None and t.fs.files.get(DEST) == DATA, "an earlier partial copy is replaced")
t = FakeTablet(FS(dirs={PART}))
r, e = deliver(t)
check(e and e.code == 2 and "folder" in e.msg and not [c for c in t.calls if c[0] == "copy"],
      "a folder at the partial name: Fail 2 before any copy")
check(not any(p.startswith(PART + "/") for p in t.fs.files), "nothing copied into a folder at the partial name")

t = FakeTablet(FS(garbage="sha256sum: Permission denied\n"))
r, e = deliver(t)
check(e and e.code == 3 and "could not read the sha256" in e.msg and wrote(t) == [], "an unreadable checksum: Fail 3")
t = FakeTablet(FS(garbage="ABC  " + DEST + "\n"))
r, e = deliver(t)
check(e and e.code == 3, "a checksum that is not 64 lowercase hex: Fail 3")

for err in (subprocess.TimeoutExpired(["adb", "push"], 600), OSError("adb gone")):
    t = FakeTablet(copy_error=err)
    r, e = deliver(t)
    check(e and e.code == 3 and type(err).__name__ in e.msg and "deliver" in e.extra, f"{type(err).__name__} in the copy: Fail 3 with deliver")
    check(PART not in t.fs.files and DEST not in t.fs.files, f"{type(err).__name__}: the partial copy is removed")
case(fx(), fx(device_path=f"/sdcard/Download/Other/{NAME}"))
t = FakeTablet()
real_copy = t.copy


def second_copy_times_out(src, dest, _n=[0]):
    _n[0] += 1
    if _n[0] == 2:
        raise subprocess.TimeoutExpired(["adb", "push"], 600)
    return real_copy(src, dest)


t.copy = second_copy_times_out
r, e = deliver(t)
check(e and e.code == 3 and len(e.extra["deliver"]["actions"]) == 1 and e.extra["deliver"]["fixtures"][0]["state"] == "copied",
      "a timeout on the second file keeps what was copied before it")

case(fx())
t = FakeTablet(conn="unauthorized")
r, e = deliver(t)
check(e and e.code == 2 and "not reachable over USB" in e.msg and t.calls == [("adb", "get-state")], "unreachable: Fail 2, no command")

case(fx(device_path=None))
t = FakeTablet()
r, e = deliver(t)
check(e is None and f"/sdcard/Download/WingtraQA/{NAME}" in t.fs.files, "no device_path: /sdcard/Download/WingtraQA/<name>")

case(fx(path=f"qa/cases/fixtures/{CID}/{NAME}"))
r, e = deliver(FakeTablet())
check(e is None, f"a source under qa/cases/fixtures/<CASE>/ is allowed ({e and e.msg})")

for bad, words in (({"sha256": "0" * 64}, "does not match the case"), ({"path": "qa/runner/act.py", "name": "act.py"}, "outside"),
                   ({"path": f"tests/t1/{NAME}"}, "outside"),
                   ({"path": "tests/t1/files/link.tif", "name": "link.tif", "device_path": None}, "symbolic link"),
                   ({"path": f"tests/t1/files/via/{NAME}"}, "symbolic link"),
                   ({"path": "tests/t1/files/gone.tif", "name": "gone.tif", "device_path": None}, "not on this Mac"),
                   ({"path": "tests/t1/files/folder.tif", "name": "folder.tif", "device_path": None}, "not a plain file"),
                   ({"path": f"tests/t1/files/../files/{NAME}"}, "plain path"), ({"path": f"/{NAME}"}, "plain path"),
                   ({"name": "a b.tif"}, "plain file name"), ({"name": "x.tif"}, "another file name"),
                   ({"name": NAME + fixtures.PARTIAL}, "plain file name"),
                   ({"device_path": f"/sdcard/Android/data/x/{NAME}"}, "device_path"),
                   ({"device_path": f"/sdcard/Download/../DCIM/{NAME}"}, "device_path"),
                   ({"device_path": "/sdcard/Download/WingtraQA/other.tif"}, "device_path"),
                   ({"device_path": f"/sdcard/Download//{NAME}"}, "device_path"),
                   ({"sha256": "ABC"}, "64 lowercase hex")):
    case(fx(**bad))
    t = FakeTablet()
    r, e = deliver(t)
    check(e and e.code == 2 and words in e.msg and t.calls == [], f"{bad}: Fail 2 '{words}' before any device call ({e and e.msg})")
real_max = fixtures.MAX_BYTES
fixtures.MAX_BYTES = len(DATA) - 1
case(fx())
r, e = deliver(FakeTablet())
check(e and e.code == 2 and "larger than" in e.msg, "a source over the size limit: Fail 2")
fixtures.MAX_BYTES = real_max

case(fx(), fx(device_path=f"/sdcard/Download/Other/{NAME}"))
t = FakeTablet()
r, e = deliver(t)
check(e is None and [f["state"] for f in r["fixtures"]] == ["copied", "copied"], "two fixtures, two places")
t = FakeTablet(FS({f"/sdcard/Download/Other/{NAME}": OTHER}))
r, e = deliver(t)
check(e and e.code == 2 and len(e.extra["deliver"]["actions"]) == 1 and e.extra["deliver"]["fixtures"][0]["state"] == "copied",
      "a later conflict keeps what was done before it")
case(fx(), fx(sha256="1" * 64))
t = FakeTablet()
r, e = deliver(t)
check(e and e.code == 2 and "two fixtures" in e.msg and t.calls == [], "two files for one place: Fail 2")
case(fx(), fx())
r, e = deliver(FakeTablet())
check(e is None and len(r["fixtures"]) == 1, "the same file pinned twice is delivered once")
case(fx(), fx(path=f"qa/cases/fixtures/{CID}/{NAME}", sha256="1" * 64, device_path=f"/sdcard/Download/Other/{NAME}"),
     targets="tablet")
try:
    plan = fixtures.plan(CASES[CID], WS, digest=False, platforms=("tablet",))
    check(len(plan) == 2, "tablet-only: two files with one name in two tablet folders are fine")
except fixtures.FixtureError as x:
    check(False, f"tablet-only plan refused a W044-only clash: {x}")
try:
    fixtures.plan(CASES[CID], WS, digest=False, platforms=("tablet", "web"))
    check(False, "both: the clash on ~/qa-fixtures/<name> was not found")
except fixtures.FixtureError as x:
    check("qa-fixtures" in str(x), "both: two different files for ~/qa-fixtures/<name> are refused")

case()
t = FakeTablet()
r, e = deliver(t)
check(e is None and r["fixtures"] == [] and t.calls == [], "no fixtures: nothing, not even a reachability check")
case(fx(), status="retired")
r, e = deliver(FakeTablet())
check(e and e.code == 2 and "retired" in e.msg, "a retired case: Fail 2")
for bad_id, words in (("../x", "case id"), ("", "case id"), ("qa-901", "case id"), ("QA-9011", "case id"),
                      ("QA-902", "no case")):
    try:
        act.deliver(FakeTablet(), bad_id, False)
        check(False, f"case id {bad_id!r} accepted")
    except act.Fail as x:
        check(x.code == 4 and words in x.msg, f"case id {bad_id!r}: Fail 4 '{words}' ({x.msg})")
CASES["QA-903"] = {"id": "QA-901", "status": "active", "targets": "both", "fixtures": [fx()]}
try:
    act.deliver(FakeTablet(), "QA-903", False)
    check(False, "a record whose id is another case was used")
except act.Fail as x:
    check(x.code == 4, "a record whose id is another case: Fail 4")

case(fx(), targets="web")
t = FakeTablet()
r, e = deliver(t)
check(e and e.code == 4 and "gets no test files" in e.msg and t.calls == [], "a web case: a tablet gets nothing")
case(fx(), targets="tablet")
w = FakeWeb()
r, e = deliver(w)
check(e and e.code == 4 and w.calls == [], "a tablet case: W044 gets nothing")

# --case-file: the version run.py pins, not the newest
c1 = {"id": CID, "status": "active", "targets": "tablet", "fixtures": [fx(device_path=f"/sdcard/Download/Old/{NAME}")]}
with open(os.path.join(WS, "qa", "cases", f"{CID}.c1.json"), "w") as fh:
    json.dump(c1, fh)
case(fx())
t = FakeTablet()
r, e = deliver(t, case_file=f"qa/cases/{CID}.c1.json")
check(e is None and f"/sdcard/Download/Old/{NAME}" in t.fs.files and r["version_file"].endswith(f"{CID}.c1.json"),
      f"--case-file delivers that version ({e and e.msg})")
for bad_file in (f"qa/cases/QA-902.c1.json", "qa/secrets.env", f"qa/cases/../cases/{CID}.c1.json", f"/tmp/{CID}.json"):
    r, e = deliver(FakeTablet(), case_file=bad_file)
    check(e and e.code == 4, f"--case-file {bad_file}: Fail 4")
r, e = deliver(FakeTablet(), case_file=f"qa/cases/{CID}.c7.json")
check(e and e.code == 4 and "no case" in e.msg, "--case-file that does not exist: Fail 4")

# ---- webpc044
case(fx())
w = FakeWeb()
r, e = deliver(w)
check(e is None and w.fs.files.get(f"qa-fixtures/{NAME}") == DATA, f"W044: copied into ~/qa-fixtures ({e and e.msg})")
check(r and r["fixtures"][0]["dest"] == f"~/qa-fixtures/{NAME}" and ("copy", SRC, f"qa-fixtures/{NAME}{fixtures.PARTIAL}") in w.calls
      and ("remote", f"mv -n qa-fixtures/{NAME}{fixtures.PARTIAL} qa-fixtures/{NAME}") in w.calls,
      "W044: shown under ~, copied to the partial name, moved with mv -n")
w = FakeWeb(FS({f"qa-fixtures/{NAME}": OTHER}))
r, e = deliver(w)
check(e and e.code == 2 and "~/qa-fixtures" in e.msg, "W044: a different file is never overwritten")
w = FakeWeb(up=False)
r, e = deliver(w)
check(e and e.code == 2 and "SSH" in e.msg and w.calls == [("reachable",)], "W044 unreachable: Fail 2")

# ---- the real adb, ssh and scp commands
check(act.Tablet.SHA_CMD == "sha256sum" and act.Tablet.MV_CMD == "mv -n -T", "tablet tools: toybox sha256sum, mv -n -T")
check(act.Web.SHA_CMD == "shasum -a 256" and act.Web.MV_CMD == "mv -n", "W044 tools: shasum -a 256, mv -n")
check(run.DELIVER_PER_FILE_S >= 4 * act.DELIVER_SUM_S + act.DELIVER_COPY_S + 3 * act.DELIVER_CMD_S,
      "run.py's per-file deliver timeout covers act.py's worst case")
seen = []
real_sh = act.sh
answer = {"echo ok": "ok\n"}
act.sh = lambda argv, timeout=20, check=False: seen.append((argv, timeout, check)) or answer.get(argv[-1], "")
try:
    tb = act.Tablet("SER1", {"cdp_port": 1})
    tb.copy("/m/a.tif", "/sdcard/Download/Q/a.tif.qa-partial")
    check(seen[-1] == (["adb", "-s", "SER1", "push", "/m/a.tif", "/sdcard/Download/Q/a.tif.qa-partial"], act.DELIVER_COPY_S, True),
          f"tablet copy argv {seen[-1]}")
    tb.remote("mkdir -p /sdcard/Download/Q")
    check(seen[-1] == (["adb", "-s", "SER1", "shell", "mkdir -p /sdcard/Download/Q"], act.DELIVER_CMD_S, False),
          f"tablet remote argv {seen[-1]}")
    try:
        act.remote_sha(tb, "/sdcard/Download/Q/a.tif")
    except act.Fail:
        pass  # no answer from the stand-in: only the call is checked here
    check(seen[-1][1] == act.DELIVER_SUM_S and seen[-1][0][:4] == ["adb", "-s", "SER1", "shell"],
          "a checksum is one adb shell call with the checksum timeout")
    wb = act.Web()
    wb.copy("/m/a.tif", "qa-fixtures/a.tif.qa-partial")
    check(seen[-1] == (["scp", "-q", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "/m/a.tif",
                        "webpc044:qa-fixtures/a.tif.qa-partial"], act.DELIVER_COPY_S, True), f"scp argv {seen[-1]}")
    wb.remote("mkdir -p qa-fixtures")
    check(seen[-1][0] == ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "webpc044", "mkdir -p qa-fixtures"],
          f"ssh argv {seen[-1]}")
    wb.reachable()
    check(seen[-1][0][-1] == "echo ok", "W044 reachability is an echo")
    answer["echo ok"] = ""
    try:
        wb.reachable()
        check(False, "W044 without an answer counted as reachable")
    except act.Fail as x:
        check(x.code == 2 and "SSH" in x.msg, "W044 without an answer: Fail 2")
finally:
    act.sh = real_sh

# ---- act.py: parser and dispatch (no page, no connect)
a = act.parser().parse_args(["TA5", "deliver", "TERR-004", "--check", "--case-file", "qa/cases/TERR-004.c2.json"])
check(a.verb == "deliver" and a.selector == "TERR-004" and a.check and a.case_file == "qa/cases/TERR-004.c2.json",
      "act.py parses deliver --check --case-file")
for abbrev in ("--ch", "--case"):
    try:
        act.parser().parse_args(["TA5", "deliver", "TERR-004", abbrev] + (["x"] if abbrev == "--case" else []))
        check(False, f"{abbrev} accepted as an abbreviation")
    except SystemExit:
        pass
fake = FakeTablet()
real = act.resolve_target, act.check_lock, act.load_profiles
act.resolve_target, act.check_lock, act.load_profiles = (lambda name, p: fake), (lambda name: None), (lambda: {})
try:
    case(fx())
    res = act.run(act.parser().parse_args(["FAKE01", "deliver", CID, "--check"]))
    check(res["deliver"]["fixtures"][0]["state"].startswith("missing") and wrote(fake) == [] and "identity" not in res,
          "run(): deliver --check reads only, without connect")
    res = act.run(act.parser().parse_args(["FAKE01", "deliver", CID]))
    check(res["deliver"]["fixtures"][0]["state"] == "copied", "run(): deliver copies, without connect")
finally:
    act.resolve_target, act.check_lock, act.load_profiles = real

# ---- fixtures.py, the case schema and the hands-off check
check(fixtures.tablet_dest({"name": "a.tif"}) == "/sdcard/Download/WingtraQA/a.tif", "default tablet path")
check(fixtures.web_dest({"name": "a.tif"}) == "qa-fixtures/a.tif", "W044 path")
five = "/sdcard/Download/" + "/".join("abcde") + "/a.tif"
six = "/sdcard/Download/" + "/".join("abcdef") + "/a.tif"
check(fixtures.tablet_dest({"name": "a.tif", "device_path": five}) == five, "a device path 5 folders deep is fine")
try:
    fixtures.tablet_dest({"name": "a.tif", "device_path": six})
    check(False, "a device path 6 folders deep accepted")
except fixtures.FixtureError:
    pass
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "app"))
import case_schema  # noqa: E402
long_folder = "/sdcard/Download/" + "x" * 160 + "/a.tif"
for dp in (five, six, long_folder, "/sdcard/Download/a.tif", "/sdcard/Download/.x/a.tif"):
    p = []
    case_schema._check_fixtures([{"name": "a.tif", "path": "tests/x/files/a.tif", "sha256": "0" * 64, "device_path": dp}], p)
    try:
        fixtures.tablet_dest({"name": "a.tif", "device_path": dp})
        engine_ok = True
    except fixtures.FixtureError:
        engine_ok = False
    check((p == []) == engine_ok, f"case schema and engine agree on {dp[:60]} (schema {p == []}, engine {engine_ok})")

ENG = {"env": "staging", "test_data": {"site": {"id": "S1", "env": "staging"}, "mock_drone": {"vehicle_id": 99900}}}


def auto_case(*fxs, targets="tablet"):
    return {"id": "X-001", "kind": "case", "status": "active", "targets": targets, "needs": {}, "fixtures": list(fxs),
            "steps": [{"n": 1, "text": "read", "gates": [], "read_js": "location.pathname"}]}


ok = autonomy.check(auto_case(fx()), ENG, root=WS)
check(ok["hands_off"] and "fixture_free" in [c["code"] for c in ok["checks"]] and any("copies" in n for n in ok["notes"]),
      f"autonomy: a good fixture is a note and a device check ({ok['blockers']})")
for bad_fx, what in ((fx(path="tests/t1/files/gone.tif", name="gone.tif", device_path=None), "a missing source"),
                     (fx(device_path="/sdcard/DCIM/" + NAME), "a device_path outside Download")):
    bad = autonomy.check(auto_case(bad_fx), ENG, root=WS)
    check(not bad["hands_off"] and [b["code"] for b in bad["blockers"]] == ["fixture_source"]
          and "fixture_free" not in [c["code"] for c in bad["checks"]] and not any("copies" in n for n in bad["notes"]),
          f"autonomy: {what} blocks, with no copy note")
clash = auto_case(fx(), fx(path=f"qa/cases/fixtures/{CID}/{NAME}", sha256="1" * 64, device_path=f"/sdcard/Download/Other/{NAME}"),
                  targets="both")
check([b["code"] for b in autonomy.check(clash, ENG, root=WS)["blockers"]] == ["fixture_source"],
      "autonomy: two files for one place on W044 block a case that runs there")
clash["targets"] = "tablet"
check(autonomy.check(clash, ENG, root=WS)["hands_off"], "autonomy: the same names do not clash on tablets only")
odd = autonomy.check(auto_case("not a fixture"), ENG, root=WS)
check([b["code"] for b in odd["blockers"]] == ["fixture_source"], "autonomy: a fixture that is not an object blocks, no crash")
off = autonomy.check(auto_case(fx()), ENG, can={"deliver_fixtures": False}, root=WS)
check("fixture_on_device" in [c["code"] for c in off["checks"]], "autonomy: with delivery off it is a device check again")
with open(os.path.join(WS, "qa", "cases", "X-001.json"), "w") as fh:
    json.dump(auto_case(fx(path="tests/t1/files/gone.tif", name="gone.tif", device_path=None)), fh)
check([b["code"] for b in autonomy.check_id("X-001", ENG, root=WS)["blockers"]] == ["fixture_source"],
      "autonomy.check_id passes its root on to the test-file check")

# ---- run.py: the deliver step at the start of a tablet attempt


class Dev(tp.Device):
    def __init__(self, deliver_out=None, ready_out=None):
        super().__init__()
        self.deliver_out, self.ready_out, self.timeouts, self.args = deliver_out, ready_out, {}, {}

    def __call__(self, target, args, run_id, timeout=90, env=None):
        self.timeouts[args[0]], self.args[args[0]] = timeout, list(args)
        if args[0] == "deliver":
            self.seq += 1
            self.seen.append(("deliver", args[1], None))
            return dict(self.deliver_out, ledger={"day": "2026-10-26", "seq": self.seq})
        if args[0] == "ready" and self.ready_out:
            self.seq += 1
            self.seen.append(("ready", None, None))
            return dict(self.ready_out, ledger={"day": "2026-10-26", "seq": self.seq})
        return super().__call__(target, args, run_id, timeout, env)


def attempt(tmp, deliver_out, n_fixtures=1, ready_out=None):
    records = []
    tp.stub_module(records)
    run.PROGRESS = os.path.join(tmp, "progress")
    r, _ = tp.make_run(tmp)
    if n_fixtures:
        r.case["fixtures"] = [fx(device_path=f"/sdcard/Download/F{i}/{NAME}") for i in range(n_fixtures)]
    dev = Dev(deliver_out, ready_out)
    run.act = dev
    r.run()
    return r, dev, (records[-1] if records else {})


with tempfile.TemporaryDirectory() as tmp:
    good = {"ok": True, "deliver": {"version_file": run.rel(os.path.join(tmp, "QA-900.json")),
                                    "fixtures": [{"name": NAME, "state": "copied"}], "actions": [f"copied {NAME} to {DEST}"]}}
    r, dev, rec = attempt(tmp, good)
    verbs = [v for v, _, _ in dev.seen]
    check(rec.get("verdict", {}).get("v") == "PASS", f"delivered: the run goes on to PASS ({rec.get('verdict')})")
    check(verbs.index("ready") < verbs.index("deliver") < verbs.index("probe"), f"order ready, deliver, probe: {verbs[:4]}")
    check(dev.args["deliver"] == ["deliver", "QA-900", "--case-file", run.rel(r.case_path)], f"deliver args {dev.args['deliver']}")
    idn = [i for i in rec["attempts"][0]["identity"] if i["verb"] == "deliver"]
    check(idn and idn[0]["fixtures"][0]["state"] == "copied" and idn[0]["actions"], "the run record keeps what deliver did")
    check(dev.timeouts["deliver"] == 120 + run.DELIVER_PER_FILE_S, f"deliver timeout {dev.timeouts['deliver']}")
    r, dev, rec = attempt(tmp, good, n_fixtures=3)
    check(dev.timeouts["deliver"] == 120 + 3 * run.DELIVER_PER_FILE_S, "the deliver timeout grows with the number of files")

    r, dev, rec = attempt(tmp, {"ok": False, "code": 2, "error": f"a different file is already at {DEST} on FAKE01"})
    v = rec.get("verdict", {})
    check(v.get("v") == "BLOCKED" and v.get("by", {}).get("kind") == "precondition" and v["by"]["ref"].startswith("test file:"),
          f"a conflict blocks the run ({v})")
    check("tap" not in [x for x, _, _ in dev.seen], "a blocked run taps nothing")

    r, dev, rec = attempt(tmp, {"ok": False, "code": 3, "error": "adb push failed"})
    v = rec.get("verdict", {})
    check(v.get("v") == "ERROR" and v.get("harness", {}).get("component") == "act.py deliver", f"a copy failure is ERROR ({v})")
    check("tap" not in [x for x, _, _ in dev.seen], "an ERROR run taps nothing")

    r, dev, rec = attempt(tmp, dict(good, deliver=dict(good["deliver"], version_file="qa/cases/QA-900.c9.json")))
    check(rec.get("verdict", {}).get("v") == "ERROR", "files of another case version: ERROR")

    r, dev, rec = attempt(tmp, good, n_fixtures=0)
    check("deliver" not in [x for x, _, _ in dev.seen] and rec["verdict"]["v"] == "PASS", "no fixtures: no deliver call")

    r, dev, rec = attempt(tmp, good, ready_out={"ok": False, "code": 2, "error": "PIN"})
    check("deliver" not in [x for x, _, _ in dev.seen] and rec["verdict"]["v"] == "BLOCKED", "not ready: no deliver call")

print(f"act.py deliver: {checks} checks, {fails} failures")
sys.exit(1 if fails else 0)
