"""Cases for make_dsm.py and dsm_geotiff.py (2026-11-04): the round trip (one sample, the geokeys, EGM96), the app's
resolution around 28 m and its rounding, a southern UTM zone, EPSG:4326, the Zurich DSM that TERR-004 c8 pins made
again byte for byte, and each refusal (latitude, longitude, resolution, size, an --out path outside
tests/<folder>/files/, .., symbolic links, an existing file) with nothing left behind.

python3 -I -B qa/runner/tests/test_make_dsm.py [-v]
Exit 0 when every case passes. Works in temporary workspaces: the real tests folder is never written. The command
line runs only for refusals, which write nothing.
"""
import io
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
import dsm_geotiff as geo  # noqa: E402
import fixtures  # noqa: E402
import make_dsm  # noqa: E402

SCRIPT = os.path.join(RUNNER, "make_dsm.py")
DIR = "tests/2026-11-04-dsm-check/files"
# sha256 of files in tests/2026-10-09-terrain-resolution/files/; the 1 m Zurich one is the fixture of TERR-004 c8
SHA_10_09 = {"Z11_Zurich_47.4N_1m_UTM32632": "6be12f33383528a594e973bcc6ff1660ba8f623b03cb32b1566840ecda25c895",
             "Z11_Zurich_47.4N_27.5m_UTM32632": "6b523e1da03e1a745da0fe40cbd5bc9853dc4c0d85aa1b4e2eb852a860edf258",
             "Z11_Helsinki_60.2N_27.5m_EPSG4326": "43f5d24fce0100b80a4d07fe24ebb9197c4788cc918446f8aca3e9b695bb2080"}
# what the app measured for the 2026-10-09 files (their manifest.json)
MEASURED = {"Z12_Nairobi_1.3S_27.9m_UTM32737": 28.0473, "Z10_Longyearbyen_78.2N_28.1m_UTM32633": 27.9974,
            "Z11_Zurich_47.4N_27.5m_UTM32632": 27.5147, "Z11_Zurich_47.4N_28.3m_UTM32632": 28.3151}

results = []


def truth(name, ok, detail=""):
    results.append((name, bool(ok), detail))


def run(args, ws):
    """make_dsm.main in this process against the workspace ws: (exit code, the one JSON object it printed)."""
    saved, buf = make_dsm.WORKSPACE, io.StringIO()
    make_dsm.WORKSPACE = ws
    try:
        code = make_dsm.main(args, out=buf)
    finally:
        make_dsm.WORKSPACE = saved
    lines = buf.getvalue().splitlines()
    try:
        obj = json.loads(lines[0]) if len(lines) == 1 else {"lines": lines}
    except ValueError:
        obj = {"lines": lines}
    return code, obj


def made(ws, name, lat, lon, res, *extra):
    return run(["--lat", str(lat), "--lon", str(lon), "--res", str(res), "--out", f"{DIR}/{name}", *extra], ws)


def tree(ws):
    """Every file and link under the workspace (relative paths)."""
    out = set()
    for root, dirs, files in os.walk(ws):
        for n in dirs + files:
            p = os.path.join(root, n)
            if os.path.islink(p) or not os.path.isdir(p):
                out.add(os.path.relpath(p, ws))
    return out


def leftovers(ws):
    return sorted(p for p in tree(ws) if os.path.basename(p).startswith(".make_dsm-"))


