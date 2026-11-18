# Playbooks

One short page per app area: what a test there needs (flags, test data, controls, clean-up, known limits), so a test
starts with 2 minutes of reading instead of a long research round. The QA worker and qa-run read the matching page
first; after a test, add what you learned (keep each page short).

| Area | Page | Tests it covers |
|---|---|---|
| Flight planning / Magic Planning (web) | flight-planning.md | Optimize flight, area splitting and flight direction (CL-80001) |
| Offline sites and flight downloads (tablets) | offline-sites.md | `OfflineSites` on: Enable offline use at site creation, download from Site details and from the site's ... menu, then a flight created and edited offline (0000fake008). `OfflineSites` off: one flight downloaded with "Make available offline", then opened offline (0000fake009) |
| Drawing a flight plan (tablets + web) | flight-planning-drawing.md | Area, corridor and take-off drawn on the map with `act.py map-tap` (any test that creates a flight plan by hand, e.g. the offline flight of 0000fake008) |

## Blocks to copy

A case from New test is written part by part (qa-worker 1b step 5, "Steps from the task"). From the playbooks it
takes blocks, never a whole example case: its area's start block, its clean-up block and its selectors. Every step
below is taken from a case in `qa/cases`, cited by its file and step text (`TERR-012.json` is that case's first
version, c0, and `TERR-012.c1.json` its c1: each version numbers its steps anew), or built from a selector a playbook
names; the steps for "Do you want to exit?" and the guard of 2026-11-10 (the start block's step 2 and the `when_js`
of the first two clean-up steps) have not run on a device yet. Number the steps in order; give the start
block's steps the first part's `"part"` and keep the clean-up steps without one (`"always": true`). The settle check
(from 2026-11-02) and the parts check (from 2026-11-05) apply to the new case. The blocks check (from 2026-11-10)
applies to every case of a New test plan, a reused one too: the app refuses a plan whose case opens a page (a step
taps `testid:btn-sites`) without the start block as its first five steps, or whose clean-up taps Sites or "Exit
without saving" without the clean-up block's `when_js` (qa/app/worker.py `start_block_problem`).
No block waits right before a tap for that tap's own target (qa-worker 1b step 5, "Never add"): a tap waits by itself
until its target shows, is not covered and is enabled, for up to its `--find-timeout` (10 s when the step gives none;
`--timeout` is only the wait for its `--expect`). Where a target can take longer, give the tap a longer
`--find-timeout`, never a wait step. Three waits were taken out of the blocks after the debug pass of 2026-11-05
("Wait for the lat/long suggestion", "Wait until Save new site is enabled", "Wait until Create is enabled"); in the
runs of 2026-11-03 and 2026-11-04 they took at most 0.6 s, 4.2 s and 0.2 s (ledger times). Save new site keeps its
wait's 60 s as `--find-timeout 60`: it stays disabled until two API reads answer (offline-sites.md, "Blocks to
copy"). The blocks without those waits have not run on a device yet.
A case from New test may write only through import, create, process, Optimize, Next, Continue, the "Enable offline
use" toggle, saving a new site it names `QA_` and saving its own flight there; the app refuses any other write before
Run, a Confirm included whatever its gates (qa-worker 1b step 5). The per-flight download taps ("Make available
offline": the Flight overview toggle and the flight list's button; offline-sites.md, "Flight downloads") write
nothing to the cloud and take no gate.
Own sites only (the person's decision, 2026-11-10): a new case writes and tests only in a `QA_` site it creates in
the run (the new-site block of offline-sites.md), never in a site, flight or map of a person (qa-worker 1b step 5).
Magic Planning cases stay in the QA engine site (flight-planning.md).

| Area | Start block, clean-up block, selectors | Devices |
|---|---|---|
| Flight planning / Magic Planning | flight-planning.md, "Blocks to copy" | web |
| Offline sites and flight downloads | offline-sites.md, "Blocks to copy" | tablet |
| Custom terrain / Maps import (TERR, MAP) | below | tablet + web |
| App files (UPG) | below | tablet |

### Start block (every area that opens a page)

Every case that opens a page starts with these five steps, copied whole and in this order: each step's `when_js`
and `helper` exactly as written here, no gate and no `read_js` on them; only the step text is free. Since
2026-11-10 the app refuses a New test plan with a case, new or reused, that taps `testid:btn-sites` and does not
begin with them. The block leaves what an earlier run or a crash left open, then lands on Sites:
- a flight setup's "Do you want to exit?" dialog (the Surveying page's navigation blocker, WingtraCloud
  `apps/src/components/ui/Blocker/Blocker.tsx` and `pages/Surveying/components/BlockerWrapper.tsx`, read on the `sky`
  checkout on 2026-11-04: Exit only lets the navigation go on and writes nothing). Left open on TA5, it stopped
  `TERR-009.json` to `TERR-011.json` at their step 1 ("Top bar: Sites") on 2026-11-04. The steps find it by its text
  "Do you really want to exit?", so the Processing page's "Do you want to exit?" ("Your progress will be lost ...",
  which drops the person's processing setup) is never tapped: the run stops there instead;
- the planner, only when it shows Hover's own flight (step 2, the guard, added 2026-11-10; not run on a device yet).
  Hover leaves a planner only when it can read that the open flight's name starts with `QA_`: the name label
  `flight-name-editable-label`, or its input while the name is edited (WingtraCloud
  `apps/src/pages/FlightPlanning/components/RightSidebar/FlightNameEditableLabel.tsx`, read at 8203f39 and 82e23ff).
  Any other flight, or a name it cannot read (the label is on the planner's item list; its geobarrier and flight
  settings screens do not show it: `FlightPlanMenuSidebar/routers/FlightPlanSidebarRouter.tsx`), stops the run at
  step 2 as a test error (ERROR: it says nothing about the app) with the step's text: a person's flight is never
  left or dropped; the person saves or closes it by hand, then runs again. On 2026-11-10 TA5 had the person's own
  unsaved flight open in the planner (`tests/2026-11-10-custom-terrain-offline/hand-run/NOTES.md`): a start that taps
  "Exit without saving" would have dropped it;
- the planner's "Exit without saving", for that own flight (`TERR-012.json` steps 1-2, "Top bar: Sites" and "Exit
  without saving, when the app asks", with the dialog added to the Sites tap's `--expect`). A case that had dropped
  this step stopped on ET65 on 2026-11-10 behind the dialog of a planner that the Mac's crash of 2026-11-09 had left
  open (`qa/runs/2026-11-10-custom-terrain-offline/R-20261110-ET65-OFFL-003-03.json`).

The Exit taps use `text:Exit` (offline-sites.md, "Leave without saving"); act.py taps the one in the open dialog.
Steps 1 and 3-5 below are `TERR-012.c1.json` steps 1-4; step 2 is the guard.

```json
{"text": "Leave a flight setup's 'Do you want to exit?' dialog when one is open (Exit; writes nothing)", "gates": [], "when_js": "/Do you really want to exit\\?/.test(document.body.innerText)", "helper": "act.py {target} tap text:Exit --expect \"js:!/Do you really want to exit\\?/.test(document.body.innerText)\" --timeout 20"}
{"text": "Stop when the planner shows a flight that is not Hover's own (name not starting with QA_): a person's flight is never left or dropped; close it by hand, then run again", "gates": [], "when_js": "location.pathname.includes('/planning/')", "helper": "act.py {target} wait --expect \"js:(((document.querySelector('[data-testid=flight-name-editable-label]')||{}).innerText||(document.querySelector('[data-testid=flight-name-editable-label-input]')||{}).value||'').trim().startsWith('QA_'))\" --timeout 5"}
{"text": "Top bar: Sites (a planner or a flight setup left open asks first)", "gates": [], "helper": "act.py {target} tap testid:btn-sites --expect \"js:/Exit without saving|Do you really want to exit\\?/.test(document.body.innerText) || location.pathname === '/sites'\" --timeout 20"}
{"text": "Exit without saving, when the planner asks (writes nothing)", "gates": [], "when_js": "/Exit without saving/.test(document.body.innerText)", "helper": "act.py {target} tap \"text:Exit without saving\" --expect \"js:location.pathname === '/sites'\" --timeout 20"}
{"text": "Exit, when a flight setup asks 'Do you want to exit?' (writes nothing)", "gates": [], "when_js": "/Do you really want to exit\\?/.test(document.body.innerText)", "helper": "act.py {target} tap text:Exit --expect \"js:location.pathname === '/sites'\" --timeout 20"}
```

### Clean-up block (every area that opens a page)

The last steps of every such case, after the area's own clean-up steps (its page names them): leave the planner
without saving and land on Sites (`OFFL-001.c5.json` steps 24-25 and `TERR-012.json` steps 108-109, "Leave the
planner" and "Exit without saving when asked", plus the Exit of a flight setup's dialog). The three steps below are
`TERR-012.c1.json` steps 66-68 with the guard of 2026-11-10 in the first two (their `when_js` and the end of their
text; not run on a device yet): the clean-up taps Sites and "Exit without saving" only when the page is no planner
or the planner shows Hover's own `QA_` flight, so a planner showing a person's flight stays as it is (the run that
stopped at the start block's step 2 leaves it too). Copy them whole: the app refuses a New test plan whose clean-up
taps Sites or "Exit without saving" without that `when_js`.

```json
{"text": "Clean-up: leave the planner (always; a planner showing a flight that is not Hover's own stays as it is)", "gates": [], "always": true, "when_js": "!location.pathname.includes('/planning/') || (((document.querySelector('[data-testid=flight-name-editable-label]')||{}).innerText||(document.querySelector('[data-testid=flight-name-editable-label-input]')||{}).value||'').trim().startsWith('QA_'))", "helper": "act.py {target} tap testid:btn-sites --expect \"js:/Exit without saving|Do you really want to exit\\?/.test(document.body.innerText) || location.pathname === '/sites'\" --timeout 20"}
{"text": "Exit without saving when asked (always; writes nothing; only for Hover's own flight)", "gates": [], "always": true, "when_js": "/Exit without saving/.test(document.body.innerText) && (((document.querySelector('[data-testid=flight-name-editable-label]')||{}).innerText||(document.querySelector('[data-testid=flight-name-editable-label-input]')||{}).value||'').trim().startsWith('QA_'))", "helper": "act.py {target} tap \"text:Exit without saving\" --expect \"js:location.pathname === '/sites'\" --timeout 20"}
{"text": "Exit when a flight setup asks 'Do you want to exit?' (always; writes nothing)", "gates": [], "always": true, "when_js": "/Do you really want to exit\\?/.test(document.body.innerText)", "helper": "act.py {target} tap text:Exit --expect \"js:location.pathname === '/sites'\" --timeout 20"}
```

### Custom terrain / Maps import (TERR, MAP)

- Start: the start block. The case then creates its own `QA_` site (every new case does, 2026-11-10) with the
  new-site block of offline-sites.md ("Blocks to copy"), without its offline toggle step (as `TERR-012.c1.json`
  steps 5-10, "New site" to "Save new site", do; their Save new site has no `--find-timeout 60` yet).
- A test that asks for terrain "from an already processed mission" (2026-11-10): a map the case imports into its
  own flight (the attached DSM or ortho, or a generated DSM) through the planner's Import file (below) is a
  stand-in. The plan and the result say "stand-in: imported map, not a processed mission", and the
  processed-mission path goes to `not_covered` as the person's hand run (qa-worker 1b step 5). Hover never opens a
  site, flight or map of the person to get one.
- Clean-up, before the clean-up block (`TERR-012.json` steps 106-107, "Bring the app to the front" and "Close a
  leftover dialog"; `TERR-005.c5.json` steps 29-30 close only an Import file dialog):

```json
{"text": "Bring the app to the front (closes a leftover file picker)", "gates": [], "always": true, "helper": "act.py {target} ready"}
{"text": "Close a leftover dialog (Cancel; only when one is open)", "gates": [], "always": true, "when_js": "!!document.querySelector('[role=dialog]') && [...document.querySelectorAll('[role=dialog] button')].some(b => b.innerText.trim() === 'Cancel')", "helper": "act.py {target} tap text:Cancel --expect \"js:!document.querySelector('[role=dialog]')\" --timeout 10"}
```

- Selectors (controls read at 82e23ff4a; `TERR-012.json` ran to its step 57 on 2026-11-04, so its steps from 58 on
  have not run on a device yet, and `TERR-012.c1.json` has not run yet; [W] = cloud write, listed in
  `cloud_writes`). Every tap and sys-tap carries its `--expect` (act.py refuses one without it); copy each step whole,
  with its `--expect`, `--timeout`, `--find-timeout` and `pause_s`:

| What | Steps | From |
|---|---|---|
| Maps, Actions menu (the arrow half of the split button), Import map | `tap testid:btn-maps --expect path:/maps --timeout 20`; `tap testid:dropdown-button --expect sel:testid:import-map`; `tap testid:import-map --expect "text:Import file"` | `TERR-012.json` steps 11-13 ("Top bar: Maps" to "Choose Import map") |
| Tablet file pick (the file the engine delivered to `Download/QA-<CASE>`) | the JSON block below | `TERR-012.json` steps 14-22 ("Open the system file chooser" to "Wait until the dialog shows <file>") |
| Web file pick | `choose-file testid:file-drop-modal-drop-zone --file <file> --expect "text:<file>" --timeout 30` (`on: web`) | `TERR-005.c5.json` step 7 ("Choose <file> in the browser's file chooser") |
| First Next (reads the coordinate system; writes nothing), gate `write_list` | `tap text:Next --expect "js:!!(document.querySelector('[data-testid=input-dataset-name]') && document.querySelector('[data-testid=input-dataset-name]').value)" --timeout 30` | `TERR-012.json` step 24 ("First Next") |
| Dataset name, then the site | `type testid:input-dataset-name --text QA_{run}-<x>`; `tap testid:new-flight-site-input --expect sel:testid:better-select-input`; `type testid:better-select-input --text "<site name>"`; `tap "testid:dropdown-option-<site name>" --expect "text:<site name>" --timeout 20` | `TERR-012.json` steps 25 and 27-29 ("Dataset name" to "Choose this run's own QA_ site"; its "Close the keyboard" steps left out) |
| [W] Second Next: imports the map, gates `write_list` + `cloud_write` | `tap text:Next --expect gone:testid:input-dataset-name --timeout 30` | `TERR-012.json` step 33 ("Second Next") |
| Back to the case's own QA_ site (after Maps) | `tap testid:btn-sites --expect "text:<site name>" --timeout 30`; `tap "text:<site name>" --expect sel:testid:create-new-flight-button --timeout 30` | `TERR-012.json` steps 58-59 ("Top bar: Sites", "Open this run's own QA_ site") |
| A new flight in the open site, [W] Create (gate `cloud_write`) | `tap testid:create-new-flight-button --expect sel:testid:new-flight-name-input --timeout 20`; `type testid:new-flight-name-input --text "QA_{target} <title>"`; `tap text:Create --expect "js:/\/planning\/[0-9a-f-]{36}$/.test(location.pathname)" --timeout 30` (Create waits for itself while it is disabled) | `TERR-012.c1.json` steps 53-55 ("Create flight" to "Create"; `TERR-012.json` steps 60-63 still had "Wait until Create is enabled" before the tap) |
| Add terrain / map in the planner | `tap testid:btn-open-left-sidebar --expect sel:testid:layer-actions-button --timeout 20` (with its `when_js`, `!((document.querySelector('[data-testid=layer-actions-button]') || {}).offsetWidth > 0)`: the button stays in the page, 0 wide, while the panel is closed, so the `TERR-012.json` step 64 form, `!document.querySelector(...)`, skipped the open and the next tap found nothing; ET65, 2026-11-11); `tap testid:layer-actions-button --expect "text:Add terrain / map"`; `tap "text:Add terrain / map" --expect "text:Use existing map (WingtraCLOUD)"` | `TERR-012.json` steps 64-66 ("Open the layers panel" to "Choose Add terrain / map") |
| Use existing map (the default choice), gate `write_list` | `tap text:Next --expect "text:Add terrain / map from previous dataset" --timeout 20` with `"pause_s": 600` (map processing, below) | `TERR-012.c1.json` step 59 ("Next with Use existing map (WingtraCLOUD) after a 10 min pause") |
| Leave the existing-map list, or an Import file dialog, without choosing | `tap text:Cancel --expect "js:!/Add terrain \/ map from previous dataset/.test(document.body.innerText)" --timeout 15`; `tap text:Cancel --expect "js:!document.querySelector('[data-testid=file-drop-modal-drop-zone]')" --timeout 15` | `TERR-012.json` steps 71 and 88 ("Cancel the existing-map dialog", "Cancel the Import file dialog") |

Tablet file pick (`TERR-012.json` steps 14-22; `QA-<CASE>` and `<file>` are the case's own folder and file). Its last
step waits for the file's name in the dialog, which the app reads after the picker closed: that is not the next
tap's own target, so it stays (a check part reads the dialog right after it):

```json
{"text": "Open the system file chooser (photo picker opens)", "gates": [], "helper": "act.py {target} tap testid:file-drop-modal-drop-zone --expect js:true", "on": "tablet"}
{"text": "Photo picker: More", "gates": [], "helper": "act.py {target} sys-tap More --expect js:true", "on": "tablet"}
{"text": "Photo picker: Browse… (opens the full-screen file browser)", "gates": [], "helper": "act.py {target} sys-tap \"Browse…\" --expect js:true", "on": "tablet"}
{"text": "File browser: open the places (Show roots)", "gates": [], "helper": "act.py {target} sys-tap \"Show roots\" --expect focus:com.google.android.documentsui", "on": "tablet"}
{"text": "File browser: the internal storage (titled with the tablet's device name)", "gates": [], "helper": "act.py {target} sys-tap @storage --expect focus:com.google.android.documentsui", "pause_s": 1, "on": "tablet"}
{"text": "File browser: Download", "gates": [], "helper": "act.py {target} sys-tap Download --expect focus:com.google.android.documentsui", "pause_s": 1, "on": "tablet"}
{"text": "File browser: the case's own folder QA-<CASE>", "gates": [], "helper": "act.py {target} sys-tap QA-<CASE> --expect focus:com.google.android.documentsui", "pause_s": 1, "on": "tablet"}
{"text": "File browser: tap <file>; the app comes back", "gates": [], "helper": "act.py {target} sys-tap <file> --expect focus:app --timeout 15", "pause_s": 1, "on": "tablet"}
{"text": "Wait until the dialog shows <file> (the app reads the file after the picker closed)", "gates": [], "helper": "act.py {target} wait --expect \"text:<file>\" --timeout 30", "on": "tablet"}
```

`@storage` is the internal storage, named after the tablet: act.py reads that name, and taps the full-screen file
browser without connecting to the page, only when every `--expect` of the sys-tap is a `focus:` readback (as above;
act.py, engine piece 3), so keep those readbacks.

- Import file in the planner (works since 2026-11-10; no engine change). After "Add terrain / map" the layers menu
  stays open over the dialog and its "Add WMS layer" row covers "Import file", which is why `tap "text:Import file"`
  stopped `TERR-013.json` at its step 22 on TA5 and ET65 on 2026-11-04 ("covered: text:Import file is under another
  element at its centre"; `qa/runs/2026-11-04-custom-terrain-final/R-20261104-*-TERR-013-01.json`). A tap on the
  default choice closes the menu; then Import file and Next work. Seen on ET65 (1.76.0-staging-82e23ff) on
  2026-11-10/11 in a `QA_` flight: `tests/2026-11-10-custom-terrain-offline/import-file-look/` (screenshots 04 and
  05). Never copy the bare tap of `TERR-013.json` step 22 or `TERR-012.json` step 75. These three steps come right
  after the "Add terrain / map in the planner" steps above:

```json
{"text": "Close the layers menu, which stays open over the dialog: tap the default choice, Use existing map (writes nothing)", "gates": [], "helper": "act.py {target} tap \"text:Use existing map (WingtraCLOUD)\" --expect \"js:![...document.querySelectorAll('body *')].some(e => (e.innerText || '').trim() === 'Add WMS layer' && e.getBoundingClientRect().width > 0)\" --timeout 10"}
{"text": "Choose Import file (writes nothing)", "gates": [], "helper": "act.py {target} tap \"text:Import file\" --expect \"js:[...document.querySelectorAll('[role=dialog] input[type=radio]')].some(i => i.checked && ((i.closest('.w-56') || {}).innerText || '').trim() === 'Import file')\" --timeout 10"}
{"text": "Next: the Import file step (writes nothing)", "gates": ["write_list"], "helper": "act.py {target} tap text:Next --expect sel:testid:file-drop-modal-drop-zone --timeout 20"}
```

  Then the tablet file pick block above (the same drop zone, `testid:file-drop-modal-drop-zone`), the First Next and
  the dataset name of the table above, and the [W] Second Next. Leave the site field alone: in the planner it is the
  open flight's site (`MapFromUploadsCreator.tsx`), and touching `testid:new-flight-site-input` makes the app refuse
  the flight's own save. The import adds the map to the open flight with no Confirm. After the processing pause the
  map is chosen in the left sidebar's terrain menu (`testid:terrain-selection-menu`, then the map by its name; each
  option's test id is the map's id, unknown before the run). The steps after the drop zone have not run in the
  planner on a device yet.
- Choosing a listed map as the flight's terrain: the Confirm of the Use existing map list adds the chosen map to the
  open flight in the cloud (WingtraCloud `ImportFromExistingDataset.tsx`, `handleConfirmUpdates`:
  `addImportedFlightDatasets`, and `deleteImportedFlightDatasets` for a map taken out; read in the WingtraCloud
  checkout on 2026-11-06). New test refuses a Confirm whatever its gates (qa-worker 1b step 5), so a case only reads
  the list and leaves it with Cancel (`TERR-012.c1.json` steps 60-63, "Wait until both maps are listed" to "Cancel the
  existing-map dialog"). A check that needs the map chosen, such as "Check in the altitude graph so the new terrain
  is used", goes to `not_covered`: that Confirm needs the person's OK first (a chat run, where the step carries
  `write_list` and the runner hands it back; act.py's own write list does not name Confirm).
- Map processing (about 10 minutes on 2026-11-04; the app estimates 10-50): never an API read (`TERR-012.json` step
  57, the API site lookup, stopped 5 of the 10 runs on 2026-11-04). The Use existing map list does not wait for it: it
  reads the site's maps once, when it opens, lists only processed ones and does not refresh while it is open
  (WingtraCloud `ImportFromExistingDataset.tsx` in the same dialog and `apps/src/hooks/datasets/useDatasetsFrom.ts`:
  no refetch interval, and nothing refreshes that list when processing ends; read on the `sky` checkout on
  2026-11-04). So a long `--timeout` inside that list (`TERR-012.json` step 68, "Wait until both maps are listed")
  waits for nothing. Wait before it opens: `"pause_s": 600` (the most a step may pause) on the step that opens it
  (`TERR-012.c1.json` step 59, Next with Use existing map), then wait in the list with a short `--timeout` (as its
  step 60 does). This pause is for the processing, not for the Next button, so the never-add rule does not drop it.
  A map still processing then is not listed, and that run ends in a test error, which says nothing about the app. The
  map viewer page polls its map every 10 s (`apps/src/pages/Maps/screens/Pix4dViewer/ViewMap.tsx`); no case waits
  there yet.

### App files (UPG)

No start or clean-up block: the steps read the app's own files through the page (the Filesystem calls of UPG-013
c2's `read_js` steps) on whatever page shows, with no navigation.

## After a crash or a restart

For the person or a chat session, never a New test run (2026-11-10). A Mac restart or a killed run loses the case
that was running: its clean-up never runs, so a planner can stay open on a tablet. The crash of 2026-11-09 left ET65
in the planner of a `QA_` flight, and the next day's case stopped behind its exit dialog. To recover:
1. Unlock TA5 and allow USB debugging; replug ET65.
2. `python3 -I qa/runner/act.py <tag> ready` for each tablet.
3. On each tablet open Sites. When the app asks, use "Exit without saving" only when the open flight is a `QA_`
   flight; a flight of a person is saved or closed by that person.
4. In the QA app, press Cancel (Release) on the stuck request: its task moves to Results with the runs that
   finished.
5. Start Hover: Claude app > Scheduled > Hover > Run now.
6. Run the unfinished case again.
