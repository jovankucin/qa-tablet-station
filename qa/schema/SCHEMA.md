# QA engine record schema (Step 1, approved)

Status: approved by the user on 2026-10-20.

Order of work (user rule, 2026-10-20): you give tasks and info → the engine tests → results go to you in chat → you give a greenlight → only then summaries, ticket drafts or comment drafts are written → you post, or approve each Swiss Army Knife submit. The engine never posts or changes anything outside the workspace on its own. `issue` records in state `draft` exist only after your greenlight.
After approval each record type below becomes `qa/schema/<kind>.schema.json` (JSON Schema 2020-12) and a stdlib validator in `qa/runner/`.

## 0. Conventions that hold for every record

**Files.** One record = one JSON file, UTF-8, written canonically: keys sorted, 2-space indent, `,` and `: ` separators (Python `json.dumps(sort_keys=True, indent=2, ensure_ascii=False)`), no trailing spaces, final newline. That is deterministic and still readable for review. Records are immutable after they are hashed into the ledger. A correction is a new record with `"supersedes": "<old id>"`; the old file stays.

**Common header.** Every record starts with:

| Field | Type | Meaning |
|---|---|---|
| `kind` | enum | `case`, `device`, `webhost`, `account`, `run`, `prediction`, `observation`, `process`, `differential`, `issue`, `event`, `claim` |
| `schema` | int | schema version, starts at 1 |
| `id` | string | stable ID, format per kind (below) |
| `created` | UTC time | host clock, ISO-8601 with ms and `Z` |
| `author` | enum | `runner`, `claude`, `user` |
| `supersedes` | id or null | |

**Hashes.** `file_sha256` = sha256 of the file's raw bytes. That value goes into the ledger, never into the record itself. Any byte change to a hashed record fails verification, including whitespace.

**Sourced values.** Any field that someone could get wrong is a sourced value, not a bare value:

```json
{"v": "1.73.0-fd8b1ca", "st": "corroborated", "src": [<pointer>, ...], "why": null}
```

`st` is one of:
- `observed`: one source, read at the time.
- `corroborated`: a second, independent source agrees, for example the device's own log.
- `conflict`: sources disagree. All of them stay in `src`, and the record says which one it keeps and why.
- `inferred`: derived, for example the full git hash from `git describe`.
- `unverified`: a value with no way to check it, for example 10-16 flags.
- `unknown`: `v` is null and `why` says why.

**Pointers.** A pointer says where a value came from precisely enough that `qa/runner/recheck.py` can extract the value again with `pick`:

| `t` | Fields | Example use |
|---|---|---|
| `tx` | `file` (transcript id8), `line`, `line_sha256`, `tool_use_id`, `pick` | a tool result the session read at the time |
| `file` | `path` (workspace-relative), `sha256`, `line` or `lines`, `pick` | a Qt log line, a Metadata filename, a pulled logcat |
| `git` | `repo`, `ref` (40-hex), `path`, `line` | source at the exact build hash |
| `ledger` | `day`, `seq` | an action or read the engine did |
| `clickup` | `task` (CL id), `part` (`title`, `description`, `comment:<id>`, `status_history`), `cache_sha256` | redacted cache in `qa/cache/clickup/` |

`pick` is a named extractor or a regex with one capture group, for example `re:"AppVersion" "([^"]+)"`.
Transcript pointers never point at an AskUserQuestion result, and they never point at the transcript line that holds a password.

**Time.** Host times are UTC. A target time is stored as printed, with its zone, plus `offset_ms` = target minus host, and the pointer that measured the offset.

**Redaction.** No record holds a password, token, cookie, Authorization header, email address, share link or typed AskUserQuestion answer. An account is a label and a cloud user ID (section 2c). Output that carries a token is stored only as its sha256.

