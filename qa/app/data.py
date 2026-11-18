"""Read model of the QA app (qa/app/DESIGN.md sections 3 and 6).

Data(root) answers every GET of the app's read API from the files under qa/ and tests/: run, case,
differential and claim records, device profiles, tasks, the runner's state folder and ticket drafts.
Pure reads: nothing here writes a file, and git (read-only, offline, argv list) is the only subprocess.

Parsed JSON is cached per (path, mtime_ns, size), and the run and case indexes are rebuilt only when a
record file changes, so polling /api/live every 3 s and /api/results stays cheap. The ledger is read only for the
time `t` of an action line (launch concurrency), through a {seq: t} index per day file kept while its size is
unchanged. Methods return plain dicts; serve.py redacts and serialises them. Unknown ids raise KeyError.
"""
import collections
import datetime
import fcntl
import glob
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import threading
import time
import urllib.parse

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)
sys.path.insert(0, os.path.join(os.path.dirname(APP_DIR), "runner"))

import autonomy  # noqa: E402  the hands-off check (engine code)
import draft_check  # noqa: E402  js_trim, utf16_len, TITLE_RE, HEADINGS, check_headings; never check() (it can write)
import records  # noqa: E402
import security  # noqa: E402  the /files/ rules, so a listed URL is always one the route serves

AREAS_FALLBACK = ("UPG", "PLAN", "FLY", "PLANE", "DEV", "SAK", "MAP", "SYNC", "OFFL", "TERR", "FLEET", "SET", "LTA",
                  "PROC")
IMAGE_EXT = frozenset(security.IMAGE_TYPES)
TIPS = {"staging": "origin/sky-staging", "develop": "origin/sky", "preproduction": "origin/sky-preproduction",
        "production": "origin/sky-production"}
GIT_ENV = {"GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}
GIT_TIMEOUT = 10
TIP_TTL_S = 60
HANDBACK_DAYS = 7
THUMBS = 4
CHANGED_PATHS = 5
TEXT_READ_CAP = 256 * 1024

# launches (QA practice in CLAUDE.md): the three devices of a case, and what an absent device's cell says
DEVICES_BOTH = ("TA5", "ET65", "W044")
DEVICES_TABLET = ("TA5", "ET65", "TA3")
WEB_TAG = "W044"
NOT_RUN_WHY = "no run in this launch"
NOT_ROUTED_WHY = "not routed (web-only case)"

RUN_ID_RE = re.compile(r"^R-(\d{8})-([A-Za-z0-9]+)-(.+)-(\d{2,})(?:c\d+)?\Z", re.A)
CORRECTION_RE = re.compile(r"c\d+\Z", re.A)
PRED_ID_RE = re.compile(r"^PR-[A-Za-z0-9][A-Za-z0-9_-]*\Z", re.A)  # no dot, no slash: a plain file name
LEDGER_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\Z", re.A)
CASE_FILE_RE = re.compile(r"^([A-Z][A-Z0-9]{1,11}-\d{3,})(?:\.c(\d+))?\.json\Z", re.A)
TASK_FILE_RE = re.compile(r"^(T-\d{8}-\d{3})\.json\Z", re.A)
SLUG_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z0-9-]+\Z", re.A)
PACK_RE = re.compile(r"^tests/(\d{4}-\d{2}-\d{2}-[a-z0-9-]+)/tickets/(\d+)/pack/.+", re.A)
LEGACY_RE = re.compile(r"^ticket([0-9]+)_(.+)\Z", re.A)
TICKET_N_RE = re.compile(r"^[0-9]+\Z", re.A)
COMMENT_RE = re.compile(r"^comment_(.+)\.md\Z", re.A)
HEX_RE = re.compile(r"^[0-9a-f]{7,40}\Z", re.A)
HASH7_IN_LABEL = re.compile(r"-([0-9a-f]{7})\Z", re.A)


def val(x):
    """The value of a sourced value {"v": ..., "st": ...}; anything else as it is."""
    return x["v"] if isinstance(x, dict) and "v" in x else x


def now_iso():
    return records.now_utc()


def parse_time(s):
    if not isinstance(s, str) or not s:
        return None
    try:
        t = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=datetime.timezone.utc)


def age_s(s, fallback_mtime=None):
    t = parse_time(s)
    ts = t.timestamp() if t else fallback_mtime
    return None if ts is None else max(0, int(time.time() - ts))


def iso_mtime(path):
    try:
        ts = os.stat(path).st_mtime
    except OSError:
        return None
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z")


def sup_id(s):
    """The id a record supersedes: a plain id or {id, file, file_sha256, why}."""
    if isinstance(s, str):
        return s or None
    if isinstance(s, dict):
        return s.get("id") if isinstance(s.get("id"), str) else None
    return None


def verdict_of(rec):
    v = rec.get("verdict")
    if isinstance(v, dict):
        return val(v.get("v"))
    return v if isinstance(v, str) else None


def scan(dirpath, prefix="", suffix=".json"):
    """[(path, stat)] of regular, non-hidden files in one folder; symlinks are skipped."""
    out = []
    try:
        with os.scandir(dirpath) as it:
            for e in it:
                if e.name.startswith(".") or not e.name.startswith(prefix) or not e.name.endswith(suffix):
                    continue
                try:
                    if e.is_file(follow_symlinks=False):
                        out.append((e.path, e.stat(follow_symlinks=False)))
                except OSError:
                    continue
    except OSError:
        pass
    out.sort(key=lambda x: x[0])
    return out


def subdirs(dirpath):
    """Non-hidden subfolders of one folder; a symlinked folder is skipped (it may lead out of the workspace)."""
    out = []
    try:
        with os.scandir(dirpath) as it:
            for e in it:
                try:
                    if not e.name.startswith(".") and e.is_dir(follow_symlinks=False):
                        out.append(e.name)
                except OSError:
                    continue
    except OSError:
        pass
    return sorted(out)


def signature(files):
    return tuple((p, st.st_mtime_ns, st.st_size) for p, st in files)


def lock_held(path):
    """True while a process holds the flock on a runner lock file (run.py's Lock), probed like act.check_lock with a
    non-blocking shared flock. A missing file is never held: run.py creates <name>.lock before <name>.json."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return False
    except OSError:
        return True  # cannot be probed: shown as held, as before
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except OSError:  # BlockingIOError: a run holds it
        return True
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def evidence_path(e):
    """The file a run-level evidence entry names: its path, or a file pointer's path."""
    ptr = e.get("ptr") if isinstance(e.get("ptr"), dict) else {}
    p = e.get("path") or (ptr.get("path") if ptr.get("t") == "file" else None)
    return p if isinstance(p, str) else None


def run_level_images(rec):
    """Image paths in a run record's own evidence list (back-test and live-recorded runs keep shots there)."""
    out = []
    for e in rec.get("evidence") or []:
        p = evidence_path(e) if isinstance(e, dict) else None
        if p and os.path.splitext(p)[1].lower() in IMAGE_EXT:
            out.append(p)
    return out


def is_int(x):
    return isinstance(x, int) and not isinstance(x, bool)


def first_attempt(rec):
    att = rec.get("attempts") or []
    return att[0] if isinstance(att, list) and att and isinstance(att[0], dict) else {}


def fmt_s(x):
    """Seconds as the concurrency line writes them: whole seconds from 10 s up, else one decimal."""
    return f"{x:.0f} s" if abs(x) >= 10 else f"{x:.1f} s"


