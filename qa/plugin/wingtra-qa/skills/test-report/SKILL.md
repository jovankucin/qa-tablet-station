---
name: test-report
description: Write the results of a WingtraApp test as a ClickUp comment on the test task and a local summary - plain words, the key facts in bold, what was tested and why, expected vs actual per step, the verdict per device, why anything failed, links to the bug tickets filed, and what was not covered. Use after the person greenlit the results ("draft the comment", "write the test report", "summarise the results"), and as the last step after bug-report filed tickets. Writes local files and may type the comment into the ClickUp comment field in the browser pane; it never presses Send.
---

# test-report

The person reads this, not the engine. Write so a colleague who did not watch the test understands what was tested,
whether it worked, and if not, why. Plain words, **bold** for the key facts, no engine jargon in the ClickUp comment
(no run ids, ledger, step numbers of the case file, verbs). Tight: about 20% shorter than a full narrative; every
sentence says something the reader needs.

Only after the person's greenlight for these results (in chat, or the app's Greenlight). Never post: the person
presses Send in ClickUp.

## 1. Gather (reads only)

- The ClickUp test task: title, requirement text, dev ticket (CL-…), requester (`qa/cache/clickup/<task id>.json` or
  a read-only connector get). What the task asks may be more than the case covers: list the parts not covered.
- The run records of the cycle (`qa/runs/<campaign>/R-*.json`): verdict per device, steps, values, side effects,
  build (About label `<semver>-<env>-<hash7>`, native versionCode on tablets), account, flags that change the code
  path (OfflineSync, SurveyOutcomes, ...).
- The case: what it checks and why (the fix or ticket it guards), the test file or data (name, what it is, size).
- Bug tickets filed in this cycle (bug-report skill): their ClickUp links and titles.
- Runs that stopped for test-setup reasons (ERROR, BLOCKED): what stopped them and whether they were re-run.

## 2. Verdict words (use exactly these)

- **PASS**: every step of the test passed on that device.
- **FAIL**: the app did something other than expected (an app problem). Always with a bug ticket link, or "suspected,
  not yet confirmed" when it has not repeated yet (2+ failing runs on one build, 2 devices, or a hand run). A FAIL of
  a case version that has never passed is written "FAIL, suspected: the test case is not validated yet (it may be the
  test that is wrong)".
- **PARTIAL**: some steps passed, others were not tested; name the untested ones.
- **NOT RUN / BLOCKED**: the test could not run there (device, account, missing feature); say why.
- A stop caused by the test setup (engine, device state, a leftover dialog) is never a FAIL of the app: say "stopped
  by the test setup, re-run: PASS" or "not re-run".

## 3. The ClickUp comment (template)

```
**<VERDICT summary, e.g. PASS on TA5, ET65 and web | FAIL on TA5, PASS on ET65 and web>:** <one line: what works or not>

**What was tested:** <why this test exists (the fix/ticket), and what was done, in one or two sentences>

**Test file:** <name> (<what it is, key properties, size>)   (or **Test data:** ...)

**Devices** (<account>, <environment>):
- TA5: <About label> (<native versionCode>)
- ET65: <About label> (<native versionCode>)
- Web (<browser> on <machine>): <About label>

**Steps, <same result on all devices | per device below>:**
1. <what was done and what happened>. **As expected.**
2. <...>. **Not as expected:** <what happened instead>. **Bug:** <ticket link> (<short title>)

**Failed:** <only if something failed: device, step, what was seen, why (or "cause not known yet"), app problem or
test setup, ticket link>

**Not covered:** <parts of the task not tested, devices not run and why>
```

- Steps in user terms (dialogs, buttons, what the screen showed), each ending in **As expected.** or **Not as
  expected:**. When devices differ, give the step result per device.
- Every FAIL names its ticket (bug-report). A suspected failure says so and is not linked to a ticket.
- Numbers that matter: file size, processing time, counts. No emails, tokens, share links or internal paths.
- Save as `tests/<campaign>/comment_<task id>.md` (markdown bold `**…**`).

## 4. The local summary

`tests/<campaign>/SUMMARY_<case or task>.md`:
- **In plain words** first: the comment's content in prose (what was tested, expected vs happened, result, stopped
  runs and why, not covered).
- Then the engineering detail: case and version, builds per device, run ids and times, parallel or not (measured
  overlap), predictions vs outcomes, evidence paths (runs, screenshots, logs), side effects (cloud objects created),
  bug tickets, protected resources line ("unchanged" / "changed (what)" / "not verified").

## 5. Hand the comment over (never send)

1. Show the comment text in chat.
2. Type it into the ClickUp comment field of the test task in the browser pane (`app.clickup.com/t/<task id>`,
   comments panel): bold with Cmd+B on and off around the bold parts (typing `**` stays literal), line breaks with
   Shift+Enter only (Enter can send). Clear an older draft with Backspace (Cmd+A does not select in that editor) and
   check the field's text and bold parts by reading the editor before you stop.
3. Never click Send. Say it is in the field, unsent, for the person to review and send. If the browser pane is not
   signed in to ClickUp, ask the person to sign in there (never type a password) or to paste the local file.

## 6. Check before you finish

- [ ] Verdict line first and bold; every device named; builds exact.
- [ ] Each step: expected vs what happened; every FAIL has a ticket link or "suspected".
- [ ] Why for every failure and every stopped run; app problem vs test setup said plainly.
- [ ] Not covered listed (parts of the task, devices).
- [ ] No jargon, no secrets, no share links; about 20% tighter than a first draft.
- [ ] Local comment file equals what is in the ClickUp field; nothing sent.
