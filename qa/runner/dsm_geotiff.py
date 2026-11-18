"""Write and read the test DSMs of make_dsm.py, and the app's resolution math (2026-11-04).

Copied, not imported, from tests/2026-10-09-terrain-resolution/tools/, the code that made the Zurich DSMs the app
accepted (TERR-004 c8 pins its Z11_Zurich_47.4N_1m_UTM32632.tif), so the engine does not depend on a test folder:
- geotiff_writer.py: the tag constants, write_geotiff and smooth_terrain, unchanged. A single-band Float32
  elevation raster, deflate in strips, ModelPixelScale + ModelTiepoint (north-up, no rotation) and a
  GeoKeyDirectory for EPSG:4326 or a projected code such as UTM 326zz/327zz, with an optional vertical key.
- geotiff_reader.py: read_geotiff, an independent reader to check what was written. Changed: its asserts raise
  ValueError (python3 -O drops asserts) and the file is closed after reading. sample() is new (one value, for the
  round-trip test).
- app_math.py: haversine_m, the WGS84 UTM functions (utm_forward, utm_inverse), app_resolution and
  deg_pixel_for_metres, unchanged. app_resolution is the app's calculateDatasetResolution.ts (checked again at
  WingtraCloud sky-staging 82e23ff4a): the centre pixel against its +1 column and +1 row neighbour, both converted to
  WGS84 lon/lat, turf.distance (haversine) in metres, the larger of the two.
Standard library only; python3 3.9 and later.
"""
import math
import struct
import zlib

# --- geotiff_writer.py ---------------------------------------------------------------------------------------------

# TIFF tag ids
IMAGE_WIDTH, IMAGE_LENGTH, BITS_PER_SAMPLE, COMPRESSION = 256, 257, 258, 259
PHOTOMETRIC, STRIP_OFFSETS, SAMPLES_PER_PIXEL, ROWS_PER_STRIP = 262, 273, 277, 278
STRIP_BYTE_COUNTS, PLANAR_CONFIG, SAMPLE_FORMAT = 279, 284, 339
MODEL_PIXEL_SCALE, MODEL_TIEPOINT, GEO_KEY_DIRECTORY = 33550, 33922, 34735
GEO_DOUBLE_PARAMS, GEO_ASCII_PARAMS, GDAL_NODATA = 34736, 34737, 42113

SHORT, LONG, DOUBLE, ASCII = 3, 4, 12, 2
TYPE_SIZE = {SHORT: 2, LONG: 4, DOUBLE: 8, ASCII: 1}
TYPE_FMT = {SHORT: "H", LONG: "I", DOUBLE: "d"}


def _geokeys(epsg, vertical_epsg=None):
    """GeoKeyDirectory entries: (key, tiff_tag_location, count, value)."""
    keys = []
    if epsg == 4326:
        keys += [(1024, 0, 1, 2),      # GTModelTypeGeoKey = Geographic
                 (1025, 0, 1, 1),      # GTRasterTypeGeoKey = PixelIsArea
                 (2048, 0, 1, 4326),   # GeographicTypeGeoKey
                 (2054, 0, 1, 9102)]   # GeogAngularUnitsGeoKey = degree
    else:
        keys += [(1024, 0, 1, 1),      # GTModelTypeGeoKey = Projected
                 (1025, 0, 1, 1),      # PixelIsArea
                 (3072, 0, 1, epsg),   # ProjectedCSTypeGeoKey
                 (3076, 0, 1, 9001)]   # ProjLinearUnitsGeoKey = metre
    if vertical_epsg:
        keys += [(4096, 0, 1, vertical_epsg),  # VerticalCSTypeGeoKey
                 (4099, 0, 1, 9001)]           # VerticalUnitsGeoKey = metre
    keys.sort()
    header = [1, 1, 0, len(keys)]
    flat = header[:]
    for k in keys:
        flat += list(k)
    return flat


