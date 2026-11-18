"""Local web view over the QA engine: routing, JSON, static files and /files/ (qa/app/DESIGN.md sections 1 to 4).

python3 -I qa/app/serve.py [--port 8787] [--root <workspace>]

Binds 127.0.0.1 only and prints the URL with its UTC start time, never the token. Every request passes the Host and
Sec-Fetch-Site checks in security.py; POST and PUT also need the per-start token, a same-origin
Origin, a JSON content type and a body of at most 64 KiB. Reads come from data.Data, writes go
through writes.Writer, and File ticket's Touch ID approval through approvals.py (which also runs the prompt on this Mac);
this module itself opens no file for writing and starts no process. Post comment (2026-11-03) is the app's one ClickUp
write: after the person's press and Touch ID (approvals.confirm) the server posts the results comment through
clickup_post.py and the Writer records it.
"""
import argparse
import codecs
import html
import http
import ipaddress
import json
import math
import os
import re
import secrets
import socketserver
import stat
import sys
import threading
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)
sys.path.insert(0, os.path.join(os.path.dirname(APP_DIR), "runner"))
import approvals  # noqa: E402
import clickup_post  # noqa: E402
import clickup_read  # noqa: E402
import data  # noqa: E402
import devstatus  # noqa: E402
import plan  # noqa: E402
import redact  # noqa: E402
import security  # noqa: E402
import tasks  # noqa: E402
import worker  # noqa: E402
import writes  # noqa: E402
import uploads  # noqa: E402
import views  # noqa: E402

STATIC_DIR = os.path.join(APP_DIR, "static")
DEFAULT_ROOT = os.path.dirname(os.path.dirname(APP_DIR))
TOKEN_SLOT = "{{QA_TOKEN}}"
IMAGE_CAP = 32 * 1024 * 1024
TEXT_SLACK = 64 * 1024  # read past the text cap, so a value the cap cuts through is redacted whole
BOMS = ((codecs.BOM_UTF32_LE, "utf-32-le", 4), (codecs.BOM_UTF32_BE, "utf-32-be", 4),
        (codecs.BOM_UTF16_LE, "utf-16-le", 2), (codecs.BOM_UTF16_BE, "utf-16-be", 2))
DRAIN_CAP = 1024 * 1024
MESSAGE_CAP = 200
REASON_TITLE_MAX = 60  # the draft title in the Touch ID prompt's reason
SHA256_RE = re.compile(r"[0-9a-f]{64}")

JSON_TYPE = "application/json; charset=utf-8"
TEXT_TYPE = "text/plain; charset=utf-8"
STATIC_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
                ".ico": "image/x-icon", ".woff2": "font/woff2", ".json": JSON_TYPE, ".txt": TEXT_TYPE}
STATIC_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}")

TASK_ID = r"T-\d{8}-\d{3}"
CASE_ID = r"[A-Z][A-Z0-9]{1,11}-\d{3,6}"
RUN_ID = r"R-[A-Za-z0-9]{1,16}(?:-[A-Za-z0-9]{1,16}){1,8}"
QUERY_RX = {"case": re.compile(CASE_ID), "task": re.compile(TASK_ID), "target": re.compile(r"[A-Za-z0-9]{1,16}"),
            "days": re.compile(r"\d{1,3}")}

# (path regex, {method: handler name}); HEAD follows GET.
ROUTES = [(re.compile(p), m) for p, m in (
    (r"/api/meta", {"GET": "api_meta"}),
    (r"/api/plan", {"GET": "api_plan"}),
    (r"/api/plan/refresh", {"POST": "api_plan_refresh"}),
    (r"/api/intake/resolve", {"POST": "api_intake_resolve"}),
    (r"/api/live", {"GET": "api_live"}),
    (r"/api/devices", {"GET": "api_devices"}),
    (r"/api/worker", {"GET": "api_worker"}),
    (r"/api/worker/check", {"POST": "api_worker_check"}),
    (r"/api/worker/start", {"POST": "api_worker_start"}),
    (r"/api/worker/answer", {"POST": "api_worker_answer"}),
    (r"/api/worker/greenlight", {"POST": "api_worker_greenlight"}),
    (r"/api/worker/cancel", {"POST": "api_worker_cancel"}),
    (r"/api/tasks", {"GET": "api_tasks", "POST": "api_task_create"}),
    (rf"/api/tasks/({TASK_ID})", {"GET": "api_task"}),
    (rf"/api/tasks/({TASK_ID})/state", {"POST": "api_task_state"}),
    (r"/api/results", {"GET": "api_results"}),
    (r"/api/results/cards", {"GET": "api_result_cards"}),
    (r"/api/results/tests", {"GET": "api_result_tests"}),
    (r"/api/results/comment", {"POST": "api_results_comment"}),
    (r"/api/results/comment/post", {"POST": "api_results_comment_post"}),
    (r"/api/stats", {"GET": "api_stats"}),
    (rf"/api/runs/({RUN_ID})", {"GET": "api_run"}),
    (r"/api/history", {"GET": "api_history"}),
    (r"/api/tickets", {"GET": "api_tickets"}),
    (r"/api/tickets/filed", {"POST": "api_ticket_filed"}),
    (r"/api/tickets/file", {"POST": "api_ticket_file"}),
    (r"/api/tickets/draft", {"POST": "api_ticket_draft"}),
    (r"/api/cases", {"GET": "api_cases", "POST": "api_case_create"}),
    (rf"/api/cases/({CASE_ID})", {"GET": "api_case", "PUT": "api_case_update"}),
    (r"/api/files/([A-Za-z0-9-]{3,40})", {"GET": "api_files"}),
    (r"/api/files/([A-Za-z0-9-]{3,40})/remove", {"POST": "api_files_remove"}),
)]
# PUT of one test-file piece (raw body, not JSON): handled by Handler._upload
UPLOAD_RX = re.compile(r"/api/files/([A-Za-z0-9-]{3,40})/([A-Za-z0-9][A-Za-z0-9._-]{0,150})")


class HttpError(Exception):
    def __init__(self, status, message, headers=()):
        super().__init__(message)
        self.status, self.message, self.headers = status, message, headers


