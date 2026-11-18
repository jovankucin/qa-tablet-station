"""Run every gate fixture through the real hook entry (gate_hook.py, a subprocess) and compare.

python3 -I qa/runner/tests/test_gate.py [-v]
Exit 0 when every fixture gets its expected decision.
"""
import datetime
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.dirname(HERE)
HOOK = os.path.join(RUNNER, "gate_hook.py")
sys.path.insert(0, RUNNER)
import gate  # noqa: E402

# gate_fixtures.jsonl is generated from transcripts (make_gate_fixtures.py); the G1 and G2 rows are written by hand.
# F6 (2026-11-02): the -s rule counts only adb that runs; hand rows, the 12 recorded -s denials and critic J's denial.
# Review (2026-11-02): runs the first F6 version let through (pipes into a shell, bash -c --, arch, aliased Python run
# functions, ...) and reads that must stay text.
# 2026-11-03: act.py wifi (offline sites) passes, plain and under the runner; raw adb Wi-Fi changes keep asking.
# 2026-11-03: act.py map-tap (drawing in the planner) passes like a tap; its shell expansions and approval flag do not.
# 2026-11-03: Post comment (api/results/comment/post) and the app's comment poster (clickup_post) are denied to every
# shell line that is not a plain reader and to every connector or browser tool; plain reads pass.
# 2026-11-04: the review's two older gaps: no line names the password file (a runner mention no longer exempts it; globs,
# run-time names and $'...' spellings in a folder named qa, bare ones where the hook's cwd or a cd puts the line in qa);
# a direct ClickUp API call that is not a plain read asks, from the shell or a connector (a row's "cwd" goes to the hook).
# review2: the round-2 review's confirmed findings (four lenses, each checked by a skeptic) that the gate now handles: a
# bypass expects its ask or deny, a false positive expects pass.
# 2026-11-16 (tablet_steps): fewer prompts in tablet testing, the person's OK: a cloud read through the app with
# CapacitorHttp.get at the engine's API base passes, and so does adb push of test files into a QA folder on the tablet;
# the shapes around both keep asking.
# 2026-11-16 (gate_holes): the three older holes, the person's OK ("Yes, fix them"): adb global options and quoted adb
# words, tablet writes (tee, exec-in, quoted or /system/bin commands, a shell fed from this Mac) and JS the gate misread
# (comments, quotes in regexes, escapes); normal tablet commands keep passing.
FIXTURES = ("gate_fixtures_g1.jsonl", "gate_fixtures_g2.jsonl", "gate_fixtures_f6.jsonl",
            "gate_fixtures_review.jsonl", "gate_fixtures_wifi.jsonl", "gate_fixtures_maptap.jsonl",
            "gate_fixtures_comments.jsonl", "gate_fixtures_gaps.jsonl",
            "gate_fixtures_review2.jsonl", "gate_fixtures_tablet_steps.jsonl", "gate_fixtures_gate_holes.jsonl")
# 2026-11-03: filing a ticket the person approved in the app. Each row runs in-process against a temporary QA dir (its
# "setup" says which approval to write there) and a fixed clock, then through the real hook, which sees the real QA dir
# (no approval there, so a "pass" row must ask through it).
TICKET_FIXTURES = "gate_fixtures_tickets.jsonl"
NOW = datetime.datetime(2026, 11, 3, 10, 0, 0, tzinfo=datetime.timezone.utc)
TICKET = "tests/2026-11-03-demo/tickets/1"
TITLE = b"[CT] Flight planning: terrain import fails (offline)\n"
DESC = b"Issue Summary\nx\nSteps to Reproduce\ny\nExpected Behavior\nz\nActual Behavior\nw\n"


