# QA app: design and contract

The app and its worker are called Hover (the person's choice: named Wingmate on 2026-11-04, renamed Hover on 2026-11-06): the page header, the status lines,
the watchdog's alerts and the scheduled task's title. The worker's notes and questions use Hover's short, friendly
voice; results, verdicts, reports, tickets and ClickUp text stay plain (qa-worker skill).

A thin local web view over the QA engine. Claude Code plus the wingtra-qa plugin execute everything; the files in
`qa/` and `tests/` are the single source of truth. The app reads them, and writes only four kinds of file
(section 4). It never runs adb, ssh, scp, CDP or the runner, and never writes the ledger. Its outside calls are the
read-only ClickUp reads (section 8): GET requests to api.clickup.com; and, since 2026-11-03, one write: Post comment
(8.16), one comment on a test's ClickUp task, only after the person's press and Touch ID.

Start: `python3 -I qa/app/serve.py [--port 8787] [--root <workspace>]` (root defaults to two levels above
`qa/app/`). It binds 127.0.0.1 only and prints `http://127.0.0.1:<port>/` (never the token).

## 1. Files

| Path | Role |
|---|---|
| `qa/app/serve.py` | `ThreadingHTTPServer` on 127.0.0.1, routing, JSON responses, static files, `/files/` |
| `qa/app/security.py` | Host, Origin, Sec-Fetch-Site and token checks, response headers, `resolve_read(root, rel, image_allowed)` |
| `qa/app/data.py` | read model: `Data(root)` with the methods in section 3; pure reads, no writes |
| `qa/app/writes.py` | the only module that writes: `Writer(root)` (section 4), `WriteError(status, message)` |
| `qa/app/case_schema.py` | `validate_case(obj) -> [problems]`, `requirement_sha256(text)`, `AREAS` |
| `qa/app/tasks.py` | task model shared by the app and `/qa-run`: states, transitions, CLI (section 5) |
| `qa/app/views.py` | the Results and History answers, pure reads: one test (`result_cards(data, task=, case=)`), the title rule (`test_title`), History's 'Your tests' (`tests`), the result cards and the quality numbers (`quality_stats`) (8.17) |
| `qa/app/static/index.html`, `app.js`, `app.css` | the page; vanilla JS, no CDN, no inline script or style |
| `qa/app/static/results.js`, `results.css` | the Results page: one test (8.17); the older detailed page stays in app.js behind `#/results?view=detail` |
| `qa/app/static/history.js`, `history.css` | the History page: 'Your tests' (8.17), then the numbers and charts over `GET /api/stats`, Bugs and What was done |
| `qa/app/tests/test_app.py` | stdlib `unittest`; `python3 -I qa/app/tests/test_app.py` |

Every module starts with `sys.path.insert(0, <qa/app>)` and `sys.path.insert(0, <qa/runner>)` (Python `-I` does not
put the script folder on the path) and imports `redact` and `records` from `qa/runner` instead of copying them.
Python 3.13 stdlib only. No Node.

## 2. Security model

Every request, any method:
- `Host` must be exactly `127.0.0.1:<port>` or `localhost:<port>` (the bound port). Otherwise 403. This is the
  DNS-rebinding guard.
- If `Sec-Fetch-Site` is present it must be `same-origin` or `none`. Otherwise 403.
- Responses carry `Cross-Origin-Resource-Policy: same-origin`, `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store`, and on HTML
  `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'; object-src 'none'`.
  No CORS headers, ever.

Writes (`POST`, `PUT`; every other method except `GET`/`HEAD` is 405):
- `X-QA-Token` must equal the per-start token (`secrets.token_urlsafe(32)`, compared with `hmac.compare_digest`).
  The page gets it from `<meta name="qa-token" content="…">` that `serve.py` fills into `index.html`.
- `Origin` must be present and equal `http://<Host header>`; `Content-Type` must be `application/json`
  (else 415); body ≤ 64 KiB (else 413); body must parse as a JSON object (else 400).

Reads:
- Every JSON response body goes through `redact.redact_obj` before serialisation; every text file through
  `redact.redact`. Text = `.json .jsonl .md .txt .log .har .csv .yaml .yml .py .sh .conf .xml`: UTF-16 or UTF-32
  after its BOM, otherwise UTF-8 (errors replaced); a file whose decoded text still holds a NUL is refused with 415,
  since NULs between characters hide values from the redactor. Capped at 2 MiB: a larger file is read 64 KiB past the
  cap (less its last, maybe cut, line or word), redacted, and only then cut at the last line break before 2 MiB
  characters, with a final line saying so; a value at the cut is therefore masked whole. `redact.py` reads
  `qa/secrets.env` again when the file changes (checked at most once a second), so a long-running app masks a new or
  rotated value.
- `/files/<rel>`: `rel` is URL-decoded once, must be relative, must not contain `\x00`, `\\`, a `..` segment or a
  segment starting with `.`, and its realpath must be inside `<root>/qa` or `<root>/tests` (symlinks resolved).
  Denied always: `qa/secrets.env`, anything ending `.env`, `qa/cache/**`, `qa/ledger/**`,
  `qa/runner/state/locks/**`, `qa/runner/state/approval.key`, `qa/runner/state/approvals/**` (section 8.15),
  `*.tmp`, `*.apk`, archives, and any file with more than one hard link (a hard link to
  a file in a denied folder shares no inode with the folder; nothing in the engine creates hard links). Images
  (`.png .jpg .jpeg .webp .gif`) are served only under `tests/` and only when the path is referenced as evidence by
  a run record (an attempt's `shot` evidence or an image in the run's own `evidence` list, where back-test and
  live-recorded runs keep theirs), is the `last_shot` of a current progress file (a run in progress has no record
  yet), or lies in a ticket's `pack/` folder; images are the one response that cannot pass through the redactor, and
  that is why they are restricted to engine evidence. Any other extension: 404. Status codes: 400 malformed path,
  403 refused, 404 missing or not a regular file.
- `data.py` lists a `/files/` URL only when `security.resolve_read` would serve it (one rule, not two copies), and it
  never follows a symlinked folder or file while it reads `qa/` and `tests/`.
- Errors return `{"error": "<message>"}` with the status; messages never echo request bodies (writes included:
  a `WriteError` names the field and the rule, never the value sent).

## 3. Read API (`GET`, all JSON unless noted)

Values in records are often sourced values `{"v": …, "st": …}`; `data.py` unwraps them with `val(x)` (returns
`x["v"]` for a dict holding `v`, else `x`). Times are ISO-8601 UTC strings. `hash7` is the first 7 of the git hash.

- `/` → `index.html` with the token meta. `/static/<name>` → files in `qa/app/static/` (exact names only).
- `/api/meta` → `{env, account_label, web_origin, targets: [{tag, serial|null, class: tablet|web}], areas: [..],
  ledger: {seq, head12}, now, port}`. Targets come from the newest device profile per serial (`qa/devices/*.vN.json`,
  kind `device`) plus `{tag: "W044", class: "web"}`.
- `/api/live` → `{status: <qa/runner/state/status.json> | null, status_age_s, progress: [<progress file>],
  locks: [{name, holder, case, since}], handovers: [{target, reason, since, file}], handbacks: [{run, case, target,
  step, reason, command, created}]}`. Approvals waiting in Claude are not shown: the user decided on 2026-10-22 not to
  add approval writes to the gate hooks.
  `progress` = `qa/runner/state/progress/*.json` (section 6). `handbacks` = run records from the last 7 days whose
  verdict is BLOCKED with `by.kind == "handover"` and no newer run of the same case and target.
- `/api/tasks` → `{tasks: [<task> + {run_summary: [{run, case, target, verdict}]}]}` newest first.
  `/api/tasks/<id>` → the task with `runs: [<run summary rows as in results>]`.
- `/api/results?case=&task=` → `{cases: [{case, title, area, targets, harness, rows: [<row>], differentials:
  [{id, result, runs, builds, compared, created}], devices, extra_devices, launches: [<launch>]}], backtest: {runs,
  current, superseded, verdicts: {PASS: n, …}, claims: {agree: n, disagree: n, not_in_records: n, unverifiable: n},
  disagreements_md: <rel path or null>}}`.
  `devices` = the case's three devices (QA practice in CLAUDE.md): `["TA5", "ET65", "W044"]` for targets `web` or
  `both` (and when the case is unknown), `["TA5", "ET65", "TA3"]` for `tablet`. `extra_devices` = other tags that ran
  in one of the listed launches (sorted; they get a column but do not count in `devices_ran`).
  A launch = runs launched together: same campaign, case and run number (the trailing `-NN` of the run id; a
  correction `…-NNc1` belongs to its base run). Newest first (newest `attempts[0].started`, else `created`, of its
  records). With `task=`, only launches holding one of the task's runs, each still with all of its runs.
  `<launch>` = `{campaign, nn, mode (live|backtest|live-recorded|mixed), builds: [hash7…], cells: {<tag>: <cell>},
  devices_ran, concurrency}`. `<cell>` = `{run, verdict, evidence_kind, attempts, stale, mode, older: [run ids]}` for
  the record nothing supersedes (then the newest; `older` lists the device's other records in the launch, a corrected
  base among them), or `{verdict: "NOT_TESTED", why}` for a device of the three with no run, `why` = `no run in this
  launch`, or `not routed (web-only case)` for TA5 and ET65 on a `web` case. `devices_ran` = `"3 of 3"`, `"2 of 3
  (W044 not run)"`, `"1 of 3 (TA5, ET65 not routed: web-only case)"`.
  `concurrency`, from the current record of each device:
  - 2+ runs with mode `live`: `{kind: "parallel launch", windows: {<tag>: {start, end}} (attempts[0].started/ended),
    overlap_s (min(end) − max(start), 3 decimals; null when ≤ 0), common_window: {start, end}|null, deciding_step,
    deciding_step_from: prediction|case, deciding_times: {<tag>: t|null}, spread_s (latest − earliest deciding time;
    null with fewer than 2), simultaneous_verified: false, finished_before: [{tag, before, step}], text}`.
    The deciding step is `deciding_assertion.step` of the run's prediction file (`qa/runs/<campaign>/pred/<id>.json`,
    `id` = the record's `prediction.id`, else `PR-<run>`, else `PR-<base run>`), else the last step with a scripted
    assert of the case version the run used (latest when unknown); the most common value across the runs is used. A
    run's deciding time is the ledger `t` of the first action (`{day, seq}`) of that step in `attempts[0].steps`; a
    run without it did not reach the step. `finished_before` lists each run whose window ended before another run's
    deciding time. `text` e.g. `Parallel launch: windows overlap 33 s; deciding step 12 read 4.8 s apart;
    simultaneous not verified; W044 finished before TA5 and ET65 reached step 12` (`no overlap of the run windows`
    when they do not overlap; seconds as whole numbers from 10 s, else one decimal).
    `simultaneous_verified` is always false: the ledger holds when each action's line was written, not when the
    deciding read started, so "simultaneous" is never written as a claim.
  - one live run: `{kind: "single device", text: "single device (<tag>)"}`.
  - no live run: `{kind: "not applicable", text}`, `back-test (reconstructed from logs)` for back-test launches,
    `live-recorded (written after the fact, no run windows)`, otherwise `no live run (<modes>)`.
  The ledger is read for `t` only: one `{seq: t}` index per `qa/ledger/<day>.jsonl` (day must match
  `\d{4}-\d{2}-\d{2}`), built on first use and kept while the file size is unchanged; a file that only grew is read
  on from the last complete line.
  `runs` counts every back-test run record (62 today), `superseded` those a later record corrects, and `verdicts`
  the `current` ones (`runs = current + superseded`).
  `<row>` = `{run, case, target (tag), target_class, mode, build: {hash7, semver, env, version_code, about_label},
  verdict, evidence_kind, attempts, created, campaign, differential, judgement_pending, stale: {v: true|false|null,
  why: [..]}, thumbs: ["/files/tests/…png", …≤4], evidence: [{kind, path, url|null}], superseded_by|null}`.
  Rows are per run, newest first; the view groups them by target and build.
- `/api/runs/<RUN>` → the run, plus `steps: [{n, text, readback, assert, actions, skipped}]`, `evidence` with URLs,
  `prediction: {id, path, ledger}`, `differential` (record or null), `case_title`.
- `/api/history?case=<id>&target=<tag>` → `{rows: [<row>…]}` across builds, oldest first.
- `/api/tickets` → `{drafts: [<draft>], triage: [<triage>], legacy: [<legacy>]}`.
  `<draft>` = folder `tests/<slug>/tickets/<n>/`: `{dir, slug, n, title, description, title_len, description_len,
  checks: [{name, ok, detail}], draft_json, pack: [{rel, size, url|null}], comments: [{cl, rel, text, len}],
  filed: <filed.json>|null, submitted: <submitted.json>|null, run_record}`. Lengths are UTF-16 units after a JS-style
  trim (`draft_check.utf16_len(draft_check.js_trim(s))`); checks: title ≤100, description ≤1000, title format
  `draft_check.TITLE_RE`, the four headings present.
  `<triage>` = `tests/<slug>/triage/<id>/triage.json` plus `{dir}`. `<legacy>` = older `tests/*/tickets/ticket<k>_*`
  files grouped by `k`: `{dir, k, title, description, title_len, description_len}`.
- `/api/cases` → `{cases: [{id, title, area, targets, status, harness, versions, latest_file, requirement_source,
  last: {<tag>: {verdict, run, created, hash7}}, stale: {v, why}}]}`. Latest version = highest `.c<N>` (the
  plain file is c0). Cases from `qa/cases/` and `qa/runner/tests/cases/` (harness).
- `/api/cases/<id>` → `{case: <latest>, versions: [{file, sha256, created, supersedes}]}`.
- `/files/<rel>` → section 2.

Staleness of a run (`stale`): `why` collects
1. `requirement changed`: the case version whose file sha256 equals the run's `case_file_sha256` has a different
   `requirement.sha256` than the latest version (unknown version → `v: null`, why `case version not found`);
2. `covered paths changed`: `git -C <root>/WingtraCloud diff --no-renames --name-only <run git_hash> <tip> --
   :(glob)<g>…` with env `GIT_NO_LAZY_FETCH=1`, `GIT_TERMINAL_PROMPT=0`, timeout 10 s, where `<tip>` =
   `origin/sky-staging` for env staging (`sky`, `sky-preproduction`, `sky-production` for the others). Non-empty
   output → stale with up to 5 changed paths. A failing git (hash not in the clone) → `v: null`.
`v` is true if any reason holds, false if both checks ran clean, null otherwise. Results are cached per
(hash, tip sha, globs). Git is the only subprocess the app may start, always with an argv list and these env vars.

## 4. Write API and write scope

`Writer(root)` is the only writer. Every write goes through `Writer._write(rel, bytes, mode)` which:
- allows only these `rel` patterns: `qa/tasks/T-\d{8}-\d{3}\.json`, `qa/cases/[A-Z]{2,6}-\d{3}(\.c\d+)?\.json`,
  `tests/<slug>/tickets/<n>/filed\.json` (`<slug>` = `\d{4}-\d{2}-\d{2}-[a-z0-9-]+`, `<n>` = `\d+`, the folder must
  already exist and hold `title.txt`);
- refuses a path that any ledger line references as `ref.path` (a hashed record), `qa/ledger/**`, `qa/secrets.env`,
  anything outside `<root>`, and any path whose parent realpath differs from its lexical parent (symlinks);
- for cases uses `O_CREAT|O_EXCL` (never overwrites); tasks and `filed.json` are replaced atomically under
  `fcntl.flock` on `qa/tasks/.lock`: a leftover `.tmp` is unlinked, a new one is created with `O_EXCL|O_NOFOLLOW`
  (never written through a symlink or hard link) and moved over the file with `os.replace`;
- writes canonical JSON (`records.canonical`) and redacts free text with `redact.redact` before writing; a
  `covered_paths` glob or `needs.flags` name that holds an e-mail, token, share link or secret is refused (400), since
  masking it would change what it matches.

Endpoints:
- `POST /api/tasks` `{text, clickup_url?, targets: [tags], priority: high|normal|low, cases?: [ids]}` → 201 task.
  `text` 3–4000 chars; targets ⊆ `/api/meta` tags, non-empty; `clickup_url` must match
  `https://app.clickup.com/t/…` (stored with parsed `task_id` or `cl`); the app never fetches it.
- `POST /api/tasks/<id>/state` `{to, note?}` → 200 task. User transitions only (section 5).
- `POST /api/cases` `{area, title, requirement: {text, source: {type, ref, locator?, requester?}}, targets,
  preconditions: [text], steps: [text], expected, covered_paths: [glob], needs?: {link, flags}}` → 201
  `{id, file}`. New ID = `<AREA>-<n+1>` where `n` is the highest number for that area across `qa/cases/`,
  `qa/runner/tests/cases/` and every case id in the ledger (never reused). Written with `status: "draft"`,
  `author: "user"`, `steps: [{n, text, helper: null, gates: []}]`, `routing: []`, and must pass `validate_case`.
- `PUT /api/cases/<id>` same body plus `base` → 201 `{id, file}`: writes `<id>.c<N+1>.json` with
  `supersedes: {id, file, file_sha256, why}`. `base` is required (400 without it) and must be the `latest_file` of
  `GET /api/cases/<id>` that the form was opened on; when a newer version exists (for example `/qa-run` completed the
  draft meanwhile) the save is refused with 409 and the form asks the user to reload. The status of the previous
  version is kept unless the body sets `status` to `draft` or `retired`. Fields the form does not carry are kept:
  routing, differential, flags_watch, fixtures, a step's helper/assert/gates when its text is unchanged, a
  precondition's id and check, `requirement.source` `cache`, `fetched` and `custom_id` while the requirement text,
  type and ref are unchanged (a locator or requester left empty is removed), and all of `needs` when the body has no
  `needs` (the form sends it only when link or flags changed). An edit that changes the step texts or their order,
  the targets, the requirement text, the expected result or `needs.link`/`needs.flags` of a case that stays active is
  refused (400): it must be saved as `draft`, and `/qa-run` reviews it before it runs again (the Edit form says so).
  Only an active case needs at least one step and a helper or `read_js` on every step (`validate_case`), so a draft
  can be retired.
- `POST /api/tickets/filed` `{dir, cl, replace?: bool}` → 201 `filed.json` = `{cl, by: "user", via: "app", at,
  run_record (from draft.json), history: [previous markers]}`. `cl` must match `CL-\d{4,6}`. An existing marker with
  another CL is replaced only with `replace: true`.
- `POST /api/tickets/file` `{dir}` → 200 `{request, approval_id}` after Touch ID on this Mac (section 8.15); 403
  `{"error": "Not approved: Touch ID was cancelled"}` (and other "Not approved: …" answers), 409 (filed, a file request
  open, a prompt already open, the draft changed during the prompt), 400 (not a draft of a failed CT run), 404.

## 5. Tasks: `qa/tasks/T-<yyyymmdd>-<nnn>.json` (mutable workflow state, not a hashed record)

`{kind: "task", schema: 1, id, created, author: "user", text, clickup: {url, task_id|null, cl|null}|null,
targets: [tags], priority, cases: [ids], state, history: [{at, from, to, by: user|claude, via: app|qa-run, note}],
runs: [run ids], campaign: slug|null, outputs: {summary: rel|null, tickets: [rel], comments: [rel]}}`

States: `queued → running → results_ready → greenlit → drafted → closed`.
- user (app or chat): `queued→closed` (cancel), `results_ready→greenlit`, `results_ready→closed`,
  `greenlit→closed`, `drafted→closed`.
- claude (`/qa-run` via the CLI): `queued→running`, `running→queued` (stopped before any run), `running→results_ready`,
  `greenlit→drafted`, `results_ready→greenlit` only with `--user-words "<the user's words>"` recorded in the note.

CLI (`python3 -I qa/app/tasks.py …`, prints JSON):
`list [--state S]`, `next` (oldest queued by priority high>normal>low then created; exit 3 when none),
`show <id>`, `set <id> <state> --by claude [--note …] [--user-words …]`, `add-run <id> <RUN>` (only while
`running` or `results_ready`: the run set the user greenlights stays fixed), `set-campaign <id> <slug>` (only while
`queued` or `running`), `add-output <id> summary|tickets|comments <rel>`. It uses `writes.Writer` for every write.

## 6. Engine additions the app reads

- Progress: `qa/runner/run.py` writes `qa/runner/state/progress/<lock name>.json` atomically at attempt start, at each
  step, and at the end: `{run, case, target, attempt, step: {n, text, started}|null, steps_total, started, updated,
  state: running|finished, verdict|null, last_shot: rel|null, clock_offset_ms|null}`.
- `/qa-run` skill in the plugin (`skills/qa-run/SKILL.md`, `disable-model-invocation: true`).

## 7. Views

Tabs since 2026-10-22 (user's layout): `#/today` (Today), `#/results` (Results & Tickets: results on top, the
drafts below), `#/history` (History: the case library for now; charts of runs and bugs come in step 3). Old links
`#/tasks` and `#/live` land on Today, `#/tickets` on Results & Tickets, `#/tests` on History, with their query kept.
The Tasks view is removed; the Live view's code stays for the run progress Today will show in step 2. Dark shell bar
with the tabs and the env/account label, light workspace.
Results, per case: first the launch matrix (one row per launch, newest 5 shown and a "Show all" toggle; one column
per device in `devices` then `extra_devices`): each cell a verdict chip that opens the run detail, evidence and stale
badges, the run id, and any older records; a NOT TESTED cell is a grey chip whose title is its `why`. Under each
row a line with `devices_ran`, the concurrency kind and its text; for a parallel launch a "Windows and deciding
times" expand lists each target's window (local clock, ISO in the title), length, deciding-step time and its offset
from the first, plus the common window, overlap, deciding step source and spread. Below it, "By target and build":
the per-target rows grouped by build, with History and the differential block. Copy buttons use
`navigator.clipboard.writeText`. All data goes into the DOM through `textContent` or `createElement`; never
`innerHTML` with data. Rules shown in the UI: engine account only; results first; nothing reaches ClickUp without the
person's Touch ID (the footer, since 2026-11-05: Post comment and File ticket each need the person's press and Touch
ID, 8.15 and 8.16).
Since 2026-11-04 `#/results` shows one test (one Run press) and History opens with 'Your tests' (Ongoing · Tested ·
Done): section 8.17. The per-case launch matrix above is the detailed page behind a row's 'Run records ›'
(`#/results?view=detail`).