**Test-account secret.** The CT team's shared WingtraCloud test account password lives only in `qa/secrets.env`. You create that file, it is chmod 600 and listed in `qa/.gitignore`. Records hold only the variable name, never the value or its hash. The redactor and the secret scan load the value at runtime only to find and mask it in argv, output, HAR, console logs, tickets and comment drafts. It is never read from transcripts or ClickUp. It is typed only by `act.py type` into the app's login form, only when the target's About env is `develop` or `staging`, and the host is `web.dev.company.example` or `web.staging.company.example` on web. Production refuses. SSH and system passwords are never handled.

**Layout.**

```
qa/cases/<CASE>.json, qa/cases/_template.json, qa/cases/fixtures/<CASE>/...
qa/devices/<serial>.json, qa/devices/webpc044.json, qa/devices/accounts.json
qa/runs/<campaign>/<RUN>.json                         campaign = tests/ folder name, e.g. 2026-10-15-android-upgrade-cl80002
qa/runs/<campaign>/{obs,process,pred,diff,event,claim}/<ID>.json
qa/issues/<ISSUE>.json
qa/ledger/YYYY-MM-DD.jsonl
qa/cache/clickup/<CL>.json                            titles and redacted summaries only
qa/secrets.env                                        yours, chmod 600, gitignored; test-account password only
tests/YYYY-MM-DD-<slug>/<deviceTag>/{shots,rec,logs}  evidence, unchanged convention
tests/YYYY-MM-DD-<slug>/<deviceTag>/<RUN>.manifest.json
```

This adds `qa/issues/`, `qa/cache/` and `qa/schema/` to the layout in the brief.

## 1. Case

ID: `<AREA>-<nnn>`. An ID is never reused, and the number is never reassigned to another requirement. AREA is one of UPG, PLAN, FLY, PLANE, DEV, SAK, MAP, SYNC, OFFL, TERR, FLEET, SET, LTA (Log and Telemetry Analysis), PROC. New areas need your OK.

| Field | Type | Notes |
|---|---|---|
| `title` | string | ≤80 chars |
| `area` | enum | as above |
| `requirement.text` | string | verbatim, redacted |
| `requirement.source` | object | `{type: clickup_task|clickup_comment|spec_doc|user_chat, ref: "0000fake002", locator: "description bullet 7", requester: "Requester Colleague", fetched: time}` |
| `requirement.sha256` | hex | sha256 of the NFC-normalised text, trailing whitespace stripped per line |
| `targets` | enum | `tablet`, `web`, `both` |
| `routing` | list | gate pointers that justify `targets`: `{t: git, ref, path, line, symbol}`, where `symbol` is `isAndroid`, `isWeb`, `isElectron`, `Capacitor.getPlatform` or a route guard. The `ref` is the build hash the case was routed at. |
| `expected_differences` | list | `{what, tablet, web, src}`, e.g. map maxZoom 20 vs 25. Applied before a difference counts against either platform. |
| `preconditions` | list | `{id: "P1", text, check (helper cmd or null), applies_to: tablet|web|both}` |
| `steps` | list | `{n, text, helper, assert, gates}` |
| `steps[].helper` | string | e.g. `tools/act.py {target} tap testid:plane-mocklink-start`. `{target}` and `{run}` are filled in by the runner. |
| `steps[].assert` | object | `{kind: scripted, expr}` (the runner decides) or `{kind: judged, question}` (Claude decides, and the reasoning is recorded) |
| `steps[].gates` | list | any of `write_list`, `account_wide`, `handover`, `install`, `clickup_write`, `cloud_write`, `mocklink_ota` (the last two: G3, section 9). These drive locks and permission prompts. |
| `fixtures` | list | `{name, path, sha256, delivery[, device_path]}`. `path`: a plain file under `tests/<folder>/files/` or `qa/cases/fixtures/<CASE>/`, named `name`. `delivery`: text. `device_path`: where it goes on a tablet, under `/sdcard/Download/` and ending in `/<name>` (default `/sdcard/Download/WingtraQA/<name>`); on webpc044 it goes to `~/qa-fixtures/<name>`. Since 2026-10-26 `act.py <target> deliver <CASE>` copies them (engine piece 2, `qa/runner/fixtures.py`): sha256-checked on the target, never over a different file. |
| `covered_paths` | list | globs over `apps/` and `packages/`, used for staleness |
| `needs` | object | `{link: none|mocklink|usb_radio|udp_sitl|real_drone, usb_port: bool, account_lock: bool, flags: [..], drone_hw: 4|5|null}` |
| `status` | enum | `active`, `retired` |