def round_trip(ws):
    code, r = made(ws, "Zurich_47.4N_27.5m_UTM.tif", 47.4, 8.55, 27.5, "--base", "450")
    truth("round trip: exit 0", code == 0, r)
    if code:
        return
    p = os.path.join(ws, *r["path"].split("/"))
    truth("round trip: the keys of the result", list(r) == ["path", "sha256", "size", "epsg", "vertical_epsg", "res_m",
                                                            "app_res_m", "flight_terrain_ok"], r)
    truth("round trip: path relative, in the folder asked", r["path"] == f"{DIR}/Zurich_47.4N_27.5m_UTM.tif", r)
    truth("round trip: sha256 and size of the file", r["sha256"] == fixtures.sha256_of(p)
          and r["size"] == os.path.getsize(p), r)
    truth("round trip: UTM 32632, EGM96, 27.5 m", (r["epsg"], r["vertical_epsg"], r["res_m"]) == (32632, 5773, 27.5), r)
    info = geo.read_geotiff(p)
    k = info["geokeys"]
    truth("round trip: keys 1024=1 (projected), 3072=32632, 4096=5773, no 2048",
          k.get(1024) == 1 and k.get(3072) == 32632 and k.get(4096) == 5773 and 2048 not in k, k)
    truth("round trip: 182 x 182 px, pixel 27.5 m, Float32 deflate, nodata -9999",
          (info["width"], info["height"]) == (182, 182) and info["pixel_scale"] == [27.5, 27.5]
          and (info["compression"], info["sample_format"], info["nodata"]) == (8, 3, "-9999"), info)
    fn = geo.smooth_terrain(base_m=450.0, amp_m=30.0, wavelength_px=max(8.0, 600.0 / 27.5))
    want = struct.unpack("<f", struct.pack("<f", fn(37, 101)))[0]
    truth("round trip: one sample (col 37, row 101) as written", geo.sample(p, 37, 101) == want,
          (geo.sample(p, 37, 101), want))
    x, y = geo.utm_forward(8.55, 47.4, 32)
    truth("round trip: centred on the point asked", info["tiepoint"] == [x - 182 * 27.5 / 2, y + 182 * 27.5 / 2], info)
    truth("round trip: the same bytes as the 10-09 Zurich 27.5 m UTM file",
          r["sha256"] == SHA_10_09["Z11_Zurich_47.4N_27.5m_UTM32632"], r["sha256"])
    truth("round trip: the app's measure, as the 10-09 manifest",
          r["app_res_m"] == MEASURED["Z11_Zurich_47.4N_27.5m_UTM32632"] and r["flight_terrain_ok"] is True, r)
    truth("round trip: a case can pin it (fixtures.source)",
          fixtures.source({"name": "Zurich_47.4N_27.5m_UTM.tif", "path": r["path"], "sha256": r["sha256"],
                           "delivery": "engine"}, ws) == os.path.realpath(p))
    truth("round trip: mode 644, no temporary file left", oct(os.stat(p).st_mode & 0o777) == "0o644"
          and not leftovers(ws), (oct(os.stat(p).st_mode), leftovers(ws)))


def around_28(ws):
    truth("Math.round: 28.5 goes up to 29 (Python's round gives 28)", make_dsm.js_round(28.5) == 29 and round(28.5) == 28)
    truth("rule: 28.49 is fine, 28.5 is too coarse",
          make_dsm.flight_terrain_ok(28.49) is True and make_dsm.flight_terrain_ok(28.5) is False)
    for res, ok in ((27.5, True), (28.4, True), (28.6, False)):
        code, r = made(ws, f"Zurich_47.4N_{res:g}m_EPSG4326.tif", 47.4, 8.55, res, "--crs", "4326")
        truth(f"4326 {res} m: the app measures {res} m, flight terrain {ok}",
              code == 0 and abs(r["app_res_m"] - res) < 1e-3 and r["flight_terrain_ok"] is ok, r)
    for name, lat, lon, res in (("Z12_Nairobi_1.3S_27.9m_UTM32737", -1.29, 36.82, 27.9),
                                ("Z10_Longyearbyen_78.2N_28.1m_UTM32633", 78.22, 15.65, 28.1),
                                ("Z11_Zurich_47.4N_28.3m_UTM32632", 47.4, 8.55, 28.3)):
        code, r = made(ws, name + ".tif", lat, lon, res)
        truth(f"{name}: the app's measure as on 10-09 ({MEASURED[name]}), rounds to 28",
              code == 0 and r["app_res_m"] == MEASURED[name] and r["flight_terrain_ok"] is True, r)
    code, r = made(ws, "Zurich_47.4N_28.6m_UTM.tif", 47.4, 8.55, 28.6)
    truth("UTM 28.6 m: too coarse for flight terrain", code == 0 and r["flight_terrain_ok"] is False
          and 28.5 < r["app_res_m"] < 28.7, r)


def south(ws):
    code, r = made(ws, "Nairobi_1.3S_27.5m_UTM.tif", -1.29, 36.82, 27.5, "--base", "1700")
    truth("south: exit 0, EPSG 32737", code == 0 and r.get("epsg") == 32737, r)
    if code:
        return
    info = geo.read_geotiff(os.path.join(ws, *r["path"].split("/")))
    x, y = geo.utm_forward(36.82, -1.29, 37)
    truth("south: keys 3072=32737 and 4096=5773, false northing", info["geokeys"].get(3072) == 32737
          and info["geokeys"].get(4096) == 5773 and y > 9_800_000 and info["tiepoint"][1] > y, info)
    truth("south: heights around 1700 m", 1660 < info["vmin"] < info["vmax"] < 1740, info)
    truth("zones: lon 180 is zone 60 (not 61, UPS), lat 0 is north",
          make_dsm.utm_epsg(0, 180) == 32660 and make_dsm.utm_epsg(-10, -180) == 32701
          and make_dsm.utm_epsg(47.4, 8.55) == 32632 and make_dsm.utm_epsg(-0.0001, 3) == 32731)


