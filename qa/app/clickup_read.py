"""Read-only ClickUp client for the QA app's Today view (qa/app/DESIGN.md section 8).

GET requests only, to https://api.clickup.com only, with the personal token CLICKUP_API_TOKEN from qa/secrets.env.
The token is read here and nowhere else in the app, is never returned, logged or written, and any text that holds
it is masked by redact (which loads every secrets.env value). Nothing in this module can create, change or delete
anything in ClickUp: there is no request method other than GET.
"""
import json
import os
import socket
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)
sys.path.insert(0, os.path.join(os.path.dirname(APP_DIR), "runner"))

API_HOST = "api.clickup.com"
API_BASE = f"https://{API_HOST}/api/v2"
TOKEN_KEY = "CLICKUP_API_TOKEN"
TIMEOUT_S = 15
MAX_PAGES = 10  # 100 tasks a page; Owner has a handful in up next / ongoing


class ClickUpError(Exception):
    """kind: no_token | offline | auth | http | bad_data. message is safe to show (no token, no body)."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind, self.message = kind, message


def read_token(secrets_path):
    """The CLICKUP_API_TOKEN value from a KEY=VALUE file, or None. Never print the result."""
    return token_status(secrets_path)[1]


def token_status(secrets_path):
    """(why, token): why is ok | no_file | rich_text | no_key | empty; token is None unless why is ok.

    Only the reason is ever shown; the value never leaves this function except to get_json.
    """
    try:
        with open(secrets_path, "rb") as f:
            raw = f.read(64 * 1024)
    except OSError:
        return "no_file", None
    if raw.lstrip().startswith(b"{\\rtf"):
        return "rich_text", None
    text = raw.decode("utf-8", "replace").replace("﻿", "")
    found = None  # the last CLICKUP_API_TOKEN line wins: a new token is usually appended below an old one
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        if key == TOKEN_KEY:
            found = value.strip().strip('"').strip("'").strip("“”‘’").strip()
    if found is None:
        return "no_key", None
    if not found:
        return "empty", None
    if not found.startswith("pk_") or "=" in found or " " in found:
        return "not_a_token", None
    return "ok", found


# no "token:" in these texts: the redactor would read what follows it as a token value and mask it
NO_TOKEN_HELP = {
    "no_file": "No ClickUp token found. The file qa/secrets.env does not exist. ",
    "rich_text": "No ClickUp token found. The file qa/secrets.env was saved as rich text (TextEdit does this). In "
                 "TextEdit choose Format > Make Plain Text, then save. ",
    "no_key": "No ClickUp token found. The file qa/secrets.env exists and is plain text, but no line in it starts "
              "with CLICKUP_API_TOKEN= (check the spelling and that the file was saved). ",
    "empty": "No ClickUp token found. The CLICKUP_API_TOKEN= line in qa/secrets.env has nothing after the =. ",
    "not_a_token": "No ClickUp token found. The last CLICKUP_API_TOKEN= line in qa/secrets.env does not hold a "
                   "ClickUp token (a token starts with pk_ and has no spaces). Maybe the clipboard held other text "
                   "when the line was added: copy the token again and fix that line. ",
}
TOKEN_HOWTO = ("In ClickUp open Settings > Apps > API Token, copy it, and add the line CLICKUP_API_TOKEN=<token> "
               "to qa/secrets.env as plain text. Then press Refresh.")


def no_token_message(secrets_path):
    why, _ = token_status(secrets_path)
    return NO_TOKEN_HELP.get(why, "No ClickUp token yet. ") + TOKEN_HOWTO


def _url(path, params):
    """API_BASE + path + query; params is a list of (key, value) pairs (repeat a key for arrays)."""
    if not path.startswith("/") or "//" in path or ".." in path:
        raise ValueError("bad API path")
    query = urllib.parse.urlencode(params or [], doseq=False)
    return API_BASE + path + ("?" + query if query else "")


def get_json(path, params, token, opener=None):
    """GET API_BASE+path and return the parsed JSON object. The one network call in the app."""
    if not token:
        raise ClickUpError("no_token", "No ClickUp token yet. In ClickUp open Settings > Apps > API Token, copy it, "
                                       "and add the line CLICKUP_API_TOKEN=<token> to qa/secrets.env. "
                                       "Then press Refresh.")
    url = _url(path, params)
    if urllib.parse.urlsplit(url).hostname != API_HOST:
        raise ValueError("only api.clickup.com is read")
    req = urllib.request.Request(url, method="GET", headers={"Authorization": token, "Accept": "application/json",
                                                             "User-Agent": "wingtra-qa-app"})
    open_ = opener or urllib.request.urlopen
    try:
        with open_(req, timeout=TIMEOUT_S) as r:
            raw = r.read(8 * 1024 * 1024)
    except urllib.error.HTTPError as e:
        said = _clickup_reason(e)
        if e.code in (401, 403):
            raise ClickUpError("auth", f"ClickUp refused the request (HTTP {e.code}{said}). Check that the newest "
                                       "CLICKUP_API_TOKEN line in qa/secrets.env holds the token you created last, "
                                       "or make a new one in ClickUp (Settings > Apps > API Token).") from None
        if e.code == 429:
            raise ClickUpError("http", "ClickUp says too many requests (HTTP 429). Try Refresh in a minute.") from None
        raise ClickUpError("http", f"ClickUp answered HTTP {e.code}{said}. Try Refresh again later.") from None
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, ssl.SSLError) as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, (socket.timeout, TimeoutError)):
            msg = "ClickUp did not answer in time. Check the internet connection, then press Refresh."
        else:
            msg = "No internet connection: ClickUp can't be reached. Check the connection, then press Refresh."
        raise ClickUpError("offline", msg) from None
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ClickUpError("bad_data", "ClickUp sent something that is not JSON. Try Refresh again.") from None
    if not isinstance(obj, dict):
        raise ClickUpError("bad_data", "ClickUp sent an unexpected answer. Try Refresh again.")
    return obj


def _clickup_reason(err):
    """', ClickUp says: <err> (<ECODE>)' from an error answer's JSON body, else ''. Short, letters only."""
    try:
        body = json.loads((err.read(2048) or b"").decode("utf-8", "replace"))
    except (ValueError, OSError, AttributeError):
        return ""
    if not isinstance(body, dict):
        return ""
    text = " ".join(str(body.get(k) or "") for k in ("err", "ECODE")).strip()
    text = "".join(c for c in text if c.isalnum() or c in " _-.")[:80].strip()
    return f", ClickUp says: {text}" if text else ""


def team_tasks(team_id, assignee_id, statuses, token, opener=None):
    """Every task in the workspace assigned to assignee_id in one of statuses (with markdown descriptions)."""
    tasks = []
    for page in range(MAX_PAGES):
        params = [("page", str(page)), ("assignees[]", str(assignee_id)), ("subtasks", "true"),
                  ("include_closed", "false"), ("include_markdown_description", "true")]
        params += [("statuses[]", s) for s in statuses]
        obj = get_json(f"/team/{team_id}/task", params, token, opener)
        batch = obj.get("tasks")
        if not isinstance(batch, list):
            raise ClickUpError("bad_data", "ClickUp's task list had no tasks array. Try Refresh again.")
        tasks.extend(t for t in batch if isinstance(t, dict))
        if obj.get("last_page", True) or not batch:
            break
    return tasks
