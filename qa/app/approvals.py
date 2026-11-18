"""The person's OK for one Swiss Army Knife ticket: Touch ID on this Mac, then a signed approval file (2026-11-03).

The worker files a ticket (sak-submit) only with an approval the person gave in the app: one press of File ticket and
Touch ID (or the account password) in the system prompt on this Mac. The AI cannot answer that prompt, so it cannot
approve its own ClickUp write. The gate (qa/runner/gate.py, its own check) lets the final Next tap pass only with an
unused, unexpired approval whose signature and draft hash hold, and then writes <id>.used.

approve(root, ticket_dir, target, reason, prompt=run_touchid) -> the approval, written only after the prompt said yes
confirm(reason, prompt=run_touchid) -> None when the person said yes in the prompt, else ApprovalError 403; nothing is
    written: for an OK the app server acts on at once (Post comment, 2026-11-03)
verify(root, approval, now=None) -> None when the approval holds, else why not (the same rules as the gate)
load_approval(root, approval_id) -> the approval file's object, or None

Files (QA = the qa folder records.QA_DIR resolves to for the real workspace, else <root>/qa for a test workspace):
  QA/runner/state/approval.key            32 random bytes as 64 hex characters, mode 0600, made on first use
  QA/runner/state/approvals/<id>.json     {id, ticket_dir, draft_sha256, target, created, expires, by, sig}, mode 0600
  QA/runner/state/approvals/<id>.used     written by the gate when it lets the final Next tap pass, and by approve() for
                                          every older unused approval of the same ticket (security review 2026-11-03)
id = A-<YYYYMMDDHHMMSS>-<6 hex>; created and expires are UTC "YYYY-MM-DDTHH:MM:SSZ", expires = created + 30 minutes;
by = "touch_id"; draft_sha256 = sha256(title.txt bytes + b"\\n" + description.txt bytes); sig = hex HMAC-SHA256 with the
32 key bytes (bytes.fromhex of the key file) over json.dumps({every other field}, sort_keys=True, separators=(",", ":"))
as UTF-8.

The prompt is touchid.swift, compiled once with swiftc into qa/app/.touchid (again when the source is newer) and run
with the reason as its only argument and a 90 s timeout. Its path is fixed here, never taken from a request. This
module and writes.Writer are the app's only writers; this one writes the key and the approval files and nothing else.
"""
import datetime
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import threading

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)
sys.path.insert(0, os.path.join(os.path.dirname(APP_DIR), "runner"))

import records  # noqa: E402

SOURCE = os.path.join(APP_DIR, "touchid.swift")
BINARY = os.path.join(APP_DIR, ".touchid")
SWIFTC = "/usr/bin/swiftc"
PROMPT_TIMEOUT_S = 90
COMPILE_TIMEOUT_S = 300
VALID_S = 30 * 60
DRAFT_FILES = ("title.txt", "description.txt", "draft.json")
DRAFT_FILE_MAX = 256 * 1024
REASON_MAX = 200
TARGETS = ("TA5", "ET65", "W044")
FIELDS = ("id", "ticket_dir", "draft_sha256", "target", "created", "expires", "by", "sig")
TICKET_DIR_RE = re.compile(r"^tests/(\d{4}-\d{2}-\d{2}-[a-z0-9-]+)/tickets/(\d+)\Z", re.A)  # ASCII digits, as the gate
APPROVAL_ID_RE = re.compile(r"^A-\d{14}-[0-9a-f]{6}\Z", re.A)
KEY_RE = re.compile(r"^[0-9a-f]{64}\Z")
HEX64_RE = re.compile(r"^[0-9a-f]{64}\Z")
TIME_FMT = "%Y-%m-%dT%H:%M:%SZ"
NOT_APPROVED = {1: "Not approved: Touch ID was cancelled",
                2: "Not approved: Touch ID and the account password are not available on this Mac",
                None: f"Not approved: no answer to the Touch ID prompt within {PROMPT_TIMEOUT_S} s"}

_BUILD_LOCK = threading.Lock()


class ApprovalError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


# ---- where ----------------------------------------------------------------------------------------------------------