def epsg4326(ws):
    code, r = made(ws, "Helsinki_60.2N_27.5m_EPSG4326.tif", 60.17, 24.94, 27.5, "--crs", "4326", "--base", "25")
    truth("4326: exit 0", code == 0 and r.get("epsg") == 4326, r)
    if code:
        return
    p = os.path.join(ws, *r["path"].split("/"))
    k = geo.read_geotiff(p)["geokeys"]
    truth("4326: keys 1024=2 (geographic), 2048=4326, 4096=5773, no 3072",
          k.get(1024) == 2 and k.get(2048) == 4326 and k.get(4096) == 5773 and 3072 not in k, k)
    truth("4326: the same bytes as the 10-09 Helsinki 27.5 m file, measured 27.5 m",
          r["sha256"] == SHA_10_09["Z11_Helsinki_60.2N_27.5m_EPSG4326"] and r["app_res_m"] == 27.5, r)
    code, r = made(ws, "Pole_89.9N_EPSG4326.tif", 89.9, 0, 28, "--crs", "4326")
    truth("4326: 89.9 N works", code == 0 and abs(r["app_res_m"] - 28) < 1e-3, r)


def terr004(ws):
    code, r = made(ws, "Z11_Zurich_47.4N_1m_UTM32632.tif", 47.4, 8.55, 1, "--base", "450")
    truth("TERR-004 c8's Zurich 1 m DSM made again byte for byte",
          code == 0 and r["sha256"] == SHA_10_09["Z11_Zurich_47.4N_1m_UTM32632"] and r["size"] == 6653046
          and r["app_res_m"] == 1.0005, r)


def refused(ws, args, want, name):
    before = tree(ws)
    code, r = run(args, ws)
    truth(f"refused: {name}", code == 2 and list(r) == ["error"] and want in r["error"] and tree(ws) == before,
          (code, r, sorted(tree(ws) - before)))


