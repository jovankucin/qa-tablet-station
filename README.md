# QA tablet station

A Mac drives two Android tablets over USB, runs fixed WingtraApp test cases on both at once, and writes a PASS or
FAIL record with evidence for each tablet. Every record is hashed into an append-only ledger, so a result cannot be
changed afterwards without the verifier noticing.

This repo is a cleaned copy of one working station (October to November 2026). Company-specific values are replaced
by placeholders (see [Placeholders](#placeholders)); nothing in it is a credential.

## At a glance

A Claude Code session starts `qa/runner/run.py`, which drives both tablets through one USB hub and writes a hashed
record per tablet. Hover (`qa/app`) shows those records in a local web page. A CI link and a go/no-go summary do not
exist yet.

| Folder | What it holds |
|---|---|
| `qa/runner` | The engine: `run.py` (runs a case on several tablets), `act.py` (one step on one tablet), `gate.py` with `gate_hook.py` and `ledger_hook.py` (the safety gate and the ledger hooks), `records.py`, `verify.py`, `measure_device.py`, `status.py`, and its tests |
| `qa/app` | Hover, the local web app on http://127.0.0.1:8787: results, history, New test, ticket filing with Touch ID, and its tests |
| `qa/plugin` | The Claude Code plugin `wingtra-qa`: skills qa-run, qa-worker, qa-prep, bug-draft, bug-report, bug-triage, sak-submit, test-report, and a status pane |
| `qa/cases` | 43 test cases with all their versions (84 files); `run.py` always runs the newest version, and tests and newer versions cite older ones |
| `qa/playbooks`, `qa/schema` | How cases are written for each app area; the record formats |
| `qa/MANIFESTO.md` | The QA rules every run follows |
| `templates/` | Account record, secrets keys and a prediction writer to copy |
| `tools/fill_placeholders.py` | Puts your station's values in place of the placeholders |

## What is left out, and why

- **Secrets and per-machine state:** the secrets file, approval keys, `qa/runner/state`, LaunchAgents and scheduled
  tasks. Each station makes its own.
- **Results of the original station:** ledger, run records, issues, history, cache, backups, task records and all
  evidence (screenshots, logs). A new station starts with an empty ledger.
- **Device profiles and account records:** per station; see `qa/devices/README.md`.
- **Test files:** the terrain GeoTIFFs and flight plans the cases upload, which lived in `tests/<campaign>/files/` on
  the original station. 16 cases name such a file and cannot run until you put your own file at that path: PLAN-001
  to PLAN-006, TERR-004, TERR-005, TERR-007 to TERR-013 and TERR-016. TERR-002 and TERR-003 are drafts that would also
  need a GeoTIFF; the other 25 cases need no file.
- **Recorded from real sessions:** the back-test inputs, `pointers.py` and `recheck.py`, and the gate's recorded
  fixture file `gate_fixtures.jsonl` (131 rows; it held real ClickUp upload tokens). `test_gate.py` runs on the 12
  fixture files that remain. One of them, `gate_fixtures_f6.jsonl`, still holds 13 shell commands recorded from the
  original station's sessions (marked "recorded" in their `source` field), with paths, ids and names replaced.
- **The plugin's eval fixtures:** real ClickUp task snapshots with people's names (and `test_eval_graders.py`, which
  reads them).

Where shipped docs or code comments name one of these files, they say it is not in this repo: `SCHEMA.md`
(`recheck.py`), `test_gate.py` (the recorded fixtures), `test_draft_check.py` (the eval fixtures: the one check that
needs them is skipped) and `qa/app/data.py` (the back-test).

## Placeholders

Every company-specific value was swapped for a stand-in that occurs nowhere else:

| Kind | Stand-in |
|---|---|
| Staging, develop and production hosts | `api.staging.company.example`, `web.staging.company.example`, ... |
| Company mail and web domain | `company.example` |
| Site, flight and map ids; ClickUp custom-field ids | `a0000001-0000-4000-8000-000000000001`, ... (short form `a0000001`) |
| ClickUp workspace, list, folder and user ids; task ids; ticket numbers | `1919191`, `919191919101`, `91919191`; `0000fake001`, ...; `CL-80001`, ... (also spelled `CL80001`, `cl-80001`, `cl80001` in dataset and folder names) |
| Retired web test PC's IP; a team id in a test URL | `192.0.2.2`; `1919192` (leave as they are) |
| Tablet serials | `TA5SERIAL01`, `ET65SERIAL0001` |
| This repo's path and the Python interpreter | `__WORKSPACE__`, `__PYTHON3__` |
| People and accounts | `Owner`, `Colleague`, `qa-stag`, `QA_TEST_EMAIL` (not reversible) |

`placeholders.example.json` lists each reversible one with what it stands for (people and accounts are not listed: they
stay as they are). `tools/fill_placeholders.py` puts your values in, also in their regex-escaped form, the 8-character
short form of each id and the other spellings of each ticket number. What it does, in short (its docstring has the
details and exit codes):

- **Scope:** the text files of the tree, except `README.md`, `placeholders.example.json`, your values file,
  `.claude/settings*.json`, `templates/`, `tools/`, `WingtraCloud/`, `WingtraPilot/`, `tests/`, `*.env` files and the
  station's own records (`qa/runs`, `qa/ledger`, `qa/devices`, `qa/cache`, `qa/history`, `qa/issues`, `qa/tasks`,
  `qa/backups`). Run it on a fresh clone, before adding files of your own and before your first run.
- **Refuses** a value whose placeholder sits inside a longer one you left empty (for example `company.example` while
  `web.dev.company.example` is empty), and one value for two placeholders (`--allow-shared` overrides that, but then
  `--reverse` cannot work).
- **Warns** when a value already occurs in the shipped files, and lists every placeholder still present afterwards.
- **Keeps hashes in step:** each case's `requirement.sha256` and `supersedes.file_sha256`.
- **Writes nothing** if a changed `.json` or `.jsonl` file would no longer parse.
- **Writes `.claude/settings.json`** from its template; if you already have a different one, it prints the hooks block
  to merge and exits 1.

Do not commit a filled tree: it holds your local values. On the original station, filling in its own values gives back
its files byte for byte (checked on 2026-11-18 for all 220 copied files), except what the builder changes on purpose:
the names and a few rewritten notes and test names, small code fixes in `test_gate.py`, `test_settings_hooks.py`,
`test_draft_check.py` and `qa/app/data.py`, and `engine.json`, which is regenerated.

To commit new cases or code from a filled station, put the placeholders back first, read everything that will be
committed (new files too: `git diff` alone does not show them), commit, then fill again:

```
python3 -I tools/fill_placeholders.py --reverse placeholders.local.json
git status --short && git add -A && git diff --cached
```

`--reverse` works in the same scope, then searches every file git would commit (`README.md`, `templates/` and `tools/`
included) for any value still there and exits 1, naming the placeholder and file, if it finds one. A value that also
occurs in your new text for another reason becomes a placeholder too, so read the diff.

Fill `CL-80008` and `0000fake005` in any case: they are Hover's never-read guard (`qa/app/plan.py`, `NEVER_READ`) for
the ticket that holds a pasted credential. Left as placeholders, the guard protects nothing. Ask the station owner
for the two values; they are not in this repo on purpose.

## What you need

| Part | Used on the original station | Notes |
|---|---|---|
| Mac | Apple silicon, macOS 27, 8 GB RAM | Keep 20 GB disk free and sleep off on the charger; the original Mac crashed once with low disk and memory |
| Python | 3.13 from python.org | The engine and Hover use only the standard library; the hooks call one absolute interpreter path |
| adb | Android platform-tools 36 (Homebrew) | Each new Mac must be allowed on each tablet |
| Claude Code | Desktop app | Sessions must start at this folder's root, or the gate does not load |
| App repo clone | `WingtraCloud` next to `qa/` | `run.py` pins each run's build with `git describe` |
| Tablets | TA5: Samsung SM-X350, Android 16; ET65: Zebra ET65, Android 14 | Both on one USB hub |
| App | The WingtraApp staging build | Every build has WebView debugging on, so no special build is needed |
| Account | Your own staging account | Its login goes in the local secrets file, never in git |
| Optional | scrcpy and ffmpeg for recordings; swiftc (Xcode command line tools) for Hover's Touch ID approvals | The engine itself does not use them |

## Set up

Estimated, not timed: about an hour per Mac and 15 minutes per tablet.

1. **Mac.** Install Python 3.13 from python.org and adb with Homebrew. Free disk to 20 GB or more. Keep it awake on
   the charger: `sudo pmset -c sleep 0`.
2. **Repo and app clone.** Clone this repo, then clone `WingtraCloud` into it as `./WingtraCloud` (ignored by git).
3. **Placeholders.** Copy `placeholders.example.json` to `placeholders.local.json`, replace the description of each
   placeholder you need with your value (at least the staging hosts, the QA_ site, the tablet serials and the two
   never-read guard ids `CL-80008` and `0000fake005`), then run
   `python3 -I tools/fill_placeholders.py placeholders.local.json`. `__WORKSPACE__` and `__PYTHON3__` fill
   themselves. Add `--dry-run` first to see the counts.
4. **Engine config.** Check `qa/runner/engine.json`: `account` (your account record's label), `api_base`,
   `web_origin`, `test_data.site` (a QA_ site your account may write in) and `test_data.mock_drone`.
5. **Account and secrets.** Copy `templates/accounts.example.json` to `qa/devices/accounts.v1.json` and fill it in.
   In a terminal, copy `templates/env.example` to `qa/secrets.env` and put in your staging login.
6. **Plugin.** From this folder: `claude plugin marketplace add ./qa/plugin --scope project`, then
   `claude plugin install wingtra-qa@wingtra-qa-local --scope project`. With only the desktop app installed, `claude`
   may not be on your PATH: use the CLI the desktop app ships (`qa/plugin/INSTALL.md` shows where it was on the
   original Mac) or install Claude Code's CLI. Open Claude Code here and trust the folder, so the gate and ledger
   hooks load.
7. **Each tablet.** Turn on USB debugging, tick "Always allow from this computer" and turn on "Disable adb
   authorization timeout". No PIN or pattern. Keep the screen on while charging:
   `adb -s <serial> shell settings put global stay_on_while_plugged_in 7`.
8. **App on each tablet.** Install the staging build, log in with your account and open it once online, so its
   feature flags are cached.
9. **Profile per tablet.** `python3 -I qa/runner/measure_device.py <serial> <tag> <slot> --write` writes
   `qa/devices/<serial>.v<N>.json`. Use the tags TA5, ET65 and TA3; the CDP port is 9341 + slot.
10. **Check.** `python3 -I qa/runner/act.py TA5 ready` (then ET65) wakes the tablet and brings the app to the front.
    `python3 -I qa/runner/run.py --check-account TA5 ET65` must print `ok` for both.
11. **Optional: Hover.** `python3 -I qa/app/serve.py --port 8787` serves the QA app on http://127.0.0.1:8787.

## Run one test

One `run.py` call runs one case on both tablets at once and writes one record per tablet. Measured on the original
station: about 9 s for UPG-013 (7 reads) and 40 s for TERR-001 (18 steps with taps).

1. Ready both tablets: `python3 -I qa/runner/act.py TA5 ready` and `python3 -I qa/runner/act.py ET65 ready`. A probe
   fails while the app is not in front.
2. Check the account: `python3 -I qa/runner/run.py --check-account TA5 ET65`.
3. Write a prediction per run and hash it into the ledger: `qa/runs/<campaign>/pred/PR-<run id>.json`. `run.py`
   refuses to start without it. Copy `templates/write_pred_example.py` (written for UPG-013) to
   `tests/<campaign>/tools/` and set the campaign, day, build hash and per-tablet facts in it.
4. Run it in the background:
   `python3 -I qa/runner/run.py UPG-013 --targets TA5,ET65 --campaign <YYYY-MM-DD-slug> --nn 01`. It always runs the
   newest case version. Its exit code is 0 whatever the verdict; the verdicts are in the JSON it prints.
5. Check the records: `python3 -I qa/runner/verify.py`.

What one run record holds (`qa/runs/<campaign>/R-<date>-<tablet>-<case>-<nn>.json`):

| Field | Content |
|---|---|
| case, case_file_sha256 | The exact case version that ran |
| target | Tablet tag, serial and the profile it was checked against |
| build | About label, git hash, release tag, native version, environment, read from the app at run time |
| account, flags | Who was logged in and a snapshot of the feature flags that change code paths |
| attempts | Every step with its action, expected and actual readback, and the scripted check |
| evidence | Screenshots, logcat and the app's log, each with its sha256 in a manifest |
| verdict | PASS, FAIL, PARTIAL, BLOCKED, ERROR or NOT_TESTED, with the hash of the deciding check and the time |

## Feeding a qualification gate

No go/no-go script exists yet, but every run record already carries what one needs: the build's git hash, the case
version, the tablet, the account and a hashed verdict. A proposed rule for one build:

- **Go:** every required case has a live PASS on every required tablet, on that build's git hash, with the current
  case version and the engine account, and `verify.py` is clean for those runs.
- **No-go:** a confirmed FAIL on that build: the same case failed twice on one build, or on two tablets (Hover
  already uses this rule for "confirmed").
- **Not enough evidence:** anything else, for example a case not run on that build, or only one tablet.

## Where the original station stood (2026-11-17)

- UPG-013 and TERR-001 passed on both tablets on the 1.77.1 staging build (2026-11-12: four rounds of UPG-013, one
  of TERR-001).
- UPG-013 passed on TA5 on the 1.78.0 staging build (2026-11-17); ET65 was not attached, so 1.78.0 is "not enough
  evidence" under the rule above.
- In all, 11 cases had passed on both tablets; 9 of them only with an earlier shared account or in back-tests.
- `verify.py` exited 1 because it cannot find the manifests of 13 older correction records (a lookup gap, not
  tampering).

## Remaining gaps

| Gap | Why it matters | Work to close |
|---|---|---|
| Other agents (for example Codex) never ran here; the gate and ledger are Claude Code hooks only | Another agent gets no approval prompts: `--approved` is just a flag to it | Days: agree what it may run (read-only runs first), or a wrapper that asks the gate |
| No CI to station link: test APKs are built and installed by hand, installs ask for approval, run records do not store the APK's sha256 | No automatic run per build | Days |
| The app repo's own Android E2E (WDIO, Appium) runs on cloud emulators, one session at a time; the original Mac has no Node | The team's specs never run on these tablets | About a day for Node, pnpm and Appium; two tablets in parallel is untested |
| No go/no-go script; `verify.py` exits 1 on 13 old records | Nothing a CI job can read yet | Hours, but it changes the frozen engine |
| Few proven cases under the current account: UPG-013 and TERR-001; TERR cases depend on staging test data | Thin coverage for a release | Days per case |
| Predictions are written by per-campaign scripts with the build hard-coded | Each new run needs a script edit | Hours for a generic writer (engine change) |
| 2 of 3 devices: TA3 has no profile; the web leg was dropped | The three-device rule of the QA manifesto is not met | Hours for TA3, plus code lists that name only TA5 and ET65 |
| Tied to one Mac: hook paths, LaunchAgents and the worker's scheduled task | Each Mac needs the fill step and its own LaunchAgents | Hours |
| Some prompts stay by design: Save taps, cloud reads through the app | A run with a save or a cloud check stops for a person | By design |

## Tests

Each test file runs on its own from this folder, for example `python3 -I qa/runner/tests/test_status.py` or
`python3 -I qa/app/tests/test_app.py`. Fill the placeholders first: several tests read the filled settings and
`engine.json`.

Checked on 2026-11-18 on two copies of this repo, each filled with the original station's values (52 test files,
about 3.5 minutes per copy):

| Copy | Passing files | What fails, and why |
|---|---|---|
| Fresh clone, filled, no station data | 49 of 52 | `test_autonomy.py` (1 test: TERR-004 needs its terrain test file), `test_act_refusal.py` (4 controls: need a TA5 device profile), `test_gate.py` (20 of 1180 rows: 6 need device profiles and test files, 14 are tied to the original station, see below) |
| Same, plus the station's device profiles, account records and test files | 51 of 52 | `test_gate.py`, the same 14 rows |

The 14 rows tied to the original station: 4 expect a prompt for edits to the original owner's `~/.claude` files, and
10 expect the gate to refuse reads of the secrets folder through the original workspace path
(`~/Documents/WingtraApp Tests/qa`). On another Mac those paths are not this station's, so the gate rightly lets them
pass and the rows fail. Rewrite them with your own paths if you want a clean run.