def plain(obj):
    """JSON-safe copy with string keys redacted too (redact_obj covers values only)."""
    if isinstance(obj, dict):
        return {redact.redact(str(k)): plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [plain(v) for v in obj]
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    return str(obj)


def json_bytes(obj):
    return json.dumps(redact.redact_obj(plain(obj)), ensure_ascii=False).encode("utf-8")


def _reject_constant(name):
    raise ValueError(f"{name} is not JSON")


def parse_body(raw):
    try:
        body = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise HttpError(400, "body must be a JSON object") from None
    if not isinstance(body, dict):
        raise HttpError(400, "body must be a JSON object")
    return body


def open_regular(path):
    """Open a file read-only without following a final symlink or blocking on a FIFO."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    f = os.fdopen(fd, "rb")
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        f.close()
        raise HttpError(404, "not found")
    return f


def decode_text(raw):
    """A text file's bytes as str: UTF-16 or UTF-32 after its BOM, else UTF-8, errors replaced.

    A NUL left in the text would split values so that the redactor misses them: such a file is refused (415).
    """
    codec = "utf-8"
    for bom, name, unit in BOMS:
        if raw.startswith(bom):
            raw, codec = raw[len(bom):], name
            raw = raw[:len(raw) - len(raw) % unit]
            break
    text = raw.decode(codec, "replace")
    if "\x00" in text:
        raise HttpError(415, "not a UTF-8, UTF-16 or UTF-32 text file")
    return text


def _drop_partial_tail(text):
    """text up to its last line break, else its last space or quote: the read may have cut the value after it."""
    cut = text.rfind("\n")
    if cut < 0:
        cut = max(text.rfind(c) for c in " \t\r\"'")
    return text[:cut + 1] if cut >= 0 else text


def read_text(path):
    """(text, truncated): decode_text of the file, redacted. A file over 2 MiB is redacted first and then cut at
    the last line break before 2 MiB characters (or at 2 MiB without one), so a value at the cut is masked whole."""
    with open_regular(path) as f:
        raw = f.read(security.TEXT_CAP + TEXT_SLACK)
        size = os.fstat(f.fileno()).st_size
    text = decode_text(raw)
    if len(raw) <= security.TEXT_CAP:
        return redact.redact(text), False
    if len(raw) == security.TEXT_CAP + TEXT_SLACK:
        text = _drop_partial_tail(text)
    text = redact.redact(text)
    cut = text.rfind("\n", 0, security.TEXT_CAP)
    text = text[:cut + 1] if cut >= 0 else text[:security.TEXT_CAP]
    return text + f"\n[truncated by the QA app: file is {size} bytes, only its start is shown]\n", True


def _log_safe(s, limit=200):
    out = "".join(c if c.isprintable() and c != '"' else f"\\x{ord(c):02x}" for c in s[:limit])
    return out + ("…" if len(s) > limit else "")


class Handler(BaseHTTPRequestHandler):
    server_version = "qa-app"
    sys_version = ""
    timeout = 20
    _head = False
    _body_read = False

    # --- dispatch -------------------------------------------------------------------------

    def do_GET(self):
        self._serve(self._read)

    def do_HEAD(self):
        self._head = True
        self._serve(self._read)

    def do_POST(self):
        self._serve(self._write)

    def do_PUT(self):
        self._serve(self._upload if self.path.startswith("/api/files/") else self._write)

    def __getattr__(self, name):
        # DELETE, PATCH, OPTIONS, TRACE and any other method: 405 after the request checks
        if name.startswith("do_"):
            return lambda: self._serve(self._not_allowed)
        raise AttributeError(name)

    def _serve(self, fn):
        try:
            self._guard()
            fn()
        except HttpError as e:
            self._drain()
            self._error(e.status, e.message, e.headers)

    def _drain(self):
        """Drop an unread request body (up to 1 MiB) so the client reads the answer instead of a reset."""
        left = 0 if self._body_read else min(security.content_length(self.headers) or 0, DRAIN_CAP)
        try:
            while left > 0:
                chunk = self.rfile.read(min(left, 65536))
                if not chunk:
                    break
                left -= len(chunk)
        except OSError:
            pass

    def _guard(self):
        srv = self.server
        for ok, status, message in (security.check_host(self.headers, srv.qa_port),
                                    security.check_fetch_site(self.headers)):
            if not ok:
                raise HttpError(status, message)

    def _target(self):
        """(path, query dict) of the request target; the path stays percent-encoded."""
        if not self.path.startswith("/"):
            raise HttpError(400, "bad request target")
        path, _, query = self.path.partition("#")[0].partition("?")
        try:
            q = urllib.parse.parse_qs(query, keep_blank_values=True, max_num_fields=20)
        except ValueError:
            raise HttpError(400, "bad query string") from None
        return path, {k: v[0] for k, v in q.items()}

    def _route(self, path, method):
        if path == "/" or path.startswith(("/static/", "/files/")):
            if method in ("GET", "HEAD"):
                return None, ()
            raise HttpError(405, "method not allowed", (("Allow", "GET, HEAD"),))
        for rx, methods in ROUTES:
            m = rx.fullmatch(path)
            if m:
                name = methods.get("GET" if method == "HEAD" else method)
                if name is None:
                    allow = ", ".join(sorted(set(methods) | ({"HEAD"} if "GET" in methods else set())))
                    raise HttpError(405, "method not allowed", (("Allow", allow),))
                return name, m.groups()
        raise HttpError(404, "not found")

    def _not_allowed(self):
        raise HttpError(405, "method not allowed", (("Allow", "GET, HEAD, POST, PUT"),))

    def _read(self):
        path, query = self._target()
        name, args = self._route(path, self.command)
        if path == "/":
            return self._index()
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])
        if path.startswith("/files/"):
            return self._file(path[len("/files/"):])
        self._call(name, args, query)

    def _write(self):
        srv = self.server
        ok, status, message = security.check_write(self.headers, srv.qa_token, srv.qa_port)
        if not ok:
            raise HttpError(status, message)
        n = security.content_length(self.headers)
        raw = self.rfile.read(n) if n else b""
        self._body_read = True
        if len(raw) != n:
            raise HttpError(400, "incomplete body")
        body = parse_body(raw)
        path, query = self._target()
        name, args = self._route(path, self.command)
        self._call(name, args, query, body)

    def _upload(self):
        """One piece of a test file from the Run form (uploads.py); the body is raw bytes, never parsed."""
        srv = self.server
        ok, status, message = security.check_upload(self.headers, srv.qa_token, srv.qa_port, uploads.PIECE_MAX)
        if not ok:
            raise HttpError(status, message)
        path, query = self._target()
        m = UPLOAD_RX.fullmatch(path)
        if not m:
            raise HttpError(404, "not found")
        n = security.content_length(self.headers)
        left = [n]

        def read(k):
            chunk = self.rfile.read(min(k, left[0]))
            left[0] -= len(chunk)
            return chunk
        try:
            try:
                offset, total = int(query.get("offset", "")), int(query.get("total", ""))
            except ValueError:
                raise writes.WriteError(400, "offset and total: whole numbers") from None
            if offset == 0 and not (plan.in_todays_list(srv.qa_root, m.group(1))
                                    or plan.load_intake(srv.qa_root, m.group(1))):
                # a test of the newest Today list (an old list's Run form) keeps Today's wording
                listed = any(t.get("task_id") == m.group(1) for t in (plan.load(srv.qa_root) or {}).get("tests", []))
                raise writes.WriteError(409, "that test is not in today's list" if listed else
                                        "open the test link first")
            res = uploads.put_piece(srv.qa_root, m.group(1), m.group(2), offset, total, n, read)
        except writes.WriteError as e:
            while left[0] > 0 and read(1 << 20):  # drop the rest of the piece so the browser reads the answer
                pass
            self._body_read = True
            raise HttpError(*_write_error(e)) from None
        self._body_read = True
        self._json(200, res)

    def _call(self, name, args, query, body=None):
        """Run one API handler; KeyError is 404 and writes.WriteError keeps its status."""
        try:
            status, obj = getattr(self, name)(*args, query=query, body=body)
        except HttpError:
            raise
        except KeyError:
            raise HttpError(404, "not found") from None
        except (writes.WriteError, worker.WorkerError) as e:
            raise HttpError(*_write_error(e)) from None
        except Exception as e:  # noqa: BLE001 - never a traceback to the client
            self._log_exception(e)
            raise HttpError(500, "internal error") from None
        self._json(status, obj)

    # --- API handlers: (status, object) ----------------------------------------------------

    def api_meta(self, query, body):
        return 200, self.server.data.meta(self.server.qa_port)

    def api_live(self, query, body):
        return 200, self.server.data.live()

    def api_devices(self, query, body):
        # New test's device boxes: the engine's status, read at most every devstatus.TTL_S seconds (read only)
        snap, err, at = self.server.devices.get()
        return 200, {"at": devstatus.iso(at), "error": err,
                     "rows": devstatus.rows(snap, err, set(self.server.writer.target_tags()))}

    def _with_prep(self, out):
        snap = out.get("snapshot")
        prep, status = plan.load_prep(self.server.qa_root, snap["day"]) if snap else (None, None)
        out.update({"today": plan.zurich_day(), "prep": prep, "prep_status": status})
        return out

    def api_plan(self, query, body):
        return 200, self._with_prep({"snapshot": plan.load(self.server.qa_root)})

    def api_plan_refresh(self, query, body):
        # reads ClickUp (GET only, clickup_read) and writes today's snapshot through the Writer
        return 200, self._with_prep(plan.refresh(self.server.qa_root, self.server.writer))

    def api_intake_resolve(self, query, body):
        # New test (DESIGN.md "Simple flow"): reads the one task the link names (GET only, clickup_read) and keeps a
        # redacted copy through the Writer
        try:
            return 200, plan.resolve_intake(self.server.qa_root, self.server.writer, body.get("url"))
        except plan.PrepError as e:
            raise HttpError(400, str(e)) from None
        except clickup_read.ClickUpError as e:
            raise HttpError(409 if e.kind == "no_token" else 502, e.message) from None

    # QA worker (DESIGN.md 8.3): the app asks, the worker session answers through worker.py.

    def api_worker(self, query, body):
        # New test's 'Your plans' (2026-11-04): each New test request carries its title, from the server's one title
        # rule (views.request_title), so the page keeps no copy of it; any other request's title is null. A start
        # request that has its task is titled like its test on Results and in History (views.test_title, API.md 1.3:
        # one name everywhere; 2026-11-05, wingmate-debug FINDINGS #13), a Today start too
        out = worker.view(self.server.qa_root)
        reqs = out.get("requests") or []
        ctx = views._Ctx(self.server.data) if any(r.get("type") == "start" and r.get("task") for r in reqs) else None
        for r in reqs:
            task = ctx.tasks.get(r.get("task")) if ctx is not None and r.get("type") == "start" \
                and isinstance(r.get("task"), str) else None
            r["title"] = views.test_title(ctx, task, views.TITLE_LIST) if isinstance(task, dict) \
                else views.request_title(r)
        return 200, out

    def _intake_check(self, body):
        """New test's Prepare plan: the device check and the worker's plan for a task read by /api/intake/resolve."""
        root = self.server.qa_root
        tid = body.get("task_id")
        if not isinstance(tid, str) or not re.fullmatch(r"[A-Za-z0-9-]{3,40}", tid) or tid in plan.NEVER_READ:
            raise HttpError(400, "task_id: the ClickUp task id of the test link")
        guidance = writes.guidance_text(body.get("guidance"))
        if guidance is None:
            raise HttpError(400, f"guidance: say what to test (1-{writes.GUIDANCE_MAX} characters)")
        registered = self.server.writer.target_tags()
        targets = body.get("targets")
        if (not isinstance(targets, list) or not targets or len(set(map(str, targets))) != len(targets)
                or any(t not in registered for t in targets)):
            raise HttpError(400, "targets: some of " + ", ".join(registered))
        intake = plan.load_intake(root, tid)
        if intake is None:
            raise HttpError(409, "open the test link again")
        cu = {k: intake.get(k) for k in ("task_id", "custom_id", "title", "url", "priority", "status")}
        targets = sorted(targets, key=lambda t: worker.TAG_ORDER.index(t) if t in worker.TAG_ORDER else 99)
        fields = intake_fields(root, tid, cu, intake.get("cases") or [], guidance, targets)
        # Prepare again on an answered plan (2026-11-04): the new plan carries the same answers, so the worker does not
        # ask them again
        src = body.get("answers_from")
        if src is not None:
            prev = worker.load_request(root, src) if isinstance(src, str) and worker.REQ_ID_RE.match(src) else None
            first = worker.load_request(root, (prev or {}).get("follows")) if isinstance((prev or {}).get("follows"), str) \
                and worker.REQ_ID_RE.match(prev["follows"]) else None
            if not prev or prev.get("intake") is not True or not first or \
                    (prev.get("clickup") or {}).get("task_id") != tid or not isinstance(prev.get("answers"), list):
                raise HttpError(400, "answers_from: an answered plan of this test")
            needs = [n for n in ((first.get("result") or {}).get("needs") or []) if isinstance(n, dict)]
            # each answer on its own (2026-11-05, wingmate-debug FINDINGS #14): the ones that still fit carry over; one
            # that no longer does (a file removed or changed since) is dropped with its reason, so Hover asks only
            # that question again. With none left the new plan asks every question again
            answers, dropped = [], []
            for a in prev["answers"]:
                try:
                    answers += worker.validate_answers([a], needs, fields["files"])
                except worker.WorkerError as e:
                    dropped.append({"need": a.get("need") if isinstance(a, dict) else None, "why": e.message})
            if answers:
                fields.update(follows=first["id"], answers=answers)
            if dropped:
                fields["answers_dropped"] = dropped
        return 201, self.server.writer.create_request(fields, exclusive=tid)

    def api_worker_answer(self, query, body):
        # New test's "Needs your input" (2026-11-04): the person's answers to a plan's needs make a follow-up check of
        # the same test, devices, description and files (intake_fields) with follows and answers; the waiting worker
        # reads the old plan, applies the answers and answers with a new plan
        srv = self.server
        body = body if isinstance(body, dict) else {}
        cid = body.get("check")
        check = worker.load_request(srv.qa_root, cid) if isinstance(cid, str) and worker.REQ_ID_RE.match(cid) else None
        if not check or check.get("type") != "check":
            raise HttpError(404, "no such plan")
        if check.get("intake") is not True or check.get("state") != "done" or not isinstance(check.get("result"), dict):
            raise HttpError(409, "this plan is not finished")
        needs = check["result"].get("needs")
        needs = [n for n in needs if isinstance(n, dict)] if isinstance(needs, list) else []
        if not needs:
            raise HttpError(400, "this plan has no questions")
        if worker.followers(srv.qa_root, check["id"]):
            raise HttpError(409, "already answered; open the newer plan")
        if worker.newer_plan(srv.qa_root, check):
            raise HttpError(409, "a newer plan of this test exists; open it")
        cu = dict(check["clickup"]) if isinstance(check.get("clickup"), dict) else {}
        tid = cu.get("task_id")
        if plan.load_intake(srv.qa_root, tid) is None:  # also None for a missing or never-read task id
            raise HttpError(409, "open the test link again")
        fields = intake_fields(srv.qa_root, tid, cu, list(check.get("clickup_cases") or []), check.get("guidance"),
                               list(check.get("targets") or []))
        # the answered files must be among the files the follow-up records (one snapshot for both)
        fields.update(follows=check["id"],
                      answers=worker.validate_answers(body.get("answers"), needs, fields["files"]))
        return 201, srv.writer.create_request(fields, exclusive=tid)

    def api_worker_check(self, query, body):
        root = self.server.qa_root
        if isinstance(body, dict) and body.get("intake") is True:
            return self._intake_check(body)
        tid = body.get("task_id") if isinstance(body, dict) else None
        if not isinstance(tid, str) or not re.fullmatch(r"[A-Za-z0-9-]{3,40}", tid):
            raise HttpError(400, "task_id: a ClickUp task id from today's list")
        # the Run form: the person's guidance and the devices they ticked; Prepare asks the worker for a summary too
        guidance = writes.guidance_text(body.get("guidance"))
        prepare = body.get("prepare") is True
        # files attached on the Run form: the cases that need input are offered to the worker too
        files = uploads.list_files(root, tid) if prepare else []
        try:
            p = plan.run_plan(root, tid, with_pending=bool(files))
        except plan.PrepError as e:
            raise HttpError(409, str(e)) from None
        registered = self.server.writer.target_tags()
        allowed = [t for t in p["targets"] if t in registered]
        targets = body.get("targets")
        if targets is not None and (not isinstance(targets, list) or not targets
                                    or len(set(map(str, targets))) != len(targets)
                                    or any(t not in allowed for t in targets)):
            raise HttpError(400, "targets: some of " + ", ".join(allowed))
        chosen = allowed if targets is None else [t for t in allowed if t in targets]

        def narrow(rows):
            out = []
            for c in rows:
                on = [t for t in c.get("targets") or [] if t in chosen]
                if on:
                    out.append(dict(c, targets=on))
            return out
        cases, pending = narrow(p["cases"]), narrow(p.get("pending") or [])
        if not cases and not pending:
            raise HttpError(400, "targets: no ready case runs on the chosen devices")
        engine = []
        for c in cases:
            engine += [e for e in c.get("engine_cases") or [] if e not in engine]
        p = dict(p, cases=cases, engine_cases=engine,
                 targets=[t for t in chosen if any(t in c["targets"] for c in cases + pending)])
        p.pop("pending", None)
        # the case file each engine case runs from now: the summary names it, Start refuses if it changed since
        ids = engine + [e for c in pending for e in c.get("engine_cases") or []] if files else engine
        fields = dict(p, type="check", case_versions=plan.case_versions(root, ids))
        if prepare:
            fields.update(prepare=True, guidance=guidance)
            if files:
                fields.update(pending=pending, files=[{k: f[k] for k in ("name", "size", "sha256", "path")}
                                                      for f in files])
        return 201, self.server.writer.create_request(fields, exclusive=tid)

    def api_worker_start(self, query, body):
        body = body if isinstance(body, dict) else {}
        cid, targets = body.get("check"), body.get("targets")
        check = worker.load_request(self.server.qa_root, cid) if isinstance(cid, str) and worker.REQ_ID_RE.match(cid) \
            else None
        passed = worker.check_usable(check)
        if worker.blocking_needs(check):  # New test (2026-11-04): Run waits for the answers (a follow-up plan)
            raise HttpError(409, "answer the questions on the plan first")
        if (not isinstance(targets, list) or not targets or len(set(map(str, targets))) != len(targets)
                or any(t not in passed for t in targets)):
            raise HttpError(400, "targets: devices that passed the check")
        # the attached files the plan uses must still be the ones Prepare saw (not removed or replaced since)
        now = {(f["name"], f["sha256"]) for f in uploads.list_files(self.server.qa_root,
                                                                    (check.get("clickup") or {}).get("task_id"))} \
            if check.get("files") else set()
        used = {x.get("name") for x in (check.get("result") or {}).get("plan_files") or [] if isinstance(x, dict)}
        if any((f.get("name"), f.get("sha256")) not in now for f in check.get("files") or [] if f.get("name") in used):
            raise HttpError(409, "the attached files changed since " +
                            ("the plan" if check.get("intake") is True else "Prepare summary") + "; prepare again")
        # the case files of the cases that can run must still be the ones the summary (or the device check) was written
        # from: a newer case version means a new plan
        runnable = {e for c in check.get("cases") or [] if isinstance(c, dict) for e in c.get("engine_cases") or []}
        runnable |= {e for e in (check.get("result") or {}).get("complete") or [] if isinstance(e, str)}
        seen = [v for v in check.get("case_versions") or [] if isinstance(v, dict) and v.get("id") in runnable]
        if check.get("intake") is True:
            # New test: the plan's cases with a ticked device, as the plan pinned them
            seen = [pc for pc in (check.get("result") or {}).get("plan_cases") or []
                    if isinstance(pc, dict) and any(t in targets for t in pc.get("targets") or [])]
        now_v = {v["id"]: v for v in plan.case_versions(self.server.qa_root, [v["id"] for v in seen])}
        changed = [v["id"] for v in seen if (now_v[v["id"]]["file"], now_v[v["id"]]["sha256"]) !=
                   (v.get("file"), v.get("sha256"))]
        if changed:
            since = ("the plan; prepare again" if check.get("intake") is True else
                     "Prepare summary; prepare again" if check.get("prepare") else "the device check; check again")
            raise HttpError(409, f"case {', '.join(changed)} changed since {since}")
        return 201, self.server.writer.start_run(check, targets)

    def api_worker_greenlight(self, query, body):
        body = body if isinstance(body, dict) else {}
        return 201, self.server.writer.greenlight(body.get("task"), body.get("outputs"))

    def api_worker_cancel(self, query, body):
        rid = body.get("id") if isinstance(body, dict) else None
        req = worker.cancel(self.server.qa_root, rid)
        # a cancelled start never ran; a released one (Hover stopped working on it) may have runs, which then are its
        # task's results (writes.close_started_task, 2026-11-05)
        how = "cancelled in the app before any run" if req["state"] == "cancelled" else \
            "released in the app: Hover showed no sign of work for 20 minutes"
        self.server.writer.close_started_task(req, f"Start {req['id']} {how}.")
        return 200, req

    def api_tasks(self, query, body):
        return 200, self.server.data.tasks()

    def api_task(self, task_id, query, body):
        return 200, self.server.data.task(task_id)

    def api_results(self, query, body):
        return 200, self.server.data.results(case=_param(query, "case"), task=_param(query, "task"))

    def api_result_cards(self, query, body):
        # Results page (2026-11-02): one card per test, newest first (views.py); since 2026-11-03 also the running tests
        # and each card's Report issue and results comment, "posting" while this server posts that comment. Since
        # 2026-11-04 ?task=<id>|current and ?case=<id> answer one test (views.result_cards, results redesign API.md
        # section 2): "current" is accepted here only; a test or a case run that does not exist is 404
        posting = self.server.comment_posting
        posting = {posting} if posting else set()
        task = "current" if query.get("task") == "current" else _param(query, "task")
        case = _param(query, "case")
        if task is None and case is None:
            return 200, views.result_cards(self.server.data, posting=posting)
        try:
            return 200, views.result_cards(self.server.data, posting=posting, task=task, case=case)
        except views.NotFound as e:
            raise HttpError(404, str(e)) from None

    def api_result_tests(self, query, body):
        # History's 'Your tests' (2026-11-04): Ongoing, Tested and Done (views.tests); a query string is ignored
        posting = self.server.comment_posting
        return 200, views.tests(self.server.data, posting={posting} if posting else set())

    def api_stats(self, query, body):
        # History page (2026-11-02): QA and reliability numbers over the last N days (views.py)
        days = _param(query, "days")
        if days is not None and not 1 <= int(days) <= views.DAYS_MAX:
            raise HttpError(400, f"days: a whole number from 1 to {views.DAYS_MAX}")
        return 200, views.quality_stats(self.server.data, days=int(days) if days else 30)

    def api_run(self, run_id, query, body):
        return 200, self.server.data.run(run_id)

    def api_history(self, query, body):
        case, target = _param(query, "case"), _param(query, "target")
        if case is None or target is None:
            raise HttpError(400, "case and target are required")
        return 200, self.server.data.history(case, target)

    def api_tickets(self, query, body):
        return 200, self.server.data.tickets()

    def api_files(self, task_id, query, body):
        return 200, {"files": uploads.list_files(self.server.qa_root, task_id), "max_bytes": uploads.MAX_BYTES,
                     "piece_bytes": uploads.PIECE_MAX // 2, "extensions": list(uploads.EXTENSIONS)}

    def api_files_remove(self, task_id, query, body):
        if worker.open_for(self.server.qa_root, task_id, ("start",)):
            raise HttpError(409, "a run of this test is open and may use the file; remove it after the run")
        return 200, uploads.remove(self.server.qa_root, task_id, (body or {}).get("name"))

    def api_cases(self, query, body):
        return 200, self.server.data.cases()

    def api_case(self, case_id, query, body):
        return 200, self.server.data.case(case_id)

    def api_task_create(self, query, body):
        return 201, self.server.writer.create_task(body)

    def api_task_state(self, task_id, query, body):
        # the app is always the user; by=claude and user_words exist only for /qa-run's CLI. Mark done (to: closed)
        # waits while this server posts the test's comment, and (writes.set_task_state, under the lock) while a write,
        # draft or file request of the test is open: the page's own rule, for a tab that read the test earlier
        # (2026-11-05, wingmate-debug FINDINGS #28)
        if body.get("to") == "closed" and self.server.comment_posting == task_id:
            raise HttpError(409, views.MARK_WHY["posting"])
        return 200, self.server.writer.set_task_state(task_id, body.get("to"), note=body.get("note"),
                                                      by="user", via="app")

    def api_case_create(self, query, body):
        return 201, self.server.writer.create_case(body)

    def api_case_update(self, case_id, query, body):
        return 201, self.server.writer.edit_case(case_id, body)

    def api_ticket_filed(self, query, body):
        return 201, self.server.writer.mark_filed(body)

    def api_ticket_file(self, query, body):
        # File ticket on a Results card (2026-11-03): the person's OK is this press plus Touch ID (or the account
        # password) on this Mac, which the AI cannot give; then the worker files the draft with the Swiss Army Knife on
        # the device of the failed run (worker request "file"). One prompt at a time.
        srv = self.server
        d = body.get("dir") if isinstance(body, dict) else None
        d = d.rstrip("/") if isinstance(d, str) else d
        if not isinstance(d, str) or not approvals.TICKET_DIR_RE.match(d):
            raise HttpError(400, "dir: tests/<yyyy-mm-dd-slug>/tickets/<n>")
        if not srv.filing.acquire(blocking=False):
            raise HttpError(409, f"a {srv.prompt_for or 'File ticket'} prompt is already open on this Mac")
        try:
            srv.prompt_for = "File ticket"
            try:
                draft = approvals.load_draft(srv.qa_root, d)
            except approvals.ApprovalError as e:
                raise HttpError(e.status, e.message) from None
            rid, target, problem = ticket_run(srv.data, draft["draft"])
            if problem:
                raise HttpError(400, problem)
            if draft["draft"].get("finding") != "confirmed":  # security review 2026-11-03: as sak-submit checks (R1)
                raise HttpError(400, "the finding is not confirmed (draft.json finding); only confirmed findings are "
                                     "filed, so nobody is asked for a fingerprint that sak-submit would then refuse")
            why = srv.writer.file_conflict(d)
            if why:
                raise HttpError(409, why)
            reason = f"File the ticket '{reason_title(draft['title'])}' with the Swiss Army Knife on {target}"
            try:
                appr = approvals.approve(srv.qa_root, d, target, reason, prompt=srv.touchid_prompt)
            except approvals.ApprovalError as e:
                raise HttpError(e.status, e.message) from None
            req = srv.writer.create_file_request({"type": "file", "ticket_dir": d, "target": target,
                                                  "approval_id": appr["id"], "task": task_of_run(srv.data, rid),
                                                  "run": rid})
            return 200, {"request": req, "approval_id": appr["id"]}
        finally:
            srv.prompt_for = None
            srv.filing.release()

    def api_ticket_draft(self, query, body):
        # Report issue on a Results card (2026-11-03): the worker drafts the Swiss Army Knife ticket of the card's
        # newest failed run that has no draft yet (worker request "draft", bug-draft); filing stays File ticket + Touch ID
        srv = self.server
        body = body if isinstance(body, dict) else {}
        task, case = body.get("task"), body.get("case")
        if not isinstance(task, str) or not re.fullmatch(TASK_ID, task):
            raise HttpError(400, "task: the card's task id (T-yyyymmdd-nnn)")
        if not isinstance(case, str) or not re.fullmatch(CASE_ID, case):
            raise HttpError(400, "case: the card's case id")
        entries = views.card_entries(srv.data, task, case)
        if not entries:
            raise HttpError(404, f"no Results card for {task} and {case}")
        busy = worker.draft_open(srv.qa_root, task, case)
        if busy:
            raise HttpError(409, f"{busy[0]['id']} is still drafting a ticket for this test")
        candidates, why = [], None
        for e in entries:  # newest first
            rid, _, problem = run_fileable(srv.data, e["id"])
            if problem is None:
                candidates.append(rid)
            elif why is None and _run_failed(e["erec"]):
                why = problem  # a failed run that could not be filed: say why
        if not candidates:
            raise HttpError(400, why or "nothing failed in this test: no run failed or has a failed step")
        by_run = views._ticket_drafts(srv.data)
        free = [r for r in candidates if not any(t.get("dir") for t in by_run.get(r, []))]
        if not free:
            raise HttpError(409, "a ticket is already drafted for each failed run of this test")
        req = srv.writer.create_draft_request({"type": "draft", "task": task, "case": case, "run": free[0]})
        return 200, {"request": req}

    def api_results_comment(self, query, body):
        # Add result on a Results card (2026-11-03): the worker writes the results comment for the ClickUp test (the
        # greenlight's write request, outputs ["comment"]); the person reads it on the card, then posts it (Post comment)
        srv = self.server
        tid = body.get("task") if isinstance(body, dict) else None
        if not isinstance(tid, str) or not re.fullmatch(TASK_ID, tid):
            raise HttpError(400, "task: the card's task id (T-yyyymmdd-nnn)")
        task = tasks.load_task(srv.qa_root, tid)
        if task is None:
            raise HttpError(404, f"no task {tid}")
        d = views.comment_draft(srv.data, task)
        if (d and d["posted"] is not None) or tid in srv.posted_now:
            raise HttpError(409, "the results comment of this test was already posted")
        if d and d["text"] and not d["problem"]:
            raise HttpError(409, "a results comment is already drafted for this test")
        return 200, {"request": srv.writer.request_comment(tid)}

    def _comment_to_post(self, tid, sha, after_prompt=False):
        """(comment draft, (ClickUp task id, title)) when the task's results comment can be posted now and its text
        still has this sha256; HttpError otherwise: 404 no task, 409 nothing to post, posted already, the text changed,
        no ClickUp task id, no token, or a posted marker the Writer would refuse (checked before anything is posted)."""
        srv = self.server
        task = tasks.load_task(srv.qa_root, tid)
        if task is None:
            raise HttpError(404, f"no task {tid}")
        d = views.comment_draft(srv.data, task)
        if d is None:
            raise HttpError(409, "there is no results comment to post yet; press Add result first")
        if d["posted"] is not None or tid in srv.posted_now:
            raise HttpError(409, "the results comment of this test was already posted")
        if d["problem"]:
            raise HttpError(409, d["problem"])
        if d["sha256"] != sha:
            raise HttpError(409, "the comment changed while the Touch ID prompt was open; nothing was posted. Read it "
                                 "again." if after_prompt else
                            "the comment changed since the page showed it; read it again before posting")
        try:
            target = clickup_post.comment_target(srv.qa_root, task)
        except clickup_post.PostError as e:
            raise HttpError(409, e.message) from None
        no_token = clickup_post.token_problem(srv.qa_root)
        if no_token:
            raise HttpError(409, no_token)
        try:
            srv.writer.posted_target(d["posted_rel"])
        except writes.WriteError as e:
            raise HttpError(*_write_error(e)) from None
        return d, target

    def api_results_comment_post(self, query, body):
        # Post comment on a Results card (2026-11-03, the person's choice): the person's OK is this press plus Touch ID
        # (or the account password) on this Mac, which the AI cannot give. Then the server itself posts the text the
        # card showed (its sha256) to the test's ClickUp task (clickup_post.py, the app's one ClickUp write) and records
        # it in the posted marker, so it is never posted twice. One Touch ID prompt at a time, shared with File ticket.
        srv = self.server
        body = body if isinstance(body, dict) else {}
        tid, sha = body.get("task"), body.get("sha256")
        if not isinstance(tid, str) or not re.fullmatch(TASK_ID, tid):
            raise HttpError(400, "task: the card's task id (T-yyyymmdd-nnn)")
        if not isinstance(sha, str) or not SHA256_RE.fullmatch(sha):
            raise HttpError(400, "sha256: the sha256 of the comment text the card shows")
        if not srv.filing.acquire(blocking=False):
            raise HttpError(409, f"a {srv.prompt_for or 'Touch ID'} prompt is already open on this Mac")
        try:
            srv.prompt_for, srv.comment_posting = "Post comment", tid
            _, target = self._comment_to_post(tid, sha)
            try:
                approvals.confirm(f"Post the results comment to '{reason_title(target[1])}'", prompt=srv.touchid_prompt)
            except approvals.ApprovalError as e:
                raise HttpError(e.status, e.message) from None
            d, again = self._comment_to_post(tid, sha, after_prompt=True)
            if again[0] != target[0]:
                raise HttpError(409, "the test's ClickUp task changed while the Touch ID prompt was open; nothing was "
                                     "posted")
            try:
                res = clickup_post.post_comment(srv.qa_root, target[0], d["text"], opener=srv.clickup_opener)
            except clickup_post.PostError as e:
                raise HttpError(502 if e.kind in ("refused", "offline") else 409, e.message) from None
            srv.posted_now.add(tid)  # until the marker is on disk, and if writing it fails
            marker = {"comment_id": res["comment_id"], "posted_at": res["posted_at"], "sha256": sha,
                      "task_id": target[0]}
            try:
                srv.writer.write_posted(d["posted_rel"], marker)
            except writes.WriteError as e:
                raise HttpError(500, f"The comment was posted to ClickUp, but the app could not record it "
                                     f"({_write_error(e)[1]}); do not post it again.") from None
            return 200, {"posted_at": res["posted_at"], "comment_id": res["comment_id"]}
        finally:
            srv.prompt_for = srv.comment_posting = None
            srv.filing.release()

    # --- page, static and /files/ ----------------------------------------------------------

    def _index(self):
        try:
            with open_regular(os.path.join(STATIC_DIR, "index.html")) as f:
                page = f.read().decode("utf-8")
        except OSError:
            raise HttpError(404, "index.html not found") from None
        page = page.replace(TOKEN_SLOT, html.escape(self.server.qa_token, quote=True))
        self._send(200, page.encode("utf-8"), STATIC_TYPES[".html"], (("Content-Security-Policy", security.CSP),))

    def _static(self, name):
        if name == "index.html":
            return self._index()
        ext = os.path.splitext(name)[1].lower()
        try:
            present = name in os.listdir(STATIC_DIR)
        except OSError:
            present = False
        if not present or not STATIC_NAME.fullmatch(name) or ext not in STATIC_TYPES:
            raise HttpError(404, "not found")
        try:
            with open_regular(os.path.join(STATIC_DIR, name)) as f:
                body = f.read()
        except OSError:
            raise HttpError(404, "not found") from None
        extra = (("Content-Security-Policy", security.CSP),) if ext in (".html", ".svg") else ()
        self._send(200, body, STATIC_TYPES[ext], extra)

    def _file(self, rel):
        srv = self.server
        try:
            path = security.resolve_read(srv.qa_root, rel, getattr(srv.data, "image_allowed", None),
                                         lambda r: views.fixture_allowed(srv.data, r))
        except security.ReadError as e:
            raise HttpError(e.status, e.message) from None
        if security.file_kind(path) == "download":
            return self._download(path)
        try:
            if security.file_kind(path) == "image":
                with open_regular(path) as f:
                    body = f.read(IMAGE_CAP + 1)
                if len(body) > IMAGE_CAP:
                    raise HttpError(413, "image larger than 32 MiB")
                ctype = security.IMAGE_TYPES[os.path.splitext(path)[1].lower()]
            else:
                text, truncated = read_text(path)
                body = text.encode("utf-8")
                is_json = path.lower().endswith(".json") and not truncated
                ctype = JSON_TYPE if is_json else TEXT_TYPE
        except OSError:
            raise HttpError(404, "not found") from None
        self._send(200, body, ctype)

    def _download(self, path):
        """A case's flight plan as a file to save: JSON, redacted like every text the app serves, whole or not at all."""
        try:
            with open_regular(path) as f:
                raw = f.read(security.TEXT_CAP + 1)
        except OSError:
            raise HttpError(404, "not found") from None
        if len(raw) > security.TEXT_CAP:
            raise HttpError(413, "file larger than 2 MiB")
        body = redact.redact(decode_text(raw)).encode("utf-8")
        name = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(path)) or "download.flightplan"
        self._send(200, body, JSON_TYPE, (("Content-Disposition", f'attachment; filename="{name}"'),))

    # --- responses and logging -------------------------------------------------------------

    def _send(self, status, body, ctype, extra=()):
        self.send_response(status)
        for k, v in security.SECURITY_HEADERS:
            self.send_header(k, v)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        if not self._head and getattr(self, "command", None) != "HEAD":
            self.wfile.write(body)

    def _json(self, status, obj, extra=()):
        self._send(status, json_bytes(obj), JSON_TYPE, extra)

    def _error(self, status, message, extra=()):
        self._json(status, {"error": message}, extra)

    def send_error(self, code, message=None, explain=None):
        """Protocol errors from http.server (bad request line, oversized headers): JSON, no echo."""
        self.close_connection = True
        try:
            phrase = http.HTTPStatus(code).phrase.lower()
        except ValueError:
            phrase = "error"
        self._error(code, phrase)

    def log_request(self, code="-", size="-"):
        code = int(code) if isinstance(code, int) else code
        path = (getattr(self, "path", "") or "").partition("?")[0].partition("#")[0]
        self.log_message('"%s %s" %s', _log_safe(getattr(self, "command", None) or "-", 16), _log_safe(path), code)

    def log_error(self, format, *args):
        self.log_message("%s", _log_safe(format % args if args else format))

    def log_message(self, format, *args):
        line = redact.redact(format % args).replace(self.server.qa_token, redact.MASK)
        sys.stderr.write(f"{self.address_string()} [{self.log_date_time_string()}] {line}\n")

    def _log_exception(self, e):
        tb = traceback.extract_tb(e.__traceback__)
        where = f"{os.path.basename(tb[-1].filename)}:{tb[-1].lineno}" if tb else "?"
        self.log_message("error %s at %s", type(e).__name__, where)

    def handle(self):
        try:
            super().handle()
        except (ConnectionError, TimeoutError):
            self.close_connection = True

    def finish(self):
        try:
            super().finish()
        except (ConnectionError, TimeoutError):
            pass


