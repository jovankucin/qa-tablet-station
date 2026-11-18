---
name: bug-draft
description: Draft a WingtraApp bug ticket for the Swiss Army Knife reporter from a failed run record or an existing report text - a short [CT] title (area, symptom, condition) and a description of at most 1000 characters in the team's four-heading template, plus the evidence pack - or, when the person chose "comment" in triage, a local comment draft. Use when someone wants a bug written up, shortened to fit the reporter, or rewritten in the house format. Only when the person asks, after they have seen the results, as part of the bug-report skill after a confirmed FAIL (the person's standing rule of 2026-10-26), or from the QA worker for every failed run of an app task (the person's rule of 2026-11-03: draft, then move on). Otherwise never on your own after a FAIL, and never chained from bug-triage. No device actions and no ClickUp writes - it only writes local files.
---

# bug-draft

Runs only on the person's request, after they have seen the results. Never start it on your own after a FAIL or
straight after bug-triage, and never go on into /sak-submit (only the person types that). Exception (the person's
standing rule of 2026-10-26): the bug-report skill runs it after a confirmed FAIL triaged as a new app bug, and then
goes on into sak-submit itself; the reporter's submit asks the person through the gate.

No device actions, no ClickUp writes, no submitting, no comments posted. Output is local files only.

## 1. Input and gate

- Run record (`qa/runs/**/R-*.json`): use its case, steps, readback, verdict, build, link, flags, target, evidence.
  Continue only when all of these hold, otherwise stop and say why:
  - `verdict.v` is `FAIL`;
  - `account.st` is `verified` and `account.v` is the engine account (the `account` label in
    `qa/runner/engine.json`: `qa-stag` since 2026-11-04, `shared-stag` before; a CT-era run is not drafted, flags
    differ per account). Otherwise it is not a test result (engine account only); if the target was logged in as
    someone else, ask the person to log the engine account back in and re-run. Never log in
    yourself, never type a password;
  - a triage result for it (`triage.json` or the Issue record) says `classification: app_bug` with
    `decision: "new"` decided by the person, or the person asked in chat to draft this run;
  - the finding is `confirmed` (R1): 2 or more FAIL runs of the case on the same build, FAIL runs on 2 different
    devices, or an independent hand run on record (bug-triage step 1b; read `finding` from `triage.json`, or check the
    same records yourself). Only engine-account records count (bug-triage step 1b), for this and for the `Reproduced:` and
    `Seen:` lines. A `suspected` finding gets no Swiss Army Knife draft: write only `report.md` (section 4)
    with `Status: suspected`, say which condition is missing, and suggest re-running the case 3 times on each device.
- A triage result is input only when the person chose `new` (ticket) or `comment` (comment mode, section 6).
- Ticket or draft text (an existing report to shorten or rewrite): allowed as it is. Keep its facts; do not add any.
  It is the reporter's finding, not the engine's: `finding` is `confirmed` only when engine-account FAIL runs confirm it
  (bug-triage step 1b, for a case whose `requirement.source` is that ticket), otherwise `reported`, and `report.md`
  says `Status: reported, not reproduced by the engine`. A `reported` draft is never filed by /sak-submit, which needs a
  confirming run.

Output folder: `ticket/` in the working directory. In the engine (the working directory holds `qa/runner/run.py` and
`qa/runs/`) use `tests/<campaign>/tickets/<n>/`, with `<campaign>` from the run's `evidence_dir` and `<n>` the next
free integer in that `tickets/` folder.

## 2. Title (Swiss Army Knife form: 3-100 UTF-16 units after trim, never a backslash or U+0008)

`[CT] <area>: <symptom> (<condition>)`, for example `[CT] Fleet: drone list empty after sync (web, Chrome)`.
Area is the app page or feature, never a version. Symptom is what the tester sees. Condition is the platform, OS,
flag or link that scopes it, never a cause. The area is 2-40 characters with no colon. At most 100 characters
including the `[CT] ` prefix.

## 3. Description (3-1000 UTF-16 units after trim; the four heading lines count)

Exactly these four headings, in this order, each alone on its line, nothing before the first. Plain text: ClickUp
shows markdown literally, so no `**`, no links, no images, no other `#` lines.

```
## Issue Summary
<one or two sentences: what happens>
Screenshots: <name1>.png, <name2>.png (attached)
Where: <page name, site and flight names, device class or browser - never the URL>
Steps: 1. ... 2. ... 3. ...
Expected: ...
Actual: ...
## Occurrence Rate
Reproduced: <n> of <m> (<targets>)
Seen: <device> <verdict>, <device> <verdict>, <device> <verdict | not run>
<since when, if known>
## Tester Action and Workaround
<what the tester did, workaround or none>
## Additional Notes
Case/run: <CASE> / <run id>[, <run id> ...]
Scope: <see below>
Flags: <relevant flags and their state>
Link: none
Dev hint: <one line, optional>
Criticality (suggested): <1-6> - <why>
```

- `Screenshots:` only when the pack has them; names only.
- Run inputs carry three lines (R2): `Reproduced:` FAIL runs of the case on this build out of all its runs on this
  build, with the targets; `Seen:` the last verdict on each of the case's three devices (TA5, ET65 and W044 for a web or
  both case; TA5, ET65 and TA3 for an Android-only case), with `not run` for a device that did not run it (never
  imply a device was used when it was not); `Case/run:` the case id and the run ids. A text input (an existing report
  to shorten) keeps its own facts instead: write these lines only when engine runs of its case exist.