`usb_port: true` (radio, real drone, SD card) makes every run of the case a handover window that you drive.

## 2. Targets and accounts

### 2a. Tablet profile: `qa/devices/<ro.serialno>.json`, kind `device`

| Field | Notes |
|---|---|
| `serial` | ro.serialno, also the file name and the lock name |
| `tag` | TA5, ET65, TA3 |
| `model`, `manufacturer` | `ro.product.model`, `ro.product.manufacturer` |
| `android` | `{release, sdk, fingerprint}` |
| `display` | `{px: [w,h], dpr, density_dpi, insets_px: {top,bottom,left,right}, measured: time, method}`. Insets are measured, never assumed. The 45 px status bar in CLAUDE.md is TA5-only until measured. |
| `rotation` | `{natural, app_locked, behaviour: "…"}` as measured |
| `oem_quirks` | list of `{id, text, src}`, e.g. `usb_prefs_steal_focus` |
| `mdm` | `{present, name}` |
| `has_pin` | bool |
| `usb_ports` | int, and whether the engine's cable occupies the only one |
| `usb_path` | `{hub: "OWC Thunderbolt Hub" or null, hub_port, mac_location_id, speed}` from `system_profiler SPUSBHostDataType`. Lets LOST tell a cable or hub drop from a device fault. |
| `charge` | `{port_w (15 on a hub TB4 port, 7.5 on its USB-A), net_gain_under_test: bool, measured}` from `dumpsys battery` at run start and end |
| `slot`, `cdp_port` | `cdp_port = 9341 + slot`. Never 9333. |
| `user_agent_model` | the model string the WebView userAgent must contain (identity check) |

Runtime state (READY, BUSY, HANDED_OVER, OFFLINE_EXPECTED, LOST) and the lock holder are not in the profile. They live in `qa/runner/state/` and every transition is an `event` record.

### 2b. Web host: `qa/devices/webpc044.json`, kind `webhost`

`{alias: "webpc044", os: "macOS 13.x", ssh: {key_only: bool, listening_off_loopback: ["sshd"], checked: time}, chrome: {version, user_data_dir, remote_port: 9222, flags: [...]}, tunnel: {local: 9350, remote: "127.0.0.1:9222"}, env_origin: "https://web.staging.company.example"}`

Identity for every web action: the browser id from `/json/version` must equal the id in `DevToolsActivePort`, read over SSH. The page target must be the tab whose origin is `env_origin`, never the first page target.

### 2c. Accounts: `qa/devices/accounts.json`, kind `account`

`{label: "shared-stag", env: "staging", cloud_user_id, org_id, secret_var: "CT_TEST_PASSWORD" | null, allowed_envs: ["develop", "staging"]}`. Never an email, never the secret. Runs reference the label.

## 3. Run

ID: `R-<yyyymmdd>-<tag>-<CASE>-<nn>`, for example `R-20261015-TA5-UPG-003-01`. Web runs use the tag `W044`.
The run key is case × target × build × link × flags × account. If any of these changes during a run (install, update, restart, page reload, link change), the run ends and a new run starts.

