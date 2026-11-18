# Playbook: Flight planning / Magic Planning (web, W044)

Read this before researching a flight-planning test. Facts from source at 1.75.0-staging-261b2d9 and the hand run of
2026-10-29 (tests/2026-10-29-magic-planning-cl80001). One run takes about 5 minutes.

## Case
**PLAN-001** (`qa/cases/PLAN-001.json`, W044, hands-off): import the QA_ plan into the QA site, Optimize, read, exit without saving. Use it for Magic Planning tests (ClickUp 0000fake006); the QA_ flight stays in the QA site.

## Before you start (2 minutes)
- **Flags** (read in the page: `localStorage.unleashFlags`):
  - `MagicPlanning` on: the "Optimize flight" button shows.
  - `MagicPlanningItemsOptimisation` on: area **splitting and flight direction** (CL-80001). Off = only path
    optimisation ("Optimized transit."). Off for the CT account on staging on 2026-10-29; **on as of 2026-10-30** (read on W044).
  - A case states the flags it needs in `needs.flags` (`"MagicPlanning"`, `"MagicPlanningItemsOptimisation"`,
    `"MapTilerTerrainSource: off"`); a flag in the wrong state is a blocking need in the plan, never a run that
    goes ahead (qa-worker 1b step 3, 2026-11-10). The states above were read for the CT account; not read for
    qa-stag on W044 yet.
  - `MapTilerTerrainSource` must be off, or the button is hidden.
- **Entitlement:** Stigg `feature-magic-flight-planning`, or the button is locked.
- **Eligible plan:** planned and editable, takeoff + landing + geobarrier, at least one area, no corridors, camera
  payload (no LiDAR), standard terrain (SRTM Auto), never flown, terrain following on every area, all items inside
  the geobarrier.

## Test data
- Plan file: `tests/2026-10-29-magic-planning-cl80001/files/QA_PLAN001_MP_split.flightplan` (INSPECT, 60 m, terrain
  following, one area up the Uetliberg slope, max ground clearance 90 m). Variants: `tools/make_plan.py` there.
- On W044 it must be in `~/qa-fixtures/` (scp, then `mv -n`; already there for this file).
- The import creates a new flight in the site you import from; the title is the file name (keep `QA_`). Online, a
  plan outside the site boundary still imports (that check runs only offline).

## Steps (act.py W044; [W] = cloud write; by hand the gate asks, in an engine case (PLAN-001) import and Optimize are declared QA-site writes the engine runs itself, Delete stays write-list)
1. README.md's start block (five steps, 2026-11-10: it leaves a planner only when the open flight is Hover's own
   `QA_` flight, then lands on Sites), then `tap "text:QA engine"` (path starts `/sites/a0000001`).
2. `tap testid:dropdown-button` → `tap testid:IMPORT_FLIGHT` → `choose-file testid:drop-zone --file <name>`.
3. [W] `tap testid:import-flight-dialog-submit-button` → lands on `/sites/<site>/planning/<flightId>`.
4. Read before: `GET https://api.staging.company.example/api/v1/flights/<id>` with `Bearer localStorage.accessToken`
   (never print it): `flightPlan.flightPlan.items`, per area `grid.angle`, `grid.altitude`,
   `camera.groundResolution`, `polygon`.
5. [W] `tap testid:magic-planning-entry-point --find-timeout 30` (creates optimisation jobs).
6. Poll the page text every 10 s (the green toast closes after 5 s): "Flight successfully optimized" + "Optimized
   transit." (+ "Split areas." / "Adjusted flight direction." only when that happened), "No further improvements
   found", "Failed to optimize flight", "Optimization takes too long" (limit 10 min). Took ~20 s on 2026-10-29.
7. Read after: area rows on screen ("A1 - Area", "A2 - Area", ...). The result is applied but **not saved**; the
   server copy stays as before until Save.

