"""Golden cases for draft_check.py: the SAK form mirror (UTF-16 lengths, JS trim), the house format, the data SAK
appends by itself, the simulated-drone rule and its loader, evidence classification, recordings and --redact-to.

python3 -I qa/runner/tests/test_draft_check.py [-v]
Exit 0 when every case passes. Works in a temporary directory with a fake secrets file; qa/secrets.env is never read.
"""
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.dirname(HERE)
sys.path.insert(0, RUNNER)
import draft_check as dc  # noqa: E402
import redact  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(RUNNER), "plugin", "wingtra-qa", "evals", "fixtures")  # not in this repo: checks that need it are skipped
FAKE_SECRET = "Fake-CT-pass-4711"  # a made-up value, only in the fake secrets file below
TMP = tempfile.mkdtemp(prefix="draft_check_test_")
SECRETS = os.path.join(TMP, "secrets.env")
with open(SECRETS, "w", encoding="utf-8") as _f:
    _f.write(f"# fake\nCT_PASSWORD={FAKE_SECRET}\n")
redact.SECRETS_FILE, redact._SECRETS = SECRETS, None

TITLE = "[CT] Plane > Updates: Wi-Fi stays off after OTA update (Android 16, MockLink)"
DESC = [
    "## Issue Summary",
    "After a drone OTA update the app asks to turn Wi-Fi back on, but Wi-Fi stays off.",
    "Where: Plane > Updates, Galaxy Tab Active5 Pro",
    "Steps: 1. Plane > Updates > update the drone over OTA. 2. Turn off Wi-Fi when asked. 3. Accept the prompt.",
    "Expected: Wi-Fi is on again.",
    "Actual: Wi-Fi stays off; it has to be turned on in the Android settings.",
    "## Occurrence Rate",
    "Always (1 of 1).",
    "## Tester Action and Workaround",
    "Turned Wi-Fi on in the Android settings.",
    "## Additional Notes",
    "Scope: Galaxy Tab Active5 Pro, Android 16",
    "Flags: OTADroneUpdates, Mocklink",
    "Link: MockLink, simulated drone 99999 (stand-in, not a real drone)",
    "Criticality (suggested): 3 - a workaround exists",
]
LINK = {"kind": "mocklink", "vehicle_id": 99999, "simulated": True}
REAL = {"kind": "real_drone", "vehicle_id": 4711, "simulated": False}
DROP = object()
_n = [0]
results = []


def sha(b):
    return hashlib.sha256(b).hexdigest()


def norm(desc):
    return dc.js_trim(desc.replace("\r\n", "\n").replace("\r", "\n"))


def lines_with(replace=None, insert_before_crit=None, drop=None):
    out = []
    for ln in DESC:
        if drop and ln.startswith(drop):
            continue
        if insert_before_crit and ln.startswith("Criticality"):
            out += insert_before_crit
        for a, b in (replace or {}).items():
            if ln.startswith(a):
                ln = b
                break
        out.append(ln)
    return "\n".join(out) + "\n"


def make(title=TITLE, desc=None, draft=None, files=None, review=(), counts=True, raw=None):
    """A ticket dir. draft: overrides for draft.json (DROP removes a key). files: {relpath: bytes}.
    review: relpaths to list in reviewed_binaries with their current sha256. raw: {name: bytes} written as is."""
    _n[0] += 1
    d = os.path.join(TMP, f"t{_n[0]:03d}")
    os.makedirs(d)
    desc = lines_with() if desc is None else desc
    with open(os.path.join(d, "title.txt"), "w", encoding="utf-8", newline="") as f:
        f.write(title + "\n")
    with open(os.path.join(d, "description.txt"), "w", encoding="utf-8", newline="") as f:
        f.write(desc)
    with open(os.path.join(d, "pack.md"), "w", encoding="utf-8") as f:
        f.write("# Evidence pack\nNothing to collect.\n")
    files = files or {}
    for rel, data in files.items():
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)
    dj = {"source": "report.md", "run_record": None, "title_chars": dc.utf16_len(dc.js_trim(title)),
          "description_chars": dc.utf16_len(norm(desc)), "criticality": 3, "scope": "tablet",
          "flags": ["OTADroneUpdates", "Mocklink"], "link": dict(LINK), "tablet_name": "TA5 Galaxy Tab Active5 Pro",
          "evidence": {"flightplan": None, "inputs": [], "screenshots": [], "recording": None, "logs": []},
          "redactions": [], "reviewed_binaries": {rel: sha(files[rel]) for rel in review}}
    for k, v in (draft or {}).items():
        if v is DROP:
            dj.pop(k, None)
        else:
            dj[k] = v
    if not counts:
        dj.pop("title_chars"), dj.pop("description_chars")
        dj["title_chars"], dj["description_chars"] = 1, 1
    with open(os.path.join(d, "draft.json"), "w", encoding="utf-8") as f:
        json.dump(dj, f)
    for name, data in (raw or {}).items():
        with open(os.path.join(d, name), "wb") as f:
            f.write(data)
    return d


