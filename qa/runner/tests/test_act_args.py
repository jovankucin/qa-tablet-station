"""act.py's options are exact: no abbreviation of --approved gets past the gate (G2, 2026-10-23). No device calls.

python3 -I qa/runner/tests/test_act_args.py
"""
import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import act  # noqa: E402

BASE = ["TA5", "tap", "text:Save site", "--expect", "path:/sites"]


def parses(extra):
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            return act.parser().parse_args(BASE + extra)
    except SystemExit:
        return None


def main():
    fails = 0
    a = parses(["--approved"])
    if a is None or not a.approved:
        print("FAIL --approved was not read as the flag")
        fails += 1
    for flag in ("--a", "--ap", "--app", "--appro", "--approve"):
        if parses([flag]) is not None:
            print(f"FAIL {flag} was accepted")
            fails += 1
    b = parses([])
    if b is None or b.approved:
        print("FAIL a call without the flag was read as approved")
        fails += 1
    print(f"act.py options: {fails} failures")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
