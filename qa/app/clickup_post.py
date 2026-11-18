"""The QA app's one ClickUp write: the results comment of a test, posted by the app server (2026-11-03).

Only serve.py's POST /api/results/comment/post calls post_comment, after the person pressed Post comment on the Results
page and confirmed with Touch ID on this Mac. The gate (qa/runner/gate.py) denies that route and this module to every
shell line and connector, so nothing else starts a post. clickup_read.py stays GET only; this module is the only place
the app writes to ClickUp, and it can do one thing: add one comment to one task.

comment_target(root, task) -> (ClickUp task id, title)
    The test a qa task belongs to, from the records only, never from a request body: the task's clickup.task_id (for a
    link that named only a custom id like CL-80002, the New test copy of that task, qa/cache/intake/<id>.json). It must
    be a bare ClickUp task id, never a task plan.NEVER_READ names, and agree with the plan or run request the task came
    from and with the New test copy. PostError("no_task") otherwise.
post_comment(root, task_id, text, opener=None) -> {"comment_id": str|None, "posted_at": ISO}
    POST https://api.clickup.com/api/v2/task/<task_id>/comment {"comment_text": text, "notify_all": false}: that URL
    only (the fixed host and path, the id checked again here), one request, TIMEOUT_S, no redirect followed (a 3xx is a
    refusal, so the token never reaches another host). The token is CLICKUP_API_TOKEN from <root>/qa/secrets.env, read
    as clickup_read reads it; it goes only into the Authorization header and is never returned, logged or put in a
    message. Any 2xx answer counts as posted (an answer without an id, or whose body was lost after its 2xx status,
    gives comment_id None, so the comment is never posted twice). A text holding the token or another ClickUp personal
    token is refused before any request (review 2026-11-03). PostError(kind, message), kind no_task | bad_text |
    no_token | refused | offline: the message is plain and short (ClickUp's own error code when it sent one), redacted,
    with the token masked.
"""
import http.client
import json
import os
import re
import socket
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)
sys.path.insert(0, os.path.join(os.path.dirname(APP_DIR), "runner"))

import clickup_read  # noqa: E402  (the token, read one way only)
import plan  # noqa: E402  (NEVER_READ and the New test copies of tasks)
import records  # noqa: E402
import redact  # noqa: E402
import worker  # noqa: E402

API_HOST = clickup_read.API_HOST
API_BASE = clickup_read.API_BASE
TASK_ID_RE = re.compile(r"^[a-z0-9]{4,40}\Z", re.A)  # a bare ClickUp task id, as plan.BARE_REF
COMMENT_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,64}\Z", re.A)
PERSONAL_TOKEN = re.compile(r"pk_[A-Za-z0-9_]+")  # a ClickUp personal token, or what is left of one after a cut
# a whole ClickUp personal token (pk_<user id>_<key>) in a comment text: never posted (review 2026-11-03)
TOKEN_IN_TEXT = re.compile(r"pk_\d+_[A-Za-z0-9]{16,}")
TOKEN_TEXT_PROBLEM = "The comment holds a ClickUp token, so the app does not post it. Press Add result for a new one."
TEXT_MAX = 1000
TIMEOUT_S = 15
ANSWER_MAX = 64 * 1024
TITLE_MAX = 200


