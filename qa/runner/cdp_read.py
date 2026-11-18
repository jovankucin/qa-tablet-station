"""Read-only Chrome DevTools Protocol client (stdlib): Runtime.evaluate on one page target.

from cdp_read import evaluate; evaluate(port, "navigator.userAgent", origin=None)

Only Runtime.evaluate is ever sent; Input.* methods are refused here and by the gate.
The page target is chosen by origin when given (never "the first page" for web runs).
No Origin header is sent, so Chrome's DevTools origin check accepts the connection.
"""
import base64
import json
import os
import socket
import struct
import urllib.parse
import urllib.request

ALLOWED_METHODS = {"Runtime.evaluate"}


class WS:
    def __init__(self, url, timeout=20):
        hostport, path = url[len("ws://"):].split("/", 1)
        host, port = hostport.split(":")
        self.s = socket.create_connection((host, int(port)), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.s.sendall(req.encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            resp += self.s.recv(4096)
        if b" 101 " not in resp.split(b"\r\n")[0]:
            raise ConnectionError(resp.split(b"\r\n")[0].decode(errors="replace"))
        self.buf = resp.split(b"\r\n\r\n", 1)[1]

    def send(self, obj):
        if obj.get("method") not in ALLOWED_METHODS:
            raise PermissionError(f"cdp_read sends only {sorted(ALLOWED_METHODS)}")
        data = json.dumps(obj).encode()
        mask = os.urandom(4)
        hdr = bytearray([0x81])
        n = len(data)
        if n < 126:
            hdr.append(0x80 | n)
        elif n < 65536:
            hdr.append(0x80 | 126)
            hdr += struct.pack(">H", n)
        else:
            hdr.append(0x80 | 127)
            hdr += struct.pack(">Q", n)
        self.s.sendall(bytes(hdr) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.s.recv(65536)
            if not chunk:
                raise ConnectionError("closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def recv(self):
        msg = b""
        while True:
            b1, b2 = self._read(2)
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            msg += self._read(n)
            if b1 & 0x80:
                return msg.decode("utf-8", "replace")

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass


def targets(port):
    return json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=10))


def page_ws(port, origin=None):
    pages = [t for t in targets(port) if t.get("type") == "page"]
    if origin:
        pages = [t for t in pages if urllib.parse.urlsplit(t.get("url", "")).netloc == origin]
        if len(pages) != 1:
            raise LookupError(f"expected exactly one page on {origin}, found {len(pages)}")
    if not pages:
        raise LookupError("no page target")
    return pages[0]["webSocketDebuggerUrl"]


def evaluate(port, expr, origin=None, await_promise=True):
    ws = WS(page_ws(port, origin))
    try:
        ws.send({"id": 1, "method": "Runtime.evaluate",
                 "params": {"expression": expr, "returnByValue": True, "awaitPromise": await_promise}})
        while True:
            m = json.loads(ws.recv())
            if m.get("id") == 1:
                if "exceptionDetails" in m.get("result", {}):
                    raise RuntimeError(m["result"]["exceptionDetails"].get("text", "evaluation failed"))
                return m["result"]["result"].get("value")
    finally:
        ws.close()
