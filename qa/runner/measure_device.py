"""Measure one tablet and write its profile record (SCHEMA.md 2a). Read-only on the tablet.

python3 -I qa/runner/measure_device.py <serial> <tag> <slot> [--write]

Reads properties, screen, insets, rotation settings, lock, MDM, battery and app state
over adb -s, the USB path from macOS, and (when the app's WebView exposes DevTools) the
userAgent model and devicePixelRatio over read-only CDP on port 9341 + slot.
With --write the profile becomes qa/devices/<serial>.v<N>.json (a hashed record);
a re-measure writes the next version, which supersedes the previous one.
"""
import glob
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp_read import evaluate  # noqa: E402
from records import QA_DIR, append, hash_record, now_utc, sha256_bytes, write_record  # noqa: E402

PKG = "com.wingtra.cloud.app"


def adb(serial, *args, timeout=20):
    argv = ["adb", "-s", serial, *args]
    p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    append({"src": "runner", "session": "-", "target": serial, "tool": "adb", "argv": argv, "exit": p.returncode,
            "out_sha256": sha256_bytes(p.stdout.encode()), "ref": None})
    return p.stdout


def shell(serial, cmd):
    return adb(serial, "shell", cmd)


def insets(serial):
    """Status and navigation bar sizes in px from the InsetsSource frames of the current rotation."""
    out = shell(serial, "dumpsys window | grep -E 'InsetsSource.*type=(statusBars|navigationBars)'")
    w, h = None, None
    m = re.search(r"Physical size: (\d+)x(\d+)", shell(serial, "wm size"))
    res = {"top": 0, "bottom": 0, "left": 0, "right": 0, "raw": []}
    for line in out.splitlines():
        f = re.search(r"type=(statusBars|navigationBars) frame=\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]", line)
        if not f or "visible=false" in line:
            continue
        kind, x0, y0, x1, y1 = f.group(1), *map(int, f.groups()[1:])
        res["raw"].append(line.strip()[:160])
        width, height = x1 - x0, y1 - y0
        if y0 == 0 and width > height:
            res["top"] = max(res["top"], height)
        elif width > height:
            res["bottom"] = max(res["bottom"], height)
        elif x0 == 0:
            res["left"] = max(res["left"], width)
        else:
            res["right"] = max(res["right"], width)
    res["raw"] = sorted(set(res["raw"]))[:6]
    return res


OWC_HUB_IDS = {("0x8087", "0x0b40"), ("0x1d5c", "0x5801")}  # the OWC hub's internal USB3 and USB2 hubs


def usb_path(serial):
    try:
        data = json.loads(subprocess.run(["system_profiler", "-json", "SPUSBHostDataType"], capture_output=True, text=True, timeout=30).stdout)
    except (ValueError, subprocess.TimeoutExpired):
        return None

    def walk(items, chain):
        for it in items or []:
            node = {"name": it.get("_name"), "vendor": it.get("USBDeviceKeyVendorName"),
                    "id": (it.get("USBDeviceKeyVendorID"), it.get("USBDeviceKeyProductID")), "location": it.get("USBKeyLocationID")}
            if it.get("USBDeviceKeySerialNumber") == serial:
                return {"chain": [f"{c['name']} ({c['vendor']})" for c in chain] + [node["name"]],
                        "location_id": node["location"], "speed": it.get("USBDeviceKeyLinkSpeed"),
                        "via_owc_hub": any(c["id"] in OWC_HUB_IDS for c in chain)}
            found = walk(it.get("_items"), chain + [node])
            if found:
                return found
        return None

    for bus in data.get("SPUSBHostDataType", []):
        found = walk(bus.get("_items"), [])
        if found:
            return found
    return None


