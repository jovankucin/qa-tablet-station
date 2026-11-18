# Playbook: Offline sites and flight downloads (tablets: TA5, ET65)

Read this before researching an offline test: a site made available offline, or one flight downloaded. Facts from
source at 1.76.0-staging-82e23ff (WingtraCloud 82e23ff4a, paths under `apps/src/`); the flight downloads were read at
1.77.1-staging-8203f39 on 2026-11-10 ("Flight downloads" below). First engine runs on 2026-11-03: OFFL-001 c3 and
OFFL-002 passed on TA5 and ET65 (CT account, `qa/runs/2026-11-03-offline-sites`). Tasks: ClickUp 0000fake008 "Offline
sites (instead of offline flights)", 3 happy paths, then create and edit a flight offline; ClickUp 0000fake009
"Download custom terrain flight", with both flags off (2026-11-10: no flight download has run in the engine yet).

## Before you start (2 minutes)
- **Flags** (read in the page: `localStorage.unleashFlags`). They are set per account, and the engine account is
  qa-stag since 2026-11-04:
  - `OfflineSites` (`featureFlags.enum.ts:88`) decides which download exists (2026-11-10):
    - on: site downloads, "Enable offline use". Every site control below sits behind
      `FeatureFlagGate flag="OFFLINE_SITES" platform="android"` (`SiteDetails.tsx:174`, `SiteCard.tsx:64`,
      `ResponsiveNavigation.tsx:97`, `SiteOfflineStatusBadge.tsx:64`), so with the flag off none of them shows;
    - off: per-flight downloads, "Make available offline" ("Flight downloads" below); with the flag on these do not
      show, and the flight is offline through its site.
    Off for qa-stag on TA5 and ET65 on 2026-11-10 (the run records of `qa/runs/2026-11-10-custom-terrain-offline`);
    it was on for the CT account on 2026-11-03 (worker check W-20261103-001).
  - `OfflineSync` (`featureFlags.enum.ts:86`) decides whether a site can be edited offline
    (`useOfflineSiteEditing.ts:13-15`): record it next to the verdict. Off for qa-stag on both tablets on 2026-11-10.
  - The task's "Test setup" lines say which state the test needs; read them first (qa-worker 1b step 3). Each case
    states it in `needs.flags`, one entry per flag: `"OfflineSites: off"`, `"OfflineSync: off"` (or `"OfflineSites:
    on"`; a bare name means on). The runner reads the flags before the first step and ends the run BLOCKED when one
    differs (run.py `flag_needs`), as rounds 1 and 2 of 2026-11-10 did, so a flag in the wrong state is a blocking
    question in the plan, never a case that runs anyway. Read a tablet's flags as the build is read (qa-worker
    section 1 step 4), with `run.FLAGS_JS` in place of `run.BUILD_JS`: the names of the flags that are on.
- **Android only**: no web leg (W044 has no offline sites). TA3 is not set up: say "2 of 3 devices".
- The site card's ... menu shows only at tablet width (`SiteCard.tsx:196`, `menuEnabled={!isMobile}`).
- Storage: Next from the site details step with offline use on needs more than 100 MB free
  (`SiteCreateSidebar.tsx:31`, `:138-156`). A site over 100 MB of tiles cannot go offline (`constants.ts:3`).