def write_geotiff(path, width, height, origin_x, origin_y, pixel_x, pixel_y,
                  elevation_fn, epsg=4326, vertical_epsg=None, nodata=-9999.0,
                  rows_per_strip=16):
    """elevation_fn(col, row) -> float metres. origin is the top-left corner."""
    strips = []
    for r0 in range(0, height, rows_per_strip):
        rows = range(r0, min(height, r0 + rows_per_strip))
        buf = bytearray()
        for r in rows:
            buf += struct.pack("<%df" % width, *(elevation_fn(c, r) for c in range(width)))
        strips.append(zlib.compress(bytes(buf), 6))

    nodata_ascii = (repr(float(nodata)).rstrip("0").rstrip(".") + "\0").encode()
    entries = [
        (IMAGE_WIDTH, LONG, [width]),
        (IMAGE_LENGTH, LONG, [height]),
        (BITS_PER_SAMPLE, SHORT, [32]),
        (COMPRESSION, SHORT, [8]),            # Adobe deflate
        (PHOTOMETRIC, SHORT, [1]),            # BlackIsZero
        (STRIP_OFFSETS, LONG, [0] * len(strips)),
        (SAMPLES_PER_PIXEL, SHORT, [1]),
        (ROWS_PER_STRIP, LONG, [rows_per_strip]),
        (STRIP_BYTE_COUNTS, LONG, [len(s) for s in strips]),
        (PLANAR_CONFIG, SHORT, [1]),
        (SAMPLE_FORMAT, SHORT, [3]),          # IEEE float
        (MODEL_PIXEL_SCALE, DOUBLE, [pixel_x, pixel_y, 0.0]),
        (MODEL_TIEPOINT, DOUBLE, [0.0, 0.0, 0.0, origin_x, origin_y, 0.0]),
        (GEO_KEY_DIRECTORY, SHORT, _geokeys(epsg, vertical_epsg)),
        (GDAL_NODATA, ASCII, nodata_ascii),
    ]
    entries.sort(key=lambda e: e[0])

    # Layout: header(8) | IFD | out-of-line values | strips
    ifd_offset = 8
    ifd_size = 2 + 12 * len(entries) + 4
    data_offset = ifd_offset + ifd_size
    blobs, ifd_entries = [], []
    cursor = data_offset

    def value_bytes(typ, vals):
        if typ == ASCII:
            return bytes(vals)
        return struct.pack("<%d%s" % (len(vals), TYPE_FMT[typ]), *vals)

    # strip offsets are known only after out-of-line data size is known: two passes
    for pass_no in (0, 1):
        blobs, ifd_entries, cursor = [], [], data_offset
        for tag, typ, vals in entries:
            count = len(vals)
            raw = value_bytes(typ, vals)
            if len(raw) <= 4:
                ifd_entries.append(struct.pack("<HHI", tag, typ, count) + raw.ljust(4, b"\0"))
            else:
                if cursor % 2:
                    blobs.append(b"\0"); cursor += 1
                ifd_entries.append(struct.pack("<HHII", tag, typ, count, cursor))
                blobs.append(raw); cursor += len(raw)
        strip_start = cursor + (cursor % 2)
        offsets, pos = [], strip_start
        for s in strips:
            offsets.append(pos); pos += len(s)
        entries = [(t, ty, offsets if t == STRIP_OFFSETS else v) for t, ty, v in entries]

    with open(path, "wb") as f:
        f.write(b"II*\0" + struct.pack("<I", ifd_offset))
        f.write(struct.pack("<H", len(entries)))
        for e in ifd_entries:
            f.write(e)
        f.write(struct.pack("<I", 0))
        for b in blobs:
            f.write(b)
        if f.tell() % 2:
            f.write(b"\0")
        for s in strips:
            f.write(s)


def smooth_terrain(base_m=500.0, amp_m=40.0, wavelength_px=60.0):
    """Gentle rolling hills so tiles look like terrain; values in metres."""
    def fn(c, r):
        return (base_m + amp_m * math.sin(c / wavelength_px) * math.cos(r / (wavelength_px * 1.3))
                + 0.15 * amp_m * math.sin((c + 2 * r) / (wavelength_px / 3.0)))
    return fn


