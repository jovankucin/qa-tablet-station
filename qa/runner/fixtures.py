"""A case's test files (fixtures) and where the engine may copy them: engine piece 2 (2026-10-26).

A fixture is {name, path, sha256, delivery[, device_path]}. The engine copies only what a case pins:
- the source is a plain file on this Mac under tests/<folder>/files/ or qa/cases/fixtures/<CASE>/ (no symbolic link
  anywhere on the way, no ..), with the fixture's name, at most 2 GiB, and its sha256 is the one the case records;
- on a tablet it goes to device_path, which lies under /sdcard/Download/ and ends in the name (default
  /sdcard/Download/WingtraQA/<name>); on webpc044 to ~/qa-fixtures/<name>.
act.py deliver copies (and never overwrites a different file); autonomy.py checks the same rules without reading the
file. qa/app/case_schema.py checks device_path with the same pattern.
"""
import hashlib
import os
import posixpath
import re

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,150}")  # a file name, and each folder of a device path
SHA256 = re.compile(r"[0-9a-f]{64}")
_PART = r"[A-Za-z0-9][A-Za-z0-9._-]{0,150}"
SOURCE_DIRS = (re.compile(rf"tests/{_PART}/files/(?:{_PART}/)*{_PART}"),
               re.compile(rf"qa/cases/fixtures/{_PART}/(?:{_PART}/)*{_PART}"))
MAX_BYTES = 2 * 1024 ** 3
TABLET_ROOT = "/sdcard/Download/"
TABLET_DEFAULT_DIR = "/sdcard/Download/WingtraQA"
TABLET_MAX_FOLDERS = 5  # folders below Download
WEB_DIR = "qa-fixtures"  # in the home folder on webpc044
PARTIAL = ".qa-partial"  # the copy's name until its sha256 is checked on the device
PLATFORMS = {"tablet": ("tablet",), "web": ("web",), "both": ("tablet", "web")}


class FixtureError(ValueError):
    pass


def source(f, workspace):
    """Absolute path of the fixture's source on this Mac, checked without reading it."""
    if not isinstance(f, dict):
        raise FixtureError("a fixture must be {name, path, sha256, delivery}")
    name, path = f.get("name"), f.get("path")
    if not isinstance(name, str) or not NAME.fullmatch(name) or name.endswith(PARTIAL):
        raise FixtureError(f"name {name!r} is not a plain file name (letters, digits, . _ -; not *{PARTIAL})")
    if not isinstance(f.get("sha256"), str) or not SHA256.fullmatch(f["sha256"]):
        raise FixtureError(f"{name}: sha256 must be 64 lowercase hex")
    if not isinstance(path, str) or posixpath.normpath(path) != path or path.startswith("/") or "\\" in path \
            or ".." in path.split("/"):
        raise FixtureError(f"{name}: path {path!r} must be a plain path inside the workspace")
    if not any(d.fullmatch(path) for d in SOURCE_DIRS):
        raise FixtureError(f"{name}: {path} is outside the test-file folders (tests/<folder>/files/, "
                           "qa/cases/fixtures/<CASE>/)")
    if posixpath.basename(path) != name:
        raise FixtureError(f"{name}: {path} has another file name")
    ws = os.path.realpath(workspace)
    p = os.path.join(ws, *path.split("/"))
    if not os.path.lexists(p):
        raise FixtureError(f"{name}: {path} is not on this Mac")
    if os.path.realpath(p) != p:
        raise FixtureError(f"{name}: {path} goes through a symbolic link")
    if not os.path.isfile(p):
        raise FixtureError(f"{name}: {path} is not a plain file")
    if os.path.getsize(p) > MAX_BYTES:
        raise FixtureError(f"{name}: {path} is larger than 2 GiB")
    return p


def sha256_of(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tablet_dest(f):
    """Where the fixture goes on a tablet: device_path, or /sdcard/Download/WingtraQA/<name>."""
    name, dp = f["name"], f.get("device_path")
    if dp is None:
        return f"{TABLET_DEFAULT_DIR}/{name}"
    parts = dp[len(TABLET_ROOT):].split("/") if isinstance(dp, str) and dp.startswith(TABLET_ROOT) else []
    if not parts or posixpath.normpath(dp) != dp or len(parts) > TABLET_MAX_FOLDERS + 1 \
            or not all(NAME.fullmatch(x) for x in parts) or parts[-1] != name:
        raise FixtureError(f"{name}: device_path {dp!r} must lie under {TABLET_ROOT} (at most {TABLET_MAX_FOLDERS} "
                           f"folders deep) and end in /{name}")
    return dp


def web_dest(f):
    """Where the fixture goes on webpc044, relative to its home folder."""
    return f"{WEB_DIR}/{f['name']}"


def plan(case, workspace, digest=True, platforms=("tablet", "web")):
    """The case's fixtures for the given platforms, checked: [{name, src, sha256, size, tablet?, web?}]. With digest
    the source's sha256 must equal the case's (the file is read once). Two fixtures for one destination on one of the
    platforms must be the same file; the same file pinned twice is listed once."""
    out, seen = [], {}
    for f in case.get("fixtures") or []:
        src = source(f, workspace)
        item = {"name": f["name"], "src": src, "sha256": f["sha256"], "size": os.path.getsize(src)}
        if "tablet" in platforms:
            item["tablet"] = tablet_dest(f)
        if "web" in platforms:
            item["web"] = web_dest(f)
        for where in platforms:
            other = seen.setdefault((where, item[where]), item["sha256"])
            if other != item["sha256"]:
                raise FixtureError(f"{item['name']}: two fixtures of the case go to {item[where]}")
        if any(all(o[w] == item[w] for w in platforms) for o in out):
            continue
        if digest:
            got = sha256_of(src)
            if got != item["sha256"]:
                raise FixtureError(f"{item['name']}: the file on this Mac does not match the case (sha256 {got[:12]}…, "
                                   f"the case pins {item['sha256'][:12]}…)")
        out.append(item)
    return out
