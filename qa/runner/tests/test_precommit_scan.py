"""Cases for precommit_scan.py: secrets.env values and their encodings, redact.py's token patterns, e-mail and share
links, masks and code that must not count, the file rules, binary content, the allow file, the CLI exit codes and
the staged-files path (git replaced by stubs).

python3 -I qa/runner/tests/test_precommit_scan.py [-v]
Exit 0 when every case passes. Works in a temporary directory with a fake secrets file; qa/secrets.env is never
read. Planted values are put together at run time, so this file itself scans clean.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.dirname(HERE)
sys.path.insert(0, RUNNER)
import precommit_scan as ps  # noqa: E402

SCRIPT = os.path.join(RUNNER, "precommit_scan.py")
TMP = tempfile.mkdtemp(prefix="precommit_scan_test_")
FAKE_SECRET = "Fake-CT-" + "pass\"4711/x"  # made up; the quote and slash make the escaped forms differ
FAKE_MAIL = "ct.fake.tester" + "@" + "fake-corp.ch"
SECRETS = os.path.join(TMP, "secrets.env")
with open(SECRETS, "w", encoding="utf-8") as _f:
    _f.write(f"# fake\nCT_PASSWORD={FAKE_SECRET}\nCT_EMAIL={FAKE_MAIL}\n")

JWT = "ey" + "J" + "a" * 12 + "." + "b" * 12 + "." + "c" * 12
BEARER = "Bear" + "er " + "q" * 24
AUTH = "Author" + "ization: Basic " + "d" * 20
TOKEN = '"access' + '_token": "' + "t" * 20 + '"'
PASSWORD = '"pass' + 'word": "' + "hunter22x" + '"'
EMAIL = "qa.fake.person" + "@" + "fake-corp.ch"
SHARE = "https://web.staging.company.example/sha" + "re/" + "0" * 8
SHARED_PATH = "GET /sha" + "red/AbC_def-123"
PLANTED = [FAKE_SECRET, FAKE_MAIL, JWT, "q" * 24, "d" * 20, "t" * 20, "hunter22x", EMAIL, SHARE, "AbC_def-123"]

results = []


def truth(name, ok, detail=""):
    results.append((name, bool(ok), detail))


def write(rel, data):
    p = os.path.join(TMP, "repo", rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data.encode("utf-8") if isinstance(data, str) else data)
    return p


def needles():
    return ps.secret_needles([FAKE_SECRET, FAKE_MAIL])


def kinds_of(text):
    return [(f.line, f.kind) for f in ps.scan_blob("x.txt", text.encode(), len(text), needles())]


def cli(*args, stdin=None):
    p = subprocess.run([sys.executable, "-I", SCRIPT, "--secrets-file", SECRETS, *args], input=stdin,
                       capture_output=True, text=True, timeout=60)
    return p.returncode, p.stdout, p.stderr


def content_cases():
    n = needles()
    truth("secret value found on its line", kinds_of("a\nb " + FAKE_SECRET + " c\n") == [(2, ps.SECRET_KIND)])
    truth("JSON-escaped secret found", kinds_of(json.dumps({"v": FAKE_SECRET})) == [(1, ps.SECRET_KIND)])
    truth("URL-encoded secret found", kinds_of("p=" + urllib.parse.quote(FAKE_SECRET, safe="")) == [(1, ps.SECRET_KIND)])
    truth("secret e-mail found in any case", (1, ps.SECRET_KIND) in kinds_of(FAKE_MAIL.upper()))
    truth("secret e-mail is also an email finding", (1, "email") in kinds_of(FAKE_MAIL))
    truth("needles never shorter than 4", all(len(x) >= 4 for x, _ in ps.secret_needles(["abc", "abcd"])))
    truth("needle list sorted longest first", [len(x) for x, _ in n] == sorted((len(x) for x, _ in n), reverse=True))
    for text, kind in [(JWT, "JWT"), (BEARER, "bearer token"), (AUTH, "auth or cookie header"),
                       (TOKEN, "token field"), (PASSWORD, "password field"), (EMAIL, "email"), (SHARE, "share link"),
                       (SHARED_PATH, "share link"), ("Cook" + "ie: sid=" + "s" * 16, "auth or cookie header")]:
        got = [k for _, k in kinds_of("x " + text + " y")]
        truth(f"{kind} found ({text[:6]}...)", kind in got, got)
    for text in ['"pass' + 'word": "[REDACTED]"', "Author" + "ization: Bearer [REDACTED]",
                 '"Author' + 'ization": "[REDACTED]"', "const h = {Author" + "ization: 'Bearer ' + accessToken};",
                 '{Author' + 'ization:\\"Bearer \\"+t}', "CT_PASS" + "WORD={FAKE}", "pass" + "word=${CT_PW}",
                 "someone" + "@" + "example.com", "a.b" + "@" + "mail.example.org", "x" + "@" + "host.test",
                 "y" + "@" + "nowhere.invalid", "[EMAIL] and [SHARE-LINK]", "plain text, no secrets",
                 "self.qa_token = secrets.token_urlsafe(32)", "{token: readToken(), meta: null}",
                 "git remote add origin git@github.com:wingtra/qa.git"]:
        got = kinds_of(text)
        truth(f"no finding for {text[:30]!r}", got == [], got)


def file_cases():
    big = os.path.join(TMP, "big.bin")
    with open(big, "wb") as f:
        f.truncate(ps.MAX_BYTES + 1)
    data, size = ps.read_disk(big)
    truth("over 5 MiB is not read", data is None and size == ps.MAX_BYTES + 1)
    truth("over 5 MiB found", "file over 5 MiB" in ps.path_kinds("big.bin", size, b""))
    truth("exactly 5 MiB is fine", "file over 5 MiB" not in ps.path_kinds("ok.bin", ps.MAX_BYTES, b""))
    for path, kind in [("qa/secrets.env", "secrets file"), ("x/prod.env", "env file"), (".env", "env file"),
                       (".env.local", "env file"), ("qa/cache/clickup/CL-1.json", "cache folder"),
                       ("runner/state/status.json", "runtime state"), ("qa/runner/state/locks/a.lock", "runtime state"),
                       ("tests/2026-10-21-x/TA5/shot.png", "evidence or clone folder"),
                       ("WingtraCloud/apps/x.ts", "evidence or clone folder"), ("build/app.APK", "APK"),
                       ("logs.tar.gz", "archive"), ("pack.zip", "archive"), ("runner/__pycache__/a.cpython-313.pyc",
                                                                                 "compiled Python"),
                       ("a/b.tmp", "temp file")]:
        got = ps.path_kinds(path, 10, b"")
        truth(f"{kind}: {path}", kind in got, got)
    truth("gzip magic in binary content is an archive", "archive" in ps.path_kinds("blob.bin", 20, b"\x1f\x8b\x08\x00"))
    truth("BZh at the start of text is not an archive", ps.path_kinds("notes.txt", 20, b"BZh is a word") == [])
    for path in ["qa/runner/tests/test_gate.py", "runner/tests/cases/SELFTEST-001.json", "qa/runs/x/R-1.json",
                 "qa/ledger/2026-10-21.jsonl", "plugin/wingtra-qa/hooks/register.tsx", "app/static/app.css"]:
        truth(f"no file rule for {path}", ps.path_kinds(path, 100, b"{}") == [])
    blob = b"\x00\x01PNG" + FAKE_SECRET.encode() + b"\x00" + JWT.encode("utf-16-le") + b"\x00\x00"
    got = ps.scan_blob("img.png", blob, len(blob), needles())
    truth("binary: secret and UTF-16 JWT found, line '-'",
          {(f.line, f.kind) for f in got} == {("-", ps.SECRET_KIND), ("-", "JWT")}, got)
    truth("binary: secret has no fingerprint", all(f.fp is None for f in got if f.kind == ps.SECRET_KIND))
    truth("parse_z splits on NUL", ps.parse_z(b"a b\x00c\n\x00\x00") == ["a b", "c\n"])
    long = ("z" * 2_000_000 + "/ x" + "@" + "y " + EMAIL).encode()
    t0 = time.monotonic()
    got = [f.kind for f in ps.scan_blob("one-line.json", long, len(long), needles())]
    truth("a 2 MB line scans in under 5 s", time.monotonic() - t0 < 5 and got == ["email"], got)


def allow_cases():
    f = ps.scan_blob("qa/runner/x.py", ("ok\n" + SHARE + "\n").encode(), 100, needles())
    fp = f[0].fp
    allow = os.path.join(TMP, "allow")
    with open(allow, "w", encoding="utf-8") as a:
        a.write(f"# fake share link in a fixture\n{fp} share link runner/x.py\n")
    e = ps.load_allow(allow)
    truth("allow entry by path suffix", ps.allowed(f[0], e))
    truth("allow entry by glob", ps.allowed(f[0], [(fp, "share link", "*/x.py")]))
    truth("allow entry: other path", not ps.allowed(f[0], [(fp, "share link", "runner/y.py")]))
    truth("allow entry: other kind", not ps.allowed(f[0], [(fp, "email", "runner/x.py")]))
    truth("same line in another file has the same fingerprint",
          ps.scan_blob("b/z.md", (SHARE + "\n").encode(), 10, needles())[0].fp == fp)
    s = ps.scan_blob("qa/x.txt", FAKE_SECRET.encode(), 10, needles())[0]
    truth("a secrets.env value can never be allowed", s.fp is None and not ps.allowed(s, [(fp, ps.SECRET_KIND, "*")]))
    bad = os.path.join(TMP, "bad_allow")
    with open(bad, "w") as b:
        b.write("not an entry\n")
    try:
        ps.load_allow(bad)
        truth("malformed allow file raises", False)
    except ValueError:
        truth("malformed allow file raises", True)


def cli_cases():
    clean = write("clean/notes.md", "# notes\nnothing here\n")
    code, out, _ = cli("--paths", clean)
    truth("CLI exit 0 on a clean file", code == 0 and "0 findings" in out, out)
    leak = write("leak/run.json", json.dumps({"a": FAKE_SECRET, "b": [JWT, EMAIL, SHARE]}, indent=1) + "\n"
                 + BEARER + "\n" + PASSWORD + "\n")
    write("leak/secrets.env", "X=1\n")
    write("leak/cache/c.json", "{}\n")
    code, out, err = cli("--paths", os.path.join(TMP, "repo", "leak"), "--fingerprints")
    truth("CLI exit 1 on findings", code == 1, out + err)
    for want in [":2: " + ps.SECRET_KIND, ":4: JWT", ":5: email", ":6: share link", ":9: bearer token",
                 ":10: password field", "secrets.env:-: secrets file", "c.json:-: cache folder"]:
        truth(f"CLI reports {want}", want in out, out)
    truth("CLI never prints a planted value", not any(v in out + err for v in PLANTED))
    truth("CLI prints no fingerprint for the secret", all("[fp" not in ln for ln in out.splitlines()
                                                           if ln.endswith(ps.SECRET_KIND)))
    fps = [ln for ln in out.splitlines() if ln.startswith(leak) and ": share link  [fp " in ln]
    allow = os.path.join(TMP, "allow_cli")
    with open(allow, "w") as a:
        a.write(fps[0].rsplit("[fp ", 1)[1].rstrip("]") + " share link leak/run.json\n")
    code, out, _ = cli("--paths", leak, "--allow", allow)
    truth("CLI: allowed finding is left out and counted", ":6: share link" not in out and "1 allowed by" in out, out)
    lst = os.path.join(TMP, "list")
    with open(lst, "wb") as f:
        f.write(clean.encode() + b"\x00" + leak.encode() + b"\x00")
    code, out, _ = cli("--paths-from", lst)
    truth("CLI --paths-from with NUL list", code == 1 and "2 files" in out, out)
    code, out, _ = cli("--paths-from", "-", stdin=clean + "\n")
    truth("CLI --paths-from stdin", code == 0 and "1 files" in out, out)
    code, _, err = cli("--paths", os.path.join(TMP, "nope.txt"))
    truth("CLI exit 2 on a missing path", code == 2 and "no such file" in err, err)
    p = subprocess.run([sys.executable, "-I", SCRIPT, "--secrets-file", os.path.join(TMP, "none.env"), "--paths",
                        clean], capture_output=True, text=True, timeout=60)
    truth("CLI exit 2 when secrets.env cannot be read", p.returncode == 2 and "not checked" in p.stderr, p.stderr)
    p = subprocess.run([sys.executable, "-I", SCRIPT, "--no-secrets-file", "--paths", clean], capture_output=True,
                       text=True, timeout=60)
    truth("CLI --no-secrets-file runs", p.returncode == 0, p.stdout + p.stderr)
    outside = tempfile.mkdtemp(prefix="precommit_scan_nogit_")
    try:
        p = subprocess.run([sys.executable, "-I", SCRIPT, "--secrets-file", SECRETS], cwd=outside,
                           capture_output=True, text=True, timeout=60,
                           env=dict(os.environ, GIT_CEILING_DIRECTORIES=os.path.dirname(outside)))
        truth("CLI staged mode outside a git work tree exits 2", p.returncode == 2 and "git" in p.stderr, p.stderr)
    finally:
        shutil.rmtree(outside, ignore_errors=True)
    code, out, _ = cli("--paths", SCRIPT, __file__)
    truth("the scanner and this test scan clean", code == 0, out)


class FakeProc:
    def __init__(self, data, rc=0):
        self.stdout, self.rc, self.killed = io.BytesIO(data), rc, False

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return self.rc


def staged_cases():
    index = {"runner/a.py": b"print('ok')\n", "runs/x/R-1.json": ("{\n \"v\": \"" + FAKE_SECRET + "\"\n}\n").encode(),
             "secrets.env": b"X=1\n", "notes/big.log": b"z" * (ps.MAX_BYTES + 10)}
    procs = []

    def fake_popen(argv, **kw):
        procs.append(FakeProc(index[argv[2][1:]]))
        return procs[-1]

    class Top:
        returncode, stdout = 0, b"/fake/repo\n"

    real = ps.git, ps.staged_paths, ps.subprocess.Popen, ps.redact.SECRETS_FILE
    ps.git = lambda args, cwd, **kw: Top
    ps.staged_paths = lambda top: list(index)
    ps.subprocess.Popen = fake_popen
    ps.redact.SECRETS_FILE = SECRETS
    buf, real_out = io.StringIO(), sys.stdout
    sys.stdout = buf
    try:
        code = ps.main([])
    finally:
        sys.stdout = real_out
        ps.git, ps.staged_paths, ps.subprocess.Popen, ps.redact.SECRETS_FILE = real
    out = buf.getvalue()
    truth("staged: exit 1", code == 1, out)
    truth("staged: secret in a staged record", "runs/x/R-1.json:2: " + ps.SECRET_KIND in out, out)
    truth("staged: secrets.env staged", "secrets.env:-: secrets file" in out, out)
    truth("staged: big blob stopped at the limit", "notes/big.log:-: file over 5 MiB" in out and procs[3].killed, out)
    truth("staged: clean file has no finding", "runner/a.py" not in out, out)
    truth("staged: 4 files", "4 files" in out, out)
    truth("staged: value not printed", FAKE_SECRET not in out)


def main(argv):
    try:
        content_cases()
        file_cases()
        allow_cases()
        cli_cases()
        staged_cases()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    fails = 0
    for name, ok, detail in results:
        if not ok:
            fails += 1
        if not ok or "-v" in argv:
            print(f"{'OK  ' if ok else 'FAIL'} {name}" + ("" if ok else f": {str(detail)[:300]}"))
    print(f"precommit_scan cases: {len(results) - fails}/{len(results)} pass")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