def refusals(ws):
    base = ["--lat", "47.4", "--lon", "8.55", "--res", "27.5"]
    out = ["--out", f"{DIR}/new.tif"]
    for args, want, name in (
            (["--lat", "84.01", "--lon", "8", "--res", "28"] + out, "--lat: -84 to 84", "UTM latitude 84.01"),
            (["--lat", "-84.01", "--lon", "8", "--res", "28"] + out, "--lat: -84 to 84", "UTM latitude -84.01"),
            (["--lat", "89.95", "--lon", "8", "--res", "28", "--crs", "4326"] + out, "--lat: -89.9 to 89.9",
             "4326 latitude 89.95"),
            (["--lat", "nan", "--lon", "8", "--res", "28"] + out, "--lat", "latitude nan"),
            (["--lat", "47", "--lon", "180.01", "--res", "28"] + out, "--lon: -180 to 180", "longitude 180.01"),
            (["--lat", "47", "--lon", "-180.5", "--res", "28"] + out, "--lon: -180 to 180", "longitude -180.5"),
            (["--lat", "47", "--lon", "inf", "--res", "28"] + out, "--lon", "longitude inf"),
            (["--lat", "47", "--lon", "8", "--res", "0"] + out, "--res: more than 0", "resolution 0"),
            (["--lat", "47", "--lon", "8", "--res", "-1"] + out, "--res: more than 0", "resolution -1"),
            (["--lat", "47", "--lon", "8", "--res", "100.01"] + out, "at most 100 metres", "resolution 100.01"),
            (base + ["--extent", "0"] + out, "--extent", "extent 0"),
            (base + ["--extent", "50001"] + out, "--extent", "extent 50001"),
            (base + ["--base", "9001"] + out, "--base", "base 9001"),
            (["--lat", "47", "--lon", "8", "--res", "0.2"] + out, "7500 x 7500 px", "too many pixels"),
            (["--lat", "47", "--lon", "8", "--res", "5e-324"] + out, "more than 5000", "a resolution near zero"),
            (["--lat", "47", "--lon", "8", "--res", "100", "--extent", "500"] + out, "fewer than 10", "too few pixels"),
            (["--lat", "0", "--lon", "180", "--res", "28"] + out, "180th meridian", "UTM across the 180th meridian"),
            (["--lat", "10", "--lon", "-179.99", "--res", "28", "--crs", "4326"] + out, "180th meridian",
             "4326 across the 180th meridian"),
            (base + ["--crs", "3857"] + out, "invalid choice", "another coordinate system"),
            (base, "--out", "no --out"),
            (base + out + ["--zone", "32"], "unrecognized arguments", "an unknown option"),
            (base + ["--ou", f"{DIR}/new.tif"], "--out", "an abbreviated option"),
    ):
        refused(ws, args, want, name)
    other = tempfile.mkdtemp(prefix="make_dsm_other_")
    try:
        for path, want, name in (
                ("/tmp/new.tif", "outside this workspace", "an absolute path elsewhere"),
                (f"{other}/{DIR}/new.tif", "outside this workspace", "another workspace's tests folder"),
                (f"{DIR}/../files/new.tif", "no ..", ".. inside"),
                (f"../{DIR}/new.tif", "no ..", ".. in front"),
                (f"{ws}/{DIR}/../files/new.tif", "no ..", ".. in an absolute path"),
                ("tests/new.tif", "only a new file", "tests/<name>"),
                ("tests/2026-11-04-dsm-check/new.tif", "only a new file", "the folder without files/"),
                (f"{DIR}/sub/new.tif", "only a new file", "a subfolder of files/"),
                ("qa/cases/fixtures/TERR-004/new.tif", "only a new file", "the case fixtures folder"),
                ("qa/runner/new.tif", "only a new file", "the engine folder"),
                ("tests/test-files-0000fake007/files/new.tif", "not a test session folder", "the app's attached files"),
                ("tests/trial-shots/files/new.tif", "not a test session folder", "a folder that is not a session"),
                ("tests/2026-11-04-Dsm/files/new.tif", "not a test session folder", "an upper-case folder"),
                (f"{DIR}/new.tiff", "ending in .tif", "a .tiff name"),
                (f"{DIR}/new.TIF", "ending in .tif", "an upper-case .TIF"),
                (f"{DIR}/.new.tif", "ending in .tif", "a hidden name"),
                (f"{DIR}/new file.tif", "ending in .tif", "a space in the name"),
                (f"{DIR}/new.tif/", "only a new file", "a trailing slash"),
                ("tests/2026-11-04-dsm-check//files/new.tif", "only a new file", "a double slash"),
                ("tests/2026-11-04-dsm-check/./files/new.tif", "only a new file", "a ./"),
                ("tests\\2026-11-04-dsm-check\\files\\new.tif", "only a new file", "backslashes"),
                ("", "only a new file", "an empty path")):
            refused(ws, base + ["--out", path], want, f"--out {name}")
    finally:
        shutil.rmtree(other, ignore_errors=True)


def links_and_existing(ws):
    base = ["--lat", "47.4", "--lon", "8.55", "--res", "27.5"]
    away = os.path.join(ws, "away")
    os.makedirs(os.path.join(away, "files"))
    os.symlink(away, os.path.join(ws, "tests", "2026-11-04-link-folder"))
    refused(ws, base + ["--out", "tests/2026-11-04-link-folder/files/new.tif"], "is a symbolic link",
            "a session folder that is a symbolic link")
    os.makedirs(os.path.join(ws, "tests", "2026-11-04-link-files"))
    os.symlink(os.path.join(away, "files"), os.path.join(ws, "tests", "2026-11-04-link-files", "files"))
    refused(ws, base + ["--out", "tests/2026-11-04-link-files/files/new.tif"], "is a symbolic link",
            "a files folder that is a symbolic link")
    os.makedirs(os.path.join(ws, "tests", "2026-11-04-link-file", "files"))
    os.symlink(os.path.join(away, "files", "target.tif"),
               os.path.join(ws, "tests", "2026-11-04-link-file", "files", "new.tif"))
    refused(ws, base + ["--out", "tests/2026-11-04-link-file/files/new.tif"], "already exists",
            "a dangling symbolic link at the name")
    truth("links: nothing written where the links point", os.listdir(os.path.join(away, "files")) == [])
    keep = os.path.join(ws, *DIR.split("/"), "existing.tif")
    with open(keep, "wb") as f:
        f.write(b"keep")
    refused(ws, base + ["--out", f"{DIR}/existing.tif"], "already exists", "an existing file")
    with open(keep, "rb") as f:
        truth("existing: the file is unchanged", f.read() == b"keep")
    with open(os.path.join(ws, "tests", "2026-11-04-plain-file"), "w") as f:
        f.write("x")
    refused(ws, base + ["--out", "tests/2026-11-04-plain-file/files/new.tif"], "is not a folder",
            "a file where the folder goes")
    ws2 = tempfile.mkdtemp(prefix="make_dsm_ws2_")
    try:
        os.makedirs(os.path.join(ws2, "real-tests"))
        os.symlink(os.path.join(ws2, "real-tests"), os.path.join(ws2, "tests"))
        refused(ws2, base + ["--out", f"{DIR}/new.tif"], "tests is a symbolic link", "a tests folder that is a link")
        os.makedirs(os.path.join(ws2, "plain"))
        refused(os.path.join(ws2, "plain"), base + ["--out", f"{DIR}/new.tif"], "no tests folder",
                "a workspace without tests")
    finally:
        shutil.rmtree(ws2, ignore_errors=True)


