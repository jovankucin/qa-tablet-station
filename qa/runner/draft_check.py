"""Validate a bug draft against the Swiss Army Knife report form, and check it and its evidence pack for secrets.

python3 -I qa/runner/draft_check.py <ticket dir> [--link-json <run record or link json>] [--redact-to <dir>]
                                    [--no-secrets-file]

<ticket dir> holds title.txt, description.txt, draft.json, pack.md (bug-draft's output) and pack/ (copied evidence).
Mirrors WingtraCloud apps/src/pages/Dev/schemas/reportFormSchema.ts at 84d3721 (yup):
  title        trim, 3..100, no backslash and no U+0008 (INVALID_CHARS_REGEX = /[\\\b]/), required
  description  trim, 3..1000, required; the four template headings count toward the 1000
  Lengths are JS .length (UTF-16 units) after a JS trim() (which also strips U+FEFF); the description's line
  breaks count as one unit each, as in the textarea value (CRLF is normalised to LF).
House rules (memory bug-ticket-format, bug-draft contract):
  title "[CT] <area>: <symptom> (<condition>)", area not a version; the four headings as exact whole lines, in order,
  nothing before the first; Issue Summary has Where:/Steps:/Expected:/Actual: lines; Additional Notes has Scope:,
  Flags:, Link: and "Criticality (suggested): <1-6> - <why>" lines; plain text (no **, no markdown links or images);
  nothing that SAK appends by itself (About label, hash7, git hash, native version, build time, compatibility,
  Sentry, tenant, user agent, sessions, drone HW/FW, emails, share links, URLs, route paths).
  Simulated drone (link.simulated, MockLink or UDP SITL, vehicle ID 1..99, 9999 or 99999): a line starting "Link:"
  holds simulated|stand-in|SITL and the vehicle ID as a whole word. The link comes from --link-json, else from
  draft.json run_record (which must exist when set, and must be set when draft.json source is a run record), else
  from draft.json link.
Evidence: a file is text when its first 8 KB hold no NUL byte. Text files are scanned for tokens, passwords, emails,
  share links and the values in qa/secrets.env. Archives fail. Images, recordings and other binaries must be listed in
  draft.json reviewed_binaries {relpath: sha256} with the current sha256 (set after the person has looked at them);
  mp4/mov recordings longer than 60 s fail.
--redact-to <dir>: writes redact.redact() copies of every text file (binaries copied as they are) to <dir>, lists
  sha256 before and after under "redactions", re-scans the copies; the originals are never changed. Exit 0 only when
  the copies are clean and redaction changed none of title.txt, description.txt, draft.json, pack.md.
Without redact.py or with an empty or unreadable qa/secrets.env the run fails with "secrets were not checked",
  unless --no-secrets-file is given (standalone or eval use).
Exit 0 when clean, 1 otherwise, 2 on usage errors.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WORKSPACE = os.path.dirname(os.path.dirname(HERE))  # <workspace>/qa/runner -> <workspace>
sys.path.insert(0, HERE)
try:
    import redact as redact_mod  # the engine's redaction rules
except Exception:  # standalone copy without redact.py, or a broken one
    redact_mod = None

MASK = "[REDACTED]"
HEADINGS = ["## Issue Summary", "## Occurrence Rate", "## Tester Action and Workaround", "## Additional Notes"]
SECTION_LINES = {
    "## Issue Summary": ["Where:", "Steps:", "Expected:", "Actual:"],
    "## Additional Notes": ["Scope:", "Flags:", "Link:", "Criticality (suggested):"],
}
AUTHORED = ("title.txt", "description.txt", "draft.json", "pack.md")
TITLE_RE = re.compile(r"\[CT\] (?P<area>[^:\n]{2,40}): [^\n]+\([^)\n]+\)\Z")
HEADING_LIKE = re.compile(r"^[ \t]*#{1,6}(?:[ \t]|$)|^[ \t]*#+[ \t]*(?:Issue Summary|Occurrence Rate|Tester Action"
                          r" and Workaround|Additional Notes)\b", re.I)
# JS String.prototype.trim(): WhiteSpace (TAB VT FF SP NBSP ZWNBSP and the other Zs) and LineTerminator (LF CR LS PS)
JS_WS = "\t\n\v\f\r \u00a0\u1680" + "".join(map(chr, range(0x2000, 0x200B))) + "\u2028\u2029\u202f\u205f\u3000\ufeff"

EMAIL = r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,}"  # as redact.py: a redacted copy re-scans clean
SHARE = r"/shared/[\w-]{6,}"
HASH7 = r"(?=[0-9a-f]{0,11}[a-f])(?=[0-9a-f]{0,11}\d)[0-9a-f]{7,12}"
ROUTES = ("sites|flights|fleet|maps|processing|history|plane|settings|help|object-detection|detect-anything"
          "|log-analysis|telemetry-analysis|survey-outcomes|shared")
SAK_APPENDS = [
    (re.compile(r"(?i)\b[0-9a-f]{40}\b"), "full git hash (SAK appends it)"),
    (re.compile(r"\b\d+\.\d+\.\d+-(develop|staging|preproduction|production)(-[0-9a-f]{7})?\b"),
     "About label (SAK appends app version and environment)"),
    (re.compile(r"\b(develop|staging|preproduction|production)-[0-9a-f]{7}\b"), "environment-hash7 (SAK appends it)"),
    (re.compile(r"\b\d+\.\d+\.\d+\b[^\n]{0,20}?\b" + HASH7 + r"\b|\b" + HASH7 + r"\b[^\n]{0,20}?\b\d+\.\d+\.\d+\b"),
     "hash7 next to a version (SAK appends it)"),
    (re.compile(r"(?i)\b(?:git|commit|hash)\s*[:=]?\s*[0-9a-f]{7,40}\b"), "git hash (SAK appends it)"),
    (re.compile(r"(?i)\bnative\s*(?:version|build|code)?\s*[:=#]?\s*\d+|\bnative version\b"),
     "native version (SAK appends it)"),
    (re.compile(r"(?i)\bversion\s*code\s*[:=]?\s*\d+"), "native version code (SAK appends it)"),
    (re.compile(r"(?i)\bbuild[\s_-]?time\b"), "build time (SAK appends it)"),
    (re.compile(r"(?i)\bcompatib(?:ility|le)(?:\s+(?:number|version|no\.?))?\s*[:=]?\s*\d+"),
     "compatibility number (SAK appends it)"),
    (re.compile(r"(?i)\bsentry\b"), "Sentry (SAK appends the replay link)"),
    (re.compile(r"(?i)\btenant\b"), "tenant (SAK appends it)"),
    (re.compile(r"Mozilla/5\.0|(?i:\buser[\s-]?agent\b)"), "user agent (SAK appends it)"),
    (re.compile(r"(?i)\b(?:PX4|OBC)\s+session\b|\bsession(?:\s*id)?\s*[:=]\s*\S+"), "session (SAK appends it)"),
    (re.compile(r"(?i)\b(?:HW|FW)\s*[:=]?\s*v?\d|\b(?:hardware|firmware)(?:\s+version)?\s*[:=]\s*v?\d"),
     "drone HW/FW (SAK appends it)"),
    (re.compile(EMAIL), "email address (SAK appends the user)"),
    (re.compile(SHARE), "flight share link (SAK appends it)"),
    (re.compile(r"(?i)\b(?:https?|wss?)://\S+|\bwww\.\S+"), "URL (SAK appends the route URL)"),
    (re.compile(r"(?i)\b(?:[a-z0-9-]+\.)+company\.example\b"), "Wingtra host name (SAK appends the route URL)"),
    (re.compile(r"(?<![\w.])/(?:" + ROUTES + r")/(?=[\w-]*\d)[\w-]{3,}"), "route path with an id (SAK appends the URL)"),
    (re.compile(r"\*\*"), "markdown ** (ClickUp shows it literally)"),
    (re.compile(r"!\[[^\]\n]*\]\(|\]\("), "markdown link or image (ClickUp shows it literally)"),
]
NOT_MASK = r"(?!\"?\[REDACTED\])"
SECRET_PATTERNS = [
    (re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{5,}"), "JWT"),
    (re.compile(r"(?i)\bbearer\s+" + NOT_MASK + r"[A-Za-z0-9._~+/=-]{16,}"), "bearer token"),
    (re.compile(r"(?i)\b(?:authorization|cookie|set-cookie|x-api-key)\"?\s*[:=]\s*\"?" + NOT_MASK + r"[^\"\s]{2,}"),
     "auth or cookie header"),
    (re.compile(r'(?i)"name"\s*:\s*"(?:authorization|cookie|set-cookie|x-api-key)"\s*,\s*"value"\s*:\s*"' + NOT_MASK
                + r'[^"]+'), "HAR auth or cookie header"),
    (re.compile(r"(?i)\b(?:access|refresh|id|session|auth|api)[_-]?token\"?\s*[:=]\s*\"?" + NOT_MASK
                + r"[A-Za-z0-9._~+/=-]{16,}"), "token"),
    (re.compile(r"(?i)\b(?:password|passwd|pwd)\"?\s*[:=]\s*\"?" + NOT_MASK + r"[^\s\"',}]{2,}"), "password"),
    (re.compile(EMAIL), "email"),
    (re.compile(SHARE), "share link"),
]
SIMULATED_WORD = re.compile(r"(?i)\b(simulated|stand-in)\b")
ARCHIVE_EXT = {".zip", ".gz", ".tgz", ".7z", ".tar", ".rar", ".bz2", ".xz", ".zst"}
ARCHIVE_MAGIC = (b"PK\x03\x04", b"PK\x05\x06", b"\x1f\x8b", b"7z\xbc\xaf\x27\x1c", b"Rar!\x1a\x07", b"BZh",
                 b"\xfd7zXZ\x00", b"\x28\xb5\x2f\xfd")
MEDIA_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".heic", ".tif", ".tiff", ".mp4", ".mov"}
VIDEO_EXT = {".mp4", ".mov"}
MAX_RECORDING_S = 60
IGNORED = {".DS_Store"}


def js_trim(s):
    return s.strip(JS_WS)


def utf16_len(s):
    return len(s.encode("utf-16-le", "surrogatepass")) // 2


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_authored(path, problems, what):
    """Text of an authored file (strict UTF-8), or None with a problem."""
    try:
        with open(path, "rb") as f:
            return f.read().decode("utf-8")
    except FileNotFoundError:
        problems.append(f"{what} is missing")
    except (OSError, UnicodeDecodeError) as e:
        problems.append(f"{what} is not readable UTF-8 ({type(e).__name__})")
    return None


def resolve(p, ticket_dir):
    """A path from draft.json: absolute, or relative to the cwd, the ticket dir or the workspace."""
    if os.path.isabs(p):
        return p if os.path.exists(p) else None
    for base in (os.getcwd(), ticket_dir, WORKSPACE):
        c = os.path.join(base, p)
        if os.path.exists(c):
            return c
    return None


def vehicle_id(link):
    for k in ("vehicle_id", "vehicleId", "drone"):
        v = link.get(k)
        if isinstance(v, bool) or v is None:
            continue
        if isinstance(v, int) or (isinstance(v, str) and v.strip().isdigit()):
            return int(v)
    return None


def link_simulated(link):
    """(simulated, vehicle_id). Vehicle ID 0 (SAK's 'no drone') and None do not count as simulated."""
    vid = vehicle_id(link)
    s = link.get("simulated")
    kind = str(link.get("kind") or "").lower()
    sim = (s is True or (isinstance(s, str) and s.lower() == "true") or kind in ("mocklink", "udp_sitl")
           or (vid is not None and (0 < vid < 100 or vid in (9999, 99999))))
    return sim, vid


def load_json(path):
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def load_link(ticket_dir, draft, link_json, problems, notes):
    """Link from --link-json, else draft.json run_record, else draft.json link. Returns (link, src, record)."""
    if link_json:
        try:
            d = load_json(link_json)
        except (OSError, ValueError) as e:
            problems.append(f"--link-json {link_json} is not readable JSON ({type(e).__name__})")
            return {}, "--link-json", None
        link = d.get("link", d) if isinstance(d, dict) else None
        return (link if isinstance(link, dict) else {}), "--link-json", d
    rr = draft.get("run_record")
    if rr:
        p = resolve(str(rr), ticket_dir)
        if not p:
            problems.append(f"draft.json run_record {rr} not found")
            return {}, "run_record", None
        try:
            d = load_json(p)
        except (OSError, ValueError) as e:
            problems.append(f"draft.json run_record {rr} is not readable JSON ({type(e).__name__})")
            return {}, "run_record", None
        link = d.get("link") if isinstance(d, dict) else None
        if not isinstance(link, dict):
            notes.append(f"run record {rr} has no link block")
            link = {}
        return link, "run_record", d
    src = draft.get("source")
    if src:
        p = resolve(str(src), ticket_dir)
        is_run = bool(re.match(r"R-.*\.json\Z", os.path.basename(str(src))))
        if p and not is_run and p.endswith(".json"):
            try:
                is_run = (load_json(p) or {}).get("kind") == "run"
            except (OSError, ValueError, AttributeError):
                pass
        if is_run:
            problems.append(f"draft.json source {src} is a run record but run_record is not set")
    link = draft.get("link")
    return (link if isinstance(link, dict) else {}), "draft.json", None


def check_headings(lines, problems):
    """Exact whole heading lines in order. Returns {heading: [section lines]} or None when they are wrong."""
    found = [(i, ln.rstrip(" \t")) for i, ln in enumerate(lines) if HEADING_LIKE.match(ln)]
    texts = [t for _, t in found]
    if texts == HEADINGS:
        if any(ln.strip(JS_WS) for ln in lines[:found[0][0]]):
            problems.append(f"text before '{HEADINGS[0]}' (the description starts with the first heading)")
        bounds = [i for i, _ in found] + [len(lines)]
        return {h: lines[bounds[k] + 1:bounds[k + 1]] for k, h in enumerate(HEADINGS)}
    for t in texts:
        if t not in HEADINGS:
            problems.append(f"heading line '{t.strip()}' is not one of the four exact headings")
    counts = [texts.count(h) for h in HEADINGS]
    for h, n in zip(HEADINGS, counts):
        if n == 0:
            problems.append(f"missing heading line '{h}'")
        elif n > 1:
            problems.append(f"heading '{h}' appears {n} times")
    if counts == [1] * len(HEADINGS) and [t for t in texts if t in HEADINGS] != HEADINGS:
        problems.append("headings out of order")
    return None


def scan_text(text, label, secrets):
    out = []
    for rx, what in SECRET_PATTERNS:
        if rx.search(text):
            out.append(f"{label}: contains a {what}")
    if any(s in text for s in secrets):
        out.append(f"{label}: contains a value from qa/secrets.env")
    return out


def binary_strings(path, limit=256 << 20):
    """Printable ASCII and UTF-16LE runs of a binary file, like strings(1)."""
    if os.path.getsize(path) > limit:
        return None
    with open(path, "rb") as f:
        data = f.read()
    parts = [m.group().decode("ascii") for m in re.finditer(rb"[\x20-\x7e]{6,}", data)]
    parts += [m.group().decode("utf-16-le") for m in re.finditer(rb"(?:[\x20-\x7e]\x00){6,}", data)]
    return "\n".join(parts)


def mp4_duration(path):
    """Seconds from the moov/mvhd box (version 0 or 1), or None when it cannot be read."""
    with open(path, "rb") as f:
        end_of_file = os.fstat(f.fileno()).st_size

        def boxes(start, end):
            pos = start
            while pos + 8 <= end:
                f.seek(pos)
                size, typ = struct.unpack(">I4s", f.read(8))
                head = 8
                if size == 1:
                    ext = f.read(8)
                    if len(ext) < 8:
                        return
                    size, head = struct.unpack(">Q", ext)[0], 16
                elif size == 0:
                    size = end - pos
                if size < head or pos + size > end:
                    return
                yield typ, pos + head, pos + size
                pos += size

        for typ, s, e in boxes(0, end_of_file):
            if typ != b"moov":
                continue
            for t2, s2, e2 in boxes(s, e):
                if t2 != b"mvhd":
                    continue
                f.seek(s2)
                data = f.read(min(e2 - s2, 32))
                if not data:
                    return None
                if data[0] == 1 and len(data) >= 32:
                    scale, dur = struct.unpack(">IQ", data[20:32])
                    unknown = dur == 0xFFFFFFFFFFFFFFFF
                elif data[0] == 0 and len(data) >= 20:
                    scale, dur = struct.unpack(">II", data[12:20])
                    unknown = dur == 0xFFFFFFFF
                else:
                    return None
                if not scale or not dur or unknown:
                    return None
                return dur / scale
    return None


def classify(path):
    """'archive', 'media', 'binary' or 'text'."""
    ext = os.path.splitext(path)[1].lower()
    with open(path, "rb") as f:
        head = f.read(8192)
    if ext in ARCHIVE_EXT or head.startswith(ARCHIVE_MAGIC) or head[257:262] == b"ustar":
        return "archive"
    if ext in MEDIA_EXT:
        return "media"
    return "binary" if b"\x00" in head else "text"


def walk(root, skip_dir=None):
    """Relative paths of every file under root, sorted; skip_dir (a realpath) is left out."""
    out = []
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if not (skip_dir and os.path.realpath(os.path.join(d, x)) == skip_dir))
        for fn in sorted(files):
            out.append(os.path.relpath(os.path.join(d, fn), root).replace(os.sep, "/"))
    return out


