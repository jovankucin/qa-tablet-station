# Playbook: Drawing a flight plan (area, corridor, take-off; tablets and W044)

Read this before a test that draws in the flight planner (e.g. offline sites: "create a flight in this site, add a
flight area, corridor and takeoff position in the middle"). Facts from source at 1.76.0-staging-82e23ff (WingtraCloud
82e23ff4a, paths under `apps/src/`). `act.py map-tap` is staged 2026-11-03 (the person's OK for drawing) and tested with
fakes only: **no device run yet**; the first run confirms the fractions and the readbacks marked (check).

## The verb
`act.py {target} map-tap <fx> <fy> [--double] [--expect ...] [--timeout N]`: a tap at fractions 0..1 of the one visible
map canvas (`canvas.mapboxgl-canvas`), measured at call time. Tablets tap through tap_css, W044 clicks. Refused:
fractions outside 0.02..0.98 (exit 4), no or several visible canvases (3), a point under something else such as the
sidebar, a toolbar or the loading screen (3, "covered"), any open dialog (2, run BLOCKED). `--double` taps the same
point again 0.12 s later (on a tablet plus one adb call; W044 gets two single clicks, no dblclick). No write list:
drawing changes only the local plan; the cloud gets it with Save (its own step and gate). `--expect` is optional: give
it on the tap that finishes an item; a read right after a map-tap needs a wait first (case_schema settle rule).

## The tools (left toolbar, `pages/FlightPlanning/components/FlightDrawingTools.tsx`)
| Tool | Selector | Source |
|---|---|---|
| Mapping tool: draws Area at first, later the last of Area/Corridor/Waypoint picked from its menu; a tap while its tool is on turns it off | `tap testid:btn-draw-area` | `FlightDrawingTools.tsx:84`, `:177-205` (test id `:183`); `hooks/useFlightDrawingTools.ts:333-351` |
| Its menu | `tap testid:open-tool-button-btn-draw-area`, opens on press | `components/map/ToolButton/components/ToolButton.tsx:154-171` (test id `:166`); `.../hooks/useFloatingElements.ts:22-34` |
| Area (in the menu) | `tap testid:btn-area-tool` | `FlightDrawingTools.tsx:141-148` |
| Corridor (in the menu) | `tap text:Corridor` (no test id) | `FlightDrawingTools.tsx:149-155`; label `i18n/locales/en.json:1338` |
| Take-off | `tap testid:btn-takeoff` | `FlightDrawingTools.tsx:207-216` |

- Tablet width (below XL) the menu arrow sits inside the mapping button (`compact`, `FlightDrawingTools.tsx:106-107`,
  `:184`; `ToolButton.tsx:209`): the arrow tap also clicks the button, so it turns its tool on (or off when it was on)
  before you pick from the menu. On a wide W044 window the arrow is a button of its own (`ToolButton.tsx:212`).
- Landing has no tool: with a take-off the app adds the land item itself (`context/FlightplanData/store/flightItemsStore.ts:427-436`); the sidebar does not list it (`.../components/flightPlanListEntries.ts:8-13`).
- The tools are hidden on a plan that is not editable (`FlightDrawingTools.tsx:182`, `:214`). Corridor is missing on a
  drone without corridors (`FlightDrawingTools.tsx:85-88`).

## How each item is drawn (mapbox-gl-draw 1.5.0 modes, `hooks/map/draw/modes/`)
- **Area** (`draw_polygon`, `useFlightDrawingTools.ts:339`): each tap adds a corner. A tap on a shown corner finishes it:
  the first corner or the last one placed (`drawPolygon.ts:54-57`, `:125-145`), so tap the last corner with `--double`
  (or the first corner once more). Fewer than 3 corners: dropped (`drawPolygon.ts:68-81`). Enter also finishes
  (`:91-93`) but is on act.py's write list; Escape drops the area in progress (`:84-90`, `helpers/handleEscapePress.ts:19-25`).
  Picking another tool finishes an area of 3 or more corners too (`useFlightDrawingTools.ts:275-294`, `:323-370`).
- **Corridor** (`draw_line_string`, `useFlightDrawingTools.ts:349`): each tap adds a point; a tap on the last point
  finishes (`drawLineString.ts:79-82`, `:126-136`): `--double` on the last point. Each segment at least 100 m on the
  ground, no overlapping consecutive segments (sharp bends) (`helpers/validateFlightPlan.ts:155-180`,
  `helpers/flightplan/constants.ts:32`).
- **Take-off** (`draw_point`, `drawPoint.ts:41-63`): one tap places it; the tool turns itself off
  (`useFlightDrawingTools.ts:231-233`).
- After each item the app turns the tool off (`hooks/map/draw/helpers/selectFeature.ts:15-22`,
  `useFlightDrawingTools.ts:242-250`), selects the item and opens its row in the right sidebar
  (`components/FlightPlanningContainer.tsx:65-78`). Pick the tool again for the next item.
- An item the checks block is deleted again, with a toast (`useFlightDrawingTools.ts:83-99`): "Corridor segments must
  be at least 100 m long.", "Two consecutive corridor segments overlap. Use multiple corridors.", "The created item is
  too far from the flight plan" (`en.json:1298-1300`), "The flight area is self-intersecting" (`en.json:1312`).

## Where to tap
The map canvas fills the page; the left toolbar and the 400 CSS px right sidebar lie over it
(`components/RightSidebar/RightSidebar.tsx:9`, `:25`; `contexts/MapViewportSizeObserver.tsx:17-20`). On TA5/ET65
(1280 CSS px wide) the sidebar starts near x 0.65: keep x within 0.1-0.6 and y within 0.15-0.8 (act.py refuses a covered
point, no tap). A new flight opens centred on its site (`components/FlightPlanningMap.tsx:62-74`, `:111`), at zoom 15
on a tablet and 14 on the web when the plan is empty (`helpers/map/focusOnFlightplan.ts:28-35`,
`configuration/map.ts:24-25`; (check) on the first run): near Zurich about 1.6 m per CSS px on a tablet, 3.2 m on the web.
Draw inside the site: the flight overview warns "Flight elements outside the offline area" (`en.json:1371`,
`.../components/ItemsOutsideSiteWarning.tsx:16-27`, `FlightOverviewModal.tsx:142`), and offline the map is held to the
flight's offline bounds (`FlightPlanningMap.tsx:94-95`). The recipe below spans about 0.12 x 0.27 of the canvas around
its centre (on a tablet roughly 250 x 340 m); shrink it toward (0.5, 0.5) for a smaller site, but keep each corridor
segment 0.05 wide or more (100 m on a tablet at zoom 15).

## Recipe (offline-sites scenario: area, corridor, take-off in the middle)
Before: the planner is loaded (`wait --expect "text:Create area and take-off point"`, shown while the plan is empty:
`.../screens/FlightPlanItemList.tsx:409-416`, `en.json:1329`).

| # | Step (`act.py {target} ...`) | Proves |
|---|---|---|
| 1 | `tap testid:btn-draw-area --expect sel:css:.mapboxgl-map.mode-draw_polygon` | Area tool on (check: the class is the library's `mode-<mode>` on the map container, not in the repo) |
| 2-4 | `map-tap 0.44 0.42`, `map-tap 0.56 0.42`, `map-tap 0.56 0.58` | three corners |
| 5 | `map-tap 0.44 0.58 --double --expect "text:A1 - Area"` | 4th corner, finished: row "A1 - Area" (`hooks/useFlightPlanMenu.ts:52-59`, ids `helpers/flightItemHelpers.ts:560-567`) |
| 6 | `tap testid:open-tool-button-btn-draw-area --expect text:Corridor` | the menu is open |
| 7 | `tap text:Corridor --expect sel:css:.mapboxgl-map.mode-draw_line_string` | Corridor tool on (check) |
| 8-9 | `map-tap 0.44 0.34`, `map-tap 0.50 0.31` | two points, above the area |
| 10 | `map-tap 0.56 0.34 --double --expect "text:C1 - Corridor"` | 3rd point, finished: row "C1 - Corridor" |
| 11 | `tap testid:btn-takeoff --expect sel:css:.mapboxgl-map.mode-draw_point` | Take-off tool on (check) |
| 12 | `map-tap 0.50 0.50 --expect "text:T - Take off"` | take-off in the middle: row "T - Take off" (`en.json:1332`, `TakeOffFlightItem.ts:57`) |
| 13 | `shot {shots}/{run}_result.png` | the drawn plan |

Also proves an item: its row's arrow `sel:testid:open-menu-A1 - Area` (`.../components/FlightItemListItem.tsx:119`,
title `:123-125`). The flight stats (minutes, battery sets, area, pictures) show only in the Flight overview dialog that
Save opens (`FlightOverviewModal.tsx:137`, `.../components/FlightPlanWorkMinimal.tsx:30-50`): read them there.
If a (check) readback never matches on the first run, use `--expect js:true` on that tool tap: the item rows prove the
drawing.

## Known limits
- No device run yet: the vertex hit on a tap (mapbox-gl-draw's 25 px touch and 2 px click buffers, not changed in
  `hooks/map/draw/useMapboxDraw.ts:122-141`) and the `mode-*` classes are library behaviour at 1.5.0
  (`drawPolygon.ts:41-45`), not in WingtraCloud.
- A blocked item disappears with a toast that closes by itself: the readback then times out (ERROR); the result shot or
  a `shot` right after the finishing tap shows the toast.
- Never `key escape` while drawing (it drops the item); act.py `ready` presses Escape only before the first step.
- Phone-width screens show a second map in the item form (`components/SmallMapCopy.tsx:37-45`, `AreaDetailsForm.tsx:39`,
  `:134`): map-tap stops there (two canvases). TA5, ET65 and W044 are wider.
- Waypoints (`text:Waypoint` in the menu, one tap like the take-off) are not in this recipe.
- Drawing changes only the open plan; keep it in the case's own flight. Saving goes only through the own-site save
  (offline-sites.md, Test data). "Export flight" saves the open flight first (`hooks/useFlightPlanMenu.ts:198-212`): it
  is on act.py's write list, never in a case. Page JS only reads (no `.click()`, no location change): New test refuses
  JS that acts (qa/app/worker.py NEW_TEST_JS_ACTS).
