"""webpc044 check: key-only login, nothing but sshd listening off loopback, tunnel answers.

python3 -I qa/runner/check_webpc044.py [--no-tunnel]

Read-only. Uses only the ssh alias `webpc044` (status reads) and the tunnel
ssh -N -L 9350:127.0.0.1:9222 webpc044. Writes a status snapshot to
qa/runner/state/webpc044.json (for the pane) and runner events to the ledger.
Exit 0 when every required check passes.
"""
import datetime
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from records import QA_DIR, append, now_utc  # noqa: E402

HOST = "webpc044"
BRIDGE_IP = "192.0.2.2"
USER = "owner"
KEY = os.path.expanduser("~/.ssh/webpc044_qa")
TUNNEL_PORT = 9350
ENV_ORIGIN = "web.staging.company.example"
STATE = os.path.join(QA_DIR, "runner", "state", "webpc044.json")


def run(argv, timeout=25, input=None):
    p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, input=input)
    return p.returncode, p.stdout, p.stderr


def ledger(tool, argv, rc, out):
    append({"src": "runner", "session": "-", "target": "W044", "tool": tool, "argv": argv, "exit": rc,
            "out_sha256": hashlib.sha256((out or "").encode()).hexdigest(), "ref": None})


def ssh(cmd, timeout=25):
    argv = ["ssh", HOST, cmd]
    rc, out, err = run(argv, timeout)
    ledger("ssh", argv, rc, out)
    return rc, out, err


def strict_ssh(dest, *opts):
    """An ssh attempt that ignores ~/.ssh/config, never prompts, and runs nothing remotely."""
    argv = ["ssh", "-F", "/dev/null", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "HostKeyAlias=" + BRIDGE_IP,
            "-o", "StrictHostKeyChecking=yes", *opts, f"{USER}@{dest}", "true"]
    rc, out, err = run(argv, 20)
    ledger("ssh", argv, rc, err)
    return rc, err.strip()


def check():
    res = {"checked": now_utc(), "host": HOST, "checks": {}, "info": {}}
    c = res["checks"]

    rc, out, _ = run(["ping", "-c", "2", "-t", "4", BRIDGE_IP])
    c["link"] = {"ok": rc == 0, "detail": (out.strip().splitlines() or [""])[-1]}

    rc, out, err = ssh("echo ok; sw_vers -productVersion; ipconfig getifaddr en0 || true")
    lines = out.split()
    c["key_login"] = {"ok": rc == 0 and lines[:1] == ["ok"], "detail": err.strip()[:200] or "key accepted, no password"}
    res["info"]["macos"] = lines[1] if len(lines) > 1 else None
    lan_ip = lines[2] if len(lines) > 2 else None
    res["info"]["lan_ip"] = lan_ip

    rc, err = strict_ssh(BRIDGE_IP, "-o", "PubkeyAuthentication=no", "-o", "PreferredAuthentications=password,keyboard-interactive")
    methods = re.search(r"Permission denied \(([^)]*)\)", err)
    c["password_refused"] = {"ok": rc != 0 and bool(methods) and methods.group(1) == "publickey",
                             "detail": err[-160:]}
    if lan_ip:
        rc, err = strict_ssh(lan_ip, "-o", "IdentitiesOnly=yes", "-i", KEY)
        c["lan_refused"] = {"ok": rc != 0 and "Permission denied" in err, "detail": f"{lan_ip}: {err[-120:]}"}
    else:
        c["lan_refused"] = {"ok": True, "detail": "webpc044 has no LAN address on en0"}

    rc, out, _ = ssh("netstat -anv -p tcp | awk '/LISTEN/ {print $4, $9}'; echo ---; "
                     "netstat -anv -p udp | awk 'NR>2 && $4 !~ /^(127\\.|::1)/ && $4 != \"*.*\" && $5 == \"*.*\" {print $4}'")
    tcp_part, _, udp_part = out.partition("---")
    listeners, owners = [], {}
    for line in tcp_part.strip().splitlines():
        addr, pid = (line.split() + ["?"])[:2]
        if addr.startswith(("127.", "::1", "fe80::1%lo0")):
            continue
        port = addr.rsplit(".", 1)[-1]
        listeners.append((addr, pid))
    pids = sorted({p for _, p in listeners if p.isdigit()})
    if pids:
        rc2, out2, _ = ssh("for p in " + " ".join(pids) + "; do printf '%s %s\\n' $p \"$(ps -o comm= -p $p)\"; done")
        for line in out2.strip().splitlines():
            pid, _, comm = line.partition(" ")
            owners[pid] = comm.rsplit("/", 1)[-1]
    others = sorted({(a.rsplit(".", 1)[-1], owners.get(p, p)) for a, p in listeners if not a.endswith(".22")})
    c["only_sshd_listening"] = {"ok": not others,
                                "detail": "TCP off loopback: " + ", ".join(sorted({f"{a.rsplit('.', 1)[-1]}/{owners.get(p, p)}" for a, p in listeners}))}
    res["info"]["udp_bound"] = sorted(set(udp_part.split()))

    # clock offset: best of 5 round trips
    best = None
    for _ in range(5):
        t0 = time.time()
        rc, out, _ = run(["ssh", HOST, "perl -MTime::HiRes=time -e 'printf \"%.3f\\n\", time'"])
        t1 = time.time()
        if rc == 0 and out.strip():
            rtt, off = t1 - t0, float(out) - (t0 + t1) / 2
            if best is None or rtt < best[0]:
                best = (rtt, off)
    res["info"]["clock_offset_ms"] = round(best[1] * 1000) if best else None
    res["info"]["clock_rtt_ms"] = round(best[0] * 1000) if best else None

    rc, out, _ = ssh("nc -z 127.0.0.1 9222 && echo up || echo down; grep -m1 'DevTools listening' ~/qa-chrome.log 2>/dev/null || true")
    chrome_up = out.startswith("up")
    m = re.search(r"/devtools/browser/([0-9a-f-]+)", out)
    log_id = m.group(1) if m else None
    res["info"]["chrome_9222"] = "up" if chrome_up else "down"
    return res, chrome_up, log_id