def check_files(root, rels, draft, secrets, label=""):
    """Findings for the files rels under root: ([(rel, problem)], notes, {rel: kind}).
    Text files are scanned; binaries need a review entry; recordings are at most 60 s."""
    problems, notes, kinds = [], [], {}
    reviewed = draft.get("reviewed_binaries") if isinstance(draft.get("reviewed_binaries"), dict) else {}
    for rel in rels:
        p = os.path.join(root, rel)
        name = label + rel
        try:
            kind = classify(p)
        except OSError as e:
            problems.append((rel, f"{name}: not readable ({type(e).__name__})"))
            continue
        kinds[rel] = kind
        if kind == "archive":
            problems.append((rel, f"{name}: archive; unpack it and attach the files, so they can be scanned"))
            continue
        if kind == "text":
            with open(p, encoding="utf-8", errors="replace") as f:
                problems += [(rel, x) for x in scan_text(f.read(), name, secrets)]
            continue
        want = str(reviewed.get(rel) or "").lower()
        if not want:
            problems.append((rel, f"{name}: {kind} file not reviewed (add it to draft.json reviewed_binaries with"
                                  " its sha256 after the person has looked at it)"))
        elif want != sha256_file(p):
            problems.append((rel, f"{name}: changed since review (sha256 differs from draft.json reviewed_binaries)"))
        if os.path.splitext(rel)[1].lower() in VIDEO_EXT:
            try:
                dur = mp4_duration(p)
            except (OSError, struct.error):
                dur = None
            if dur is None:
                problems.append((rel, f"{name}: could not read the recording length (mvhd)"))
            elif dur > MAX_RECORDING_S:
                problems.append((rel, f"{name}: recording is {dur:.1f} s (at most {MAX_RECORDING_S} s)"))
        elif kind == "binary":
            strings = binary_strings(p)
            if strings is None:
                notes.append(f"{name}: too large to scan its strings")
            else:
                problems += [(rel, f"{x} (in its strings)") for x in scan_text(strings, name, secrets)]
    if not label:
        for rel in sorted(set(reviewed) - set(rels)):
            notes.append(f"draft.json reviewed_binaries lists {rel}, which is not in the ticket dir")
    return problems, notes, kinds