## 8. Today: Colleague's office tests from ClickUp (added 2026-10-22, step 1 of the morning plan)

The default view (`#/today`). ClickUp is read when the page opens, on Refresh, and again on a new Europe/Zurich
day; moving between Today's screens re-reads only the saved list, the pre-test notes and the engine cases
(`GET /api/plan`, `/api/cases`), keeping the last ClickUp read's error. Task screens show the same error and
old-list banners as the board. Notes are paired with cases by number, so they are shown only while the test's
`cases_sig` (sha256 of the numbered case texts, stored in the list and copied into the notes by `put_prep`)
still matches, or for older notes while the case count matches. Screens: the board (two columns, Up next and Ongoing, titles only, in
Colleague's priority order; one status line; errors as banners), `?task=<id>` (the task with two buttons, Run and
Details), `&show=details` (summary, dev tickets, each case as a collapsed row with its pre-test note, ClickUp notes
and engine cases) and `&show=run` (what will be tested: cases whose note says ready, with devices, estimate and
what they create; what won't run, with the note's first reason; the device check and start come in step 2).

- `qa/app/clickup_read.py` is the only network client. `GET` only, host `api.clickup.com` only, header
  `Authorization: <CLICKUP_API_TOKEN>` from the workspace secrets file (a personal token the user creates; never
  returned, logged or written; redact masks it everywhere). Errors become `{kind, message}`: `no_token`, `offline`
  (DNS, connection or timeout: "check the internet connection"), `auth` (401/403), `http`, `bad_data`.
