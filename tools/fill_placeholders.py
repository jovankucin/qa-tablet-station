"""Put your station's own values in place of this repo's placeholders, once, after cloning; or put the placeholders
back (--reverse) before you commit new work from a filled station.

    python3 -I tools/fill_placeholders.py [--dry-run] [--allow-shared] placeholders.local.json
    python3 -I tools/fill_placeholders.py --reverse [--dry-run] placeholders.local.json

placeholders.local.json is a copy of placeholders.example.json with your value in place of each description you
need (or a flat {"<placeholder>": "<your value>"} map). Entries still holding the description are skipped. The file
is in .gitignore; never commit it.
__WORKSPACE__ defaults to this repo's folder and __PYTHON3__ to the python3 on your PATH; give them only to override.

Scope: the text files of the tree, except README.md, placeholders.example.json, the values file you pass,
.claude/settings*.json, templates/, tools/, the app clones (WingtraCloud/, WingtraPilot/), your test notes (tests/),
*.env files and the station's own records and per-machine folders (qa/runs, qa/ledger, qa/devices, qa/cache,
qa/history, qa/issues, qa/tasks, qa/backups and the runner's local folder).

What a fill does:
- replaces every placeholder in scope (also its regex-escaped form, a.b -> a\\.b), plus the 8-character short form
  of each filled id and the other spellings of each filled ticket number (CL80001, cl-80001, cl80001);
- refuses a value whose placeholder sits inside a longer one you left empty (for example company.example while
  web.dev.company.example is empty), because that longer one could not be filled afterwards;
- refuses one value for two placeholders, because --reverse could not tell them apart later; --allow-shared fills
  anyway (then --reverse refuses and placeholders must be put back by hand);
- warns when a value already occurs in the shipped files: a later --reverse would turn those into the placeholder;
- recomputes requirement.sha256 in changed case files (qa/cases, qa/runner/tests/cases) and each
  supersedes.file_sha256 that matched its older case version before the fill;
- prepares every changed file first, checks that each changed .json and .jsonl file still parses, and writes nothing
  if one does not;
- lists every placeholder still present afterwards, by name and file count;
- writes .claude/settings.json from .claude/settings.template.json (the safety gate and ledger hooks need absolute
  paths, so the template is never used as is); if a different .claude/settings.json is already there, it keeps it,
  prints the hooks to merge and exits 1.
Fill on a fresh clone, before you add files of your own and before the first run: filling later changes case files,
so their sha256 no longer matches older run records. A second run fills only placeholders still left; keep the
earlier values in the file. Values must be plain text (no quotes, backslashes, tabs or line breaks). After a fill,
do not commit: the tree holds your local values.

--reverse puts the placeholders back in place of the same values (short forms and ticket spellings too), in the same
scope, with the same hash updates and parse check, so new cases or code from a filled station can be committed. It
leaves __PYTHON3__ and .claude/settings.json alone, refuses when two placeholders share one value, and also reverses
the workspace path the repo was filled at if it has moved since (read from .claude/settings.json). Afterwards it
searches every file git would commit (or, without git, the tree), README.md, templates/ and tools/ included, for any
value still there, prints those by placeholder name and file, and exits 1 if it finds one. Then read everything that
will be committed, new files too:
    git status --short && git add -A && git diff --cached
Fill again afterwards to keep working.

Exit codes: 0 done; 1 done with a warning to act on (settings not installed, values still present after --reverse);
2 nothing written (usage error, bad or unknown values, a refused value, a file that would no longer parse).
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True  # python3 -I ignores PYTHONDONTWRITEBYTECODE; a dry run writes nothing
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP = {"README.md", "placeholders.example.json", "placeholders.local.json",
        os.path.join(".claude", "settings.template.json"), os.path.join(".claude", "settings.json"),
        os.path.join(".claude", "settings.local.json")}
SKIP_DIRS = {".git", "__pycache__"}
SKIP_TOP = {"tools", "templates", "tests", "WingtraCloud", "WingtraPilot"}  # folders at the repo root
SKIP_QA = {"runs", "ledger", "devices", "cache", "history", "issues", "tasks", "backups"}  # folders in qa/
SKIP_RUNNER = {"state"}  # qa/runner's per-machine folder
UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
TICKET = re.compile(r"CL-(\d+)")
TICKET_ANY = re.compile(r"(?:CL-|CL|cl-|cl)\d+")
HEXLIKE = re.compile(r"[0-9a-fA-F-]+")
CASE_FILE = re.compile(r"qa/(?:.+/)?cases/[^/]+\.json")  # qa/cases and qa/runner/tests/cases
FLAGS = {"--dry-run", "--reverse", "--allow-shared"}


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


def rules(mapping, label_new=False):
    """(old, new) pairs -> (label, compiled rule, replacement), longest first. The placeholder side (old for a fill,
    new for --reverse) decides how a match must stand, so both directions agree: ids and numbers as whole tokens
    (never inside a sha256 or a longer number), ticket numbers never as a prefix, serials anywhere (they sit glued to
    adb's -s). On --reverse, a hex-like value of a free-matching placeholder never matches inside a hex run. The
    label is the placeholder, so counts never show a value."""
    pairs = []
    for old, new in mapping.items():
        pairs.append((old, new))
        if "." in old:
            pairs.append((old.replace(".", "\\."), new.replace(".", "\\.")))
    out = []
    for old, new in sorted(pairs, key=lambda p: -len(p[0])):
        placeholder = new if label_new else old
        rx = re.escape(old)
        if HEXLIKE.fullmatch(placeholder):
            rx = r"(?<![0-9A-Za-z])" + rx + r"(?![0-9A-Za-z])"
        elif TICKET_ANY.fullmatch(placeholder):
            rx = r"(?<![0-9A-Za-z])" + rx + r"(?!\d)"
        elif label_new and HEXLIKE.fullmatch(old):
            rx = r"(?<![0-9a-fA-F])" + rx + r"(?![0-9a-fA-F])"
        out.append((placeholder, re.compile(rx), new.replace("\\", "\\\\")))
    return out


def in_scope(rel):
    parts = rel.split("/")
    if rel in {p.replace(os.sep, "/") for p in SKIP} or parts[0] in SKIP_TOP or parts[-1].endswith(".env"):
        return False
    if any(x in SKIP_DIRS for x in parts[:-1]):
        return False
    if len(parts) > 2 and parts[0] == "qa" and parts[1] in SKIP_QA:
        return False
    return not (len(parts) > 3 and parts[:2] == ["qa", "runner"] and parts[2] in SKIP_RUNNER)


def walk(skip_real=(), everything=False):
    """(path, rel, text) of the text files in scope, or with everything=True of every text file outside .git, the
    app clones, tests/, the station's records and secrets."""
    for d, dirs, files in os.walk(REPO):
        rel_d = os.path.relpath(d, REPO).replace(os.sep, "/")
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS
                   and not (rel_d == "." and x in SKIP_TOP - ({"tools", "templates"} if everything else set()))
                   and not (rel_d == "qa" and x in SKIP_QA)
                   and not (rel_d == "qa/runner" and x in SKIP_RUNNER)]
        for f in files:
            p = os.path.join(d, f)
            rel = os.path.relpath(p, REPO).replace(os.sep, "/")
            if os.path.realpath(p) in skip_real or f.endswith(".env"):
                continue
            if not everything and not in_scope(rel):
                continue
            if everything and (rel.startswith(".claude/settings") or re.fullmatch(r"placeholders.*\.local\.json", rel)):
                continue  # ignored by git: the filled settings and local values files
            try:
                yield p, rel, open(p, encoding="utf-8").read()
            except (UnicodeDecodeError, OSError):
                continue


