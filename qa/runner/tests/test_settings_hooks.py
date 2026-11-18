"""The gate and ledger hooks as wired in the workspace settings (2026-10-22): the exact command lines run through
/bin/sh with a hook input on stdin, as Claude Code runs them. No device, no ClickUp, no write to the real ledger.

python3 -I qa/runner/tests/test_settings_hooks.py [settings.json]   (default: <workspace>/.claude/settings.json)
"""
import glob
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.dirname(HERE)
WORKSPACE = os.path.dirname(os.path.dirname(RUNNER))
EVENTS = {"PreToolUse": "gate_hook.py", "PostToolUse": "ledger_hook.py", "PostToolUseFailure": "ledger_hook.py",
          "PermissionDenied": "ledger_hook.py"}
FAILS = []
N = [0]


def check(cond, what):
    N[0] += 1
    if not cond:
        FAILS.append(what)
        print("FAIL", what)


def commands(settings, event):
    return [h.get("command", "") for m in settings.get("hooks", {}).get(event, []) for h in m.get("hooks", [])
            if h.get("type") == "command"]


def run(cmd, payload, cwd=WORKSPACE):
    p = subprocess.run(["/bin/sh", "-c", cmd], input=json.dumps(payload), capture_output=True, text=True,
                       timeout=60, cwd=cwd)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def hook_input(event, command, cwd=WORKSPACE, **extra):
    d = {"session_id": "testsess", "transcript_path": "/dev/null", "cwd": cwd, "hook_event_name": event,
         "tool_name": "Bash", "tool_input": {"command": command}, "tool_use_id": "toolu_test_1"}
    d.update(extra)
    return d


def ledger_state(folder):
    return {f: (os.path.getsize(f), os.path.getmtime(f)) for f in glob.glob(os.path.join(folder, "*"))}


def main(argv):
    path = argv[0] if argv else os.path.join(WORKSPACE, ".claude", "settings.json")
    settings = json.load(open(path, encoding="utf-8"))

    # every event is wired, for every tool, to an existing script
    for event, script in EVENTS.items():
        cmds = commands(settings, event)
        check(len(cmds) == 1, f"{event}: exactly one command hook (got {len(cmds)})")
        matchers = [m.get("matcher") for m in settings.get("hooks", {}).get(event, [])]
        check(all(m in ("*", "", None) for m in matchers), f"{event}: matches every tool ({matchers})")
        for c in cmds:
            scripts = re.findall(r'"([^"]+\.py)"', c)
            check(any(s.endswith("/qa/runner/" + script) for s in scripts), f"{event}: runs qa/runner/{script}")
            check(all(os.path.isabs(s) and os.path.isfile(s) for s in scripts), f"{event}: script paths exist: {scripts}")
            exe = shlex.split(c)[0]  # the interpreter, quoted or not
            check(os.path.isabs(exe) and os.access(exe, os.X_OK), f"{event}: interpreter {exe} exists")
    gate = commands(settings, "PreToolUse")[0]
    ledger = commands(settings, "PostToolUse")[0]

    # the gate, live command line: a git write is denied, a git read passes
    rc, out, err = run(gate, hook_input("PreToolUse", "git -C WingtraCloud stash list"))
    d = json.loads(out or "{}").get("hookSpecificOutput", {})
    check(rc == 0 and d.get("permissionDecision") == "deny" and "(G1)" in d.get("permissionDecisionReason", ""),
          f"stash list denied with the G1 reason (rc {rc}, out {out[:200]}, err {err[:200]})")
    rc, out, err = run(gate, hook_input("PreToolUse", "git -C WingtraCloud log -1"))
    check(rc == 0 and out == "", f"git log -1 passes with no output (rc {rc}, out {out[:200]}, err {err[:200]})")
    rc, out, _ = run(gate, hook_input("PreToolUse", "adb kill-server"))
    check(json.loads(out or "{}").get("hookSpecificOutput", {}).get("permissionDecision") == "deny", "adb kill-server denied")
    rc, out, _ = run(gate, {**hook_input("PreToolUse", ""), "tool_name": "Write",
                            "tool_input": {"file_path": os.path.join(WORKSPACE, "WingtraCloud", "x.ts"), "content": "x"}})
    check(json.loads(out or "{}").get("hookSpecificOutput", {}).get("permissionDecision") == "deny", "Write in a clone denied")

    # fail closed: when the interpreter is missing, the command line asks instead of passing
    first = shlex.split(gate)[0]
    exe = '"' + first + '"' if gate.startswith('"') else first
    rc, out, _ = run(gate.replace(exe, "/nonexistent/python3", 1), hook_input("PreToolUse", "ls"))
    d = json.loads(out or "{}").get("hookSpecificOutput", {})
    check(rc == 0 and d.get("permissionDecision") == "ask", f"a gate that cannot start asks (rc {rc}, out {out[:200]})")

    # the ledger, on a scratch copy of the runner: one src "hook" line per relevant event, the real ledger untouched
    real = ledger_state(os.path.join(WORKSPACE, "qa", "ledger"))
    with tempfile.TemporaryDirectory() as tmp:
        ws = os.path.join(tmp, "ws copy")
        os.makedirs(os.path.join(ws, "qa", "ledger"))
        shutil.copytree(RUNNER, os.path.join(ws, "qa", "runner"),
                        ignore=shutil.ignore_patterns("tests", "state", "__pycache__", "backtest", "*.json"))
        scratch_cmd = ledger.replace(WORKSPACE, ws)
        check(scratch_cmd != ledger, "the ledger command names the workspace path, so it can be pointed at a copy")
        for event, extra in (("PostToolUse", {"tool_response": {"stdout": "ok"}}),
                             ("PostToolUseFailure", {"error": "exit 1"}),
                             ("PermissionDenied", {"reason": "denied by the gate"})):
            rc, out, err = run(commands(settings, event)[0].replace(WORKSPACE, ws),
                               hook_input(event, "adb -s TA5SERIAL01 shell getprop", cwd=ws, **extra), cwd=ws)
            check(rc == 0, f"{event}: ledger hook exits 0 (err {err[:200]})")
        lines = [json.loads(x) for f in glob.glob(os.path.join(ws, "qa", "ledger", "*.jsonl"))
                 for x in open(f, encoding="utf-8").read().splitlines() if x.strip()]
        check([x.get("event") for x in lines] == ["PostToolUse", "PostToolUseFailure", "PermissionDenied"],
              f"three hook lines in the scratch ledger (got {[x.get('event') for x in lines]})")
        check(all(x.get("src") == "hook" and x.get("tool") == "Bash" for x in lines), "lines are src hook, tool Bash")
        # a session outside the workspace writes nothing
        rc, _, _ = run(scratch_cmd, hook_input("PostToolUse", "adb devices", cwd=tmp, tool_response={}), cwd=tmp)
        after = sum(1 for f in glob.glob(os.path.join(ws, "qa", "ledger", "*.jsonl")) for _ in open(f))
        check(after == 3, "a hook input whose cwd is outside the workspace writes no line")
    check(ledger_state(os.path.join(WORKSPACE, "qa", "ledger")) == real, "the real ledger is unchanged")

    print(f"settings hooks: {N[0] - len(FAILS)}/{N[0]} pass ({path})")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
