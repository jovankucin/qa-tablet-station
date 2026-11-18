"""Test files the person attaches on the Run form (qa/app/DESIGN.md section 8.13).

A file lives at tests/test-files-<ClickUp task id>/files/<name>, a folder act.py deliver already accepts as a fixture
source (qa/runner/fixtures.py), next to tests/test-files-<task id>/files.json, the manifest: [{name, size, sha256,
added}]. The browser sends a file in pieces of at most PIECE_MAX bytes, in order (offset 0 first); the last piece
checks the size, hashes the file and moves it into place. Only files in the manifest are listed or removed: nothing
else in the folder is ever touched. Files stay on this Mac; the engine copies them only to the test devices, and only
when a case version pins them by sha256.
"""
import fcntl
import hashlib
import json
import os
import re
import shutil
import sys
import time

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(APP_DIR), "runner"))
import fixtures  # noqa: E402
import records  # noqa: E402
from writes import WriteError  # noqa: E402

TASK_RE = re.compile(r"[A-Za-z0-9-]{3,40}")
EXTENSIONS = (".tif", ".tiff", ".las", ".laz", ".zip")
MAX_BYTES = fixtures.MAX_BYTES        # 2 GiB, as for any test file
PIECE_MAX = 16 * 1024 * 1024          # one PUT; the browser sends 8 MiB pieces
FREE_MARGIN = 512 * 1024 * 1024       # keep this much disk free
PART = ".qa-upload"                   # the file's name until its last piece arrived
MAX_FILES = 20
STALE_PART_S = 2 * 3600               # an unfinished upload untouched this long is dropped


def folder_rel(task_id):
    if not isinstance(task_id, str) or not TASK_RE.fullmatch(task_id):
        raise WriteError(400, "task: a ClickUp task id from today's list")
    return f"tests/test-files-{task_id}"


def check_name(name):
    if not isinstance(name, str) or not fixtures.NAME.fullmatch(name) or name.endswith((fixtures.PARTIAL, PART)):
        raise WriteError(400, "name: a plain file name (letters, digits, . _ -)")
    if not name.lower().endswith(EXTENSIONS):
        raise WriteError(400, "name: only " + ", ".join(EXTENSIONS) + " files")
    return name


def _paths(root, task_id):
    base = os.path.join(root, *folder_rel(task_id).split("/"))
    return base, os.path.join(base, "files"), os.path.join(base, "files.json")


def _manifest(path):
    try:
        with open(path, encoding="utf-8") as f:
            obj = json.load(f)
    except (OSError, ValueError):
        return []
    return [x for x in obj if isinstance(x, dict) and isinstance(x.get("name"), str)] if isinstance(obj, list) else []