def made_inside(ws):
    """The workspace reached through a link, an absolute --out in it, and a folder that is made."""
    link = ws.rstrip("/") + "-link"
    os.symlink(ws, link)
    try:
        code, r = run(["--lat", "47.4", "--lon", "8.55", "--res", "27.5", "--out",
                       "tests/2026-11-04-new-folder/files/a.tif"], link)
        truth("inside: a missing session folder is made", code == 0 and os.path.isfile(
            os.path.join(ws, "tests", "2026-11-04-new-folder", "files", "a.tif")), r)
        for prefix in (link, os.path.realpath(ws)):
            out = f"{prefix}/tests/2026-11-04-new-folder/files/b-{len(prefix)}.tif"
            code, r = run(["--lat", "47.4", "--lon", "8.55", "--res", "27.5", "--out", out], link)
            truth(f"inside: an absolute --out under {'the link' if prefix == link else 'the real folder'}",
                  code == 0 and r["path"] == f"tests/2026-11-04-new-folder/files/b-{len(prefix)}.tif", r)
    finally:
        os.unlink(link)


def bad_write(ws):
    """A file that reads back wrong is never given its name."""
    real = geo.write_geotiff

    def wrong_key(*a, **k):
        k["vertical_epsg"] = 3855
        return real(*a, **k)
    geo.write_geotiff = wrong_key
    try:
        before = tree(ws)
        code, r = made(ws, "wrong-key.tif", 47.4, 8.55, 27.5)
    finally:
        geo.write_geotiff = real
    truth("read back: a wrong vertical key is refused, nothing left",
          code == 2 and "vertical key" in r.get("error", "") and tree(ws) == before, (r, sorted(tree(ws) - before)))


def cli():
    """The command line: one JSON object and exit 2 for refusals (they write nothing)."""
    for args, want in ((["--lat", "95", "--lon", "8", "--res", "28", "--out", f"{DIR}/x.tif"], "--lat"),
                       (["--lat", "47", "--lon", "8", "--res", "28"], "--out"),
                       (["--lat", "47", "--lon", "8", "--res", "28", "--out", "/etc/x.tif"], "outside this workspace")):
        p = subprocess.run([sys.executable, "-I", "-B", SCRIPT] + args, capture_output=True, text=True, timeout=60)
        lines = p.stdout.splitlines()
        try:
            obj = json.loads(lines[0]) if len(lines) == 1 else None
        except ValueError:
            obj = None
        truth(f"cli: {' '.join(args[-2:])} exits 2 with one JSON error", p.returncode == 2 and isinstance(obj, dict)
              and list(obj) == ["error"] and want in obj["error"] and not p.stderr, (p.returncode, p.stdout, p.stderr))


def main(argv):
    ws = tempfile.mkdtemp(prefix="make_dsm_test_")
    try:
        os.makedirs(os.path.join(ws, *DIR.split("/")))
        round_trip(ws)
        around_28(ws)
        south(ws)
        epsg4326(ws)
        terr004(ws)
        refusals(ws)
        links_and_existing(ws)
        made_inside(ws)
        bad_write(ws)
        cli()
        truth("no temporary file left anywhere", not leftovers(ws), leftovers(ws))
    finally:
        shutil.rmtree(ws, ignore_errors=True)
    fails = 0
    for name, ok, detail in results:
        if not ok:
            fails += 1
        if not ok or "-v" in argv:
            print(f"{'OK  ' if ok else 'FAIL'} {name}" + ("" if ok else f": {str(detail)[:300]}"))
    print(f"make_dsm cases: {len(results) - fails}/{len(results)} pass")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