def join_and(items):
    items = list(items)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def hands_off(rec, root=None):
    """The hands-off verdict of a case record (qa/runner/autonomy.py): {ok, blockers, checks, notes}. root: the
    workspace whose test files are checked."""
    try:
        r = autonomy.check(rec, root=root)
    except Exception as e:  # noqa: BLE001 (a case the check cannot read is not hands-off)
        return {"ok": False, "blockers": [{"code": "unreadable", "step": None, "text": type(e).__name__,
                                           "fix": "the person"}], "checks": [], "notes": []}
    return {"ok": r["hands_off"], "blockers": r["blockers"], "checks": [c["text"] for c in r["checks"]],
            "notes": r["notes"]}


def newest(rid, succ):
    """Follow superseded_by links to the newest record (cycle-safe)."""
    seen = {rid}
    while rid in succ and succ[rid] not in seen:
        rid = succ[rid]
        seen.add(rid)
    return rid


def successors(items):
    """{old id: newest direct successor id} from the supersedes field of each record."""
    succ = {}
    for rid, it in items.items():
        old = sup_id(it["rec"].get("supersedes"))
        if old and old != rid and old in items:
            prev = succ.get(old)
            if prev is None or (it["rec"].get("created") or "") > (items[prev]["rec"].get("created") or ""):
                succ[old] = rid
    return succ