## Pass criteria (CL-80001, with the items flag on)
- Split: more area rows than before; direction: a child's `grid.angle` differs from the original.
- Children keep the original GSD and height; every child inside the geobarrier.
- The toast names only what changed; the plan is unsaved until Save; `wasOptimisationApplied` true only after Save.

## Clean-up
Leave the planner without saving with README.md's clean-up block (2026-11-10: it taps Sites and "Exit without
saving" only while the planner shows Hover's own `QA_` flight; a person's flight stays as it is). The QA_ flights
**stay in the QA site**: no deleting needed (the person's decision, 2026-10-30); each run works on the flight it
just created.

## Blocks to copy
A new case takes these blocks and the selectors in Steps above, never a whole case (README.md, "Blocks to copy").
- Start: README.md's start block, then the QA engine site (`PLAN-001.c4.json` step 2, "Open the QA engine site"):

```json
{"text": "Open the QA engine site", "gates": [], "helper": "act.py {target} tap \"text:QA engine\" --expect \"js:location.pathname.startsWith('/sites/a0000001-0000-4000-8000-000000000001')\" --timeout 20"}
```

- Clean-up: a running optimisation first (`PLAN-001.c4.json` step 15, "Clean-up: let a running optimisation finish
  before leaving"), then README.md's clean-up block:

```json
{"text": "Clean-up: let a running optimisation finish before leaving (always; up to 80 s)", "gates": [], "always": true, "when_js": "/Locked \\(optimizing\\)/.test(document.body.innerText)", "helper": "act.py {target} wait --expect \"js:!/Locked \\\\(optimizing\\\\)/.test(document.body.innerText)\" --timeout 80"}
```

- Selectors: Steps above, as `PLAN-001.c4.json` steps 3-6 and 9 write them (Actions menu `testid:dropdown-button`,
  `testid:IMPORT_FLIGHT`, `choose-file testid:drop-zone`, [W] `testid:import-flight-dialog-submit-button`, [W]
  `testid:magic-planning-entry-point --find-timeout 30`). Leave out its step 7, "Wait until the planner shows Optimize
  flight": it waits for the Optimize button before step 8's read, which a New test case never adds, so in a new case
  it would be a wait right before the Optimize tap for its own target. The tap's `--find-timeout 30` waits for the
  button instead (that wait took 0.8-2.2 s on W044 from 2026-10-30 to 2026-11-03, ledger times).
- The result: `PLAN-001.c4.json` steps 10-12 ("Wait for the result", three steps) wait on `window.__qaToasts` and
  `window.__qaT0`, which only its step 8's `read_js` ("Before: flags, ...") sets up (its MutationObserver; the same
  step reads the flight from the API, which a New test case never adds). Copied without step 8 they pass at once and
  the result is read too early, so never copy steps 10-12 alone. A new
  case waits on screen right after the Optimize tap, in the check part that reads the result (a run took about 90 s
  on 2026-10-30; this wait has not run on a device yet, and a message that is gone before it looks is missed):

```json
{"text": "Wait for the result on screen (up to 4 min)", "gates": [], "helper": "act.py {target} wait --expect \"js:/Flight successfully optimi[sz]ed|No further improvements found|Failed to optimi[sz]e flight|Optimi[sz]ation takes too long/i.test(document.body.innerText)\" --timeout 240"}
```

## Known limits at 261b2d9
- The geobarrier is not sent with the item (splitting) request (`services/magic-planning-lambdas/.../mapping.py:53-72`).
- Per-error messages (e.g. "Could not find a flight route") are not in this build.
- 2026-10-30 (PLAN-001 c3 PASS on W044): the run takes ~90 s (item job ~80 s, path ~10 s); on QA_PLAN001_MP_split the item step changes the flight direction ("Adjusted flight direction.") but does not split. A splitting test needs a different plan. Each run leaves one QA_ flight in the QA site.
- The page's network list can be empty after navigation: read results from the page and the API, not from it.
