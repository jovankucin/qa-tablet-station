---
name: bug-report
description: When a WingtraApp test fails, turn the failure into a complete bug ticket filed through the Swiss Army Knife reporter - confirm it, triage it, collect the evidence (screenshots, a screen recording, the files that failed, logs, the build) and point to the place in the code, draft it in the team format, file it (the submit asks the person through the gate), attach the files, and link the ticket in the test report. Use after a FAIL in a test run (the person's standing rule of 2026-10-26: a failure gets a ticket without being asked), or when someone asks to report a bug they saw.
---

# bug-report

The person's rule (2026-10-26): **if something fails during testing, open a ticket via the Swiss Army Knife, complete
it with information, and link it in the test report.** Every ticket carries screenshots, a screen recording, the files
that failed, and the place of the bug in the software.

This skill runs the existing pieces in order and adds the evidence standard: `bug-triage` (classify, duplicates,
already fixed), `bug-draft` (title, description, `report.md`, evidence folder), `sak-submit` (the reporter, uploads),
then `test-report` (the link). Their own gates still apply. Filing is a ClickUp write: the reporter's Report bug and
Next taps ask the person through the gate, and that answer is their OK for this ticket (CLAUDE.md: per ticket).
Uploads ask again. Never post comments; never file with any account but the engine account (`account` in `qa/runner/engine.json`).

## 1. Is it a bug? (before any ticket)

- **A FAIL, not a stop.** Only a run whose verdict is FAIL (the app did something other than expected) starts this.
  ERROR or BLOCKED runs are test-setup problems (engine, device state, a leftover dialog, an account): fix and re-run;
  they go into the test report as "stopped by the test setup", never into a ticket.
- **Confirmed, not suspected (QA practice).** After a FAIL, re-run the case 3 times on each device. Confirmed when it
  repeats: 2 or more FAIL runs on the same build, a FAIL on 2 devices, or an independent hand run. A suspected
  failure gets only `report.md` (`Status: suspected`) and a "suspected, not yet confirmed" line in the test report.
- **Only a validated case version counts (the person's rule of 2026-10-27).** Engine FAIL runs count toward the first two
  routes only when that case version (`case_file_sha256`) has passed once on the same kind of device (bug-triage 1b).
  A FAIL of a version that never passed stays suspected ("case not validated"; the test itself may be wrong) and gets
  no ticket, unless an independent hand run shows the same failure.
- **Triage (bug-triage).** Exactly one class: app bug, spec question (for the requester), OS/OEM behaviour, test
  artefact, expected behaviour. Only an app bug gets a ticket. Search duplicates in Auto Bug Reports and the dev
  lists; a duplicate gets a comment draft on the existing ticket instead, and that ticket is linked in the test
  report. Check whether a fix is already in a newer build (git at the build hash); if so, say so and re-test there
  before filing.

## 2. Evidence (always, before the draft)

Collect into `tests/<campaign>/tickets/<n>/attach/` (bug-draft's evidence folder), each file with its sha256:
- **Screenshots**: the screen just before the failing action and the failure itself (`act.py <t> shot <path>`), and
  the expected state from a passing device or build when there is one (side by side tells the story).
- **Screen recording** of one reproduction, at most 3 minutes, trimmed to the failure
  (`tests/2026-10-09-terrain-resolution/tools/record.py <scenario> <out.mp4>`; trim with Swift/AVFoundation, no
  ffmpeg on this Mac).
- **The files that failed**: the input file (e.g. the .tif, the .flightplan of the flight), unchanged, with its
  sha256 and a one-line description (what it is, size, coordinate system, etc.).
- **Logs** pulled right after the failure: tablet `Logcat/`, `Logs/` (Qt log), `Metadata/` (CLAUDE.md "Getting logs
  off a tablet"); web: the run's HAR and console log. Note the time window of the failure (device clock).
- **Build and setup**: About label `<semver>-<env>-<hash7>` and native versionCode, device and Android or browser
  version, account label, environment, flags that change the code path (OfflineSync, SurveyOutcomes, ...).
- **Where in the software** (from source at the build hash, never from memory): the screen and control
  (`data-testid`), the WingtraCloud file and line that shows or handles it (`git -C WingtraCloud grep <pattern>
  <hash> -- apps/...`, `git show <hash>:<path>`), the API call and its response when the server is involved, and the
  native file for app-shell or drone issues. Write it as a one-line dev hint, marked **suspected cause** unless
  proven.

## 3. Draft (bug-draft), with these contents

- **Title**: `[CT] <area>: <symptom> <condition>` (3-100 characters), one bug per ticket.
- **Description** in the team's four headings, at most 1000 characters (memory bug-ticket-format):
  - `## Issue Summary`: one or two sentences, then the screenshots, then Where (app version, native, device, site,
    flight), Steps, **Expected**, **Actual**.
  - `## Occurrence Rate`: `n of m` runs per device and build (e.g. "3 of 3 on TA5, 0 of 3 on ET65").
  - `## Tester Action and Workaround`: what the user can do instead, or "none".
  - `## Additional Notes`: the dev hint (file:line, suspected cause), regression info (last build where it passed,
    first where it failed, when known), the test case and the requirement it breaks.
- **Severity** (bug-draft criticality 1-6), judged from the user's impact: data loss, wrong data or a blocked flight
  workflow is high; a wrong message with a working path is low. Say the impact in one sentence.
- `report.md` holds everything else (all runs, cross-device differences, ledger refs); it is never typed into the
  reporter.
- No emails, tokens, share links or customer data in the title or description.

## 4. File (sak-submit), attach, link

1. Open the flight or site the bug is about first (the reporter shares the last opened flight).
2. Run the sak-submit steps for the ticket folder: show the draft, fill the form, submit once. The submit taps ask
   the person through the gate: that is their OK for this ticket. A "no" stops here; the draft stays local.
3. Find the ticket by its exact title (the reporter does not show the id) and write `submitted.json`.
4. Upload the evidence the reporter did not attach (screenshots, recording, input files, extra logs): a separate
   ClickUp write that asks again (`clickup_request_attachment_upload` + `clickup_upload.py`). Read back the attachment
   count.
5. Add the bug to `qa/history/bugs.json` (CL id, title, case, devices, build, date, status) so History shows it.
6. Give the ticket link to `test-report`: the step that failed says **Not as expected** with **Bug: <link>**, and the
   verdict line names the failing device.

## 5. Check before you finish

- [ ] FAIL confirmed (or reported as suspected, no ticket); triage says app bug; no duplicate; not already fixed.
- [ ] Screenshots (before and failure), a recording, the failing files, logs, build and setup are in the ticket.
- [ ] Expected vs actual, steps and occurrence rate are exact; severity matches the user impact.
- [ ] The code location is from source at the build hash and marked as suspected unless proven.
- [ ] Filed only after the gate's OK; attachments uploaded with their own OK; ticket id found and recorded.
- [ ] The ticket is linked in the test report comment and in `qa/history/bugs.json`.
