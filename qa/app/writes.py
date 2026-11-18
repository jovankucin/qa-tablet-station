"""The QA app's only writer (qa/app/DESIGN.md section 4).

Writer(root) writes these kinds of file and nothing else:
  qa/tasks/T-<yyyymmdd>-<nnn>.json             tasks, replaced atomically under flock on qa/tasks/.lock
  qa/cases/<AREA>-<nnn>[.c<N>].json            draft cases and edits, O_CREAT|O_EXCL, never overwritten
  tests/<slug>/tickets/<n>/filed.json          the user's "filed as CL-…" marker, replaced under the same lock
  qa/cache/plan/<day>[.prep|.prep-status].json  the Today list and its pre-test notes (plan.py)
  qa/cache/intake/<ClickUp task id>.json        a task read from its link on the New test page (plan.py)
  qa/runner/state/worker/W-<yyyymmdd>-<nnn>.json, heartbeat.json   QA worker requests and heartbeat (worker.py)
  tests/<slug>/COMMENT_<ClickUp id>_<task>.posted.json   the marker of a results comment the app server posted to
                                               ClickUp after the person's Touch ID (2026-11-03): created once, never
                                               replaced, next to its COMMENT_<ClickUp id>_<task>.md
(The Touch ID approvals of ticket filing, qa/runner/state/approval.key and approvals/<id>.json, are written by
approvals.py alone, 2026-11-03.)
Every byte goes through Writer._write, which enforces that scope: no ledger-referenced (hashed) path, nothing under
qa/ledger, never qa/secrets.env, nothing outside the root, no symlinked parent folder. Free text from a request is
redacted (redact.redact) before it is stored; a glob or flag name that holds an e-mail, token or link is refused, since
masking would change what it means. JSON is canonical (records.canonical).

Errors are WriteError(status, message) with an HTTP status: 400 bad input, 403 refused path, 404 not found,
409 conflict with the current state.
"""
import contextlib
import fcntl
import json
import os
import re
import stat
import sys
import threading

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)
sys.path.insert(0, os.path.join(os.path.dirname(APP_DIR), "runner"))

import case_schema  # noqa: E402
import records  # noqa: E402
import redact  # noqa: E402
import tasks  # noqa: E402
import worker  # noqa: E402

SLUG = r"\d{4}-\d{2}-\d{2}-[a-z0-9-]+"
SLUG_RE = re.compile(rf"^{SLUG}\Z")
ALLOWED = (
    ("task", re.compile(r"^qa/tasks/T-\d{8}-\d{3}\.json\Z"), "replace"),
    ("case", re.compile(r"^qa/cases/[A-Z]{2,6}-\d{3}(\.c\d+)?\.json\Z"), "create"),
    ("filed", re.compile(rf"^tests/{SLUG}/tickets/\d+/filed\.json\Z"), "replace"),
    ("plan", re.compile(r"^qa/cache/plan/\d{4}-\d{2}-\d{2}(\.prep|\.prep-status)?\.json\Z"), "replace"),
    ("intake", re.compile(r"^qa/cache/intake/[A-Za-z0-9-]{3,40}\.json\Z"), "replace"),
    ("worker", re.compile(r"^qa/runner/state/worker/(heartbeat|W-\d{8}-\d{3})\.json\Z"), "replace"),
    ("posted", re.compile(rf"^tests/{SLUG}/COMMENT_[A-Za-z0-9-]{{1,40}}_T-\d{{8}}-\d{{3}}\.posted\.json\Z", re.A),
     "create"),
)
POSTED_FIELDS = ("comment_id", "posted_at", "sha256", "task_id")
CASE_DIRS = ("qa/cases", "qa/runner/tests/cases")
APP_CASE_ID_RE = re.compile(r"^[A-Z]{2,6}-\d{3}\Z")
RUN_ID_RE = re.compile(r"^R-\d{8}-[A-Z0-9]{2,8}-[A-Z]{2,10}-\d{3}-\d{2}(c\d+)?\Z")
CL_RE = re.compile(r"^CL-\d{4,6}\Z")
CLICKUP_URL_RE = re.compile(r"^https://app\.clickup\.com/t/(?:(\d+)/)?([A-Za-z0-9-]{4,40})/?\Z")
CUSTOM_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*-\d+\Z")
TICKET_DIR_RE = re.compile(rf"^tests/({SLUG})/tickets/(\d+)/?\Z")
# a flag name, or "Name: on" / "Name: off" for the value the case needs (qa/runner/run.py flag_needs, 2026-11-02)
FLAG_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}(?: ?: ?(?:on|off))?\Z")
WEB_TAG = "W044"
GUIDANCE_MAX = 1000  # the person's own words on the Run form: what to test and how
EXPLAIN_FILE_MAX = 256 * 1024  # the worker's explain file for the Results cards (add_output kind "explain")


class WriteError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def _encode(obj):
    return records.canonical(obj).encode("utf-8")


def _write_all(fd, data):
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]
    os.fsync(fd)


def _text(value, name, lo, hi, multiline=False):
    """A stripped, length-checked, redacted string from a request body."""
    if not isinstance(value, str):
        raise WriteError(400, f"{name}: must be text")
    v = value.replace("\r\n", "\n").strip() if multiline else " ".join(value.split())
    if not lo <= len(v) <= hi:
        raise WriteError(400, f"{name}: must be {lo}-{hi} characters")
    if "\x00" in v:
        raise WriteError(400, f"{name}: must not contain NUL")
    return redact.redact(v)


def _opt_text(value, name, hi, multiline=False):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return _text(value, name, 1, hi, multiline)


def guidance_text(value):
    """The Run form's guidance: None when blank, else multi-line text of at most GUIDANCE_MAX characters, redacted."""
    return _opt_text(value, "guidance", GUIDANCE_MAX, multiline=True)


def _text_list(value, name, max_items, hi):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > max_items:
        raise WriteError(400, f"{name}: must be a list of at most {max_items} texts")
    return [_text(v, f"{name}[{i}]", 1, hi, multiline=True) for i, v in enumerate(value)]


def _no_secret(value, name):
    """value as it is, or 400 when it holds a secret, token, e-mail or share link (masking would change its meaning)."""
    if redact.contains_secret(value) or redact.redact(value) != value:
        raise WriteError(400, f"{name}: must not hold an e-mail, token or link")
    return value


def _req_key(text):
    """A requirement text the way _case_body stores it, redacted and normalised: equal keys = same requirement."""
    if not isinstance(text, str):
        return None
    return case_schema.normalise_requirement(redact.redact(text.replace("\r\n", "\n").rstrip().lstrip("\n")))


def _expected_key(text):
    return redact.redact(text.replace("\r\n", "\n").strip()) if isinstance(text, str) else None


def _step_texts(steps):
    return [s.get("text") if isinstance(s, dict) else s for s in steps] if isinstance(steps, list) else []


