"""New test's live device status: the engine's status snapshot as one plain row per device, read at most every TTL_S."""
import json
import os
import sys
import threading
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(TESTS_DIR)
sys.path[:0] = [TESTS_DIR, APP_DIR, os.path.join(os.path.dirname(APP_DIR), "runner")]
import devstatus  # noqa: E402
import serve  # noqa: E402
from test_worker import Base, EndpointTests  # noqa: E402

TAGS = {"TA5", "ET65", "W044"}


def tablet(tag, state, adb="device", **kw):
    return dict({"tag": tag, "state": state, "adb": adb, "why": ""}, **kw)


def snap(tablets, web=None):
    return {"tablets": tablets, "webpc044": web or {"state": "READY", "link": True, "ssh": True, "chrome_9222": "up"}}


class RowsTests(unittest.TestCase):
    def texts(self, s, err=None, tags=TAGS):
        return {r["tag"]: (r["ready"], r["text"]) for r in devstatus.rows(s, err, tags)}

    def test_ready_and_what_to_do(self):
        got = self.texts(snap([tablet("TA5", "READY"), tablet("ET65", "LOST", "unauthorized")]))
        self.assertEqual(got, {"TA5": (True, "Ready"), "ET65": (False, "USB debugging: unlock ET65 and tap Allow"),
                               "W044": (True, "Ready (QA Chrome running)"), "TA3": (False, "Not set up on this Mac")})

    def test_every_tablet_state(self):
        got = self.texts(snap([tablet("TA5", "BUSY", lock="R-1", case="UPG-013"), tablet("ET65", "LOST", "absent")]))
        self.assertEqual(got["TA5"], (False, "Busy: a run is using it (UPG-013)"))
        self.assertEqual(got["ET65"], (False, "Not connected: plug ET65 in by USB"))
        got = self.texts(snap([tablet("TA5", "LOST", "offline"), tablet("ET65", "HANDED_OVER", why="hand test")]))
        self.assertEqual(got["TA5"], (False, "Not answering over USB: plug TA5 out and in again"))
        self.assertEqual(got["ET65"], (False, "Handed over: hand test"))
        self.assertEqual(self.texts(snap([]))["TA5"], (False, "Not connected: plug TA5 in by USB"))

    def test_web_states(self):
        down = {"state": "DEGRADED", "link": True, "ssh": True, "chrome_9222": "down"}
        self.assertEqual(self.texts(snap([], down))["W044"],
                         (False, "QA Chrome not running: on W044 run sh ~/qa-files/start-qa-chrome.sh"))
        self.assertEqual(self.texts(snap([], {"state": "LOST", "link": False}))["W044"],
                         (False, "Not reachable: check that W044 is on and connected"))
        self.assertEqual(self.texts(snap([], {"state": "DEGRADED", "link": True, "ssh": False}))["W044"],
                         (False, "W044 answers, but not over SSH: check W044"))

    def test_an_unreadable_status_is_unknown_not_lost(self):
        rows = devstatus.rows(None, "RuntimeError: adb not found", TAGS)
        self.assertEqual({r["tag"]: r["state"] for r in rows},
                         {"TA5": "unknown", "ET65": "unknown", "W044": "unknown", "TA3": "not_set_up"})
        self.assertFalse(any(r["ready"] for r in rows))


class ToolEnvTests(unittest.TestCase):
    def test_the_launchagent_path_gets_the_tool_folders(self):
        env = devstatus.tool_env({"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": "/h"})
        self.assertEqual(env["PATH"], "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin")
        self.assertEqual(env["HOME"], "/h")
        self.assertEqual(devstatus.tool_env({"PATH": "/opt/homebrew/bin:/usr/bin"})["PATH"],
                         "/opt/homebrew/bin:/usr/bin:/usr/local/bin")


class CacheTests(unittest.TestCase):
    def test_one_read_per_ttl_and_errors_are_kept_not_raised(self):
        calls = []

        def run():
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("adb hung")
            return {"tablets": []}
        c = devstatus.Cache("/nowhere", run=run, ttl=8)
        self.assertEqual(c.get(now=100)[:2], ({"tablets": []}, None))
        self.assertEqual(c.get(now=107)[:2], ({"tablets": []}, None))  # shared, no second read
        snap_, err, at = c.get(now=108)
        self.assertEqual((snap_, err, at, len(calls)), (None, "RuntimeError: adb hung", 108, 2))
        self.assertEqual(c.get(now=116)[1], None)

    def test_pages_asking_at_once_share_one_read(self):
        gate, calls = threading.Event(), []

        def run():
            calls.append(1)
            gate.wait(2)
            return {"tablets": []}
        c = devstatus.Cache("/nowhere", run=run, ttl=8)
        threads = [threading.Thread(target=c.get) for _ in range(4)]
        for t in threads:
            t.start()
        gate.set()
        for t in threads:
            t.join(3)
        self.assertEqual(len(calls), 1)


class EndpointDeviceTests(Base):
    _req = EndpointTests._req

    def setUp(self):
        super().setUp()
        self.srv = serve.build_server(self.root, 0)
        self.srv.devices = devstatus.Cache(self.root, run=lambda: snap([tablet("TA5", "READY")]))
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    def test_get_devices(self):
        status, body = self._req("GET", "/api/devices")
        self.assertEqual(status, 200)
        self.assertIsNone(body["error"])
        self.assertTrue(body["at"].endswith("+00:00"))
        self.assertEqual([(r["tag"], r["ready"]) for r in body["rows"]],
                         [("TA5", True), ("ET65", False), ("W044", True), ("TA3", False)])
        self.assertEqual(json.dumps(body).count("Not set up on this Mac"), 1)


if __name__ == "__main__":
    unittest.main()