def _param(query, name):
    """A validated query parameter, or None when absent or empty."""
    v = query.get(name)
    if not v:
        return None
    if not QUERY_RX[name].fullmatch(v):
        raise HttpError(400, f"bad {name} parameter")
    return v


def intake_fields(root, tid, clickup, clickup_cases, guidance, targets):
    """The fields of a New test check request (DESIGN.md 8.14), from Prepare plan and from answered needs alike: the
    ClickUp test, its cases, the description, the devices, the test's attached files now and empty case lists. Since
    2026-11-10 also clickup_description, the task's ClickUp text as saved from its link (worker.clickup_description:
    redacted, at most worker.CLICKUP_TEXT_MAX characters, "" when there is none), read again for each request, so the
    worker plans from the task's own words (its test setup included) without opening the saved copy."""
    files = [{k: f[k] for k in ("name", "size", "sha256", "path")} for f in uploads.list_files(root, tid)]
    return {"type": "check", "intake": True, "prepare": True, "clickup": clickup, "clickup_cases": clickup_cases,
            "clickup_description": worker.clickup_description(root, tid),
            "guidance": guidance, "targets": targets, "cases": [], "engine_cases": [], "fix_commits": [],
            "case_versions": [], "files": files}


def reason_title(raw):
    """The draft title for the Touch ID reason: one line of printable text, redacted, at most REASON_TITLE_MAX."""
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
    text = redact.redact(" ".join("".join(c if c.isprintable() else " " for c in text).split()))
    return text if len(text) <= REASON_TITLE_MAX else text[:REASON_TITLE_MAX - 1].rstrip() + "…"