class PostError(Exception):
    """kind: no_task | bad_text | no_token | refused | offline. message is safe to show (no token, no body)."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind, self.message = kind, message


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is not followed: urllib would send the Authorization header on to the new URL."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # the 3xx then raises HTTPError


def _urlopen(req, timeout):
    return urllib.request.build_opener(_NoRedirect).open(req, timeout=timeout)


# ---- which task ------------------------------------------------------------------------------------------------------

def _intake_id_of(root, custom_id):
    """The task id of the New test copy whose custom id is custom_id (a link like .../t/CL-80002), else None."""
    d = os.path.join(root, plan.INTAKE_DIR)
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return None
    found = set()
    for name in names:
        rec = plan.load_intake(root, name[:-5]) if name.endswith(".json") else None
        if rec and rec.get("custom_id") == custom_id:
            found.add(rec.get("task_id"))
    return found.pop() if len(found) == 1 else None


def comment_target(root, task):
    """(ClickUp task id, title) of the test the qa task belongs to; PostError("no_task") when there is none or the
    records disagree."""
    ck = task.get("clickup") if isinstance(task, dict) and isinstance(task.get("clickup"), dict) else {}
    tid, cl = ck.get("task_id"), ck.get("cl")
    origin = task.get("origin") if isinstance(task, dict) and isinstance(task.get("origin"), dict) else {}
    req_ck = None
    for rid in (origin.get("start"), origin.get("check")):
        r = worker.load_request(root, rid) if isinstance(rid, str) and worker.REQ_ID_RE.match(rid) else None
        if isinstance(r, dict) and isinstance(r.get("clickup"), dict):
            req_ck = r["clickup"]
            break
    if not tid and isinstance(cl, str):
        tid = _intake_id_of(root, cl)
    if not isinstance(tid, str) or not TASK_ID_RE.match(tid) or tid in plan.NEVER_READ or cl in plan.NEVER_READ:
        raise PostError("no_task", "This test has no ClickUp task id to post to.")
    if req_ck is not None and req_ck.get("task_id") not in (None, tid):
        raise PostError("no_task", "The test's ClickUp task is not the one its plan was made for; nothing is posted.")
    intake = plan.load_intake(root, tid)
    if intake and isinstance(cl, str) and intake.get("custom_id") not in (None, cl):
        raise PostError("no_task", "The test's ClickUp link and the task read in New test disagree; nothing is posted.")
    title = next((t for t in ((intake or {}).get("title"), (req_ck or {}).get("title"), cl, tid)
                  if isinstance(t, str) and t.strip()), tid)
    return tid, title[:TITLE_MAX]


def task_url(task_id):
    """The ClickUp page of a task (for a link on the card), or None for anything but a bare task id."""
    if isinstance(task_id, str) and TASK_ID_RE.match(task_id) and task_id not in plan.NEVER_READ:
        return f"https://app.clickup.com/t/{task_id}"
    return None


def comment_url(task_id):
    """The one URL this module posts to; PostError for anything that is not a bare task id."""
    if not isinstance(task_id, str) or not TASK_ID_RE.match(task_id) or task_id in plan.NEVER_READ:
        raise PostError("no_task", "This test has no ClickUp task id to post to.")
    url = f"{API_BASE}/task/{task_id}/comment"
    parts = urllib.parse.urlsplit(url)
    if (parts.scheme, parts.hostname, parts.port, parts.path, parts.query, parts.fragment) != \
            ("https", API_HOST, None, f"/api/v2/task/{task_id}/comment", "", ""):
        raise PostError("no_task", "This test has no ClickUp task id to post to.")
    return url


# ---- the post --------------------------------------------------------------------------------------------------------

def _secrets_path(root):
    return os.path.join(root, "qa", "secrets.env")


def token_problem(root):
    """None when <root>/qa/secrets.env holds a ClickUp token, else the plain message saying how to add one (serve.py
    checks this before the person is asked for Touch ID)."""
    path = _secrets_path(root)
    if clickup_read.read_token(path):
        return None
    return clickup_read.no_token_message(path).replace("Then press Refresh.", "Then press Post comment again.")


def _safe(message, token):
    """message with the token masked, also a piece of it or of another personal token (pk_...) that a cut left, then
    redacted."""
    text = message.replace(token, redact.MASK) if token else message
    return redact.redact(PERSONAL_TOKEN.sub(redact.MASK, text))


def _refused(err, task_id):
    code = getattr(err, "code", None)
    said = clickup_read._clickup_reason(err)
    if code in (401, 403):
        return (f"ClickUp refused the comment (HTTP {code}{said}). Check that the newest CLICKUP_API_TOKEN line in "
                "qa/secrets.env holds your token and that it may comment on this task.")
    if code == 404:
        return f"ClickUp has no task {task_id} for this token (HTTP 404{said}). Nothing was posted."
    if code == 429:
        return "ClickUp says too many requests (HTTP 429). Nothing was posted; try again in a minute."
    if isinstance(code, int) and 300 <= code < 400:
        return f"ClickUp answered with a redirect (HTTP {code}), which the app does not follow. Nothing was posted."
    return f"ClickUp refused the comment (HTTP {code}{said}). Nothing was posted."


def post_comment(root, task_id, text, opener=None):
    """Add text as a comment to the ClickUp task task_id (see the module text). opener: a urlopen-like callable
    (request, timeout=) for tests; by default one that follows no redirect."""
    url = comment_url(task_id)
    if not isinstance(text, str) or not text.strip() or len(text) > TEXT_MAX or "\x00" in text:
        raise PostError("bad_text", f"The comment must be 1-{TEXT_MAX} characters of text.")
    token = clickup_read.read_token(_secrets_path(root))
    if not token:
        raise PostError("no_token", token_problem(root) or "No ClickUp token found. Nothing was posted.")
    if token in text or TOKEN_IN_TEXT.search(text):  # review 2026-11-03: the token never goes out in the comment either
        raise PostError("bad_text", TOKEN_TEXT_PROBLEM)
    body = json.dumps({"comment_text": text, "notify_all": False}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Authorization": token, "Content-Type": "application/json",
                                          "Accept": "application/json", "User-Agent": "wingtra-qa-app"})
    open_ = opener or _urlopen
    raw = b""
    try:
        with open_(req, timeout=TIMEOUT_S) as r:
            status = getattr(r, "status", None)
            if not isinstance(status, int) or 200 <= status < 300:
                try:
                    raw = r.read(ANSWER_MAX)
                except (OSError, http.client.HTTPException, ValueError):
                    raw = b""  # review 2026-11-03: the 2xx came, so ClickUp has the comment; only its answer was lost
    except urllib.error.HTTPError as e:
        raise PostError("refused", _safe(_refused(e, task_id), token)) from None
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, ssl.SSLError, OSError,
            http.client.HTTPException):  # review 2026-11-03: a broken status line is no answer either, never a 500
        raise PostError("offline", "ClickUp could not be reached or did not answer in time. Look at the ClickUp task "
                                   "before you post again: the comment is there if only the answer was lost.") from None
    if isinstance(status, int) and not 200 <= status < 300:
        raise PostError("refused", _safe(f"ClickUp answered HTTP {status}. Nothing was posted.", token))
    try:
        obj = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, ValueError, RecursionError):
        obj = {}
    cid = obj.get("id") if isinstance(obj, dict) else None
    if isinstance(cid, int) and not isinstance(cid, bool):
        cid = str(cid)
    return {"comment_id": cid if isinstance(cid, str) and COMMENT_ID_RE.match(cid) else None,
            "posted_at": records.now_utc()}