## Test data
- New sites only as `QA_{target} <test title> {time}` (e.g. `QA_ET65 Happy path during creation 11-03 14:30`; the
  runner fills in the tablet and the run's start; the person's rule of 2026-11-03), typed with
  `act.py {target} type testid:input-site-name --text "QA_{target} <test title> {time}"`. The name is 3-100 characters and unique in
  the account (`SiteDetails.tsx:34-54`). New test runs Save new site only after that step, and Save site only on
  the site the same case created earlier with no type step in between (qa/app/worker.py `qa_site_named`,
  `own_site_update`). The sites stay in the engine account, like QA_ flights (the person's decision, 2026-11-03).
- A flight in the test's own QA_ site may be saved, by its label, and nothing else (the person's OK, 2026-11-03):
  the flight the same case created with `text:Create` (New Flight form) after its own allowed Save new site, saved
  with `text:Save`, `"text:Save & exit"` or `"text:Save as draft"`, each tap (the planner's Save too) gated
  `write_list` + `cloud_write` and listed in `cloud_writes` (qa/app/worker.py `own_flight_save`, run.py
  `standing_step`; act.py then taps only the planner's own save controls). Refused: the save test ids, any other
  flight, a flight in the QA engine site or another site, a site field between the site save and the flight save, a
  site save or Create with `when_js`, `read_js` or on another platform than the save. A Confirm is refused anywhere in
  a New test case, whatever its gates (`text:Confirm`, `aria:Confirm`, `sys-tap Confirm`, `testid:none`; qa-worker 1b
  step 5): the Edit flight Confirm moves the flight on save (`EditFlightDetailsModal.tsx:45-60`,
  `useFlightPlanMenu.ts:184-195`). At run time (review 2026-11-03) run.py reads the new site's id from the page right
  after Save new site (give it an `--expect` that waits for `/sites/<id>`, as `OFFL-001.c5.json` step 11 does), and
  act.py taps a save only while the page is `/sites/<that id>/planning/<flight>`: a case that left its flight stops
  BLOCKED with a hand-back.
- Keep the site area small: a short download, well under the limit.
- Always a fresh QA_ site per run (the person's decision, 2026-11-10: Hover writes and tests only in a `QA_` site it
  creates in the run): its toggle starts in a known state, and its flight list holds only the run's own flight. No
  toggle, download, save or open of a site, flight or map of a person: on 2026-11-10 a plan put the ... menu toggle
  on a person's site card and the app accepted it (the toggle takes no gate), so only this rule stops that. Never
  Save site on the QA engine site (a0000001): it rewrites the site.

## Controls ([W] = cloud write: gate `cloud_write`, listed in `cloud_writes`)
| What | Selector | Source |
|---|---|---|
| New site | `tap testid:btn-new-site` | `pages/Sites/screens/SitesList.page.tsx:109-112` |
| Site name | `type testid:input-site-name --text "QA_{target} <test title> {time}"` | `SiteDetails.tsx:140-146` |
| Location and site area | "Enter address or lat/long", then the area on the map (required: `SiteDetails.tsx:52`) | `SiteDetails.tsx:147-151`; not researched for the engine yet |
| Enable offline use | `tap "text:Enable offline use"` (the label; no testid). State: `aria-checked` of the `[role=checkbox]` around it | `SiteOfflineUseToggle.tsx:13-19`, `ToggleSwitch.tsx:83-95` |
| Next (site steps) | `tap testid:btn-sitecreation-next`, no gate (`text:Next` would need `write_list`) | `SiteCreateSidebar.tsx:259-267` |
| [W] Save new site | `tap "text:Save new site"` (by the label: its test id `btn-save-site-<loading>` is the same button on Site details, so New test refuses it) | `SiteCreateSidebar.tsx:242-257`; creates `useSiteSaving.ts:106`, then downloads `:113-116` |
| Site details | `tap testid:btn-site-settings` (path `/sites/<id>/site-details`) | `ResponsiveNavigation.tsx:91-95`, `SiteOverviewRouter.router.tsx:15` |
| [W] Save site | `tap "text:Save site"`: rewrites the open site (name, area, base locations, checkpoints: `useSiteSaving.ts:62-104`), then downloads | `SiteCreateSidebar.tsx:255`; only on the site the case created earlier (Save new site), no type step in between |
| Site card ... menu | `tap testid:elipsis-menu-handler`: the same testid on every card, so search for the site first (search bar `SitesList.page.tsx:86-90`) | `components/ui/ElipsisMenu/ElipsisMenu.tsx:31-43` |
| Toggle in the ... menu | `tap "text:Enable offline use"`: starts the download at once, with no save and no dialog | `SiteCard.tsx:60-70`, `:153-167` |
| Leave without saving | `tap text:Exit`, then Exit in "Exit without saving?" | `SiteCreateSidebar.tsx:269`, `SiteCreate.page.tsx:176-188`, `:238` |
| [W] Create (New Flight form) | `tap text:Create` (no testid); the site field defaults to the open site, never touch it (`testid:new-flight-site-input`) | `pages/Sites/components/FlightSetup/NewFlightForm.tsx:59`, `:176-184`, `:230-232`; creates and opens the planner `:79-106` |
| [W] Save (planner) | `tap text:Save` (tool button, testid `tool-button`; "Exit" when the flight cannot be edited): opens Flight overview, or Missing basic components for an incomplete plan; no write itself | `FlightPlanEditDone.tsx:107-134`, `:183-189` |
| [W] Save & exit | `tap "text:Save & exit"` in Flight overview (Android); disabled while the mission checks run and, offline, when flight items are outside the site | `FlightOverviewModal.tsx:207-218`; saves `FlightPlanEditDone.tsx:65-105` |
| [W] Save (draft) | `tap text:Save` in Missing basic components: saves the flight as a draft | `MissingBasicComponentsModal.tsx:62-79`, `FlightPlanEditDone.tsx:157-163` |
| [W] Save / Save as draft on leaving | "Exiting the flight planning" (leaving with unsaved changes): `text:Save`, or `"text:Save as draft"` for a draft plan; "Exit without saving" leaves | `Blocker.tsx:116-160` |

None of the three site download paths (`OfflineSites` on) has a confirm dialog. Paths: 1 creation: name, area, toggle on, Next (storage
check), [W] Save new site (lands on the site's flights, `useSiteSaving.ts:120-127`). 2 Site details of the QA_ site
the same case created with the toggle off: toggle on, Next to the last step, [W] Save site; the engine checks the
case, not the screen, so the case must still be on that site. 3 ... menu on the QA_ site
card: toggle on. Then wait for "Site available offline", `act.py {target} wifi off --until-offline`, and create
and edit a flight offline (New flight's Create is allowed; draw its area, corridor and take-off with `map-tap`:
flight-planning-drawing.md), then save it: [W] Save, then [W] Save & exit (or Save in
Missing basic components), and wait for "Flight successfully saved" (`en.json` flight_planning.flight_saved,
`FlightPlanEditDone.tsx:84`) as that save tap's `--expect`; a step that checks the message comes only when the task
asks for one (qa-worker 1b step 5, "Never add"). Offline the save goes to the tablet's own database and is sent on
the next sync (`useUpdateFlight.ts:22-35`, `hooks/connection/useOfflineCompatibleMutation.ts:28-40`;
`hooks/offlineMode/flights/useOfflineFlightSync.ts:15-19`, `:83-117`; with OfflineSync on, through the local API,
`providers/wingtra-api/tsRestClient.ts:84-106`).

## Flight downloads (`OfflineSites` off)
Read at 8203f39 on 2026-11-10; `FlightOverviewModal.tsx`, `MissingBasicComponentsModal.tsx`,
`DownloadingProgressCell.tsx`, `useFlightColumns.tsx`, `TableRow.tsx` and `ToggleSwitch.tsx` are the same files at
82e23ff4a. With the flag off there is no site download: one flight is downloaded, with the DSM and ortho of the
terrain saved on it (`hooks/offlineMode/flights/download/getDatasetInfo.ts:26-60`). Every way below calls the same
`downloadFlight` (`hooks/offlineMode/flights/DownloadFlightsContext.tsx:151`), on Android and only while online
(`:152-154`; offline the toggle and the button are blocked: `components/ui/Tooltip/OfflineBlockingTooltip.tsx:54-63`,
`components/ui/IconButton/OfflineDisabledIconButton.tsx:42-46`). That download code changed in WingtraCloud 7332290c0
(#11025, "restore legacy flight downloads and custom terrain"), which 8203f39 contains and 82e23ff4a does not: a
test of that fix says per tablet whether its build has it (qa-worker 1b step 2).

| What | Selector | Source |
|---|---|---|
| "Make available offline" in Flight overview | `tap "text:Make available offline"` (the label; no test id). State: `aria-checked` of the `[role=checkbox]` around it; off when the dialog opens, unless the flight is downloaded already. It only marks the flight: the download starts after the save | `FlightOverviewModal.tsx:173-184`, `ToggleSwitch.tsx:83-95`; `FlightPlanEditDone/FlightPlanEditDone.tsx:45`, `:59-63` |
| [W] Save & exit with the toggle on | `tap "text:Save & exit"`: saves the flight and leaves the planner, and the download runs from that save (to the page the planner was opened from, else the site's flight list) | `FlightOverviewModal.tsx:82-103`, `:207-218`; `FlightPlanEditDone.tsx:65-106`; `hooks/navigation/useStartingPointNavigation.ts:63-70` |
| The same toggle in Missing basic components | `tap "text:Make available offline"`, then [W] `tap text:Save` (saves a draft, then downloads it) | `MissingBasicComponentsModal.tsx:52-61`, `FlightPlanEditDone.tsx:158-165` |
| Download button in the flight list | `tap "aria:Make available offline"` (no test id): the button in the flight's row, column "Downloaded" (`en.json` flights.columns.available_offline); the download starts at once, with no save and no dialog | `pages/Flights/DownloadingProgressCell.tsx:47-55`, `:79-88`; the column: `pages/Flights/useFlightColumns.tsx:170-185`, in the Android list only `:220-233`; the site's flight list uses it (`pages/Sites/components/SitesFlights/SitesFlightsListView.tsx:109`) |
| A flight's download state | its row `[data-testid=flights-table-row]` holds `[data-testid=flight-offline-status]` with `data-state`: `not-downloaded` (the button), `downloading` (a spinner and "<n>%"), `available` (a green double tick, no button) or `failed` (the button, labelled "Retry flight download") | `DownloadingProgressCell.tsx:12-31`, `:57-91`; `components/table/TableRow.tsx:28` |

- From New test (qa/app/worker.py `new_test_write_problem`, read on 2026-11-10): the toggle and the list button are
  on no write list and write nothing to the cloud, so they take no gate and are not listed in `cloud_writes`. Save &
  exit, and the planner's Save before it, is the own-flight save of "Test data" above: only the flight the case
  created in the `QA_` site it created, gates `write_list` + `cloud_write`. So a flight download from New test is
  always Hover's own flight in its own site; a flight of a person is never opened, saved or downloaded.
- Which way: the one the task's words describe. Downloading the flight from the planner is the toggle with Save &
  exit; the download then takes what that save stored. The list button is for a flight that is saved already.
- The flight's own row: `aria:Make available offline` is the same on every row that is not downloaded, and act.py
  stops when it finds more than one ("ambiguous"), so it never taps another flight's button; in the run's own site
  there is one row. A wait for the downloaded state names the flight and reads that row, never "some row is
  available": on 2026-11-10 the person's site held two flights of one name, both downloaded. The row's ... menu has
  "Make available offline" too (`pages/Flights/useFlightRowMenuOptions.tsx:56-61`, `:77-79`); the button does the
  same, and no case opens that menu (Share, Archive and Delete sit next to it).
- Failure: toast "Flight download failed" (`en.json` flight_planning.offline.download.failed) and the row's state
  `failed` (`DownloadFlightsContext.tsx:346-352`, `DownloadingProgressCell.tsx:27-29`).
- Not run on a device yet (2026-11-10): none of these taps and waits, nor the planner's Save and Save & exit under
  the engine. Seen by hand: the Flight overview toggle, off, on ET65 at 82e23ff with the flag off
  (`tests/2026-11-10-custom-terrain-offline/hand-run/NOTES.md`), and the "Downloaded" column with green ticks on TA5
  (`tests/2026-11-10-custom-terrain-offline/diagnosis/TA5_now_1006.png`).

## Messages (`i18n/locales/en.json` at 82e23ff4a)
- Downloading: card badge "Downloading offline site — <n>%" (`en.json:418`, `SiteOfflineStatusBadge.tsx:22-29`).
- Done: "Site available offline" (`en.json:593`): the card badge (`SiteOfflineStatusBadge.tsx:20-21`) and the site
  header (`ResponsiveNavigation.tsx:96-105`).
- Failed: card badge "Failed to make available offline" (`en.json:421`, `SiteOfflineStatusBadge.tsx:38-44`; a tap
  retries, `:71-77`); toast "Failed to make site available offline" + "Check your internet connection and try
  again." (`en.json:441-442`, `useMapActions.ts:44-47`).
- Size limit: toast "Cannot make site available offline" + "The site is too large. Reduce the offline area and try
  again." (`en.json:428-431`, `useMapActions.ts:27-31`). The error's own text "This site exceeds the 100 MB limit for
  offline maps." (`services/mapsManager/errors/SiteSizeLimitError.ts:5`) is not shown.
- Storage: "Not enough storage for offline sites" (`useMapActions.ts:32-36`, `SiteCreateSidebar.tsx:145-150`).
- Also: "Waiting to resume download", "Offline map not up to date", "Maps service is unavailable. Contact Wingtra
  support." (`useMapActions.ts:18-20`). Toasts close by themselves: poll the page text.

## Clean-up
- Last step `act.py {target} wifi on` with `"always": true`; the runner also turns Wi-Fi back on after any run that
  switched it off, whatever the verdict (a note on the run when it cannot). Not when run.py itself is killed: the
  next run then stops at ready with "<tag> is offline: check its Wi-Fi".
- QA_ sites stay in the engine account: never delete or archive them. Their offline maps stay on the tablet
  (turning the toggle off removes the local map, `SiteCard.tsx:158-159`; it is not clean-up).
- Leave site creation or Site details without saving (Exit, Exit).

## Blocks to copy
A new case takes these blocks and the selectors in Controls above, never a whole case (README.md, "Blocks to copy").
- Start: README.md's start block, then a new `QA_` site with offline use: `OFFL-001.c5.json` steps 3-11 ("New site"
  to "Save new site"), with the test's own name and lat/long, without its two waits right before a tap for the tap's
  own target ("Wait for the lat/long suggestion", "Wait until Save new site is enabled": README.md, "Blocks to copy")
  and without the pauses it has before the lat/long pick (1 s) and the toggle (3 s): `TERR-013.json` steps 7-8 picked
  the lat/long and turned the toggle on without a pause on TA5 and ET65 on 2026-11-04 (both readbacks passed). Save new
  site keeps the 60 s of the wait it replaces as `--find-timeout 60`: the button stays disabled until two API reads of
  the recommended coordinate systems answer (`SiteCreateSidebar.tsx:249`, `isInitializingCrs` in
  `components/crs/context/CoordinatesSelectContext.tsx:85` and `:203`, `helpers/crs/getRecommendedCrs.ts:25-26`;
  1.3-4.2 s in the runs of 2026-11-03 and 2026-11-04, ledger times). These steps 3-11 are those of `OFFL-001.c3.json`
  but for the site name, and c3 passed on TA5 and ET65 on 2026-11-03. Leave out the toggle step for a site that stays
  online, as `TERR-012.c1.json` steps 5-10 do. Then wait for "Site available offline" as `OFFL-001.c5.json` step 12
  ("Wait for 'Site available offline' (up to 5 min)") does, when the test needs the download.

```json
{"text": "New site (opens site creation with the name field)", "gates": [], "helper": "act.py {target} tap testid:btn-new-site --expect sel:testid:input-site-name --timeout 20"}
{"text": "Site name: QA_{target} <test title> {time}", "gates": [], "helper": "act.py {target} type testid:input-site-name --text \"QA_{target} <test title> {time}\""}
{"text": "Location: type the lat/long <lat>, <lon>", "gates": [], "helper": "act.py {target} type testid:input-location --text \"<lat>, <lon>\""}
{"text": "Pick the lat/long suggestion: the map centres there and the default site area is set", "gates": [], "helper": "act.py {target} tap \"text:<lat>, <lon>\" --expect \"js:!document.querySelector('[data-testid=location-option]')\" --timeout 15"}
{"text": "Turn on Enable offline use", "gates": [], "helper": "act.py {target} tap \"text:Enable offline use\" --expect \"js:[...document.querySelectorAll('[role=checkbox]')].some(e => { let p = e; for (let i = 0; i < 4 && p; i++) { if (/Enable offline use/.test(p.innerText || '')) return e.getAttribute('aria-checked') === 'true'; p = p.parentElement; } return false; })\" --timeout 15"}
{"text": "Next (storage check; goes to the coordinate system step)", "gates": [], "helper": "act.py {target} tap testid:btn-sitecreation-next --expect \"text:Save new site\" --timeout 30"}
{"text": "Save new site: creates the QA_ site (cloud write) and starts its offline download", "gates": ["cloud_write"], "helper": "act.py {target} tap \"text:Save new site\" --find-timeout 60 --expect \"js:/^\\/sites\\/[0-9a-f-]{36}/.test(location.pathname)\" --timeout 60"}
```

- Flight download with `OfflineSites` off (2026-11-10; not run on a device yet): for the case's own flight in its own
  `QA_` site, from its planner, after the steps that draw or change it. Create that site with the new-site block
  above without its "Turn on Enable offline use" step: with the flag off the toggle is not on the page. The case states the task's setup in
  `needs.flags` (`"OfflineSites: off"`, `"OfflineSync: off"`). `<flight title>` is the name the case typed for its
  flight. The wait ends on downloaded or failed, so a failed download shows at once and is the app's result, not a
  timeout; the last line is the read of a check part (a part that only downloads leaves it out):

```json
{"text": "Save (planner): opens Flight overview, which shows Make available offline while OfflineSites is off", "gates": ["write_list", "cloud_write"], "helper": "act.py {target} tap text:Save --expect \"text:Make available offline\" --timeout 30"}
{"text": "Turn on Make available offline", "gates": [], "helper": "act.py {target} tap \"text:Make available offline\" --expect \"js:[...document.querySelectorAll('[role=checkbox]')].some(e => /Make available offline/.test(e.innerText || '') && e.getAttribute('aria-checked') === 'true')\" --timeout 15"}
{"text": "Save & exit: saves the QA_ flight (cloud write) and leaves the planner; the download runs from that save", "gates": ["write_list", "cloud_write"], "helper": "act.py {target} tap \"text:Save & exit\" --find-timeout 60 --expect \"js:!location.pathname.includes('/planning/')\" --timeout 60"}
{"text": "Wait until the flight's own row shows downloaded or failed (up to 5 min)", "gates": [], "helper": "act.py {target} wait --expect \"js:[...document.querySelectorAll('[data-testid=flights-table-row]')].some(r => r.innerText.includes('QA_{target} <flight title>') && !!r.querySelector('[data-testid=flight-offline-status][data-state=available],[data-testid=flight-offline-status][data-state=failed]'))\" --timeout 300"}
{"text": "Read the download state of the flight's own row; it must be available", "gates": [], "read_js": "(() => { const r = [...document.querySelectorAll('[data-testid=flights-table-row]')].find(x => x.innerText.includes('QA_{target} <flight title>')); const s = r ? r.querySelector('[data-testid=flight-offline-status]') : null; return s ? s.getAttribute('data-state') : null; })()", "save_as": "download_state", "assert": {"kind": "scripted", "expr": "v['download_state'] == 'available'"}}
```

- The list button instead, for a flight that is saved already, with the case on its own site's flight list (not run
  on a device yet); then the same wait and read:

```json
{"text": "Download the flight from its own row (the only row in the run's own site)", "gates": [], "helper": "act.py {target} tap \"aria:Make available offline\" --expect \"js:[...document.querySelectorAll('[data-testid=flights-table-row]')].some(r => r.innerText.includes('QA_{target} <flight title>') && !r.querySelector('[data-testid=flight-offline-status][data-state=not-downloaded]'))\" --timeout 20"}
```

- Clean-up: README.md's clean-up block, then Wi-Fi back on, last (`OFFL-001.c5.json` step 26), in every case that
  switches Wi-Fi off:

```json
{"text": "Clean-up: Wi-Fi back on (always)", "gates": [], "always": true, "helper": "act.py {target} wifi on"}
```

- Selectors: Controls and "Flight downloads" above; offline steps as in "None of the three site download paths"
  above (`wifi off --until-offline`, Create, the flight's own save).

## Known limits at 82e23ff4a
- Saving a flight runs only for the case's own flight in its own QA_ site (Test data above); a New test case has no
  other flight (own sites only, 2026-11-10).
- "Make available offline" in Flight overview and Missing basic components shows only with `OfflineSites` off
  (`FlightOverviewModal.tsx:173-184`, `MissingBasicComponentsModal.tsx:52-61`): with it on, the flight is offline
  through its site. With it off it is the flight download ("Flight downloads" above, 2026-11-10).
- Two more controls save the open flight and are on no write list, so neither New test's guard nor act.py treats
  them as writes: "Start flight setup" in Flight overview (`btn-flight-planning-fly`, `FlightOverviewModal.tsx:220-230`)
  and the web's "Done" (`done-button`, `:190-205`). Never use them in a case; closing that gap needs the person's OK.
  Flight overview opens only from the planner's Save, which the engine taps only in the planner of a flight in the
  run's own site, so these two can reach no other flight (they still leave no side_effect record on a tablet).
- The planner's "Export flight" (sidebar button and the ... menu item, no test id) saves the open flight first when it
  has unsaved changes (`FlightPlanItemList.tsx:333-339`, `hooks/useFlightPlanMenu.ts:198-212`), any flight in any
  site: since the review of 2026-11-03 it is on act.py's write list like Save (handed back under the runner, refused
  from New test). Never use it in a case.
- How a case sets the site area on creation is not researched yet.
- No size estimate before a download at creation (`SiteCreateSidebar.tsx:158`, a TODO): the size limit shows only
  when the download starts.