def _run_failed(rec):
    """A FAIL, or a PARTIAL or BLOCKED run with a step whose check or readback failed: views.run_failed, the one rule,
    which the Results answer also uses for File ticket (2026-11-04)."""
    return views.run_failed(rec)


def ticket_run(store, draft):
    """(run id, target tag, None) for a draft whose run record (draft.json run_record) failed on the engine account,
    else (run id or None, target or None, why not). A run corrected since (<id>c1) counts as its correction says."""
    rr = draft.get("run_record") if isinstance(draft, dict) else None
    if not isinstance(rr, str) or not rr.endswith(".json"):
        return None, None, ("the draft names no run record (draft.json run_record); a run on the engine account must "
                            "show the bug first")
    return run_fileable(store, os.path.basename(rr)[:-5])


def run_fileable(store, rid):
    """(run id, target tag, None) for a run whose newest record failed (a FAIL, or a PARTIAL or BLOCKED run with a
    failed check) on the verified engine account on a filing target, else (run id or None, target or None, why not).
    The engine account is engine.json's account (qa-stag since 2026-11-04, before that shared-stag): a run on an
    earlier engine account is not filed. The rule File ticket applies to a draft's run (ticket_run) and Report issue
    to a card's runs (2026-11-03)."""
    idx = store._runs()
    item = idx["runs"].get(rid)
    if item is None:
        return None, None, f"the draft's run {rid} is not among the run records"
    eff = data.newest(rid, idx["run_succ"])
    rec = (idx["runs"].get(eff) or item)["rec"]
    if not _run_failed(rec):
        v = views._vobj(rec).get("v") or "no verdict"
        return rid, None, f"run {eff} is {v} without a failed step: nothing to file"
    # the account and target rule lives in views (file_rule, 2026-11-05), so Report issue, File ticket, the next press
    # and the unreported counts on Results and History follow the same rule as this check
    engine, target = store._engine().get("account"), item["base"]["target"]
    block = views.file_rule(rec, target, engine)
    if block and block[0] == "account":
        who = engine if isinstance(engine, str) and engine else "none is set in engine.json"
        return rid, None, f"run {eff} was not on the verified engine account ({who}); only its runs are filed"
    if block:
        return rid, None, f"tickets are filed on {', '.join(approvals.TARGETS)}; run {rid} ran on {target}"
    return rid, target, None


