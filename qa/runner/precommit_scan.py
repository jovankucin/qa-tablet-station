"""Pre-commit scan: block secrets, tokens, e-mail addresses, share links and files that must not be committed.

python3 -I qa/runner/precommit_scan.py                    the staged files (git diff --cached), content from the index
python3 -I qa/runner/precommit_scan.py --paths <file|dir>…  the given files from disk (a folder is walked)
           [--paths-from <list>|-]                         newline- or NUL-separated file list (- = stdin)
           [--allow <file>] [--fingerprints] [--secrets-file <file>] [--no-secrets-file]

Prints "<path>:<line>: <kind>" per finding (line "-" for a whole-file finding) and a summary line. A matched value
is never printed. Kinds:
  value from secrets.env   any qa/secrets.env value (redact.load_secret_values), also JSON-escaped or URL-encoded;
                           a value holding "@" matches in any case
  JWT, auth or cookie header, bearer token, token field, password field
                           redact.py's token patterns; a mask ([REDACTED]) or code in the value place (a variable,
                           template, call or expression such as 'Bearer ' + token) does not count
  email                    redact.py's e-mail pattern, except the reserved example domains (example.com/.net/.org,
                           *.example, *.test, *.invalid, *.localhost) and git@ (SSH remotes)
  share link               redact.py's share-link pattern, and /shared/<id> paths (as draft_check.SHARE)
  secrets file, env file, cache folder, runtime state, evidence or clone folder, APK, archive, compiled Python,
  temp file, file over 5 MiB
                           by name, path, magic bytes or size
Binary content (a NUL byte in the first 8 KiB) is scanned through its printable strings for the secret and token
kinds only.
Allowed findings: --allow <file> (default qa/.precommit-allow when it exists), one "<fingerprint> <kind> <path>" per
line, "#" starts a comment. --fingerprints prints each finding's fingerprint (sha256 of the kind and the stripped
line, not the value). <path> is a glob or a path suffix, so "runner/x.py" matches "qa/runner/x.py" too. A
secrets.env value and the file rules can never be allowed.
Exit 0 when clean, 1 on findings, 2 on usage or git errors and when secrets.env could not be read (unless
--no-secrets-file).
"""
import argparse
import collections
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
QA_DIR = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import redact  # noqa: E402  the engine's redaction rules and secrets loader

MAX_BYTES = 5 << 20
SECRET_KIND = "value from secrets.env"
# redact._PATTERNS in its order: five token patterns (what contains_secret checks), then email and share link
TOKEN_KINDS = ("JWT", "auth or cookie header", "bearer token", "token field", "password field")
TOKEN_RX = list(zip((rx for rx, _ in redact._PATTERNS[:5]), TOKEN_KINDS))
EMAIL_RX = redact._PATTERNS[5][0]
SHARE_RX = [redact._PATTERNS[6][0], re.compile(r"/shared/[\w-]{6,}")]  # second: same as draft_check.SHARE
MASKS = ("[REDACTED]", "[EMAIL]", "[SHARE-LINK]")
EXAMPLE_DOMAINS = re.compile(r"(?i)(?:^|\.)(?:example\.(?:com|net|org)|[^.]+\.(?:example|test|invalid|localhost))$")

ARCHIVE_EXT = {".zip", ".gz", ".tgz", ".7z", ".tar", ".rar", ".bz2", ".xz", ".zst"}  # as draft_check.py
ARCHIVE_MAGIC = (b"PK\x03\x04", b"PK\x05\x06", b"\x1f\x8b", b"7z\xbc\xaf\x27\x1c", b"Rar!\x1a\x07", b"BZh",
                 b"\xfd7zXZ\x00", b"\x28\xb5\x2f\xfd")
APK_EXT = {".apk", ".aab", ".apks", ".xapk"}
OUTSIDE_QA = {"tests", "WingtraCloud", "WingtraPilot"}  # first segment when the repo is the workspace root
NEVER_ALLOWED = {SECRET_KIND}

