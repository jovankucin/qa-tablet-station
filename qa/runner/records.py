"""Record files and the hash-chained ledger (qa/schema/SCHEMA.md sections 0 and 7).

A record is one canonical JSON file. Once written it is hashed into the ledger and
never rewritten; a correction is a new record with "supersedes".
"""
import datetime
import fcntl
import glob
import hashlib
import json
import os
import re

from redact import redact_obj

QA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKSPACE = os.path.dirname(QA_DIR)
LEDGER_DIR = os.path.join(QA_DIR, "ledger")
ZERO = "0" * 64


def canonical(obj):
    return json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def now_utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def rel(path):
    return os.path.relpath(os.path.abspath(path), WORKSPACE)


def write_record(path, obj, overwrite=False):
    """Write a record canonically. Refuses to overwrite unless asked (drafts only)."""
    for k in ("kind", "schema", "id", "created", "author"):
        if k not in obj:
            raise ValueError(f"record {obj.get('id')} lacks header field {k}")
    obj.setdefault("supersedes", None)
    data = canonical(obj).encode("utf-8")
    if os.path.exists(path) and not overwrite:
        if open(path, "rb").read() == data:
            return sha256_bytes(data)
        raise FileExistsError(f"{rel(path)} exists; records are immutable, write a superseding record")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    return sha256_bytes(data)


def _ledger_files():
    if not os.path.isdir(LEDGER_DIR):
        return []
    return sorted(os.path.join(LEDGER_DIR, f) for f in os.listdir(LEDGER_DIR) if f.endswith(".jsonl"))


def _last_line(path):
    with open(path, "rb") as f:
        lines = f.read().splitlines()
    return lines[-1] if lines else None


def ledger_head():
    """(seq, sha256 of last line bytes) across all ledger days, or (0, ZERO)."""
    for path in reversed(_ledger_files()):
        last = _last_line(path)
        if last:
            return json.loads(last)["seq"], sha256_bytes(last)
    return 0, ZERO


def append(entry, day=None):
    """Append one ledger line under an exclusive lock. Returns the written entry."""
    os.makedirs(LEDGER_DIR, exist_ok=True)
    day = day or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    path = os.path.join(LEDGER_DIR, f"{day}.jsonl")
    lock_path = os.path.join(LEDGER_DIR, ".lock")
    with open(lock_path, "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            seq, prev = ledger_head()
            e = {"seq": seq + 1, "prev": prev, "t": now_utc()}
            e.update(redact_obj(entry))
            line = json.dumps(e, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            with open(path, "ab") as f:
                f.write(line.encode("utf-8") + b"\n")
            return e
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)


def hash_record(path, session="-", note=None):
    """Hash an existing record file into the ledger (src=record)."""
    with open(path, "rb") as f:
        data = f.read()
    obj = json.loads(data)
    entry = {
        "src": "record",
        "session": session,
        "target": "-",
        "tool": "-",
        "argv": [],
        "exit": None,
        "out_sha256": None,
        "ref": {"kind": obj["kind"], "id": obj["id"], "path": rel(path), "file_sha256": sha256_bytes(data)},
    }
    if note:
        entry["note"] = note
    return append(entry)


def _reserve_event(campaign, day):
    """Reserve qa/runs/<campaign>/event/E-<yyyymmdd>-<nnn>.json with O_EXCL, so parallel writers never share an id.
    The number continues after the highest one of that day in any campaign; the scan and the reservation happen
    under one lock on qa/runs/.event.lock, so writers in different campaigns do not pick the same number."""
    runs = os.path.join(QA_DIR, "runs")
    os.makedirs(runs, exist_ok=True)
    rx = re.compile(rf"^E-{day}-(\d{{3,}})\.json$")
    with open(os.path.join(runs, ".event.lock"), "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            taken = [int(m.group(1)) for d in glob.glob(os.path.join(runs, "*", "event"))
                     for f in os.listdir(d) for m in [rx.match(f)] if m]
            folder = os.path.join(runs, campaign, "event")
            os.makedirs(folder, exist_ok=True)
            n = max(taken, default=0) + 1
            while True:
                eid = f"E-{day}-{n:03d}"
                path = os.path.join(folder, eid + ".json")
                try:
                    os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644))
                    return eid, path
                except FileExistsError:
                    n += 1
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)


def side_effect(campaign, fields, session="-"):
    """Write and hash one side_effect event record (SCHEMA.md section 6, G3). Returns its id."""
    eid, path = _reserve_event(campaign, now_utc()[:10].replace("-", ""))
    rec = {"kind": "event", "schema": 1, "id": eid, "created": now_utc(), "author": "runner", "supersedes": None,
           "type": "side_effect"}
    rec.update(fields)
    write_record(path, rec, overwrite=True)  # the file is the empty placeholder reserved above
    hash_record(path, session=session, note="side effect")
    return eid


def iter_ledger():
    for path in _ledger_files():
        with open(path, "rb") as f:
            for raw in f.read().splitlines():
                if raw.strip():
                    yield path, raw
