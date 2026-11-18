---
name: sak-submit
description: File a prepared bug draft through the WingtraApp Swiss Army Knife reporter on the tablet or web where the bug was seen. When the person types /sak-submit <n>, or from the bug-report skill after a confirmed FAIL (the person's standing rule of 2026-10-26); the Report bug and Next taps ask the person through the gate either way.
argument-hint: <n> or <slug>/<n> (ticket folder tests/<slug>/tickets/<n>/)
---

# sak-submit <n>

This creates a ClickUp task (a write), makes the last valid flight public ("anyone with the link", with file export)
before the task exists, and attaches logs. It runs when the person typed `/sak-submit <n>`, or from the bug-report
skill after a confirmed FAIL (the person's standing rule of 2026-10-26); never chain into it from anywhere else. The
gate's prompt on the submit taps is the person's OK for this ticket: a "no" stops here. From the QA worker's `file`
request (2026-11-03) the OK is already given: the person pressed File ticket and confirmed with Touch ID; prefix both
`--approved` taps with `QA_TICKET=<ticket dir>` and the gate passes them without a prompt (only for that draft, that
device, once, within 30 minutes; anything else still asks).
The write-list taps (`Report bug`, `Next`) go through `act.py ... --approved`, which the gate turns into a permission
prompt. The one copy without a prompt is the scp of the ticket's evidence folder to webpc044 (web only). Never retry
blindly, and never press Enter (`act.py ... key enter`) while the Report dialog is open: Enter in Title or Tablet name
submits the form.

Paths below: `T` = `tests/<slug>/tickets/<n>`, `<t>` = the target (tablet tag such as TA5, or W044), `<serial>` = that
tablet's adb serial from its profile in `qa/devices/`, `<run>` = the run record path from `T/draft.json` `run_record`.

## Before step 0: checks (reads only; stop at the first failure and say why)

1. Ticket folder: the argument is `<n>` or `<slug>/<n>`, and `<n>` must match `^[0-9]+$`. Resolve
   `tests/*/tickets/<n>/` (or `tests/<slug>/tickets/<n>/`); exactly one folder must match, otherwise stop and ask.
2. Already filed? If `T/submitted.json` exists, stop and give its CL id. Otherwise search read-only
   (`clickup_search` in Auto Bug Reports, 919191919101) for the exact title in `T/title.txt`; a task with that exact
   title means it is already filed: stop and give its CL id. Do not continue.
3. Draft and run: `run_record` must be set and exist (a draft without a run is not filed: a run on the engine account
   must reproduce it first). The run's `verdict.v` is `FAIL`, its `account.st` is `verified` and `account.v` is the
   engine account (`account` in `qa/runner/engine.json`, as in bug-draft), and `draft.json` `finding` is `confirmed` (R1: a suspected finding is never filed). The `Scope:` line
   is present and fits the target (web: browser, OS and webpc044). Anything wrong
   goes back to bug-draft; never edit the text here.
4. Target: where the bug was seen, from the run's `target` (tablet tag, or W044 for web). A `both` draft is made from
   the tablet run unless the person picked web (bug-draft), so it files on the tablet, where the logs attach.
5. Account: `python3 -I qa/runner/run.py --check-account <t>` must exit 0 with `ok: true`. On any other result stop
   and ask the person to log the engine account in themselves. Never log in, never use `act.py type --secret`, never type
   a password, never continue under another account.
6. Build re-pin: read the running build the way the runner does:
   `python3 -I qa/runner/act.py <t> read "$(python3 -I -c 'import sys; sys.path.insert(0, "qa/runner"); import run; print(run.BUILD_JS)')"`
   Its `git_hash` (and on a tablet its `version_code`) must equal the run's `build.git_hash.v` (and
   `build.version_code.v`). If not, stop: SAK would stamp a build the bug was not seen on. Re-run the case on this build
   first.
7. Flight SAK will share (it reads these Capacitor Preferences keys; an empty site or flight gives NoFP, id `0`, and
   then nothing is shared):
   - tablet: `python3 -I qa/runner/act.py <t> read "Promise.all(['lastValidFlightPlanSiteId','lastValidFlightPlanFlightId'].map(k=>Capacitor.Plugins.Preferences.get({key:k}).then(r=>r.value)))"`
   - web: `python3 -I qa/runner/act.py W044 read "['lastValidFlightPlanSiteId','lastValidFlightPlanFlightId'].map(k=>localStorage.getItem('CapacitorStorage.'+k))"`
   Compare with the flight the run used. Stop and ask when either value is empty, null or `0` ("no flight will be
   shared"), or when it is another flight. Never submit with another flight opened. If the run recorded no flight id,
   show the ids and ask the person to confirm the flight.
   G3: SAK makes that flight public, so it must be one the engine made: its site id (`lastValidFlightPlanSiteId`) must
   equal `test_data.site.id` in `qa/runner/engine.json` (that site holds only data the engine creates), and a
   `side_effect` record of this ticket's campaign (`qa/runs/<campaign>/event/E-*.json`, `what` `cloud_create` or
   `cloud_update`) must point into that site: `object.path` (from the HAR) or `object.route` (from an act.py tap)
   contains `/sites/<that id>`. While `test_data.site.id` is null, no flight passes this check. Any other flight (a
   customer's, a colleague's, or one of unknown origin) is shared only when the person names that flight for this
   ticket in chat; otherwise stop and say so.
8. Tablet: the logs SAK attaches by itself (newest 2 `Logcat/*.txt`, 3 with the full-logcat dump, plus the newest 2
   each of `Logs/*.txt`, `*.telemetry` and `*.msg`):
   `adb -s <serial> shell ls -lt /storage/emulated/0/Documents/WingtraCloud/Logcat/ /storage/emulated/0/Documents/WingtraCloud/Logs/`
   Pull exactly those, read-only on the device:
   `adb -s <serial> pull /storage/emulated/0/Documents/WingtraCloud/<Logcat|Logs>/<name> T/auto_logs/`
9. Check, same command for the rest of this skill:
   `python3 -I qa/runner/draft_check.py T/ --link-json <run> --redact-to T/attach`
   (never `--no-secrets-file` here). It scans the text, `pack/` and `auto_logs/` and writes redacted copies into
   `T/attach/`. The only problems allowed now are "not reviewed" for binaries shown in step 0; anything else stops.
   Text findings show up as "before redaction" notes and as `redactions` entries with `changed: true`:
   - in `pack/`: the upload set is `T/attach/pack/` (it always is; it equals `pack/` when nothing changed);
   - in `auto_logs/`: SAK must not attach those logs. Plan "Completely skip auto log attachment (not recommended)" on,
     and the redacted copies in `T/attach/auto_logs/` go up in the separately approved upload step (step 3).

## 0. Show first, then wait

Show the person exactly:
- the title and the full description as they will be typed (from `title.txt` and `description.txt`, with the
  character counts from the check), the tablet name (`draft.json` `tablet_name`; tablet only), and the target;
- the flight SAK will share as "anyone with the link" with file export: site id, flight id and the names the run has;
- the toggles: full logcat (on only for a crash report), skip auto log attachment (on only when check 9 found
  something in `auto_logs/`), Attach more files (on for web, off on a tablet);
- every attachment: the SAK auto logs (names, sizes, times against the run window, and whether they are clean; 9 files
  when the full-logcat dump is on, and that dump is made at submit and cannot be checked first; the newest Logcat and
  Qt log keep growing until submit, so later lines are not scanned, and skip plus the redacted copies is the
  alternative), and the evidence files in `T/attach/pack/` with how they go (tablet: the upload step after "Report
  sent"; web: the person drops them into "Attach files to report");
- the check's notes, and the binaries for review: the screenshots (look at each yourself first for emails, avatars,
  share links and tokens), the recording's name and length, binary logs with their strings-scan result. Say plainly
  that whatever is not scanned goes to ClickUp as it is.
Nothing typed may differ from what was shown. Any change means re-running the check and asking again.
Wait for an explicit go-ahead in chat. Then record `{relpath: sha256}` in `draft.json` `reviewed_binaries` for the
binaries the person reviewed, and re-run the check 9 command: it must now exit 0, otherwise stop.
Worker mode (the QA worker's `file` request, 2026-11-03): do not wait. The person saw the draft on the Results page and
approved exactly this text with File ticket and Touch ID. Attach nothing beyond SAK's own auto logs (no `pack/` files:
no web drop, no upload step), so there are no binaries to review; note which evidence files stayed out.

## 1. Fill (tablet and web alike; web through `act.py W044`, CSS px, and it has no tablet name field)

1. `python3 -I qa/runner/act.py <t> probe`: the app in front, Dev help tools closed.
2. DevReporting on: `act.py <t> read "JSON.parse(localStorage.getItem('unleashFlags')||'[]').some(f=>f.name==='DevReporting'&&f.enabled)"` must be true.
3. Open the reporter: the top-left Swiss Army Knife icon (no text, testid or aria-label: use a `css:` selector that
   `act.py <t> find` resolves to exactly one element at the top-left) with `--expect "text:Dev help tools"`, then
   `act.py <t> tap "text:Report bug" --expect "sel:css:input[name=title]" --approved`.
4. Read the flight keys again (check 7): still the run's flight, or stop.
5. Type, each read back by act.py:
   `act.py <t> type "css:input[name=title]" --text-from T/title.txt`,
   `act.py <t> type "css:textarea[name=description]" --text-from T/description.txt`
   (`T` as its path under `tests/`; act.py reads the draft file itself, so the gate has no shell expansion to ask about),
   tablet only: `act.py <t> type "css:input[name=tabletName]" --text "<draft.json tablet_name>"`.
6. Toggles: read them with
   `act.py <t> read "[...document.querySelectorAll('[role=dialog] [role=checkbox]')].map(e=>[e.innerText.trim(),e.getAttribute('aria-checked')])"`
   and set each one that differs from step 0 by tapping its label, e.g.
   `act.py <t> tap "text:Dump and attach the full logcat file" --expect "js:[...document.querySelectorAll('[role=dialog] [role=checkbox]')].some(e=>e.innerText.trim()==='Dump and attach the full logcat file'&&e.getAttribute('aria-checked')==='true')"`.
7. Tablet keyboard: `adb -s <serial> shell dumpsys input_method | grep -c 'mInputShown=true'`. Only when it prints 1:
   `act.py <t> key back --expect "sel:css:textarea[name=description]"` (the dialog must still be open). Never BACK
   without that check: with no keyboard up it closes the dialog. Never Enter.
8. Read back title, description, tablet name and toggles in one `act.py <t> read`; they must equal what was shown.
9. Tablet, auto logs not skipped: list `Logcat/` and `Logs/` again; the newest names must be the ones pulled in check
   8. Otherwise stop: pull and check again, and show the change.
10. Web (not in worker mode, which copies nothing): the remote folder is `qa-files/tickets/<slug>-<n>` (`<slug>` the campaign, so tickets of two campaigns never
    share it). First `ssh webpc044 test ! -e qa-files/tickets/<slug>-<n>`: when it exists, stop and ask whether to
    remove it (old evidence would mix with the new). Then copy only the evidence,
    `scp -r T/attach/pack webpc044:qa-files/tickets/<slug>-<n>` (if `~/qa-files/tickets` does not exist yet, creating
    it is a remote command that asks first). Never copy `title.txt`, `description.txt`, `draft.json`, `pack.md` or
    `auto_logs/`.

## 2. Submit once

Record T0 (host UTC) just before. Tap Next once, by exact text (`text:Next`, never a substring: "Attach more files in
the next step" contains "next"), never ESC or a click outside:
`python3 -I qa/runner/act.py <t> tap text:Next --expect "gone:text:Next" --timeout 15 --approved`
- Tablet: `python3 -I qa/runner/act.py <t> wait --expect "text:Report sent" --timeout 300` (log uploads take time).
- Web in worker mode: Attach more files stays off, so wait for "Report sent" directly.
- Web: `python3 -I qa/runner/act.py W044 wait --expect "text:Attach files to report" --timeout 120`. Then hand over:
  the person drops the files from `~/qa-files/tickets/<slug>-<n>/` into the dialog on webpc044 (act.py has no file-input
  verb: `DOM.setFileInputFiles` is not among its allowed CDP methods). Then
  `python3 -I qa/runner/act.py W044 wait --expect "text:Report sent" --timeout 300`.
A readback ERROR, the "Couldn't create report, retry or cancel" toast, or the form coming back is never a reason to tap
Next again in this invocation: the flight may already be shared and the task may exist. Search read-only for the exact
title in 919191919101, created from T0 minus 5 minutes; wait at least 2 minutes for indexing and search once more;
then stop and report what you found. A retry needs a new `/sak-submit <n>` after the person confirms there is no task.
On web, once the person confirms there is no task, remove the remote copy as in step 3.6.

## 3. After "Report sent"

1. `act.py <t> tap text:Finish --expect "gone:text:Report sent"`.
2. Find the ticket read-only by its exact title, created from T0 minus 5 minutes, in Auto Bug Reports (919191919101):
   CL id, task id, status, created, attachment count, and whether the description holds a flight share link.
3. Write `T/submitted.json` with only: `cl_id`, `task_id`, `status`, `created`, `attachment_count`, `title` and
   `description` exactly as typed (from `title.txt` and `description.txt`), `flight_id`, `share_link_present`
   (true/false). Never the share URL, the metadata block, emails, names, tenant or Stigg ids, or the Sentry URL.
4. Uploads (never in worker mode: name the files left out in the request's done note, for the person to attach),
   a separate ClickUp write with its own OK per file list: on a tablet the files in `T/attach/pack/` (and
   `T/attach/auto_logs/` when SAK's auto logs were skipped), with `clickup_request_attachment_upload` and
   `python3 -I tests/2026-10-09-terrain-resolution/tools/clickup_upload.py <upload_url> <ticket> <file>`. Upload only
   the files listed in step 0, and read back the attachment count afterwards.
5. Links: when the run has an Issue record, write a superseding Issue record with `clickup: {cl, task_id, list, via:
   "sak", created, status, attachments}` through qa/runner (`records.write_record` and `records.hash_record`), never
   by editing a hashed record. Linking the run record itself needs a superseding run record, and qa/runner has no
   command for that yet (run.py has no `--link-issue`): say so and leave it open.
6. Web: delete the copy on webpc044 with `ssh webpc044 rm -r -- ~/qa-files/tickets/<slug>-<n>` (`<n>` checked
   against `^[0-9]+$` and `<slug>` against `^[0-9]{4}-[0-9]{2}-[0-9]{2}-[a-z0-9-]+$` again; this asks you first).
7. Record what changed (G3, QA practice): one `side_effect` event for the report, written through qa/runner:
   `python3 -I -c 'import json,sys; sys.path.insert(0,"qa/runner"); import records; print(records.side_effect(sys.argv[1], json.load(sys.stdin), session="sak-submit"))' <campaign> <<'SE'`
   with `{"target": "<t>", "what": "report_sent", "object": {"flight_id": "...", "site_id": "...", "cl_id": "CL-...",
   "share_level": "anyone_with_link", "file_export": true}, "account": "<engine.json account>", "ledger": {"day": "...",
   "seq": <the Next tap's ledger seq>}, "observed_by": "sak-submit", "reverted_by": null}`, then `SE` on its own line.
   The final report says `Protected resources: changed (flight <id> shared with anyone with the link, with file
   export; ClickUp task <CL> created)`.

Description rewrites and extra uploads are a separate step with their own approval, never part of this one. ClickUp
comments are never posted by Claude: draft them locally (short, precise) only when the person asks, and the person
posts them.