def task_of_run(store, rid):
    """The newest task that lists the run, the run it corrects or its correction; None when no task does."""
    idx = store._runs()
    ids, cur = {rid, data.newest(rid, idx["run_succ"])}, rid
    while cur in idx["runs"]:
        cur = data.sup_id(idx["runs"][cur]["rec"].get("supersedes"))
        if not cur or cur in ids:
            break
        ids.add(cur)
    best = None
    for tid, t in store._tasks().items():
        if any(r in ids for r in store._task_runs(t)):
            key = (t.get("created") or "", tid)
            best = max(best, (key, tid)) if best else (key, tid)
    return best[1] if best else None


def _write_error(e):
    """(status, message) of a writes.WriteError; capped, since a writer message may quote a field."""
    status = getattr(e, "status", 500)
    if not isinstance(status, int) or not 400 <= status <= 599:
        status = 500
    message = str(getattr(e, "message", "write refused"))
    return status, message if len(message) <= MESSAGE_CAP else message[:MESSAGE_CAP] + "…"


class QAServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_port = False  # no second listener on the same port

    def __init__(self, root, port, host):
        super().__init__((host, port), Handler)
        self.qa_root = root
        self.qa_port = self.server_address[1]
        self.qa_token = secrets.token_urlsafe(32)
        self.filing = threading.Lock()  # one Touch ID prompt at a time: File ticket and Post comment share it
        self.prompt_for = None  # what the open prompt is for, while the lock is held ("File ticket", "Post comment")
        self.comment_posting = None  # the task whose results comment is being posted (the card shows "posting")
        self.posted_now = set()  # tasks whose comment this server posted (refused again even if the marker failed)
        self.touchid_prompt = approvals.run_touchid  # tests put a fake here; nothing else changes it
        self.clickup_opener = None  # clickup_post's own opener (no redirects); tests put a fake here
        try:
            self.data = data.Data(root)
            self.writer = writes.Writer(root)
            self.devices = devstatus.Cache(root)
        except BaseException:
            self.server_close()
            raise

    def server_bind(self):
        # HTTPServer.server_bind calls getfqdn, a reverse DNS lookup that can stall; the name is not needed
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, TimeoutError)):
            return
        sys.stderr.write(f"qa-app: unhandled {type(exc).__name__} while serving a request\n")