def main(argv):
    serial, tag, slot = argv[0], argv[1], int(argv[2])
    write = "--write" in argv
    port = 9341 + slot
    gp = lambda k: shell(serial, f"getprop {k}").strip()
    if gp("ro.serialno") != serial:
        print("ro.serialno does not match the adb serial; refusing to profile")
        return 2
    size = re.search(r"Physical size: (\d+)x(\d+)", shell(serial, "wm size"))
    dens = re.search(r"Physical density: (\d+)", shell(serial, "wm density"))
    px = [int(size.group(1)), int(size.group(2))] if size else None
    dpi = int(dens.group(1)) if dens else None
    focus = shell(serial, "dumpsys window | grep -m1 mCurrentFocus").strip()
    policy = shell(serial, "dumpsys device_policy | grep -m4 -iE 'Device Owner|Profile Owner|admin='").strip()
    lock_disabled = shell(serial, "locksettings get-disabled 2>&1").strip()
    keyguard = shell(serial, "dumpsys window | grep -m2 -E 'isKeyguardSecure|mKeyguardSecure|KeyguardSecure'").strip()
    cred = re.search(r"CredentialType: (\w+)", shell(serial, "dumpsys lock_settings | grep -m2 -E 'CredentialType'"))
    battery = shell(serial, "dumpsys battery | grep -E 'AC powered|USB powered|Max charging|level:|status:'").strip()
    pkg = shell(serial, f"dumpsys package {PKG} | grep -m3 -E 'versionCode|versionName'").strip()
    pid = shell(serial, f"pidof {PKG}").strip()
    sockets = sorted(set(re.findall(r"webview_devtools_remote_\d+", shell(serial, "cat /proc/net/unix"))))
    prof = {
        "kind": "device", "schema": 1, "created": now_utc(), "author": "runner",
        "serial": serial, "tag": tag, "slot": slot, "cdp_port": port,
        "model": gp("ro.product.model"), "manufacturer": gp("ro.product.manufacturer"),
        "android": {"release": gp("ro.build.version.release"), "sdk": gp("ro.build.version.sdk"), "fingerprint": gp("ro.build.fingerprint")},
        "display": {"px": px, "density_dpi": dpi, "dpr_from_density": round(dpi / 160, 3) if dpi else None,
                    "insets_px": insets(serial), "measured": now_utc(), "method": "wm size/density, dumpsys window InsetsSource frames"},
        "rotation": {"accelerometer_rotation": shell(serial, "settings get system accelerometer_rotation").strip(),
                     "user_rotation": shell(serial, "settings get system user_rotation").strip(),
                     "behaviour": "measured settings only; app rotation behaviour is a test result (UPG-015)"},
        "oem_quirks": [],
        "mdm": {"raw": policy[:300],
                "present": bool(re.search(r"admin=ComponentInfo|Device Owner Type: (?!-1)\d", policy))},
        "lock": {"locksettings_get_disabled": lock_disabled[:80], "keyguard": keyguard[:200]},
        "has_pin": None,
        "credential_type": None,
        "usb_ports": 1, "usb_path": usb_path(serial),
        "charge": {"raw": battery.replace("\n", "; ")[:300]},
        "app": {"package": PKG, "dumpsys": pkg.replace("\n", "; "), "pid": pid or None, "devtools_sockets": sockets},
        "user_agent_model": None, "dpr": None, "viewport_css": None,
        "focus_at_measure": focus[:200],
    }
    if cred:
        prof["credential_type"] = cred.group(1)
        prof["has_pin"] = cred.group(1) != "NONE"
    if "UsbDetailsActivity" in focus:
        prof["oem_quirks"].append({"id": "usb_prefs_steal_focus", "text": "The USB preferences screen takes focus after plug-in; taps would land in Settings.",
                                   "src": "mCurrentFocus at measure time"})
    if pid and f"webview_devtools_remote_{pid}" in sockets:
        fl = subprocess.run(["adb", "forward", "--list"], capture_output=True, text=True).stdout
        mine = [l for l in fl.splitlines() if l.startswith(serial + " ") and f"tcp:{port} " in l + " "]
        if mine and f"webview_devtools_remote_{pid}" not in mine[0]:
            adb(serial, "forward", "--remove", f"tcp:{port}")
            mine = []
        if not mine:
            adb(serial, "forward", "--no-rebind", f"tcp:{port}", f"localabstract:webview_devtools_remote_{pid}")
        try:
            v = evaluate(port, "({ua: navigator.userAgent, dpr: devicePixelRatio, w: innerWidth, h: innerHeight, path: location.pathname, origin: location.origin})")
            m = re.search(r"Android [\d.]+; ([^;)]+?)(?: Build|\))", v["ua"])
            prof["user_agent_model"] = m.group(1).strip() if m else None
            prof["dpr"] = v["dpr"]
            prof["viewport_css"] = [v["w"], v["h"]]
            prof["app"]["origin"] = v["origin"]
        except Exception as e:  # noqa: BLE001
            prof["app"]["cdp_error"] = f"{type(e).__name__}: {e}"[:200]
    else:
        prof["app"]["cdp_error"] = "no DevTools socket for the running app process (app not running, or a build without WebView debugging)"
    versions = sorted(int(re.search(r"\.v(\d+)\.json$", f).group(1)) for f in glob.glob(os.path.join(QA_DIR, "devices", f"{serial}.v*.json")))
    n = (versions[-1] + 1) if versions else 1
    prof["id"] = f"DEV-{serial}-v{n}"
    prof["supersedes"] = f"DEV-{serial}-v{n - 1}" if n > 1 else None
    print(json.dumps(prof, indent=2))
    if write:
        path = os.path.join(QA_DIR, "devices", f"{serial}.v{n}.json")
        write_record(path, prof)
        hash_record(path, session="5e551004", note="device profile")
        print("wrote and hashed", path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
