"""Redaction for everything the engine stores or prints.

Secrets come from qa/secrets.env at runtime and are only used to find and mask
themselves. Patterns cover tokens, auth headers, cookies, emails and share links.
The values are read again when the file changes (looked at once a second at most), so a
long-running process such as the QA app also masks a value added or rotated after it started.
"""
import os
import re
import threading
import time

QA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRETS_FILE = os.path.join(QA_DIR, "secrets.env")

MASK = "[REDACTED]"

_PATTERNS = [
    # JWTs (accessToken, idToken, refresh tokens in JWT form)
    (re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), MASK),
    # Authorization / Cookie headers, any casing, in HAR-like or curl-like text
    (re.compile(r"(?i)(authorization|cookie|set-cookie|x-api-key)(\"?\s*[:=]\s*\"?)([^\"\r\n]+)"), r"\1\2" + MASK),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer " + MASK),
    # token-ish JSON or query fields
    (re.compile(r"(?i)(\"?(?:access|refresh|id|session|auth|api)?_?token\"?\s*[:=]\s*\"?)([A-Za-z0-9._~+/=-]{8,})"), r"\1" + MASK),
    (re.compile(r"(?i)(\"?password\"?\s*[:=]\s*\"?)([^\"\s,}]+)"), r"\1" + MASK),
    # email addresses; RFC 5321 part lengths keep the scan linear on a long run without an @
    (re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,}"), "[EMAIL]"),
    # ClickUp / cloud share links
    (re.compile(r"https?://[^\s\"']*(?:share|sharing|/s/|token=)[^\s\"']*", re.I), "[SHARE-LINK]"),
]


def load_secret_values():
    """Values from qa/secrets.env (KEY=VALUE lines). Never print the result."""
    vals = []
    try:
        with open(SECRETS_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                v = line.split("=", 1)[1].strip()
                if len(v) >= 4:
                    vals.append(v)
    except OSError:
        pass
    # longest first so a value containing another is masked whole
    return sorted(set(vals), key=len, reverse=True)


RECHECK_S = 1.0

_SECRETS = None  # the values in use: None reloads them, a list set by hand (tests) is used as it is
_loaded = (None, None, None)  # (the list _secrets() loaded, stat key of its file or None to retry, the file)
_checked = 0.0
_lock = threading.Lock()


def _file_key(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns


def _secrets():
    """The secrets.env values, read again when the file changes, appears or is swapped for another path."""
    global _SECRETS, _loaded, _checked
    with _lock:
        cur, (mine, key, path) = _SECRETS, _loaded
        if cur is not None and cur is not mine:
            return cur
        now = time.monotonic()
        same_file = cur is not None and path == SECRETS_FILE
        if same_file and now - _checked < RECHECK_S:
            return cur
        _checked = now
        new_key = _file_key(SECRETS_FILE)
        if same_file and new_key is not None and new_key == key:
            return cur
        vals = load_secret_values()
        _SECRETS, _loaded = vals, (vals, new_key if vals else None, SECRETS_FILE)
        return vals


def redact(text):
    if not isinstance(text, str) or not text:
        return text
    for v in _secrets():
        if v in text:
            text = text.replace(v, MASK)
    for rx, rep in _PATTERNS:
        text = rx.sub(rep, text)
    return text


def contains_secret(text):
    """True if text holds a secrets.env value or a token-like pattern."""
    if not isinstance(text, str) or not text:
        return False
    if any(v in text for v in _secrets()):
        return True
    return any(rx.search(text) for rx, _ in _PATTERNS[:5])


def redact_obj(obj):
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, list):
        return [redact_obj(x) for x in obj]
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items()}
    return obj
