"""Make a test DSM for a New test plan: the worker's "Make one for me" (qa-worker skill section 1b, 2026-11-04).

python3 -I -B qa/runner/make_dsm.py --lat <deg> --lon <deg> --res <metres> [--crs utm|4326] [--extent <metres>]
                                    [--base <metres>] --out tests/<YYYY-MM-DD>-<slug>/files/<name>.tif

Writes a north-up, single-band Float32 GeoTIFF with heights on EGM96 (vertical key 5773): a square of --extent
metres (default 1500 below 5 m/px, else 5000) centred on --lat/--lon, with gentle hills around --base metres
(default 500). Terrain, layout and keys are those of the 2026-10-09 terrain-resolution files the app accepted
(tests/2026-10-09-terrain-resolution/tools/make_test_files.py): --lat 47.4 --lon 8.55 --res 1 --base 450 makes the
Zurich DSM that TERR-004 c8 pins again, byte for byte.
Coordinate systems: --crs utm (the default), WGS84 UTM in zone floor((lon+180)/6)+1 (60 at lon 180), EPSG 326zz
from the equator north and 327zz south of it; or --crs 4326. No other.
--out: a new file in tests/<YYYY-MM-DD>-<slug>/files/ of this workspace (a relative path is read from the workspace
folder) with a plain name ending in .tif (letters, digits, . _ -), as qa/runner/fixtures.py takes for a case's test
file. Refused: an existing file, .., a symbolic link on the way, a subfolder and any other folder, the app's attached
files in tests/test-files-<task id>/ too. Missing folders are made. The file is written under a temporary name in
that folder, read back and checked, and gets its name only then.

Prints one JSON object and exits 0:
  {"path", "sha256", "size", "epsg", "vertical_epsg", "res_m", "app_res_m", "flight_terrain_ok"}
path is relative to the workspace, as a case's fixture names it. app_res_m is the resolution the app measures for the
file (dsm_geotiff.app_resolution). flight_terrain_ok is the app's rule for terrain in a flight import,
Math.round(app_res_m) <= 28 (WingtraCloud sky-staging 82e23ff4a, MapFilesUpload.tsx:189).
Refused or failed: prints {"error": "..."} and exits 2; no .tif is left behind.
"""
import argparse
import json
import math
import os
import posixpath
import re
import sys
import tempfile

RUNNER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RUNNER)
import dsm_geotiff as geo  # noqa: E402
import fixtures  # noqa: E402

WORKSPACE = os.path.dirname(os.path.dirname(RUNNER))
LAT_MAX = {"utm": 84.0, "4326": 89.9}
RES_MAX = 100.0
EXTENT_MAX = 50000.0
PX_MIN, PX_MAX = 10, 5000            # pixels per side; 5000 x 5000 takes about 15 s and 60 MB
BASE_MIN, BASE_MAX = -500.0, 9000.0
AMP_M = 30.0                         # hill height, as the 10-09 files
VERTICAL_EPSG = 5773                 # EGM96 height
FLIGHT_RES_MAX = 28                  # DEFAULT_GROUND_RESOLUTION, metres per pixel
NODATA = "-9999"
FOLDER = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}-[a-z0-9][a-z0-9-]{0,80}")  # a test session folder