def build_server(root, port=8787, host="127.0.0.1"):
    """A bound, not yet serving QAServer; port 0 picks a free port (see .qa_port)."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is None or ip.version != 4 or not ip.is_loopback:
        raise ValueError("the QA app binds a loopback IPv4 address only")
    if not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("port must be 0-65535")
    return QAServer(os.path.abspath(root), port, host)


def startup_line(port):
    """What serve.py prints when it starts: the URL and the UTC time, so a restart can be found in the log."""
    return f"http://127.0.0.1:{port}/ started {data.now_iso()}"


def main(argv):
    ap = argparse.ArgumentParser(prog="serve.py", description="Local web view over the QA engine.")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--root", default=DEFAULT_ROOT, help="workspace folder holding qa/ and tests/")
    a = ap.parse_args(argv)
    root = os.path.abspath(a.root)
    if not os.path.isdir(os.path.join(root, "qa")):
        print("serve.py: --root must be the workspace folder that holds qa/", file=sys.stderr)
        return 2
    try:
        srv = build_server(root, a.port)
    except (OSError, ValueError) as e:
        print(f"serve.py: cannot listen on 127.0.0.1:{a.port}: {getattr(e, 'strerror', None) or e}",
              file=sys.stderr)
        return 1
    print(startup_line(srv.qa_port), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
