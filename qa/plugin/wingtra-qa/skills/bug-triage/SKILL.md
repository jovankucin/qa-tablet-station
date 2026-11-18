---
name: bug-triage
description: Triage a failed WingtraApp test result or a bug report before anything is filed. Checks the engine-account gate, classifies it (app bug, spec question, OS/OEM, test artefact, expected behaviour), searches ClickUp for duplicates, and checks with git whether a fix is already in the build it was seen on. Use when someone asks whether a FAIL or a ticket is a real bug, a duplicate, or already fixed. Only when the person asks, after they have seen the results, or as part of the bug-report skill after a confirmed FAIL (the person's standing rule of 2026-10-26). Otherwise never on your own after a FAIL, and never chained into bug-draft. Read-only - it never writes to ClickUp or touches a device.
---

# bug-triage

Runs only on the person's request, after they have seen the results. Never start it on your own after a FAIL, and
never go on into bug-draft or /sak-submit from here. Exception (the person's standing rule of 2026-10-26): the
bug-report skill runs it after a confirmed FAIL; there a clear app bug with no duplicate counts as the decision `new`,
a duplicate as `comment`, and the person's OK comes at the reporter's submit (the gate).

Read-only. Never call a ClickUp tool that creates, updates, comments, attaches, moves or deletes, and never touch a
tablet or webpc044. The only files you write are `triage.json`, the redacted index rows (step 3) and, after the
person's answer, `issue.json` and the Issue record made from it (step 6). The person decides new / comment / skip; you only prepare that decision.

Never fetch, read, cache or list CL-80008 (it holds the CT test-account credential). If a search returns it, skip it
unread and do not name it as a candidate.

## 1. Input and gate

The input is one of: a run record (`qa/runs/**/R-*.json`), a ticket id (`CL-12345`), or ticket text.
- Run record, two gates. Stop at the first that fails, write `triage.json` with `gate: "stopped"`, `gate_reason`,
  `classification: null`, `search: null` and `duplicates: []`, and stop:
  - Verdict: continue only when `verdict.v` is `FAIL`. PASS, PARTIAL, BLOCKED, ERROR or NOT_TESTED stop (ERROR says
    nothing about the app; BLOCKED and NOT_TESTED were not decided).
  - Account (memory test-only-with-ct-account): continue only when `account.st` is `verified` and `account.v` is the
    engine account (the `account` label in `qa/runner/engine.json`: `qa-stag` since 2026-11-04, `shared-stag`
    before). Anything else stops: "not a test result (engine account only)". A run on the earlier engine account (CT)
    stops too, because flags differ per account: re-run it on the engine account. If the target was logged in as
    someone else, ask the person to log the engine account back in and re-run; never log in yourself, never type a
    password.
- Ticket id or text: there is no verdict; treat it as a reported failure. Remember its own id: it is excluded from every
  candidate list (`excluded_self`). Set `account` from the reporter or the auto-report metadata: `engine` when it
  names the engine account (`account` in `qa/runner/engine.json`), `other` when it names another user, `unknown` when
  nothing says. Records written before 2026-11-04 say `CT`: the engine account of that time. The `[CT]` title prefix is
  the reporter's default and proves nothing. For `other`, still answer the duplicate and fix questions, say in
  `reason` that it was not seen on the engine account, and recommend `skip` unless it reproduces on the engine
  account. For `unknown`, say so in `reason`.

Collect: symptom in one line, on-screen text (exact), page and route (`currentUrl` or the page), platform, build
(About label: semver, env, hash7; full git hash), date seen, flags, link (MockLink / SITL / simulated drone IDs 9999,
99999 or 1-99; 0 means no drone).

## 1b. Confirmed or suspected (R1)

A finding is `confirmed` only when at least one of these is on record, otherwise it is `suspected`:
- 2 or more FAIL runs of the same case on the same build (same `build.git_hash.v`), on any targets;
- FAIL runs of the same case on 2 different devices (targets);
- an independent hand run on record: a run or observation record made by a person (`mode` manual, or a verdict with
  `judged: user`) for the same case that shows the same failure.
**Validated case version (the person's rule of 2026-10-27).** A FAIL run counts toward the first two conditions only
when its case version has passed once: a PASS run with the same `case_file_sha256`, on the same kind of device (the
same `target.class`, `tablet` or `web`), engine account verified, not superseded, any build. Runs without
`case_file_sha256` (hand runs, back-tests, eval fixtures) are not engine runs of a case file and stay outside this
rule. The FAILs of a
version that never passed are `suspected` with `confirmation.basis` "case not validated": the test itself may be wrong
(TERR-005 c2-c4, 2026-10-26: 7 FAILs, all from the case). Recommend `re-run` once the case version has passed on
another device or build, or an independent hand run (the third condition, which needs no validated version).
Look read-only in `qa/runs/**/R-*.json` and `qa/runs/**/O-*.json` (same `case`; a record that another one supersedes
does not count). Only engine-account records count: runs with `account.st` `verified` and `account.v` the `account`
label of `qa/runner/engine.json`, and hand runs or observations that record that account as verified (so CT-era runs
do not count since the switch to `qa-stag` on 2026-11-04). Back-test runs whose
account is `unknown`, and records a decision event names as out of scope (for example `applies_to` of
`E-20261021-decision-ct-only`), never count; name what you left out in `confirmation.basis`. For a ticket id or text the finding is `confirmed` only when such runs exist for a case whose
`requirement.source` is that ticket; otherwise it is `suspected` (reported, not reproduced by the engine).
Write `finding` and `confirmation` (step 5). A `suspected` finding is never recommended as `new`: recommend `re-run`
(run the case 3 more times on each of its three devices: TA5, ET65 and webpc044 for a web or both case, TA5, ET65
and TA3 for an Android-only case, minus the devices its routing excludes (tablets for a `web` case, W044 for a `tablet`
case) or that are not registered (TA3 until it has a profile in `qa/devices/`), each named as missing with the reason),
or `skip` / `comment` as the other rules say. A web-only case has one device, so only 2 or more FAIL runs on the same
build can confirm it; say so. Say plainly in `reason` which of the three conditions is missing.

## 2. Classify (exactly one) and scope

- `app_bug` - the app does something its own requirement or code intends otherwise.
- `spec_question` - requirement unclear or two sources disagree.
- `os_oem` - Android / Samsung / Zebra / browser behaviour outside the app's control.
- `test_artefact` - caused by the harness, the stand-in (MockLink, simulated drone) or the setup, not the app.
- `expected_behaviour` - the app does what it is meant to do (cite the source or the decision).
Only `app_bug` continues to steps 3 and 4. For the others write the reason, `duplicates: []` and
`recommendation: "skip"`, and go to step 5.

Platform scope (`tablet`, `web`, `both`, `undetermined`) and `scope_basis`:
- Single-platform run: the run's target class; `scope_basis` = the run id. Ticket: its reported platform;
  `scope_basis` = `report`.
- A `both` case: read the run's differential (`differential` id, record in `qa/runs/<campaign>/`) and always write
  `scope_basis: "<D-id>"`.
  - `consistent` and every run in it FAIL -> `both`.
  - `platforms_differ` (or `tablet_only` / `web_only`) -> the platform whose run verdict is FAIL, or whose value differs
    from `differential.api.value` (the third oracle) -> `tablet` or `web`. If that does not single out one side ->
    `undetermined`.
  - `all_differ` or any `undecidable_*` -> `undetermined`, and name the reason in `reason`: missing run, build skew
    (the hash7s differ) or flag skew (the case-relevant flags differ).

## 3. Duplicate search

Data source, in this order:
1. A local ClickUp snapshot if one is present (`clickup_snapshot/` in the working directory: `index.json`,
   `tasks/<CL-id>.json`). Offline mode: use it and nothing else, and write no cache.
2. Otherwise the ClickUp read tools (`clickup_search`, `clickup_get_task`, `clickup_get_task_comments`,
   `clickup_get_task_time_in_status`). Read what you need, but save only index rows (below).

Cache (online, in the engine only, so the decision can be re-checked):
`tests/<campaign>/triage/<input id>/index.json`, rows
`{custom_id, title, status, list, created, deployed: [{env, at, src}], summary}`. `summary` is at most 300 chars and,
like the title, passes through `qa/runner/redact.py` `redact()` before it is written; it is cut after redaction. Never
write a description, comment body, metadata block, attachment or share link, and never a row for CL-80008. Pass the
rows on stdin so nothing raw touches the disk:
```
python3 -I -c 'import json,os,sys; sys.path.insert(0,"qa/runner"); from redact import redact
rows=[r for r in json.load(sys.stdin) if r.get("custom_id")!="CL-80008"]
out=[{"custom_id":r["custom_id"],"title":redact(r.get("title")),"status":r.get("status"),"list":r.get("list"),"created":r.get("created"),"deployed":r.get("deployed",[]),"summary":redact(r.get("summary") or "")[:300]} for r in rows]
bad=[r["custom_id"] for r in out if redact(json.dumps(r))!=json.dumps(r)]
os.makedirs(os.path.dirname(sys.argv[1]),exist_ok=True)
sys.exit("still redactable: "+",".join(bad)) if bad else json.dump(out,open(sys.argv[1],"x"),indent=1)' tests/<campaign>/triage/<input id>/index.json <<'ROWS'
[{"custom_id": "CL-...", "title": "...", "status": "...", "list": "...", "created": "...", "deployed": [], "summary": "..."}]
ROWS
```
If it exits non-zero, drop the named rows' summaries and run it again; never write the rows another way. The file is
never overwritten: an existing `index.json` means this input was triaged before; read it, and write a new search to
`index_<yyyymmddThhmm>.json`. For a ticket input, `<campaign>` is the campaign of a case whose `requirement.source` is that
ticket; when there is none, `<today>-triage-<CL>` (for example `2026-10-22-triage-CL-80006`). When
`qa/runner/redact.py` is missing, write no cache at all.

Search:
- Lists: Auto Bug Reports (919191919101), Wingtra SW (Cloud) dev tasks (919191919102), UX Improvements (919191919103).
- Window: tickets created from 90 days before the input's date up to now. Later tickets count too: double submits come
  seconds after the input, companion tickets minutes later.
- Key phrases of 2-4 words: the on-screen error text first (also its spelling variants, e.g. "pre-flight" and
  "preflight", "log in" and "login"), then the symptom words. Offline, match them case-insensitively against
  `index.json` titles and summaries, then read `tasks/<id>.json` of the hits. Search counts drift; never rely on a
  count.
- Text to key: when the repo and the build hash are available, resolve the on-screen text to its key with
  `git -C WingtraCloud grep -n -F '<text>' <build hash> -- apps/src/i18n/locales/en.json apps/cpp/wipapp/src/UserMessages`
  (keep the pathspec this narrow: the clone is blobless) and compare candidates by key, not by text, because
  interpolated strings differ and two keys can share a text. Without the repo, compare the exact text and say so.
- Routes: replace UUIDs and numeric ids with `:id`, then require the same route, not a prefix.
- Score each candidate 0-100 and say why: same key or exact text 30, same route 15, same build 10, title + summary word
  overlap up to 15, same reporter session 30 (same minute, consecutive ids = double submit), an explicit link or
  comment ("same as CL-x", "fixed in <id>") 30; cap at 100.
- Internal ClickUp ids in comments (`<internal id>`, 11 chars) are resolved by looking them up in the task data (the
  `id` / `internal_id` fields of the snapshot, or `clickup_get_task`), never assumed.
- Drop the input ticket itself. Tickets that share the topic but differ in mechanism go to `related`, not
  `duplicates`.
Show the top 3-5 duplicates: id, title, status, list, score, why.

## 4. Already fixed?

Rule (no exceptions): already fixed = `git merge-base --is-ancestor <tag> <build hash>` exits 0, where `<tag>` is the
release tag cut just before the fix task went "deployed to <env>", and `<env>` is the environment of the build being
checked (About label env, the run's `build.env`, or the report's "Report env" field). Never compare semver
("1.10 > 1.9"), never treat a CL number as a PR number (`(#12345)` in a commit is a PR, not CL-12345).

1. Fix task: from the input's and the duplicates' links or comments ("fixed in ..."). None found -> `fix_task: null`,
   `fix_in_build: null`, `ancestry: {command: null, exit_code: null}`.
2. Repo: `git -C WingtraCloud fetch origin --tags --prune` first. If it fails (offline, no remote), say so in `reason`
   and use the local refs.
3. Build hash: from the run's `build.git_hash` (a sourced value: use its `v`; older records hold a plain string) or
   the report's git hash, resolved with
   `git -C WingtraCloud rev-parse --verify '<hash>^{commit}'`. No repo, or the hash does not resolve ->
   `fix_in_build: null`, `ancestry.exit_code: null`, and `reason` says why. Never write an exit code you did not get.
4. Deploy moment for `<env>` -> `deployed: [{env, at, src}]`: the status-change time into "deployed to <env>" from the
   task's status history (`clickup_get_task_time_in_status` online; a `status_history` in the snapshot offline).
   Only when no status history exists, use the date-only field ("Deployed to dev date" = develop, "Deployed to stag
   date" = staging) and mark `src` as date only. If the task has no entry for the build's env (e.g. a production
   build), use the release that carried the fix to the nearest earlier env and say so: the same tag is promoted on.
5. Release tags only: `git -C WingtraCloud for-each-ref --sort=creatordate --format='%(creatordate:iso-strict) %(refname:short)' 'refs/tags/[0-9]*.[0-9]*.[0-9]*'`.
   - With a timestamp: the last release tag created before it.
   - With a date only: the candidates are the release tags created that day up to 23:59:59Z. Pick the one whose
     promote merge is on the env branch:
     `git -C WingtraCloud log --first-parent --merges --format='%cI %s' origin/sky-<env> | grep 'promote-to-<env>/v<tag>'`
     (env branch `sky-staging`, `sky-preproduction` or `sky-production`; the local branch when there is no remote).
     No release tag created that day: take the release whose promote-to-<env> merge on that branch is dated that day,
     and say so.
   - No deploy time or date at all (the task only shows the status): take the earliest release tag, in creation order,
     that contains the fix commit (`git tag --contains`) and whose promote-to-<env> merge is on the env branch, and
     say so in `reason`. No fix commit found either -> `fix_in_build: null`.
   - Cross-check with the fix commit when you can find it (`git -C WingtraCloud log --oneline --grep='<topic words or CL id>'`
     or `-- <file>`): `git -C WingtraCloud tag --contains <fix commit>`. If the two disagree, report both in `reason`
     and set `fix_in_build: null`.
6. Develop builds (`<env>` = develop, branch `sky`): there is no release cut for a dev deploy. Check the fix commit
   itself: `git -C WingtraCloud merge-base --is-ancestor <fix commit> <build hash>`; `release_tag: null`. No fix
   commit found -> `fix_in_build: null`.
7. Run the ancestry check and record the exact command and its exit code: 0 -> `fix_in_build: true`, 1 -> `false`,
   anything else -> `null` with the error in `reason`.

## 5. Output

Write `triage.json` where the person asked, else in the working directory; in the engine (the working directory holds
`qa/runner/run.py` and `qa/runs/`) write it to `tests/<campaign>/triage/<input id>/triage.json`, next to the index
rows. Print the same as a short summary.
All values below are placeholders; fill every one from your own search and checks.

```json
{
  "input": "CL-12345",
  "gate": "<passed | stopped>",
  "gate_reason": "<why, when stopped; else null>",
  "account": "<engine | other | unknown>",
  "classification": "<app_bug | spec_question | os_oem | test_artefact | expected_behaviour | null when stopped>",
  "platform_scope": "<tablet | web | both | undetermined>",
  "scope_basis": "<D-id, run id or report>",
  "search": {"phrases": ["<2-4 words>", "<2-4 words>"],
             "lists": ["919191919101", "919191919102", "919191919103"],
             "window": {"from": "<input date minus 90 days>", "to": "<now>"}},
  "duplicates": [{"id": "CL-12346", "title": "...", "status": "...", "list": "...", "score": 0, "why": "..."}],
  "related": [{"id": "CL-12000", "why": "..."}],
  "excluded_self": "CL-12345",
  "fix_task": "<CL id or null>",
  "deployed": [{"env": "<env>", "at": "<time or date>", "src": "<status history | date-only field>"}],
  "release_tag": "<tag or null>",
  "build_hash": "<40 hex or null>",
  "ancestry": {"command": "<exact command or null>", "exit_code": "<0 | 1 | null>"},
  "fix_in_build": "<true | false | null>",
  "finding": "<confirmed | suspected | null when stopped>",
  "confirmation": {"basis": "<which R1 condition holds, or which is missing>",
                   "fail_runs": [{"run": "R-...", "target": "<tag>", "build_hash7": "<hash7>"}],
                   "hand_runs": ["<record id>"]},
  "recommendation": "<comment on <id> | new | re-run | skip>",
  "reason": "one or two sentences",
  "decision": null,
  "decided_by": null
}
```
Write real JSON types: `exit_code` a number or null, `fix_in_build` a boolean or null. `input` is the ticket's CL id
or the run record's `id`; `excluded_self` is the input ticket's own id (null for a run input). `duplicates` holds 0-5 entries, best first, never the input itself. `fix_in_build` is null
when there is no fix task, no repo, or the build cannot be resolved.

`new` only for a `confirmed` finding (step 1b). End by asking the person: new, comment on <id>, re-run, or skip.
Do not draft, comment or file anything yourself.

## 6. After the person answers

Set `decision` (`new`, `comment on <CL id>`, `re-run` or `skip`, naming the CL for a comment) and `decided_by: "user"` in
`triage.json`; nothing else changes. For `re-run` write no Issue record: list the re-runs (case, targets, 3 each)
for the person to start (through `/qa-run` or their own request). For any other decision on a run
input in the engine, write the Issue record (SCHEMA.md section 5) as a new file `qa/issues/I-<nnn>.json` (next free
number) through qa/runner, never with Write or Edit, and never over an existing record:
```
python3 -I -c 'import json,sys; sys.path.insert(0,"qa/runner"); import records; records.write_record(sys.argv[1], json.load(open(sys.argv[2]))); records.hash_record(sys.argv[1], note="bug-triage decision")' qa/issues/I-<nnn>.json tests/<campaign>/triage/<input id>/issue.json
```
`issue.json`: header (`kind: "issue"`, `schema: 1`, `id`, `created`, `author: "claude"`, `supersedes: null`), then
`classification`, `summary` (at most 300 chars, redacted), `platform_scope`, `scope_basis`, `found_by: [<run id>]`,
`clickup: null`, `duplicates: {phrases, lists, window_days: 90, candidates: [{cl, status, score, why}], decision,
decided_by: "user", decided}`, `comment_drafts: []`, `fix: {dev_task, deployed, release_cut, in_build: {build_hash,
is_ancestor, checked}}`, `verified_by: null`, `state` (`draft` for new, `skipped` for skip, `draft` for comment until
bug-draft writes the comment draft).
Drafting the ticket or the comment is bug-draft's job, and only when the person asks for it.