class Refused(Exception):
    """A plain reason, printed as {"error": ...} with exit 2."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Refused(message)


def parser():
    p = _Parser(prog="make_dsm.py", allow_abbrev=False,
                description="Make a test DSM (single-band Float32 GeoTIFF, EGM96 heights) for a New test plan.")
    p.add_argument("--lat", type=float, required=True, help="centre latitude, degrees")
    p.add_argument("--lon", type=float, required=True, help="centre longitude, degrees")
    p.add_argument("--res", type=float, required=True, help="pixel size, metres")
    p.add_argument("--crs", choices=("utm", "4326"), default="utm")
    p.add_argument("--extent", type=float, help="side of the square, metres (default 1500 below 5 m/px, else 5000)")
    p.add_argument("--base", type=float, default=500.0, help="ground height, metres (default 500)")
    p.add_argument("--out", required=True, help="tests/<YYYY-MM-DD>-<slug>/files/<name>.tif, a new file")
    return p


def js_round(x):
    """JavaScript's Math.round: a half goes up (Python's round() goes to the even number)."""
    return math.floor(x + 0.5)


def flight_terrain_ok(app_res_m):
    return js_round(app_res_m) <= FLIGHT_RES_MAX


def utm_epsg(lat, lon):
    zone = min(60, int(math.floor((lon + 180.0) / 6.0)) + 1)
    return (32600 if lat >= 0 else 32700) + zone


def layout(a):
    """The raster asked for: {epsg, px, ox, oy, sx, sy, res, base}; Refused when it is out of range."""
    lat_max = LAT_MAX[a.crs]
    if not -lat_max <= a.lat <= lat_max:
        raise Refused("--lat: -84 to 84 for UTM (-89.9 to 89.9 with --crs 4326)" if a.crs == "utm"
                      else "--lat: -89.9 to 89.9")
    if not -180.0 <= a.lon <= 180.0:
        raise Refused("--lon: -180 to 180")
    if not 0 < a.res <= RES_MAX:
        raise Refused("--res: more than 0 and at most 100 metres")
    extent = a.extent if a.extent is not None else (1500.0 if a.res < 5 else 5000.0)
    if not 0 < extent <= EXTENT_MAX:
        raise Refused("--extent: more than 0 and at most 50000 metres")
    if not BASE_MIN <= a.base <= BASE_MAX:
        raise Refused("--base: -500 to 9000 metres")
    side = extent / a.res
    if not side < PX_MAX + 0.5:
        raise Refused(f"the DSM would be {side:.0f} x {side:.0f} px, more than {PX_MAX}: use a larger --res or a "
                      "smaller --extent")
    px = int(round(side))
    if px < PX_MIN:
        raise Refused(f"the DSM would be {px} x {px} px, fewer than {PX_MIN}: use a larger --extent")
    if a.crs == "4326":
        dlon, dlat = geo.deg_pixel_for_metres(a.res, a.lat)
        r = {"epsg": 4326, "ox": a.lon - px * dlon / 2, "oy": a.lat + px * dlat / 2, "sx": dlon, "sy": dlat}
        corners = [(r["ox"] + i * px * dlon, r["oy"] - j * px * dlat) for i in (0, 1) for j in (0, 1)]
    else:
        epsg = utm_epsg(a.lat, a.lon)
        x, y = geo.utm_forward(a.lon, a.lat, epsg % 100)
        r = {"epsg": epsg, "ox": x - px * a.res / 2, "oy": y + px * a.res / 2, "sx": a.res, "sy": a.res}
        corners = [geo.utm_inverse(r["ox"] + i * px * a.res, r["oy"] - j * px * a.res, epsg % 100, epsg >= 32700)
                   for i in (0, 1) for j in (0, 1)]
    if not all(-180.0 <= lon <= 180.0 and -90.0 < lat < 90.0 for lon, lat in corners):
        raise Refused("the area crosses the 180th meridian or a pole: move it or use a smaller --extent")
    r.update(px=px, res=a.res, base=a.base)
    return r


def target(out, workspace):
    """(path relative to the workspace, files folder, file) for --out, checked before anything is written; Refused
    unless it is a new plain .tif in tests/<YYYY-MM-DD>-<slug>/files/ with no symbolic link on the way."""
    where = "--out: only a new file tests/<YYYY-MM-DD>-<slug>/files/<name>.tif"
    if not out or "\\" in out or "\0" in out:
        raise Refused(where)
    if ".." in out.split("/"):
        raise Refused("--out: no .. in the path")
    ws = os.path.realpath(workspace)
    rel = out
    if out.startswith("/"):
        for base in (os.path.abspath(workspace).rstrip("/") + "/", ws.rstrip("/") + "/"):
            if out.startswith(base):
                rel = out[len(base):]
                break
        else:
            raise Refused(f"--out: {out} is outside this workspace's tests folder")
    parts = rel.split("/")
    if posixpath.normpath(rel) != rel or len(parts) != 4 or parts[0] != "tests" or parts[2] != "files":
        raise Refused(where)
    folder, name = parts[1], parts[3]
    if not FOLDER.fullmatch(folder):
        raise Refused(f"--out: {folder} is not a test session folder (YYYY-MM-DD-<slug>, lower case)")
    if not fixtures.NAME.fullmatch(name) or not name.endswith(".tif"):
        raise Refused("--out: a plain file name ending in .tif (letters, digits, . _ -)")
    tests = os.path.join(ws, "tests")
    files_dir = os.path.join(tests, folder, "files")
    for d in (tests, os.path.dirname(files_dir), files_dir):
        if os.path.islink(d):
            raise Refused(f"--out: {os.path.relpath(d, ws)} is a symbolic link")
        if os.path.lexists(d) and not os.path.isdir(d):
            raise Refused(f"--out: {os.path.relpath(d, ws)} is not a folder")
    if not os.path.isdir(tests):
        raise Refused("--out: this workspace has no tests folder")
    dest = os.path.join(files_dir, name)
    if os.path.lexists(dest):
        raise Refused(f"--out: {rel} already exists: choose a new name")
    return rel, files_dir, dest


def make_folders(files_dir):
    """Make tests/<folder>/files/ when missing; Refused when a link appeared on the way meanwhile."""
    for d in (os.path.dirname(files_dir), files_dir):
        try:
            os.mkdir(d)
        except FileExistsError:
            pass
    if os.path.realpath(files_dir) != files_dir or not os.path.isdir(files_dir):
        raise Refused("--out: the folder goes through a symbolic link")


def check_file(path, r):
    """Read the file back with the independent reader and measure it as the app does: (app_res_m, epsg)."""
    info = geo.read_geotiff(path)
    keys = info["geokeys"]
    if r["epsg"] == 4326:
        epsg = keys.get(2048)
        crs_ok = keys.get(1024) == 2 and epsg == 4326 and 3072 not in keys
    else:
        epsg = keys.get(3072)
        crs_ok = keys.get(1024) == 1 and epsg == r["epsg"] and 2048 not in keys
    problems = [what for what, ok in (
        ("size", (info["width"], info["height"]) == (r["px"], r["px"])),
        ("pixel size", info["pixel_scale"] == [r["sx"], r["sy"]]),
        ("origin", info["tiepoint"] == [r["ox"], r["oy"]]),
        ("coordinate system", crs_ok),
        ("vertical key", keys.get(4096) == VERTICAL_EPSG),
        ("Float32 deflate", info["compression"] == 8 and info["sample_format"] == 3),
        ("nodata", info["nodata"] == NODATA),
        ("heights", r["base"] - 1.2 * AMP_M <= info["vmin"] <= info["vmax"] <= r["base"] + 1.2 * AMP_M),
    ) if not ok]
    if problems:
        raise Refused("the file read back is wrong (" + ", ".join(problems) + "): not written")
    app_res = geo.app_resolution(epsg, *info["tiepoint"], *info["pixel_scale"], info["width"], info["height"])[0]
    if not abs(app_res - r["res"]) <= 0.02 * r["res"]:
        raise Refused(f"the app would measure {app_res:.4f} m for the {r['res']:g} m asked: not written")
    return app_res, epsg


def make(argv, workspace):
    """Write the DSM and return the result; Refused when the arguments or --out do not fit."""
    a = parser().parse_args(argv)
    r = layout(a)
    rel, files_dir, dest = target(a.out, workspace)
    make_folders(files_dir)
    fd, tmp = tempfile.mkstemp(prefix=".make_dsm-", suffix=".part", dir=files_dir)
    os.close(fd)
    try:
        geo.write_geotiff(tmp, r["px"], r["px"], r["ox"], r["oy"], r["sx"], r["sy"],
                          geo.smooth_terrain(base_m=r["base"], amp_m=AMP_M, wavelength_px=max(8.0, 600.0 / r["res"])),
                          epsg=r["epsg"], vertical_epsg=VERTICAL_EPSG)
        app_res, epsg = check_file(tmp, r)
        digest, size = fixtures.sha256_of(tmp), os.path.getsize(tmp)
        os.chmod(tmp, 0o644)
        try:
            os.link(tmp, dest)  # the name only now, and never over a file that appeared meanwhile
        except FileExistsError:
            raise Refused(f"--out: {rel} already exists: choose a new name")
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return {"path": rel, "sha256": digest, "size": size, "epsg": epsg, "vertical_epsg": VERTICAL_EPSG,
            "res_m": r["res"], "app_res_m": round(app_res, 4), "flight_terrain_ok": flight_terrain_ok(app_res)}


def main(argv=None, out=None):
    """Print one JSON object; 0 when the DSM was made, 2 when refused or failed."""
    out = out or sys.stdout
    try:
        result, code = make(sys.argv[1:] if argv is None else argv, WORKSPACE), 0
    except Refused as e:
        result, code = {"error": str(e)}, 2
    except Exception as e:  # the caller reads one JSON object, whatever went wrong
        result, code = {"error": f"could not make the DSM: {type(e).__name__}: {e}"}, 2
    out.write(json.dumps(result) + "\n")
    out.flush()
    return code


if __name__ == "__main__":
    sys.exit(main())
