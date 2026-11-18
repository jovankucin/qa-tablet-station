"""Put your station's own values in place of this repo's placeholders, once, after cloning.

    python3 -I tools/fill_placeholders.py [--dry-run] placeholders.local.json

placeholders.local.json is a copy of placeholders.example.json with your value in place of each description you
need (or a flat {"<placeholder>": "<your value>"} map). Entries still holding the description are skipped. The file
is in .gitignore; never commit it.
__WORKSPACE__ defaults to this repo's folder and __PYTHON3__ to the python3 on your PATH; give them only to override.

What it does:
- replaces every placeholder in the text files of the tree (also its regex-escaped form, a.b -> a\\.b), except
  README.md, placeholders.example.json, the values file you pass, the templates folder and this tools folder. It never
  touches the app clones (WingtraCloud/, WingtraPilot/), your test notes (tests/), *.env files or the station's own
  records and per-machine folders (qa/runs, qa/ledger, qa/devices, qa/cache, qa/history, qa/issues, qa/tasks,
  qa/backups and the runner's local folder). Run it on a fresh clone, before you add files of your own;
- fills the 8-character short form of each filled id and the other spellings of each filled ticket number
  (CL80001, cl-80001, cl80001) by itself;
- refuses a value whose placeholder sits inside a longer one you left empty (for example company.example while
  web.dev.company.example is empty), because that longer one could not be filled afterwards;
- writes .claude/settings.json from .claude/settings.template.json (the safety gate and ledger hooks need absolute
  paths, so the template is never used as is), unless a different .claude/settings.json is already there;
- recomputes requirement.sha256 in qa/cases/*.json where the requirement text changed.
Fill before the first run: filling later changes case files, so their sha256 no longer matches older run records.
A second run fills only placeholders still left; keep the earlier values in the file. Values must be plain text
(no quotes, backslashes, tabs or line breaks). After it ran, do not commit: the tree now holds your local values.
"""
import json
import os
import re
import shutil
import sys

sys.dont_write_bytecode = True  # python3 -I ignores PYTHONDONTWRITEBYTECODE; a dry run writes nothing
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP = {"README.md", "placeholders.example.json", "placeholders.local.json", os.path.join(".claude", "settings.template.json")}
SKIP_DIRS = {".git", "__pycache__"}
SKIP_TOP = {"tools", "templates", "tests", "WingtraCloud", "WingtraPilot"}  # folders at the repo root
SKIP_QA = {"runs", "ledger", "devices", "cache", "history", "issues", "tasks", "backups"}  # folders in qa/
SKIP_RUNNER = {"state"}  # qa/runner's per-machine folder
UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
TICKET = re.compile(r"CL-(\d+)")
TICKET_ANY = re.compile(r"(?:CL-|CL|cl-|cl)\d+")


def expand(values):
    """Short forms of filled ids and the other spellings of filled ticket numbers come along."""
    out = dict(values)
    for fake, real in values.items():
        if UUID.fullmatch(fake) and UUID.fullmatch(real):
            out.setdefault(fake[:8], real[:8])
        m, r = TICKET.fullmatch(fake), TICKET.fullmatch(real)
        if m and r:
            for pre in ("CL", "cl-", "cl"):
                out.setdefault(pre + m[1], pre + r[1])
    return out


def rules(values):
    pairs = []
    for fake, real in values.items():
        pairs.append((fake, real))
        if "." in fake:
            pairs.append((fake.replace(".", "\\."), real.replace(".", "\\.")))
    out = []
    for fake, real in sorted(pairs, key=lambda p: -len(p[0])):
        rx = re.escape(fake)
        if re.fullmatch(r"[0-9a-fA-F-]+", fake):  # ids and numbers: whole tokens only
            rx = r"(?<![0-9A-Za-z])" + rx + r"(?![0-9A-Za-z])"
        elif TICKET_ANY.fullmatch(fake):
            rx = r"(?<![0-9A-Za-z])" + rx + r"(?!\d)"
        out.append((fake, re.compile(rx), real.replace("\\", "\\\\")))
    return out


