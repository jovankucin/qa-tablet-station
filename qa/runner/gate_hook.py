"""PreToolUse hook entry: reads the hook input JSON on stdin, answers with the gate's decision.

Default output is the classic hook form (hookSpecificOutput.permissionDecision).
With --mod it prints {"decision", "reason"} for the plugin module, which maps it to
{deny} / {ask} / next(). A crash in the gate asks for device, act.py and ClickUp calls (RISKY)
and passes everything else, so a broken gate never silently allows a device or ClickUp write.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# The fallback when the gate itself fails (once the same regex as RISKY in qa/plugin/wingtra-qa/hooks/register.tsx);
# 2026-11-03: the approval key and approvals folder too, so a gate crash never passes a read of the key; and (security
# review, same day) the Touch ID prompt's files and the File ticket route; and the Post comment route and the app's
# ClickUp comment poster (same day); and ClickUp's REST API (review, same day).
RISKY = re.compile(r'\badb\b|act\.py|--approved|\bW044\b|\bscp\b|mcp__[^"]*clickup|clickup_upload\.py|webpc044|(?i:secrets\.env)|qa/ledger|'
                   r'approval\.key|approvals|runner/state|touchid|tickets/file|comment/post|clickup_post|(?i:api\.clickup\.com)')


def main(argv):
    raw = sys.stdin.read()
    try:
        e = json.loads(raw or "{}")
        name, inp = e.get("tool_name") or e.get("tool") or "", e.get("tool_input") or e.get("input") or {}
        from gate import decide
        decision, reason = decide(name, inp, cwd=e.get("cwd"))
    except Exception as exc:  # noqa: BLE001
        risky = bool(RISKY.search(raw))
        decision, reason = ("ask", f"gate error ({type(exc).__name__}); asking to be safe") if risky else ("pass", "")
    if "--mod" in argv:
        print(json.dumps({"decision": decision, "reason": reason}))
        return 0
    if decision == "pass":
        return 0
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                             "permissionDecision": decision,
                                             "permissionDecisionReason": "QA gate: " + reason}}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
