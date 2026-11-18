# QA tablet station

Office and bench testing of the Wingtra app (WingtraApp) on Android tablets driven from this Mac. The engine is in
`qa/`, the app's source in a `WingtraCloud` clone next to it (not part of this repo). See README.md for setup.

All testing here follows the QA manifesto: @qa/MANIFESTO.md

## Safety rules for every session

- Start Claude Code at this folder's root. The safety gate and the ledger are hooks in `.claude/settings.json`
  (written by `tools/fill_placeholders.py`) and load only then.
- Never read, print or copy `qa/secrets.env`, `qa/runner/state/` or approval keys. Never type a password or sign in for
  the person; never print an access token.
- Test only with the engine account named in `qa/runner/engine.json` (`account`).
- No ClickUp writes, except Hover's Add result and File ticket, each approved with Touch ID.
- Cloud data the engine creates goes only into the QA_ site in `engine.json` (`test_data.site`), with names starting
  `QA_`. Never delete existing sites, flights or maps.
- Never edit the gate, the hooks or the settings to silence a prompt. Propose the change and wait for an OK.
- Evidence and notes go in `tests/YYYY-MM-DD-<slug>/` (ignored by git).

## Builds and environments

One Android package, `com.wingtra.cloud.app`; CI bakes the environment into the web bundle by branch.

| Branch | Environment | Web app | API |
|---|---|---|---|
| `sky` | develop | web.dev.company.example | - |
| `sky-staging` | staging (office testing) | web.staging.company.example | api.staging.company.example |
| `sky-production` | production | web.company.example | - |

Settings > About shows the native version (APK versionCode) and the web bundle `<semver>-<env>-<hash7>`. A JS fix
can ship without a new APK; native changes need one.

## Reading at the right app version

The tablet runs a release, not the branch head. `git -C WingtraCloud fetch origin --tags --prune`, then read at the
build's hash with `git -C WingtraCloud show <ref>:<path>` and `git -C WingtraCloud grep <pattern> <ref> -- <path>`.
Every run record carries the build's git hash. Git is read-only here: no commit, push, branch switch or checkout in
the clone.

## QA practice

- Devices: TA5 and ET65 (TA3 once it has a profile). The report names every tablet that ran and any that did not,
  with why.
- Concurrency: write "parallel launch" with the measured overlap of the run windows and the spread of the deciding
  step. Write "simultaneous" only when the deciding steps overlapped.
- Confirmed vs suspected: a finding stays "suspected" until it repeats: 2 or more FAIL runs on the same build, a FAIL
  on 2 devices, or an independent hand run. After a FAIL, re-run it 3 times per device. Only confirmed findings get a
  ticket draft.
- Validated case version: engine FAIL runs count toward confirmation only after that case version
  (`case_file_sha256`) has passed once on the same kind of device. A false FAIL found later gets a correction record
  `<run id>c1` (verdict ERROR) that supersedes it.
- Verdict wording: PASS only when every step passed on the device. Otherwise PARTIAL, naming the untested steps.
  Stand-ins (MockLink, simulated drone) and deciding configuration (account, flags that change the code path) go next
  to the verdict. Say what was not tested as plainly as what was.
- End of cycle: the WingtraCloud clone is clean and on its branch; every ClickUp task the cycle referenced is
  unchanged unless the person posted; the report says "Protected resources: unchanged", "changed (what)" or "not
  verified".

## Getting logs off a tablet

Pull right after the test: logcat rotates and the app deletes old logs. From the test folder:

```
for d in Logcat Logs Metadata PowerSync; do adb pull /storage/emulated/0/Documents/WingtraCloud/$d ./tablet_$d; done
adb logcat -d -b all > tablet_full_logcat.txt
```

`Logs/` holds the app's logcat files, the C++ Qt log `*.txt`, `msgs_*.msg` and `TelemStats_*.telemetry`.

## Driving the tablet

The app's WebView has remote debugging on, so the engine reads the screen exactly over DevTools (CDP port 9341 +
profile slot) while every action is a real adb tap. CSS px x 1.5 = screen px, plus 45 px for the status bar on a
1920x1200 landscape tablet. Use `qa/runner/act.py`; one plain call per step (no shell variables or functions around
it), or the gate asks.