def write_copies(ticket_dir, rels, kinds, out_dir):
    """redact.redact() copies of text files, plain copies of binaries. Returns the redactions list."""
    redactions = []
    for rel in rels:
        kind = kinds.get(rel)
        if kind in (None, "archive"):
            continue
        src, dst = os.path.join(ticket_dir, rel), os.path.join(out_dir, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        before = sha256_file(src)
        if kind == "text":
            with open(src, "rb") as f:
                text = f.read().decode("utf-8", "surrogateescape")
            with open(dst, "wb") as f:
                f.write(redact_mod.redact(text).encode("utf-8", "surrogateescape"))
        else:
            shutil.copyfile(src, dst)
        after = sha256_file(dst)
        redactions.append({"file": rel, "kind": kind, "sha256_before": before, "sha256_after": after,
                           "changed": before != after})
    return redactions


def check(ticket_dir, link_json=None, redact_to=None, no_secrets_file=False):
    problems, notes = [], []
    ticket_dir = os.path.abspath(ticket_dir)
    res = {"title_chars": None, "description_chars": None, "problems": problems, "notes": notes, "ok": False}
    if not os.path.isdir(ticket_dir):
        problems.append(f"{ticket_dir} is not a directory")
        return res
    raw_title = read_authored(os.path.join(ticket_dir, "title.txt"), problems, "title.txt")
    raw_desc = read_authored(os.path.join(ticket_dir, "description.txt"), problems, "description.txt")
    draft = {}
    raw_draft = read_authored(os.path.join(ticket_dir, "draft.json"), problems, "draft.json")
    if raw_draft is not None:
        try:
            draft = json.loads(raw_draft.lstrip("\ufeff"))
        except ValueError:
            problems.append("draft.json is not valid JSON")
        if not isinstance(draft, dict):
            problems.append("draft.json is not a JSON object")
            draft = {}
        for k in ("source", "run_record", "criticality", "title_chars", "description_chars", "link", "scope", "flags",
                  "tablet_name", "evidence"):
            if k not in draft:
                problems.append(f"draft.json has no {k}")
    pm = os.path.join(ticket_dir, "pack.md")
    if not os.path.isfile(pm) or not open(pm, encoding="utf-8", errors="replace").read().strip():
        problems.append("pack.md is missing or empty (the evidence pack plan)")

    # reportFormSchema.ts (yup): JS trim, JS .length
    title = js_trim(raw_title) if raw_title is not None else None
    desc = js_trim(raw_desc.replace("\r\n", "\n").replace("\r", "\n")) if raw_desc is not None else None
    if title is not None:
        n = res["title_chars"] = utf16_len(title)
        if not 3 <= n <= 100:
            problems.append(f"title is {n} UTF-16 units after trim (3..100)")
        if "\\" in title or "\b" in title:
            problems.append("title contains a backslash or U+0008")
        if "\n" in title or "\r" in title:
            problems.append("title.txt must be one line")
        m = TITLE_RE.match(title)
        if not m:
            problems.append('title is not "[CT] <area>: <symptom> (<condition>)"')
        elif re.search(r"\d+\.\d+", m.group("area")):
            problems.append("title area holds a version; name the page or feature")
        if isinstance(draft.get("title_chars"), int) and draft["title_chars"] != n:
            problems.append(f"draft.json title_chars is {draft['title_chars']}, the title is {n}")
    if desc is not None:
        n = res["description_chars"] = utf16_len(desc)
        if not 3 <= n <= 1000:
            problems.append(f"description is {n} UTF-16 units after trim (3..1000, the four headings included)")
        if isinstance(draft.get("description_chars"), int) and draft["description_chars"] != n:
            problems.append(f"draft.json description_chars is {draft['description_chars']}, the description is {n}")

    # house format
    lines = desc.split("\n") if desc is not None else []
    sections = check_headings(lines, problems) if desc is not None else None
    if sections:
        for h, labels in SECTION_LINES.items():
            starts = [ln.lstrip(" \t") for ln in sections[h]]
            for lab in labels:
                if not any(s.startswith(lab) for s in starts):
                    problems.append(f"'{h}' has no line starting '{lab}'")
    for text, what in ((title, "title"), (desc, "description")):
        for rx, why in SAK_APPENDS if text else ():
            if rx.search(text):
                problems.append(f"{what} repeats {why}")
    crit = draft.get("criticality")
    if not (isinstance(crit, int) and not isinstance(crit, bool) and 1 <= crit <= 6):
        problems.append("draft.json criticality must be an integer 1..6")
    if sections:
        cl = [re.match(r"[ \t]*Criticality \(suggested\):[ \t]*([1-6])[ \t]+-[ \t]*\S", ln)
              for ln in sections["## Additional Notes"]]
        cl = [m for m in cl if m]
        if not cl:
            problems.append("no 'Criticality (suggested): <1-6> - <why>' line")
        elif isinstance(crit, int) and not isinstance(crit, bool) and int(cl[0].group(1)) != crit:
            problems.append(f"description criticality {cl[0].group(1)} differs from draft.json criticality {crit}")

    # simulated drone
    link, src, record = load_link(ticket_dir, draft, link_json, problems, notes)
    sim, vid = link_simulated(link)
    res["link"] = {"src": src, "kind": link.get("kind"), "vehicle_id": vid, "simulated": sim}
    dl = draft.get("link")
    if isinstance(dl, dict):
        if not isinstance(dl.get("simulated"), bool):
            problems.append("draft.json link.simulated must be true or false")
        if src != "draft.json":
            dsim, dvid = link_simulated(dl)
            if dsim != sim:
                problems.append(f"draft.json link says simulated={dsim}, the {src} says simulated={sim}")
            if vid is not None and dvid != vid:
                problems.append(f"draft.json link vehicle_id {dvid} differs from the {src} ({vid})")
    if sim and desc is not None:
        link_lines = [ln for ln in lines if re.match(r"[ \t]*Link:", ln)]
        good = [ln for ln in link_lines
                if SIMULATED_WORD.search(ln) and (vid is None or re.search(rf"\b{vid}\b", ln))]
        if not good:
            what = f"vehicle {vid}" if vid is not None else str(link.get("kind") or "simulated")
            problems.append(f"the link is simulated ({what}) but no line starting 'Link:' says simulated or stand-in"
                            + (f" together with the ID {vid} as a whole word" if vid is not None else ""))
        if vid is None:
            notes.append("the simulated link has no vehicle_id; only the word was checked")

    run_ev = record.get("evidence_dir") if isinstance(record, dict) else None
    if run_ev and not os.path.isdir(os.path.join(ticket_dir, "pack")):
        notes.append(f"the run has evidence_dir {run_ev} but the ticket has no pack/")

    # secrets
    secrets = []
    if redact_mod is not None:
        secrets = [s for s in redact_mod.load_secret_values() if len(s) >= 4]
    if (redact_mod is None or not secrets) and not no_secrets_file:
        problems.append("secrets were not checked (redact.py missing, or qa/secrets.env missing, empty or unreadable);"
                        " pass --no-secrets-file only outside the engine")

    # files
    out_real = os.path.realpath(redact_to) if redact_to else None
    rels = []
    for r in walk(ticket_dir, out_real):
        if os.path.basename(r) in IGNORED:
            notes.append(f"ignored (not scanned, not copied): {r}")
        else:
            rels.append(r)
    fprob, fnotes, kinds = check_files(ticket_dir, rels, draft, secrets)
    notes += fnotes
    tdir = os.path.realpath(ticket_dir)
    if not redact_to:
        problems += [m for _, m in fprob]
    elif redact_mod is None or out_real == tdir or tdir.startswith(out_real + os.sep):
        problems.append("--redact-to needs redact.py" if redact_mod is None
                        else "--redact-to must not be the ticket dir or one of its parents")
        problems += [m for _, m in fprob]
    else:
        # binary and archive findings do not change with redaction; text findings are re-checked on the copies
        problems += [m for r, m in fprob if kinds.get(r) != "text"]
        notes += [f"before redaction: {m}" for r, m in fprob if kinds.get(r) == "text"]
        to_copy = {r for r, k in kinds.items() if k != "archive"}
        for r in (walk(redact_to) if os.path.isdir(redact_to) else []):
            if r not in to_copy:
                problems.append(f"{redact_to}: {r} is not a copy from this ticket (stale file)")
        reds = write_copies(ticket_dir, rels, kinds, redact_to)
        res["redact_to"] = os.path.abspath(redact_to)
        res["redactions"] = reds
        for r in reds:
            if r["changed"] and r["file"] in AUTHORED:
                problems.append(f"{r['file']}: redaction changed it; fix the text itself")
            if r["kind"] != "text" and r["changed"]:
                problems.append(f"{r['file']}: copy differs from the original")
        copied = [r["file"] for r in reds if r["kind"] == "text"]
        problems += [m for _, m in check_files(redact_to, copied, draft, secrets, label="copy:")[0]]
    res["ok"] = not problems
    return res


def cli(argv):
    """(exit code, result) for argv; result is None on usage errors."""
    ap = argparse.ArgumentParser(prog="draft_check.py", add_help=False)
    ap.add_argument("ticket_dir")
    ap.add_argument("--link-json")
    ap.add_argument("--redact-to")
    ap.add_argument("--no-secrets-file", action="store_true")
    try:
        a = ap.parse_args(argv)
    except SystemExit:
        return 2, None
    res = check(a.ticket_dir, a.link_json, a.redact_to, a.no_secrets_file)
    return (0 if res["ok"] else 1), res


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 2
    code, res = cli(argv)
    if res is None:
        print(__doc__)
        return code
    print(json.dumps(res, indent=1))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
