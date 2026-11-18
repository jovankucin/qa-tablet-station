"""Tests for the QA watchdog (qa/app/watchdog.py).

python3 -I qa/app/tests/test_watchdog.py  (temporary folder, fixed clock, fake worker and app status; no notification)
"""
import datetime
import fcntl
import json
import os
import shutil
import sys
import tempfile
import unittest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_DIR)
import watchdog  # noqa: E402
import worker  # noqa: E402

UTC = datetime.timezone.utc
DAY = "2026-10-28"  # a Wednesday, CET (UTC+1)


def at(hhmm, day=DAY):
    """A UTC datetime for a Zurich local time on day (CET in late October)."""
    h, m = map(int, hhmm.split(":"))
    d = datetime.datetime.fromisoformat(day)
    return datetime.datetime(d.year, d.month, d.day, h, m, tzinfo=watchdog.ZURICH).astimezone(UTC)


def iso(t):
    return t.isoformat().replace("+00:00", "Z")


def ok_worker(state="ready", **kw):
    return lambda root, now: dict({"state": state, "heartbeat_at": "2026-10-28T07:20:00Z"}, **kw)


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="qa-watchdog-")
        self.sent = []

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def put(self, rel, obj):
        p = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            json.dump(obj, f)

    def found(self, now, status=None, app=True):
        return list(watchdog.problems(self.root, now, status or ok_worker(), lambda: app).values())

    def run_at(self, times, status=None, app=True, between=None):
        """watchdog.run with a clock that returns times in order; between() runs during the recheck wait."""
        it = iter(times)
        return watchdog.run(self.root, clock=lambda: next(it), sleep=lambda s: between and between(),
                            status=status or ok_worker(), app=lambda: app,
                            send_fn=lambda m: self.sent.append(m) or True)


class WindowTests(Base):
    def test_weekend_and_outside_hours_are_silent(self):
        self.assertEqual(self.found(at("10:00", "2026-10-31"), status=ok_worker("off")), [])  # Saturday
        self.assertEqual(self.found(at("08:30")), [])
        self.assertEqual(self.found(at("19:05"), status=ok_worker("off"), app=False), [])

    def test_next_try(self):
        self.assertEqual(watchdog.next_try(watchdog.WORKER_TRIES, at("08:55")), "09:15")
        self.assertEqual(watchdog.next_try(watchdog.WORKER_TRIES, at("12:55")), "13:15")
        self.assertEqual(watchdog.next_try(watchdog.WORKER_TRIES, at("14:30")), "15:15")
        self.assertEqual(watchdog.next_try(watchdog.WORKER_TRIES, at("18:30")), "none today")

    def test_local_day_across_utc_midnight(self):
        # 23:30 UTC on the 27th is 00:30 on the 28th in Zurich: the day is the 28th (and outside the window)
        t = datetime.datetime(2026, 10, 27, 23, 30, tzinfo=UTC)
        self.assertEqual(watchdog.local(t).strftime("%Y-%m-%d"), DAY)
        self.assertEqual(self.found(t), [])


class NoPrepTests(Base):
    """The morning prep was removed on 2026-10-29: no prep file, or an old failed one, is ever an alert."""

    def test_no_prep_alert(self):
        self.assertEqual(self.found(at("08:50")), [])
        self.put(f"qa/cache/plan/{DAY}.prep-status.json", {"kind": "today_prep_status", "day": DAY, "state": "failed",
                                                             "at": iso(at("08:05"))})
        self.assertEqual(self.found(at("08:50")), [])


class WorkerAndAppTests(Base):
    def test_worker_off_in_window(self):
        (msg,) = self.found(at("09:00"), status=ok_worker("off"))
        self.assertIn("Hover is off duty", msg)
        self.assertIn("Next try 09:15", msg)
        (msg,) = self.found(at("18:30"), status=ok_worker("off"))
        self.assertIn("Next try none today", msg)
        self.assertEqual(self.found(at("18:50"), status=ok_worker("off")), [])  # after 18:45 "off" is normal

    def test_worker_stuck_names_the_cases(self):
        self.put("qa/runner/state/worker/W-20261028-001.json", {"kind": "worker_request", "id": "W-20261028-001",
                                                                "type": "start", "engine_cases": ["TERR-005"]})
        st = ok_worker("stuck", request="W-20261028-001", last_activity=iso(at("09:10")))
        (msg,) = self.found(at("09:40"), status=st)
        self.assertEqual(msg, "Hover went quiet on TERR-005 since 09:10. See its page.")

    def test_app_down(self):
        (msg,) = self.found(at("09:00"), app=False)
        self.assertIn("Hover's page is not reachable", msg)

    def test_real_worker_status_on_an_empty_folder_is_off(self):
        self.assertEqual(worker.status(self.root, at("09:00"))["state"], "off")
        msgs = list(watchdog.problems(self.root, at("09:00"), app=lambda: True).values())
        self.assertEqual(len(msgs), 1)
        self.assertIn("Hover is off duty", msgs[0])