| Field | Type | Notes |
|---|---|---|
| `mode` | enum | `live` or `backtest` |
| `case`, `case_file_sha256` | | the exact case version that was run |
| `target` | object | tablet: `{class: tablet, serial, tag, profile_sha256}`. Web: `{class: web, host: webpc044, browser: "Chrome/<ver>", browser_id, tab_origin, profile_sha256}` |
| `build` | object | below |
| `link` | object | below |
| `flags` | object | below |
| `account` | sourced | label from accounts.json |
| `clock` | list | `{at: start|reconnect|end, host, target_raw, offset_ms, rtt_ms, method: "adb shell date +%s%3N" | "ssh perl -MTime::HiRes", src}` |
| `battery` | list | tablets: `{at: start|end, level, status, temp_c}` from `dumpsys battery` |
| `prediction` | object | `{id, file_sha256, assertion_sha256, ledger: {day, seq}}`. The ledger seq must be lower than the seq of the first action. |
| `attempts` | list | below |
| `verdict` | object | section 4: the run's final verdict |
| `issues` | list | issue IDs |
| `differential` | id or null | for `both` cases |
| `evidence_dir` | path | `tests/<campaign>/<tag>/` |
| `manifest_sha256` | hex | of `<RUN>.manifest.json` |
| `process` | id or null | the `process` record of the app process the run ran against (tablets) |

### 3a. `build`, every field a sourced value

| Field | Android | Web |
|---|---|---|
| `about_label` | Settings > About, e.g. `1.73.0-staging-fd8b1ca` | same, read on the web page |
| `semver`, `env`, `hash7` | parsed from about_label | same |
| `git_hash` | 40-hex, `st: inferred`, from `git rev-parse <hash7>^{commit}` in WingtraCloud | same |
| `release_ref` | `git describe --tags <git_hash>` | same |
| `version_code` | `dumpsys package` versionCode (About "Native version" as a second source) | literal `"web"` |
| `apk_sha256` | sha256 of `pm path` + pull, or of the installed file | null |
| `bundle_source` | `live_update` or `apk_bundled`, when it can be told | `served` |

For a backtest run, the `build` fields point at what the session read at the time (`tx` pointers), and `build_check` points at the device's own record:

`build_check: {process: "P-TA5-20261015_200208", fields: {hash7: agree, semver: agree, env: disagree, version_code: not_in_log}, skew_ms, skew_src}`

### 3b. `link`

`{kind: none|mocklink|usb_radio|udp_sitl|real_drone, vehicle_id, simulated: bool, hw: 4|5|null, fw, src}`. `simulated` is true when the vehicle ID is 1-99 or 9999 (`isSimulatedDrone.ts`), 99999 (WiP SITL / MockLink), or when `activeLinkIsMocklink` is set; 0 means no drone. A run on a simulated link always has `evidence_kind: stand-in` when the case needs a real drone.

### 3c. `flags`

`{src: "localStorage unleashFlags" | "logcat" | "none", read, sha256 (of the canonical enabled-set), enabled: [..], relevant: {DevReporting: true, …}, st}`. A full snapshot is saved as evidence. The record holds the hash, the enabled names and the case-relevant ones. 10-16 backtest runs get `st: unverified`.

### 3d. `attempts[]`

| Field | Notes |
|---|---|
| `n` | 1..n |
| `started`, `ended` | host UTC |
| `markers` | tablets: `{start, end}` WTQA logcat lines (`log -t WTQA "<RUN> a<n> start"`) with ledger refs |
| `identity` | per action: `{ledger seq, checks: {forward_ok, ua_model_ok, dpr_ok, focus_ok}}` (tablet) or `{browser_id_ok, tab_origin_ok}` (web). A failed check stops the attempt with ERROR. |
| `steps` | `{n, actions: [ledger refs], readback: {expected, got, ok}, assert: {kind, result, by: script|claude, reasoning_sha256}}` |
| `evidence` | `{kind: shot|rec|qt_log|logcat|metadata|har|console|api_read|cdp_read|manifest, path, sha256, captured, target_time}` |
| `verdict` | section 4, for this attempt |

## 4. Verdict