def _hook(fx):
    p = subprocess.run([sys.executable, "-I", HOOK, "--mod"], input=json.dumps(fx), capture_output=True, text=True, timeout=20)
    got = json.loads(p.stdout)["decision"] if p.stdout.strip() else "error"
    # the classic form must agree with the mod form
    q = subprocess.run([sys.executable, "-I", HOOK], input=json.dumps(fx), capture_output=True, text=True, timeout=20)
    classic = json.loads(q.stdout)["hookSpecificOutput"]["permissionDecision"] if q.stdout.strip() else "pass"
    return got, classic


def _ts(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _approval(adir, key, aid, s, **over):
    """Write one approval file as the app server would (sig over the other fields), then the setup's tampering."""
    created = NOW - datetime.timedelta(minutes=s.get("age_min", 1))
    a = {"id": aid, "ticket_dir": TICKET, "draft_sha256": hashlib.sha256(TITLE + b"\n" + DESC).hexdigest(),
         "target": "TA5", "created": _ts(created), "expires": _ts(created + datetime.timedelta(minutes=s.get("ttl_min", 30))),
         "by": "touch_id"}
    a.update(s.get("fields", {}), **over)
    if s.get("extra_field"):
        a["note"] = "x"
    sign_key = os.urandom(32) if s.get("other_key") else (key.hex().encode() if s.get("key_as_text") else key)
    msg = json.dumps(a, sort_keys=True, separators=(",", ":")).encode("utf-8")
    a["sig"] = hmac.new(sign_key, msg, hashlib.sha256).hexdigest()
    if s.get("bad_sig"):
        a["sig"] = a["sig"][:-1] + ("0" if a["sig"][-1] != "0" else "1")
    a.update(s.get("tamper_after_sign", {}))
    name = (aid[:-6] + "fedcba" if s.get("id_mismatch") else aid) + ".json"
    path = os.path.join(adir, name)
    if s.get("approval_symlink"):
        real = os.path.join(os.path.dirname(os.path.dirname(adir)), "elsewhere.json")
        with open(real, "w", encoding="utf-8") as f:
            json.dump(a, f)
        os.symlink(real, path)
    else:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(a, f)
    return path


def _setup(tmp, s):
    """A QA dir under tmp (tickets under tmp/tests) holding what the row's setup names; None setup: nothing at all."""
    qa = os.path.join(tmp, "qa")
    state = os.path.join(qa, "runner", "state")
    os.makedirs(state)
    if s is None:
        return qa, None
    adir = os.path.join(state, "approvals")
    if s.get("approvals_symlink"):
        os.makedirs(os.path.join(tmp, "fake_approvals"))
        os.symlink(os.path.join(tmp, "fake_approvals"), adir)
    else:
        os.makedirs(adir)
    key = os.urandom(32)
    if s.get("key", True):
        with open(os.path.join(state, "approval.key"), "w", encoding="ascii") as f:
            f.write((key[:16] if s.get("key_short") else key).hex() + "\n")
    tdir = os.path.join(tmp, TICKET)
    os.makedirs(tdir)
    if not s.get("no_draft"):
        for name, body in (("title.txt", TITLE), ("description.txt", DESC)):
            with open(os.path.join(tdir, name), "wb") as f:
                f.write(body)
    for n, kind in enumerate(s.get("other_approvals", [])):  # ids that sort first: the scan must go past them
        _approval(adir, key, f"A-20261103090000-00000{n}", dict(s, age_min=40) if kind == "expired" else s,
                  **({"target": "ET65"} if kind == "wrong_target" else {}))
    aid = "A-20261103095900-abc123"
    if s.get("approval", True):
        _approval(adir, key, aid, s)
    if s.get("used"):
        open(os.path.join(adir, aid + ".used"), "w").close()
    if s.get("draft_changed"):
        with open(os.path.join(tdir, "description.txt"), "ab") as f:
            f.write(b"one more line\n")
    if s.get("filed"):  # security review 2026-11-03: the ticket was filed already (submitted.json or filed.json)
        with open(os.path.join(tdir, s["filed"]), "w", encoding="utf-8") as f:
            json.dump({"cl_id": "CL-1"}, f)
    return qa, os.path.join(adir, aid + ".used")


def _ticket_row(fx):
    """(ok, detail) for one ticket fixture: in-process with the row's approval, then through the real hook."""
    tmp = tempfile.mkdtemp(prefix="gate_tickets_")
    saved = gate.QA_DIR, gate._now
    try:
        qa, used = _setup(tmp, fx.get("setup"))
        gate.QA_DIR, gate._now = qa, (lambda: NOW)
        seq = fx.get("expect_seq", [fx["expect"]])
        got = [gate.decide(fx["tool_name"], fx["tool_input"])[0] for _ in seq]
        ok = got == seq
        if "expect_used" in fx:
            ok = ok and os.path.exists(used) == fx["expect_used"]
            got.append(f"used={os.path.exists(used)}")
    finally:
        gate.QA_DIR, gate._now = saved
        shutil.rmtree(tmp, ignore_errors=True)
    want = "ask" if fx["expect"] == "pass" and "setup" in fx else fx["expect"]
    hook, classic = _hook(fx)
    ok = ok and hook == want and classic == hook
    return ok, f"in-process={','.join(map(str, got))} hook={hook} classic={classic}"


def main(argv):
    fails = 0
    n = 0
    lines = []
    for name in FIXTURES:
        lines += open(os.path.join(HERE, name), encoding="utf-8").read().splitlines()
    for line in lines:
        fx = json.loads(line)
        got, classic = _hook(fx)
        n += 1
        ok = got == fx["expect"] and classic == got
        if not ok:
            fails += 1
        if not ok or "-v" in argv:
            cmd = json.dumps(fx["tool_input"])[:150]
            print(f"{'OK  ' if ok else 'FAIL'} expect={fx['expect']:4} got={got:4} classic={classic:4} [{fx['category']}] {fx['tool_name'].split('__')[-1]} {cmd}  ({fx['source']})")
    t_fails = t_n = 0
    for line in open(os.path.join(HERE, TICKET_FIXTURES), encoding="utf-8").read().splitlines():
        fx = json.loads(line)
        ok, detail = _ticket_row(fx)
        t_n += 1
        if not ok:
            t_fails += 1
        if not ok or "-v" in argv:
            cmd = json.dumps(fx["tool_input"])[:150]
            print(f"{'OK  ' if ok else 'FAIL'} expect={fx['expect']:4} {detail} [{fx['category']}] {fx['tool_name']} {cmd}  setup={json.dumps(fx.get('setup'))}")
    # approvals.py writes "YYYY-MM-DDTHH:MM:SSZ"; the gate reads it on this Python and on the system one (3.9 before
    # 3.11 refuses a bare Z in fromisoformat), so a hook run by another interpreter never drops every approval
    z_fails, z_n = 0, 1
    want = datetime.datetime(2026, 11, 3, 10, 30, tzinfo=datetime.timezone.utc)
    if gate._iso("2026-11-03T10:30:00Z") != want or gate._iso("2026-11-03T10:30:00+00:00") != want:
        z_fails += 1
        print("FAIL gate._iso does not read approvals.py's Z time")
    if os.access("/usr/bin/python3", os.X_OK):
        z_n += 1
        p = subprocess.run(["/usr/bin/python3", "-I", "-c", "import sys; sys.path.insert(0, sys.argv[1]); import gate; "
                            "print(gate._iso('2026-11-03T10:30:00Z').isoformat())", RUNNER],
                           capture_output=True, text=True, timeout=60)
        if p.stdout.strip() != want.isoformat():
            z_fails += 1
            print(f"FAIL /usr/bin/python3: gate._iso of a Z time gave {p.stdout.strip() or p.stderr.strip()[-200:]}")
    print(f"gate fixtures: {n - fails}/{n} pass")
    print(f"ticket fixtures: {t_n - t_fails}/{t_n} pass")
    print(f"approval time format: {z_n - z_fails}/{z_n} pass")
    return 1 if fails or t_fails or z_fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