def committable(skip_real):
    """rel -> text of every file git would commit (tracked or new, not ignored); without git, a wide walk."""
    if os.path.isdir(os.path.join(REPO, ".git")) and shutil.which("git"):
        try:
            out = subprocess.run(["git", "-C", REPO, "ls-files", "-z", "-co", "--exclude-standard"],
                                 capture_output=True, text=True, timeout=60, check=True).stdout
            texts = {}
            for rel in filter(None, out.split("\0")):
                p = os.path.join(REPO, rel)
                if os.path.realpath(p) in skip_real or not os.path.isfile(p):
                    continue
                try:
                    texts[rel] = open(p, encoding="utf-8").read()
                except (UnicodeDecodeError, OSError):
                    continue
            return texts
        except (subprocess.SubprocessError, OSError):
            pass
    return {rel: text for _, rel, text in walk(skip_real, everything=True)}


def fix_sha(rel, text):
    """A case file's requirement.sha256 follows its requirement text."""
    if not CASE_FILE.fullmatch(rel):
        return text
    sys.path.insert(0, os.path.join(REPO, "qa", "app"))
    import case_schema  # noqa: E402
    data = json.loads(text)
    req = (data.get("requirement") or {}) if isinstance(data, dict) else {}
    old, body = req.get("sha256"), req.get("text")
    if not (old and isinstance(body, str)):
        return text
    return text.replace(old, case_schema.requirement_sha256(body))