```json
{"v": "PASS|FAIL|BLOCKED|NOT_TESTED|PARTIAL|ERROR",
 "by":      {"kind": "issue|precondition|device_state|handover|case", "ref": "I-003"},
 "why":     {"reason": "out_of_scope|no_target|not_reached|needs_hardware|time", "text": "…"},
 "sub":     [{"id": "S1", "text": "…", "v": "PASS"}, {"id": "S2", "text": "…", "v": "FAIL"}],
 "harness": {"component": "act.py|runner|cdp|adb|ssh|hook", "detail": "…", "ledger": {"day": "…", "seq": 0}},
 "evidence_kind": "device|stand-in|code/API",
 "stand_in": "MockLink drone 99999 for a real Gen II",
 "judged": "script|claude|user",
 "assertion_sha256": "…",
 "decided": "…"}
```

- `by` is set only for BLOCKED, `why` only for NOT_TESTED, `sub` only for PARTIAL (at least one PASS and one non-PASS), and `harness` only for ERROR.
- `device`: seen on the declared target, build and link. `stand-in`: seen on a substitute (MockLink or a simulated vehicle for a drone, another tablet model, another environment). `code/API`: concluded from source at the build hash or from a direct API read, with nothing seen on screen.
- ERROR is never turned into PASS or FAIL. A harness failure says nothing about the app.
- The run verdict is the last attempt's verdict, unless the run record says otherwise and gives a reason.

## 5. Issue: `qa/issues/I-<nnn>.json`

| Field | Notes |
|---|---|
| `classification` | `app_bug`, `spec_question`, `os_oem`, `test_artefact`, `expected_behaviour` |
| `summary` | ≤300 chars, redacted |
| `platform_scope` | `tablet`, `web`, `both`, `undetermined`. For `both` cases, `scope_basis` must name a differential. |
| `found_by` | run IDs |
| `clickup` | `{cl: "CL-80006", task_id, list, via: sak|existing|manual, created, status: sourced, attachments, share_link_sha256}` |
| `duplicates` | `{phrases: [..], lists: [..], window_days: 90, candidates: [{cl, status, score, why}], decision: new|comment|skip, decided_by: user, decided}`. `comment` means a local draft only. |
| `comment_drafts` | `[{for_cl, path: tests/<slug>/tickets/<n>/comment_<CL>.md, sha256, drafted, posted: {by: user, seen_at, src} or null}]`. The engine never posts or edits a ClickUp comment. `posted` is set only after you say it is posted and a read-only fetch shows it. |
| `fix` | `{dev_task: CL, deployed: [{env, at, src}], release_cut: "1.74.0", in_build: {build_hash, is_ancestor, checked}}`. `is_ancestor` comes from `git merge-base --is-ancestor <release_cut> <build_hash>`, never from comparing semver. |
| `verified_by` | run ID or null |
| `state` | `draft`, `filed`, `comment_drafted`, `comment_posted_by_user`, `skipped`, `fixed_unverified`, `verified`, `reopened` |

## 6. Other records

### `event` (ID `E-<yyyymmdd>-<seq>`), with `type` one of:

- `side_effect`: `{target, what: cloud_create|cloud_update|cloud_delete|install|uninstall|pm_clear|setting|file_push|mocklink_start|mocklink_stop|login|logout|report_sent, object (e.g. QA_TA5_…), account, ledger, reverted_by}`. A `login` also records `{env (from About), host, account label}`, never the secret.
- `approval`: `{gate_rule, tool, argv_sha256, asked, outcome: approved|denied, outcome_src: PostToolUse|PostToolUseFailure|PermissionDenied}`. The outcome is taken from what the hooks saw, never assumed.
- `handover`: `{target, to: user, reason, case, app_state: {focus, route, about_label}, link_state: {active, src (latest Qt log line)}, mocklink: stopped|told_user|none, lock_released, returned, confirmed_by_user, state_after}`
- `device_state`: `{target, from, to, reason}`
- `lock`: `{name, mode: exclusive|shared|account, holder (run or session), acquired, released}`

### `observation` (ID `O-<tx8>-L<line>` in a backtest, `O-<day>-<seq>` live)