def run(d, *opts):
    return dc.cli([d, *opts])


def expect(name, got, ok, has=(), lacks=()):
    code, res = got
    probs = res["problems"] if res else []
    bad = []
    if (code == 0) != ok or res is None or res["ok"] != ok:
        bad.append(f"expected ok={ok}, got exit {code}")
    for s in has:
        if not any(s in p for p in probs):
            bad.append(f"no problem containing {s!r}")
    for s in lacks:
        if any(s in p for p in probs):
            bad.append(f"unexpected problem containing {s!r}")
    results.append((name, not bad, bad, probs))
    return res


def truth(name, cond, detail=""):
    results.append((name, bool(cond), [] if cond else [detail or "condition false"], []))


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    return path


def box(typ, payload):
    return struct.pack(">I4s", 8 + len(payload), typ) + payload


def mp4(seconds, version=0, mdat_first=False, largesize=False):
    """A minimal mp4: ftyp, moov/mvhd (timescale 1000) and an mdat, built in memory."""
    scale, dur = 1000, int(round(seconds * 1000))
    if version == 0:
        mvhd = box(b"mvhd", b"\0\0\0\0" + struct.pack(">IIII", 0, 0, scale, dur) + b"\0" * 80)
    else:
        mvhd = box(b"mvhd", b"\1\0\0\0" + struct.pack(">QQIQ", 0, 0, scale, dur) + b"\0" * 80)
    payload = b"\0" * 4096
    mdat = struct.pack(">I4sQ", 1, b"mdat", 16 + len(payload)) + payload if largesize else box(b"mdat", payload)
    moov = box(b"moov", mvhd)
    ftyp = box(b"ftyp", b"isom\0\0\x02\0isomiso2mp41")
    return ftyp + (mdat + moov if mdat_first else moov + mdat)


PNG = b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR" + b"\0" * 64