def sha_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fix_supersedes(before, after):
    """{rel: text} before and after. A case's supersedes.file_sha256 that matched its older version before is set to
    that version's hash after (oldest version first, so chains stay whole); a pointer that was already stale stays."""
    done = {}

    def final(p, depth=0):
        if p in done:
            return done[p]
        text = after[p]
        if CASE_FILE.fullmatch(p) and depth < 200:
            try:
                sup = json.loads(text).get("supersedes")
            except (ValueError, AttributeError):
                sup = None
            ref = sup.get("file") if isinstance(sup, dict) else None
            old = sup.get("file_sha256") if isinstance(sup, dict) else None
            if isinstance(ref, str) and isinstance(old, str) and ref != p and ref in after and ref in before \
                    and old == sha_text(before[ref]):
                new = sha_text(final(ref, depth + 1))
                if new != old:
                    text = re.sub(r'("file_sha256"\s*:\s*")' + re.escape(old) + '"',
                                  lambda m: m.group(1) + new + '"', text)
        done[p] = text
        return text

    return {p: final(p) for p in after}


def parse_errors(rel, text):
    try:
        if rel.endswith(".json"):
            json.loads(text)
        elif rel.endswith(".jsonl"):
            for line in text.splitlines():
                if line.strip():
                    json.loads(line)
    except ValueError as e:
        return f"{rel}: {e}"
    return None


def found(rs, texts):
    """placeholder -> sorted files where its rule matches."""
    out = {}
    for label, rx, _ in rs:
        files = [rel for rel, text in texts.items() if rx.search(text)]
        if files:
            out.setdefault(label.replace("\\.", "."), set()).update(files)
    return {k: sorted(v) for k, v in out.items()}


def show(title, hits):
    print(title)
    for label in sorted(hits):
        files = hits[label]
        print(f"  {label}: {len(files)} file(s): {', '.join(files[:5])}{' ...' if len(files) > 5 else ''}")