Finding = collections.namedtuple("Finding", "path line kind fp")


# --- rules -------------------------------------------------------------------------------------------------------

def secret_needles(values):
    """(needle, any_case) for each secrets.env value and its JSON-escaped and URL-encoded forms."""
    out = set()
    for v in values:
        forms = {v, json.dumps(v)[1:-1], json.dumps(v, ensure_ascii=False)[1:-1],
                 urllib.parse.quote(v, safe=""), urllib.parse.quote_plus(v)}
        for f in forms:
            if len(f) >= 4:
                out.add((f.lower(), True) if "@" in v else (f, False))
    return sorted(out, key=lambda n: -len(n[0]))


def path_kinds(path, size, head):
    """File rules: what the path, size or first bytes say about a file that must not be committed."""
    parts = path.replace("\\", "/").split("/")
    name = parts[-1]
    low = name.lower()
    ext = os.path.splitext(low)[1]
    kinds = []
    if name == "secrets.env":
        kinds.append("secrets file")
    elif low == ".env" or low.endswith(".env") or low.startswith(".env."):
        kinds.append("env file")
    if any(p in ("cache", ".cache") for p in parts[:-1]):
        kinds.append("cache folder")
    if "/runner/state/" in "/" + "/".join(parts[:-1]) + "/":
        kinds.append("runtime state")
    if len(parts) > 1 and parts[0] in OUTSIDE_QA:
        kinds.append("evidence or clone folder")
    if ext in APK_EXT:
        kinds.append("APK")
    if ext in ARCHIVE_EXT or (b"\x00" in head[:8192] and head.startswith(ARCHIVE_MAGIC)):
        kinds.append("archive")
    if "__pycache__" in parts[:-1] or ext in (".pyc", ".pyo"):
        kinds.append("compiled Python")
    if ext == ".tmp":
        kinds.append("temp file")
    if size > MAX_BYTES:
        kinds.append("file over 5 MiB")
    return kinds


def _placeholder(m, kind):
    """True when a token pattern caught a mask or code (a variable, template, call or expression), not a value."""
    if not m.re.groups:
        return False
    if m.string[m.end():m.end() + 1] == "(":  # a call such as secrets.token_urlsafe(32)
        return True
    v = (m.group(m.re.groups) or "").lstrip("\"'\\ \t")
    if v.startswith(MASKS) or v.startswith(("{", "$", "<", "%(", "+")):
        return True
    if kind == "password field":
        return len(v) < 4
    v = re.sub(r"(?i)^(?:bearer|basic|token)\s+", "", v)
    return not re.match(r"[A-Za-z0-9._~+/=%-]{8,}", v)


def _email_kept(addr):
    local, domain = addr.rsplit("@", 1)
    if local.lower() == "git":  # an SSH remote (git@github.com:org/repo.git), not a person
        return False
    return not EXAMPLE_DOMAINS.search(domain.rstrip("."))


def _emails(line):
    """E-mail matches in a window around each "@"; the pattern alone is quadratic on a long run without one."""
    i = line.find("@")
    while i != -1:
        for m in EMAIL_RX.finditer(line[max(0, i - 128):i + 256]):
            yield m.group()
        i = line.find("@", i + 1)


def line_kinds(line, needles):
    """Content rules for one line of text: the kinds it holds, at most once each."""
    kinds = []
    low = None
    for n, any_case in needles:
        if any_case:
            low = line.lower() if low is None else low
            hit = n in low
        else:
            hit = n in line
        if hit:
            kinds.append(SECRET_KIND)
            break
    for rx, kind in TOKEN_RX:
        if any(not _placeholder(m, kind) for m in rx.finditer(line)):
            kinds.append(kind)
    if any(_email_kept(e) for e in _emails(line)):
        kinds.append("email")
    if any(rx.search(line) for rx in SHARE_RX):
        kinds.append("share link")
    return kinds