- `qa/app/plan.py` does one paged read of `GET /api/v2/team/1919191/task` (assignee Owner 91919191, statuses
  `up next` and `ongoing`, markdown descriptions). Kept: tasks in the folder CT Test Planner (919191919104) whose
  LOCATION (a0000006…) is OFFICE (a0000007…). LOCATION empty or another option: not listed, only counted in
  `hidden: {location_unset, not_office}`. CL-80008 / 0000fake005 is never listed. Order: priority (urgent, high,
  normal, low, none), then ongoing before up next, then newest update.
  Cases are list items at indent 0, numbered in order of appearance (matching the engine's `description bullet N`
  locators); deeper items are notes; items under a `Different HW:` or `Android versions:` line are the device
  matrix; a description with no list is one case. Markers: `field` ("field test"), `drone` ("connected to the
  drone", "only manually", "real drone"), `green` (the user's own green highlight = tested by hand and passed; set
  only where the ClickUp text carries it). Text is plain: images and links reduced to words, URLs dropped.
- Snapshot: `qa/cache/plan/<Europe/Zurich day>.json` (`kind: today_plan`), redacted, replaced on each successful
  refresh through `Writer._write` (kind `plan`). `qa/cache` is never served by `/files/` and is git-ignored.
- `GET /api/plan` returns `{today, snapshot}` (the newest snapshot). `POST /api/plan/refresh` (token as for every
  write) returns `{ok: true, snapshot, today}` or `{ok: false, kind, message, snapshot: <last or null>, today}`.
- The page joins each case with `/api/cases` (the engine case whose `requirement.source.ref` is the ClickUp task and
  whose locator names that bullet; cases linked only through a dev ticket's CL are listed per test) and shows the
  last verdict per device, plus the device states from `/api/live`.
- Tests: `qa/app/tests/test_plan.py` (no network; a fake opener answers every ClickUp call).

### 8.1 Pre-test notes (added 2026-10-22, step 2)

The background engine (skill `wingtra-qa:qa-prep`, plugin 0.3.0) writes per case what can run in the office today.
The app only shows them; it never writes them.

- Files, both written through `Writer._write` (kind `plan`, same path rule as the snapshot):
  `qa/cache/plan/<day>.prep.json` (`kind: today_prep`, schema 1) and `qa/cache/plan/<day>.prep-status.json`
  (`kind: today_prep_status`, `state: running | done | failed`, `at`, `note`).
- `plan.validate_prep` checks every field before a save and names the first bad one: `day` equals the file's day;
  `created` is ISO-8601 UTC; per test `task_id`, `summary`, `dev_tickets` (`cl` is `CL-<n>`, `status`, `env` one of
  develop/staging/preproduction/production, `fix {commits: git hashes, pr, in: {env branch: bool}}`, `note`) and
  `cases`; per case `n`, `status` (`ready`, `needs_input`, `blocked`, `not_office`, `covered`), `why`, `green`,
  `engine_cases` (case ids), `targets` and `missing_devices` (`TA5`, `ET65`, `W044`, `TA3`), `required {env,
  build_note}`, `needs`, `side_effects`, `estimate_min` (1-480), `note`, `proposal`. Texts are 1-300/400
  characters, never links; the result is redacted. CL-80008 / 0000fake005 is refused. A save needs that day's
  snapshot.
- `GET /api/plan` and `POST /api/plan/refresh` add `prep` and `prep_status` for the snapshot's day.
- The page shows a status chip, why, needs, devices (with missing ones), build, what a run would create, a proposed
  case and an estimate under each case, a summary and the dev tickets' fix places per test, and "you marked passed"
  for green cases. It warns only when a test's case count differs from what the notes cover (every app start
  refreshes the list, so comparing times would always warn).
- CLI for the engine (no network except `refresh`, which is the same GET-only read):
  `python3 -I qa/app/plan.py refresh | show [--day D] | status <day> running|done|failed [--note T] |
  put-prep <day> < prep.json`. Errors print `{"error", "status"}` and exit 1.
- Green marks are not in ClickUp's markdown. The skill reads the rich text (`clickup_execute_operator`, model `task`,
  operator `get_many`, `data.content`, Quill ops): a list line whose ops carry `badge-class: green` is green; a
  blue badge marks "(field testing)". Seen on 2026-10-22 in two tests.

### 8.2 Running at login (added 2026-10-22, user OK)

- The server runs as the user LaunchAgent `com.wingtra.qa-app` (`~/Library/LaunchAgents/com.wingtra.qa-app.plist`):
  python.org Python 3.13 `-I qa/app/serve.py --port 8787` in the workspace, `RunAtLoad`, restarted only after a crash
  (`KeepAlive.SuccessfulExit=false`, 30 s throttle), log `~/Library/Logs/wingtra-qa-app.log`.
- `.claude/launch.json` entry `qa-app` attaches the preview pane to `http://localhost:8787` and starts nothing.
- After a change to the app's Python: `launchctl kickstart -k gui/501/com.wingtra.qa-app`. Static files need only
  a page reload. Undo: `launchctl bootout gui/501/com.wingtra.qa-app` and delete the plist.
- The pre-test notes come from the Desktop scheduled task `qa-morning-prep` (weekdays 08:00, app jitter to 08:09),
  which reads `qa/plugin/wingtra-qa/skills/qa-prep/SKILL.md`.

### 8.3 Run from Today: the QA worker (added 2026-10-23, step 2, user OK)

The app asks; the QA worker answers. The worker is a Claude session started by the Desktop scheduled task `qa-worker`
(weekdays 08:15, app jitter to 08:17, Auto mode, workspace root), which follows
`qa/plugin/wingtra-qa/skills/qa-worker/SKILL.md`. It waits in a background `worker.py wait` (no tokens while waiting;
the session is woken when the wait exits), handles one request, and waits again until 19:00.

- Files, written only through `writes.Writer` (kind `worker`): `qa/runner/state/worker/W-<yyyymmdd>-<nnn>.json`
  (`kind: worker_request`: `type` check|start|write, `state` new → claimed → done|failed, new → cancelled, `notes`,
  `result`) and `heartbeat.json` (`state` ready|waking|busy|idle|off, `pid`, `until`, `link` = the worker session's
  `claude://claude.ai/epitaxy/local_…` link). `worker.py wait` holds `.waiter.lock` (one waiting worker).
- Worker state for the app (`worker.status`): busy while a request is claimed (or waking, or idle between requests,
  2 min each); stuck when a claim shows no sign of work for 20 min (no note, no run.py progress of its cases and
  targets since the claim); ready while the heartbeat is "ready", under 30 s old and its pid alive; else off. `wait`
  fails old new checks (30 min) and starts (2 h), and claims this worker left or that are stuck. An end time more than
  12 h ahead (a wait after midnight) becomes 3 h.
- `GET /api/worker`: the state and the requests of the last two days plus every open one.
- `POST /api/worker/check {task_id[, targets]}`: `plan.run_plan` takes today's list and notes (case fingerprint must
  match), the cases whose note is `ready`, their engine cases, targets and the dev tickets' fix commits; one open check
  per ClickUp test. The worker answers per target: connected, account, app (About label, env, fix commits), and
  `needs` (what the person has to do).
- `POST /api/worker/start {check, targets}`: the check must be done, under 30 min old, and every target must have
  passed. The Start is the person's approval: the server creates the qa task (text names the check; `origin: {start,
  check}`) and a start request carrying the cases with their side effects and needs as shown; the worker runs it with
  qa-run's "App mode". One open start per ClickUp test. `tasks.py next` never picks a task with `origin`.
- `POST /api/worker/greenlight {task, outputs}`: `results_ready` → `greenlit` by the user via the app, plus a write
  request; outputs `summary` and/or `comment` (`tests/<campaign>/COMMENT_<id>_<task>.md`, ready to paste). On a task
  already greenlit with no open write request it only asks for the drafts again (Write drafts).
- `POST /api/worker/cancel {id}`: a check or start while new (cancelled), or while stuck (failed, "released"); a
  start's queued task is then closed by the user via the app. Drafts are not cancelled.
- The worker never asks and never runs a gated or handed-back command: such a step ends BLOCKED, and Today shows it
  under "Needs your OK" with the step and reason, to approve in a Claude session in this folder.
- Today: Run overview → Check devices → `?show=check&req=W-…` (polls every 2 s; a table per device with checkboxes;
  Start on the ticked devices, "n of 3 devices") → `?show=progress&req=W-…` (polls every 3 s: notes, run.py progress
  files of the task's cases since the claim, handbacks, verdicts, "Results ready"). Results & Tickets: "Your
  greenlight" card (results_ready tasks with verdicts, outputs checkboxes; greenlit; drafted with Copy comment).
- Tests: `qa/app/tests/test_worker.py`.

### 8.4 History (added 2026-10-23, step 3, user OK)

`#/history` is a board over the last 30 local days, built in the page from existing reads (no server change):
`/api/results` (every run record that stands: superseded runs and harness cases left out; a run counts on the day
it started), `/api/live` (handbacks), `/api/tickets` (ticket drafts and their filed markers) and the hand-kept bug list
`/files/qa/history/bugs.json` (`{kind: "bug_register", schema: 1, updated, bugs: [{cl, title, found, filed, status,
test, parent, devices, build, source}]}`; 404 means none yet).

- Four tiles: runs (NOT_TESTED slots counted apart), passed (PASS over runs with a verdict; harness errors left out),
  steps waiting for the person's OK, bugs filed.
- Runs per day and results per device: inline SVG, stacked in the order PASS, PARTIAL, BLOCKED, FAIL, harness error,
  Wingtra semantic colours (`.hv-*` in app.css; colour-blind separation checked with the dataviz validator), a
  legend on each chart and a native tooltip per segment.