class RunTests(Base):
    def test_recheck_clears_a_wake_race(self):
        # at wake the worker has not written its heartbeat yet; 10 min later it is waiting: no alert
        seen = []

        def status(root, now):
            return ok_worker("ready" if seen else "off")(root, now)
        sent = self.run_at([at("09:10"), at("09:20")], status=status, between=lambda: seen.append(1))
        self.assertEqual(sent, [])
        self.assertEqual(self.sent, [])

    def test_still_there_alerts_once_then_one_reminder(self):
        off = ok_worker("off")
        self.assertEqual(len(self.run_at([at("08:45"), at("08:55")], status=off)), 1)
        self.assertEqual(self.run_at([at("09:30"), at("09:40")], status=off), [])  # same problem, under 2 h: quiet
        (msg,) = self.run_at([at("10:45"), at("10:55")], status=off)             # 2 h later: one reminder
        self.assertTrue(msg.startswith("Still: Hover is off duty, so Prepare plan and Run will wait. "
                                       "Next try 11:15"))
        self.assertEqual(self.run_at([at("12:45"), at("12:55")], status=off), [])  # no second reminder
        state = json.load(open(os.path.join(self.root, watchdog.STATE_REL)))
        self.assertEqual(state["day"], DAY)
        self.assertTrue(state["last_alert"]["text"].startswith("Still:"))

    def test_a_new_failure_later_alerts_again(self):
        self.assertEqual(len(self.run_at([at("08:45"), at("08:55")], status=ok_worker("off"))), 1)
        later = ok_worker("off", heartbeat_at=iso(at("10:06")))  # the worker ran again and stopped again
        (msg,) = self.run_at([at("10:45"), at("10:55")], status=later)
        self.assertIn("Hover is off duty", msg)

    def test_a_new_day_starts_fresh(self):
        self.run_at([at("08:45"), at("08:55")], status=ok_worker("off"))
        nxt = "2026-10-29"
        self.assertEqual(len(self.run_at([at("08:45", nxt), at("08:55", nxt)], status=ok_worker("off"))), 1)

    def test_nothing_wrong_no_wait(self):
        waited = []
        it = iter([at("09:00"), at("09:00")])
        watchdog.run(self.root, clock=lambda: next(it), sleep=waited.append, status=ok_worker(), app=lambda: True,
                     send_fn=self.sent.append)
        self.assertEqual((waited, self.sent), ([], []))


class MainTests(Base):
    def setUp(self):
        super().setUp()
        self.real = (watchdog.notify, watchdog.run)
        watchdog.notify = lambda m: self.sent.append(m) or True

    def tearDown(self):
        watchdog.notify, watchdog.run = self.real
        super().tearDown()

    def test_test_trigger_sends_one_alert_and_removes_the_file(self):
        self.put(watchdog.TEST_REL, {})
        self.assertEqual(watchdog.main(["watchdog.py"], root=self.root), 0)
        self.assertEqual(self.sent, ["QA watchdog test: alerts from the watchdog reach this Mac."])
        self.assertFalse(os.path.exists(os.path.join(self.root, watchdog.TEST_REL)))

    def test_a_crash_alerts(self):
        def boom(root):
            raise KeyError("x")
        watchdog.run = boom
        self.assertEqual(watchdog.main(["watchdog.py"], root=self.root), 1)
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.sent[0].startswith("QA watchdog error: KeyError."))

    def test_a_second_instance_leaves_at_once(self):
        watchdog.run = lambda root: self.fail("the second instance must not run")
        os.makedirs(os.path.join(self.root, "qa/runner/state"), exist_ok=True)
        with open(os.path.join(self.root, watchdog.LOCK_REL), "a") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            self.assertEqual(watchdog.main(["watchdog.py"], root=self.root), 0)

    def test_unknown_arguments_refused(self):
        self.assertEqual(watchdog.main(["watchdog.py", "--now"], root=self.root), 2)


if __name__ == "__main__":
    unittest.main(verbosity=1)
