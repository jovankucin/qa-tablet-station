"""Ledger hook: one ledger line per relevant tool call of a Claude session (PostToolUse, PostToolUseFailure,
PermissionDenied). Reads the classic hook input on stdin; prints nothing; never blocks.

Relevant: Bash that touches a target or the records (adb, ssh/scp, act.py, run.py, qa/, tests/), ClickUp
tools, and Write/Edit under qa/ or tests/. Every PermissionDenied is relevant. The command is redacted and
act.py --text values are dropped; the tool output is kept only as its sha256.

Only sessions whose cwd (realpath) is the workspace or inside it write: an eval sandbox or any other
folder the plugin is loaded in never reaches the real ledger. A hook input without cwd writes nothing.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from records import WORKSPACE, append, sha256_bytes  # noqa: E402
from redact import redact  # noqa: E402

BASH_RELEVANT = re.compile(r"\badb\b|\bssh\b|\bscp\b|act\.py|run\.py|qa/|tests/")
TEXT_ARG = re.compile(r"(--text\s+)(\"(?:[^\"\\]|\\.)*\"|'[^']*'|\S+)")


def summary(tool, inp):
    if tool == "Bash":
        return [redact(TEXT_ARG.sub(r"\1<text>", inp.get("command", "")))[:600]]
    if tool in ("Write", "Edit", "Read", "NotebookEdit"):
        return [os.path.relpath(inp.get("file_path", ""), WORKSPACE)]
    keys = ("task_id", "list_id", "operator", "name")
    return [f"{k}={inp[k]}" for k in keys if isinstance(inp, dict) and k in inp][:4]


def in_workspace(cwd):
    if not isinstance(cwd, str) or not cwd:
        return False
    ws, here = os.path.realpath(WORKSPACE), os.path.realpath(cwd)
    return here == ws or here.startswith(ws + os.sep)


def relevant(event, tool, inp):
    if event == "PermissionDenied":
        return True
    if tool == "Bash":
        return bool(BASH_RELEVANT.search(inp.get("command", "")))
    if "clickup" in tool:
        return True
    if tool in ("Write", "Edit"):
        p = os.path.relpath(inp.get("file_path", ""), WORKSPACE)
        return p.startswith(("qa/", "tests/"))
    return False


def main():
    try:
        e = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0
    if not isinstance(e, dict) or not in_workspace(e.get("cwd")):
        return 0
    event, tool = e.get("hook_event_name"), e.get("tool_name", "")
    inp = e.get("tool_input") if isinstance(e.get("tool_input"), dict) else {}
    if not relevant(event, tool, inp):
        return 0
    if event == "PostToolUse":
        rc, out = 0, json.dumps(e.get("tool_response"), sort_keys=True, default=str)
    elif event == "PostToolUseFailure":
        rc, out = 1, e.get("error") or ""
    else:
        rc, out = "denied", e.get("reason") or ""
    entry = {"src": "hook", "session": (e.get("session_id") or "-")[:8], "target": "-", "tool": tool,
             "argv": summary(tool, inp), "exit": rc, "out_sha256": sha256_bytes(out.encode()), "ref": None,
             "event": event, "tool_use_id": e.get("tool_use_id")}
    if event != "PostToolUse":
        entry["note"] = redact(out)[:300]
    append(entry)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 (a ledger hook never breaks the session)
        sys.exit(0)