def fingerprint(kind, text):
    return hashlib.sha256(f"{kind}\0{text}".encode("utf-8", "replace")).hexdigest()[:16]


def binary_strings(data):
    """Printable ASCII and UTF-16LE runs of binary content, like strings(1)."""
    parts = [m.group().decode("ascii") for m in re.finditer(rb"[\x20-\x7e]{6,}", data)]
    parts += [m.group().decode("utf-16-le") for m in re.finditer(rb"(?:[\x20-\x7e]\x00){6,}", data)]
    return parts


def scan_blob(path, data, size, needles):
    """Findings for one file; data is None when the file is over the limit and was not read."""
    head = data[:8192] if data is not None else b""
    out = [Finding(path, "-", k, None) for k in path_kinds(path, size, head)]
    if data is None:
        return out
    if b"\x00" in head:
        whole = hashlib.sha256(data).hexdigest()
        seen = set()
        for s in binary_strings(data):
            for k in line_kinds(s, needles):
                if k in (SECRET_KIND,) + TOKEN_KINDS and k not in seen:
                    seen.add(k)
                    out.append(Finding(path, "-", k, None if k in NEVER_ALLOWED else fingerprint(k, whole)))
        return out
    for i, line in enumerate(data.decode("utf-8", "replace").split("\n"), 1):
        for k in line_kinds(line, needles):
            out.append(Finding(path, i, k, None if k in NEVER_ALLOWED else fingerprint(k, line.strip())))
    return out


# --- allow list --------------------------------------------------------------------------------------------------

def load_allow(path):
    """[(fingerprint, kind, path glob)] from an allow file; kinds are matched case-insensitively."""
    entries = []
    with open(path, encoding="utf-8") as f:
        for n, raw in enumerate(f, 1):
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            m = re.fullmatch(r"([0-9a-f]{16})\s+(.+?)\s+(\S+)", line)
            if not m:
                raise ValueError(f"{path}:{n}: expected '<fingerprint> <kind> <path>'")
            entries.append((m.group(1), m.group(2).lower(), m.group(3)))
    return entries


def allowed(f, entries):
    if f.fp is None:
        return False
    p = f.path.replace("\\", "/")
    for fp, kind, pat in entries:
        if fp == f.fp and kind == f.kind.lower() and (fnmatch.fnmatchcase(p, pat) or p.endswith("/" + pat)):
            return True
    return False


# --- sources -----------------------------------------------------------------------------------------------------