def cases():
    # ---- baseline
    base = make()
    expect("baseline ticket passes", run(base), True)

    # ---- title: 100 vs 101 UTF-16 units (an astral emoji counts 2), JS trim incl. U+FEFF, backslash, U+0008
    pre, post = "[CT] Flight planning: terrain list \U0001F600 ", " (Android)"
    fill = 100 - dc.utf16_len(pre + post)
    t100 = pre + "x" * fill + post
    truth("title of 100 UTF-16 units is 99 code points", dc.utf16_len(t100) == 100 and len(t100) == 99)
    expect("title 100 UTF-16 units passes", run(make(title=t100)), True)
    t101 = pre + "x" * (fill + 1) + post
    expect("title 101 UTF-16 units fails", run(make(title=t101)), False, has=["title is 101 UTF-16 units"])
    expect("title with BOM and spaces around is trimmed like JS", run(make(title="﻿  " + t100 + " \t")), True)
    expect("title with a backslash fails", run(make(title=TITLE.replace("Wi-Fi", "Wi\\Fi"))), False,
           has=["backslash or U+0008"])
    expect("title with U+0008 fails", run(make(title=TITLE.replace("Wi-Fi", "Wi\bFi"))), False,
           has=["backslash or U+0008"])
    expect("title not in house format fails", run(make(title="Wi-Fi stays off after OTA update")), False,
           has=['title is not "[CT] <area>: <symptom> (<condition>)"'])
    expect("title area that is a version fails",
           run(make(title="[CT] Android 1.73.0: custom terrain cannot be selected (metadata)")), False,
           has=["title area holds a version"])

    # ---- description: 1000 vs 1001 UTF-16 units after trim (headings included), CRLF counts as one
    d0 = lines_with()
    pad = 1000 - dc.utf16_len(norm(d0)) - 3  # " " + emoji (2 units)
    d1000 = lines_with(replace={"Actual:": DESC[5] + " \U0001F600" + "x" * pad})
    truth("1000-unit description built", dc.utf16_len(norm(d1000)) == 1000, str(dc.utf16_len(norm(d1000))))
    expect("description 1000 UTF-16 units with whitespace around passes", run(make(desc="\n \t" + d1000 + "\n\n ")), True)
    d1001 = lines_with(replace={"Actual:": DESC[5] + " \U0001F600" + "x" * (pad + 1)})
    expect("description 1001 UTF-16 units fails", run(make(desc=d1001)), False, has=["description is 1001 UTF-16"])
    r = expect("description with CRLF line ends counts LF only", run(make(desc=d1000.replace("\n", "\r\n"))), True)
    truth("CRLF description reports 1000", r and r["description_chars"] == 1000)

    # ---- headings: exact whole lines, in order, nothing before the first
    swapped = lines_with(replace={"## Occurrence Rate": "## Tester Action and Workaround",
                                  "## Tester Action and Workaround": "## Occurrence Rate"})
    expect("headings out of order fail", run(make(desc=swapped)), False, has=["headings out of order"])
    expect("### heading variant fails", run(make(desc=lines_with(replace={"## Issue Summary": "### Issue Summary"}))),
           False, has=["heading line '### Issue Summary' is not one of the four", "missing heading line '## Issue Summary'"])
    expect("heading with trailing text fails",
           run(make(desc=lines_with(replace={"## Occurrence Rate": "## Occurrence Rate: always"}))), False,
           has=["'## Occurrence Rate: always' is not one of the four"])
    inline = lines_with().replace("Always (1 of 1).\n## Tester Action and Workaround",
                                  "Always (1 of 1). ## Tester Action and Workaround")
    expect("heading not on its own line fails", run(make(desc=inline)), False,
           has=["missing heading line '## Tester Action and Workaround'"])
    expect("duplicated heading fails", run(make(desc=lines_with() + "## Additional Notes\n")), False,
           has=["'## Additional Notes' appears 2 times"])
    expect("heading with trailing spaces passes", run(make(desc=lines_with(replace={"## Issue Summary": "## Issue Summary  "}))),
           True)
    expect("text before the first heading fails", run(make(desc="Summary first\n" + lines_with())), False,
           has=["text before '## Issue Summary'"])
    expect("missing Where: line fails", run(make(desc=lines_with(drop="Where:"))), False,
           has=["'## Issue Summary' has no line starting 'Where:'"])
    one_line = lines_with(replace={"Scope:": "Scope: TA5; Flags: Mocklink; Link: MockLink, simulated drone 99999"},
                          drop="Flags:").replace("Link: MockLink, simulated drone 99999 (stand-in, not a real drone)\n", "")
    expect("Scope/Flags/Link on one line fails", run(make(desc=one_line)), False,
           has=["no line starting 'Flags:'", "no line starting 'Link:'"])

    # ---- data SAK appends by itself (each one alone, in a Dev hint line)
    sak = [
        ("74783aa0b32a91d484d1919e76d9ee0ad5148441", "full git hash"),
        ("1.73.0-staging-74783aa", "About label"),
        ("1.73.0-develop", "About label"),
        ("1.73.0-production", "About label"),
        ("built from staging-74783aa", "environment-hash7"),
        ("app 1.73.0 (74783aa)", "hash7 next to a version"),
        ("74783aa / 1.73.0", "hash7 next to a version"),
        ("git 74783aa", "git hash"),
        ("native 140", "native version"),
        ("Native version: 535", "native version"),
        ("versionCode 535", "native version code"),
        ("build time 2026-10-13 13:44", "build time"),
        ("compatibility 41", "compatibility number"),
        ("see the Sentry replay", "Sentry"),
        ("tenant 5f0c", "tenant"),
        ("Mozilla/5.0 (Linux; Android 16)", "user agent"),
        ("user agent Chrome", "user agent"),
        ("PX4 session 12", "session"),
        ("OBC session 7", "session"),
        ("session id: abc123", "session"),
        ("drone 4711 HW 5", "drone HW/FW"),
        ("FW v2.24.0-rc.20", "drone HW/FW"),
        ("firmware: 2.24.0", "drone HW/FW"),
        ("reporter jane.doe@example.com", "email address"),
        ("/shared/AbC_def-123", "flight share link"),
        ("https://web.staging.company.example/sites", "URL"),
        ("http://localhost:3001/api", "URL"),
        ("www.example.org/page", "URL"),
        ("web.staging.company.example", "Wingtra host name"),
        ("/sites/3f2a9c10-1b2c-4d5e-8f90-123456789abc", "route path"),
        ("/flights/12345", "route path"),
        ("**bold**", "markdown **"),
        ("![shot](shot.png)", "markdown link or image"),
        ("[CL-80007](x)", "markdown link or image"),
    ]
    for snippet, why in sak:
        expect(f"SAK pattern caught: {snippet}", run(make(desc=lines_with(insert_before_crit=["Dev hint: " + snippet]))),
               False, has=[f"description repeats {why}"])
    for snippet in ("since 1.73.0, not in 1.72.0", "Sites > Processing > Results", "the native layer logs an error",
                    "compatibility check passes", "#10303 made upAxis required", "deadbeef 1.73.0", "Gen II and RAY",
                    "TR_Z11_Zurich_47.4N, Android 16 on SM-X350"):
        expect(f"no SAK false positive: {snippet}", run(make(desc=lines_with(insert_before_crit=["Dev hint: " + snippet]))),
               True)
    expect("SAK pattern in the title fails", run(make(title=TITLE.replace("(Android 16", "(1.73.0-staging-74783aa"))),
           False, has=["title repeats About label"])

    # ---- simulated drone rule
    expect("flag-name-only loophole: MockLink in Flags and Link, no simulated word",
           run(make(desc=lines_with(replace={"Link:": "Link: MockLink drone 99999"}))), False,
           has=["no line starting 'Link:' says simulated"])
    expect("simulated word only in the Flags line fails",
           run(make(desc=lines_with(replace={"Flags:": "Flags: simulated, Mocklink", "Link:": "Link: drone 99999"}))),
           False, has=["no line starting 'Link:' says simulated"])
    expect("word and ID on different lines fail",
           run(make(desc=lines_with(replace={"Link:": "Link: MockLink, simulated drone (stand-in)",
                                             "Scope:": "Scope: Galaxy Tab Active5 Pro, drone 99999"}))),
           False, has=["together with the ID 99999"])
    v1 = {"kind": "real_drone", "vehicle_id": 1, "simulated": False}
    expect("ID substring loophole: ID 1 only in 'Steps: 1.'",
           run(make(desc=lines_with(replace={"Link:": "Link: simulated drone (stand-in)"}), draft={"link": v1})),
           False, has=["together with the ID 1 "])
    expect("ID 1 as a whole word on the Link line passes",
           run(make(desc=lines_with(replace={"Link:": "Link: simulated drone 1 (stand-in)"}), draft={"link": v1})), True)
    expect("ID 99 is not matched by 990",
           run(make(desc=lines_with(replace={"Link:": "Link: simulated drone 990"}),
                    draft={"link": {"kind": "mocklink", "vehicle_id": 99, "simulated": True}})),
           False, has=["together with the ID 99 "])
    expect("vehicle ID 0 (no drone) is not simulated",
           run(make(desc=lines_with(replace={"Link:": "Link: none"}),
                    draft={"link": {"kind": "none", "vehicle_id": 0, "simulated": False}})), True)
    expect("UDP SITL 9999 with only 'SITL' on the Link line fails (the word must be simulated or stand-in)",
           run(make(desc=lines_with(replace={"Link:": "Link: UDP SITL, drone 9999"}),
                    draft={"link": {"kind": "udp_sitl", "vehicle_id": 9999, "simulated": True}})), False)
    expect("UDP SITL 9999 written as the skill says passes",
           run(make(desc=lines_with(replace={"Link:": "Link: SITL, simulated drone 9999 (stand-in, not a real drone)"}),
                    draft={"link": {"kind": "udp_sitl", "vehicle_id": 9999, "simulated": True}})), True)
    expect("vehicle 9999 on a 'real_drone' kind still counts as simulated",
           run(make(desc=lines_with(replace={"Link:": "Link: drone 9999"}),
                    draft={"link": {"kind": "real_drone", "vehicle_id": 9999, "simulated": False}})),
           False, has=["simulated (vehicle 9999)"])
    expect("MockLink without vehicle_id needs only the word",
           run(make(desc=lines_with(replace={"Link:": "Link: MockLink, simulated drone (stand-in)"}),
                    draft={"link": {"kind": "mocklink", "vehicle_id": None, "simulated": True}})), True)
    expect("MockLink without vehicle_id and without the word fails",
           run(make(desc=lines_with(replace={"Link:": "Link: MockLink"}),
                    draft={"link": {"kind": "mocklink", "vehicle_id": None, "simulated": True}})),
           False, has=["no line starting 'Link:' says simulated"])
    expect("real drone 4711 needs no simulated word",
           run(make(desc=lines_with(replace={"Link:": "Link: real drone"}), draft={"link": dict(REAL)})), True)
    expect("vehicle_id as a digit string counts",
           run(make(desc=lines_with(replace={"Link:": "Link: MockLink drone 99999"}),
                    draft={"link": {"kind": "none", "vehicle_id": "99999", "simulated": False}})),
           False, has=["simulated (vehicle 99999)"])

    # ---- link loader: --link-json, else run_record, else draft.json link
    run_sim = write_json(os.path.join(TMP, "R-20261015-TA5-UPG-012-01.json"),
                         {"kind": "run", "id": "R-x", "link": dict(LINK), "evidence_dir": "tests/x/TA5"})
    run_real = write_json(os.path.join(TMP, "R-20261015-TA5-REAL-01.json"), {"kind": "run", "link": dict(REAL)})
    lie = {"kind": "none", "vehicle_id": None, "simulated": False}
    expect("run_record link wins over a draft.json that hides the simulated drone",
           run(make(desc=lines_with(replace={"Link:": "Link: none"}), draft={"run_record": run_sim, "link": lie})),
           False, has=["no line starting 'Link:' says simulated", "draft.json link says simulated=False"])
    expect("run_record set but missing fails",
           run(make(draft={"run_record": os.path.join(TMP, "nope", "R-missing.json")})), False,
           has=["run_record", "not found"])
    expect("source is a run record but run_record is not set fails",
           run(make(draft={"source": run_sim, "run_record": None})), False, has=["run_record is not set"])
    plain_run = write_json(os.path.join(TMP, "input_run.json"), {"kind": "run", "link": dict(LINK)})
    expect("source with kind run (any file name) needs run_record",
           run(make(draft={"source": plain_run, "run_record": None})), False, has=["run_record is not set"])
    expect("--link-json overrides draft.json run_record",
           run(make(draft={"run_record": run_real}), "--link-json", run_sim), True)
    expect("without --link-json the real-drone run_record disagrees with draft.json",
           run(make(draft={"run_record": run_real})), False, has=["draft.json link says simulated=True"])
    bare = write_json(os.path.join(TMP, "link.json"), dict(LINK))
    expect("--link-json with a bare link object",
           run(make(desc=lines_with(replace={"Link:": "Link: MockLink"})), "--link-json", bare),
           False, has=["no line starting 'Link:' says simulated"])
    expect("--link-json missing fails", run(make(), "--link-json", os.path.join(TMP, "missing.json")), False,
           has=["--link-json"])
    expect("draft.json link.simulated must be a boolean",
           run(make(draft={"link": {"kind": "mocklink", "vehicle_id": 99999, "simulated": None}})), False,
           has=["link.simulated must be true or false"])

    # ---- draft.json fields
    for bad in (0, 7, True, "3"):
        expect(f"criticality {bad!r} fails", run(make(draft={"criticality": bad})), False,
               has=["criticality must be an integer 1..6"])
    expect("criticality missing fails", run(make(draft={"criticality": DROP})), False, has=["draft.json has no criticality"])
    expect("description criticality differs from draft.json",
           run(make(desc=lines_with(replace={"Criticality": "Criticality (suggested): 4 - blocks OTA"}))), False,
           has=["description criticality 4 differs"])
    expect("no Criticality line fails", run(make(desc=lines_with(drop="Criticality"))), False,
           has=["Criticality (suggested): <1-6>"])
    expect("wrong title_chars/description_chars fail", run(make(counts=False)), False,
           has=["draft.json title_chars is 1", "draft.json description_chars is 1"])
    d = make()
    os.remove(os.path.join(d, "draft.json"))
    expect("missing draft.json fails", run(d), False, has=["draft.json is missing"])

    # ---- evidence: NUL detection, archives, binaries, recordings
    expect("text file without extension is scanned (NUL rule)",
           run(make(files={"pack/logs/logcat": b"I/WingtraCloud: login jane.doe@example.com\n"})), False,
           has=["pack/logs/logcat: contains a email"])
    expect("text with a .bin extension is scanned for secrets.env values",
           run(make(files={"pack/logs/dump.bin": f"pw={FAKE_SECRET}\n".encode()})), False,
           has=["pack/logs/dump.bin: contains a value from qa/secrets.env"])
    nul = b"log line\n\0\0binary" + b"\0" * 100
    expect("NUL in the first 8 KB makes a .txt binary, needs review",
           run(make(files={"pack/logs/qt.txt": nul})), False, has=["pack/logs/qt.txt: binary file not reviewed"])
    expect("reviewed binary .txt passes", run(make(files={"pack/logs/qt.txt": nul}, review=["pack/logs/qt.txt"])), True)
    late = b"a" * 9000 + b"\0" + b" jane.doe@example.com\n"
    expect("NUL after 8 KB stays text and is scanned", run(make(files={"pack/logs/late.log": late})), False,
           has=["pack/logs/late.log: contains a email"])
    blob = b"\0\1\2\3" * 10 + b"user=jane.doe@example.com" + b"\0" * 10
    expect("binary strings are scanned for emails",
           run(make(files={"pack/logs/msgs_1.msg": blob}, review=["pack/logs/msgs_1.msg"])), False,
           has=["pack/logs/msgs_1.msg: contains a email (in its strings)"])
    for rel, data in (("pack/logs.zip", b"PK\x03\x04" + b"\0" * 30), ("pack/logs.gz", b"\x1f\x8b\x08\0" + b"\0" * 20),
                      ("pack/blob.dat", b"PK\x03\x04zipped"), ("pack/notes.7z", b"text only")):
        expect(f"archive fails: {rel}", run(make(files={rel: data})), False, has=[f"{rel}: archive"])
    expect("unreviewed screenshot fails", run(make(files={"pack/shots/1.png": PNG})), False,
           has=["pack/shots/1.png: media file not reviewed"])
    expect("reviewed screenshot passes", run(make(files={"pack/shots/1.png": PNG}, review=["pack/shots/1.png"])), True)
    expect("screenshot changed since review fails",
           run(make(files={"pack/shots/1.png": PNG}, draft={"reviewed_binaries": {"pack/shots/1.png": "0" * 64}})),
           False, has=["pack/shots/1.png: changed since review"])
    expect(".DS_Store is ignored", run(make(raw={".DS_Store": b"\0\0\0\1Bud1\0"})), True)
    for name, data, ok, why in (
            ("61 s v0", mp4(61), False, "recording is 61.0 s"),
            ("59 s v0", mp4(59), True, None),
            ("60 s v0", mp4(60), True, None),
            ("61 s v1", mp4(61, version=1), False, "recording is 61.0 s"),
            ("59 s moov after mdat", mp4(59, mdat_first=True), True, None),
            ("61 s moov after a largesize mdat", mp4(61, mdat_first=True, largesize=True), False, "recording is 61.0 s")):
        expect(f"recording {name}", run(make(files={"pack/rec/rec.mp4": data}, review=["pack/rec/rec.mp4"])), ok,
               has=[why] if why else [])
    expect("unreadable .mov fails", run(make(files={"pack/rec/x.mov": b"\0\0\0\x10notamoov" + b"\0" * 8},
                                             review=["pack/rec/x.mov"])), False,
           has=["could not read the recording length"])
    p = os.path.join(TMP, "d61.mp4")
    with open(p, "wb") as f:
        f.write(mp4(61.5, version=1, mdat_first=True))
    truth("mp4_duration reads v1 after mdat", dc.mp4_duration(p) == 61.5, str(dc.mp4_duration(p)))

    # ---- secrets.env check
    expect("secrets.env value in the description fails",
           run(make(desc=lines_with(insert_before_crit=[f"Dev hint: {FAKE_SECRET}"]))), False,
           has=["description.txt: contains a value from qa/secrets.env"])
    redact.SECRETS_FILE = os.path.join(TMP, "no-secrets.env")
    expect("missing secrets.env fails", run(make()), False, has=["secrets were not checked"])
    expect("missing secrets.env with --no-secrets-file passes", run(make(), "--no-secrets-file"), True)
    empty = os.path.join(TMP, "empty-secrets.env")
    open(empty, "w").close()
    redact.SECRETS_FILE = empty
    expect("empty secrets.env fails", run(make()), False, has=["secrets were not checked"])
    redact.SECRETS_FILE, redact._SECRETS = SECRETS, None
    saved, dc.redact_mod = dc.redact_mod, None
    try:
        expect("redact.py missing fails", run(make()), False, has=["secrets were not checked"])
        expect("--redact-to without redact.py fails", run(make(), "--redact-to", os.path.join(TMP, "nr")), False,
               has=["--redact-to needs redact.py"])
    finally:
        dc.redact_mod = saved

    # ---- --redact-to
    log = (f"I/WingtraCloud: user jane.doe@example.com signed in\nAuthorization: Bearer abcdefghijklmnopqrstuvwxyz012\n"
           f"pw {FAKE_SECRET}\nnothing else\n").encode()
    files = {"pack/logs/qt.txt": log, "pack/shots/1.png": PNG}
    t = make(files=files, review=["pack/shots/1.png"])
    expect("pack log with email, bearer and secret fails without --redact-to", run(t), False,
           has=["pack/logs/qt.txt: contains a email", "bearer token", "value from qa/secrets.env"])
    out = os.path.join(TMP, "attach1")
    r = expect("--redact-to writes clean copies and passes", run(t, "--redact-to", out), True)
    with open(os.path.join(out, "pack/logs/qt.txt"), "rb") as f:
        copy = f.read()
    with open(os.path.join(t, "pack/logs/qt.txt"), "rb") as f:
        orig = f.read()
    truth("redacted copy has no email or secret", b"@example.com" not in copy and FAKE_SECRET.encode() not in copy
          and b"[EMAIL]" in copy and b"[REDACTED]" in copy)
    truth("original evidence is unchanged", orig == log)
    reds = {x["file"]: x for x in (r or {}).get("redactions", [])}
    truth("redactions list sha256 before/after for the log",
          reds.get("pack/logs/qt.txt", {}).get("sha256_before") == sha(log)
          and reds["pack/logs/qt.txt"]["sha256_after"] == sha(copy) and reds["pack/logs/qt.txt"]["changed"])
    truth("binary copied as is", reds.get("pack/shots/1.png", {}).get("changed") is False
          and open(os.path.join(out, "pack/shots/1.png"), "rb").read() == PNG)
    truth("authored files copied unchanged", all(reds.get(a, {}).get("changed") is False for a in dc.AUTHORED))
    truth("findings in the originals become notes", r and any(n.startswith("before redaction:") for n in r["notes"]))
    inner = os.path.join(t, "attach")
    expect("--redact-to inside the ticket dir passes", run(t, "--redact-to", inner), True)
    expect("--redact-to inside the ticket dir is idempotent", run(t, "--redact-to", inner), True)
    t2 = make(files={"pack/logs/console.log": b"GET /api/shared/AbCdEf123 200\n"})
    expect("a share path redact() does not mask keeps the copy dirty",
           run(t2, "--redact-to", os.path.join(TMP, "attach2")), False,
           has=["copy:pack/logs/console.log: contains a share link"])
    expect("redaction never fixes the description",
           run(make(desc=lines_with(insert_before_crit=["Dev hint: ask jane.doe@example.com"])), "--redact-to",
               os.path.join(TMP, "attach3")), False, has=["description.txt: redaction changed it"])
    t4 = make()
    expect("--redact-to equal to the ticket dir fails", run(t4, "--redact-to", t4), False,
           has=["must not be the ticket dir"])
    stale = os.path.join(TMP, "attach5")
    os.makedirs(stale)
    open(os.path.join(stale, "old.txt"), "w").close()
    expect("stale file in the redact dir fails", run(make(), "--redact-to", stale), False, has=["old.txt is not a copy"])
    t6 = make(files={"pack/logs.zip": b"PK\x03\x04" + b"\0" * 30})
    out6 = os.path.join(TMP, "attach6")
    expect("archive fails with --redact-to too", run(t6, "--redact-to", out6), False, has=["pack/logs.zip: archive"])
    truth("archive is not copied", not os.path.exists(os.path.join(out6, "pack/logs.zip")))

    # ---- whole drafts from the review
    sample = lines_with(replace={
        "Where:": "Where: Plane > Updates, 1.73.0-staging-74783aa, native 140, FW 2.24.0, tenant 5f0c",
        "Flags:": "Flags: OTADroneUpdates, Mocklink",
        "Link:": "Link: drone 99999 HW 5"})
    expect("review sample (flag name only, About label, native, FW, tenant) fails", run(make(desc=sample)), False,
           has=["no line starting 'Link:' says simulated", "About label", "native version", "drone HW/FW", "tenant"])
    fx = os.path.join(FIXTURES, "cl80006_original.md")
    if os.path.exists(fx):
        with open(fx, encoding="utf-8") as f:
            original = f.read()
        expect("cl80006 original as filed fails", run(make(desc=original)), False,
               has=["description is", "About label", "native version", "full git hash", "compatibility number",
                    "markdown **", "markdown link or image", "email address", "flight share link",
                    "heading line '# [CT] Android 1.73.0"])

    # ---- CLI wiring (subprocess, -I, never reads the real secrets: --no-secrets-file)
    script = os.path.join(RUNNER, "draft_check.py")
    p = subprocess.run([sys.executable, "-I", script, base, "--no-secrets-file"], capture_output=True, text=True,
                       timeout=60)
    truth("CLI exit 0 and JSON ok on a clean ticket", p.returncode == 0 and json.loads(p.stdout)["ok"] is True,
          p.stdout[-300:] + p.stderr[-300:])
    p = subprocess.run([sys.executable, "-I", script, make(title="bad"), "--no-secrets-file"], capture_output=True,
                       text=True, timeout=60)
    truth("CLI exit 1 on a bad ticket", p.returncode == 1 and json.loads(p.stdout)["ok"] is False)
    p = subprocess.run([sys.executable, "-I", script], capture_output=True, text=True, timeout=60)
    truth("CLI exit 2 without arguments", p.returncode == 2)


def main(argv):
    try:
        cases()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    fails = 0
    for name, ok, bad, probs in results:
        if not ok:
            fails += 1
        if not ok or "-v" in argv:
            print(f"{'OK  ' if ok else 'FAIL'} {name}" + ("" if ok else f": {'; '.join(bad)}"))
            if not ok and probs:
                print("     problems: " + " | ".join(probs))
    print(f"draft_check cases: {len(results) - fails}/{len(results)} pass")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
