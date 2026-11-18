"""Request checks, response headers and the /files/ read policy for the QA app (qa/app/DESIGN.md section 2).

The check_* functions return (ok, status, message) and never raise. resolve_read maps the
part of a URL after /files/ to an absolute file path, or raises ReadError(status, message).
Messages are fixed strings: they never echo a header value, a path or a request body.
"""
import hmac
import os
import re
import stat
import sys
import unicodedata
import urllib.parse

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)
sys.path.insert(0, os.path.join(os.path.dirname(APP_DIR), "runner"))

SECURITY_HEADERS = (
    ("Cross-Origin-Resource-Policy", "same-origin"),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    ("Cache-Control", "no-store"),
)
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'none'; object-src 'none'")

MAX_BODY = 64 * 1024
TEXT_CAP = 2 * 1024 * 1024
MAX_REL = 1024

TEXT_EXT = frozenset((".json", ".jsonl", ".md", ".txt", ".log", ".har", ".csv", ".yaml", ".yml", ".py", ".sh",
                      ".conf", ".xml"))
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
               ".gif": "image/gif"}
# test files served as a download (2026-11-02): only one a case lists in its fixtures (views.fixture_allowed)
DOWNLOAD_EXT = frozenset((".flightplan",))
DENIED_EXT = (".env", ".tmp", ".apk", ".aab", ".apks", ".xapk", ".ipa", ".zip", ".tar", ".gz", ".tgz", ".bz2",
              ".tbz2", ".xz", ".txz", ".7z", ".rar", ".zst", ".lz4", ".lzma", ".jar", ".dmg", ".iso")
DENIED_FILES = ("qa/secrets.env", "qa/runner/state/approval.key")
# approvals: File ticket's signed Touch ID approvals (approvals.py, 2026-11-03), never served
DENIED_DIRS = ("qa/cache", "qa/ledger", "qa/runner/state/locks", "qa/runner/state/approvals")
READ_BASES = ("qa", "tests")

FORBIDDEN = "forbidden"