def _link_flags(needs):
    needs = needs if isinstance(needs, dict) else {}
    return needs.get("link") or "none", list(needs.get("flags") or [])


class Writer:
    def __init__(self, root):
        self.root = os.path.realpath(root)
        self._tl = threading.local()

    # ---- scope -------------------------------------------------------------------------------------------------

    def _iter_ledger(self):
        if self.root == os.path.realpath(records.WORKSPACE):
            yield from records.iter_ledger()
            return
        d = os.path.join(self.root, "qa", "ledger")  # a test workspace: same reading as records.iter_ledger
        try:
            names = sorted(f for f in os.listdir(d) if f.endswith(".jsonl"))
        except OSError:
            return
        for name in names:
            with open(os.path.join(d, name), "rb") as f:
                for raw in f.read().splitlines():
                    if raw.strip():
                        yield name, raw

    def _ledger_refs(self):
        for _, raw in self._iter_ledger():
            try:
                ref = json.loads(raw).get("ref")
            except (ValueError, AttributeError):
                continue
            if isinstance(ref, dict):
                yield ref

    def _ledger_paths(self):
        return {os.path.normpath(r["path"]) for r in self._ledger_refs() if isinstance(r.get("path"), str)}

    def _target(self, rel):
        """(kind, mode, absolute path) for an allowed rel, else WriteError(403/404)."""
        if (not isinstance(rel, str) or not rel or "\x00" in rel or "\\" in rel or os.path.isabs(rel)
                or os.path.normpath(rel) != rel):
            raise WriteError(403, "path refused: not a clean relative path")
        if rel == "qa/secrets.env" or rel == "qa/ledger" or rel.startswith("qa/ledger/"):
            raise WriteError(403, "path refused: the ledger and secrets are never written")
        hit = next(((k, m) for k, rx, m in ALLOWED if rx.match(rel)), None)
        if hit is None:
            raise WriteError(403, "path refused: only tasks, cases, filed and posted markers, the plan, read tasks and "
                                  "worker requests are written")
        if rel in self._ledger_paths():
            raise WriteError(403, "path refused: a hashed record in the ledger")
        full = os.path.join(self.root, rel)
        parent = os.path.dirname(full)
        if os.path.realpath(parent) != parent:
            raise WriteError(403, "path refused: a parent folder is a symlink")
        if os.path.commonpath([parent, self.root]) != self.root:
            raise WriteError(403, "path refused: outside the workspace")
        if os.path.islink(full):
            raise WriteError(403, "path refused: the file is a symlink")
        if hit[0] == "filed" and not os.path.isfile(os.path.join(parent, "title.txt")):
            raise WriteError(404, "ticket folder not found (it must exist and hold title.txt)")
        if hit[0] == "posted" and not os.path.isfile(full[:-len(".posted.json")] + ".md"):
            raise WriteError(404, "comment file not found (the posted marker goes next to it)")
        return hit[0], hit[1], full

    @contextlib.contextmanager
    def _lock(self):
        """flock on qa/tasks/.lock; re-entrant within one thread."""
        if getattr(self._tl, "depth", 0):
            self._tl.depth += 1
            try:
                yield
            finally:
                self._tl.depth -= 1
            return
        d = os.path.join(self.root, tasks.TASKS_REL)
        if os.path.realpath(d) != d:  # checked before and after creating it
            raise WriteError(403, "path refused: qa/tasks or qa is a symlink")
        os.makedirs(d, exist_ok=True)
        if os.path.realpath(d) != d:
            raise WriteError(403, "path refused: qa/tasks or qa is a symlink")
        try:
            fd = os.open(os.path.join(d, ".lock"), os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o644)
        except OSError:
            raise WriteError(403, "path refused: qa/tasks/.lock is not a plain file") from None
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            self._tl.depth = 1
            try:
                yield
            finally:
                self._tl.depth = 0
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def _write(self, rel, data, mode):
        """The one place bytes reach the disk. mode: "create" (cases) or "replace" (tasks, filed.json)."""
        if not isinstance(data, bytes):
            raise TypeError("data must be bytes")
        kind, allowed_mode, full = self._target(rel)
        if mode != allowed_mode:
            raise WriteError(403, f"path refused: {kind} files are written with mode {allowed_mode} only")
        parent = os.path.dirname(full)
        if kind not in ("filed", "posted"):
            os.makedirs(parent, exist_ok=True)
            if os.path.realpath(parent) != parent:
                raise WriteError(403, "path refused: a parent folder is a symlink")
        if mode == "create":
            self._write_new(full, rel, data)
        else:
            with self._lock():
                self._replace(full, data)
        return records.sha256_bytes(data)

    @staticmethod
    def _write_new(full, rel, data):
        try:
            fd = os.open(full, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        except FileExistsError:
            raise WriteError(409, f"{rel} exists; cases are never overwritten") from None
        try:
            _write_all(fd, data)
        except OSError:
            os.close(fd)
            os.unlink(full)
            raise
        os.close(fd)

    @staticmethod
    def _replace(full, data):
        """Write data to a new <full>.tmp (a leftover one, or a link planted there, is removed, never written through)
        and rename it over full."""
        tmp = full + ".tmp"
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        except OSError:
            raise WriteError(403, "path refused: the temporary file is not a plain file") from None
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        except OSError:
            raise WriteError(403, "path refused: the temporary file is not a plain file") from None
        try:
            _write_all(fd, data)
        finally:
            os.close(fd)
        os.replace(tmp, full)

    # ---- reads the writes depend on ----------------------------------------------------------------------------

    def target_tags(self):
        """Tags of the newest device profile per serial, plus the web host."""
        d = os.path.join(self.root, "qa", "devices")
        newest = {}
        try:
            names = os.listdir(d)
        except OSError:
            names = []
        for name in names:
            m = re.match(r"^(.+?)(?:\.v(\d+))?\.json\Z", name)
            if not m:
                continue
            try:
                with open(os.path.join(d, name), encoding="utf-8") as f:
                    obj = json.load(f)
            except (OSError, ValueError):
                continue
            if not isinstance(obj, dict) or obj.get("kind") != "device" or not isinstance(obj.get("tag"), str):
                continue
            serial, ver = obj.get("serial") or m.group(1), int(m.group(2) or 0)
            if serial not in newest or ver > newest[serial][0]:
                newest[serial] = (ver, obj["tag"])
        return sorted({t for _, t in newest.values()}) + [WEB_TAG]

    def _case_versions(self, cid, dirs=("qa/cases",)):
        """[(N, rel)] of a case's files, plain file = 0, sorted by N."""
        rx = re.compile(rf"^{re.escape(cid)}(?:\.c(\d+))?\.json\Z")
        out = []
        for d in dirs:
            try:
                names = os.listdir(os.path.join(self.root, d))
            except OSError:
                continue
            out += [(int(m.group(1) or 0), f"{d}/{n}") for n in names if (m := rx.match(n))]
        return sorted(out)

    def case_exists(self, cid):
        return bool(self._case_versions(cid, CASE_DIRS))

    def next_case_id(self, area):
        """<AREA>-<n+1>, n the highest number for the area in the case folders and in the ledger."""
        file_rx = re.compile(rf"^{re.escape(area)}-(\d{{3}})(?:\.c\d+)?\.json\Z")
        id_rx = re.compile(rf"^{re.escape(area)}-(\d+)\Z")
        n = 0
        for d in CASE_DIRS:
            try:
                names = os.listdir(os.path.join(self.root, d))
            except OSError:
                continue
            n = max([n] + [int(m.group(1)) for x in names if (m := file_rx.match(x))])
        for ref in self._ledger_refs():
            if ref.get("kind") == "case" and isinstance(ref.get("id"), str) and (m := id_rx.match(ref["id"])):
                n = max(n, int(m.group(1)))
        if n >= 999:
            raise WriteError(409, f"area {area} has no case number left")
        return f"{area}-{n + 1:03d}"

    # ---- tasks -------------------------------------------------------------------------------------------------

    def _clickup(self, url):
        if url is None or (isinstance(url, str) and not url.strip()):
            return None
        if not isinstance(url, str) or len(url) > 300:
            raise WriteError(400, "clickup_url: must be a ClickUp task link")
        base = re.split(r"[?#]", url.strip(), maxsplit=1)[0]
        m = CLICKUP_URL_RE.match(base)
        if not m:
            raise WriteError(400, "clickup_url: must look like https://app.clickup.com/t/…")
        ident = m.group(2)
        if CUSTOM_ID_RE.match(ident):
            return {"url": base.rstrip("/"), "task_id": None, "cl": ident}
        if re.match(r"^[a-z0-9]+\Z", ident):
            return {"url": base.rstrip("/"), "task_id": ident, "cl": None}
        raise WriteError(400, "clickup_url: no task id or custom id in the link")

    def _task_rel(self, task_id):
        return f"{tasks.TASKS_REL}/{task_id}.json"

    def create_task(self, body):
        if not isinstance(body, dict):
            raise WriteError(400, "body must be a JSON object")
        text = _text(body.get("text"), "text", 3, 4000, multiline=True)
        targets = body.get("targets")
        known = self.target_tags()
        if (not isinstance(targets, list) or not targets or len(set(map(str, targets))) != len(targets)
                or any(t not in known for t in targets)):
            raise WriteError(400, f"targets: a non-empty list of distinct tags from {', '.join(known)}")
        priority = body.get("priority") or "normal"
        if priority not in tasks.PRIORITY:
            raise WriteError(400, f"priority: must be one of {', '.join(tasks.PRIORITY)}")
        cases = body.get("cases") or []
        if not isinstance(cases, list) or len(cases) > 100:
            raise WriteError(400, "cases: must be a list of case ids")
        for c in cases:
            if not isinstance(c, str) or not case_schema.CASE_ID_RE.match(c) or not self.case_exists(c):
                raise WriteError(400, "cases: every id must name an existing case")
        clickup = self._clickup(body.get("clickup_url"))
        now = records.now_utc()
        day = now[:10].replace("-", "")
        with self._lock():
            d = os.path.join(self.root, tasks.TASKS_REL)
            nums = [int(m.group(2)) for n in os.listdir(d)
                    if (m := re.match(rf"^T-({day})-(\d{{3}})\.json\Z", n))]
            nxt = max(nums, default=0) + 1
            if nxt > 999:
                raise WriteError(409, "no task number left for today")
            task = {
                "kind": "task", "schema": 1, "id": f"T-{day}-{nxt:03d}", "created": now, "author": "user",
                "text": text, "clickup": clickup, "targets": list(targets), "priority": priority,
                "cases": list(dict.fromkeys(cases)), "state": "queued",
                "history": [{"at": now, "from": None, "to": "queued", "by": "user", "via": "app", "note": None}],
                "runs": [], "campaign": None, "outputs": {"summary": None, "tickets": [], "comments": []},
            }
            self._write(self._task_rel(task["id"]), _encode(task), "replace")
        return task

    def _update_task(self, task_id, change):
        """Load, change and write a task under the lock; returns the written task."""
        if not isinstance(task_id, str) or not tasks.TASK_ID_RE.match(task_id):
            raise WriteError(404, "no such task")
        with self._lock():
            task = tasks.load_task(self.root, task_id)
            if task is None:
                raise WriteError(404, f"no task {task_id}")
            change(task)
            self._write(self._task_rel(task_id), _encode(task), "replace")
        return task

    def set_task_state(self, task_id, to, note=None, by="user", via="app", user_words=None):
        if to not in tasks.STATES:
            raise WriteError(400, f"to: must be one of {', '.join(tasks.STATES)}")
        if by not in tasks.ACTORS or via not in tasks.VIA or (by == "claude" and via != "qa-run"):
            raise WriteError(400, "by must be user (app or qa-run) or claude (qa-run)")
        note = _opt_text(note, "note", 1000, multiline=True)
        words = _opt_text(user_words, "user_words", 1000, multiline=True)

        def change(task):
            frm = task.get("state")
            if not tasks.can_transition(frm, to, by):
                raise WriteError(409, f"{by} cannot move a task from {frm} to {to}")
            if by == "claude" and (frm, to) in tasks.CLAUDE_NEEDS_USER_WORDS and not words:
                raise WriteError(400, f"{frm} to {to} by claude needs the user's words (--user-words)")
            if by == "user" and via != "app" and not words:
                raise WriteError(400, "a user decision outside the app needs the user's words (--user-words)")
            if (frm, to) == ("running", "queued") and task.get("runs"):
                raise WriteError(409, "runs are recorded; a task goes back to queued only before any run")
            if by == "user" and frm == "running":
                # the person moves a running task on only after its start request ended (failed or cancelled:
                # close_started_task, 2026-11-05, wingmate-debug FINDINGS #9); Hover or /qa-run moves the others
                why = self._start_still_open(task)
                if why:
                    raise WriteError(409, why)
            if via == "app" and to == "closed" and worker.open_work(worker.list_requests(self.root), task_id):
                # Mark done waits for Hover's work on the results (2026-11-05, FINDINGS #28): the page asks the same
                raise WriteError(409, worker.WORK_OPEN_WHY)
            text = note
            if words:
                text = f'user words: "{words}"' + (f"; {note}" if note else "")
            task["state"] = to
            task.setdefault("history", []).append(
                {"at": records.now_utc(), "from": frm, "to": to, "by": by, "via": via, "note": text})

        return self._update_task(task_id, change)

    def _start_still_open(self, task):
        """Why the person may not move this running task on, or None: its start request (origin.start) ended."""
        origin = task.get("origin") if isinstance(task.get("origin"), dict) else {}
        rid = origin.get("start")
        req = worker.load_request(self.root, rid) if isinstance(rid, str) and worker.REQ_ID_RE.match(rid) else None
        if req is None:
            return "a running task that was not started in the app is moved on by Hover or /qa-run"
        if req.get("state") in worker.OPEN:
            return f"{rid} is still open: Hover is running this test"
        return None

    def settle_started_task(self, req, why, by="claude", via="qa-run"):
        """The task of a start request that ended failed or cancelled while the task is still running (2026-11-05,
        wingmate-debug FINDINGS #9): with runs recorded it moves to results_ready (they are its results, and Mark done
        works), else back to queued. A task started from a chat (no start request) stays as it is. by and via: Hover
        (expire, its own fail) or the person in the app (Release, close_started_task). The task after the move, else
        None."""
        if not isinstance(req, dict) or req.get("type") != "start" or req.get("state") not in ("failed", "cancelled"):
            return None
        tid = req.get("task")
        with self._lock():
            task = tasks.load_task(self.root, tid) if isinstance(tid, str) else None
            origin = task.get("origin") if task and isinstance(task.get("origin"), dict) else {}
            if not task or task.get("state") != "running" or origin.get("start") != req.get("id"):
                return None
            to = "results_ready" if task.get("runs") else "queued"
            return self.set_task_state(task["id"], to, note=why, by=by, via=via)

    def add_run(self, task_id, run_id):
        if not isinstance(run_id, str) or not RUN_ID_RE.match(run_id):
            raise WriteError(400, "run: must look like R-<yyyymmdd>-<tag>-<CASE>-<nn>")

        def change(task):
            if task.get("state") not in ("running", "results_ready"):
                raise WriteError(409, f"a {task.get('state')} task takes no runs: runs are added while running or "
                                      "before the greenlight")
            runs = task.setdefault("runs", [])
            if run_id not in runs:
                runs.append(run_id)

        return self._update_task(task_id, change)

    def set_campaign(self, task_id, slug):
        if not isinstance(slug, str) or len(slug) > 100 or not SLUG_RE.match(slug):
            raise WriteError(400, "campaign: must be a tests/ folder name, yyyy-mm-dd-<slug>")

        def change(task):
            if task.get("state") not in ("queued", "running"):
                raise WriteError(409, "the campaign is set only while the task is queued or running")
            task["campaign"] = slug

        return self._update_task(task_id, change)

    def _output_rel(self, rel):
        if not isinstance(rel, str):
            raise WriteError(400, "output: must be a path under tests/")
        rel = rel.rstrip("/")
        segs = rel.split("/")
        if (not rel or len(rel) > 400 or "\x00" in rel or "\\" in rel or os.path.isabs(rel)
                or os.path.normpath(rel) != rel or segs[0] != "tests" or len(segs) < 3
                or any(s.startswith(".") for s in segs)):
            raise WriteError(400, "output: must be a clean path under tests/<campaign>/")
        full = os.path.join(self.root, rel)
        tests_dir = os.path.join(self.root, "tests")
        if os.path.commonpath([os.path.realpath(full), tests_dir]) != tests_dir:
            raise WriteError(400, "output: resolves outside tests/")
        if not os.path.exists(full):
            raise WriteError(404, f"output not found: {rel}")
        return rel

    def _explain_file(self, rel):
        """An explain output is read inline by the Results cards: a .json file of its own (no link, no second hard
        link), at most EXPLAIN_FILE_MAX bytes, holding {"cases": {...}}."""
        full = os.path.join(self.root, *rel.split("/"))
        try:
            st = os.lstat(full)
        except OSError:
            raise WriteError(404, f"output not found: {rel}") from None
        if not rel.lower().endswith(".json") or not stat.S_ISREG(st.st_mode) or st.st_nlink > 1:
            raise WriteError(400, "explain: must be a .json file under tests/, not a folder or a link")
        if st.st_size > EXPLAIN_FILE_MAX:
            raise WriteError(413, f"explain: the file is larger than {EXPLAIN_FILE_MAX // 1024} KiB")
        try:
            with open(full, "rb") as f:
                obj = json.loads(f.read(EXPLAIN_FILE_MAX + 1).decode("utf-8"))
        except (OSError, ValueError, RecursionError):
            raise WriteError(400, "explain: the file is not JSON") from None
        if not isinstance(obj, dict) or not isinstance(obj.get("cases"), dict):
            raise WriteError(400, 'explain: must be {"cases": {"<CASE>": {tested, happened, why, not_tested}}}')

    def add_output(self, task_id, kind, rel):
        if kind not in tasks.OUTPUT_KINDS:
            raise WriteError(400, f"kind: must be one of {', '.join(tasks.OUTPUT_KINDS)}")
        rel = self._output_rel(rel)
        if kind == "explain":
            self._explain_file(rel)

        def change(task):
            if task.get("state") not in tasks.OUTPUT_STATES.get(kind, tasks.DRAFT_STATES):
                raise WriteError(409, "the explain file is added once the runs are in" if kind == "explain" else
                                 "outputs are written only after the user's greenlight")
            out = task.setdefault("outputs", {"summary": None, "tickets": [], "comments": []})
            if kind == "summary":
                out["summary"] = rel
            elif rel not in out.setdefault(kind, []):
                out[kind].append(rel)

        return self._update_task(task_id, change)

    # ---- QA worker requests (worker.py, DESIGN.md 8.3) -------------------------------------------------------

    def create_request(self, fields, exclusive=None):
        """A new request in state new; fields carry type and payload. exclusive: a ClickUp task id that must have no
        open request of the same type. Re-entrant under the lock."""
        if fields.get("type") not in worker.TYPES:
            raise WriteError(400, f"type: one of {', '.join(worker.TYPES)}")
        if fields["type"] in ("file", "draft"):
            try:
                fields = (worker.validate_file_request if fields["type"] == "file" else
                          worker.validate_draft_request)(fields)
            except worker.WorkerError as e:
                raise WriteError(e.status, e.message) from None
        now = records.now_utc()
        day = now[:10].replace("-", "")
        with self._lock():
            if exclusive:
                busy = worker.open_for(self.root, exclusive, (fields["type"],))
                if busy:
                    raise WriteError(409, f"{busy[0]['id']} for this test is still open")
            d = os.path.join(self.root, worker.WORKER_REL)
            try:
                names = os.listdir(d)
            except FileNotFoundError:
                names = []
            nums = [int(m.group(2)) for n in names if (m := re.match(rf"^W-({day})-(\d{{3}})\.json\Z", n))]
            nxt = max(nums, default=0) + 1
            if nxt > 999:
                raise WriteError(409, "no request number left for today")
            req = dict(fields, kind="worker_request", schema=1, id=f"W-{day}-{nxt:03d}", created=now, state="new",
                       claimed_at=None, done_at=None, link=None, notes=[], result=fields.get("result"))
            self._write(worker.request_rel(req["id"]), _encode(req), "replace")
        return req

    def file_conflict(self, ticket_dir):
        """Why a ticket folder cannot get a new "file" request (filed already, or a file request still open), else
        None. A new file request whose approval expired is failed here first (worker.EXPIRED_NOTE), so it never holds
        the folder after the worker missed it."""
        with self._lock():
            if worker.submitted(self.root, ticket_dir):
                return "this ticket is already filed (submitted.json)"
            if os.path.lexists(os.path.join(self.root, ticket_dir, "filed.json")):
                return "this ticket is already marked as filed"
            for r in worker.file_requests(self.root, ticket_dir):
                if worker.file_expired(r):
                    def change(req):
                        if req.get("state") != "new":
                            raise WriteError(409, f"{req['id']} changed meanwhile")
                        req["state"], req["done_at"] = "failed", records.now_utc()
                        req.setdefault("notes", []).append({"at": records.now_utc(), "text": worker.EXPIRED_NOTE})
                    self.update_request(r["id"], change)
                elif r.get("state") in worker.OPEN:
                    return f"{r['id']} for this ticket is still open"
        return None

    def create_file_request(self, fields):
        """The worker request that files one ticket draft after the person's Touch ID (serve.py POST
        /api/tickets/file); 409 when file_conflict finds a reason, checked again under the lock."""
        try:
            fields = worker.validate_file_request(fields)
        except worker.WorkerError as e:
            raise WriteError(e.status, e.message) from None
        with self._lock():
            why = self.file_conflict(fields["ticket_dir"])
            if why:
                raise WriteError(409, why)
            return self.create_request(fields)

    def create_draft_request(self, fields):
        """The worker request that drafts one Swiss Army Knife ticket for one run (serve.py POST /api/tickets/draft,
        Report issue, 2026-11-03); 409 while a draft request of the same card (task and case) is open or when the run
        has a draft folder already, checked again under the lock."""
        try:
            fields = worker.validate_draft_request(fields)
        except worker.WorkerError as e:
            raise WriteError(e.status, e.message) from None
        with self._lock():
            busy = worker.draft_open(self.root, fields["task"], fields["case"])
            if busy:
                raise WriteError(409, f"{busy[0]['id']} is still drafting a ticket for this test")
            if worker.draft_dir_for_run(self.root, fields["run"]):
                raise WriteError(409, "this run already has a ticket draft")
            return self.create_request(fields)

    @staticmethod
    def _write_clickup(task):
        cu = task.get("clickup") if isinstance(task.get("clickup"), dict) else {}
        return {"task_id": cu.get("task_id"), "title": None, "url": cu.get("url"), "priority": None}

    def request_comment(self, task_id):
        """Add result on a Results card (serve.py POST /api/results/comment, 2026-11-03): the write request for the
        results comment alone. The press is the person's greenlight: a results_ready task is greenlit (greenlight with
        outputs ["comment"]), a greenlit task asks for the comment again, and a drafted task (its drafts were written
        without a comment) gets the request and stays drafted. 409 while the results are not in, for a closed task, a
        task with no ClickUp test, and while a write request of the task is open."""
        if not isinstance(task_id, str) or not tasks.TASK_ID_RE.match(task_id):
            raise WriteError(404, "no such task")
        with self._lock():
            task = tasks.load_task(self.root, task_id)
            if task is None:
                raise WriteError(404, f"no task {task_id}")
            state = task.get("state")
            if state in ("queued", "running"):
                raise WriteError(409, "the results of this test are not in yet")
            if state not in ("results_ready", "greenlit", "drafted"):
                raise WriteError(409, f"this test is {state}: a results comment is written only for a test with "
                                      "results")
            cu = self._write_clickup(task)
            ck = task.get("clickup") if isinstance(task.get("clickup"), dict) else {}
            if not (ck.get("task_id") or ck.get("cl")):
                raise WriteError(409, "this test has no ClickUp task to comment on")
            if any(r.get("type") == "write" and r.get("task") == task_id and r.get("state") in worker.OPEN
                   for r in worker.list_requests(self.root)):
                raise WriteError(409, "the drafts for this task are already being written")
            if state != "drafted":
                return self.greenlight(task_id, ["comment"])
            return self.create_request({"type": "write", "task": task_id, "outputs": ["comment"], "clickup": cu})

    def posted_target(self, rel):
        """Check, before anything is posted, that the posted marker of a results comment can be written: an allowed
        path next to its comment file, with no file there yet (409: posted already)."""
        kind, _, full = self._target(rel)
        if kind != "posted":
            raise WriteError(403, "path refused: not a posted marker")
        if os.path.lexists(full):
            raise WriteError(409, "the results comment of this test was already posted")
        return full

    def write_posted(self, rel, marker):
        """The posted marker {comment_id, posted_at, sha256, task_id} of a results comment the app server posted to
        ClickUp (serve.py POST /api/results/comment/post): created once (O_EXCL), never replaced, so the same comment is
        never posted twice."""
        if not isinstance(marker, dict) or sorted(marker) != sorted(POSTED_FIELDS):
            raise WriteError(400, "posted marker: exactly " + ", ".join(POSTED_FIELDS))
        self.posted_target(rel)
        try:
            self._write(rel, _encode(marker), "create")
        except WriteError as e:
            if e.status == 409:
                raise WriteError(409, "the results comment of this test was already posted") from None
            raise
        return marker

    def update_request(self, rid, change):
        rel = worker.request_rel(rid)
        with self._lock():
            req = worker.load_request(self.root, rid)
            if req is None:
                raise WriteError(404, f"no request {rid}")
            change(req)
            self._write(rel, _encode(req), "replace")
        return req

    def start_run(self, check, targets):
        """The user's Start after a device check: the qa task the worker runs, and its start request."""
        if check.get("intake") is True:
            return self._start_intake(check, targets)
        cu = check.get("clickup") or {}
        title = cu.get("title") or cu.get("task_id")
        # only the cases that run on a device the person kept ticked, each narrowed to those devices; with attached
        # files, also the pending cases the worker said the files complete (its answer's "complete")
        done = [e for e in (check.get("result") or {}).get("complete") or [] if isinstance(e, str)]
        added = []
        for c in check.get("pending") or []:
            mine = [e for e in (c.get("engine_cases") or []) if e in done] if isinstance(c, dict) else []
            if mine:
                added.append(dict(c, engine_cases=mine))  # only the engine cases the files complete
        cases = []
        for c in list(check.get("cases") or []) + added:
            on = [t for t in (c.get("targets") or []) if t in targets] if isinstance(c, dict) else []
            if on:
                cases.append(dict(c, targets=on))
        if not cases:
            raise WriteError(400, "targets: no case runs on the chosen devices")
        engine = list(dict.fromkeys(e for c in cases for e in c.get("engine_cases") or []))
        # what the person saw and approved on the summary screen travels with the run
        guidance = check.get("guidance") if isinstance(check.get("guidance"), str) else None
        summary = (check.get("result") or {}).get("summary")
        summary = summary if isinstance(summary, str) else None
        # the attached files of the cases that still run (devices unticked before Run can drop a completed case),
        # as the check recorded them (name, size, sha256, path)
        done = [e for e in done if e in engine]
        plan_files = [x for x in (check.get("result") or {}).get("plan_files") or []
                      if isinstance(x, dict) and x.get("case") in done]
        used = {x.get("name") for x in plan_files}
        files = [f for f in check.get("files") or [] if isinstance(f, dict) and f.get("name") in used]
        # the case file of each engine case that runs, as Prepare saw it (serve.py refused a changed one)
        versions = [v for v in check.get("case_versions") or [] if isinstance(v, dict) and v.get("id") in engine]
        approved = (" with their guidance and the summary they approved" if summary and guidance else
                    " with the summary they approved" if summary else "")
        text = (f"Run from Today: {title}. Cases {', '.join(engine)} on {', '.join(targets)}. "
                f"Started by the user in the app after device check {check['id']}{approved}.")
        return self._open_start(check, targets, text, cases, engine, {
            "fix_commits": check.get("fix_commits") or [], "guidance": guidance, "plan_summary": summary,
            "plan_targets": list(check.get("targets") or []) if summary else None,
            "files": files, "plan_files": plan_files, "completes": done, "case_versions": versions},
            ("guidance", "plan_summary", "files", "plan_files", "case_versions"))

    def _start_intake(self, check, targets):
        """Run after a New test plan: the plan's cases that have a ticked device, each narrowed to those devices and run
        exactly as pinned (no completion step), with the files the plan gives them."""
        cu = check.get("clickup") or {}
        result = check.get("result") or {}
        runs, cases = [], []
        for pc in result.get("plan_cases") or []:
            on = [t for t in (pc.get("targets") or []) if t in targets] if isinstance(pc, dict) else []
            if on:
                runs.append(dict(pc, targets=on))
                cases.append({"n": None, "text": pc.get("text"), "engine_cases": [pc.get("id")], "targets": on,
                              "side_effects": [], "needs": []})
        if not cases:
            raise WriteError(400, "targets: no case of the plan runs on the chosen devices")
        # a ticked device no planned case runs on stays out of the run (and stays free for a later Run)
        targets = [t for t in targets if any(t in pc["targets"] for pc in runs)]
        engine = list(dict.fromkeys(pc.get("id") for pc in runs))
        plan_files = [x for x in result.get("plan_files") or [] if isinstance(x, dict) and x.get("case") in engine]
        used = {x.get("name") for x in plan_files}
        files = [f for f in check.get("files") or [] if isinstance(f, dict) and f.get("name") in used]
        summary = result.get("summary") if isinstance(result.get("summary"), str) else None
        # the plan's title (worker.PLAN_TITLE_MAX), which the person approved with Run, names the test in the app
        title = result.get("title") if isinstance(result.get("title"), str) else None
        text = (f"Run from New test: {cu.get('title') or cu.get('task_id')}. Cases {', '.join(engine)} on "
                f"{', '.join(targets)}. Started by the user in the app after plan {check['id']} with the description "
                "and plan they approved.")
        return self._open_start(check, targets, text, cases, engine, {
            "intake": True, "fix_commits": [], "guidance": check.get("guidance"), "plan_summary": summary,
            "plan_targets": list(check.get("targets") or []), "plan_cases": runs,
            "case_versions": [{k: pc.get(k) for k in ("id", "file", "sha256")} for pc in runs],
            "files": files, "plan_files": plan_files, "completes": [], "title": title},
            ("intake", "guidance", "plan_summary", "plan_targets", "plan_cases", "files", "plan_files",
             "case_versions", "title"))

    def _open_start(self, check, targets, text, cases, engine, fields, task_fields):
        """The task and the start request of a Start, under the lock; fields go on the request, task_fields of them
        also on the task."""
        cu = check.get("clickup") or {}
        priority = {"urgent": "high", "high": "high", "low": "low"}.get(cu.get("priority"), "normal")
        with self._lock():
            busy = worker.open_for(self.root, cu.get("task_id"), ("start",))
            if busy:
                raise WriteError(409, f"{busy[0]['id']} for this test is still open")
            # one approved summary runs once per device: a second Run from the same check needs a new check
            # (a start cancelled before the worker took it, or failed by it before any run, ran nothing)
            for r in worker.list_requests(self.root):
                if r.get("type") != "start" or r.get("check") != check["id"] or r.get("state") in ("cancelled", "failed"):
                    continue
                ran = [t for t in r.get("targets") or [] if t in targets]
                if ran and check.get("intake") is True:
                    raise WriteError(409, f"already run from this plan as {r['id']}; prepare again")
                if ran:
                    raise WriteError(409, f"already run on {', '.join(ran)} as {r['id']}; check the devices again to "
                                          "run again")
            task = self.create_task({"text": text, "targets": list(targets), "priority": priority, "cases": engine,
                                     "clickup_url": cu.get("url")})
            req = self.create_request(dict({"type": "start", "check": check["id"], "task": task["id"], "clickup": cu,
                                            "cases": cases, "engine_cases": engine, "targets": list(targets)},
                                           **fields))

            def mark(t):
                t["origin"] = {"start": req["id"], "check": check["id"]}
                t.update({k: fields.get(k) for k in task_fields})
            self._update_task(task["id"], mark)
            return req

    def close_started_task(self, req, why):
        """The task of a start request the person cancelled or released in the app: a task still running moves on first
        (settle_started_task as the user, 2026-11-05: results_ready with runs, else back to queued); a task still
        queued is then closed."""
        if req.get("type") != "start" or not req.get("task"):
            return
        with self._lock():
            self.settle_started_task(req, why, by="user", via="app")
            task = tasks.load_task(self.root, req.get("task"))
            origin = task.get("origin") if task and isinstance(task.get("origin"), dict) else {}
            if task and task.get("state") == "queued" and origin.get("start") == req.get("id"):
                self.set_task_state(task["id"], "closed", note=why, by="user", via="app")

    def greenlight(self, task_id, outputs):
        """The user's greenlight in the app, with the outputs the worker writes next. On a task already greenlit
        (a drafts request that failed, or a greenlight given in chat) it only asks for the drafts again."""
        if (not isinstance(outputs, list) or not outputs or len(set(outputs)) != len(outputs)
                or any(o not in worker.OUTPUTS for o in outputs)):
            raise WriteError(400, f"outputs: one or more of {', '.join(worker.OUTPUTS)}")
        if not isinstance(task_id, str) or not tasks.TASK_ID_RE.match(task_id):
            raise WriteError(404, "no such task")
        with self._lock():
            current = tasks.load_task(self.root, task_id)
            if current is None:
                raise WriteError(404, f"no task {task_id}")
            if any(r.get("type") == "write" and r.get("task") == task_id and r.get("state") in worker.OPEN
                   for r in worker.list_requests(self.root)):
                raise WriteError(409, "the drafts for this task are already being written")
            if current.get("state") == "greenlit":
                task = current
            else:
                task = self.set_task_state(task_id, "greenlit", note="outputs: " + ", ".join(outputs), by="user",
                                           via="app")
            return self.create_request({"type": "write", "task": task_id, "outputs": list(outputs),
                                        "clickup": self._write_clickup(task)})

    def write_heartbeat(self, fields):
        hb = dict(fields, kind="worker_heartbeat", schema=1, at=records.now_utc())
        self._write(worker.HEARTBEAT_REL, _encode(hb), "replace")
        return hb

    # ---- cases -------------------------------------------------------------------------------------------------

    def _case_body(self, body):
        """The request fields of POST/PUT /api/cases, checked and redacted."""
        if not isinstance(body, dict):
            raise WriteError(400, "body must be a JSON object")
        req = body.get("requirement")
        if not isinstance(req, dict) or not isinstance(req.get("source"), dict):
            raise WriteError(400, "requirement: must be {text, source: {type, ref, locator?, requester?}}")
        src = req["source"]
        if src.get("type") not in case_schema.SOURCE_TYPES:
            raise WriteError(400, f"requirement.source.type: one of {', '.join(case_schema.SOURCE_TYPES)}")
        source = {"type": src["type"], "ref": _text(src.get("ref"), "requirement.source.ref", 1, 200)}
        for k, hi in (("locator", 200), ("requester", 100)):
            v = _opt_text(src.get(k), f"requirement.source.{k}", hi)
            if v is not None:
                source[k] = v
        rtext = req.get("text")
        if not isinstance(rtext, str):
            raise WriteError(400, "requirement.text: must be text")
        rtext = rtext.replace("\r\n", "\n").rstrip().lstrip("\n")
        if not 1 <= len(rtext) <= 4000 or "\x00" in rtext:
            raise WriteError(400, "requirement.text: must be 1-4000 characters")
        if body.get("targets") not in case_schema.TARGETS:
            raise WriteError(400, f"targets: one of {', '.join(case_schema.TARGETS)}")
        paths = body.get("covered_paths") or []
        if not isinstance(paths, list) or len(paths) > 50:
            raise WriteError(400, "covered_paths: a list of at most 50 globs")
        globs = []
        for i, g in enumerate(paths):
            if not isinstance(g, str) or not g.strip() or len(g) > 200:
                raise WriteError(400, f"covered_paths[{i}]: must be a glob")
            g = g.strip()
            if not g.startswith(("apps/", "packages/")) or ".." in g.split("/") or "\\" in g or "\x00" in g:
                raise WriteError(400, f"covered_paths[{i}]: must be a glob under apps/ or packages/")
            globs.append(_no_secret(g, f"covered_paths[{i}]"))
        needs = body.get("needs") or {}
        if not isinstance(needs, dict):
            raise WriteError(400, "needs: must be {link, flags}")
        link = needs.get("link", "none")
        if link not in case_schema.LINKS:
            raise WriteError(400, f"needs.link: one of {', '.join(case_schema.LINKS)}")
        flags = needs.get("flags") or []
        if not isinstance(flags, list) or len(flags) > 50 or any(
                not isinstance(f, str) or not FLAG_RE.match(f) for f in flags):
            raise WriteError(400, "needs.flags: a list of flag names")
        for i, f in enumerate(flags):
            _no_secret(f, f"needs.flags[{i}]")
        status = body.get("status")
        if status is not None and status not in case_schema.STATUSES:
            raise WriteError(400, f"status: one of {', '.join(case_schema.STATUSES)}")
        return {
            "area": body.get("area"),
            "title": _text(body.get("title"), "title", 3, case_schema.TITLE_MAX),
            "requirement_text": redact.redact(rtext),
            "source": source,
            "targets": body["targets"],
            "preconditions": _text_list(body.get("preconditions"), "preconditions", 30, 500),
            "steps": _text_list(body.get("steps"), "steps", 60, 1000),
            "expected": _text(body.get("expected"), "expected", 1, 2000, multiline=True),
            "covered_paths": list(dict.fromkeys(globs)),
            "link": link,
            "flags": list(dict.fromkeys(flags)),
            "needs_given": body.get("needs") is not None,
            "status": status,
            "why": _opt_text(body.get("why"), "why", 300, multiline=True),
        }

    @staticmethod
    def _check_and_encode(case):
        problems = case_schema.validate_case(case)
        if problems:
            raise WriteError(400, "case invalid: " + "; ".join(problems[:5]))
        return _encode(case)

    def create_case(self, body):
        f = self._case_body(body)
        if f["area"] not in case_schema.AREAS:
            raise WriteError(400, f"area: one of {', '.join(case_schema.AREAS)}")
        if f["status"] not in (None, "draft"):
            raise WriteError(400, "status: a new case is always a draft")
        targets = f["targets"]
        for _ in range(20):
            cid = self.next_case_id(f["area"])
            case = {
                "kind": "case", "schema": 1, "id": cid, "created": records.now_utc(), "author": "user",
                "supersedes": None, "title": f["title"], "area": f["area"],
                "requirement": {"text": f["requirement_text"],
                                "sha256": case_schema.requirement_sha256(f["requirement_text"]),
                                "source": f["source"]},
                "targets": targets, "routing": [], "expected_differences": [],
                "preconditions": [{"id": f"P{i}", "text": t, "check": None, "applies_to": targets}
                                  for i, t in enumerate(f["preconditions"], 1)],
                "steps": [{"n": i, "text": t, "helper": None, "gates": []} for i, t in enumerate(f["steps"], 1)],
                "expected": f["expected"], "fixtures": [], "covered_paths": f["covered_paths"],
                "needs": {"link": f["link"], "usb_port": f["link"] in ("usb_radio", "real_drone"),
                          "account_lock": False, "flags": f["flags"], "drone_hw": None},
                "status": "draft",
            }
            rel = f"qa/cases/{cid}.json"
            try:
                self._write(rel, self._check_and_encode(case), "create")
            except WriteError as e:
                if e.status == 409 and "exists" in e.message:
                    continue  # another writer took this number first
                raise
            return {"id": cid, "file": rel}
        raise WriteError(409, "could not allocate a new case id")

    @staticmethod
    def _carry(texts, previous, make):
        """New list items for texts; an item whose text equals an unused previous item keeps that item."""
        pool = [p for p in previous if isinstance(p, dict)]
        out = []
        for i, t in enumerate(texts, 1):
            hit = next((p for p in pool if p.get("text") == t), None)
            if hit is not None:
                pool.remove(hit)
            out.append(make(i, t, hit))
        return out

    @staticmethod
    def _run_changes(case, prev):
        """Names of the fields an edit changes that decide what the runner runs and checks."""
        old_req = prev.get("requirement") if isinstance(prev.get("requirement"), dict) else {}
        changes = (("steps", _step_texts(case["steps"]) != _step_texts(prev.get("steps"))),
                   ("targets", case["targets"] != prev.get("targets")),
                   ("requirement", _req_key(case["requirement"]["text"]) != _req_key(old_req.get("text"))),
                   ("expected", case["expected"] != _expected_key(prev.get("expected"))),
                   ("needs", _link_flags(case["needs"]) != _link_flags(prev.get("needs"))))
        return [name for name, changed in changes if changed]

    def edit_case(self, case_id, body):
        if not isinstance(case_id, str) or not APP_CASE_ID_RE.match(case_id):
            raise WriteError(404, "no such case")
        versions = self._case_versions(case_id)
        if not versions:
            if self.case_exists(case_id):
                raise WriteError(403, "harness cases are not edited from the app")
            raise WriteError(404, f"no case {case_id}")
        latest_n, latest_rel = versions[-1]
        base = body.get("base") if isinstance(body, dict) else None
        if not isinstance(base, str):
            raise WriteError(400, "base: the latest_file the edit form was opened on is required")
        if base != latest_rel:
            raise WriteError(409, "the case changed since the form was opened: reload it and edit again")
        try:
            with open(os.path.join(self.root, latest_rel), "rb") as fh:
                raw = fh.read()
            prev = json.loads(raw)
        except (OSError, ValueError):
            raise WriteError(409, f"{latest_rel} is unreadable") from None
        if not isinstance(prev, dict):
            raise WriteError(409, f"{latest_rel} is not a case")
        f = self._case_body(body)
        if f["area"] is not None and f["area"] != prev.get("area"):
            raise WriteError(400, "area: a case keeps its area")
        status = prev.get("status")
        if f["status"] in ("draft", "retired"):
            status = f["status"]
        elif f["status"] == "active" and status != "active":
            raise WriteError(400, "status: the app sets draft or retired only")
        targets = f["targets"]

        def make_step(i, t, old):
            return dict(old, n=i) if old else {"n": i, "text": t, "helper": None, "gates": []}

        def make_pre(i, t, old):
            return dict(old) if old else {"id": None, "text": t, "check": None, "applies_to": targets}

        pres = self._carry(f["preconditions"], prev.get("preconditions") or [], make_pre)
        used = {p["id"] for p in pres if p["id"]}
        k = 0
        for p in pres:
            while p["id"] is None:
                k += 1
                if f"P{k}" not in used:
                    p["id"] = f"P{k}"
                    used.add(p["id"])
        old_req = prev.get("requirement") or {}
        source = dict(f["source"])
        old_src = old_req.get("source") or {}
        if (f["requirement_text"] == old_req.get("text") and source["type"] == old_src.get("type")
                and source["ref"] == old_src.get("ref")):
            source.update({k: old_src[k] for k in ("cache", "fetched", "custom_id") if k in old_src})
        needs = dict(prev.get("needs") or {})
        if f["needs_given"]:  # the form sends needs only when link or flags changed
            if needs.get("link") != f["link"]:
                needs["usb_port"] = f["link"] in ("usb_radio", "real_drone")
            needs.update(link=f["link"], flags=f["flags"])
        needs.setdefault("link", "none")
        needs.setdefault("usb_port", needs["link"] in ("usb_radio", "real_drone"))
        needs.setdefault("flags", [])
        needs.setdefault("account_lock", False)
        needs.setdefault("drone_hw", None)
        case = dict(prev)
        case.update({
            "title": f["title"],
            "requirement": {"text": f["requirement_text"],
                            "sha256": case_schema.requirement_sha256(f["requirement_text"]), "source": source},
            "targets": targets, "preconditions": pres,
            "steps": self._carry(f["steps"], prev.get("steps") or [], make_step),
            "expected": f["expected"], "covered_paths": f["covered_paths"], "needs": needs, "status": status,
        })
        ignore = ("created", "author", "supersedes")
        if {k: v for k, v in case.items() if k not in ignore} == {k: v for k, v in prev.items() if k not in ignore}:
            raise WriteError(409, "nothing changed")
        case.update({
            "created": records.now_utc(), "author": "user",
            "supersedes": {"id": case_id, "file": latest_rel, "file_sha256": records.sha256_bytes(raw),
                           "why": f["why"] or "edited in the QA app"},
        })
        changed = self._run_changes(case, prev) if status == "active" else []
        if changed:
            raise WriteError(400, f"this edit changes the {', '.join(changed)} of an active case: save it with status "
                                  "draft (/qa-run reviews it before it runs again)")
        if status == "active" and any(s.get("helper") is None and not s.get("read_js") for s in case["steps"]):
            raise WriteError(400, "a new step has no helper yet: save the edit with status draft")
        rel = f"qa/cases/{case_id}.c{latest_n + 1}.json"
        self._write(rel, self._check_and_encode(case), "create")
        return {"id": case_id, "file": rel}

    # ---- tickets -----------------------------------------------------------------------------------------------

    def mark_filed(self, body):
        if not isinstance(body, dict):
            raise WriteError(400, "body must be a JSON object")
        d, cl, replace = body.get("dir"), body.get("cl"), body.get("replace", False)
        m = TICKET_DIR_RE.match(d) if isinstance(d, str) else None
        if not m:
            raise WriteError(400, "dir: must be tests/<yyyy-mm-dd-slug>/tickets/<n>")
        if not isinstance(cl, str) or not CL_RE.match(cl):
            raise WriteError(400, "cl: must look like CL-12345")
        if not isinstance(replace, bool):
            raise WriteError(400, "replace: must be true or false")
        folder = d.rstrip("/")
        rel = f"{folder}/filed.json"
        self._target(rel)  # refuse before reading anything in the folder or taking the lock
        run_record = None
        try:
            fd = os.open(os.path.join(self.root, folder, "draft.json"), os.O_RDONLY | os.O_NOFOLLOW)
            with open(fd, encoding="utf-8") as fh:
                draft = json.load(fh)
            if isinstance(draft, dict) and isinstance(draft.get("run_record"), str):
                run_record = redact.redact(draft["run_record"])
        except (OSError, ValueError):
            pass
        with self._lock():
            old = None
            try:
                with open(os.path.join(self.root, rel), encoding="utf-8") as fh:
                    old = json.load(fh)
            except FileNotFoundError:
                pass
            except (OSError, ValueError):
                raise WriteError(409, "the existing filed.json is unreadable") from None
            history = []
            if isinstance(old, dict):
                if old.get("cl") == cl:
                    return old
                if not replace:
                    raise WriteError(409, f"already marked as filed as {old.get('cl')}; send replace to change it")
                history = list(old.get("history") or []) + [{k: v for k, v in old.items() if k != "history"}]
            marker = {"cl": cl, "by": "user", "via": "app", "at": records.now_utc(), "run_record": run_record,
                      "history": history}
            self._write(rel, _encode(marker), "replace")
        return marker