- Bugs: the bug list plus ticket drafts not already in it (by CL), newest first, CL linked to ClickUp.
- What was done: one line per day and campaign with verdict counts, devices and the cases (linked to Results).
- The case library moved to `#/history?show=cases` (and `?case=<id>`, where old `#/tests` links land), titled Cases.

Corrected 2026-11-04: this board is now the old view (`#/history?view=old`). History itself is static/history.js
(since 2026-11-02): the numbers and charts over `GET /api/stats` (views.quality_stats), Bugs and What was done; since
2026-11-04 it opens with 'Your tests' (Ongoing · Tested · Done, GET /api/results/tests, 8.17), which is not tied to
the period links. ERROR reads "Test error" in its legend; a case link of What was done opens the newest test that ran
that case (`#/results?case=<id>`).

### 8.5 Hands-off check (added 2026-10-26, user's goal: fully hands-off once a test starts)

`qa/runner/autonomy.py` decides, as engine code, whether a case can run from Start to its verdict with nobody
touching a device (`python3 -I qa/runner/autonomy.py <CASE…>|--all`).
- Blockers: draft or retired; no steps or a step without an engine action; a `handover` step (a person: a hand-pick,
  a PIN); a `write_list` step (the runner hands it back); `install`/`clickup_write` steps (never hands-off);
  `run.refuse_case` (G3 rules); a real drone or USB radio; a web leg that must choose a file.
- Device checks (confirmed by the device check, never a blocker by themselves): engine account, build, app awake and in
  front, test files on the device.
- `CAN` holds what the engine does on its own; each switch turns on when its piece of the plan is built and tested:
  1 ready the devices, 2 deliver test files, 3 Android file picker, 4 web file chooser, 5 write steps in runs.
- `/api/cases` carries each case's verdict (`hands_off: {ok, blockers, checks, notes}`). `plan.run_plan` keeps only
  ready cases whose engine cases are all hands-off; with none, Check devices and Start are refused with the first
  blocker. Today shows "Hands-off ✓" or "Needs you" per test, and the Run overview lists the cases that would need
  the person under "Won't run today" with the reason.
- The worker's scheduled task retries every hour from 09:15 to 18:15 (2026-11-02; the morning prep is off since 10-29)
  and does nothing when a worker already runs.
- Tests: `qa/app/tests/test_autonomy.py`.

### 8.6 Engine piece 1: the engine readies the tablets (live 2026-10-26, user OK)

`python3 -I qa/runner/act.py <tag> ready` makes a tablet usable without a person, then runs the identity checks:
- Not reachable over USB (`get-state` is not `device`): Fail 2, no input sent.
- Screen not Awake: `KEYCODE_WAKEUP`. Lock screen: `wm dismiss-keyguard`; still locked (PIN, pattern, password): Fail 2.
- App not in front or not running: the launcher intent of `com.wingtra.cloud.app/.MainActivity` (`am start -W`, 40 s).
  It covers the USB-preferences screen, Settings and other apps. A start may apply a downloaded web bundle, so the
  build is read again after ready. Still covered: Fail 2.
- Then waits up to 25 s for the page (`connect`). Every failure carries `ready.{before, actions, after}`.
- Screen state comes from `dumpsys window displays` (a plain `dumpsys window` starts with the last ANR snapshot,
  kept two hours). The app's window is matched by component (`u0 com.wingtra.cloud.app/`), not package name.
- run.py readies each tablet at the start of its attempt: Fail 2 ends BLOCKED (precondition), anything else is a
  harness ERROR. The qa-worker device check and qa-run readiness ready the tablets first.
- `autonomy.CAN["ready_devices"]` is on; the device check `app_in_front` became `no_pin` (the person keeps tablets
  on while charging or without a PIN).
- Tablet setting, done with the user's OK 2026-10-26: `stay_on_while_plugged_in` = 7 on TA5 and ET65 (was 0).
- First real run 2026-10-26: TA5 dozing + lock screen → woke, dismissed, app in front. ET65 dozing + lock screen, app
  not running, Settings behind → woke, dismissed, started the app. Both on `/sites`, staging, CT. Notes and
  screenshots: `tests/2026-10-26-engine-ready/`.
- Tests: `qa/runner/tests/test_ready.py` (16 fake-tablet scenarios). Plugin 0.4.2.

### 8.7 Engine piece 2: the engine delivers the test files (live 2026-10-26, user OK)

`python3 -I qa/runner/act.py <target> deliver <CASE> [--check] [--case-file F]` puts a case's fixtures on a target
(policy in `qa/runner/fixtures.py`):
- Only what a version of the case pins (the newest, or the version run.py names with `--case-file`), and only on a
  target the case runs on (`targets`). The case id must be exact (`[A-Z]{2,10}-\d{3}`) and the record's `id` must match.
- Source: a plain file under `tests/<folder>/files/` or `qa/cases/fixtures/<CASE>/`, no symbolic link, at most 2 GiB,
  named as the fixture, sha256 equal to the case's. Destination: `device_path` under `/sdcard/Download/` (default
  `/sdcard/Download/WingtraQA/<name>`) on a tablet, `~/qa-fixtures/<name>` on webpc044.
- Copy to `<dest>.qa-partial`, sha256 on the target, then `mv -n -T` (tablet) / `mv -n` (webpc044). A different
  file, folder or link at the destination is never overwritten (Fail 2); a failed or timed-out copy removes the
  partial (Fail 3). Every failure carries `deliver.{fixtures, actions}`. `--check` only reads.
- run.py delivers after `ready` and before `probe` when the case has fixtures (exit 2: BLOCKED precondition, otherwise
  a harness ERROR; it also compares `version_file`). The qa-worker device check runs `--check` on the targets each
  case runs on; a missing file is not a `needs`.
- `autonomy.CAN["deliver_fixtures"]` is on: a source that is missing or breaks the rules is a `fixture_source`
  blocker; `fixture_on_device` became the device check `fixture_free`. `case_schema` validates `device_path`.
- The gate lets `act.py ... deliver` pass without asking (the limits above are the safeguard); a raw push to a tablet
  still asks.
- Records: `qa/cases/TERR-004.c2.json` (device_path `/sdcard/Download/WingtraTerrainTests/…`, steps unchanged) and
  `qa/runner/tests/cases/SELFTEST-001.c1.json` (the self-test also delivers
  `qa/cases/fixtures/SELFTEST-001/qa-deliver-selftest.txt`, 254 bytes). Plugin 0.4.3.
- First real run 2026-10-26: SELFTEST-001 missing → copied → already there on TA5, ET65 and W044, with equal
  checksums on all four machines. TERR-004 was already there on TA5 and ET65 and was refused for W044 (a tablet case).
  Notes: `tests/2026-10-26-engine-deliver/`.
- Tests: `qa/runner/tests/test_deliver.py` (122 checks; 20 of 20 deliberate code breaks caught), `test_autonomy.py`.

### 8.8 Engine piece 3: the file pick without a person (live 2026-10-26, user OK)

`act.py <tablet> sys-tap <text> --expect focus:<app|package>` needs no app page (the full-screen file browser pauses
it) and taps only system windows; `sys-tap @storage` taps the internal storage (the tablet's device name). A case
picks a file with plain steps: Show roots, @storage, Download, the case's own folder, the file `--expect focus:app`
(TERR-004 c4 steps 8-12). The file sits alone in `/sdcard/Download/QA-<CASE>/` (piece 2 delivers it there), so no
scrolling. Not the Downloads place: it lists only files the media index marks as downloads, which a delivered file is
not. Dumps remove the old dump file first (no stale screen). `autonomy.CAN["file_picker"]` is on; a step that still
waits for a person blocks. First run: ET65, steps 1-13 hands-off (`tests/2026-10-26-engine-picker/`).

### 8.9 Engine piece 5: write steps in runs under a standing rule (live 2026-10-26, user OK)

`run.standing_step(case, step, eng)`: a write_list step runs in the runner, without a hand-back, only when it is a tap
on Next or Continue, its gates are only write_list and cloud_write, a cloud_write step is listed in cloud_writes, and
the case is active and passes refuse_case (G3: the QA test site, QA_ names, the engine account). run.py then marks that
one act.py call (an env variable only run.py sets; the gate denies shell commands that name it), and act.py lets that
Next/Continue through its request check; the dialog check after the find still refuses Next in the bug reporter and
Continue in the OTA Wi-Fi dialog. Everything else is handed back as before. Also: read_js gets {target} and {run}
filled, scripted asserts see `target` and `run`; `ready` declines the app's own update dialog (Not now); sys-tap
ignores the file browser's path labels. TERR-004 c6 (dataset QA_<run id>, clean start and clean-up) is hands-off:
run R-20261026-ET65-TERR-004-04 PASS (`tests/2026-10-26-terr004-hands-off/`). Test: qa/runner/tests/test_standing.py.
Review 2026-11-03: a scripted assert is evaluated only when it is a plain check (run.assert_problem: subscripts,
literals, comparisons, and/or/not, + and -, any/all/len/sorted/set; no attribute, lambda or :=), since an empty
`__builtins__` is no sandbox (`().__class__...`, a generator's `gi_frame.f_back.f_globals` reach run.py's globals);
anything else is a harness ERROR before eval (qa/runner/tests/test_assert_eval.py). From New test the app also
refuses page JS that may act (worker.NEW_TEST_JS_ACTS), and act.py's write list holds every control that saves the
open flight: Save, Save & exit, Save as draft and Export flight (qa/app/tests/test_review_flight_save.py).

### 8.10 Engine piece 4: the browser's file chooser on W044 (live 2026-10-26, user OK)

`act.py W044 choose-file <sel> --file <name> --expect ...`: finds the control like a tap (write-list and cloud-write
controls refused), turns on Chrome's file-chooser interception, clicks, waits for Page.fileChooserOpened and gives
that input `~/qa-fixtures/<name>` on webpc044 (DOM.setFileInputFiles; deliver puts the file there, Fail 2 if it is
not), then turns interception off. No macOS dialog opens. `autonomy.CAN["web_file_chooser"]` is on; a web step that
taps the drop zone still blocks. TERR-004 c7 (targets both, web step 7) is hands-off. First run: the web leg on W044
up to the file card, no import (`tests/2026-10-26-engine-picker/`). Test: qa/runner/tests/test_choose_file.py.

### 8.11 Watchdog: alerts when the prep or the worker died or never ran (live 2026-10-28, user OK)

`qa/app/watchdog.py`, run outside Claude by the LaunchAgent `com.wingtra.qa-watchdog` (`~/Library/LaunchAgents/`) at
08:45, 10:45, 12:45, 15:00, 17:00, 18:30 and at login; no KeepAlive. It acts only on weekdays 08:45-19:00 Zurich time
and alerts with a macOS notification titled "Hover" when `worker.status()` is `off` (08:45-18:45) or `stuck`;
when the app does not answer on 127.0.0.1:8787; or when the watchdog itself fails. (Until 2026-10-29 it also alerted
about the morning prep; those checks went with the prep, 8.14.) A problem must still be there 10 min later
(the catch-up runs at wake) and alerts once per key (day, problem, its time or request id), plus one "Still:"
reminder after 2 h. Read-only except `qa/runner/state/watchdog.json` (last run, sent alerts, last alert). Log:
`~/Library/Logs/wingtra-qa-watchdog.log`. `--dry-run` prints what it would alert. Launchd path test: create
`qa/runner/state/watchdog.test`, then `launchctl kickstart gui/501/com.wingtra.qa-watchdog`. Off:
`launchctl bootout gui/501/com.wingtra.qa-watchdog`. Plan and reasons: `tests/2026-10-27-qa-status-review/ALERTS_PLAN.md`.
Test: qa/app/tests/test_watchdog.py.

### 8.12 Run form, summary and approved plan (2026-10-28, user OK; design agreed screen by screen on the canvas "QA app Run flow")

Run on a test opens the run overview ("Will be tested, hands-off" / "Won't run today") with a form: the person's
guidance (optional, at most 1000 characters, `writes.GUIDANCE_MAX`, redacted, never in the URL) and a checkbox per
device (TAG_ORDER; a device that is not registered or that no ready case runs on is greyed out; "Web only" / "All").
Prepare summary posts `POST /api/worker/check {task_id, targets, guidance, prepare: true}`. The server keeps only
registered devices the ready cases route to, narrows every case's `targets` to the chosen ones (cases with none
left are dropped, `engine_cases` recomputed) and stores `prepare: true` and `guidance` on the check request. The
worker's answer to a prepare check must hold `summary` (multi-line, at most 1500 characters in 30 lines,
`worker.SUMMARY_MAX`, redacted): what it will test and what counts as a pass, how the guidance is applied or why it
cannot be, what it creates, what is not tested. The summary screen (`?show=check&req=`) shows the summary, the
guidance, tiles (devices, time from the cases' estimates, side effects) and the device table; Run (the existing
start, disabled without a summary on a prepare check) is the approval, Edit goes back to the form filled from that
request (`?show=run&edit=<W-id>`), Check devices again resends the same devices and guidance. Start narrows the
cases to the devices still ticked (cases with none left are dropped, `engine_cases` recomputed) and copies
`guidance` and `plan_summary` from the check into the start request and the task (`t.guidance`, `t.plan_summary`;
the task text says so, naming the guidance only when there is some); the start request's `plan_targets` are the
devices the summary was written for. Guidance never changes what counts as a pass: that stays the case's own. The progress screen shows "The plan you approved", the guidance, "You can leave this page",
a "Results ready (n of 3 devices)" banner and NOT RUN rows for the check's devices the run left out. qa-worker:
guidance is the person's words, followed only within the request's cases and devices (never a gated step, an
install, a log-in, a ClickUp write, another case, device or write); qa-run App mode compares what would run with
`plan_summary` too. Checks without `prepare` (older requests) behave as before. Tests: qa/app/tests/test_worker.py
(PrepareTests).

