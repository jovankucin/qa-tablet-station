"""Web evidence for a run: a stripped HAR and the console, recorded over CDP through the webpc044 tunnel.

from har import HarRecorder; r = HarRecorder(har_path, console_path); r.start(); ...; r.stop(); r.files()

Listens only: it sends Network.enable, Runtime.enable and Log.enable on its own connection to the one
page on the environment's origin, and never an Input method or a body read. The HAR keeps method, URL
(query values redacted), status, mime type, sizes and timings; no headers, cookies or bodies.
Console lines are redacted before they are written.
"""
import json
import os
import socket
import struct
import threading
import time
import urllib.parse
import urllib.request

from cdp_read import WS
from records import now_utc, rel, sha256_file
from redact import redact

TUNNEL_PORT = 9350
ALLOWED = {"Network.enable", "Runtime.enable", "Log.enable"}


def env_origin():
    from records import QA_DIR
    try:
        return json.load(open(os.path.join(QA_DIR, "runner", "engine.json")))["web_origin"]
    except (OSError, ValueError, KeyError):
        return "web.staging.company.example"


def strip_url(u):
    if u.startswith("data:"):
        return f"{u.split(',', 1)[0][:60]},<{len(u)} chars>"
    p = urllib.parse.urlsplit(u)
    q = urllib.parse.urlencode([(k, "x") for k, _ in urllib.parse.parse_qsl(p.query, keep_blank_values=True)])
    return urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, q, ""))


class Listener(WS):
    def send_cmd(self, n, method):
        if method not in ALLOWED:
            raise PermissionError(f"har.py sends only {sorted(ALLOWED)}")
        data = json.dumps({"id": n, "method": method}).encode()
        mask = os.urandom(4)
        hdr = bytearray([0x81, 0x80 | len(data)]) if len(data) < 126 else bytearray([0x81, 0x80 | 126]) + struct.pack(">H", len(data))
        self.s.sendall(bytes(hdr) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))


class HarRecorder:
    def __init__(self, har_path, console_path):
        self.har_path, self.console_path = har_path, console_path
        self.entries, self.reqs, self.console = [], {}, []
        self.stop_flag = threading.Event()
        self.thread = None
        self.error = None
        self.page_url = None

    def start(self):
        want = env_origin()
        pages = [p for p in json.load(urllib.request.urlopen(f"http://127.0.0.1:{TUNNEL_PORT}/json/list", timeout=5))
                 if p.get("type") == "page" and urllib.parse.urlsplit(p.get("url", "")).netloc == want]
        if len(pages) != 1:
            raise LookupError(f"expected exactly one page on {want}, found {len(pages)}")
        self.page_url = strip_url(pages[0]["url"])
        self.ws = Listener(pages[0]["webSocketDebuggerUrl"], timeout=1)
        self.t0 = time.time()
        for n, m in enumerate(sorted(ALLOWED), 1):
            self.ws.send_cmd(n, m)
        self.started = now_utc()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self):
        while not self.stop_flag.is_set():
            try:
                m = json.loads(self.ws.recv())
            except socket.timeout:
                continue
            except (OSError, ConnectionError, ValueError) as e:
                if not self.stop_flag.is_set():
                    self.error = f"{type(e).__name__}: {e}"
                return
            self._event(m.get("method"), m.get("params") or {})

    def _event(self, method, p):
        if method == "Network.requestWillBeSent":
            r = p["request"]
            self.reqs[p["requestId"]] = {"startedDateTime": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(p.get("wallTime", time.time()))) + "Z",
                                         "request": {"method": r.get("method"), "url": strip_url(r.get("url", ""))},
                                         "_type": p.get("type"), "_ts": p.get("timestamp")}
        elif method == "Network.responseReceived":
            e = self.reqs.get(p["requestId"])
            if e:
                r = p["response"]
                e["response"] = {"status": r.get("status"), "mimeType": r.get("mimeType"), "fromCache": bool(r.get("fromDiskCache"))}
        elif method == "Network.loadingFinished":
            e = self.reqs.pop(p["requestId"], None)
            if e:
                e["time_ms"] = round((p.get("timestamp", 0) - (e.pop("_ts") or 0)) * 1000)
                e["bodySize"] = p.get("encodedDataLength")
                self.entries.append(e)
        elif method == "Network.loadingFailed":
            e = self.reqs.pop(p["requestId"], None)
            if e:
                e.pop("_ts", None)
                e["failed"] = {"error": p.get("errorText"), "canceled": p.get("canceled")}
                self.entries.append(e)
        elif method == "Runtime.consoleAPICalled":
            text = " ".join(str(a.get("value", a.get("description", a.get("type")))) for a in p.get("args", []))
            self.console.append(self._line(p.get("timestamp"), {"level": p.get("type"), "text": redact(text)[:2000]}))
        elif method == "Log.entryAdded":
            e = p.get("entry", {})
            self.console.append(self._line(e.get("timestamp"), {"level": e.get("level"), "source": e.get("source"),
                                           "text": redact(e.get("text", ""))[:2000], "url": strip_url(e.get("url", "") or "")}))

    def _line(self, ts_ms, d):
        """Page time of the message (ms since epoch); messages from before start() are Chrome's replay on enable."""
        d["page_time"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts_ms / 1000)) + f".{int(ts_ms % 1000):03d}Z" if ts_ms else None
        d["replayed"] = bool(ts_ms and ts_ms / 1000 < self.t0)
        d["received"] = now_utc()
        return d

    def stop(self):
        self.stop_flag.set()
        if self.thread:
            self.thread.join(timeout=3)
        self.ws.close()
        for e in self.reqs.values():
            e.pop("_ts", None)
            e["unfinished"] = True
            self.entries.append(e)
        har = {"log": {"version": "1.2", "creator": {"name": "wingtra-qa har.py", "version": "1"},
                       "pages": [{"id": "page_1", "title": self.page_url, "startedDateTime": self.started}],
                       "entries": self.entries, "_stripped": "no headers, cookies or bodies; query values replaced by x",
                       "_error": self.error}}
        with open(self.har_path, "w", encoding="utf-8") as f:
            f.write(redact(json.dumps(har, indent=1)))
        with open(self.console_path, "w", encoding="utf-8") as f:
            for c in self.console:
                f.write(json.dumps(c) + "\n")

    def files(self):
        return [{"kind": "har", "path": rel(self.har_path), "sha256": sha256_file(self.har_path)},
                {"kind": "console", "path": rel(self.console_path), "sha256": sha256_file(self.console_path)}]