GIT_ENV = dict(os.environ, GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0")


def git(args, cwd, **kw):
    return subprocess.run(["git", *args], cwd=cwd, env=GIT_ENV, capture_output=True, timeout=60, **kw)


def parse_z(out):
    return [p.decode("utf-8", "surrogateescape") for p in out.split(b"\x00") if p]


def staged_paths(top):
    p = git(["diff", "--cached", "--name-only", "-z", "--no-renames", "--diff-filter=d"], top)
    if p.returncode:
        raise RuntimeError("git diff --cached failed")
    return parse_z(p.stdout)


def read_index(top, path, limit=MAX_BYTES):
    """(data or None when over the limit, size) of the staged blob, read through git show :<path>."""
    proc = subprocess.Popen(["git", "show", f":{path}"], cwd=top, env=GIT_ENV, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL)
    try:
        data = proc.stdout.read(limit + 1)
        if len(data) > limit:
            proc.kill()
            proc.wait()
            return None, limit + 1
        rest = proc.stdout.read()
        if proc.wait(timeout=60):
            raise RuntimeError(f"git show :{path} failed")
        return data + rest, len(data) + len(rest)
    finally:
        proc.stdout.close()


def read_disk(path, limit=MAX_BYTES):
    size = os.path.getsize(path)
    if size > limit:
        return None, size
    with open(path, "rb") as f:
        return f.read(), size


def expand_paths(paths):
    out = []
    for p in paths:
        if os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                dirs[:] = sorted(d for d in dirs if d != ".git")
                out.extend(os.path.join(root, f) for f in sorted(files))
        elif os.path.isfile(p):
            out.append(p)
        else:
            raise FileNotFoundError(p)
    return out


def read_list(spec):
    raw = sys.stdin.buffer.read() if spec == "-" else open(spec, "rb").read()
    sep = b"\x00" if b"\x00" in raw else b"\n"
    return [p.decode("utf-8", "surrogateescape").strip("\r") for p in raw.split(sep) if p.strip()]


# --- main --------------------------------------------------------------------------------------------------------

def scan(items, needles, entries):
    """items: (path, loader) pairs. Returns (findings, allowed findings)."""
    found, ok = [], []
    for path, load in items:
        data, size = load()
        for f in scan_blob(path, data, size, needles):
            (ok if allowed(f, entries) else found).append(f)
    return found, ok


def report(found, ok, n_files, show_fp, allow_file):
    for f in found:
        print(f"{f.path}:{f.line}: {f.kind}" + (f"  [fp {f.fp}]" if show_fp and f.fp else ""))
    kinds = collections.Counter(f.kind for f in found)
    detail = " (" + ", ".join(f"{k} {n}" for k, n in sorted(kinds.items())) + ")" if kinds else ""
    allow = f", {len(ok)} allowed by {allow_file}" if allow_file else ""
    print(f"precommit_scan: {n_files} files, {len(found)} findings{detail}{allow}")


def main(argv):
    ap = argparse.ArgumentParser(prog="precommit_scan.py", description="Scan files before a commit.")
    ap.add_argument("--paths", nargs="+", help="files or folders to scan from disk instead of the index")
    ap.add_argument("--paths-from", help="file with one path per line (or NUL-separated); - for stdin")
    ap.add_argument("--allow", help="allow file (default qa/.precommit-allow when it exists)")
    ap.add_argument("--fingerprints", action="store_true", help="print each finding's fingerprint")
    ap.add_argument("--secrets-file", help="secrets file to read instead of qa/secrets.env")
    ap.add_argument("--no-secrets-file", action="store_true", help="skip the secrets.env values (outside the engine)")
    a = ap.parse_args(argv)

    if a.secrets_file:
        redact.SECRETS_FILE = a.secrets_file
    values = [] if a.no_secrets_file else redact.load_secret_values()
    if not values and not a.no_secrets_file:
        print("precommit_scan: secrets.env values were not checked (file missing, empty or unreadable);"
              " pass --no-secrets-file only outside the engine", file=sys.stderr)
        return 2
    needles = secret_needles(values)

    allow_file = a.allow or (os.path.join(QA_DIR, ".precommit-allow")
                             if os.path.isfile(os.path.join(QA_DIR, ".precommit-allow")) else None)
    try:
        entries = load_allow(allow_file) if allow_file else []
    except (OSError, ValueError) as e:
        print(f"precommit_scan: {e}", file=sys.stderr)
        return 2

    try:
        if a.paths or a.paths_from:
            paths = expand_paths((a.paths or []) + (read_list(a.paths_from) if a.paths_from else []))
            items = [(os.path.normpath(p), (lambda p=p: read_disk(p))) for p in paths]
        else:
            top = git(["rev-parse", "--show-toplevel"], os.getcwd())
            if top.returncode:
                print("precommit_scan: not in a git work tree (use --paths)", file=sys.stderr)
                return 2
            root = top.stdout.decode().strip()
            items = [(p, (lambda p=p: read_index(root, p))) for p in staged_paths(root)]
        found, ok = scan(items, needles, entries)
    except FileNotFoundError as e:
        print(f"precommit_scan: no such file: {e}", file=sys.stderr)
        return 2
    except (RuntimeError, OSError, subprocess.SubprocessError) as e:
        print(f"precommit_scan: {e}", file=sys.stderr)
        return 2
    report(found, ok, len(items), a.fingerprints, allow_file)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