def _save_manifest(path, items):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=1, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class _Lock:
    def __init__(self, base):
        os.makedirs(base, exist_ok=True)
        self.path = os.path.join(base, ".lock")

    def __enter__(self):
        self.f = open(self.path, "a")
        fcntl.flock(self.f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


def _sweep(files, now=None):
    """Drop unfinished uploads nobody has touched for STALE_PART_S (a closed tab, a dropped connection)."""
    now = now or time.time()
    try:
        names = os.listdir(files)
    except OSError:
        return
    for n in names:
        p = os.path.join(files, n)
        if n.endswith(PART) and os.path.isfile(p) and not os.path.islink(p) and now - os.path.getmtime(p) > STALE_PART_S:
            os.remove(p)


def _in_place(files, x):
    p = os.path.join(files, x["name"])
    return os.path.isfile(p) and not os.path.islink(p) and os.path.getsize(p) == x.get("size")


def list_files(root, task_id):
    """The attached files that are still in place with their recorded size: [{name, size, sha256, added, path}]."""
    base, files, man = _paths(root, task_id)
    if os.path.isdir(files):
        with _Lock(base):
            _sweep(files)
    out = []
    for x in _manifest(man):
        if _in_place(files, x):
            out.append({"name": x["name"], "size": x["size"], "sha256": x.get("sha256"), "added": x.get("added"),
                        "path": f"{folder_rel(task_id)}/files/{x['name']}"})
    return out


def put_piece(root, task_id, name, offset, total, length, read):
    """Write one piece (length bytes from read(n)) at offset of a file of total bytes. Returns {done, received}
    and, after the last piece, the manifest entry."""
    check_name(name)
    base, files, man = _paths(root, task_id)
    if not isinstance(total, int) or not 0 < total <= MAX_BYTES:
        raise WriteError(413, "size: 1 byte to 2 GiB")
    if not isinstance(offset, int) or offset < 0 or not 0 < length <= PIECE_MAX or offset + length > total:
        raise WriteError(400, "piece: offset and length must lie inside the file")
    with _Lock(base):
        os.makedirs(files, exist_ok=True)
        dest, part = os.path.join(files, name), os.path.join(files, name + PART)
        items = _manifest(man)
        if offset == 0:
            _sweep(files)
            # an entry whose file is gone or changed (by hand) no longer holds the name
            stale = [x for x in items if x["name"] == name and not _in_place(files, x)]
            if stale:
                items = [x for x in items if x not in stale]
                _save_manifest(man, items)
        if name in [x["name"] for x in items] or os.path.lexists(dest):
            raise WriteError(409, "a file with this name is already attached; remove it first")
        if offset == 0:
            parts = [n for n in os.listdir(files) if n.endswith(PART) and n != name + PART]
            if len(items) + len(parts) >= MAX_FILES:
                raise WriteError(409, f"at most {MAX_FILES} files per test")
        # every piece: what is left of this file must fit with the margin to spare (other uploads may run too)
        if shutil.disk_usage(files).free < (total - offset) + FREE_MARGIN:
            if os.path.isfile(part) and not os.path.islink(part):
                os.remove(part)
            raise WriteError(507, "not enough free disk space on this Mac for this file")
        if offset == 0:
            fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
        else:
            if not os.path.isfile(part) or os.path.islink(part) or os.path.getsize(part) != offset:
                raise WriteError(409, "pieces out of order; start this file again")
            fd = os.open(part, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
        got = 0
        try:
            while got < length:
                chunk = read(min(1 << 20, length - got))
                if not chunk:
                    break
                os.write(fd, chunk)
                got += len(chunk)
            os.fsync(fd)
        finally:
            os.close(fd)
        if got != length:
            os.remove(part)
            raise WriteError(400, "incomplete piece; start this file again")
        if offset + length < total:
            return {"done": False, "received": offset + length}
        if os.path.getsize(part) != total:
            os.remove(part)
            raise WriteError(400, "the file arrived with another size; start it again")
        h = hashlib.sha256()
        with open(part, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        os.replace(part, dest)
        entry = {"name": name, "size": total, "sha256": h.hexdigest(), "added": records.now_utc()}
        try:
            _save_manifest(man, items + [entry])
        except OSError:
            os.replace(dest, part)  # not recorded: back to an unfinished upload, which remove and the sweep clear
            raise WriteError(507, "could not record the file (disk full?); start it again") from None
        return {"done": True, "received": total, "file": dict(entry, path=f"{folder_rel(task_id)}/files/{name}")}


def remove(root, task_id, name):
    """Remove one attached file (or an unfinished upload of it); never anything the app did not attach."""
    check_name(name)
    base, files, man = _paths(root, task_id)
    with _Lock(base):
        items = _manifest(man)
        part = os.path.join(files, name + PART)
        if os.path.isfile(part) and not os.path.islink(part):
            os.remove(part)
            if name not in [x["name"] for x in items]:
                return {"removed": name, "unfinished": True}
        if name not in [x["name"] for x in items]:
            raise WriteError(404, "no such attached file")
        p = os.path.join(files, name)
        if os.path.isfile(p) and not os.path.islink(p):
            os.remove(p)
        _save_manifest(man, [x for x in items if x["name"] != name])
        return {"removed": name}