### 8.13 Test files on the Run form (2026-10-28, user OK; design agreed on the canvas "QA app Run flow")

The Run form has a "Test files" drop box (`qa/app/uploads.py`). A file goes to `tests/test-files-<ClickUp id>/files/<name>`
(a folder act.py deliver accepts as a fixture source) with `tests/test-files-<ClickUp id>/files.json`, the manifest
[{name, size, sha256, added}]. Only .tif, .tiff, .las, .laz and .zip, plain names (the browser turns other characters
into `_`), at most 2 GiB (fixtures.MAX_BYTES), at most 20 per test, and only while 512 MiB of disk stay free. The
browser sends 8 MiB pieces in order: `PUT /api/files/<id>/<name>?offset=&total=` with a raw body
(`security.check_upload`: the same token and Origin as other writes, Content-Type application/octet-stream, at most
16 MiB); the first piece needs the test in today's list or read from its link on New test (8.14); the last piece checks the size, hashes the file and moves it
from `<name>.qa-upload` into place. `GET /api/files/<id>` lists the attached files (only manifest entries still in
place with their size); `POST /api/files/<id>/remove {name}` removes one (or an unfinished upload), never a file the
app did not attach, and is refused while a run of the test is open. Files stay on this Mac and are copied only to the
test devices when a case version pins them by sha256. With files attached, Prepare also offers the test's cases that
need input (`plan.run_plan(..., with_pending=True)` → the check's `pending`, narrowed to the chosen devices) and stores
the files on the check. The worker's answer may then hold `complete` (the pending cases' engine case ids the files complete) and
`plan_files` ([{name, case}], each file on a case it completes and every completed case with a file, validated by `worker._file_plan`); the summary screen lists file → case → devices and
names attached files the plan does not use. Run refuses (409) when a used file was removed or replaced since Prepare, keeps only the completed cases and files of devices still ticked, and adds the completed cases to the start request and the task with
`files`, `plan_files` and `completes`; qa-run App mode completes those draft cases with exactly those files as fixtures and stops if the completed version is not hands-off. Unfinished uploads untouched for 2 h are swept; free space is checked on every piece; leaving the form stops its uploads; a file dropped anywhere on the form is attached.
The progress screen names the files. Tests: qa/app/tests/test_uploads.py.

### 8.14 Simple flow (2026-10-29, user OK; screens S1 "New test" and S2 "Plan" agreed on the canvas "QA app Run flow")

New test (`#/new`) is the home page. The person drops or pastes a ClickUp test link, writes what to test, may attach
test files, ticks devices and presses Prepare plan; the QA worker answers with a plan; the person presses Run (or
Edit). No morning list, no Today statuses (ready / needs input / covered), no prep: their screens and the prep's
watchdog checks are gone; the Today code stays, and a check without `intake` behaves as in 8.3 and 8.12. Running and
Results stayed as they were then; since 2026-11-04 Run lands on Results, which shows that one test, live while it runs
(8.17), and New test lists 'Your plans' only.
- `POST /api/intake/resolve {url}` (token and Origin as for every POST): a task link, a bare task id or a custom id
  like CL-80002 (`plan.task_ref`). `plan.resolve_intake` reads that one task (GET only, `clickup_read`; a custom id
  with `custom_task_ids=true` and `team_id` = `plan.TEAM_ID`), refuses `plan.NEVER_READ` (CL-80008, 0000fake005) with
  400 before any request (and keeps nothing when ClickUp answers with one of them), saves a redacted copy at
  `qa/cache/intake/<task id>.json` (`kind: intake`, Writer kind "intake") and answers 200 `{task_id, custom_id, title,
  status, priority, url, list, cases: [{n, text}]}` (cases parsed as for the Today list; may be empty). No token: 409
  with the no-token message; a ClickUp error: 502 with its reason; a link that names no task: 400.
- `POST /api/worker/check {intake: true, task_id, guidance, targets, prepare: true}`: `guidance` 1-1000 characters
  (required), `targets` a non-empty subset of `writer.target_tags()`; 409 "open the test link again" without the cache
  file. The check carries `intake`, `prepare`, `clickup {task_id, custom_id, title, url, priority, status}`,
  `clickup_cases`, `guidance`, `targets`, `files` (the attached files) and empty `cases`, `engine_cases`,
  `fix_commits`, `case_versions`; one open check per task. A body without `intake` is the Today check, unchanged.
- The worker's answer (`worker.validate_check_result`): the device rows, `note`, `summary` (required) and `plan_cases`,
  0-10 `{id, file, sha256, new, targets, text}`: each the newest version under `qa/cases` (`plan.case_versions`) with
  that sha256, hands-off (`autonomy.check_id`), on some of the request's devices (a tablet case on tablets, a web case
  on W044), ids unique, `text` (what it checks) at most 200 characters; optional `plan_files` [{name, case}] (an
  attached file, a case in `plan_cases`); no `complete`. An empty `plan_cases` is allowed: the summary says what is
  missing and Run stays off.
- Optional `not_covered` (2026-11-03, the person's request after the offline-sites run that left the flight a draft and
  still said PASS for the rest): at most 10 texts of 1-120 characters (whitespace collapsed, redacted, each once), the
  parts of the person's description the plan does not test, in their words; stored as `result.not_covered` ([] when
  absent), read by the Plan screen from `GET /api/worker`. A check without `intake` refuses a non-empty one. On the
  Plan screen a non-empty list shows a warning above Run, "Not covered by this run:" with one line per text, and Run
  reads "Run anyway on <devices> (n of 3 devices)" (the one primary button; the person can still run). A plan without a
  case keeps "Nothing to run" and shows the same box (review 2026-11-03: the screen shows what the worker sent).