class ReadError(Exception):
    """A /files/ request that is refused; status is the HTTP status to answer with."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def allowed_hosts(port):
    return (f"127.0.0.1:{port}", f"localhost:{port}")


def _values(headers, name):
    """Every value of one header (case-insensitive name); works on HTTPMessage and on a plain dict."""
    if hasattr(headers, "get_all"):
        return list(headers.get_all(name) or [])
    return [v for k, v in headers.items() if k.lower() == name.lower()]


def _one(headers, name):
    """The single value of a header, or None when it is missing or repeated."""
    vals = _values(headers, name)
    return vals[0] if len(vals) == 1 else None


def check_host(headers, port):
    """DNS-rebinding guard: Host must be exactly 127.0.0.1:<port> or localhost:<port>."""
    host = _one(headers, "Host")
    if host is None or host not in allowed_hosts(port):
        return False, 403, "bad Host header"
    return True, 200, ""


def check_fetch_site(headers):
    """If the browser says where the request comes from, it must be this origin or the user."""
    vals = _values(headers, "Sec-Fetch-Site")
    if not vals:
        return True, 200, ""
    if len(vals) == 1 and vals[0].strip().lower() in ("same-origin", "none"):
        return True, 200, ""
    return False, 403, "cross-site request refused"


def content_length(headers):
    """Content-Length as an int, or None when it is missing, repeated or not a plain number."""
    v = _one(headers, "Content-Length")
    if v is None or not re.fullmatch(r"\d{1,12}", v.strip()):
        return None
    return int(v.strip())


def check_write(headers, token, port):
    """POST/PUT: token, Origin, Content-Type and body length, in that order."""
    got = _one(headers, "X-QA-Token")
    if got is None or not hmac.compare_digest(got.encode("utf-8", "replace"), token.encode("utf-8")):
        return False, 403, "missing or wrong token"
    host, origin = _one(headers, "Host"), _one(headers, "Origin")
    if host not in allowed_hosts(port) or origin != f"http://{host}":
        return False, 403, "bad or missing Origin"
    ctype = _one(headers, "Content-Type")
    if ctype is None or ctype.split(";", 1)[0].strip().lower() != "application/json":
        return False, 415, "Content-Type must be application/json"
    if _values(headers, "Transfer-Encoding"):
        return False, 411, "chunked bodies are not accepted"
    n = content_length(headers)
    if n is None:
        if _values(headers, "Content-Length"):
            return False, 400, "bad Content-Length"
        return False, 411, "Content-Length required"
    if n > MAX_BODY:
        return False, 413, "body larger than 64 KiB"
    return True, 200, ""


def check_upload(headers, token, port, piece_max):
    """PUT of one test-file piece: the same token and Origin checks as check_write, a raw body, an exact length."""
    got = _one(headers, "X-QA-Token")
    if got is None or not hmac.compare_digest(got.encode("utf-8", "replace"), token.encode("utf-8")):
        return False, 403, "missing or wrong token"
    host, origin = _one(headers, "Host"), _one(headers, "Origin")
    if host not in allowed_hosts(port) or origin != f"http://{host}":
        return False, 403, "bad or missing Origin"
    ctype = _one(headers, "Content-Type")
    if ctype is None or ctype.split(";", 1)[0].strip().lower() != "application/octet-stream":
        return False, 415, "Content-Type must be application/octet-stream"
    if _values(headers, "Transfer-Encoding"):
        return False, 411, "chunked bodies are not accepted"
    n = content_length(headers)
    if n is None:
        return False, 411, "Content-Length required"
    if not 0 < n <= piece_max:
        return False, 413, "a piece is 1 byte to 16 MiB"
    return True, 200, ""


def _fold(s):
    """Comparison form for deny rules: the file system here ignores case and Unicode normalisation."""
    return unicodedata.normalize("NFKC", s).casefold()


def _denied(rel):
    """True when a workspace-relative POSIX path falls under a deny rule (DESIGN.md section 2)."""
    r = _fold(rel)
    parts = r.split("/")
    if any(p.startswith(".") for p in parts):
        return True
    if r in DENIED_FILES or r.endswith(DENIED_EXT):
        return True
    return any(r == d or r.startswith(d + "/") for d in DENIED_DIRS)


def _decode(rel):
    """URL-decode once and check the shape; returns the list of path segments."""
    try:
        rel = urllib.parse.unquote(rel, encoding="utf-8", errors="strict")
    except UnicodeDecodeError:
        raise ReadError(400, "bad path") from None
    if not rel or len(rel) > MAX_REL or "\x00" in rel or "\\" in rel:
        raise ReadError(400, "bad path")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in rel):
        raise ReadError(400, "bad path")
    parts = rel.split("/")
    if any(p == "" for p in parts):  # absolute path, // or a trailing slash
        raise ReadError(400, "bad path")
    if any(p == ".." for p in parts):
        raise ReadError(403, FORBIDDEN)
    return parts


def _real_rel(root, real):
    """(base realpath, 'qa/…' or 'tests/…' POSIX path) for a realpath inside <root>/qa or <root>/tests, else None."""
    for base in READ_BASES:
        base_real = os.path.realpath(os.path.join(root, base))
        if real == base_real or real.startswith(base_real + os.sep):
            sub = os.path.relpath(real, base_real).replace(os.sep, "/")
            return base_real, base if sub == "." else f"{base}/{sub}"
    return None


def _inode(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_dev, st.st_ino


def _hits_denied_inode(root, real, base_real):
    """Same-file check against the denied files and folders: catches case variants and a hard link to a denied file.
    A hard link to a file inside a denied folder shares no inode with the folder: resolve_read refuses those by
    refusing every file with more than one link."""
    denied = {_inode(os.path.join(root, *p.split("/"))) for p in DENIED_FILES + DENIED_DIRS} - {None}
    p = real
    while True:
        if _inode(p) in denied:
            return True
        if p == base_real or len(p) <= len(base_real):
            return False
        p = os.path.dirname(p)


def file_kind(path):
    """'text', 'image', 'download' or None by extension (case-insensitive)."""
    ext = os.path.splitext(path)[1].lower()
    if ext in TEXT_EXT:
        return "text"
    if ext in IMAGE_TYPES:
        return "image"
    if ext in DOWNLOAD_EXT:
        return "download"
    return None


def resolve_read(root, rel, image_allowed=None, download_allowed=None):
    """Absolute real path for /files/<rel>, or ReadError(status, message).

    rel is the raw part of the URL path after /files/ (no query) and is URL-decoded exactly once.
    image_allowed is a callable(rel) -> bool (a run record or a ticket pack references that image),
    a bool, or None (no images). download_allowed is the same for a download (.flightplan): a case lists it as a
    test file; it must be the file itself under tests/, not a link to another one.
    400: malformed path. 403: traversal, dotfiles, deny list, outside qa/ and tests/, a file with more than one hard
    link, unreferenced image, a download no case lists.
    404: missing, not a regular file, or an extension that is neither text, image nor download.
    """
    parts = _decode(rel)
    lexical = "/".join(parts)
    if parts[0] not in READ_BASES or _denied(lexical):
        raise ReadError(403, FORBIDDEN)
    root = os.path.abspath(root)
    real = os.path.realpath(os.path.join(root, *parts))
    found = _real_rel(root, real)
    if found is None:
        raise ReadError(403, FORBIDDEN)
    base_real, real_rel = found
    if _denied(real_rel):
        raise ReadError(403, FORBIDDEN)
    try:
        st = os.stat(real)
    except OSError:
        raise ReadError(404, "not found") from None
    if not stat.S_ISREG(st.st_mode):
        raise ReadError(404, "not found")
    if st.st_nlink > 1:
        raise ReadError(403, FORBIDDEN)
    if _hits_denied_inode(root, real, base_real):
        raise ReadError(403, FORBIDDEN)
    kind = file_kind(real)
    if kind is None:
        raise ReadError(404, "not found")
    if kind == "image":
        if parts[0] != "tests" or not real_rel.startswith("tests/") or not _image_ok(image_allowed, lexical):
            raise ReadError(403, "image is not engine evidence")
    # a .flightplan URL serves a listed test file or nothing: also when the name is a link to a text file
    if kind == "download" or file_kind(lexical) == "download":
        if parts[0] != "tests" or real_rel != lexical or not _image_ok(download_allowed, lexical):
            raise ReadError(403, "file is not a case test file")
    return real


def _image_ok(image_allowed, rel):
    """The answer of an allow rule (callable, bool or None) for rel; images and downloads use it alike."""
    if callable(image_allowed):
        try:
            return bool(image_allowed(rel))
        except Exception:  # noqa: BLE001 - a failing check denies
            return False
    return image_allowed is True