def filled_at():
    """The workspace path .claude/settings.json was written for, or None."""
    try:
        s = json.load(open(os.path.join(REPO, ".claude", "settings.json"), encoding="utf-8"))
        cmd = s["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        m = re.search(r'"([^"]+)/qa/runner/gate_hook\.py"', cmd)
        return m.group(1) if m else None
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return None


def main(argv):
    flags = {a for a in argv if a.startswith("-")}
    files = [a for a in argv if not a.startswith("-")]
    if len(files) != 1 or flags - FLAGS:
        print(__doc__)
        return 2
    dry, reverse, allow_shared = "--dry-run" in flags, "--reverse" in flags, "--allow-shared" in flags
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
    reversible = {k: v for k, v in values.items() if k != "__PYTHON3__"}
    by_value = {}
    for k, v in reversible.items():
        by_value.setdefault(v, []).append(k)
    shared = sorted(sorted(ks) for ks in by_value.values() if len(ks) > 1)
    skip_real = {os.path.realpath(files[0])}
    if reverse:
        if shared:
            for ks in shared:
                print("these placeholders share one value, so --reverse cannot tell them apart:", ", ".join(ks))
            return 2
        mapping = {v: k for k, v in reversible.items()}
        old_ws = filled_at()
        if old_ws and old_ws != reversible["__WORKSPACE__"] and old_ws not in mapping:
            print(f"note: this repo was filled at another path; that path is put back as __WORKSPACE__ too")
            mapping[old_ws] = "__WORKSPACE__"
        rs = rules(mapping, label_new=True)
        rev = rs
    else:
        if shared and not allow_shared:
            for ks in shared:
                print("one value for several placeholders:", ", ".join(ks))
            print("--reverse could not tell them apart later. Give each its own value, or pass --allow-shared to "
                  "fill anyway (then put placeholders back by hand before committing).")
            return 2
        rs = rules(values)
        rev = rules({v: k for k, v in reversible.items()}, label_new=True) if not shared else []
        # a value whose match rule would hit inside a longer placeholder left empty would make that one unfillable
        inside = sorted({(k, other) for k, rx, _ in rs for other in allowed - set(values)
                         if k in values and k != other and rx.search(other)})
        if inside:
            for k, other in inside:
                print(f"{k} sits inside {other}, which has no value: give {other} a value too, or leave {k} out")
            return 2
    # prepare every change first
    counts, paths, before, after = {}, {}, {}, {}
    for path, rel, text in walk(skip_real):
        new = text
        for label, rx, rep in rs:
            new, n = rx.subn(rep, new)
            if n:
                counts[label] = counts.get(label, 0) + n
        if new != text:
            new = fix_sha(rel, new)
        paths[rel], before[rel], after[rel] = path, text, new
    after = fix_supersedes(before, after)
    changed = [rel for rel in after if after[rel] != before[rel]]
    errors = [e for e in (parse_errors(rel, after[rel]) for rel in changed) if e]
    if errors:
        print("nothing written: these files would no longer parse:\n  " + "\n  ".join(errors))
        return 2
    rc = 0
    if not reverse and rev:
        pre = found(rev, before)  # values already in the shipped files: --reverse would turn them into placeholders
        if pre:
            show("WARNING: these values already occur in shipped files; a later --reverse would turn those "
                 "occurrences into the placeholder too (check git diff then):", pre)
    if not dry:
        for rel in changed:
            with open(paths[rel], "w", encoding="utf-8") as fh:
                fh.write(after[rel])
    if not reverse:
        tpl = os.path.join(REPO, ".claude", "settings.template.json")
        settings = open(tpl, encoding="utf-8").read()
        for _, rx, rep in rs:
            settings = rx.sub(rep, settings)
        target = os.path.join(REPO, ".claude", "settings.json")
        old = open(target, encoding="utf-8").read() if os.path.exists(target) else None
        if old is not None and old != settings:
            print("WARNING: .claude/settings.json exists and differs, so the gate and ledger hooks were NOT installed. "
                  "Merge the \"hooks\" block of this into it by hand:\n" + settings)
            rc = 1
        elif not dry:
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(settings)
    for label in sorted(counts):
        print(f"{counts[label]:5d}  {label}")
    print(f"{len(changed)} files {'would change' if dry else 'changed'}")
    if reverse:
        texts = committable(skip_real)
        texts.update({rel: after[rel] for rel in after if rel in texts or dry})  # what the tree holds after this run
        left = found(rev, texts)
        if left:
            show("VALUES STILL PRESENT (put them back by hand, then run again):", left)
            rc = 1
        print("dry run, nothing written" if dry else
              "placeholders put back; now read everything that will be committed, new files too:\n"
              "  git status --short && git add -A && git diff --cached")
    else:
        left = found(rules({k: k for k in sorted(allowed)}), after)
        if left:
            show("placeholders still present (no value, or a value whose shape gave no short form or spelling):",
                 {k + (" (given a value)" if k in values else ""): v for k, v in left.items()})
        print("dry run, nothing written" if dry else "done")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
