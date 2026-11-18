"""Task model shared by the QA app and /qa-run (qa/app/DESIGN.md section 5).

A task is mutable workflow state in qa/tasks/T-<yyyymmdd>-<nnn>.json, not a hashed record. Every write goes
through writes.Writer; this module only reads and holds the state table.

python3 -I qa/app/tasks.py [--root <workspace>] list [--state S]
python3 -I qa/app/tasks.py next                       oldest queued task by priority; exit 3 when none
python3 -I qa/app/tasks.py show <id>                  the task, plus "current": each run's newest record and verdict
python3 -I qa/app/tasks.py set <id> <state> --by claude [--note ...] [--user-words "<the user's words>"]
python3 -I qa/app/tasks.py add-run <id> <RUN>         while running or results_ready (before the greenlight)
python3 -I qa/app/tasks.py set-campaign <id> <slug>   while queued or running
python3 -I qa/app/tasks.py add-output <id> summary|tickets|comments|explain <rel>
                                                      explain (the worker's plain words per case for the Results
                                                      cards, {"cases": {"<CASE>": {tested, happened, why, not_tested}}})
                                                      while running or later; the others after the greenlight

Prints JSON. A refused write prints {"error", "status"} and exits 1.
"""
import argparse
import json
import os
import re
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)
sys.path.insert(0, os.path.join(os.path.dirname(APP_DIR), "runner"))

DEFAULT_ROOT = os.path.dirname(os.path.dirname(APP_DIR))
TASKS_REL = "qa/tasks"
TASK_ID_RE = re.compile(r"^T-(\d{8})-(\d{3})\Z")

STATES = ("queued", "running", "results_ready", "greenlit", "drafted", "closed")
PRIORITY = {"high": 0, "normal": 1, "low": 2}
ACTORS = ("user", "claude")
VIA = ("app", "qa-run")
OUTPUT_KINDS = ("summary", "tickets", "comments", "explain")
# the states in which an output may be added: drafts only after the user's greenlight; the explain file describes the
# results themselves, so the worker adds it as soon as the runs are in (2026-11-02)
OUTPUT_STATES = {"explain": ("running", "results_ready", "greenlit", "drafted")}
DRAFT_STATES = ("greenlit", "drafted")

# (from, to) -> who may make the move
TRANSITIONS = {
    ("queued", "closed"): frozenset({"user"}),
    ("results_ready", "greenlit"): frozenset({"user", "claude"}),
    ("results_ready", "closed"): frozenset({"user"}),
    ("greenlit", "closed"): frozenset({"user"}),
    ("drafted", "closed"): frozenset({"user"}),
    ("queued", "running"): frozenset({"claude"}),
    # the person too (2026-11-05, wingmate-debug FINDINGS #9), only once the task's start request ended (writes.py:
    # Release in the app of a start Hover stopped working on)
    ("running", "queued"): frozenset({"claude", "user"}),
    ("running", "results_ready"): frozenset({"claude", "user"}),
    ("greenlit", "drafted"): frozenset({"claude"}),
}
# moves claude may make only with the user's words recorded in the note
CLAUDE_NEEDS_USER_WORDS = frozenset({("results_ready", "greenlit")})


def can_transition(frm, to, by):
    return by in TRANSITIONS.get((frm, to), ())


def allowed_targets(frm, by):
    """States `by` may move a task to from `frm`, in state order."""
    return [to for to in STATES if can_transition(frm, to, by)]


def task_path(root, task_id):
    return os.path.join(root, TASKS_REL, f"{task_id}.json")


def load_task(root, task_id):
    if not isinstance(task_id, str) or not TASK_ID_RE.match(task_id):
        return None
    try:
        with open(task_path(root, task_id), encoding="utf-8") as f:
            obj = json.load(f)
    except (OSError, ValueError):
        return None
    return obj if isinstance(obj, dict) and obj.get("id") == task_id else None