- `Scope:` comes from triage `platform_scope` (no triage: the run's target):
  - `tablet`: `Scope: <tablet model>, Android <n>`;
  - `web`: `Scope: Chrome <version> on macOS <version> (webpc044)` from the run's `target.browser` and the web host
    (sak-submit types it as written; it never edits the text);
  - `both`: `Scope: <tablet model>, Android <n> and Chrome <version> on macOS <version> (webpc044); differential
    <D-id> consistent`. Draft it from the differential's tablet run (`run_record`), so it files on the tablet where the
    logs attach, unless the person picks web;
  - `undetermined`: where it was seen, and that the other platform was not decided.
- `Link:` the link that was active when the bug was seen; a "last connected" drone does not count:
  - no drone link: `Link: none`;
  - simulated (MockLink, UDP SITL, `link.simulated` true, or vehicle ID 9999, 99999 or 1-99; 0 means no drone):
    `Link: MockLink, simulated drone <vehicle id> (stand-in, not a real drone)` (SITL: `Link: SITL, simulated drone
    <vehicle id> (stand-in, not a real drone)`). The word and the ID stay on the `Link:` line;
  - real drone: `Link: real drone over <USB radio | Wi-Fi>` (SAK appends the drone's ID, HW and FW).
- Never repeat what the reporter appends by itself: the About label (`x.y.z-env-hash7`), hash7 or full git hash,
  native version, build time, compatibility numbers, the reporter's user, email or name, tenant, Sentry, the flight
  share link, URLs and route paths, user agent, PX4/OBC sessions, the drone's HW/FW, payload/PPK serials, the
  auto-attached logs. A bare earlier release number is fine only to say since when ("worked in <previous release>").
  Never paste share links, emails, tokens or passwords.
- Criticality 1 (cosmetic) ... 6 (blocks flying or loses data); give the reason in a few words.
- Long input (e.g. an old 1500-character draft): keep Steps, Expected, Actual and one dev-hint line; drop analysis,
  file paths and repeated metadata until the whole description is at most 1000.

## 4. Files

- `title.txt` - the title only, one line.
- `description.txt` - the description only.
- `draft.json`:
  ```
  {"source": "<input path>", "run_record": "<run record path or null>", "title_chars": n, "description_chars": n,
   "criticality": 1-6, "scope": "...", "flags": [...], "finding": "<confirmed | reported>", "reproduced": "<n> of <m> | null",
   "link": {"kind": "none|mocklink|udp_sitl|usb_radio|real_drone", "vehicle_id": <int or null>, "simulated": true|false},
   "tablet_name": "<profile tag + model, or null for web>",
   "evidence": {"flightplan": <pack path or null>, "inputs": [..], "screenshots": [..], "recording": <path or null>,
                "logs": [..]},
   "redactions": [], "reviewed_binaries": {}}
  ```
  Counts are UTF-16 units after trim (`len(s.encode('utf-16-le')) // 2`). `run_record` is set for every run input.
  `tablet_name` comes from the target's device profile (`qa/devices/<serial>.v<N>.json`: `tag` + `model`); for a
  text input, the tablet the text names, else null. sak-submit types it as it is here.
- `report.md` - the full local report, no length limit, never typed into the reporter. Run inputs always get it,
  suspected ones only it. Ten fields, as the QA manifesto lists them: defect title and description; the test case
  (id, title, requirement source); expected versus actual; affected device and environment (per target: model,
  Android or browser, About label, account label; "2 of 3 devices" and which one is missing and why when fewer ran); reproduction steps; observed frequency (`n of m` per device and
  build); the verdict per device as the QA practice words it (PASS only when every step passed, else PARTIAL naming the
  untested steps; stand-ins and deciding configuration such as the account, OfflineSync and other code-path flags next
  to it); evidence and diagnostic references (run ids, evidence paths with sha256, ledger seqs); severity and
  functional impact; reproduction status (`confirmed` or `suspected`, with the R1 basis); cross-device differences
  (the differential and `Seen:` per device). Then two lines: `Execution:` "parallel launch" with each target's
  attempt window, the common overlap and the spread of the deciding step's reads from the run records (never
  "simultaneous": the records do not show when the deciding reads started, so `simultaneous_verified` is always false;
  the app's Results view shows the same line), or "single device"; and `Protected resources:` `unchanged`, `changed (<what>)` or
  `not verified`, from the end-of-cycle checks in CLAUDE.md (clones clean, referenced ClickUp tasks unchanged, flight
  share state); without those checks it is `not verified`.
- `pack.md` - the evidence pack: each file in `pack/` with where it came from, and what is still to collect (flight
  plan, inputs, 2 screenshots, a recording of at most 60 s, pulled logs or a stripped HAR plus console log).
- `pack/` - only when the run has an `evidence_dir`. Copy (never move) the run's own evidence files, the ones listed
  in its `attempts[].evidence` (at most 2 screenshots that show the failure, the recording, logcat and Qt log, or
  HAR and console for web), plus the case's flight plan and input files when they exist (`qa/cases/fixtures/<CASE>/`).
  The copy checks each file against the sha256 in the run record and writes `pack_manifest.json` next to `pack/`:
  ```
  python3 -I -c 'import hashlib,json,os,shutil,sys
  d,run=sys.argv[1],json.load(open(sys.argv[2])); h=lambda p: hashlib.sha256(open(p,"rb").read()).hexdigest()
  known={e["path"]:e.get("sha256") for a in run.get("attempts",[]) for e in a.get("evidence",[]) if e.get("path")}
  os.makedirs(os.path.join(d,"pack"),exist_ok=True); m={}
  for src in sys.argv[3:]:
    s=h(src)
    if known.get(src) and known[src]!=s: sys.exit("evidence changed since the run: "+src)
    dst=os.path.join(d,"pack",os.path.basename(src)); shutil.copy2(src,dst)
    m["pack/"+os.path.basename(src)]={"source":src,"source_sha256":s,"in_run_record":src in known,"sha256":h(dst)}
  json.dump(m,open(os.path.join(d,"pack_manifest.json"),"w"),indent=1)' <ticket dir> <run record> <evidence path> ...
  ```
  A sha256 mismatch stops the draft: tell the person, never re-copy over it.
  Never edit, truncate or delete pulled evidence (the originals or the copies in `pack/`) to make a check pass. The
  only change ever made to evidence is a `redact()` copy written by `draft_check.py --redact-to` (section 5).

## 5. Check before you finish

When `qa/runner/draft_check.py` exists in the working directory, run:
```
python3 -I qa/runner/draft_check.py <the directory you wrote> --link-json <run record>
```
(`--link-json` for run inputs; without it the check loads the link from `draft.json` `run_record`). Outside the engine
(a scratch or eval directory without the engine's `qa/runs/`), add `--no-secrets-file` when the check says secrets were
not checked, and say so; never use it in the engine.
- Text-secret findings only inside `pack/`: run it again with `--redact-to <the directory you wrote>/attach`. It writes
  `redact()` copies of the ticket folder into `attach/` and re-scans them; `attach/pack/` is then the attach set, and
  `pack/` keeps the exact copies. Put its `redactions` list into `draft.json` `redactions` and run that same command
  once more. From then on every check of this ticket, sak-submit's included, uses the same `--redact-to`. A finding in
  `title.txt`, `description.txt`, `draft.json` or `pack.md` is fixed in the text itself, never by redaction.
- Binaries (screenshots, recordings, binary logs) need the person's review: look at each screenshot yourself for
  emails, avatars, share links and tokens, then show them and name the recording. Only after the person says they
  looked at them, record `{relpath: sha256}` in `draft.json` `reviewed_binaries` and re-run. Never fill it on your own.
  Until then, the only open problems may be "not reviewed"; say so.

When `qa/runner/draft_check.py` is absent, run this fallback from the working directory (Bash with python3; without Bash, count by
reading the files back) and fix until it prints `ok: true`:
```
python3 -I -c 'import json,pathlib,re,sys
d=pathlib.Path(sys.argv[1]); rd=lambda f:(d/f).read_text("utf-8-sig",errors="replace") if (d/f).exists() else ""
t,s=rd("title.txt").strip(),rd("description.txt").replace("\r\n","\n").strip(); n=lambda x:len(x.encode("utf-16-le"))//2; p=[]
if not 3<=n(t)<=100: p.append("title %d units" % n(t))
if re.search(r"[\\\x08]",t) or "\n" in t: p.append("title: backslash, U+0008 or a line break")
if not re.match(r"\[CT\] [^:\n]{2,40}: [^\n]+\([^)\n]+\)$",t): p.append("title is not [CT] <area>: <symptom> (<condition>)")
if not 3<=n(s)<=1000: p.append("description %d units" % n(s))
H=["## Issue Summary","## Occurrence Rate","## Tester Action and Workaround","## Additional Notes"]
if [l.rstrip() for l in s.split("\n") if l.lstrip().startswith("#")]!=H or not s.startswith(H[0]): p.append("headings: exactly the four, in order, as whole lines, nothing before the first")
for lab in ["Where:","Steps:","Expected:","Actual:","Scope:","Flags:","Link:","Criticality (suggested):"]:
  if not re.search(r"(?m)^"+re.escape(lab),s): p.append("no line "+lab)
for rx in [r"\b[0-9a-f]{40}\b",r"\b\d+\.\d+\.\d+-(develop|staging|preproduction|production)\b",r"(?i)\bnative\s*(version)?\s*[:=]?\s*\d",r"(?i)\b(sentry|tenant|build time)\b",r"https?://",r"\*\*|!\[|\]\("]:
  if re.search(rx,t+"\n"+s): p.append("title/description matches "+rx)
for f in ["title.txt","description.txt","draft.json","pack.md"]:
  for rx in [r"[\w.+-]+@[\w-]+\.[\w.-]+",r"/shared/\S+",r"eyJ[\w-]{10,}\.",r"(?i)password\s*[:=]"]:
    if re.search(rx,rd(f)): p.append(f+" matches "+rx)
print(json.dumps({"title_chars":n(t),"description_chars":n(s),"problems":p,"ok":not p})); sys.exit(1 if p else 0)' ticket
```
Then show the title, the description, the character counts, `pack.md` and the check's result. Stop there: filing is
`/sak-submit <n>`, which only the person types.

## 6. Comment mode (the person chose "comment on <CL>" in triage)

Write `comment_<CL>.md` in the output folder (`tests/<campaign>/tickets/<n>/comment_<CL>.md` in the engine): short and
precise, plain text, at most about 600 characters: what was seen again, where (page, platform, release and env), how
often, the run id. No emails, share links, tokens, hashes, Sentry or other SAK data. Check it and fix any hit:
```
python3 -I -c 'import re,sys; s=open(sys.argv[1],encoding="utf-8").read(); h=[rx for rx in [r"[\w.+-]+@[\w-]+\.[\w.-]+",r"/shared/\S+",r"eyJ[\w-]{10,}\.",r"(?i)password\s*[:=]",r"\b[0-9a-f]{40}\b",r"\b\d+\.\d+\.\d+-(develop|staging|preproduction|production)\b"] if re.search(rx,s)]; print(h or "clean"); sys.exit(1 if h else 0)' <comment file>
```
When the run has an Issue record, record the draft in its `comment_drafts`
(`{for_cl, path, sha256, drafted, posted: null}`) as a superseding Issue record written through qa/runner
(`records.write_record` and `records.hash_record`), never by editing the hashed record.
Never post it. ClickUp comments are posted by the person, not by Claude.
