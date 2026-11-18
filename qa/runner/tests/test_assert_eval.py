"""run.py evaluates a scripted assert only when it is a plain check (review 2026-11-03).

python3 -I qa/runner/tests/test_assert_eval.py

eval with an empty __builtins__ is no sandbox: ().__class__.__base__.__subclasses__() or a generator's
gi_frame.f_back.f_globals reach run.py's own globals (os, subprocess, act). A stub run (test_progress's stubs, no
device) shows such an assert is a harness ERROR and never evaluated, a plain one still decides PASS or FAIL, and every
scripted assert in this copy's cases is a plain check.
"""
import glob
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import run  # noqa: E402
import test_progress as tp  # noqa: E402

FAILS, N = [], [0]
REACHED = []  # an escaping assert that ran would append here through run.py's globals


def check(cond, what):
    N[0] += 1
    if not cond:
        FAILS.append(what)
        print("FAIL", what)


ESCAPES = [
    "[c for c in ().__class__.__base__.__subclasses__()] and True",
    # no dunder at all: the generator's running frame, then eval's frame, then run.py's do_step frame and its globals
    "set([(gen := (gen.gi_frame.f_back.f_back.f_globals['REACHED_HOOK']() for _ in [0])) and None] + [y for y in gen])",
    "any(x for x in [v.get('answer')])",
    "(lambda: True)()",
    "f'{v}' == ''",
    "__import__('os')",
    "v['answer'] ** 99 == 0",
]
PLAIN = ["v['answer'] == 42", "any(o == 42 for o in [v['answer']]) and len(v) == 1",
         "sorted(set([v['answer'], 1])) == [1, 42] and build['env'] == 'staging' and target == 'QA9'",
         "v['answer'] + 1 >= 43 if run else False"]


def one_run(expr):
    """The stub run's verdict and the error of its deciding step with this assert on step 3."""
    records = []
    tp.stub_module(records)
    with tempfile.TemporaryDirectory() as tmp:
        run.PROGRESS = os.path.join(tmp, "progress")
        r, dev = tp.make_run(tmp)
        r.case["steps"][2]["assert"]["expr"] = expr
        run.act = dev
        run.REACHED_HOOK = lambda: REACHED.append(expr)  # what the second escape would call
        try:
            r.run()
        finally:
            del run.REACHED_HOOK
        rec = records[-1] if records else {}
        steps = (rec.get("attempts") or [{}])[0].get("steps") or []
        last = steps[-1] if steps else {}
        return (rec.get("verdict") or {}).get("v"), (last.get("assert") or {}).get("error"), r.error


def main():
    for expr in ESCAPES:
        check(run.assert_problem(expr) is not None, f"not a plain check: {expr}")
        v, err, crash = one_run(expr)
        check(crash is None and v == "ERROR" and err and "not a plain check" in err,
              f"the run refuses the assert before eval: {expr} -> {v}, {err}, {crash}")
    check(REACHED == [], f"no escaping assert ran: {REACHED}")
    for expr, want in [(p, "PASS") for p in PLAIN] + [("v['answer'] == 41", "FAIL")]:
        check(run.assert_problem(expr) is None, f"a plain check: {expr}")
        v, err, crash = one_run(expr)
        check(crash is None and v == want and not err, f"a plain assert decides: {expr} -> {v}, {err}, {crash}")
    qa = os.path.dirname(os.path.dirname(HERE))
    seen = 0
    for d in (os.path.join(qa, "cases"), os.path.join(HERE, "cases")):
        for f in sorted(glob.glob(os.path.join(d, "*.json"))):
            with open(f, encoding="utf-8") as fh:
                case = json.load(fh)
            for s in case.get("steps") or []:
                a = s.get("assert") if isinstance(s, dict) else None
                if isinstance(a, dict) and a.get("kind") == "scripted":
                    seen += 1
                    check(run.assert_problem(a.get("expr")) is None, f"{os.path.basename(f)} step {s.get('n')}: "
                                                                      f"{run.assert_problem(a.get('expr'))}")
    check(seen > 0, "this copy's cases hold scripted asserts")
    print(f"assert eval: {N[0]} checks ({seen} case asserts), {len(FAILS)} failures")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