One value read from one tool result: `{target, what: about_label|version_code|apk_sha256|flags|link|clock|account|install_result, value, method, at_host, src}`.
This is the bridge from "what the session saw" to a run's `build`.

### `process` (ID `P-<tag>-<qtlog stem>`): the per-process build ledger

`{target, qt_log: {path, sha256, lines, first, last}, start_host: sourced (target time minus skew), app_version: sourced (MetadataLogging "AppVersion" line), semver, hash7, git_hash (inferred), env: sourced (Metadata_V4_<env>_… filename of that process), os: sourced ("TabletOSVersion"), version_code: sourced (logcat, when present), links_seen: [{kind, first_line}], ended: next_process|crash|unknown}`

For 10-16, where there is no Qt log, the record is built from logcat AppVersion lines, and `flags` and `env` are `unverified`.

### `prediction` (ID `PR-<RUN>`)

Same shape as `tests/2026-10-13-terrain-resolution-retest/code_expectations_1.73.0.json`: `readers[] {key, predictions[] {scenario, expected, reason, evidence (git pointer), confidence}}`, `skeptic {consolidated[] {scenario, expected, verdict, evidence}}`, plus `requirement_sha256`, `build_git_hash` and `deciding_assertion {step, expr, expected}`. The hook writes `file_sha256` and `sha256(deciding_assertion)` to the ledger before the run's first device action.

### `differential` (ID `D-<CASE>-<yyyymmdd>-<nn>`)

`{case, runs: {tablet: [RUN…], web: RUN}, api: {endpoint, ledger, response_sha256}, builds: {tablet: hash7, web: hash7}, flags_sha256: {…}, expected_differences_applied: [..], result: consistent|tablet_only|web_only|all_differ|undecidable_build_skew|undecidable_flag_skew}`.
A difference is called platform-specific only when both hash7s and the case-relevant flags match. Otherwise the result is one of the `undecidable_*` values.

### `claim` (Step 2 scoring, ID `K-<doc>-L<line>`)

`{doc: pointer (README/REPORT/draft line, or a clickup comment), text_sha256, excerpt (≤120 chars, redacted), about: {record, field}, theirs, ours, result: agree|disagree|not_in_records|unverifiable}`.
The disagreement list you score is every `claim` with `result: disagree`, plus the `not_in_records` ones listed separately.

## 7. Ledger: `qa/ledger/YYYY-MM-DD.jsonl`

One line per event, append-only, hash-chained:

```json
{"seq": 41, "prev": "<sha256 of line 40 bytes>", "t": "2026-10-21T08:15:02.113Z",
 "src": "hook:PostToolUse|hook:PostToolUseFailure|hook:PermissionDenied|runner|record",
 "session": "<session id8>", "target": "TA5|W044|-", "tool": "Bash",
 "argv": ["python3", "-I", "qa/runner/act.py", "TA5", "tap", "text:Next"],
 "exit": 0, "out_sha256": "…",
 "ref": {"kind": "run", "id": "R-…", "path": "qa/runs/…", "file_sha256": "…"}}
```

- `argv` passes through the redactor first. Never stored: typed answers, screenshots, token-bearing output (that output appears only as `out_sha256`).
- Lines with `src: record` hash a record file. These are the "records hashed into the ledger".
- `qa/runner/verify.py` recomputes every `file_sha256` and the chain, and reports each mismatching record by ID. Planted alterations show up as exactly the altered files. Cross-reference breakage caused by an altered file is reported under that file, not as extra failures.
- `qa/runner/recheck.py` re-extracts every sourced value through its pointers and reports field-level disagreements. This also catches an altered value with its hash recomputed, as long as the field has a source.
- After each hashing batch I give you the ledger head (`seq` and line sha256) in chat, so a rewritten ledger is detectable too.

## 8. Decisions I took (change any of them now)

