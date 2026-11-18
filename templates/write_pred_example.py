"""Write and hash the predictions for one UPG-013 (c3) run of the WingLabs demo (2026-11-17).

python3 -I tests/2026-11-17-winglabs-demo/tools/write_pred.py <nn> TA5="<prior>" [ET65="<prior>"]

Run from the workspace root. Copied from tests/2026-11-12-station-first-run/tools/write_pred.py with today's campaign,
build and per-tablet facts. A prediction is written once, before run.py, and never edited (records are immutable):
the script stops when a file already exists.
"""
import json
import os
import sys

sys.path.insert(0, "qa/runner")
import records  # noqa: E402

CAMPAIGN = "2026-11-17-winglabs-demo"
DAY = "20261117"
# 1.78.0 staging (1.78.0-4-gd594ec611, origin/sky-staging), LiveUpdate versionCode 146, read on TA5 before the run
# (ledger 2026-11-17 seq 21104); the four files cited below are unchanged since 14a5167 (git diff --stat empty).
BUILD = "d594ec6117cc4dcbe3593f0b13fe2d693a5e1a3e"
VERSION = "c3"
JAVA = "apps/android/app/src/main/java/com/wingtra/cloud/app/"
P_MAIN = JAVA + "MainActivity.java"
P_CLEAN = JAVA + "plugins/WiPPlugin/FileCleanupUtils.kt"
P_LOGDIR = JAVA + "helpers/LogDirectory.kt"
P_REPORT = "apps/src/pages/Dev/components/ReportingModal.tsx"

FACTS = {
    "TA5": {
        "anchor": "20260922_140842_logcat_wingtracloud.txt",
        "listing": "adb listing of Documents/WingtraCloud/Logs on 2026-11-17 before the run: 304 entries, the anchor "
                   "is the oldest name; Documents/WingtraCloud holds Logs and Metadata only",
        "anchor_why": "the anchor ends in plain .txt, which the Qt-log cleanup filter can match, but it is still "
                      "listed after eight weeks of use",
        "older": "files named 20260922 and 20260924, before the 2026-10-15 reinstall, are listed",
        "started": "native 146 was installed at 08:51 today and logcat files from today (09:10 to 16:35) are "
                   "listed, so the Activity started today",
    },
}


def ptr(path, line, why):
    return {"line": line, "path": path, "ref": BUILD, "repo": "WingtraCloud", "t": "git", "why": why}


def prediction(tag, nn, prior):
    case = json.load(open(f"qa/cases/UPG-013.{VERSION}.json"))
    steps = case["steps"] if isinstance(case["steps"], list) else [s for v in case["steps"].values() for s in v]
    step4 = next(s for s in steps if s.get("n") == 4)
    f = FACTS[tag]
    run = f"R-{DAY}-{tag}-UPG-013-{nn}"
    return {
        "kind": "prediction",
        "schema": 1,
        "id": f"PR-{run}",
        "created": records.now_utc(),
        "author": "claude",
        "supersedes": None,
        "run": run,
        "case": "UPG-013",
        "case_version": VERSION,
        "build_git_hash": BUILD,
        "requirement_sha256": case["requirement"]["sha256"],
        "deciding_assertion": {"step": 4, "expr": step4["assert"]["expr"], "expected": True},
        "prior_observation": prior,
        "readers": [{
            "key": "logpath",
            "source": "WingtraCloud at d594ec6",
            "predictions": [
                {
                    "scenario": "step 3: a logcat file in Logs was written today",
                    "expected": "logs ok, newest_logcat mtime >= local midnight",
                    "reason": "redirectLogs() writes a new logcat file into Documents/WingtraCloud/Logs on every "
                              "Activity start (called from load() at line 92); " + f["started"],
                    "evidence": ptr(P_MAIN, 303, "redirectLogs()"),
                    "confidence": "0.9",
                },
                {
                    "scenario": "step 4: earlier sessions and the anchor " + f["anchor"] + " are kept",
                    "expected": "anchors_lost == [], earlier_sessions >= 1",
                    "reason": f["listing"] + "; " + f["anchor_why"],
                    "evidence": ptr(P_CLEAN, 259, "endsWith(.ext) cleanup filter"),
                    "confidence": "0.9",
                },
                {
                    "scenario": "steps 5 and 6: today's file and a pre-reinstall file read back",
                    "expected": "non-empty, >= 1 logcat line each",
                    "reason": "plain UTF-8 logcat output, read the way the bug reporter does (readdir at 373, "
                              "readFile at 322); " + f["older"],
                    "evidence": ptr(P_REPORT, 373, "readdir of WingtraCloud/Logs, then read by uri"),
                    "confidence": "0.8",
                },
                {
                    "scenario": "step 7: the old Logcat folder is gone",
                    "expected": "logcat_dir_present False",
                    "reason": "migrateLegacyDirectory moves the files and removes the folder; today's adb listing "
                              "shows no Logcat folder",
                    "evidence": ptr(P_LOGDIR, 37, "migrateLegacyDirectory"),
                    "confidence": "0.9",
                },
            ],
        }],
        "skeptic": {"consolidated": [
            {
                "scenario": "the Qt-log .txt cleanup (newest files ending in .txt) removes the anchor between "
                            "today's listing and the run",
                "expected": "anchors_lost non-empty, FAIL at step 4",
                "verdict": "unlikely: " + f["anchor_why"],
                "evidence": ptr(P_CLEAN, 259, "endsWith(.ext) cleanup filter"),
            },
            {
                "scenario": "the new build 1.78.0 (native 146) changed where or how app logs are written or kept",
                "expected": "FAIL at step 3 or 4",
                "verdict": "unlikely: MainActivity.java, FileCleanupUtils.kt, LogDirectory.kt and ReportingModal.tsx "
                           "are unchanged between 14a5167 (last PASS) and d594ec6",
                "evidence": ptr(P_LOGDIR, 37, "migrateLegacyDirectory"),
            },
            {
                "scenario": "today's logcat file holds no line in logcat format yet",
                "expected": "step 5 logcat_lines 0, FAIL at step 5",
                "verdict": "possible, unlikely: today's files are 200 to 380 KB",
                "evidence": ptr(P_MAIN, 303, "redirectLogs()"),
            },
        ]},
        "confidence": "0.85",
    }


def main(argv):
    if len(argv) < 2 or not (len(argv[0]) == 2 and argv[0].isdigit()):
        print(__doc__)
        return 2
    nn = argv[0]
    priors = dict(a.split("=", 1) for a in argv[1:])
    unknown = [t for t in priors if t not in FACTS]
    if unknown:
        print("no facts written for: " + ", ".join(unknown))
        return 2
    paths = {tag: f"qa/runs/{CAMPAIGN}/pred/PR-R-{DAY}-{tag}-UPG-013-{nn}.json" for tag in priors}
    existing = [p for p in paths.values() if os.path.exists(p)]
    if existing:
        print("not written, these exist already: " + ", ".join(existing))
        return 2
    for tag, path in paths.items():
        records.write_record(path, prediction(tag, nn, priors[tag]))
        entry = records.hash_record(path, note="winglabs demo: prediction")
        print(path, "ledger seq", entry["seq"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