def qa_dir(root):
    """The qa folder of root: records.QA_DIR for the real workspace (the staging copy when this runs from qa_stage),
    else <root>/qa (a test workspace)."""
    root = os.path.realpath(root)
    if root == os.path.realpath(records.WORKSPACE):
        return os.path.realpath(records.QA_DIR)
    return os.path.join(root, "qa")


def state_dir(root):
    return os.path.join(qa_dir(root), "runner", "state")


def approvals_dir(root):
    return os.path.join(state_dir(root), "approvals")


def key_path(root):
    return os.path.join(state_dir(root), "approval.key")


def _private_dir(path):
    """Make path (mode 0700 when new) and refuse it when it or a parent is a symlink (checked before and after)."""
    path = os.path.normpath(path)
    top = path
    while not os.path.lexists(top):
        top = os.path.dirname(top)
    if os.path.realpath(top) != top:
        raise ApprovalError(500, "approvals: a folder on the way is a symlink")
    os.makedirs(path, mode=0o700, exist_ok=True)
    if os.path.realpath(path) != path or not os.path.isdir(path):
        raise ApprovalError(500, "approvals: a folder on the way is a symlink")
    return path


# ---- the draft --------------------------------------------------------------------------------------------------------

def draft_sha256(title, description):
    """The draft hash the approval binds: sha256(title.txt bytes + b"\\n" + description.txt bytes)."""
    return hashlib.sha256(title + b"\n" + description).hexdigest()


def _read_plain(path, name):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        raise ApprovalError(404, f"the draft has no {name}") from None
    except OSError:
        raise ApprovalError(400, f"{name}: not a plain file (a symlink is refused)") from None
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode):
            raise ApprovalError(400, f"{name}: not a plain file")
        raw = f.read(DRAFT_FILE_MAX + 1)
    if len(raw) > DRAFT_FILE_MAX:
        raise ApprovalError(400, f"{name}: larger than {DRAFT_FILE_MAX // 1024} KiB")
    return raw