1. Raw-byte hashing, not canonical-content hashing, so a whitespace edit also counts as an alteration.
2. Sourced values everywhere a fact can be wrong. This roughly doubles record size but makes Step 2 re-derivable.
3. Case IDs are area-prefixed (`UPG-003`). The 17 bullets of 0000fake002 keep their bullet number in `requirement.source.locator`.
4. Accounts by label plus cloud user ID, no email.
5. Run state changes (install, reload, link change) end the run instead of being recorded inside it.
6. ClickUp comments are drafts only (`issue.comment_drafts`); you post them.
7. Logging in with the CT test account is a gated `act.py` verb that is off until you confirm it in this session (see the Test-account secret paragraph in section 0).

## 9. Additions made while building the runner (2026-10-21, all optional fields, nothing removed)

- Case `steps[].on`: `tablet`, `web` or `both` (default `both`), for a `both` case whose steps differ by platform.
- Case `steps[].save_as`: name under which the runner keeps the step's `read` value (run `values`), for asserts and the differential.
- Case `differential: {values: [names]}`: the saved values the differential compares across the runs of a `both` case.
- Case `harness: true` with area `HARNESS`: a runner self-test kept in `qa/runner/tests/cases/`, never a product verdict.
- Run `values`, `judgement_pending` (judged steps waiting for Claude; the verdict stays null until a superseding record decides it), `harness_case`.
- Verdict `handback: {step, reason, command}` with BLOCKED `by.kind: handover`: a write-list step act.py refused under the runner; the command goes back to Claude to run with your OK.
- Run `build.entry_chunk` and the build pin read from the running bundle (40-hex GIT_HASH in the entry chunk, `window.env`, LiveUpdate versionCode); `about_label` is derived from the same constants (`st: derived`).
- Prediction: one record per run, `PR-<RUN>`, hashed before the runner starts; the runner refuses to start without it.
- Case `steps[].read_js`: a read-only page expression the runner passes to `act.py read` as one argument (no shell splitting); its value is kept under `save_as`.
- Case `steps[].when_js`: a read-only page expression read before the step; when it is falsy the step is recorded as `skipped` (for a dialog that may or may not appear).
- Case `steps[].pause_s`: seconds the runner waits before the step (for data that loads in the background).
- Case `flags_watch`: flags recorded per run (`flags.relevant`) without blocking it; `needs.flags` still blocks when off.
- Case `differential.flags`: the flags that must be equal across targets before a difference counts as platform-specific; Android-only flags that are always off on web go in `expected_differences`. `differential.api_js` (+ `api_endpoint`): a read-only cloud API read through the web page after the runs, recorded as the differential's `api`.
- Run `account` is now checked, not assumed: before any step the runner reads the logged-in user (the access token's email, hashed in the page, and its tenant) and compares it with the engine account (`accounts.v2`: tenant, email from `qa/secrets.env`). Another user or tenant blocks the run (BLOCKED, `by.kind: precondition`). `run.py --check-account <targets>` runs the same check on its own. Rule: test only with the CT account (decision E-20261021-decision-ct-only).
- (2026-10-22, QA app, qa/app/DESIGN.md) Case `status: "draft"` besides `active` and `retired`: a case the app's Add test wrote (`author: "user"`, `routing: []`, steps `{n, text, helper: null, gates: []}`). It is not runnable as written; `/qa-run` completes it from source at the build hash (routing, act.py helpers, gates, asserts, covered paths, needs) as a new version `<ID>.c<N>.json` with `status: "active"` and `supersedes: {id, file, file_sha256, why}`, written with `records.write_record` and hashed with `records.hash_record`. The newest `.c<N>` is the case (the plain file is c0); a hashed version is never edited.
- Case top-level `expected` (string): the expected result in the user's words, from the app's Add test or Edit form. It is kept in later versions; the asserts made from it live in `steps[].assert`.
- Task `qa/tasks/T-<yyyymmdd>-<nnn>.json` (`kind: "task"`): mutable workflow state, not a record. Never hashed and never in the ledger; replaced atomically by `qa/app/writes.py` (the app and `python3 -I qa/app/tasks.py`) under `fcntl.flock` on `qa/tasks/.lock`. Shape, states and who may make each transition: qa/app/DESIGN.md section 5. Its `runs` and `outputs` point at records and files; the results themselves stay in the run records.
- Progress `qa/runner/state/progress/<lock name>.json` (lock name = tablet serial or `webpc044`): `{run, case, target, attempt, step: {n, text, started}|null, steps_total, started, updated, state: running|finished, verdict|null, last_shot: rel|null, clock_offset_ms|null}`. Runtime state like the locks: written by `run.py` atomically at attempt start, at each step and at the end, only while the run holds the lock, overwritten by the next run on that lock, never hashed. A failed write never affects the run; the run record is the result.
- (2026-10-22, G3, user decision: test site "QA engine", dedicated mock drone) `qa/runner/engine.json` `test_data: {site: {name, id, env}, mock_drone: {vehicle_id}, name_prefix: "QA_", status, decided, why}` is the only authorized place for cloud data the engine creates. Case `cloud_writes: [{step, what, object}]` and `test_data: {site_id, vehicle_id}` declare a case's cloud writes and where they go; step gates `cloud_write` and `mocklink_ota` mark the steps. `run.py` refuses, before any device call, a case whose status is not `active`, a case that creates cloud data (declared, gated, or tapping a `CLOUD_WRITE_*` control of act.py) unless `test_data.site.id` is set and the case declares it, and any MockLink case (`needs.link: mocklink`, OTA or not: every MockLink connect writes to the CT fleet) unless `test_data.mock_drone.vehicle_id` is set and the case declares it. Read-only cases run as before.
- (G3) Event `side_effect` gains `observed_by` (`har` | `act.py`), `run`, `case`, `declared` (whether the case declared cloud writes), `outcome`, and `src` (the HAR file pointer with `pick: "log.entries[i]"`); `object` is `{method, path, route, status}` from a HAR entry or `{control, route}` from an act.py tap. Ids are `E-<yyyymmdd>-<nnn>`, reserved with O_EXCL (`records.side_effect`). A web run writes one per non-GET request to the cloud API host that did not fail; an approved act.py tap on a cloud-write control writes one (campaign from `QA_CAMPAIGN`, else `<date>-side-effects`), also when its readback fails. Run `side_effects: {observed_by, ids, note?}`; tablets have no network capture (`observed_by: null`).
- (2026-10-22, review fixes) `run.py` also refuses, before any device call: a case with no steps ("nothing to run"), a helper shlex cannot split, a case whose authorized site belongs to another env (`test_data.site.env`), a re-run whose run record already exists (use the next `--nn`). Cloud writes are found by tapping a `CLOUD_WRITE_*` (G2, asks) or `CLOUD_CHANGE_*` control (G3 detection only, not on the write list; labels and test ids read at WingtraCloud 84d3721, incl. the add-drone controls), by the gates, and by page JS that writes (`read_js`, `when_js`, `read` helpers, `--expect js:` and `differential.api_js` with POST/PUT/PATCH/DELETE, `.post/.put/.patch/.delete(` or `mutate(`). Run `side_effects` gains `complete: false` and `error` when the HAR listener died or the read failed; failed HAR entries count unless Chrome says the request was never sent. `run.py` passes `QA_CAMPAIGN` to every act.py call, and a handback command carries `QA_CAMPAIGN` and `QA_HANDBACK_RUN`, so act.py side effects land in the run's campaign. Event ids are reserved under `qa/runs/.event.lock`.
- (2026-10-27, user OK) Verdict `harness.component` may be `case`: the test case itself was wrong (wrong timing, a check that read the wrong element, a bad test file), which says nothing about the app. Used by correction records `R-…c1` that supersede a false FAIL; `correction {original, reason, note, case_fixed_in, passing_run, found_by}` says why. A FAIL counts toward "confirmed" only when its case version has a PASS on the same kind of device (bug-triage 1b).