# --- geotiff_reader.py ---------------------------------------------------------------------------------------------

TYPES = {1: ("B", 1), 2: ("s", 1), 3: ("H", 2), 4: ("I", 4), 11: ("f", 4), 12: ("d", 8), 16: ("Q", 8)}


def _tags(data):
    """The first IFD's tags of a little-endian classic TIFF: {tag: list of values, or str for ASCII}."""
    if data[:4] != b"II*\0":
        raise ValueError("not a little-endian classic TIFF")
    (ifd,) = struct.unpack_from("<I", data, 4)
    (n,) = struct.unpack_from("<H", data, ifd)
    tags = {}
    for i in range(n):
        tag, typ, count, val = struct.unpack_from("<HHI4s", data, ifd + 2 + 12 * i)
        fmt, size = TYPES[typ]
        raw = val if size * count <= 4 else data[struct.unpack("<I", val)[0]:struct.unpack("<I", val)[0] + size * count]
        if typ == 2:
            tags[tag] = raw[:count].split(b"\0")[0].decode()
        else:
            tags[tag] = list(struct.unpack("<%d%s" % (count, fmt), raw[:size * count]))
    return tags


def _read(path):
    with open(path, "rb") as f:
        return f.read()


def read_geotiff(path, decode=True):
    data = _read(path)
    tags = _tags(data)
    w, h = tags[256][0], tags[257][0]
    gk = tags[34735]
    keys = {gk[4 + 4 * i]: gk[4 + 4 * i + 3] for i in range(gk[3])}
    info = {
        "width": w, "height": h,
        "pixel_scale": tags[33550][:2], "tiepoint": tags[33922][3:5],
        "geokeys": keys, "compression": tags[259][0], "sample_format": tags[339][0],
        "nodata": tags.get(42113), "bytes": len(data),
    }
    if decode:
        vmin, vmax, count = float("inf"), float("-inf"), 0
        for off, bc in zip(tags[273], tags[279]):
            raw = zlib.decompress(data[off:off + bc])
            vals = struct.unpack("<%df" % (len(raw) // 4), raw)
            vmin, vmax, count = min(vmin, min(vals)), max(vmax, max(vals)), count + len(vals)
        if count != w * h:
            raise ValueError(f"decoded {count} samples, expected {w * h}")
        info.update(vmin=vmin, vmax=vmax)
    return info


def sample(path, col, row):
    """The Float32 value at (col, row) as stored (new, for the round-trip test)."""
    data = _read(path)
    tags = _tags(data)
    w, per_strip = tags[256][0], tags[278][0]
    s = row // per_strip
    raw = zlib.decompress(data[tags[273][s]:tags[273][s] + tags[279][s]])
    return struct.unpack_from("<f", raw, 4 * ((row - s * per_strip) * w + col))[0]


# --- app_math.py ---------------------------------------------------------------------------------------------------

TURF_EARTH_RADIUS = 6371008.8          # @turf/helpers earthRadius (verify)

# WGS84
A = 6378137.0
F = 1 / 298.257223563


def haversine_m(lon1, lat1, lon2, lat2, r=TURF_EARTH_RADIUS):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = p2 - p1
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.sin(dlon / 2) ** 2 * math.cos(p1) * math.cos(p2)
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _kruger_coeffs():
    n = F / (2 - F)
    a_hat = A / (1 + n) * (1 + n ** 2 / 4 + n ** 4 / 64 + n ** 6 / 256)
    beta = [n / 2 - 2 * n ** 2 / 3 + 37 * n ** 3 / 96 - n ** 4 / 360,
            n ** 2 / 48 + n ** 3 / 15 - 437 * n ** 4 / 1440,
            17 * n ** 3 / 480 - 37 * n ** 4 / 840,
            4397 * n ** 4 / 161280]
    alpha = [n / 2 - 2 * n ** 2 / 3 + 5 * n ** 3 / 16 + 41 * n ** 4 / 180,
             13 * n ** 2 / 48 - 3 * n ** 3 / 5 + 557 * n ** 4 / 1440,
             61 * n ** 3 / 240 - 103 * n ** 4 / 140,
             49561 * n ** 4 / 161280]
    return n, a_hat, alpha, beta


def utm_forward(lon, lat, zone):
    n, a_hat, alpha, _ = _kruger_coeffs()
    k0, lon0 = 0.9996, math.radians(-183 + 6 * zone)
    phi, lam = math.radians(lat), math.radians(lon) - lon0
    e = math.sqrt(F * (2 - F))
    t = math.sinh(math.atanh(math.sin(phi)) - e * math.atanh(e * math.sin(phi)))
    xi_p = math.atan2(t, math.cos(lam))
    eta_p = math.atanh(math.sin(lam) / math.sqrt(1 + t * t))
    xi = xi_p + sum(alpha[j] * math.sin(2 * (j + 1) * xi_p) * math.cosh(2 * (j + 1) * eta_p) for j in range(4))
    eta = eta_p + sum(alpha[j] * math.cos(2 * (j + 1) * xi_p) * math.sinh(2 * (j + 1) * eta_p) for j in range(4))
    x = 500000 + k0 * a_hat * eta
    y = k0 * a_hat * xi
    if lat < 0:
        y += 10000000
    return x, y


def utm_inverse(x, y, zone, south):
    n, a_hat, _, beta = _kruger_coeffs()
    k0, lon0 = 0.9996, math.radians(-183 + 6 * zone)
    if south:
        y -= 10000000
    xi = y / (k0 * a_hat)
    eta = (x - 500000) / (k0 * a_hat)
    xi_p = xi - sum(beta[j] * math.sin(2 * (j + 1) * xi) * math.cosh(2 * (j + 1) * eta) for j in range(4))
    eta_p = eta - sum(beta[j] * math.cos(2 * (j + 1) * xi) * math.sinh(2 * (j + 1) * eta) for j in range(4))
    chi = math.asin(math.sin(xi_p) / math.cosh(eta_p))
    lam = math.atan2(math.sinh(eta_p), math.cos(xi_p))
    e = math.sqrt(F * (2 - F))
    # iterate conformal latitude -> geodetic latitude
    tau_p = math.tan(chi)
    tau = tau_p
    for _ in range(10):
        sigma = math.sinh(e * math.atanh(e * tau / math.sqrt(1 + tau * tau)))
        tau_i = tau * math.sqrt(1 + sigma * sigma) - sigma * math.sqrt(1 + tau * tau)
        dtau = (tau_p - tau_i) / math.sqrt(1 + tau_i * tau_i) * (1 + (1 - e * e) * tau * tau) / ((1 - e * e) * math.sqrt(1 + tau * tau))
        tau += dtau
        if abs(dtau) < 1e-14:
            break
    return math.degrees(lon0 + lam), math.degrees(math.atan(tau))


def app_resolution(epsg, origin_x, origin_y, pixel_x, pixel_y, width, height):
    """pixel_y is the positive ModelPixelScale value; geotiff.js returns it negated."""
    res_y = -pixel_y
    cx = origin_x + width * pixel_x / 2
    cy = origin_y + height * res_y / 2

    def to_geo(x, y):
        if epsg == 4326:
            return x, y
        zone = epsg % 100
        return utm_inverse(x, y, zone, south=(epsg // 100 == 327))

    c = to_geo(cx, cy)
    col = to_geo(cx + pixel_x, cy)
    row = to_geo(cx, cy + res_y)
    w = haversine_m(c[0], c[1], col[0], col[1])
    h = haversine_m(c[0], c[1], row[0], row[1])
    return max(w, h), w, h, c


def deg_pixel_for_metres(target_m, lat):
    """Square-in-metres EPSG:4326 pixel: (dlon, dlat) so the app measures target_m on both axes."""
    dlat = math.degrees(target_m / TURF_EARTH_RADIUS)
    # width: solve haversine for dlon at latitude lat with equal lat
    s = math.sin(target_m / (2 * TURF_EARTH_RADIUS)) / math.cos(math.radians(lat))
    dlon = math.degrees(2 * math.asin(s))
    return dlon, dlat