def text_files(skip_real=()):
    for d, dirs, files in os.walk(REPO):
        rel_d = os.path.relpath(d, REPO)
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS
                   and not (rel_d == "." and x in SKIP_TOP)
                   and not (rel_d == "qa" and x in SKIP_QA)
                   and not (rel_d == os.path.join("qa", "runner") and x in SKIP_RUNNER)]
        for f in files:
            p = os.path.join(d, f)
            if os.path.relpath(p, REPO) in SKIP or f.endswith(".env") or os.path.realpath(p) in skip_real:
                continue
            try:
                yield p, open(p, encoding="utf-8").read()
            except (UnicodeDecodeError, OSError):
                continue


def fix_sha(path, text):
    rel = os.path.relpath(path, REPO).replace(os.sep, "/")
    if not re.fullmatch(r"qa/(?:.+/)?cases/[^/]+\.json", rel):  # qa/cases and qa/runner/tests/cases
        return text
    sys.path.insert(0, os.path.join(REPO, "qa", "app"))
    import case_schema  # noqa: E402
    data = json.loads(text)
    req = (data.get("requirement") or {}) if isinstance(data, dict) else {}
    old, body = req.get("sha256"), req.get("text")
    if not (old and isinstance(body, str)):
        return text
    return text.replace(old, case_schema.requirement_sha256(body))


def main(argv):
    files = [a for a in argv if not a.startswith("-")]
    if len(files) != 1 or set(argv) - set(files) - {"--dry-run"}:
        print(__doc__)
        return 2
    dry = "--dry-run" in argv
    data = json.load(open(files[0], encoding="utf-8"))
    values = dict(data.get("placeholders", data)) if isinstance(data, dict) else {}
    values.pop("about", None)
    known = json.load(open(os.path.join(REPO, "placeholders.example.json"), encoding="utf-8"))["placeholders"]
    # an entry still holding the example's description was not filled in: leave that placeholder alone
    values = {k: v for k, v in values.items() if isinstance(v, str) and v.strip() and v != known.get(k)}
    bad = sorted(k for k, v in values.items() if v != v.strip() or any(c in v for c in '"\\\t\r\n'))
    if bad:
        print("values must be plain text (no surrounding spaces, quotes, backslashes, tabs or line breaks):",
              ", ".join(bad))
        return 2
    values.setdefault("__WORKSPACE__", REPO)
    values.setdefault("__PYTHON3__", shutil.which("python3") or sys.executable)
    values = expand(values)
    allowed = set(expand({k: k for k in known}))
    unknown = sorted(set(values) - allowed)
    if unknown:
        print("not placeholders of this repo:", ", ".join(unknown))
        return 2
    rs = rules(values)
    # a value whose match rule would hit inside a longer placeholder left empty would make that one unfillable
    inside = sorted({(k, other) for k, rx, _ in rs for other in allowed - set(values)
                     if k in values and k != other and rx.search(other)})
    if inside:
        for k, other in inside:
            print(f"{k} sits inside {other}, which has no value: give {other} a value too, or leave {k} out")
        return 2
    missing = sorted(set(known) - set(values))
    counts = {}
    for path, text in text_files(skip_real={os.path.realpath(files[0])}):
        new = text
        for fake, rx, real in rs:
            new, n = rx.subn(real, new)
            if n:
                counts[fake] = counts.get(fake, 0) + n
        if new != text:
            new = fix_sha(path, new)
            if not dry:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(new)
    tpl = os.path.join(REPO, ".claude", "settings.template.json")
    settings = open(tpl, encoding="utf-8").read()
    for fake, rx, real in rs:
        settings = rx.sub(real, settings)
    target = os.path.join(REPO, ".claude", "settings.json")
    old = open(target, encoding="utf-8").read() if os.path.exists(target) else None
    rc = 0
    if old is not None and old != settings:
        print("WARNING: .claude/settings.json exists and differs, so the gate and ledger hooks were NOT installed. "
              "Merge the \"hooks\" block of this into it by hand:\n" + settings)
        rc = 1
    elif not dry:
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(settings)
    for fake in sorted(counts):
        print(f"{counts[fake]:5d}  {fake}")
    if missing:
        print("left as placeholders (no value given):", ", ".join(missing))
    print("dry run, nothing written" if dry else "done")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