def load_tasks(root):
    """All readable tasks, newest first (the id holds the day and the per-day number)."""
    d = os.path.join(root, TASKS_REL)
    try:
        names = os.listdir(d)
    except OSError:
        return []
    out = []
    for name in names:
        if name.endswith(".json") and TASK_ID_RE.match(name[:-5]):
            t = load_task(root, name[:-5])
            if t is not None:
                out.append(t)
    return sorted(out, key=lambda t: t["id"], reverse=True)


def next_task(root):
    """Oldest queued task, high before normal before low; None when nothing is queued."""
    # a task started from Today (origin) is run only by the QA worker, for its start request
    queued = [t for t in load_tasks(root) if t.get("state") == "queued" and not t.get("origin")]
    if not queued:
        return None
    return min(queued, key=lambda t: (PRIORITY.get(t.get("priority"), len(PRIORITY)), t.get("created", ""), t["id"]))


def current_runs(root, task):
    """[{run, newest, verdict}] per run of the task: its newest record (a correction <id>c1 replaces <id>) and that
    record's verdict. Read only: the task keeps the run ids it was given."""
    import data  # the app's read model, only for show

    idx = data.Data(root)._runs()
    out = []
    for rid in (r for r in task.get("runs") or [] if isinstance(r, str)):
        cur = data.newest(rid, idx["run_succ"])
        item = idx["runs"].get(cur)
        out.append({"run": rid, "newest": cur, "verdict": item["base"]["verdict"] if item else None})
    return out


def _add_root(p):
    # its own action per parser, default SUPPRESS: a shared action or set_defaults would let the subcommand's
    # default overwrite a --root given before it
    p.add_argument("--root", default=argparse.SUPPRESS, help="workspace root (default: this workspace)")
    return p


def _parser():
    ap = _add_root(argparse.ArgumentParser(prog="tasks.py", description="QA tasks: read and move them."))
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = _add_root(sub.add_parser("list"))
    p.add_argument("--state", choices=STATES)
    _add_root(sub.add_parser("next"))
    p = _add_root(sub.add_parser("show"))
    p.add_argument("id")
    p = _add_root(sub.add_parser("set"))
    p.add_argument("id")
    p.add_argument("state", choices=STATES)
    p.add_argument("--by", required=True, choices=ACTORS)
    p.add_argument("--note")
    p.add_argument("--user-words", dest="user_words")
    p = _add_root(sub.add_parser("add-run"))
    p.add_argument("id")
    p.add_argument("run")
    p = _add_root(sub.add_parser("set-campaign"))
    p.add_argument("id")
    p.add_argument("slug")
    p = _add_root(sub.add_parser("add-output"))
    p.add_argument("id")
    p.add_argument("kind", choices=OUTPUT_KINDS)
    p.add_argument("rel")
    return ap


def _print(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True))


def main(argv):
    args = _parser().parse_args(argv)
    root = os.path.abspath(getattr(args, "root", DEFAULT_ROOT))
    if args.cmd == "list":
        _print([t for t in load_tasks(root) if args.state is None or t.get("state") == args.state])
        return 0
    if args.cmd == "next":
        t = next_task(root)
        _print(t)
        return 0 if t else 3
    if args.cmd == "show":
        t = load_task(root, args.id)
        if t is None:
            _print({"error": f"no task {args.id}", "status": 404})
            return 1
        _print(dict(t, current=current_runs(root, t)))
        return 0

    import writes  # writes imports this module, so only here

    w = writes.Writer(root)
    try:
        if args.cmd == "set":
            t = w.set_task_state(args.id, args.state, note=args.note, by=args.by, via="qa-run",
                                 user_words=args.user_words)
        elif args.cmd == "add-run":
            t = w.add_run(args.id, args.run)
        elif args.cmd == "set-campaign":
            t = w.set_campaign(args.id, args.slug)
        else:
            t = w.add_output(args.id, args.kind, args.rel)
    except writes.WriteError as e:
        _print({"error": e.message, "status": e.status})
        return 1
    _print(t)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