class Data:
    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.qa = os.path.join(self.root, "qa")
        self.state = os.path.join(self.qa, "runner", "state")
        self.repo = os.path.join(self.root, "WingtraCloud")
        self._json_cache = {}
        self._text_cache = {}
        self._sha_cache = {}
        self._lock = threading.RLock()
        self._runs_sig, self._runs_idx = None, None
        self._cases_sig, self._cases_idx = None, None
        self._claims = (None, None)  # (signature, counts), swapped as one tuple
        self._git_cache = {}
        self._tips = {}
        self._ledger_idx = {}  # day -> {size, ino, offset, t: {seq: t}}, rebuilt when the file size changes

    # ---- file access -------------------------------------------------------------------------------

    def _rel(self, path):
        return os.path.relpath(path, self.root).replace(os.sep, "/")

    def _abs(self, rel):
        return os.path.join(self.root, *rel.split("/"))

    def _json(self, path, st=None):
        """Parsed JSON of a file, cached per (mtime_ns, size); None when missing, a symlink or not JSON."""
        try:
            st = st or os.lstat(path)
        except OSError:
            return None
        if not stat.S_ISREG(st.st_mode):
            return None
        key = (st.st_mtime_ns, st.st_size)
        hit = self._json_cache.get(path)
        if hit and hit[0] == key:
            return hit[1]
        try:
            with open(path, "rb") as f:
                obj = json.loads(f.read().decode("utf-8"))
        except (OSError, ValueError):
            obj = None
        self._json_cache[path] = (key, obj)
        return obj

    def _text(self, path):
        """UTF-8 text (errors replaced, capped), cached like _json; None when missing or a symlink."""
        try:
            st = os.lstat(path)
        except OSError:
            return None
        if not stat.S_ISREG(st.st_mode):
            return None
        key = (st.st_mtime_ns, st.st_size)
        hit = self._text_cache.get(path)
        if hit and hit[0] == key:
            return hit[1]
        try:
            with open(path, "rb") as f:
                text = f.read(TEXT_READ_CAP).decode("utf-8", errors="replace")
        except OSError:
            text = None
        self._text_cache[path] = (key, text)
        return text

    def _sha256(self, path, st):
        key = (path, st.st_mtime_ns, st.st_size)
        if key not in self._sha_cache:
            self._sha_cache[key] = records.sha256_file(path)
        return self._sha_cache[key]

    def _engine(self):
        return self._json(os.path.join(self.qa, "runner", "engine.json")) or {}

    # ---- /files/ policy ----------------------------------------------------------------------------

    @staticmethod
    def _clean_rel(rel):
        """rel when it is a plain workspace-relative path (no .., no dot segment, no backslash), else None."""
        if not isinstance(rel, str) or not rel or "\x00" in rel or "\\" in rel or rel.startswith("/"):
            return None
        parts = rel.split("/")
        if any(p in ("", "..") or p.startswith(".") for p in parts):
            return None
        return rel

    @staticmethod
    def _inside(path, top):
        real = os.path.realpath(path)
        base = os.path.realpath(top)
        return real == base or real.startswith(base + os.sep)

    def image_allowed(self, rel):
        """True for images a run record names as evidence (attempt shots or the run's own evidence list), the
        last_shot of a runner progress file (a run in progress has no record yet) and files in a ticket's pack/."""
        return self._image_allowed(rel, self._runs()["shots"] | self._progress_shots())

    def _progress_shots(self):
        out = set()
        for _, _, obj in self._state_dir("progress"):
            if isinstance(obj.get("last_shot"), str):
                out.add(obj["last_shot"])
        return out

    def _image_allowed(self, rel, shots):
        rel = self._clean_rel(rel)
        if not rel or not rel.startswith("tests/") or os.path.splitext(rel)[1].lower() not in IMAGE_EXT:
            return False
        full = self._abs(rel)
        if not self._inside(full, os.path.join(self.root, "tests")):
            return False
        if rel in shots:
            return True
        m = PACK_RE.match(rel)
        if m:
            pack = os.path.join(self.root, "tests", m.group(1), "tickets", m.group(2), "pack")
            return os.path.isdir(pack) and self._inside(full, pack)
        return False

    def file_url(self, rel):
        """"/files/<url-quoted rel>" for an existing file the /files/ route would serve, else None."""
        return self._file_url(rel, None)

    def _file_url(self, rel, shots):
        """The /files/ URL when security.resolve_read (the rule the route itself applies) would serve rel."""
        rel = self._clean_rel(rel)
        if not rel:
            return None
        quoted = urllib.parse.quote(rel, safe="/")
        known = self._runs()["shots"] if shots is None else shots
        try:
            security.resolve_read(self.root, quoted, lambda r: self._image_allowed(r, known))
        except security.ReadError:
            return None
        return "/files/" + quoted

    # ---- run and differential index ----------------------------------------------------------------

    def _run_files(self):
        runs_dir = os.path.join(self.qa, "runs")
        files = []
        for camp in subdirs(runs_dir):
            cdir = os.path.join(runs_dir, camp)
            files += [(camp, p, st) for p, st in scan(cdir, "R-") + scan(cdir, "D-") + scan(os.path.join(cdir, "diff"))]
        return files

    def _runs(self):
        files = self._run_files()
        sig = signature((p, st) for _, p, st in files)
        with self._lock:
            if sig != self._runs_sig:
                self._runs_idx = self._build_runs(files)
                self._runs_sig = sig
            return self._runs_idx

    def _build_runs(self, files):
        runs, diffs = {}, {}
        for camp, path, st in files:
            rec = self._json(path, st)
            if not isinstance(rec, dict) or not isinstance(rec.get("id"), str):
                continue
            item = {"rec": rec, "path": path, "campaign": camp}
            if rec.get("kind") == "run":
                runs[rec["id"]] = item
            elif rec.get("kind") == "differential":
                diffs[rec["id"]] = item
        run_succ, diff_succ = successors(runs), successors(diffs)
        shots = set()
        for item in runs.values():
            for a in item["rec"].get("attempts") or []:
                for e in (a.get("evidence") or []) if isinstance(a, dict) else []:
                    if isinstance(e, dict) and e.get("kind") == "shot" and isinstance(e.get("path"), str):
                        shots.add(e["path"])
            shots.update(run_level_images(item["rec"]))
        launches = collections.defaultdict(lambda: collections.defaultdict(list))
        for rid, item in runs.items():
            item["base"] = self._base_row(rid, item, run_succ, diff_succ)
            m = RUN_ID_RE.match(rid)
            b = item["base"]
            if m and isinstance(b["case"], str) and isinstance(b["target"], str):
                # runs launched together: same campaign, case and run number; a correction (-NNc1) joins its base
                launches[b["case"]][(item["campaign"], m.group(4))].append(rid)
        return {"runs": runs, "diffs": diffs, "run_succ": run_succ, "diff_succ": diff_succ, "shots": frozenset(shots),
                "launches": {c: dict(g) for c, g in launches.items()}}

    @staticmethod
    def _tag(rec):
        t = rec.get("target") if isinstance(rec.get("target"), dict) else {}
        tag = val(t.get("tag"))
        if isinstance(tag, str) and tag:
            return tag
        m = RUN_ID_RE.match(rec.get("id") or "")
        return m.group(2) if m else None

    @staticmethod
    def _build(rec):
        b = rec.get("build") if isinstance(rec.get("build"), dict) else {}
        out = {k: val(b.get(k)) for k in ("hash7", "semver", "env", "version_code", "about_label")}
        if not out["hash7"]:
            gh = val(b.get("git_hash"))
            if isinstance(gh, str) and HEX_RE.match(gh):
                out["hash7"] = gh[:7]
            elif isinstance(out["about_label"], str) and HASH7_IN_LABEL.search(out["about_label"]):
                out["hash7"] = HASH7_IN_LABEL.search(out["about_label"]).group(1)
        return out

    @staticmethod
    def _started(rec):
        att = rec.get("attempts") or []
        first = att[0] if att and isinstance(att[0], dict) else {}
        return first.get("started") or rec.get("created")

    def _base_row(self, rid, item, run_succ, diff_succ):
        rec = item["rec"]
        tag = self._tag(rec)
        target = rec.get("target") if isinstance(rec.get("target"), dict) else {}
        verdict = rec.get("verdict") if isinstance(rec.get("verdict"), dict) else {}
        attempts = [a for a in rec.get("attempts") or [] if isinstance(a, dict)]
        evidence, shots = [], []
        for a in attempts:
            for e in a.get("evidence") or []:
                if isinstance(e, dict):
                    evidence.append({"kind": e.get("kind"), "path": e.get("path") if isinstance(e.get("path"), str)
                                     else None})
        for a in reversed(attempts):
            shots += [e["path"] for e in a.get("evidence") or []
                      if isinstance(e, dict) and e.get("kind") == "shot" and isinstance(e.get("path"), str)]
        shots += [p for p in run_level_images(rec) if p not in shots]
        for e in rec.get("evidence") or []:
            if isinstance(e, dict):
                evidence.append({"kind": e.get("kind"), "path": evidence_path(e)})
        diff = rec.get("differential")
        diff = newest(diff, diff_succ) if isinstance(diff, str) else diff
        mode = rec.get("mode")
        return {
            "run": rid, "case": rec.get("case"), "target": tag,
            "target_class": target.get("class") or ("web" if tag == "W044" else "tablet"),
            "mode": mode, "build": self._build(rec), "verdict": verdict_of(rec),
            "evidence_kind": verdict.get("evidence_kind"), "attempts": len(attempts),
            "created": rec.get("created"), "started": self._started(rec), "campaign": item["campaign"],
            "differential": diff, "judgement_pending": rec.get("judgement_pending") or [],
            "superseded_by": run_succ.get(rid), "harness": bool(rec.get("harness_case")),
            "_shots": shots, "_evidence": evidence,
        }

    def _row(self, rid, idx, cases):
        item = idx["runs"][rid]
        row = {k: v for k, v in item["base"].items() if not k.startswith("_")}
        row["build"] = dict(row["build"])
        row["stale"] = self._stale(item["rec"], cases)
        urls = [self._file_url(p, idx["shots"]) for p in item["base"]["_shots"]]
        row["thumbs"] = [u for u in urls if u][:THUMBS]
        row["evidence"] = [dict(e, url=self._file_url(e["path"], idx["shots"]) if e["path"] else None)
                           for e in item["base"]["_evidence"]]
        return row

    @staticmethod
    def _sort_key(row):
        return (row.get("started") or row.get("created") or "", row.get("run") or "")

    # ---- case index -------------------------------------------------------------------------------

    def _case_files(self):
        out = []
        for d, harness in ((os.path.join(self.qa, "cases"), False),
                           (os.path.join(self.qa, "runner", "tests", "cases"), True)):
            out += [(p, st, harness) for p, st in scan(d) if CASE_FILE_RE.match(os.path.basename(p))]
        return out

    def _cases(self):
        files = self._case_files()
        sig = signature((p, st) for p, st, _ in files)
        with self._lock:
            if sig != self._cases_sig:
                self._cases_idx = self._build_cases(files)
                self._cases_sig = sig
            return self._cases_idx

    def _build_cases(self, files):
        cases = {}
        for path, st, harness in files:
            m = CASE_FILE_RE.match(os.path.basename(path))
            rec = self._json(path, st)
            if not isinstance(rec, dict) or rec.get("kind", "case") != "case":
                continue
            cid, n = m.group(1), int(m.group(2) or 0)
            c = cases.setdefault(cid, {"id": cid, "versions": [], "harness": False})
            c["harness"] = c["harness"] or harness or bool(rec.get("harness"))
            c["versions"].append({"n": n, "file": self._rel(path), "sha256": self._sha256(path, st),
                                  "created": rec.get("created"), "supersedes": rec.get("supersedes"), "rec": rec})
        for c in cases.values():
            c["versions"].sort(key=lambda v: v["n"])
            c["latest"] = c["versions"][-1]
        return cases

    # ---- staleness ---------------------------------------------------------------------------------

    def _git(self, args):
        """(returncode, stdout) of a read-only git command in the WingtraCloud clone, or (None, error)."""
        if not os.path.isdir(self.repo):
            return None, "WingtraCloud clone not found"
        try:
            p = subprocess.run(["git", "-C", self.repo] + args, env=dict(os.environ, **GIT_ENV),
                               stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=GIT_TIMEOUT)
        except subprocess.TimeoutExpired:
            return None, "git timed out"
        except OSError as e:
            return None, f"git did not start ({type(e).__name__})"
        return p.returncode, p.stdout

    def _tip(self):
        """(ref, sha) of the engine environment's branch tip in the clone; sha None when unknown."""
        ref = TIPS.get(self._engine().get("env"), TIPS["staging"])
        hit = self._tips.get(ref)
        if hit and time.monotonic() - hit[0] < TIP_TTL_S:
            return ref, hit[1]
        rc, out = self._git(["rev-parse", "--verify", "--quiet", ref + "^{commit}"])
        sha = out.strip() if rc == 0 and HEX_RE.match(out.strip() or "-") else None
        self._tips[ref] = (time.monotonic(), sha)
        return ref, sha

    def _changed(self, git_hash, globs):
        """(changed paths, None) between the run's build and the tip on the case's covered paths, or (None, why)."""
        ref, tip = self._tip()
        if not tip:
            return None, f"{ref} not found in the WingtraCloud clone"
        key = (git_hash, tip, tuple(globs))
        if key in self._git_cache:
            return self._git_cache[key]
        rc, out = self._git(["diff", "--no-renames", "--name-only", git_hash, tip, "--"] +
                            [":(glob)" + g for g in globs])
        if rc is None:
            return None, out  # timeouts are not cached
        res = ([ln for ln in out.splitlines() if ln], None) if rc == 0 else \
            (None, "git diff failed (build hash not in the clone)")
        self._git_cache[key] = res
        return res

    def _stale(self, rec, cases):
        """{v, why, changed_paths, tip}: requirement changed, or covered paths changed since the run's build."""
        why, reasons, unknown, changed = [], False, False, []
        case = cases.get(rec.get("case"))
        if not case:
            return {"v": None, "why": ["case not found"], "changed_paths": [], "tip": None}
        latest = case["latest"]["rec"]
        latest_req = (latest.get("requirement") or {}).get("sha256")
        run_req = None
        fsha = rec.get("case_file_sha256")
        if fsha:
            ver = next((v for v in case["versions"] if v["sha256"] == fsha), None)
            if ver:
                run_req = (ver["rec"].get("requirement") or {}).get("sha256")
        elif rec.get("case_requirement_sha256"):
            run_req = rec["case_requirement_sha256"]
        if not run_req:
            unknown = True
            why.append("case version not found")
        elif run_req != latest_req:
            reasons = True
            why.append("requirement changed")
        build = rec.get("build") if isinstance(rec.get("build"), dict) else {}
        git_hash = val(build.get("git_hash")) or val(build.get("hash7"))
        globs = [g for g in latest.get("covered_paths") or [] if isinstance(g, str) and g and "\x00" not in g]
        ref = None
        if not (isinstance(git_hash, str) and HEX_RE.match(git_hash)):
            unknown = True
            why.append("no build git hash")
        elif not globs:
            unknown = True
            why.append("case has no covered_paths")
        else:
            ref = self._tip()[0]
            paths, err = self._changed(git_hash, globs)
            if paths is None:
                unknown = True
                why.append(err)
            elif paths:
                reasons = True
                changed = paths[:CHANGED_PATHS]
                more = f" (+{len(paths) - CHANGED_PATHS} more)" if len(paths) > CHANGED_PATHS else ""
                why.append(f"covered paths changed since {git_hash[:7]} on {ref}: {', '.join(changed)}{more}")
        v = True if reasons else (None if unknown else False)
        return {"v": v, "why": why, "changed_paths": changed, "tip": ref}

    # ---- /api/meta ---------------------------------------------------------------------------------

    def _ledger_head(self):
        for path, st in reversed(scan(os.path.join(self.qa, "ledger"), suffix=".jsonl")):
            last = self._last_line(path, st.st_size)
            if last:
                try:
                    seq = json.loads(last).get("seq")
                except (ValueError, AttributeError):
                    seq = None
                return {"seq": seq, "head12": hashlib.sha256(last).hexdigest()[:12]}
        return {"seq": 0, "head12": None}

    @staticmethod
    def _last_line(path, size):
        chunk = 64 * 1024
        try:
            with open(path, "rb") as f:
                while True:
                    start = max(0, size - chunk)
                    f.seek(start)
                    lines = [ln for ln in f.read(size - start).splitlines() if ln.strip()]
                    if lines and (start == 0 or len(lines) > 1):
                        return lines[-1]
                    if start == 0:
                        return None
                    chunk *= 4
        except OSError:
            return None

    def _targets(self):
        best = {}
        for path, st in scan(os.path.join(self.qa, "devices")):
            m = re.match(r"^(.+)\.v([0-9]+)\.json\Z", os.path.basename(path))
            rec = self._json(path, st) if m else None
            if not isinstance(rec, dict) or rec.get("kind") != "device":
                continue
            serial = rec.get("serial") or m.group(1)
            if serial not in best or int(m.group(2)) > best[serial][0]:
                best[serial] = (int(m.group(2)), rec)
        out = [{"tag": rec.get("tag"), "serial": serial, "class": "tablet", "model": rec.get("model")}
               for serial, (_, rec) in best.items()]
        out.sort(key=lambda t: t["tag"] or "")
        return out + [{"tag": "W044", "serial": None, "class": "web", "model": None}]

    def meta(self, port):
        try:
            from case_schema import AREAS
            areas = list(AREAS)
        except Exception:
            areas = list(AREAS_FALLBACK)
        eng = self._engine()
        return {"env": eng.get("env"), "account_label": eng.get("account"), "web_origin": eng.get("web_origin"),
                "targets": self._targets(), "areas": areas, "ledger": self._ledger_head(), "now": now_iso(),
                "port": port}

    # ---- /api/live ---------------------------------------------------------------------------------

    def _state_dir(self, name, name_re=None):
        out = []
        for path, st in scan(os.path.join(self.state, name)):
            m = name_re.match(os.path.basename(path)) if name_re else True
            obj = self._json(path, st) if m else None
            if isinstance(obj, dict):
                out.append((path, st, obj))
        return out

    def _handbacks(self, idx):
        cutoff = time.time() - HANDBACK_DAYS * 86400
        latest = {}
        for rid, item in idx["runs"].items():
            if rid in idx["run_succ"]:
                continue
            b = item["base"]
            k = (b["case"], b["target"])
            if k not in latest or self._sort_key(b) > self._sort_key(latest[k]):
                latest[k] = b
        out = []
        for b in latest.values():
            rec = idx["runs"][b["run"]]["rec"]
            verdict = rec.get("verdict") if isinstance(rec.get("verdict"), dict) else {}
            by = verdict.get("by") if isinstance(verdict.get("by"), dict) else {}
            t = parse_time(rec.get("created"))
            if b["verdict"] != "BLOCKED" or by.get("kind") != "handover" or not t or t.timestamp() < cutoff:
                continue
            hb = verdict.get("handback") if isinstance(verdict.get("handback"), dict) else {}
            out.append({"run": b["run"], "case": b["case"], "target": b["target"], "step": hb.get("step"),
                        "reason": hb.get("reason"), "command": hb.get("command") or by.get("ref"),
                        "created": rec.get("created")})
        out.sort(key=lambda h: h["created"] or "", reverse=True)
        return out

    def live(self):
        status_path = os.path.join(self.state, "status.json")
        status = self._json(status_path)
        status = status if isinstance(status, dict) else None
        status_age = None
        if status is not None:
            try:
                status_age = age_s(status.get("refreshed"), os.stat(status_path).st_mtime)
            except OSError:
                pass
        progress = []
        for path, st, obj in self._state_dir("progress"):
            progress.append(dict(obj, file=self._rel(path), age_s=age_s(obj.get("updated"), st.st_mtime)))
        progress.sort(key=lambda p: p.get("updated") or "", reverse=True)
        # a lock is held only while its <name>.lock is flocked; a <name>.json left by a killed run.py is stale
        locks = [{"name": obj.get("name") or os.path.basename(path)[:-5], "holder": obj.get("holder"),
                  "case": obj.get("case"), "since": obj.get("since"), "stale": not lock_held(path[:-5] + ".lock")}
                 for path, st, obj in self._state_dir("locks")]
        handovers = [{"target": os.path.basename(path)[:-5], "reason": obj.get("reason"),
                      "since": obj.get("since") or obj.get("at") or obj.get("created") or iso_mtime(path),
                      "file": self._rel(path)}
                     for path, st, obj in self._state_dir("handover")]
        return {"status": status, "status_age_s": status_age, "progress": progress, "locks": locks,
                "handovers": handovers, "handbacks": self._handbacks(self._runs())}

    # ---- /api/tasks --------------------------------------------------------------------------------

    def _tasks(self):
        out = {}
        for path, st in scan(os.path.join(self.qa, "tasks"), "T-"):
            m = TASK_FILE_RE.match(os.path.basename(path))
            obj = self._json(path, st) if m else None
            if isinstance(obj, dict):
                out[m.group(1)] = obj
        return out

    def _summary(self, rid, idx):
        item = idx["runs"].get(rid)
        if not item:
            return {"run": rid, "case": None, "target": None, "verdict": None, "missing": True}
        b = item["base"]
        out = {"run": rid, "case": b["case"], "target": b["target"], "verdict": b["verdict"],
               "superseded_by": b["superseded_by"]}
        # a correction (<id>c1) stands in for the run it supersedes: its verdict counts, the task's run id stays
        cur = newest(rid, idx["run_succ"])
        if cur != rid and cur in idx["runs"]:
            out.update(run=cur, verdict=idx["runs"][cur]["base"]["verdict"], corrects=rid,
                       superseded_by=idx["run_succ"].get(cur))
        return out

    @staticmethod
    def _task_runs(task):
        return [r for r in task.get("runs") or [] if isinstance(r, str)]

    def tasks(self):
        idx = self._runs()
        out = [dict(t, run_summary=[self._summary(r, idx) for r in self._task_runs(t)])
               for t in self._tasks().values()]
        out.sort(key=lambda t: (t.get("created") or "", t.get("id") or ""), reverse=True)
        return {"tasks": out}

    def task(self, task_id):
        task = self._tasks()[task_id]
        idx, cases = self._runs(), self._cases()
        rows = []
        for r in self._task_runs(task):
            # as in _summary: a correction (<id>c1) stands in for the run it supersedes
            cur = newest(r, idx["run_succ"])
            if cur not in idx["runs"]:
                rows.append(self._summary(r, idx))
            else:
                rows.append(dict(self._row(cur, idx, cases), corrects=r) if cur != r else self._row(r, idx, cases))
        return dict(task, run_ids=self._task_runs(task), runs=rows)

    # ---- /api/results, /api/runs, /api/history -----------------------------------------------------

    def _differentials(self, case_id, idx):
        out = []
        for did, item in idx["diffs"].items():
            rec = item["rec"]
            if rec.get("case") != case_id:
                continue
            out.append({"id": did, "result": rec.get("result"), "runs": rec.get("runs"), "builds": rec.get("builds"),
                        "compared": rec.get("compared"), "created": rec.get("created"), "campaign": item["campaign"],
                        "superseded_by": idx["diff_succ"].get(did)})
        out.sort(key=lambda d: (d["created"] or "", d["id"]), reverse=True)
        return out

    def _case_head(self, case_id, cases):
        c = cases.get(case_id)
        rec = c["latest"]["rec"] if c else {}
        return {"case": case_id, "title": rec.get("title"), "area": rec.get("area"), "targets": rec.get("targets"),
                "harness": bool(c and c["harness"])}

    def _backtest(self, idx):
        runs = [it for it in idx["runs"].values() if it["rec"].get("mode") == "backtest"]
        current = [it for it in runs if it["rec"]["id"] not in idx["run_succ"]]
        verdicts = collections.Counter(it["base"]["verdict"] or "none" for it in current)
        return {"runs": len(runs), "current": len(current), "superseded": len(runs) - len(current),
                "verdicts": dict(sorted(verdicts.items())), "claims": self._claim_counts(),
                "disagreements_md": self._disagreements_md()}

    def _claim_counts(self):
        files = []
        for camp in subdirs(os.path.join(self.qa, "runs")):
            files += scan(os.path.join(self.qa, "runs", camp, "claim"))
        sig = signature(files)
        cached_sig, cached = self._claims
        if sig == cached_sig:
            return dict(cached)
        claims = {}
        for path, st in files:
            rec = self._json(path, st)
            if isinstance(rec, dict) and rec.get("kind", "claim") == "claim" and isinstance(rec.get("id"), str):
                claims[rec["id"]] = {"rec": rec}
        succ = successors(claims)
        counts = {"agree": 0, "disagree": 0, "not_in_records": 0, "unverifiable": 0}
        for cid, c in claims.items():
            if cid not in succ:
                r = c["rec"].get("result") or "none"
                counts[r] = counts.get(r, 0) + 1
        self._claims = (sig, counts)
        return dict(counts)

    def _disagreements_md(self):
        """The back-test's DISAGREEMENTS.md: under qa/runner/backtest/inputs/, else next to the back-test runs (the back-test is not in this repo)."""
        qa = glob.escape(self.qa)
        groups = (glob.glob(os.path.join(qa, "runner", "backtest", "inputs", "**", "DISAGREEMENTS.md"), recursive=True),
                  glob.glob(os.path.join(qa, "runs", "*", "DISAGREEMENTS.md")))
        for found in groups:
            found = [p for p in found if os.path.isfile(p)]
            if found:
                return self._rel(max(found, key=os.path.getmtime))
        return None

    # ---- launches: the case x device matrix and the concurrency line -------------------------------

    def _ledger_t(self, day, seq):
        """The ledger time `t` of line (day, seq), or None. One {seq: t} index per day file, built on first use and
        kept while the file size stays the same."""
        if not (isinstance(day, str) and LEDGER_DAY_RE.match(day)) or not is_int(seq):
            return None
        path = os.path.join(self.qa, "ledger", day + ".jsonl")
        try:
            st = os.lstat(path)
        except OSError:
            return None
        if not stat.S_ISREG(st.st_mode):
            return None
        with self._lock:
            hit = self._ledger_idx.get(day)
            if hit is None or hit["size"] != st.st_size or hit["ino"] != st.st_ino:
                hit = self._index_ledger(path, st, hit)
                self._ledger_idx[day] = hit
            return hit["t"].get(seq)

    @staticmethod
    def _index_ledger(path, st, prev):
        """{size, ino, offset, t} of one day file. The ledger is append-only: a file that only grew is read on from
        where the last pass stopped (a half-written last line waits for the next pass); anything else from the start."""
        grew = prev is not None and prev["ino"] == st.st_ino and prev["size"] is not None and st.st_size > prev["size"]
        index = dict(prev["t"]) if grew else {}
        offset = prev["offset"] if grew else 0
        try:
            with open(path, "rb") as f:
                f.seek(offset)
                chunk = f.read(max(0, st.st_size - offset))
        except OSError:
            return {"size": None, "ino": st.st_ino, "offset": 0, "t": {}}  # size None: retried on the next lookup
        end = chunk.rfind(b"\n") + 1
        for ln in chunk[:end].splitlines():
            if not ln.strip():
                continue
            try:
                obj = json.loads(ln)
            except ValueError:
                continue
            if isinstance(obj, dict) and is_int(obj.get("seq")) and isinstance(obj.get("t"), str):
                index[obj["seq"]] = obj["t"]
        return {"size": st.st_size, "ino": st.st_ino, "offset": offset + end, "t": index}

    def _deciding_step(self, rid, item, cases):
        """(n, "prediction"|"case") of a run's deciding step: deciding_assertion.step of its prediction file
        (qa/runs/<campaign>/pred/<prediction.id>.json, else PR-<run>.json, else PR-<base run>.json for a correction),
        else the last step with a scripted assert of the case version the run used (the latest when unknown)."""
        rec = item["rec"]
        pred = rec.get("prediction") if isinstance(rec.get("prediction"), dict) else {}
        pdir = os.path.join(self.qa, "runs", item["campaign"], "pred")
        for pid in (pred.get("id"), f"PR-{rid}", "PR-" + CORRECTION_RE.sub("", rid)):
            if not (isinstance(pid, str) and PRED_ID_RE.match(pid)):
                continue
            obj = self._json(os.path.join(pdir, pid + ".json"))
            if isinstance(obj, dict):
                da = obj.get("deciding_assertion") if isinstance(obj.get("deciding_assertion"), dict) else {}
                if is_int(da.get("step")):
                    return da["step"], "prediction"
                break
        case = cases.get(rec.get("case")) if isinstance(rec.get("case"), str) else None
        if not case:
            return None, None
        fsha = rec.get("case_file_sha256")
        ver = next((v for v in case["versions"] if v["sha256"] == fsha), case["latest"]) if fsha else case["latest"]
        last = None
        for s in ver["rec"].get("steps") or []:
            a = s.get("assert") if isinstance(s, dict) else None
            if isinstance(a, dict) and a.get("kind") == "scripted" and is_int(s.get("n")):
                last = s["n"]
        return (last, "case") if last is not None else (None, None)

    def _step_time(self, rec, n):
        """Ledger time of the first action of step n in the run's first attempt, or None (step not reached)."""
        for s in first_attempt(rec).get("steps") or []:
            if isinstance(s, dict) and s.get("n") == n:
                acts = s.get("actions") or []
                a = acts[0] if isinstance(acts, list) and acts and isinstance(acts[0], dict) else {}
                return self._ledger_t(a.get("day"), a.get("seq"))
        return None

    def _parallel(self, live, idx, cases):
        """Concurrency of 2+ live runs: run windows (attempts[0].started..ended), their common overlap, and how far
        apart the deciding step's first action was logged. The ledger holds when each action's line was written, not
        when the deciding read started, so the records never show a simultaneous read."""
        windows, spans = {}, {}
        for tag, rid in live:
            a0 = first_attempt(idx["runs"][rid]["rec"])
            ts, te = parse_time(a0.get("started")), parse_time(a0.get("ended"))
            if ts and te and te >= ts:
                windows[tag] = {"start": a0["started"], "end": a0["ended"]}
                spans[tag] = (ts, te)
        no_window = [t for t, _ in live if t not in spans]
        overlap_s, common = None, None
        if len(spans) >= 2:
            t_start = max(spans, key=lambda t: spans[t][0])
            t_end = min(spans, key=lambda t: spans[t][1])
            d = (spans[t_end][1] - spans[t_start][0]).total_seconds()
            if d > 0:
                overlap_s = round(d, 3)
                common = {"start": windows[t_start]["start"], "end": windows[t_end]["end"]}
        found = [self._deciding_step(rid, idx["runs"][rid], cases) for _, rid in live]
        counts = collections.Counter(n for n, _ in found if n is not None)
        n = min(counts, key=lambda k: (-counts[k], k)) if counts else None
        source = next((src for k, src in found if k == n), None) if n is not None else None
        times, at = {}, {}
        for tag, rid in live:
            t = self._step_time(idx["runs"][rid]["rec"], n) if n is not None else None
            times[tag] = t
            if parse_time(t):
                at[tag] = parse_time(t)
        spread_s = round((max(at.values()) - min(at.values())).total_seconds(), 3) if len(at) >= 2 else None
        finished = [{"tag": a, "before": b, "step": n} for a, _ in live for b, _ in live
                    if a != b and a in spans and b in at and spans[a][1] < at[b]]

        parts = []
        if len(spans) >= 2:
            parts.append(f"windows overlap {fmt_s(overlap_s)}" if overlap_s is not None else "no overlap of the run windows")
            if no_window:
                parts.append("no run window for " + ", ".join(no_window))
        else:
            parts.append("overlap unknown (no run window for " + ", ".join(no_window) + ")")
        if n is None:
            parts.append("deciding step unknown")
        else:
            reached = [t for t, _ in live if t in at]
            missing = [t for t, _ in live if t not in at]
            if len(reached) >= 2:
                parts.append(f"deciding step {n} read {fmt_s(spread_s)} apart" +
                             (f" (not reached by {', '.join(missing)})" if missing else ""))
            elif reached:
                parts.append(f"deciding step {n} reached by {reached[0]} only")
            else:
                parts.append(f"deciding step {n} not reached")
        parts.append("simultaneous not verified")
        for a, _ in live:
            before = [f["before"] for f in finished if f["tag"] == a]
            if before:
                parts.append(f"{a} finished before {join_and(before)} reached step {n}")
        return {"kind": "parallel launch", "windows": windows, "overlap_s": overlap_s, "common_window": common,
                "deciding_step": n, "deciding_step_from": source, "deciding_times": times, "spread_s": spread_s,
                "simultaneous_verified": False, "finished_before": finished,
                "text": "Parallel launch: " + "; ".join(parts)}

    def _concurrency(self, chosen, idx, cases):
        modes = {idx["runs"][rid]["rec"].get("mode") for _, rid in chosen}
        live = [(tag, rid) for tag, rid in chosen if idx["runs"][rid]["rec"].get("mode") == "live"]
        if len(live) >= 2:
            return self._parallel(live, idx, cases)
        if live:
            return {"kind": "single device", "text": f"single device ({live[0][0]})"}
        if modes == {"backtest"}:
            return {"kind": "not applicable", "text": "back-test (reconstructed from logs)"}
        if modes == {"live-recorded"}:
            return {"kind": "not applicable", "text": "live-recorded (written after the fact, no run windows)"}
        return {"kind": "not applicable",
                "text": "no live run (" + ", ".join(sorted(str(m) for m in modes)) + ")"}

    @staticmethod
    def _devices(targets):
        """The case's three devices (QA practice): TA3 replaces W044 for a tablet-only case."""
        return list(DEVICES_TABLET if targets == "tablet" else DEVICES_BOTH)

    def _launches(self, case_id, targets, idx, cases, row_of, wanted=None):
        """(launches newest first, extra device tags) of one case. wanted (a task's runs) keeps only the launches
        that hold one of them; every run of a kept launch still fills its cell."""
        devices = self._devices(targets)
        web_only = targets == "web"
        picked, extra = [], []
        for (camp, nn), rids in (idx["launches"].get(case_id) or {}).items():
            if wanted is not None and not wanted.intersection(rids):
                continue
            by_tag = collections.defaultdict(list)
            for rid in rids:
                by_tag[idx["runs"][rid]["base"]["target"]].append(rid)
            extra += [t for t in by_tag if t not in devices and t not in extra]
            picked.append((camp, nn, rids, by_tag))
        extra.sort()
        out = []
        for camp, nn, rids, by_tag in picked:
            cells, chosen = {}, []
            for tag in devices + extra:
                ids = by_tag.get(tag)
                if not ids:
                    if tag in devices:
                        why = NOT_ROUTED_WHY if web_only and tag != WEB_TAG else NOT_RUN_WHY
                        cells[tag] = {"verdict": "NOT_TESTED", "why": why}
                    continue
                # the record nothing supersedes wins (a correction over its base), then the newest
                ids = sorted(ids, key=lambda r: (r not in idx["run_succ"], self._sort_key(idx["runs"][r]["base"])),
                             reverse=True)
                row = row_of(ids[0])
                cells[tag] = {"run": ids[0], "verdict": row["verdict"], "evidence_kind": row["evidence_kind"],
                              "attempts": row["attempts"], "stale": row["stale"], "mode": row["mode"],
                              "older": ids[1:]}
                chosen.append((tag, ids[0]))
            # a record whose verdict is NOT_TESTED means the device was not tested: it never counts as having run
            ran = [t for t in devices if "run" in cells[t] and cells[t]["verdict"] != "NOT_TESTED"]
            untested = [t for t in devices if "run" in cells[t] and cells[t]["verdict"] == "NOT_TESTED"]
            not_run = [t for t in devices if "run" not in cells[t] and cells[t]["why"] == NOT_RUN_WHY]
            not_routed = [t for t in devices if "run" not in cells[t] and cells[t]["why"] == NOT_ROUTED_WHY]
            notes = []
            if not_run:
                notes.append(", ".join(not_run) + " not run")
            if untested:
                notes.append(", ".join(untested) + " not tested, the record says NOT_TESTED")
            if not_routed:
                notes.append(", ".join(not_routed) + " not routed: web-only case")
            also = [t for t in extra if t in cells]
            if also:
                notes.append(", ".join(also) + " also ran (not one of the three)")
            modes = sorted({str(idx["runs"][r]["rec"].get("mode") or "unknown") for _, r in chosen})
            builds = []
            for _, r in chosen:
                h = idx["runs"][r]["base"]["build"]["hash7"]
                if h and h not in builds:
                    builds.append(h)
            out.append({
                "campaign": camp, "nn": nn, "mode": modes[0] if len(modes) == 1 else "mixed", "builds": builds,
                "cells": cells,
                "devices_ran": f"{len(ran)} of {len(devices)}" + (f" ({'; '.join(notes)})" if notes else ""),
                "concurrency": self._concurrency(chosen, idx, cases),
                "_key": (max(self._sort_key(idx["runs"][r]["base"]) for r in rids), camp, nn),
            })
        out.sort(key=lambda x: x["_key"], reverse=True)
        for x in out:
            del x["_key"]
        return out, extra

    def results(self, case=None, task=None):
        idx, cases = self._runs(), self._cases()
        rids = list(idx["runs"])
        case_ids = set()
        wanted = None
        if task is not None:
            t = self._tasks()[task]
            wanted = set(self._task_runs(t))
            rids = [r for r in rids if r in wanted]
            case_ids |= {c for c in t.get("cases") or [] if isinstance(c, str)}
        if case is not None:
            if case not in cases and not any(it["rec"].get("case") == case for it in idx["runs"].values()):
                raise KeyError(case)
            rids = [r for r in rids if idx["runs"][r]["rec"].get("case") == case]
            case_ids = {case}
        elif task is None:
            case_ids |= set(cases)
        row_cache = {}

        def row_of(rid):
            if rid not in row_cache:
                row_cache[rid] = self._row(rid, idx, cases)
            return row_cache[rid]

        rows = collections.defaultdict(list)
        for r in rids:
            row = row_of(r)
            rows[row["case"]].append(row)
        case_ids |= {c for c in rows if c is not None and (case is None or c == case)}
        out = []
        for cid in sorted(case_ids, key=lambda c: (bool(cases.get(c, {}).get("harness")), c)):
            head = self._case_head(cid, cases)
            head["rows"] = sorted(rows.get(cid, []), key=self._sort_key, reverse=True)
            head["differentials"] = self._differentials(cid, idx)
            head["devices"] = self._devices(head["targets"])
            head["launches"], head["extra_devices"] = self._launches(cid, head["targets"], idx, cases, row_of, wanted)
            out.append(head)
        return {"cases": out, "backtest": self._backtest(idx)}

    def run(self, run_id):
        idx, cases = self._runs(), self._cases()
        item = idx["runs"][run_id]
        rec = item["rec"]
        out = dict(rec)
        row = self._row(run_id, idx, cases)
        attempts = [a for a in rec.get("attempts") or [] if isinstance(a, dict)]
        last = attempts[-1] if attempts else {}
        case = cases.get(rec.get("case"))
        case_steps = {}
        for s in (case["latest"]["rec"].get("steps") or []) if case else []:
            if isinstance(s, dict):
                case_steps[s.get("n")] = s
        out["steps"] = [{"n": s.get("n"), "text": s.get("text") or (case_steps.get(s.get("n")) or {}).get("text"),
                         "readback": s.get("readback"), "assert": s.get("assert"), "actions": s.get("actions") or [],
                         "skipped": bool(s.get("skipped")), "error": s.get("error"), "attempt": last.get("n")}
                        for s in last.get("steps") or [] if isinstance(s, dict)]
        evidence = []
        for a in attempts:
            for e in a.get("evidence") or []:
                if isinstance(e, dict):
                    p = e.get("path") if isinstance(e.get("path"), str) else None
                    evidence.append(dict(e, attempt=a.get("n"), url=self._file_url(p, idx["shots"]) if p else None))
        for e in rec.get("evidence") or []:
            if isinstance(e, dict):
                p = evidence_path(e)
                evidence.append(dict(e, attempt=None, path=p, url=self._file_url(p, idx["shots"]) if p else None))
        out["evidence"] = evidence
        pred = rec.get("prediction") if isinstance(rec.get("prediction"), dict) else {}
        pid = pred.get("id") or f"PR-{run_id}"
        ppath = os.path.join(self.qa, "runs", item["campaign"], "pred", f"{pid}.json")
        out["prediction"] = dict(pred, id=pid if (pred or os.path.isfile(ppath)) else None,
                                 path=self._rel(ppath) if os.path.isfile(ppath) else None, ledger=pred.get("ledger"))
        did = rec.get("differential")
        out["differential_id"] = did
        if isinstance(did, str) and did in idx["diffs"]:
            out["differential"] = idx["diffs"][newest(did, idx["diff_succ"])]["rec"]
        else:
            out["differential"] = None
        out["case_title"] = case["latest"]["rec"].get("title") if case else None
        out["file"] = self._rel(item["path"])
        out["campaign"] = row["campaign"]
        out["target_tag"] = row["target"]
        out["target_class"] = row["target_class"]
        out["build_summary"] = row["build"]
        out["superseded_by"] = row["superseded_by"]
        out["stale"] = row["stale"]
        out["thumbs"] = row["thumbs"]
        return out

    def history(self, case, target):
        idx, cases = self._runs(), self._cases()
        if case not in cases and not any(it["rec"].get("case") == case for it in idx["runs"].values()):
            raise KeyError(case)
        known = {t["tag"] for t in self._targets()} | {it["base"]["target"] for it in idx["runs"].values()}
        if target not in known:
            raise KeyError(target)
        rows = [self._row(rid, idx, cases) for rid, it in idx["runs"].items()
                if it["base"]["case"] == case and it["base"]["target"] == target and rid not in idx["run_succ"]]
        return {"rows": sorted(rows, key=self._sort_key)}

    # ---- /api/cases --------------------------------------------------------------------------------

    def _last_by_tag(self, case_id, idx):
        last = {}
        for rid, it in idx["runs"].items():
            b = it["base"]
            if b["case"] != case_id or rid in idx["run_succ"] or not b["target"]:
                continue
            if b["target"] not in last or self._sort_key(b) > self._sort_key(last[b["target"]]):
                last[b["target"]] = b
        return last

    def cases(self):
        idx, cases = self._runs(), self._cases()
        out = []
        for cid in sorted(cases, key=lambda c: (cases[c]["harness"], c)):
            c = cases[cid]
            rec = c["latest"]["rec"]
            last = self._last_by_tag(cid, idx)
            vs, why = [], []
            for tag in sorted(last):
                s = self._stale(idx["runs"][last[tag]["run"]]["rec"], cases)
                vs.append(s["v"])
                why += [f"{tag}: {w}" for w in s["why"]]
            if not last:
                stale = {"v": None, "why": ["no runs"]}
            else:
                stale = {"v": True if True in vs else (None if None in vs else False), "why": why}
            # the case version each last run ran on, and whether that is the newest version file (the one the runner
            # loads now, autonomy.load_newest): a PASS on an older version may not hold for the current one
            cur = c["latest"]["sha256"]
            ran = {tag: idx["runs"][b["run"]]["rec"].get("case_file_sha256") for tag, b in last.items()}
            out.append({
                "id": cid, "title": rec.get("title"), "area": rec.get("area"), "targets": rec.get("targets"),
                "status": rec.get("status"), "harness": c["harness"], "versions": len(c["versions"]),
                "latest_file": c["latest"]["file"], "current_sha256": cur,
                "requirement_source": (rec.get("requirement") or {}).get("source"),
                "last": {tag: {"verdict": b["verdict"], "run": b["run"], "created": b["created"],
                               "hash7": b["build"]["hash7"], "case_sha256": ran[tag], "on_current": ran[tag] == cur}
                         for tag, b in sorted(last.items())},
                "stale": stale,
                "hands_off": hands_off(rec, self.root),
            })
        return {"cases": out}

    def case(self, case_id):
        c = self._cases()[case_id]
        return {"case": c["latest"]["rec"], "harness": c["harness"], "latest_file": c["latest"]["file"],
                "versions": [{"file": v["file"], "sha256": v["sha256"], "created": v["created"],
                              "supersedes": v["supersedes"]} for v in c["versions"]]}

    # ---- /api/tickets ------------------------------------------------------------------------------

    @staticmethod
    def _lens(title, desc):
        t = draft_check.js_trim(title or "")
        d = draft_check.js_trim((desc or "").replace("\r\n", "\n").replace("\r", "\n"))
        return t, d, draft_check.utf16_len(t), draft_check.utf16_len(d)

    def _checks(self, title, desc, tl, dl, has_desc):
        problems = []
        sections = draft_check.check_headings(desc.split("\n"), problems) if has_desc else None
        return [
            {"name": "title_length", "ok": 3 <= tl <= 100, "detail": f"{tl} UTF-16 units (3..100)"},
            {"name": "description_length", "ok": has_desc and 3 <= dl <= 1000,
             "detail": f"{dl} UTF-16 units (3..1000, headings included)" if has_desc else "description.txt is missing"},
            {"name": "title_format", "ok": bool(draft_check.TITLE_RE.match(title)),
             "detail": "[CT] <area>: <symptom> (<condition>)"},
            {"name": "headings", "ok": sections is not None and not problems,
             "detail": "; ".join(problems) if problems else ("the four headings, in order" if has_desc
                                                             else "description.txt is missing")},
        ]

    def _draft(self, slug, n, ndir, shots):
        title_raw = self._text(os.path.join(ndir, "title.txt")) or ""
        desc_raw = self._text(os.path.join(ndir, "description.txt"))
        title, desc, tl, dl = self._lens(title_raw, desc_raw)
        draft_json = self._json(os.path.join(ndir, "draft.json"))
        pack = []
        pdir = os.path.join(ndir, "pack")
        for dirpath, dirnames, filenames in os.walk(pdir):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for name in sorted(filenames):
                p = os.path.join(dirpath, name)
                if name.startswith(".") or os.path.islink(p):
                    continue
                rel = self._rel(p)
                try:
                    size = os.path.getsize(p)
                except OSError:
                    continue
                pack.append({"rel": rel, "size": size, "url": self._file_url(rel, shots)})
        comments = []
        for path, st in scan(ndir, "comment_", ".md"):
            m = COMMENT_RE.match(os.path.basename(path))
            text = self._text(path) or ""
            comments.append({"cl": m.group(1) if m else None, "rel": self._rel(path), "text": text,
                             "len": draft_check.utf16_len(draft_check.js_trim(text))})
        filed = self._json(os.path.join(ndir, "filed.json"))
        submitted = self._json(os.path.join(ndir, "submitted.json"))
        return {"dir": self._rel(ndir), "slug": slug, "n": int(n), "title": title, "description": desc,
                "title_len": tl, "description_len": dl,
                "checks": self._checks(title, desc, tl, dl, desc_raw is not None),
                "draft_json": draft_json, "pack": pack, "comments": comments,
                "filed": filed if isinstance(filed, dict) else None,
                "submitted": submitted if isinstance(submitted, dict) else None,
                "run_record": draft_json.get("run_record") if isinstance(draft_json, dict) else None}

    def _legacy(self, tdir, shots):
        groups = collections.defaultdict(dict)
        for path, st in scan(tdir, "ticket", ""):
            m = LEGACY_RE.match(os.path.basename(path))
            if m:
                groups[int(m.group(1))][m.group(2)] = path
        out = []
        for k in sorted(groups):
            files = groups[k]
            title_p = next((files[n] for n in ("title.txt", "title.md") if n in files), None)
            desc_p = next((files[n] for n in ("short.md", "description.txt", "description.md") if n in files), None)
            title, desc, tl, dl = self._lens(self._text(title_p) if title_p else "",
                                            self._text(desc_p) if desc_p else "")
            out.append({"dir": self._rel(tdir), "k": k, "title": title, "description": desc, "title_len": tl,
                        "description_len": dl, "description_file": self._rel(desc_p) if desc_p else None,
                        "files": [{"rel": self._rel(p), "url": self._file_url(self._rel(p), shots)}
                                  for _, p in sorted(files.items())]})
        return out

    def tickets(self):
        drafts, triage, legacy = [], [], []
        shots = self._runs()["shots"]
        tests = os.path.join(self.root, "tests")
        for slug in reversed(subdirs(tests)):
            sdir = os.path.join(tests, slug)
            tdir = os.path.join(sdir, "tickets")
            if os.path.isdir(tdir):
                if SLUG_RE.match(slug):
                    nums = sorted((n for n in subdirs(tdir) if TICKET_N_RE.match(n)), key=int, reverse=True)
                    drafts += [self._draft(slug, n, os.path.join(tdir, n), shots) for n in nums
                               if os.path.isfile(os.path.join(tdir, n, "title.txt"))]
                legacy += self._legacy(tdir, shots)
            trdir = os.path.join(sdir, "triage")
            for tid in subdirs(trdir):
                p = os.path.join(trdir, tid, "triage.json")
                obj = self._json(p) if os.path.isfile(p) and not os.path.islink(p) else None
                if isinstance(obj, dict):
                    triage.append(dict(obj, dir=self._rel(os.path.dirname(p))))
        return {"drafts": drafts, "triage": triage, "legacy": legacy}
