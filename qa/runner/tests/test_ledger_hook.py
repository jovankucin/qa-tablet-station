"""ledger_hook.py's filter, redaction and workspace check, without writing the ledger.

python3 -I qa/runner/tests/test_ledger_hook.py

main() runs with append replaced by a recorder: a session whose cwd is outside the workspace (an eval
sandbox, a sibling folder, a symlink out) or has no cwd writes nothing.
"""
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import ledger_hook as lh  # noqa: E402
from records import WORKSPACE  # noqa: E402

CASES = [
    ("PostToolUse", "Bash", {"command": "adb -s TA5SERIAL01 shell dumpsys battery"}, True),
    ("PostToolUse", "Bash", {"command": "python3 -I qa/runner/act.py ET65 tap text:Sites --expect path:/sites"}, True),
    ("PostToolUse", "Bash", {"command": "ls -la"}, False),
    ("PostToolUse", "Bash", {"command": "git -C WingtraCloud log --oneline -3"}, False),
    ("PostToolUse", "Read", {"file_path": os.path.join(WORKSPACE, "qa/cases/UPG-002.json")}, False),
    ("PostToolUse", "Write", {"file_path": os.path.join(WORKSPACE, "qa/runs/x/PR-R-1.json"), "content": "{}"}, True),
    ("PostToolUse", "Edit", {"file_path": os.path.join(WORKSPACE, "WingtraCloud/README.md")}, False),
    ("PostToolUse", "mcp__x__clickup_get_task", {"task_id": "0000fake002"}, True),
    ("PostToolUseFailure", "Bash", {"command": "ssh webpc044 true"}, True),
    ("PermissionDenied", "Glob", {"pattern": "*"}, True),
]


WS_CHECKS = 0


def run_main(e):
    """lh.main() on one hook input; returns the entries it would have appended."""
    wrote = []
    real_append, real_stdin = lh.append, sys.stdin
    lh.append = lambda entry, day=None: wrote.append(entry) or entry
    sys.stdin = io.StringIO(json.dumps(e))
    try:
        lh.main()
    finally:
        lh.append, sys.stdin = real_append, real_stdin
    return wrote


def workspace_checks():
    global WS_CHECKS
    fails = 0
    bash = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": "evalsess1",
            "tool_input": {"command": "ls qa/runner/draft_check.py"}, "tool_response": {"stdout": "x"}}
    denied = {"hook_event_name": "PermissionDenied", "tool_name": "Bash", "session_id": "evalsess1",
              "tool_input": {"command": "adb -s X shell true"}, "reason": "ask in headless run"}
    with tempfile.TemporaryDirectory() as tmp:
        link_in = os.path.join(tmp, "ws-link")
        os.symlink(WORKSPACE, link_in)
        sandbox = os.path.join(tmp, "eval-sandbox")
        os.mkdir(sandbox)
        link_out = os.path.join(sandbox, "out-link")
        os.symlink(tmp, link_out)
        cwds = [
            (WORKSPACE, True),
            (os.path.join(WORKSPACE, "qa", "plugin", "wingtra-qa", "evals"), True),
            (link_in, True),                                   # a symlink to the workspace: realpath is inside
            (os.path.join(link_in, "tests"), True),
            (sandbox, False),                                  # an eval sandbox
            (link_out, False),
            (WORKSPACE + "2", False),                          # a sibling that shares the prefix
            (os.path.dirname(WORKSPACE), False),
            (os.path.join(WORKSPACE, "qa", "..", "..", "elsewhere"), False),
            ("", False),
            (None, False),
        ]
        for cwd, want in cwds:
            got = lh.in_workspace(cwd)
            WS_CHECKS += 1
            if got != want:
                fails += 1
                print(f"FAIL in_workspace({cwd!r}) -> {got}, expected {want}")
            for e in (bash, denied):
                ev = dict(e, cwd=cwd) if cwd is not None else dict(e)
                wrote = run_main(ev)
                WS_CHECKS += 1
                if bool(wrote) != want:
                    fails += 1
                    print(f"FAIL main {e['hook_event_name']} with cwd {cwd!r}: wrote {len(wrote)}, expected {'1' if want else 'none'}")
    return fails


def main():
    fails = 0
    for ev, tool, inp, want in CASES:
        if lh.relevant(ev, tool, inp) != want:
            fails += 1
            print(f"FAIL relevant {ev} {tool} {inp} -> {not want}")
    s = lh.summary("Bash", {"command": 'python3 -I qa/runner/act.py ET65 type css:input --text "my site name" --expect x'})[0]
    if "my site name" in s or "--text <text>" not in s:
        fails += 1
        print(f"FAIL --text not dropped: {s}")
    s = lh.summary("Bash", {"command": "curl -H 'Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc' x"})[0]
    if "eyJhbGci" in s:
        fails += 1
        print(f"FAIL token not redacted: {s}")
    fails += workspace_checks()
    print(f"{len(CASES) + 2 + WS_CHECKS} ledger-hook checks, {fails} failures")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