def tunnel(res, log_id):
    c = res["checks"]
    argv = ["ssh", "-N", "-L", f"{TUNNEL_PORT}:127.0.0.1:9222", HOST]
    t = subprocess.Popen(argv, stderr=subprocess.PIPE, text=True)
    try:
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", TUNNEL_PORT), timeout=1).close()
                break
            except OSError:
                if t.poll() is not None:
                    break
                time.sleep(0.1)
        t0 = time.time()
        v = json.load(urllib.request.urlopen(f"http://127.0.0.1:{TUNNEL_PORT}/json/version", timeout=5))
        ms = round((time.time() - t0) * 1000)
        bid = v.get("webSocketDebuggerUrl", "").rsplit("/", 1)[-1]
        tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{TUNNEL_PORT}/json/list", timeout=5))
        env_tabs = [x for x in tabs if x.get("type") == "page" and urllib.request.urlparse(x.get("url", "")).netloc == ENV_ORIGIN]
        res["info"]["browser"] = v.get("Browser")
        res["info"]["env_tab_paths"] = [urllib.request.urlparse(x["url"]).path for x in env_tabs]
        c["tunnel"] = {"ok": True, "detail": f"/json/version in {ms} ms"}
        c["browser_identity"] = {"ok": bool(bid) and bid == log_id, "detail": f"tunnel {bid[:8]} vs log {(log_id or '-')[:8]}"}
        c["env_tab"] = {"ok": len(env_tabs) == 1, "detail": f"{len(env_tabs)} page(s) on {ENV_ORIGIN}"}
    except Exception as e:  # noqa: BLE001
        c["tunnel"] = {"ok": False, "detail": f"{type(e).__name__}: {e}"[:200]}
    finally:
        t.terminate()
        try:
            t.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            t.kill()
    ledger("ssh-tunnel", argv, 0 if c["tunnel"]["ok"] else 1, json.dumps(c.get("tunnel")))


def main(argv):
    res, chrome_up, log_id = check()
    if "--no-tunnel" in argv:
        pass
    elif chrome_up:
        tunnel(res, log_id)
    else:
        res["checks"]["tunnel"] = {"ok": False, "detail": "QA Chrome is not running on webpc044 (port 9222 closed): run sh ~/qa-files/start-qa-chrome.sh there"}
    required = ["link", "key_login", "password_refused", "lan_refused", "only_sshd_listening", "tunnel"]
    res["ok"] = all(res["checks"].get(k, {}).get("ok") for k in required if not ("--no-tunnel" in argv and k == "tunnel"))
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2)
    for k, v in res["checks"].items():
        print(f"{'PASS' if v['ok'] else 'FAIL'}  {k:22} {v['detail']}")
    print("info:", json.dumps(res["info"]))
    print("webpc044:", "OK" if res["ok"] else "NOT OK")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