- Optional `needs` (2026-11-04, step 1 of "Needs your input", the person's OK): what the worker needs from the person
  to plan the rest (a file, a choice between ways to test, an unclear requirement), where an answer would unblock what
  it would otherwise leave out; `not_covered` keeps what no answer can fix. At most 8 `{id, kind, text, blocking,
  options[, accept]}`, the most important first (`worker._needs`): `id` N1 to N8, each once; `kind` choice, file or
  text; `text` 1-300 characters (whitespace collapsed, redacted), what is needed and why; `blocking` true when the plan
  cannot test that part without the answer; `options` at most 4 `{id, label}` (`id` `^[a-z][a-z0-9_-]{0,19}$`, each
  once in the need; `label` 1-40 characters), 2 to 4 for a choice, e.g. `make` "Make one for me" or `skip` "Skip this
  part" on a file or text need; `accept`, required for a file need and refused on the others, a non-empty list of
  extensions from `uploads.EXTENSIONS`, each once. Other keys are dropped; absent or null is []; a check without
  `intake` refuses a non-empty one ("needs: only for a test from New test"). Stored as `result.needs`, read by the Plan
  screen from `GET /api/worker`: a "Needs your input" card under "What I will do", one row per need (buttons, a file
  of the test or "Attach a file", a text box) and Send answers; Run stays off while a blocking need is open ("Answer
  the questions first").
- `POST /api/worker/answer {check, answers}` (token, Origin, JSON and 64 KiB as for every POST): Send answers. In this
  order: 404 "no such plan" (no such request, or not a check); 409 "this plan is not finished" (not intake, or not
  done); 400 "this plan has no questions"; 409 "already answered; open the newer plan" when a request that is not
  cancelled or failed `follows` it (a follow-up the person cancelled, or one that failed before any plan came from it,
  used no answer, so the plan can be answered again); 409 "a newer plan of this test exists; open it" when a later
  intake check of the same test that does not follow it and is not cancelled or failed exists (Prepare again or Edit
  replaced the plan; review 2026-11-04); 409 "open the test link again" without the intake cache file; 400
  for the answers (`worker.validate_answers`): 1 to 8 `{need, choice | text | file}`, each a need of the plan, once,
  with exactly one value (null counts as absent; other keys are dropped): `choice` an option id of that need, `text`
  only for a text need, 1-1000 characters (line breaks kept, runs of spaces collapsed, redacted), `file` only for a
  file need, `{name, sha256}` of a file attached to the test now with that sha256 and an extension the need accepts.
  Then 201 and the follow-up check request: the fields of an intake check (`serve.intake_fields`, the same as Prepare
  plan) with `clickup`, `clickup_cases`, `guidance` and `targets` copied from the answered check and `files` the test's
  attached files now, plus `follows` (the answered check's id) and `answers`; one open check per test (409). The page
  moves to the new plan as Prepare again does. The waiting worker takes it like any new check, reads the old plan
  (`worker.py show <follows>`), applies the answers (skill qa-worker, section 1b) and answers with a new plan, which
  may hold new needs; answering those makes the next follow-up. Answer errors are plain words the page shows as
  they come ("dsm.tif changed after you chose it. Choose it again, then send.").
- A follow-up plan may copy the answered plan's device rows instead of checking the devices again: the worker then
  answers with `devices_copied: true`, `worker.validate_check_result` checks that every row equals that plan's row for
  the device (400 "devices_copied: copy the rows exactly from the plan this one follows" otherwise) and keeps that
  plan's `checked_at`, so the 30-minute Run window counts from the real device check, also along a chain.
- Prepare again on an answered plan (one with `follows` and `answers`) sends `answers_from: <its id>`: the new intake
  check carries the same `follows` and `answers`, checked again against the first plan's needs and the files attached
  now (409 "your answers no longer fit (…); answer the first plan again" when, say, an answered file was removed; 400
  "answers_from: an answered plan of this test" for anything else), so the worker does not ask them again.
- The page: a cancelled or failed follow-up does not count as answered (as on the server); its answers come back on
  the plan, with "Your last answers did not reach a plan. Send them again." A plan replaced by a newer plan of the
  test says so and links to it. The files box in the card has no Remove button (removing a file the plan uses makes
  Start refuse). "Your plans" ("Your tests" until 2026-11-04, 8.17) labels a plan that waits for answers "Needs your
  input". Whether a case is `new` or
  `changed_in_plan` is measured from the first plan of the chain (`worker.first_plan_created`).
- `POST /api/worker/start {check, targets}` on an intake check whose `result.needs` holds a need with `blocking: true`:
  409 "answer the questions on the plan first" (`worker.blocking_needs`, right after the check is found done and fresh,
  before the device, file and case checks and `start_run`). Non-blocking needs leave Run as it is. The answered plan
  keeps waiting; its follow-up plan runs.
- `POST /api/worker/start {check, targets}` on an intake check runs the `plan_cases` with a ticked device, each
  narrowed to the ticked devices (a ticked device no case runs on stays out); 400 if none; 409 "case <id> changed since
  the plan; prepare again" when a ticked case's newest file or sha256 changed since the answer; freshness, attached
  files and run-once as in 8.3, 8.12 and 8.13. The start request and the task carry `intake: true`, `engine_cases`,
  `case_versions` (id, file, sha256 of `plan_cases`), `plan_cases`, `files`, `plan_files`, `guidance`, `plan_summary`,
  `plan_targets`; the request's `cases` are [{n: null, text, engine_cases: [id], targets, side_effects: [], needs: []}]
  (the task's `cases` are the engine case ids, as always). Task text: "Run from New test: <title>. Cases <ids> on
  <devices>. Started by the user in the app after plan <check id> with the description and plan they approved."
  qa-run runs those cases exactly as pinned, with no completion step.
- Rollout (go-live with the person's OK): the "QA morning prep" scheduled task is disabled, the plugin is updated
  (qa/plugin/INSTALL.md) and the QA worker restarted so it reads section 1b.
- Test files (8.13) are also taken for a task with an intake cache file.
- The worker (skill qa-worker, "Intake check") uses an existing engine case for the task that fits the description or
  writes a new one from the person's words, never more than the person described. The person's words decide what a
  new case checks; for an existing case they never change its pass criterion. A case that cannot run hands-off stays
  out of `plan_cases` and the summary says exactly what is missing.
- Routes: `#/new` (S1), `#/new?show=plan&req=<check id>` (S2), `#/new?show=progress&req=<start id>` (running and
  finished, the existing progress screen), `#/new?edit=<check id>` (S1 refilled from that check: link, description,
  devices). Tests: qa/app/tests/test_intake.py, test_watchdog.py, test_needs.py (needs, answers, the follow-up,
  devices_copied, answers_from and the Start refusal), test_needs_page.py with needs_page_harness.js (the card, the
  answers and Run in macOS JavaScriptCore with a fake DOM).

### 8.15 File ticket: the person's OK is one press plus Touch ID (2026-11-03, the person's request)

- A failed run gets a Swiss Army Knife draft (bug-draft) and the run goes on. Each draft on a Results card carries
  `state`: `draft`, `waiting_online` (the run's device showed the app offline at ready, `offline: true`, or the last
  file request failed with "Connect <tag> to the internet, then press File ticket again."), `approved` (Touch ID given,
  the worker has not taken it), `filing` (the worker took it), `filed` (`submitted.json`, or the person's `filed.json`;
  `cl_id`) or `failed` (`error`: the last file request's note); `request` `{id, state}` of the open file request and
  `target` (where it files). A new file request older than 30 minutes shows `failed` (its approval expired).
- `POST /api/tickets/file` `{dir}`: the draft folder `tests/<campaign>/tickets/<n>` (no symlink on the way, title.txt,
  description.txt, draft.json) whose `run_record` is a FAIL, or a PARTIAL or BLOCKED run with a failed check, on the
  verified engine account (`account` in engine.json; a correction `<id>c1` counts as it says); the target is the run's tag (TA5, ET65 or W044). Then
  `approvals.approve` shows the system prompt "File the ticket '<title, 60 chars>' with the Swiss Army Knife on <tag>"
  (`touchid.swift`, `LAContext.evaluatePolicy(.deviceOwnerAuthentication)`, so Touch ID or the account password; built
  once with `/usr/bin/swiftc` into `qa/app/.touchid`, run with a 90 s timeout; one prompt at a time). Only on a yes it
  writes `qa/runner/state/approvals/<id>.json` = `{id: A-<YYYYMMDDHHMMSS>-<6 hex>, ticket_dir, draft_sha256, target,
  created, expires (+30 min), by: "touch_id", sig}` (0600), signed with HMAC-SHA256 under the 32 bytes of
  `qa/runner/state/approval.key` (64 hex characters, 0600, made on first use) over the compact sorted JSON of the other
  fields; `draft_sha256` = sha256(title.txt + "\n" + description.txt). Then the worker request `{type: "file",
  ticket_dir, target, approval_id, task, run}`.
- The worker checks the device is online (`act.py <tag> ready`, `offline`), fails the request with the Connect note when
  it is not, else files with sak-submit (each act.py tap prefixed `QA_TICKET=<ticket_dir>`), writes `submitted.json`
  and answers done (`worker.py done` refuses a file request without `submitted.json`). The gate lets the final Next
  pass only with an unused, unexpired approval whose signature and draft hash hold, and writes `<id>.used`.
  Tests: qa/app/tests/test_ticket_filing.py.
- Security review (same day): the route also needs `draft.json` `finding: confirmed` (400 before the prompt, as
  sak-submit checks); a new approval writes `.used` (`by: superseded`) for every older unused approval of the same
  ticket, so a press after a failed request leaves one approval; the folder and approval-id patterns are ASCII-only, as
  the gate's. At the filing Next, act.py (with `QA_TICKET`) reads the reporter's title and description and taps only
  when they equal title.txt and description.txt, so the approval never carries hand-typed text. The gate denies writes
  of `qa/app/.touchid` and File ticket from a shell or a connector, asks on writes of `touchid.swift` and
  `qa/app/__pycache__/`, on act.py JS that clicks, submits, sends or sets a value, and on sweeps over `runner/state`,
  and passes no approval for a ticket with `submitted.json` or `filed.json`. Limit: the gate reads text only, so a
  script file run as this user can still read the key and sign an approval; only an OS boundary (a key the model's
  processes cannot read) closes that. Tests: qa/app/tests/test_ticket_security.py,
  qa/runner/tests/test_ticket_form.py, the `tickets_review` rows of qa/runner/tests/gate_fixtures_tickets.jsonl.

### 8.16 Results card actions, server and worker side (2026-11-03, the person's request)

After Run the app shows the Results page: running tests with live progress, then the normal card. Each card has
Report issue and Add result; the comment is posted with Post comment and Touch ID (the person's choice).
Since 2026-11-04 they sit on the one-test page (8.17): Report issue and File ticket on the case rows, Add result and
Post comment in the result box, once per test. The routes, bodies, checks and Touch ID below are unchanged.
- `GET /api/results/cards` adds `running`: `[{task, title, cases, started, devices: [{tag, state, step, of, text,
  shot_url, updated, case, run, verdict}], state, request: {id, state}|null, note}]`, newest first: a task whose start
  request is still open (new or claimed; `started` = the Run press), and a task /qa-run runs from a chat (state
  `running`, no origin). A Run that ended before results (start failed or cancelled) and a task queued in a chat are
  not listed. A device's row is the newest run.py progress file of that target for one of the task's cases started
  since the worker took the request (the data the New test progress view reads): `state` `waiting` (nothing yet),
  `running` (step n of `of`, its text, the last screenshot's `/files/` URL) or `finished` (`step` = `of`, `verdict`).
- Each card adds `report: {enabled, why}`: enabled when a device verdict is failed, failed (suspected) or partly
  passed; else `why` in plain words (`views.REPORT_WHY`). And `comment: {state, text, sha256, posted_at, error,
  clickup_url}`: `none`; `drafting` (a write request with `comment` is open); `draft` (text: the first
  `outputs.comments` entry that is this task's `tests/<campaign>/COMMENT_<ClickUp id>_<task>.md`, else the first entry;
  `outputs.comment_file` names the same file; line ends unified, stripped and
  redacted, at most 1000 characters; `sha256` of it: what the person reads is what is posted); `posting` (this server
  is posting it: Touch ID open or the POST on its way); `posted` (`COMMENT_<ClickUp id>_<task>.posted.json` exists,
  `posted_at`); `failed` (`error`: the draft cannot be posted, empty, over 1000, another file name, a link, a ClickUp
  token in it (masked in `text`); or the last write request failed, its note; never a `sha256`, so the page offers Add
  result, not Post comment). `clickup_url` = `https://app.clickup.com/t/<task id>` for a bare task id. Tickets
  carry `run`, and a run with a `draft` request but no draft folder yet gets an entry with `dir`, `n` and `title` empty:
  `state` `drafting` while the request is open, `failed` with `error` (the worker's note) when it failed.
- `POST /api/tickets/draft {task, case}` (Report issue): the card's runs (`views.card_entries`), newest first; the
  first that `serve.run_fileable` accepts (the rule File ticket applies: a FAIL, or a PARTIAL or BLOCKED run with a
  failed check, on the verified engine account, on TA5, ET65 or W044) and that has no draft folder → worker request
  `{type: "draft", task, case, run}` → 200 `{request}`. 409: a draft request of the card is open, or every such run has
  a draft; 400: bad body, nothing failed, or the failed runs cannot be filed (the reason); 404: no such card.
  `writes.Writer.create_draft_request` checks both 409 rules again under the lock. The worker drafts with bug-draft in
  Owner's voice (qa-worker section 5); `worker.py done` refuses a draft request until
  `worker.draft_dir_for_run(run)` finds `tests/<campaign>/tickets/<n>` with title.txt, description.txt and draft.json
  whose `run_record` names the run (or its correction). A draft request can be cancelled like a file request.
- `POST /api/results/comment {task}` (Add result): 409 when a comment is drafted (no problem) or posted, and
  (`Writer.request_comment`, under the lock) while the results are not in (queued, running), for a closed task, a task
  with no ClickUp link, or an open write request; else a results_ready task is greenlit by the press (greenlight with
  outputs `["comment"]`, note "outputs: comment"), a greenlit task asks again, and a drafted task gets the request and
  stays drafted → 200 `{request}`. A draft that cannot be posted (too long, empty) may be asked for again.
- `POST /api/results/comment/post {task, sha256}` (Post comment): one Touch ID prompt at a time, shared with File ticket
  (`srv.filing`; the 409 names the open prompt). Before the prompt: the comment exists, is not posted (no marker, not
  posted by this server run), has no problem, its text has this sha256; the ClickUp task comes from the records only
  (`clickup_post.comment_target`: the task's `clickup.task_id`, or for a custom-id link the New test copy in
  `qa/cache/intake`; a bare id `[a-z0-9]{4,40}`, never `plan.NEVER_READ`, the same as its plan request's and the copy's);
  a token is there; the Writer would write the marker. Then `approvals.confirm("Post the results comment to '<ClickUp
  title, 60>'")` (403 "Not approved: ..."), the same checks again (409 when the text changed during the prompt), then
  `clickup_post.post_comment`: one `POST https://api.clickup.com/api/v2/task/<id>/comment` `{"comment_text": text,
  "notify_all": false}` with the token from `qa/secrets.env` (read as clickup_read reads it), 15 s, no redirect
  followed; a text holding a ClickUp token is never sent. A 2xx is posted, also when its body was lost after the status
  (`comment_id` null): the Writer creates the marker `{comment_id, posted_at, sha256, task_id}` (O_EXCL, never
  replaced) → 200 `{posted_at, comment_id}`. ClickUp refused or did not answer (a broken status line too) → 502 with its
  plain message (the token masked), nothing written. A post whose marker then fails to write answers 500 and stays
  refused while the server runs.
- `clickup_post.py` is the app's only ClickUp write. The gate denies the route `api/results/comment/post` and the module
  name `clickup_post` to every shell line that is not a plain reader and to every connector or browser tool, like File
  ticket (rows of qa/runner/tests/gate_fixtures_comments.jsonl); `gate_hook.py`'s fallback asks on both. Review
  (2026-11-03): any path below `api/results/comment/`, the routes with URL dot segments (`/./`, `x/../`, which curl and
  browsers drop), the poster as an attribute (`serve.clickup_post`), and ClickUp's own comment endpoints
  (`api.clickup.com` with `/comment`) count too. The gate reads text only: a name or URL built at run time is still out
  of its reach, so the person's Touch ID stays the guard of the route.
- Results page (static/results.js): Post comment shows only for a `draft` with its `sha256` and sends that sha256; the
  text shown is the server's text (a draft is never cut, so the file is not read again); only a `failed` text cut at
  1000 is read whole from its file, to read. Add result on a `failed` comment asks for a new one. A custom-id-only
  ClickUp link counts for Add result; a failed draft request shows the worker's note on load.
- Tests: qa/app/tests/test_result_actions.py, qa/runner/tests/test_gate.py.

### 8.17 Results: one test; History: your tests (2026-11-04, the person's OK "Yes, build it")

The person found the Results page confusing: twelve cards of several tests, titles that were the ClickUp name, "Not
decided" with a question mark, and buttons on every card. The approved design (tests/2026-11-04-results-redesign/
SPEC.md; the JSON both sides use: API.md there, with its Deviations notes) shows one test per page, moves every other
test to History, and writes new cases as at most 8 parts that quote the task. Nothing moves to Done by age; qa/runner
(the frozen engine) is unchanged.

- **The page** (`#/results`, static/results.js): one test, which is one Run press (one qa task). `#/results` shows the
  newest test by Run press (`?task=current`), `?task=T-…` that test, `?case=X` X's newest run: the newest test that
  ran X with X's row open, unless X's newest run from a chat (no test) started after that test's newest run of X,
  then that chat run's case alone (2026-11-05, wingmate-debug FINDINGS #1: a failure never hides behind an older
  pass). After the first answer the page asks only for that test (`?task=<id>`; a chat run `?case=X`, whose ticket
  watch stops and says so when another run of X takes its place). Top line: 'N other tests wait for you · History ›'
  and, rarely, 'Also running: <title> ›'. Header: the title (at most 2 lines), a grey line ('ClickUp: <name> ↗', the
  devices, 'n of 3 devices (<missing>: <why>)', the build, 'Run HH:MM → results HH:MM') and the fold 'What you asked
  and the plan' (the description, the plan summary, what the plan does not cover). The box: the worst result in one
  sentence ('Test error in 5 of 5 cases, on TA5 and ET65'), the fixed meaning of its word, 'Hover: ' and the start's
  done note (PASS, FAIL and ERROR in words; it ends ' (written before the correction of <day>)' when a run was
  corrected after it), how a test in Done ended, and the buttons. One row per case: the case title, one chip per
  device and, when it did not pass, one line of Hover's why (its leading verdict clause cut). A row opens in place:
  'What it checks' (the plan's line, without a case id), the parts or check steps per device (✓ as expected, ✗ not
  as expected, ⚒ the test stopped here, – not reached), What happened, Why and Not tested, the shots, each device's
  own run time, the ticket part (for a failure no ticket is filed for, 'Report issue: <why>.' instead) and 'Run
  records ›' (the detailed page). A row opens by itself where a device failed or partly passed, or where a ticket
  draft waits for File ticket. No test at all: 'No results yet' with links to New test and History; a case without an
  engine run: 'No engine run of X. Its back-test records, if any, are in Run records ›'. Gone: the 12/60 cards and
  'Older results', the compact Not decided card, '2 runs, same result', 'run took', case ids and c0/c1 labels, the
  greyed Report issue line, the '?' icon. The footer reads 'Engine account only · results first · nothing reaches
  ClickUp without your Touch ID'.
- **While it runs** (ongoing: its start request is new or claimed, whatever the task's state, so the result box comes
  with Hover's done note; or a chat's /qa-run): the box is live and looks again every 4 s: each device's step
  ('Step 3 of 6: <part>' for a case with parts, else 'Step 57 of 109: <step>') and newest screenshot, 'Waiting for
  Hover', 'Hover is off' or '<tag> waits for your OK at step n'; the case rows say done, running or waiting, '–' on
  a device the plan does not run that case on (running.case_rows null, from the start's cases[].targets), and get
  their verdicts when the test ends. Then the box becomes the result box in place; a test that ended without results
  (404) says so.
- **The title rule** (`views.test_title`, one function for the header, History, the live box and also_running): the
  plan's title (task.title, the start request's, the check's result.title; written by Hover, 1-80 characters,
  shown on the Plan screen where Run approves it); else, for a test of one case, that case's title (the version that
  ran); else, for a New test task only, the description cut (`views.description_title`: list marks, asides in
  parentheses and label lines ending in ':' dropped; one line whole; several lines cut at 48 characters on a word and
  joined with ' · ', whole parts only, '…' when some are left out); else the ClickUp name; else the task id. 160
  characters in the header, 90 in lists. A Today task's guidance is never its title. The ClickUp name is never the
  title; it is the grey line. GET /api/worker titles a start request that has a task by `views.test_title`, as
  Results and History do (2026-11-05, FINDINGS #13: one name everywhere), and every other New test request by
  `views.request_title` (the plan title, else the description cut, else the ClickUp name); 'Your plans' shows it.
- **History's 'Your tests'** (static/history.js, first card, not tied to the period links; GET /api/results/tests,
  `views.tests`): Ongoing ('Waiting for Hover', 'Running: TA5 step 3 of 6', 'Running' between two cases, '<tag>
  waits for your OK at step n'), Tested (results in, not Done; newest change first; 'Stopped early' when the start
  failed, was cancelled or expired after some runs; each row 'Next: <press> ›') and Done. **The Done rule**: its
  comment is posted ('Posted <date> ↗'), or the person pressed Mark done ('Marked done'), or it was replaced: a newer
  test of the same ClickUp task has results, ran every (case, device) this one ran, and every failure of this one
  failed again there ('Replaced by the <date> run', 'Failed again in the <date> run' when it had failures; the newest
  such test is named, and a replaced test stays replaced). So a failure never leaves view by itself. Left out: queued
  tasks with no run and no open start, and chat runs (they stay in What was done). Tested and Done show 5 rows, then
  '<n> more ▾ (<k> of them failed and are not reported)' and 'Show all <n> ▾', counting the rows sent (at most 100 a
  group; a failure no ticket can be filed for is not counted, below). Each row opens `#/results?task=`.
- **Verdict words**: the code not_decided (an engine ERROR) reads 'Test error' (chips), 'test error' in a sentence and
  'test errors' after a count above 1, with the meaning 'The test itself stopped before it could check the app. This
  says nothing about the app.' and a hatched badge with a wrench; History's legend reads 'Test error (the test itself
  stopped)'. History's charts say 'test error(s)' for those runs too (the day titles, the week table and its
  tooltip, the causes card; 2026-11-05, the person's decision on FINDINGS #30); 'decided' stays for the decided rate.
- **The buttons**: one Sun Orange button, the first that applies of File ticket > Post comment > Report issue > Plan
  again > Add result; every other button is secondary. The page works it out from the buttons it shows, so it stays
  right after a press; the server's `test.next` (and History's 'Next:') follow the same order. One filing rule
  (2026-11-05, FINDINGS #3 and #8): a run may be filed when its newest record still failed (`views.run_failed`) and
  it ran on the verified engine account and a filing device (`views.file_rule`); `serve.run_fileable`, File ticket,
  Report issue, the next press and the unreported counts all use it. Each ticket carries `fileable` and
  `fileable_why`: File ticket shows only for a draft that may be filed, else its why ('Not filed: its run was
  corrected to a test error, so there is nothing to file.'), and such a draft is never the orange button. A failure
  that may not be filed (it ran on the earlier engine account shared-stag, say) has no Report issue: its row says
  'Report issue: ran on the earlier engine account shared-stag; not filed.' (`report.why`), and it is neither the
  next press nor counted as not reported. Report issue and File ticket sit on the rows where a device failed or partly
  passed; Add result and Post comment sit in the box, once per test. Their routes, bodies, checks, refusals and Touch
  ID are those of 8.15 and 8.16: Post comment sends the sha256 of the text the person read (a press on a folded
  comment first opens it), and File ticket asks for Touch ID, then a file request. Add result shows only while the
  task is results_ready, greenlit or drafted and its comment is none, failed or drafting (the server refuses it
  otherwise); it is off, without the orange look, while a write request of the test is new or claimed
  (`test.open_write`, FINDINGS #31). A refusal shown under a part goes once the draft or comment it was about
  arrives. After Post comment the page reads the test again, so the test shows in Done at once. `views.card_entries`
  looks inside the task, so Report issue works on a test opened from History. A test in Done shows no orange button,
  no Plan again, no Report issue, no Add result and no Mark done; a drafted ticket of it still shows with File ticket.
- **Mark done…** (the box, a test in Tested): it asks inline 'It moves to Done in History; Add result is off after
  this.' (writes.py refuses Add result on a closed task, and tasks.py has no way back), then sends POST
  /api/tasks/<id>/state {to: closed} and reads the test again. Off, with why, while the test runs, while a write,
  draft or file request of it is open (a file approval that expired is no open work), while its comment is being
  posted, and for a task in a state the person cannot close (they can close queued, results_ready, greenlit and
  drafted: `views.CLOSE_STATES`, tasks.TRANSITIONS). The state route refuses the same with 409 (FINDINGS #28), so a
  stale second tab cannot close a test while its comment is written. A start that ends failed, cancelled or expired
  while its task is still running moves the task on (FINDINGS #9, `writes.Writer.settle_started_task`, by Hover in
  worker.py fail and expire, by the person in the app's Release): with runs recorded to results_ready, so Mark done
  works, else back to queued. A task started from a chat (no start request) stays as it is.
- **Plan again** (a New test task where no case gave an app result, not in Done): it sends the Plan screen's Prepare
  again body (`test.plan_again`: the description, the devices and, for an answered plan, `answers_from`) unchanged to
  POST /api/worker/check, then opens the new plan (`#/new?show=plan&req=<id>`). Nothing runs before Run. A newer plan
  of the same ClickUp task (`test.plan_again.newer` {id, fresh}, FINDINGS #21; fresh while Hover makes it or its
  device check is at most 30 minutes old): while it is fresh, Plan again is not the next press and the box links 'A
  newer plan of this test exists ›' in its place; once it expired, Plan again shows as a secondary button. Plan again
  and Prepare again carry over each earlier answer that still fits and drop the others with their reason
  (`answers_dropped` [{need, why}], FINDINGS #14), so Hover asks only those again.
- **New test**: 'Your tests' became 'Your plans' (plan rows only, titled by the server, the ClickUp name in grey; a
  Running row opens Results, a 'Run stopped' row the run screen), plus 'Tests you ran: n wait for you in History ›';
  a cancelled newer check no longer hides the plan before it. The Plan screen shows the plan's title on top with the
  ClickUp line under it; each case lists its parts (checks marked, the quoted words shown) or, for a case written
  before the rule, its check steps, of the version the plan pinned ('This plan runs an earlier version of this case;
  a newer one exists.' when there is a newer one); 'All engine steps (n)' folds away with the expected result. A plan
  Hover never took says so when it expires ('Hover did not take this plan within 30 minutes (it may be off). Press
  Prepare again once Hover runs.'). Run records keeps its own view when a filter changes or a run closes, and shows
  the records before the case list for its filters arrives.
- **Parts** ("Steps from the task", qa-worker 1b step 5; from 2026-11-05, `case_schema.PARTS_FROM`): a case written or
  rewritten from New test lists 1-8 parts `{n, text (1-100), quote (a text or a list of texts), check}` before its
  steps; every step but `always: true` clean-up names its `part`; only a check part's steps read the page (`read_js`,
  an act.py read) or assert. `case_schema.validate_case` refuses it for an active case created from PARTS_FROM whose
  requirement source is user_chat with a locator starting "New test description" when its steps differ from the
  version it supersedes or it carries `parts` or a step `part` (FINDINGS #7; a title-only edit stays unchecked).
  Beyond SPEC's list it also refuses a part whose n is not its place, a clean-up step with a part or one that reads or
  asserts, and counts an act.py read as a read. `worker._intake_plan` runs `validate_case(case, for_new_test=True)` on
  each case new or changed in the plan, whatever its requirement source (FINDINGS #6: a rewrite of a ClickUp-sourced
  case too), then checks each quote against the description, the ClickUp text (qa/cache/intake/<task>.json) and the
  answered questions (case, spaces and punctuation ignored); a refusal names the case and the part. It also refuses a
  check part that quotes a ClickUp item with lines under it when no part of the case quotes one of those lines, and
  names them sentence by sentence ('quote the result lines this part checks; the others go to not_covered';
  FINDINGS #4, `worker._heading_problem`; the lines under an item that ends in 'e.g.', 'for example' or 'such as' are
  examples, not results). Then the app appends to the plan's `not_covered`, in the task's words, every task line no
  part quotes (FINDINGS #2, `worker.uncovered_task_lines`, for a plan whose cases all have parts: each sentence of a
  ClickUp item a part carries out and of the lines under it, and each line of the description that is also a
  ClickUp line; images, links, labels, placeholders, short '?' prompts and bare platform words are skipped; cut at
  120 characters with '…', at most 10 added, never a refusal). From New test a Confirm tap (`text:`/`aria:Confirm`,
  `sys-tap Confirm`, the Edit flight Confirm's `testid:none`) is a cloud write whatever its gates and is refused
  (FINDINGS #5, `worker.new_test_write_problem`); copied device rows (`devices_copied`) may be at most 20 minutes old
  (FINDINGS #15). The plan's optional `title` (1-80 characters) is kept and copied to the start request and the task
  at Start. Playbooks give blocks to copy (a start block that leaves 'Exit without saving' and 'Do you want to
  exit?', a clean-up block, selectors), never whole cases, cite the case file and step text they come from, never
  wait right before a tap for its own target (a longer wait is the tap's `--find-timeout`), and say what is known not
  to work.
- **Go-live** (with the person's OK, one session): the app, the playbooks and plugin 0.4.20 (qa-worker and qa-run;
  the Hover name of 2026-11-06 comes with them) go live together; run the update commands in qa/plugin/INSTALL.md and
  message the day's worker session. The plugin must not come later than the app: the app alone refuses, from
  PARTS_FROM on, every New test case written without parts. If the plugin is installed after 2026-11-05, set
  `case_schema.PARTS_FROM` to the day it is installed.
- Not built (open): the Plan screen folds only the engine steps, not each case; a task started from a chat and left
  in running has no way out by hand; a quote is any text found in the task, and a quote that holds a whole result
  line counts each of its sentences as checked; a not_covered entry Hover writes in other words than the task's is
  not recognised, so the task's line is added after it too; Hover's 10 entries and the 10 the app adds make up to 20,
  while a plan may send back at most 10; GET /api/cases still works out hands-off for every case on each call (Run
  records no longer waits for it); the cards answer without parameters (section 3 of API.md) keeps the old Report
  issue rule.
- Tests: qa/app/tests/test_results_one_test.py and test_views.py (the title rule, the one-test answer, the groups and
  the Done rule, card_entries by task), test_result_actions.py and test_ticket_filing.py (Touch ID, tickets and the
  comment, as on 2026-11-03), test_results_live.py with results_live_harness.js and test_results_tickets.py with
  results_ticket_harness.js (the page), test_history_tests.py with history_tests_harness.js ('Your tests'),
  test_needs_page.py with needs_page_harness.js (the Plan screen and Your plans), test_playbooks.py (the playbook
  blocks and the skill texts against the app: never-add, citations, Confirm, result lines), test_parts.py,
  test_worker.py and test_intake.py (the case rule, the plan title, the task lines and not_covered).

### 8.18 Start steps, the person's open work, the ClickUp text and preconditions (2026-11-10, the person's OK "Yes, A and B")

After ClickUp test 0000fake009 failed in three rounds (`tests/2026-11-10-custom-terrain-offline/diagnosis`). Text and
app only; the engine (qa/runner) is unchanged.
- **The blocks check** (worker.py `start_block_problem`, called in `_intake_plan`: at plan time only, never at Run):
  every case of a New test plan, written for it or reused, that opens a page (a step taps `testid:btn-sites`) begins
  with the five steps of `START_BLOCK`, and its clean-up taps Sites or "Exit without saving" only with
  `CLEANUP_BLOCK`'s `when_js`. The constants are the one source; qa/playbooks/README.md holds the same steps as JSON
  (tests/test_start_block.py compares them). On 2026-11-10 all 54 page-opening case files in qa/cases (20 cases at
  their newest version) predate the guard, so each case gets a new version the first time a plan reuses it
  (qa-worker 1b step 4); the 23 files that open no page pass. Since the review of 2026-11-11 any truthy `always` is
  clean-up, and "Exit without saving" outside clean-up runs only as the start block's step 4 or with the clean-up
  block's guarded `when_js`, whichever control opened the dialog.
- **The guard** (start step 2 and the clean-up `when_js`): Hover leaves a planner only when it can read that the open
  flight's name starts with `QA_` (`flight-name-editable-label`, WingtraCloud 8203f39 and 82e23ff). Any other flight
  stops the run at step 2 as a test error, and the clean-up then leaves that planner as it is. Not run on a device yet.
- **`clickup_description`** (serve.py `intake_fields`, worker.py `clickup_description`): a New test check request, a
  follow-up too, carries the task's ClickUp text (redacted, at most `CLICKUP_TEXT_MAX` characters), so the worker
  plans from the task's own words, its "Test setup" lines included.
- **Plan screen** (app.js `caseSteps`, `preItem`, `fromPerson`; app.css `.plan-pre`): under a case's parts, "Before
  the run, this test takes as given" lists its preconditions; one from the person (check "person", or "set by ...")
  is a warning box with the chip "Set by you". Nothing shows for a case without preconditions.
- **Texts** (plugin 0.4.21: qa-worker and qa-run; playbooks README.md, offline-sites.md, flight-planning.md): own
  sites only and stand-ins (the person's decision of 2026-11-10), nothing the person prepared, honest quotes, the
  flight download with `OfflineSites` off and `needs.flags`, a blocking need for a prerequisite that blocks the run,
  the build check per tablet, the `Open on <tag>:` summary line, and "After a crash or a restart".
- **Go-live** (with the person's OK, one session): the app, the playbooks and plugin 0.4.21 together. The app alone
  refuses plans whose cases lack the blocks, and only the new skill and README tell Hover to write them. Run the
  update commands in qa/plugin/INSTALL.md and message the day's worker session.
- Not built (open; each needs the person's OK): the planner's Import file tap and ticking a map in Use existing map
  (engine), the existing-map Confirm (app), the MockLink flight. The guard and the flight-download blocks have not
  run on a device yet.
- Tests: qa/app/tests/test_start_block.py (the blocks check; the README against the constants), test_plan_pre.py
  with plan_pre_harness.js (the Plan screen list), test_intake.py and test_needs.py (clickup_description on new and
  follow-up requests), test_app.py; 650 app tests.

### 8.19 Import file in the planner, and the review of 8.18 (2026-11-11, the person's OK "Yes, build 12")

- **Import file in the planner** (fix 12; no engine change). After "Add terrain / map" the layers menu stays open over
  the dialog and covers "Import file", so act.py refused the tap as covered, correctly. qa/playbooks/README.md
  "Import file in the planner" now closes the menu with a tap on the default choice, then chooses Import file and
  Next. That block was run as written on ET65 on 2026-11-11 (tests/2026-11-10-custom-terrain-offline/import-file-look).
  The README's "open the layers panel" condition was also wrong (the button stays in the page at 0×0) and now reads
  its width. The steps after the drop zone have not run in the planner on a device yet.
- **Review corrections** (independent review of 8.18): any truthy `always` is clean-up, as the runner reads it.
  "Exit without saving" outside clean-up runs only as the start block's step 4 after the whole block, or with the
  clean-up block's guarded `when_js`, whichever control opened the dialog. A refused old case is told that its new
  version is checked as a case written for the plan (parts too). The saved ClickUp text keeps up to 12000 characters
  and ends with '…' when cut. Texts: a tablet showing a planner with an old case is not ready (qa-worker, qa-run);
  "Fix first" never rewrites a case for a stop at the guard; a flag BLOCK keeps the case when the flag fits again;
  exact flag names and the gate-safe flag read; stand-ins come in through Import file; leftover lines removed.
- Not changed (open): a malformed helper still ends answer-check with a traceback (it was there before);
  `--case-file`/`--text-from` before a selector hide it from the checks (needs an engine change); the Plan screen's
  "Set by you" mark knows two wordings only.
- Tests: tests/test_start_block.py ExitWithoutSavingTests (5 new); 655 app tests.