def load_draft(root, ticket_dir):
    """{dir, slug, n, title, description (bytes), draft (draft.json), sha256} of tests/<campaign>/tickets/<n>, with no
    symlink anywhere from root down to its three files; ApprovalError 400 or 404 otherwise."""
    m = TICKET_DIR_RE.match(ticket_dir) if isinstance(ticket_dir, str) else None
    if not m:
        raise ApprovalError(400, "dir: tests/<yyyy-mm-dd-slug>/tickets/<n>")
    path = os.path.realpath(root)
    for part in ticket_dir.split("/"):
        path = os.path.join(path, part)
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            raise ApprovalError(404, f"no ticket draft at {ticket_dir}") from None
        if stat.S_ISLNK(st.st_mode):
            raise ApprovalError(400, f"{ticket_dir}: a folder on the way is a symlink")
        if not stat.S_ISDIR(st.st_mode):
            raise ApprovalError(404, f"no ticket draft at {ticket_dir}")
    raw = {name: _read_plain(os.path.join(path, name), name) for name in DRAFT_FILES}
    try:
        draft = json.loads(raw["draft.json"].decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        draft = None
    if not isinstance(draft, dict):
        raise ApprovalError(400, "draft.json: not a JSON object")
    return {"dir": ticket_dir, "slug": m.group(1), "n": int(m.group(2)), "title": raw["title.txt"],
            "description": raw["description.txt"], "draft": draft,
            "sha256": draft_sha256(raw["title.txt"], raw["description.txt"])}


# ---- the key and the signature ------------------------------------------------------------------------------------------

def _read_key(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        raise
    except OSError:
        raise ApprovalError(500, "approval.key is not a plain file") from None
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_mode & 0o077 or st.st_uid != os.getuid():
            raise ApprovalError(500, "approval.key must be a plain file of this user with mode 0600")
        text = f.read(200).decode("ascii", "replace").strip()
    if not KEY_RE.match(text):
        raise ApprovalError(500, "approval.key must hold 64 hex characters")
    return bytes.fromhex(text)


def load_key(root, create=True):
    """The 32 key bytes; with create, the key file is made on first use (0600, linked into place whole)."""
    path = key_path(root)
    try:
        return _read_key(path)
    except FileNotFoundError:
        if not create:
            raise ApprovalError(500, "no approval.key yet") from None
    _private_dir(os.path.dirname(path))
    tmp = f"{path}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        _write_all(fd, (secrets.token_hex(32) + "\n").encode("ascii"))
    finally:
        os.close(fd)
    try:
        os.link(tmp, path)  # never over a key another thread made first
    except FileExistsError:
        pass
    finally:
        os.unlink(tmp)
    return _read_key(path)


def payload(approval):
    """The signed bytes: every field but sig, as compact sorted JSON."""
    rest = {k: v for k, v in approval.items() if k != "sig"}
    return json.dumps(rest, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign(key, approval):
    return hmac.new(key, payload(approval), hashlib.sha256).hexdigest()


# ---- the prompt -----------------------------------------------------------------------------------------------------------

def build_helper():
    """The compiled prompt at its fixed path, built with swiftc when it is missing or older than touchid.swift."""
    with _BUILD_LOCK:
        try:
            src = os.stat(SOURCE)
        except OSError:
            raise ApprovalError(500, "touchid.swift is missing") from None
        try:
            b = os.lstat(BINARY)
            if stat.S_ISREG(b.st_mode) and b.st_mtime_ns >= src.st_mtime_ns and b.st_mode & 0o100:
                return BINARY
        except FileNotFoundError:
            pass
        tmp = BINARY + ".build"
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        try:
            p = subprocess.run([SWIFTC, "-O", "-o", tmp, SOURCE], stdin=subprocess.DEVNULL, capture_output=True,
                               timeout=COMPILE_TIMEOUT_S, check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise ApprovalError(500, f"the Touch ID helper did not build: {type(e).__name__}") from None
        if p.returncode != 0 or not os.path.isfile(tmp):
            last = (p.stderr.decode("utf-8", "replace").strip().splitlines() or ["no output"])[-1][:200]
            raise ApprovalError(500, f"the Touch ID helper did not build: {last}")
        os.chmod(tmp, 0o700)
        os.replace(tmp, BINARY)
        return BINARY


def run_touchid(reason):
    """The system prompt on this Mac: 0 approved, 1 cancelled or failed, 2 not available, None no answer in 90 s."""
    path = build_helper()
    try:
        p = subprocess.run([path, reason], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=PROMPT_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired:
        return None
    return p.returncode


# ---- approve and verify -------------------------------------------------------------------------------------------------

def _iso(t):
    return t.astimezone(datetime.timezone.utc).strftime(TIME_FMT)


def _parse(s):
    try:
        return datetime.datetime.strptime(s, TIME_FMT).replace(tzinfo=datetime.timezone.utc)
    except (TypeError, ValueError):
        return None


def _write_all(fd, data):
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]
    os.fsync(fd)


def _write_approval(root, approval):
    d = _private_dir(approvals_dir(root))
    final = os.path.join(d, f"{approval['id']}.json")
    tmp = os.path.join(d, f".{approval['id']}.json.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        _write_all(fd, (json.dumps(approval, sort_keys=True, indent=2) + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    try:
        os.link(tmp, final)  # whole or not at all, and never over another approval
    except FileExistsError:
        raise ApprovalError(500, "approval id taken; press File ticket again") from None
    finally:
        os.unlink(tmp)
    return final


def _check_reason(reason):
    if (not isinstance(reason, str) or not reason.strip() or len(reason) > REASON_MAX
            or any(not c.isprintable() for c in reason)):
        raise ApprovalError(400, f"reason: one line of 1-{REASON_MAX} characters")


def confirm(reason, prompt=run_touchid):
    """Ask the person in the system prompt (prompt(reason) -> 0 when approved). Returns None on a yes; raises
    ApprovalError 400 for a bad reason, 403 "Not approved: ..." otherwise. Writes nothing: for an OK the caller acts on
    at once, in the same request (Post comment, 2026-11-03)."""
    _check_reason(reason)
    code = prompt(reason)
    if code != 0:
        raise ApprovalError(403, NOT_APPROVED.get(code, f"Not approved: the Touch ID prompt failed (exit {code})"))


def approve(root, ticket_dir, target, reason, prompt=run_touchid, now=None):
    """Ask the person (prompt(reason) -> 0 when approved) and, only on a yes, write the signed approval of this draft
    for this target. Raises ApprovalError: 400/404 for a bad draft, 403 when not approved, 409 when the draft changed
    while the prompt was open, 500 when the key or the helper is broken."""
    if target not in TARGETS:
        raise ApprovalError(400, f"target: one of {', '.join(TARGETS)}")
    _check_reason(reason)
    draft = load_draft(root, ticket_dir)
    key = load_key(root)  # a broken key fails before the person is asked
    confirm(reason, prompt)
    if load_draft(root, ticket_dir)["sha256"] != draft["sha256"]:
        raise ApprovalError(409, "the draft changed while the prompt was open; press File ticket again")
    created = (now or datetime.datetime.now(datetime.timezone.utc)).astimezone(datetime.timezone.utc).replace(
        microsecond=0)
    approval = {"id": f"A-{created.strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}", "ticket_dir": ticket_dir,
                "draft_sha256": draft["sha256"], "target": target, "created": _iso(created),
                "expires": _iso(created + datetime.timedelta(seconds=VALID_S)), "by": "touch_id"}
    approval["sig"] = sign(key, approval)
    _write_approval(root, approval)
    _supersede(root, approval)
    return approval


def _supersede(root, approval):
    """Security review 2026-11-03: the newest approval of a ticket is its only one. Every older unused approval of the
    same ticket folder gets its .used marker now (by "superseded"), so a press after a failed request never leaves two
    approvals the gate would each let file the ticket once."""
    d = approvals_dir(root)
    for name in sorted(os.listdir(d)):
        aid, ext = os.path.splitext(name)
        if ext != ".json" or aid == approval["id"] or not APPROVAL_ID_RE.match(aid) or is_used(root, aid):
            continue
        old = load_approval(root, aid)
        if not isinstance(old, dict) or old.get("ticket_dir") != approval["ticket_dir"]:
            continue
        marker = os.path.join(d, f"{aid}.used")
        try:
            fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            continue  # the gate used it meanwhile
        try:
            _write_all(fd, (json.dumps({"id": aid, "used": _iso(datetime.datetime.now(datetime.timezone.utc)),
                                        "by": "superseded", "superseded_by": approval["id"]}) + "\n").encode("utf-8"))
        finally:
            os.close(fd)


def load_approval(root, approval_id):
    if not isinstance(approval_id, str) or not APPROVAL_ID_RE.match(approval_id):
        return None
    try:
        raw = _read_plain(os.path.join(approvals_dir(root), f"{approval_id}.json"), "approval")
        obj = json.loads(raw.decode("utf-8"))
    except (ApprovalError, UnicodeDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def is_used(root, approval_id):
    return os.path.lexists(os.path.join(approvals_dir(root), f"{approval_id}.used"))


def verify(root, approval, now=None):
    """None when the approval holds now (fields, signature, not expired, not used, the draft unchanged), else why not."""
    if not isinstance(approval, dict) or sorted(approval) != sorted(FIELDS):
        return f"not an approval: fields {', '.join(FIELDS)}"
    if not all(isinstance(approval[k], str) for k in FIELDS):
        return "not an approval: every field is text"
    if not APPROVAL_ID_RE.match(approval["id"]) or not TICKET_DIR_RE.match(approval["ticket_dir"]) \
            or approval["target"] not in TARGETS or approval["by"] != "touch_id" \
            or not HEX64_RE.match(approval["draft_sha256"]) or not HEX64_RE.match(approval["sig"]):
        return "not an approval: a field has the wrong form"
    try:
        key = load_key(root, create=False)
    except ApprovalError as e:
        return e.message
    if not hmac.compare_digest(sign(key, approval), approval["sig"]):
        return "the signature does not match"
    created, expires = _parse(approval["created"]), _parse(approval["expires"])
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if created is None or expires is None or expires - created != datetime.timedelta(seconds=VALID_S) \
            or approval["id"][2:16] != created.strftime("%Y%m%d%H%M%S"):
        return "the approval's times are not created + 30 minutes"
    if now >= expires:
        return "the approval expired"
    if created > now + datetime.timedelta(minutes=2):
        return "the approval is from the future"
    if is_used(root, approval["id"]):
        return "the approval was used"
    try:
        current = load_draft(root, approval["ticket_dir"])["sha256"]
    except ApprovalError as e:
        return e.message
    if current != approval["draft_sha256"]:
        return "the draft changed after the approval"
    return None
