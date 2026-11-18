"""act.py: the only way the engine taps, types or presses keys on a target.

python3 -I qa/runner/act.py <target> <verb> [selector] [--expect COND ...] [options]

targets   TA5 | ET65 | TA3 | <adb serial>      (tablets, via adb + read-only CDP)
          W044                               (Chrome on webpc044, via the ssh tunnel)
verbs     probe                  identity and screen state, no input
          ready                  (tablets) wake the screen, dismiss a lock screen without a PIN, bring WingtraApp
                                 to the front, then the identity checks, then the app's offline indicator (offline
                                 is Fail 2); W044: the identity checks, which bring the app tab to the front (every
                                 W044 call does; still hidden is Fail 2). Both then close the app's own dialogs with
                                 Escape, at most 3 presses, never a tap or Back (still open: Fail 2)
          find <sel>             locate one element, no input
          tap <sel>              tap or click its centre, then wait for every --expect
          type <sel>             focus the field, insert --text TEXT or --secret VAR, verify the value
          key <back|enter|tab|escape>   press a key, then wait for every --expect
          sys-tap <exact text>   (tablets) tap a system-dialog button found by uiautomator; with only focus:
                                 readbacks it needs no app page (works in the full-screen file browser)
          choose-file <sel> --file <name>   (W044) click the control that opens the browser's file chooser and
                                 give it ~/qa-fixtures/<name> on webpc044 (Chrome's file-chooser interception;
                                 deliver puts the file there), then wait for every --expect
          read <js expression>   evaluate a read-only page expression and print its value
          shot <png path>        screenshot
          wait                   only wait for every --expect
          deliver <CASE> [--check] [--case-file F]   copy the case's test files (fixtures) to the target, if the
                                 case runs there: tablets to device_path under /sdcard/Download/, W044 to
                                 ~/qa-fixtures/; sha256-checked on the target, a different file there is never
                                 overwritten (mv -n); --check only reads; --case-file pins a version (default: the
                                 newest); see fixtures.py
          wifi <off|on>          (tablets) switch the tablet's Wi-Fi over USB (cmd wifi set-wifi-enabled, else svc wifi),
                                 then wait up to --timeout (default 20 s, one budget for every wait of the call) until
                                 the tablet reports the new state; --until-offline (off only) also waits for the app's
                                 offline indicator; --expect readbacks are optional. A device action, not a cloud write:
                                 no write list. Never on a tablet attached over Wi-Fi debugging (Fail 2)
          map-tap <fx> <fy> [--double]   tap the planner's map at fractions 0.02..0.98 of the one visible Mapbox
                                 canvas (canvas.mapboxgl-canvas, measured at call time; outside: Fail 4, none or
                                 several: Fail 3); --double taps the point again MAP_DOUBLE_GAP_S later. Refused while
                                 a dialog is open (Fail 2) or when something else covers the point (Fail 3). Draws on
                                 the map only: no write list; --expect readbacks are optional
selectors testid:<id> | text:<exact visible text> | aria:<exact aria-label> | css:<selector>
          (text is never a substring match; exactly one visible, unblocked, enabled match is required)
expect    path:<exact pathname> | text:<exact visible text> | gone:<selector> | sel:<selector>
          | focus:app | value:<selector>=<text> | js:<expression>

Before any input on a tablet: adb state, profile, forward on the profile's CDP port (--no-rebind),
userAgent model and devicePixelRatio equal to the profile, and the app in focus (sys-tap excepted).
Before any input on W044: tunnel up, the browser on port 9222 running the QA profile ~/qa-chrome, exactly one
page on the environment's origin. Every call writes ledger events.

Write-list actions (app update, install, bundle restart, MockLink start/stop, firmware and OTA
network actions, Swiss Army Knife submit, the Enter key) need --approved, which the gate turns into a
permission prompt. The check runs on the request first, before any adb, ssh or CDP call. Under
QA_RUNNER=1 they are always refused and handed back as a command to run outside the runner; there the
runner also refuses what only a device read could tell apart (Next, Continue, the shared dialog action,
css: selectors). A second check on the element found catches what the selector did not name.
The exceptions are run.py's standing steps: Next and Continue (engine piece 5), and since 2026-11-03 a flight save by
its label (FLIGHT_SAVE_TEXT) with QA_CLOUD_WRITE=1, let through only on one of the flight planner's save controls and
only while the page is the planner of a flight in QA_OWN_SITE, the site the run created (else Fail 2, handed back).
A tap on a control that creates or changes cloud data (G3_*, or any tap of a step the runner marks with
QA_CLOUD_WRITE=1) writes a side_effect record, also when the tap or its readback fails; a handed-back command
carries QA_CAMPAIGN and QA_HANDBACK_RUN so that record joins its run.
With QA_TICKET=<tests/<slug>/tickets/<n>> (the filing of a ticket the person approved with Touch ID), tap text:Next
first reads the reporter's title and description and refuses (exit 2, no tap) unless they equal that draft's title.txt
and description.txt (security review 2026-11-03).

Exit: 0 done, 2 refused (approval or policy), 3 ERROR (identity, not found, ambiguous, covered,
readback timeout), 4 usage. A failure that is a setup problem the person fixes (a tablet not connected or not
authorised, webpc044 not reachable, the QA Chrome not running or not identified, no or several app tabs) carries
"setup": true; run.py blocks the run on it only before the case's first step.
"""
import argparse
import base64
import fcntl
import glob
import http.client
import json
import os
import posixpath
import re
import shlex
import socket
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fixtures  # noqa: E402
from cdp_read import WS  # noqa: E402
from records import QA_DIR, WORKSPACE, append, rel, sha256_bytes, side_effect  # noqa: E402
from redact import redact  # noqa: E402

PKG = "com.wingtra.cloud.app"
# The app's own window: its component, not just the package name (an ANR or crash dialog's title names the package).
APP_WINDOW = re.compile(r"u0 " + re.escape(PKG) + r"/")
READY_SETTLE_S = 1.5   # after a wake, an unlock or a start, before the state is read again
READY_CONNECT_S = 25   # how long ready waits for the app's page after bringing it to the front
DELIVER_COPY_S = 600   # one test file's copy to a target (USB or the network to webpc044)
DELIVER_SUM_S = 180    # one sha256 of a test file on a target (up to 2 GiB)
DELIVER_CMD_S = 30     # mkdir, mv, rm and the reachability check on a target
CASE_ID = re.compile(r"[A-Z]{2,10}-\d{3}")  # qa/app/case_schema.py CASE_ID_RE: exact case, so APFS cannot mix versions
STATE = os.path.join(QA_DIR, "runner", "state")
TAGS = {}
WEB_TUNNEL_PORT = 9350
WEB_VISIBLE_S = 2      # the app tab's visibilityState turning visible after /json/activate
ENV_ORIGINS = {"develop": "web.dev.company.example", "staging": "web.staging.company.example"}
SECRET_ORIGINS = set(ENV_ORIGINS.values())
# every WingtraApp web origin (CLAUDE.md environments): a tab on one of these that is not the engine's is the wrong env
APP_HOSTS = {"web.dev.company.example", "web.staging.company.example", "web.pre.company.example", "web.company.example"}
# the W044 ssh calls (the tunnel and the QA Chrome check): a dead link ends in about a minute, not a hang (F4)
SSH_KEEPALIVE = ["-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=4", "-o", "ConnectTimeout=10"]
QA_CHROME_START = "Start the QA Chrome on W044: sh ~/qa-files/start-qa-chrome.sh"
DIALOG_PRESSES = 3     # F2: Escape presses ready makes to close the app's own dialogs
DIALOG_SETTLE_S = 2    # after a press, how long ready waits for the dialog to go (a fade-out is still on screen)
# the key ready presses on a tablet: Escape, as on W044 (2026-11-02 review). Back is the WebView's history back in this
# app: it leaves the page under the dialog (in the planner that aborts a running optimisation, the very thing "Abort
# optimization?" asks about) and with no history it sends the app to the background
TABLET_DIALOG_KEY = "escape"
OFFLINE_SETTLE_S = 5   # F3: the app's network status is 'unknown' (shown offline) until its first check answers
KEYBOARD_CLOSE_S = 3   # a tap the keyboard covers: how long Back may take to close it (2026-11-03)
KEYBOARD_SETTLE_S = 0.5
# map-tap (2026-11-03, the person's OK for drawing in the flight planner): fractions of the map canvas within
# MAP_MIN..MAP_MAX, so a tap never lands on the map's own buttons and attribution at its edges
MAP_MIN, MAP_MAX = 0.02, 0.98
MAP_DOUBLE_GAP_S = 0.12  # --double: the second tap at least this long after the first (a tablet adds its adb call)
READBACK_TIMEOUT_S = 8.0   # --timeout when not given
# wifi (2026-11-03, the offline-sites campaign): the person allowed the engine to switch a tablet's Wi-Fi. cmd wifi
# set-wifi-enabled is Android 11+; svc wifi is the fallback where the first is not supported (a non-zero exit or an
# error in its output). The state is read back from the tablet: cmd wifi status, else dumpsys wifi.
WIFI_TIMEOUT_S = 20.0      # --timeout of a wifi call when not given
WIFI_POLL_S = 0.5
WIFI_SET = {"off": ("cmd wifi set-wifi-enabled disabled", "svc wifi disable"),
            "on": ("cmd wifi set-wifi-enabled enabled", "svc wifi enable")}
WIFI_NOT_SUPPORTED = re.compile(r"unknown command|can't find service|not found|exception|invalid|usage|error|denial", re.I)
WIFI_STATUS_CMD = ("cmd wifi status 2>/dev/null | grep -m1 -iE 'wi-?fi is (enabled|disabled)' || "
                   "dumpsys wifi | grep -m1 -E 'Wi-Fi is (enabled|disabled)'")
WIFI_STATE_RE = re.compile(r"\bWi-?Fi is (enabled|disabled)\b", re.I)
# a tablet attached over Wi-Fi debugging (adb connect host:port, or an mDNS ..._adb-tls-connect._tcp serial): its Wi-Fi
# is the engine's own link to it, so wifi off would cut it and the safety net could not turn it back on
NETWORK_SERIAL = re.compile(r":\d+\Z|\._adb")

# Write-list controls, from WingtraCloud apps/src at 84d3721 (selector research, 2026-10-21).
# Labels are exact visible text, matched case- and spacing-insensitively. Test ids are matched with the
# shared Button's "btn-wrapper-" prefix removed.
# Controls that change cloud data or the session (G2), and the drone OTA controls (G3 reads both).
# Review 2026-11-03 (WingtraCloud 82e23ff4a), every control that saves the open flight is on the write list, so under
# the runner only run.py's standing flight save (FLIGHT_SAVE_TEXT, below) gets one through:
# - "save & exit" and "save as draft" (moved here from CLOUD_CHANGE_TEXT): Save as draft in the "Exiting the flight
#   planning" dialog is the shared btn-dialog-action (Blocker.tsx:116-160), no write-list test id, so before this a
#   tap on it ran under the runner with no hand-back;
# - "export flight": the planner's Export flight (FlightPlanItemList.tsx:333-339, the button :452-461 and the ... menu
#   item :384-390; no test id of its own) saves the open flight first when it has unsaved changes
#   (useFlightPlanMenu.ts:198-212), whatever flight and site.
CLOUD_WRITE_TEXT = {"delete", "share", "save", "rename", "log out", "save & exit", "save as draft", "export flight"}
# test ids at WingtraCloud 84d3721 (no rename test id exists)
CLOUD_WRITE_TESTID = {"btn-delete-all-files", "delete-button", "icon-delete-sharing-link", "layer-row-delete-button",
                      "sidebar-delete-item-button", "btn-share-icon", "copy-share-link", "sidebar-btn-share-flight",
                      "btn-save-and-exit", "btn-save-default-flight", "btn-save-default-geobarrier", "btn-save-oprn",
                      "btn-save-with-missing-components", "log-out-btn"}
# G3 only (never asked for, not on the write list): other controls that create or change cloud data, at
# WingtraCloud 84d3721. run.refuse_case refuses a case that taps one while no test-data site is authorized,
# and a tap on one leaves a side_effect record.
CLOUD_CHANGE_TEXT = {
    "create",                           # general.create: DuplicateFlightForm.tsx:221, NewFlightForm.tsx:231 (create flight)
    "import",                           # import_flight_dialog.import: importFlightDialog.tsx:305 (create flight)
    "archive flight", "un-archive",     # FlightsTable.tsx:102 (archiveFlightMutation)
    "archive", "unarchive",             # SiteCard.tsx:98 (archive site), DatasetsTable.tsx:113 (archive dataset)
    "add",                              # actions.add: AddDroneCode.tsx:172 (create drone), OPRNInputModal.tsx:120
    "invite", "resend invite",          # TenantInvites.tsx:186, :234
    "send",                             # components.comments.send: CommentForm.tsx:147 (create comment)
    "save new site", "save site",       # SiteCreateSidebar.tsx:214 (useSiteSaving: create or update site)
    # ("save & exit", "save as draft": on the write list since the review of 2026-11-03, CLOUD_WRITE_TEXT)
    "save as new dataset",              # ReprocessDatasetModal.tsx:157
    "sync & save",                      # CheckpointsOverview.tsx:220
    "upload",                           # general.upload: useLogsNotifier.tsx:150 (missing-log sync)
    "process", "reprocess",             # OverviewModal.tsx:97 (start processing), ReprocessTilesModal.tsx:71
    "delete all", "delete flight log", "delete from flight",  # OutputsTable.tsx:109, UlogStats.tsx:66, DatasetMenuOption.tsx:102
    "submit", "join", "restore organization",  # CreateOrganisation.tsx:151, JoinTenant.tsx:131, Welcome.tsx:167, RestoreTenant.tsx:54
    # adding a drone to the fleet (mock-drone research 2026-10-22, tests/2026-10-22-qa-app/MOCK_DRONE_PROPOSAL.md 4-5)
    "add now", "add drone", "activate via internet", "upload license file (blue)", "use setup code (activated drone)",
}
CLOUD_CHANGE_TESTID = {"btn-create-duplicate-flight", "import-flight-dialog-submit-button", "btn-add-drone-confirm",
                       "inviteBtn", "btn-resent-invite", "btn-save-site-false", "btn-save-site-true",
                       "btn-reset-default-flight", "processing-overview-modal-process", "btn-start-processing-modal", "join",
                       # AddConnectedDrone.tsx:34, AddEquipment.tsx, activation screen, SpareWing.tsx:81 at 84d3721
                       "btn-add-drone-next", "btn-add-drone", "activate-connected-drone", "btn-deactivate-drone"}
G3_TEXT = CLOUD_WRITE_TEXT | CLOUD_CHANGE_TEXT
G3_TESTID = CLOUD_WRITE_TESTID | CLOUD_CHANGE_TESTID
OTA_TEXT = {"update connected drone", "establish ota connection", "create ota network", "close ota network",
            "copy to sd card", "update wo", "update wr", "update drone", "update firmware"}
OTA_TESTID = {"ota-update-now", "ota-wifi-off-continue", "ota-wifi-off-continue-wrapper", "firmware-update-action"}
WRITE_LIST_TEXT = CLOUD_WRITE_TEXT | OTA_TEXT | {
    "update now",               # app update (useAppUpdateChecker), firmware OTA and SD-card update confirms
    "install", "install anyway",  # forced app update; Android package installer / Play Protect
    "restart now",              # applies a downloaded web bundle (LiveUpdate.reload)
    "check for updates",        # Settings > About / Fleet: downloads an APK, can open the update dialogs
    "start mocklink", "start default mocklink", "stop mocklink",
    "report bug", "send report",
    "cancel failure", "trigger failure",  # Swiss Army Knife failsafe testing: drone commands
    "disconnect", "set home position", "crash the app", "send event",  # Plane > Debug
}
WRITE_LIST_TESTID = CLOUD_WRITE_TESTID | OTA_TESTID | {"btn-start-mocklink", "btn-stop-mocklink", "btn-failure-swipe"}
# Dialogs the app opens on its own (startup, daily check, drone connect, OBC ask): no tap goes through
# while one is open, except its decline, unless the call is --approved.
UPDATE_DIALOG_TITLES = ("Update WingtraApp on this tablet?", "To continue using the app install the latest version",
                        "Restart Wingtra app to update", "Update drone firmware?")
UPDATE_DIALOG_DECLINE = {"not now"}
# Labels and test ids that are a write only in some dialogs. Outside the runner the post-find check decides
# from the dialog; under the runner they are refused before any device call.
WRITE_LIST_CONTEXT = {"next": "Swiss Army Knife submit (Next in the Report dialog)",
                      "continue": "the firmware OTA start (Continue in the turn-off-Wi-Fi dialog)"}
WRITE_LIST_CONTEXT_TESTID = {"btn-dialog-action": "the shared confirm-dialog action (also the app update's Update now)"}
WRITE_LIST_KEYS = {"enter": "Enter can submit the open form (the bug report submits on Enter in Title)"}
# Saving a flight (2026-11-03, the person's OK for the offline-sites campaign): under the runner only as run.py's
# standing step (run.standing_step: a flight the case created in the QA_ site it created itself, gated write_list and
# cloud_write, so QA_STANDING_STEP and QA_CLOUD_WRITE=1), tapped by its label, and only on one of the flight planner's
# save controls at WingtraCloud 82e23ff4a. All save the flight open in the planner (useFlightPlanMenu.ts:159-196):
# Save on the planner's tool button (tool-button, FlightPlanEditDone.tsx:183-189: opens Flight overview or Missing
# basic components) and in Missing basic components (MissingBasicComponentsModal.tsx:68-76, a draft); Save & exit in
# Flight overview on Android (FlightOverviewModal.tsx:207-218); Save or Save as draft in the exit dialog (the shared
# btn-dialog-action under "Exiting the flight planning", Blocker.tsx:143-160).
FLIGHT_SAVE_TEXT = {"save", "save & exit", "save as draft"}
FLIGHT_SAVE_TESTID = {"save": {"tool-button", "btn-save-with-missing-components"}, "save & exit": {"btn-save-and-exit"}}
FLIGHT_EXIT_DIALOG = "exiting the flight planning"  # en.json flight_planning.exiting_flight_planning
# Review 2026-11-03: the case rule reads the case, not the screen (a case that leaves its flight, a step skipped or on
# the other platform, a duplicate's Create), so the save also checks the page: only in the planner of a flight in the
# site this run created. The planner's route is /sites/<siteId>/planning/<flightId>[/...] (SiteOverviewRoutesEnum.ts:3,
# SiteOverviewRouter.router.tsx:17); after Save new site the app opens /sites/<new id>/flights/... (useSiteSaving.ts:
# 120-127), where run.py reads the id and passes it as QA_OWN_SITE. A site id is a UUID at 82e23ff4a, never "create".
SITE_ID = r"[0-9A-Za-z][0-9A-Za-z_-]{7,63}"
SITE_PATH = re.compile(rf"/sites/({SITE_ID})(?:/.*)?", re.S)


class Fail(Exception):
    def __init__(self, code, msg, **extra):
        super().__init__(msg)
        self.code, self.msg, self.extra = code, msg, extra


def ledger(target, verb, argv, rc, result):
    return append({"src": "runner", "session": os.environ.get("QA_RUN_ID", "-"), "target": target, "tool": "act.py",
            "argv": [verb] + argv, "exit": rc, "out_sha256": sha256_bytes(json.dumps(result, sort_keys=True).encode()), "ref": None})


def sh(argv, timeout=20, check=False):
    p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    if check and p.returncode:
        raise Fail(3, f"{' '.join(argv[:4])} failed: {p.stderr.strip()[:200]}")
    return p.stdout


def engine_env():
    try:
        return json.load(open(os.path.join(QA_DIR, "runner", "engine.json")))["env"]
    except (OSError, ValueError, KeyError):
        return "staging"


def engine_account():
    try:
        return json.load(open(os.path.join(QA_DIR, "runner", "engine.json"))).get("account")
    except (OSError, ValueError, AttributeError):
        return None


def load_profiles():
    out = {}

    def version(f):
        m = re.search(r"\.v(\d+)\.json$", f)
        return int(m.group(1)) if m else 0
    for f in sorted(glob.glob(os.path.join(QA_DIR, "devices", "*.json")), key=version):
        try:
            d = json.load(open(f))
        except ValueError:
            continue
        if d.get("kind") == "device":
            out[d["serial"]] = d
    return out


# ---------------------------------------------------------------- CDP session (act.py's own sender)

class Session:
    ALLOWED = {"Runtime.evaluate", "Input.insertText", "Input.dispatchMouseEvent", "Input.dispatchKeyEvent",
               "Page.captureScreenshot", "Page.bringToFront",
               # choose-file (piece 4): the file chooser is intercepted and given a delivered test file
               "Page.enable", "Page.setInterceptFileChooserDialog", "DOM.setFileInputFiles"}

    def __init__(self, ws_url):
        self.ws = WS(ws_url)
        self.n = 0
        self.events = []

    def call(self, method, params=None):
        if method not in self.ALLOWED:
            raise Fail(2, f"act.py does not send {method}")
        self.n += 1
        payload = {"id": self.n, "method": method, "params": params or {}}
        data = json.dumps(payload).encode()
        # WS.send refuses non-evaluate methods; act.py is the one allowed sender, so frame it here
        mask = os.urandom(4)
        hdr = bytearray([0x81])
        ln = len(data)
        if ln < 126:
            hdr.append(0x80 | ln)
        elif ln < 65536:
            hdr.append(0x80 | 126)
            hdr += ln.to_bytes(2, "big")
        else:
            hdr.append(0x80 | 127)
            hdr += ln.to_bytes(8, "big")
        self.ws.s.sendall(bytes(hdr) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))
        while True:
            m = json.loads(self.ws.recv())
            if "method" in m:
                self.events = (self.events + [m])[-50:]
            if m.get("id") == self.n:
                if "error" in m:
                    raise Fail(3, f"{method}: {m['error'].get('message')}")
                return m.get("result", {})

    def wait_event(self, method, timeout):
        """The params of the next event named method, waiting up to timeout seconds."""
        end = time.time() + timeout
        while True:
            for m in self.events:
                if m.get("method") == method:
                    self.events.remove(m)
                    return m.get("params") or {}
            left = end - time.time()
            if left <= 0:
                raise Fail(3, f"no {method} within {timeout:g} s")
            self.ws.s.settimeout(left)
            try:
                m = json.loads(self.ws.recv())
            except (socket.timeout, TimeoutError):
                continue
            finally:
                self.ws.s.settimeout(20)
            if "method" in m:
                self.events.append(m)

    def eval(self, expr):
        r = self.call("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
        if "exceptionDetails" in r:
            raise Fail(3, "page evaluation failed: " + r["exceptionDetails"].get("text", "")[:200])
        return r["result"].get("value")

    def close(self):
        self.ws.close()


FIND_JS = r"""
((sel) => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const visible = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && +s.opacity !== 0 &&
           r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth;
  };
  let c = [];
  if (sel.kind === 'testid') c = [...document.querySelectorAll('[data-testid="' + CSS.escape(sel.value) + '"]')];
  else if (sel.kind === 'aria') c = [...document.querySelectorAll('[aria-label="' + CSS.escape(sel.value) + '"]')];
  else if (sel.kind === 'css') c = [...document.querySelectorAll(sel.value)];
  else if (sel.kind === 'text') {
    const want = norm(sel.value);
    c = [...document.querySelectorAll('body *')].filter(el => norm(el.innerText) === want &&
         ![...el.children].some(ch => norm(ch.innerText) === want));
  }
  c = c.filter(visible);
  const dialogs = [...document.querySelectorAll('[role=dialog],[aria-modal=true]')].filter(visible);
  const top = dialogs.length ? dialogs[dialogs.length - 1] : null;
  if (top) { const inTop = c.filter(el => top.contains(el)); if (inTop.length) c = inTop; }
  return c.map(el => {
    const r = el.getBoundingClientRect(), cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    const hit = document.elementFromPoint(cx, cy);
    const clickable = el.closest('button,a,[role=button],[role=link],[role=tab],[role=menuitem],[role=option],input,label,select,textarea,[tabindex]');
    const testids = []; for (let n = el, i = 0; n && i < 6; n = n.parentElement, i++) { const t = n.getAttribute && n.getAttribute('data-testid'); if (t) testids.push(t); }
    if (clickable && clickable.getAttribute('data-testid')) testids.push(clickable.getAttribute('data-testid'));
    return { tag: el.tagName, text: norm(el.innerText).slice(0, 60), testid: el.getAttribute('data-testid'), testids,
             dialogTitle: top ? norm(top.innerText).slice(0, 120) : '',
             rect: [r.left, r.top, r.width, r.height], cx, cy,
             unblocked: !!hit && (el.contains(hit) || hit.contains(el)),
             disabled: !!(el.disabled || el.getAttribute('aria-disabled') === 'true' || (clickable && (clickable.disabled || clickable.getAttribute('aria-disabled') === 'true'))),
             inDialog: !!(top && top.contains(el)),
             label: norm((clickable || el).innerText || (clickable || el).getAttribute('aria-label')).slice(0, 60) };
  });
})
"""


def parse_sel(s):
    m = re.match(r"(testid|text|aria|css):(.*)$", s or "", re.S)
    if not m:
        raise Fail(4, f"selector must be testid:, text:, aria: or css: (got {s!r})")
    return {"kind": m.group(1), "value": m.group(2)}


def find_one(sess, sel_text, nth=None):
    sel = parse_sel(sel_text)
    found = sess.eval(FIND_JS + f"({json.dumps(sel)})") or []
    if not found:
        raise Fail(3, f"not found: {sel_text}")
    if len(found) > 1 and nth is None:
        raise Fail(3, f"ambiguous: {len(found)} visible matches for {sel_text}", matches=[f["text"] for f in found][:6])
    el = found[nth or 0]
    if not el["unblocked"]:
        raise Fail(3, f"covered: {sel_text} is under another element at its centre")
    if el["disabled"]:
        raise Fail(3, f"disabled: {sel_text}")
    return el


SCROLL_JS = r"""
((sel) => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  let c = [];
  if (sel.kind === 'testid') c = [...document.querySelectorAll('[data-testid="' + CSS.escape(sel.value) + '"]')];
  else if (sel.kind === 'aria') c = [...document.querySelectorAll('[aria-label="' + CSS.escape(sel.value) + '"]')];
  else if (sel.kind === 'css') c = [...document.querySelectorAll(sel.value)];
  else if (sel.kind === 'text') { const want = norm(sel.value);
    c = [...document.querySelectorAll('body *')].filter(el => norm(el.innerText) === want && ![...el.children].some(ch => norm(ch.innerText) === want)); }
  const rendered = c.filter(el => { const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && +s.opacity !== 0; });
  const off = rendered.filter(el => { const r = el.getBoundingClientRect();
    return r.bottom <= 0 || r.right <= 0 || r.top >= innerHeight || r.left >= innerWidth; });
  if (rendered.length !== 1 || off.length !== 1) return null;
  off[0].scrollIntoView({block: 'center', inline: 'center'});
  const r = off[0].getBoundingClientRect();
  return {top: Math.round(r.top), viewport: [innerWidth, innerHeight]};
})
"""


# type --text-from (2026-11-03): the Swiss Army Knife fields get a ticket draft's own text, read here, so the command
# carries no shell expansion (the gate asks on $(cat ...)); only a draft's title.txt or description.txt, never another file
TICKET_TEXT_RE = re.compile(r"^tests/\d{4}-\d{2}-\d{2}-[a-z0-9-]+/tickets/\d+/(title|description)\.txt\Z")


def read_ticket_text(relpath):
    """The text of a ticket draft file, as $(cat file) gives it (trailing newlines dropped). Fail 4 for any other path,
    a link anywhere on it, or a file over 8 KiB."""
    if not isinstance(relpath, str) or not TICKET_TEXT_RE.match(relpath):
        raise Fail(4, "--text-from takes a ticket draft's title.txt or description.txt (tests/<campaign>/tickets/<n>/)")
    full = WORKSPACE
    for part in relpath.split("/"):
        full = os.path.join(full, part)
        if os.path.islink(full):
            raise Fail(4, f"--text-from: {relpath} has a link on its path")
    if not os.path.isfile(full) or os.path.getsize(full) > 8192:
        raise Fail(4, f"--text-from: {relpath} is not a file of at most 8 KiB")
    with open(full, encoding="utf-8") as f:
        return f.read().replace("\r\n", "\n").rstrip("\n")


def find_waiting(sess, sel_text, nth, timeout):
    """find_one, retried while the element is missing, covered or disabled (a page still loading).

    One rendered match outside the viewport (e.g. under the on-screen keyboard) is scrolled into view first.
    """
    end = time.time() + timeout
    scrolled = None
    while True:
        try:
            el = find_one(sess, sel_text, nth)
            if scrolled:
                el["scrolled"] = scrolled
            return el
        except Fail as f:
            if f.code != 3 or f.msg.startswith("ambiguous") or time.time() > end:
                raise
            if f.msg.startswith("not found") and not scrolled:
                scrolled = sess.eval(SCROLL_JS + f"({json.dumps(parse_sel(sel_text))})")
            time.sleep(0.3)


def norm_label(s):
    return re.sub(r"\s+", " ", s or "").strip().lower()


def bare_testid(t):
    t = (t or "").strip()
    return t[len("btn-wrapper-"):] if t.startswith("btn-wrapper-") else t


def cloud_write(el, gated=False):
    """The label or test id when the element creates or changes cloud data (G3_*, or any control of a step the
    case gated cloud_write), else None."""
    label = norm_label(el.get("label") or el.get("text"))
    tids = {bare_testid(x) for x in (el.get("testid") or "").split() + list(el.get("testids") or []) if x}
    if label in G3_TEXT:
        return label
    hit = sorted(tids & G3_TESTID)
    if hit:
        return f"testid:{hit[0]}"
    if gated:
        return label or (f"testid:{sorted(tids)[0]}" if tids else "gated step")
    return None


def side_effect_what(control):
    c = control.replace("testid:", "").lower()
    if "delete" in c:
        return "cloud_delete"
    if "log" in c and "out" in c:
        return "logout"
    if any(w in c for w in ("share", "create", "import", "add", "invite", "send", "new", "submit", "process", "upload")):
        return "cloud_create"
    return "cloud_update"


def is_write_list(el, sel_text, dialog_title=""):
    label = norm_label(el.get("label") or el.get("text"))
    tids = {bare_testid(t) for t in el.get("testids") or [el.get("testid")]}
    if label in WRITE_LIST_TEXT or tids & WRITE_LIST_TESTID:
        return f"write-list control '{el.get('label') or el.get('text')}'"
    title = norm_label(dialog_title)
    if label == "next" and el.get("inDialog") and title.startswith("report"):
        return WRITE_LIST_CONTEXT["next"]
    if label == "continue" and el.get("inDialog") and "wi-fi" in title:
        return WRITE_LIST_CONTEXT["continue"]
    return None


def standing_flight_save(a):
    """True for run.py's standing flight-save tap (FLIGHT_SAVE_TEXT): tap text:<one of those labels> with
    QA_STANDING_STEP and QA_CLOUD_WRITE=1 set. The caller checks it runs under the runner."""
    if a.verb != "tap" or not os.environ.get("QA_STANDING_STEP") or os.environ.get("QA_CLOUD_WRITE") != "1":
        return False
    m = re.match(r"text:(.*)$", a.selector or "", re.S)
    return bool(m) and norm_label(m.group(1)) in FLIGHT_SAVE_TEXT


def flight_save_control(el, sel_text, dialog_title=""):
    """None when the element found for a standing flight-save tap is one of the planner's save controls for that label
    (FLIGHT_SAVE_TESTID, or Save / Save as draft in the exit dialog) with no other write-list test id on it, else why
    not."""
    want = norm_label(parse_sel(sel_text)["value"])
    tids = {bare_testid(t) for t in el.get("testids") or [el.get("testid")] if t}
    own = FLIGHT_SAVE_TESTID.get(want, set())
    exit_dialog = (want != "save & exit" and bool(el.get("inDialog")) and "btn-dialog-action" in tids
                   and norm_label(dialog_title).startswith(FLIGHT_EXIT_DIALOG))
    same_label = norm_label(el.get("label") or el.get("text")) == want
    if same_label and (tids & own or exit_dialog) and not (tids & WRITE_LIST_TESTID) - own:
        return None
    return f"'{el.get('label') or el.get('text')}' is not one of the flight planner's save controls"


def own_site_planner(t, a):
    """Before a standing flight-save tap (review 2026-11-03): the page must be the planner of a flight in QA_OWN_SITE,
    the site this run created (run.py reads it after the case's Save new site). Else Fail 2 and the hand-back, before
    any input: the case left its flight, or the run never saw its own site."""
    site = os.environ.get("QA_OWN_SITE") or ""
    path = t.sess.eval("location.pathname")
    if re.fullmatch(SITE_ID, site) and isinstance(path, str) and \
            re.fullmatch(rf"/sites/{re.escape(site)}/planning/[^/]+(?:/.*)?", path, re.S):
        return {"site": site, "path": path}
    seen = f"site {site}" if site else "no such site seen"
    raise Fail(2, f"a flight save runs under the runner only in the planner of a flight in the site this run created "
                  f"({seen}; the page is {str(path)[:100]!r}): refused, hand back to Claude to run with your OK",
               command=hand_back(a))


def write_list_request(a, runner):
    """The write-list check on the request alone, made before any device call.

    tap/type/sys-tap selectors that name a write-list label or test id are refused here, and so is a
    write-list key (Enter) everywhere: no device read tells whether a form that submits on Enter is open.
    Under the runner a context-dependent label (Next) and a css: selector are refused too, because
    telling them apart would need a device read first; outside the runner the post-find check covers them.
    """
    if a.verb == "key":
        k = norm_label(a.selector)
        return f"key {k}: {WRITE_LIST_KEYS[k]}" if k in WRITE_LIST_KEYS else None
    if a.verb not in ("tap", "type", "sys-tap", "choose-file") or not a.selector:
        return None
    if a.verb == "sys-tap":
        kind, value = "text", a.selector
    else:
        sel = parse_sel(a.selector)
        kind, value = sel["kind"], sel["value"]
    label = norm_label(value)
    if kind in ("text", "aria") and label in WRITE_LIST_TEXT:
        if runner and standing_flight_save(a):
            return None  # 2026-11-03: run.py's standing flight save; the control check after the find still applies
        return f"write-list control '{value.strip()}'"
    if kind == "testid" and bare_testid(value) in WRITE_LIST_TESTID:
        return f"write-list control testid {value.strip()}"
    if runner and kind == "testid" and bare_testid(value) in WRITE_LIST_CONTEXT_TESTID:
        return f"testid {value.strip()} is {WRITE_LIST_CONTEXT_TESTID[bare_testid(value)]}; the runner taps it by its label"
    if runner and kind in ("text", "aria") and label in WRITE_LIST_CONTEXT:
        if kind == "text" and a.verb == "tap" and os.environ.get("QA_STANDING_STEP"):
            return None  # piece 5: run.py's standing rule; the dialog check after the find still applies
        return f"'{value.strip()}' can be {WRITE_LIST_CONTEXT[label]}; the runner does not tap it"
    if runner and kind == "css":
        return "the runner taps only testid:, text: or aria: selectors, which are checked before any device call"
    return None


def handback_env(campaign=None, run_id=None):
    """The env prefix of a handed-back command, so its side_effect lands in the run's campaign and names the run."""
    env = {"QA_CAMPAIGN": campaign or os.environ.get("QA_CAMPAIGN"), "QA_HANDBACK_RUN": run_id or os.environ.get("QA_RUN_ID")}
    return "".join(f"{k}={shlex.quote(v)} " for k, v in env.items() if v)


def hand_back(a):
    return handback_env() + " ".join(["python3 -I qa/runner/act.py", a.target, a.verb, json.dumps(a.selector)] +
                                     sum([["--expect", json.dumps(x)] for x in a.expect or []], []) + ["--approved"])


UPDATE_DIALOG_JS = r"""
((titles) => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const open = [...document.querySelectorAll('[role=dialog],[data-modal-overlay]')].filter(d => {
    const r = d.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
  for (const d of open) { const t = norm(d.innerText); const hit = titles.find(x => t.startsWith(x) || t.includes(x)); if (hit) return hit; }
  return null;
})
"""

# F2: the app's own open dialogs (FIND_JS's [role=dialog],[aria-modal=true], visible), outermost only, as their titles in
# page order (the last is on top): the aria-labelledby text, else the first heading, else the first line of text.
DIALOGS_JS = r"""
(() => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const visible = el => { const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && +s.opacity !== 0; };
  const all = [...document.querySelectorAll('[role=dialog],[aria-modal=true]')].filter(visible);
  return all.filter(d => !all.some(o => o !== d && o.contains(d))).map(d => {
    const by = d.getAttribute('aria-labelledby');
    const h = (by && document.getElementById(by)) || d.querySelector('h1,h2,h3,h4,h5,h6,[role=heading]');
    return norm((h && h.innerText) || (d.innerText || '').split('\n').find(l => l.trim()) || '').slice(0, 80);
  });
})()
"""
# F3: the app's own offline indicator, WingtraCloud 261b2d9 NavbarSyncStatusIcon.tsx:65 (AppNavbar.spec.tsx:164-175).
# It is rendered while useOfflineMode().isOnline is false, the state in which OfflineBlockingTooltip greys out Maps and
# the other disableWhenOffline links (AppNavbar.tsx:52, inert and pointer-events none: a tap does nothing). Read by
# presence: the navbar keeps a hidden desktop copy, and the whole navbar is hidden in the fly view.
OFFLINE_JS = "!!document.querySelector('[data-testid=\"offline-status-indicator\"]')"
KEY_NAMES = {"escape": "Escape"}  # the only key reset_dialogs presses (2026-11-02 review: not Back, never Enter)


def reset_dialogs(t, tag, key, actions, done):
    """F2 (2026-11-02): close the app's own dialogs a stopped run left open (e.g. "Abort optimization?" on W044, which
    covered btn-maps for the next run), by pressing Escape at most DIALOG_PRESSES times while one is open; each press is
    in actions with the dialog's title. Never a tap or a click: at WingtraCloud 261b2d9 the app's Dialog closes on an
    Escape keydown on the document (floating-ui useDismiss escapeKey, Dialog/hooks/useDialog.tsx) through its close
    handler: ConfirmDialog calls onClose or onCancel, "Abort optimization?" answers No
    (OptimisationNavigationConfirmation.tsx:118); it never confirms. A dialog still open after the last press is the
    person's (Fail 2). Escape on a tablet (KEYCODE_ESCAPE) is not yet seen on a device: a dialog it does not close blocks
    the run, it is never tapped away."""
    if key not in KEY_NAMES:
        raise Fail(4, f"reset_dialogs presses only {sorted(KEY_NAMES)}, not {key}")
    for n in range(DIALOG_PRESSES + 1):
        titles = t.sess.eval(DIALOGS_JS) or []
        if not titles:
            return
        title = titles[-1] or "(no title)"
        if n == DIALOG_PRESSES:
            raise Fail(2, f'A dialog stays open on {tag}: "{title}". Close it, then start again.', ready=done())
        t.key(key)
        actions.append(f'pressed {KEY_NAMES[key]} to close the app\'s dialog "{title}"')
        end = time.time() + DIALOG_SETTLE_S
        while time.time() < end and (t.sess.eval(DIALOGS_JS) or []) == titles:
            time.sleep(0.25)


COND_JS = r"""
((c) => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const visible = el => { const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const q = (sel) => {
    const [k, ...rest] = sel.split(':'); const v = rest.join(':');
    if (k === 'testid') return [...document.querySelectorAll('[data-testid="' + CSS.escape(v) + '"]')].filter(visible);
    if (k === 'aria') return [...document.querySelectorAll('[aria-label="' + CSS.escape(v) + '"]')].filter(visible);
    if (k === 'css') return [...document.querySelectorAll(v)].filter(visible);
    if (k === 'text') return [...document.querySelectorAll('body *')].filter(el => norm(el.innerText) === norm(v) && visible(el));
    return [];
  };
  if (c.kind === 'path') return location.pathname === c.value;
  if (c.kind === 'text') return q('text:' + c.value).length > 0;
  if (c.kind === 'sel') return q(c.value).length > 0;
  if (c.kind === 'gone') return q(c.value).length === 0;
  if (c.kind === 'value') { const [s, ...t] = c.value.split('='); const el = q(s)[0]; return !!el && el.value === t.join('='); }
  if (c.kind === 'js') return !!(0, eval)(c.value);
  return false;
})
"""


def parse_cond(s):
    m = re.match(r"(path|text|gone|sel|focus|value|js):(.*)$", s, re.S)
    if not m:
        raise Fail(4, f"bad --expect {s!r}")
    return {"kind": m.group(1), "value": m.group(2)}


# map-tap: the planner's map is one Mapbox GL canvas that fills the page (WingtraCloud 82e23ff4a
# FlightPlanningMap.tsx:99); the toolbars and the right sidebar lie over it (MapViewportSizeObserver.tsx:17-20), so the
# point is hit-tested. Open dialogs as DIALOGS_JS and UPDATE_DIALOG_JS find them, outermost only, by title; the canvases
# visible in the viewport (a phone-width item form adds a second, SmallMapCopy.tsx:37-45); the point at fractions
# fx, fy of the first one's visible rectangle and what lies on top there.
MAP_JS = r"""
((fx, fy) => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const shown = el => { const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && +s.opacity !== 0 &&
           r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth; };
  const all = [...document.querySelectorAll('[role=dialog],[aria-modal=true],[data-modal-overlay]')].filter(shown);
  const dialogs = all.filter(d => !all.some(o => o !== d && o.contains(d))).map(d => {
    const by = d.getAttribute('aria-labelledby');
    const h = (by && document.getElementById(by)) || d.querySelector('h1,h2,h3,h4,h5,h6,[role=heading]');
    return norm((h && h.innerText) || (d.innerText || '').split('\n').find(l => l.trim()) || '').slice(0, 80);
  });
  const canvases = [...document.querySelectorAll('canvas.mapboxgl-canvas')].filter(shown);
  const out = {canvases: canvases.length, dialogs};
  if (canvases.length !== 1) return out;
  const c = canvases[0], r = c.getBoundingClientRect();
  const l = Math.max(r.left, 0), t = Math.max(r.top, 0);
  const w = Math.min(r.right, innerWidth) - l, h = Math.min(r.bottom, innerHeight) - t;
  const x = l + fx * w, y = t + fy * h, hit = document.elementFromPoint(x, y);
  const cls = hit && typeof hit.className === 'string' ? hit.className.trim().split(/\s+/).slice(0, 2).join('.') : '';
  out.rect = [l, t, w, h];
  out.point = [x, y];
  out.hit = hit === c ? 'canvas' : !hit ? 'nothing' : hit.tagName.toLowerCase() +
    (hit.getAttribute('data-testid') ? '[data-testid=' + hit.getAttribute('data-testid') + ']' : '') +
    (cls ? '.' + cls : '') + (norm(hit.innerText) ? ' "' + norm(hit.innerText).slice(0, 40) + '"' : '');
  return out;
})
"""


# ---------------------------------------------------------------- tablet target

class Tablet:
    def __init__(self, serial, prof):
        self.serial, self.prof = serial, prof
        self.tag = prof.get("tag") or serial
        self.port = prof["cdp_port"]
        self.sess = None
        self.origin = None

    def adb(self, *args, timeout=20, check=False):
        return sh(["adb", "-s", self.serial, *args], timeout, check)

    def focus(self):
        f = self.adb("shell", "dumpsys window displays | grep -m1 mCurrentFocus")
        m = re.search(r"u0 ([\w.]+)/([\w.$]+)", f)
        if APP_WINDOW.search(f):
            return "app", f.strip()
        if m:
            return m.group(1), f.strip()
        return "other", f.strip()

    def connect(self):
        conn = self.adb("get-state").strip()
        if conn != "device":
            # F4: the same words as ready's; a setup problem, which blocks the run only before the case's first step
            raise Fail(3, f"{self.serial} is not reachable over USB (adb state: {conn or 'unauthorized, offline or '
                          'unplugged'}): connect it and allow USB debugging", setup=True)
        pid = self.adb("shell", f"pidof {PKG}").strip().split(" ")[0]
        if not pid:
            raise Fail(3, "the app is not running")
        sock = f"webview_devtools_remote_{pid}"
        fl = sh(["adb", "forward", "--list"])
        mine = [l for l in fl.splitlines() if l.startswith(self.serial + " ") and f" tcp:{self.port} " in f" {l} "]
        if mine and sock not in mine[0]:
            self.adb("forward", "--remove", f"tcp:{self.port}")
            mine = []
        if not mine:
            self.adb("forward", "--no-rebind", f"tcp:{self.port}", f"localabstract:{sock}")
        pages = json.load(urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/list", timeout=10))
        live = []
        for p in pages:
            if p.get("type") != "page" or not p.get("url", "").startswith("https://"):
                continue
            try:
                d = json.loads(p.get("description") or "{}")
            except ValueError:
                d = {}
            if d.get("attached") and d.get("visible"):
                live.append((p, d))
        if len(live) != 1:
            hidden = [p for p in pages if p.get("type") == "page" and '"visible":false' in (p.get("description") or "")]
            if not live and hidden:
                power = self.adb("shell", "dumpsys power | grep -m1 mWakefulness=; dumpsys window displays | grep -m1 isKeyguardShowing")
                if "Awake" not in power or "isKeyguardShowing=true" in power:
                    raise Fail(3, "the tablet's screen is off or locked (" + " ".join(power.split())[:80] + "); wake and unlock it")
                raise Fail(3, f"the app page is not visible (focus: {self.focus()[1][:80]})")
            raise Fail(3, f"expected one attached, visible app page on CDP port {self.port}, found {len(live)}")
        page, d = live[0]
        # Android's DevTools reports the WebView's screen rectangle: the tap origin
        self.rect = (d["screenX"], d["screenY"], d["screenX"] + d["width"], d["screenY"] + d["height"]) if "screenY" in d else None
        self.sess = Session(page["webSocketDebuggerUrl"])
        info = self.sess.eval("({ua: navigator.userAgent, dpr: devicePixelRatio, origin: location.host, path: location.pathname})")
        m = re.search(r"Android [\d.]+; ([^;)]+?)(?: Build|\))", info["ua"])
        ua_model = m.group(1).strip() if m else None
        if ua_model != self.prof.get("user_agent_model"):
            raise Fail(3, f"identity: userAgent model {ua_model!r} is not the profile's {self.prof.get('user_agent_model')!r}")
        if abs(float(info["dpr"]) - float(self.prof.get("dpr") or 0)) > 1e-6:
            raise Fail(3, f"identity: devicePixelRatio {info['dpr']} is not the profile's {self.prof.get('dpr')}")
        if info["origin"] != ENV_ORIGINS[engine_env()]:
            raise Fail(3, f"identity: the app is on {info['origin']}, the engine is on {ENV_ORIGINS[engine_env()]} ({engine_env()})")
        self.origin = info["origin"]
        return {"pid": pid, "port": self.port, "ua_model": ua_model, "dpr": info["dpr"], "origin": info["origin"], "path": info["path"],
                "webview_rect": self.rect}

    def webview_origin(self):
        if getattr(self, "rect", None):
            return self.rect
        x = self.ui_dump()
        for m in re.finditer(r"<node [^>]*class=\"android\.webkit\.WebView\"[^>]*>", x):
            if f'package="{PKG}"' in m.group(0):
                b = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', m.group(0))
                return tuple(map(int, b.groups()))
        raise Fail(3, "the app's WebView is not on screen (something covers it)")

    def state(self):
        """Screen, lock screen, the window in front and whether the app runs: one adb call, read only. The window
        comes from `dumpsys window displays`: a plain `dumpsys window` starts with the last ANR's snapshot, which
        Android keeps for two hours and which holds the focus of that moment."""
        out = self.adb("shell", "dumpsys power | grep -m1 'mWakefulness='; dumpsys window displays | grep -m1 "
                                "isKeyguardShowing; dumpsys window displays | grep -m1 mCurrentFocus; "
                                f"pidof {PKG} || true")
        wake = re.search(r"mWakefulness=(\w+)", out)
        f = re.search(r"mCurrentFocus=.*", out)
        foc = f.group(0).strip() if f else ""
        m = re.search(r"u0 ([\w.]+)/", foc)
        return {"wakefulness": wake.group(1) if wake else "unknown", "keyguard": "isKeyguardShowing=true" in out,
                "focus": "app" if APP_WINDOW.search(foc) else (m.group(1) if m else "other"), "focus_raw": foc[:160],
                "app_running": any(re.fullmatch(r"\d+(?: \d+)*", ln.strip()) for ln in out.splitlines())}

    def offline(self):
        """F3: True when the app shows its offline indicator (OFFLINE_JS) and it does not clear within OFFLINE_SETTLE_S."""
        end = time.time() + OFFLINE_SETTLE_S
        while self.sess.eval(OFFLINE_JS):
            if time.time() >= end:
                return True
            time.sleep(0.5)
        return False

    def wifi_state(self):
        """'on', 'off' or 'unknown': the Wi-Fi state the tablet reports (one adb shell call, read only)."""
        m = WIFI_STATE_RE.search(self.adb("shell", WIFI_STATUS_CMD))
        return ("on" if m.group(1).lower() == "enabled" else "off") if m else "unknown"

    def wifi(self, want, deadline):
        """The wifi verb (2026-11-03): switch the tablet's Wi-Fi off or on over USB, then read the state back until it is
        the one asked for or the deadline passes. Returns what it did ({want, before, tried, command, after, waited_s}).
        Fail 2 on a tablet attached over Wi-Fi debugging; Fail 3 when it is not reachable (setup), when neither command
        switches it, or when the readback runs out; every Fail 3 after the first command carries the record (wifi)."""
        if NETWORK_SERIAL.search(self.serial):
            raise Fail(2, f"{self.tag} is attached over Wi-Fi debugging ({self.serial}): switching its Wi-Fi would cut the "
                          "engine's own link to it. Attach it over USB")
        conn = self.adb("get-state").strip()
        if conn != "device":
            raise Fail(3, f"{self.serial} is not reachable over USB (adb state: {conn or 'unauthorized, offline or '
                          'unplugged'}): connect it and allow USB debugging", setup=True)
        rec = {"want": want, "before": self.wifi_state(), "tried": [], "command": None, "after": None}
        try:
            return self._wifi_switch(want, deadline, rec)
        except (OSError, subprocess.TimeoutExpired) as e:  # review 2026-11-03: an adb call that hangs or fails mid-way
            raise Fail(3, f"Wi-Fi {want} on {self.tag}: {type(e).__name__}: {e}"[:300], wifi=rec) from e

    def _wifi_switch(self, want, deadline, rec):
        for cmd in WIFI_SET[want]:
            out = self.adb("shell", f"{cmd} 2>&1; echo QA_RC=$?")
            m = re.search(r"QA_RC=(\d+)", out)
            said = (out[:m.start()] if m else out).strip()
            rec["tried"].append({"command": cmd, "rc": int(m.group(1)) if m else None, "output": said[:200]})
            if m and m.group(1) == "0" and not WIFI_NOT_SUPPORTED.search(said):
                rec["command"] = cmd
                break
        else:
            raise Fail(3, f"Wi-Fi could not be switched {want} on {self.tag}: " + "; ".join(
                f"{x['command']} (exit {x['rc']}) {x['output'][:80]}".strip() for x in rec["tried"]), wifi=rec)
        start = time.time()
        while True:
            rec["after"] = self.wifi_state()
            if rec["after"] == want:
                rec["waited_s"] = round(time.time() - start, 1)
                return rec
            if time.time() >= deadline:
                raise Fail(3, f"readback: Wi-Fi on {self.tag} reads {rec['after']}, not {want}, "
                              f"{time.time() - start:.0f} s after {rec['command']}", wifi=rec)
            time.sleep(WIFI_POLL_S)

    def ready(self):
        """Engine piece 1 (2026-10-26): make the tablet usable without a person. Wake the screen, dismiss a lock
        screen that has no PIN, bring WingtraApp to the front (it covers the USB-preferences screen, Settings and
        other apps; it starts the app when it does not run), then wait for the page and run the identity checks.
        Not reachable over USB, a PIN, pattern or password, or a window that stays on top is the person's: Fail 2
        with what to do. Then (2026-11-02) the app's update dialog (a forced one, without Not now, F4), the app
        offline (F3) and the app's own dialogs left open (F2, reset_dialogs) are the person's too. Every failure
        carries what ready did before it (ready.actions)."""
        conn = self.adb("get-state").strip()
        if conn != "device":
            raise Fail(2, f"{self.serial} is not reachable over USB (adb state: {conn or 'unauthorized, offline or '
                          'unplugged'}): connect it and allow USB debugging", ready={"actions": []})
        before = st = self.state()
        actions = []

        def done():
            return {"before": before, "actions": list(actions), "after": st}

        if st["wakefulness"] != "Awake":
            self.adb("shell", "input", "keyevent", "KEYCODE_WAKEUP")
            actions.append("woke the screen")
            time.sleep(READY_SETTLE_S)
            st = self.state()
        if st["keyguard"]:
            self.adb("shell", "wm", "dismiss-keyguard")
            actions.append("dismissed the lock screen")
            time.sleep(READY_SETTLE_S)
            st = self.state()
            if st["keyguard"]:
                raise Fail(2, "the lock screen has a PIN, pattern or password: unlock it by hand, or keep the screen on "
                              "while charging so it never locks", ready=done())
        if "NotificationShade" in st["focus_raw"]:  # the notification or quick-settings panel is pulled down
            self.adb("shell", "cmd", "statusbar", "collapse")
            actions.append("closed the notification panel")
            time.sleep(READY_SETTLE_S)
            st = self.state()
        if st["focus"] != "app" or not st["app_running"]:
            covered = st["focus"] if st["focus"] not in ("app", "other") else None
            try:
                self.adb("shell", "am", "start", "-W", "-a", "android.intent.action.MAIN", "-c",
                         "android.intent.category.LAUNCHER", "-n", f"{PKG}/.MainActivity", timeout=40)
            except subprocess.TimeoutExpired:
                raise Fail(3, "starting WingtraApp timed out (40 s)", ready=done()) from None
            actions.append("brought WingtraApp to the front" +
                           ("" if st["app_running"] else " (started it: a downloaded app update may have been applied, "
                                                         "so the build is read again after ready)") +
                           (f" over {covered}" if covered else ""))
            time.sleep(READY_SETTLE_S)
            st = self.state()
            if st["focus"] != "app":
                raise Fail(2, f"WingtraApp could not be brought to the front; on top: {st['focus_raw'][:120]}",
                           ready=done())
        deadline = time.time() + READY_CONNECT_S
        while True:
            try:
                identity = self.connect()
                break
            except Fail as e:  # a page still loading after a start, or a forward to a new process
                if e.code != 3 or time.time() > deadline:
                    raise Fail(e.code, e.msg, **dict(e.extra, ready=done())) from None
            except OSError as e:  # DevTools socket not there yet right after a start
                if time.time() > deadline:
                    raise Fail(3, f"{type(e).__name__}: {e}"[:300], ready=done()) from None
            time.sleep(2)
        # the app's own update dialog (it shows on a resume while a native update is pending) blocks every tap:
        # decline it (Not now, the one tap allowed without approval); installing stays the person's
        upd = self.sess.eval(UPDATE_DIALOG_JS + f"({json.dumps(UPDATE_DIALOG_TITLES)})") if self.sess else None
        if upd:
            try:
                el = find_waiting(self.sess, "text:Not now", None, 5)
            except Fail as e:
                if not e.msg.startswith("not found"):
                    raise Fail(e.code, e.msg, **dict(e.extra, ready=done())) from None
                # F4: the forced update (WingtraCloud 261b2d9 useAppUpdateChecker.ts:218-225: no cancel, no dismiss)
                raise Fail(2, f"Install the app update on {self.tag} by hand (the app shows '{upd}' without Not now)",
                           ready=done()) from None
            self.tap_css(el["cx"], el["cy"])
            actions.append(f"declined the app's dialog '{upd}' (Not now; an app update is pending)")
            time.sleep(READY_SETTLE_S)
            if self.sess.eval(UPDATE_DIALOG_JS + f"({json.dumps(UPDATE_DIALOG_TITLES)})"):
                raise Fail(2, f"the app's dialog '{upd}' stays open after Not now", ready=done())
        # F3: offline is reported, not a stop: run.py blocks only a case that needs the network (run.needs_network);
        # a case that reads local files (UPG-013) runs on (R-20261028-TA5-UPG-013-01 passed with the app offline)
        offline = bool(self.sess) and self.offline()
        if offline:
            actions.append("the app shows it is offline (no network): check the Wi-Fi")
        if self.sess:
            reset_dialogs(self, self.tag, TABLET_DIALOG_KEY, actions, done)
        return {"ready": done(), "identity": identity, "offline": offline}

    # test files (deliver): the absolute path on the tablet, toybox's sha256sum and mv (-T: never into a folder that
    # appeared at the destination), adb shell and adb push
    SHA_CMD = "sha256sum"
    MV_CMD = "mv -n -T"

    def dest(self, item):
        return item["tablet"]

    def shown(self, path):
        return path

    def where(self):
        return self.serial

    def reachable(self):
        conn = self.adb("get-state").strip()
        if conn != "device":
            raise Fail(2, f"{self.serial} is not reachable over USB (adb state: {conn or 'unauthorized, offline or '
                          'unplugged'}): connect it and allow USB debugging")

    def remote(self, cmd, timeout=DELIVER_CMD_S):
        return self.adb("shell", cmd, timeout=timeout)

    def copy(self, src, dest):
        self.adb("push", src, dest, timeout=DELIVER_COPY_S, check=True)

    def require_app_focus(self):
        who, raw = self.focus()
        if who != "app":
            raise Fail(3, f"focus is not the app: {raw[:120]}", focus=who)

    def keyboard_shown(self):
        """True while the on-screen keyboard is up (the input method's own state, read only)."""
        out = self.adb("shell", "dumpsys input_method | grep -m1 mInputShown || true")
        return "mInputShown=true" in out

    def tap_css(self, cx, cy):
        l, t, r, b = self.webview_origin()
        dpr = float(self.prof["dpr"])
        x, y = round(l + cx * dpr), round(t + cy * dpr)
        closed = False
        if not (l <= x < r and t <= y < b):
            # 2026-11-03 (R-20261103-ET65-OFFL-001-01, -03, -04): a target below the WebView's bounds. Either the keyboard
            # of the field just typed in still covers it: Back closes it, only while Android says it shows, as Back
            # with no keyboard leaves the page. Or DevTools' screen rectangle is stale (it kept the keyboard-sized
            # WebView after the keyboard had closed). Either way the bounds are measured again from the screen itself
            # (the UI dump); a target still outside them stops as before.
            if self.keyboard_shown():
                self.adb("shell", "input", "keyevent", "KEYCODE_BACK")
                closed = True
                deadline = time.time() + KEYBOARD_CLOSE_S
                while self.keyboard_shown() and time.time() < deadline:
                    time.sleep(0.25)
                time.sleep(KEYBOARD_SETTLE_S)  # the WebView takes the freed space back
            self.rect = None
            l, t, r, b = self.webview_origin()
            x, y = round(l + cx * dpr), round(t + cy * dpr)
        if not (l <= x < r and t <= y < b):
            raise Fail(3, f"computed tap ({x},{y}) is outside the WebView {l,t,r,b}" +
                       (" (after closing the keyboard)" if closed else " (measured again from the screen)"))
        self.adb("shell", "input", "tap", str(x), str(y))
        out = {"screen": [x, y], "webview": [l, t, r, b]}
        if closed:
            out["keyboard_closed"] = True
        return out

    def key(self, name):
        code = {"back": "KEYCODE_BACK", "enter": "KEYCODE_ENTER", "tab": "KEYCODE_TAB", "escape": "KEYCODE_ESCAPE"}[name]
        self.adb("shell", "input", "keyevent", code)

    def ui_dump(self):
        """The screen as uiautomator XML; the old dump file is removed first, so a failed dump is never read as the
        current screen (piece 3)."""
        for _ in range(3):
            x = self.adb("shell", "rm -f /data/local/tmp/qa_ui.xml; uiautomator dump /data/local/tmp/qa_ui.xml "
                                  ">/dev/null 2>&1; cat /data/local/tmp/qa_ui.xml 2>/dev/null", timeout=30)
            if "</hierarchy>" in x:
                return x
            time.sleep(1)
        raise Fail(3, "uiautomator could not dump the screen (3 tries)")

    def sys_tap(self, text, system_only=False):
        x = self.ui_dump()
        hits = []
        for m in re.finditer(r"<node [^>]*>", x):
            node = m.group(0)
            if 'resource-id="com.google.android.documentsui:id/breadcrumb_text"' in node or \
                    'resource-id="com.android.documentsui:id/breadcrumb_text"' in node:
                continue  # the file browser's path labels: never a tap target, and shown behind the places drawer
            t = re.search(r' text="([^"]*)"', node)
            d = re.search(r' content-desc="([^"]*)"', node)
            # a picture tile in the file browser's grid carries its name only in its description "<name>, <size>, …"
            tile = system_only and d and not (t and t.group(1)) and d.group(1).startswith(text + ", ")
            if (t and t.group(1).strip() == text) or (d and d.group(1).strip() == text) or tile:
                b = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', node)
                pkg = re.search(r' package="([^"]*)"', node)
                hits.append((tuple(map(int, b.groups())), pkg.group(1) if pkg else "?", bool(tile)))
        if len(hits) != 1:
            raise Fail(3, f"system text {text!r}: {len(hits)} matches on screen")
        (l, t, r, b), pkg, tile = hits[0]
        if system_only and pkg == PKG:
            raise Fail(3, f"system text {text!r} is in the app, not a system window: no tap without the app checks")
        # a tile is tapped in its lower-left quarter: its preview button sits in the top-right corner
        x, y = ((l + (r - l) // 4), (b - (b - t) // 4)) if tile else ((l + r) // 2, (t + b) // 2)
        self.adb("shell", "input", "tap", str(x), str(y))
        return {"screen": [x, y], "package": pkg}

    def shot(self, path):
        p = subprocess.run(["adb", "-s", self.serial, "exec-out", "screencap", "-p"], capture_output=True, timeout=30)
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        open(path, "wb").write(p.stdout)
        return {"path": path, "sha256": sha256_bytes(p.stdout), "bytes": len(p.stdout)}

    def cond(self, c):
        if c["kind"] == "focus":
            return self.focus()[0] == c["value"]
        return bool(self.sess.eval(COND_JS + f"({json.dumps(c)})"))


# ---------------------------------------------------------------- web target (webpc044)

class Web:
    def __init__(self):
        self.sess = None
        self.origin = None
        self.target = "W044"

    def tunnel(self):
        try:
            socket.create_connection(("127.0.0.1", WEB_TUNNEL_PORT), timeout=1).close()
            return "up"
        except OSError:
            pass
        subprocess.Popen(["ssh", "-N", *SSH_KEEPALIVE, "-L", f"{WEB_TUNNEL_PORT}:127.0.0.1:9222", "webpc044"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", WEB_TUNNEL_PORT), timeout=1).close()
                return "started"
            except OSError:
                time.sleep(0.1)
        raise Fail(3, "could not open the tunnel to webpc044: switch it on and check the network", setup=True)

    def qa_chrome(self, bid):
        """F4: the browser listening on webpc044's port 9222 must be the QA Chrome: the one serving it from its own
        profile (~/qa-chrome), told by its process, not by the id in ~/qa-chrome.log (a Chrome update relaunches it with
        a new id the log does not have, 2026-10-29). Not reachable, nothing listening or another browser is a setup
        problem; bid is the browser id for the message."""
        try:
            args = sh(["ssh", *SSH_KEEPALIVE, "webpc044", "pid=$(lsof -nP -iTCP:9222 -sTCP:LISTEN -t | head -1); "
                       "if [ -n \"$pid\" ]; then ps -o args= -p \"$pid\"; else echo QA_NONE; fi"], 20)
        except subprocess.TimeoutExpired:
            args = ""
        if not args.strip():
            raise Fail(3, "webpc044 is not reachable over SSH: switch it on and check the network", setup=True)
        if args.strip() == "QA_NONE":
            raise Fail(3, f"{QA_CHROME_START} (nothing listens on webpc044's port 9222)", setup=True)
        if not re.search(r"--user-data-dir=\S*/qa-chrome(\s|$)", args) or "--remote-debugging-port=9222" not in args:
            raise Fail(3, f"{QA_CHROME_START} (identity: the browser on webpc044's port 9222 ({bid}) is not the QA "
                          "Chrome, profile ~/qa-chrome)", setup=True)

    def connect(self):
        state = self.tunnel()
        try:
            v = json.load(urllib.request.urlopen(f"http://127.0.0.1:{WEB_TUNNEL_PORT}/json/version", timeout=5))
        except (OSError, ValueError, http.client.HTTPException) as e:
            # nothing answers through the tunnel: the person's when the QA Chrome is not running or webpc044 is away
            self.qa_chrome("no answer")
            raise Fail(3, f"the QA Chrome runs on webpc044, but its port 9222 does not answer through the tunnel: "
                          f"{type(e).__name__}: {e}"[:300]) from None
        bid = v.get("webSocketDebuggerUrl", "").rsplit("/", 1)[-1]
        self.qa_chrome(bid[:8])
        want = ENV_ORIGINS[engine_env()]
        listed = [(p, urllib.request.urlparse(p.get("url", "")).netloc) for p in
                  json.load(urllib.request.urlopen(f"http://127.0.0.1:{WEB_TUNNEL_PORT}/json/list", timeout=5))
                  if p.get("type") == "page"]
        pages = [p for p, host in listed if host == want]
        if len(pages) != 1:
            # F4: no app tab, a tab on another environment, or several tabs: the person opens or closes tabs
            short = want.split(".")[0]
            if not pages:
                other = sorted({host for _, host in listed if host in APP_HOSTS} - {want})
                raise Fail(3, f"Open {short} in the QA Chrome on W044 (expected exactly one page on {want}, found 0"
                              + (f"; WingtraApp is open on {', '.join(other)}" if other else "") + ")", setup=True)
            raise Fail(3, f"Close the extra {short} tabs in the QA Chrome on W044, so that one is left (expected exactly "
                          f"one page on {want}, found {len(pages)})", setup=True)
        # a tab behind another tab is hidden and Chrome throttles it (taps of 7-11 s, 2026-10-27): bring it to the front
        urllib.request.urlopen(f"http://127.0.0.1:{WEB_TUNNEL_PORT}/json/activate/{pages[0]['id']}", timeout=5).read()
        self.sess = Session(pages[0]["webSocketDebuggerUrl"].replace("9222", str(WEB_TUNNEL_PORT)))
        self.origin = want
        end = time.time() + WEB_VISIBLE_S
        while self.sess.eval("document.visibilityState") != "visible":
            if time.time() > end:
                raise Fail(2, "W044: the WingtraCloud tab stays hidden after activation (another tab in front, or the "
                              "window minimised); bring it to the front", device_state="app tab hidden")
            time.sleep(0.1)
        return {"tunnel": state, "browser": v.get("Browser"), "browser_id": bid[:8], "origin": want,
                "path": self.sess.eval("location.pathname")}

    def ready(self):
        """W044's ready: the identity checks (connect brings the app tab to the front), then the app's own dialogs left
        open by a stopped run are closed with Escape (F2, reset_dialogs)."""
        identity = self.connect()
        actions = []
        reset_dialogs(self, self.target, "escape", actions, lambda: {"actions": list(actions)})
        return {"ready": {"actions": actions}, "identity": identity}

    # test files (deliver): a path relative to the home folder on webpc044, macOS's shasum and mv (BSD mv has no
    # -T; ~/qa-fixtures is the engine's own folder), ssh and scp
    SHA_CMD = "shasum -a 256"
    MV_CMD = "mv -n"
    SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]

    def dest(self, item):
        return item["web"]

    def shown(self, path):
        return "~/" + path

    def where(self):
        return "webpc044"

    def reachable(self):
        if sh(["ssh", *self.SSH_OPTS, "webpc044", "echo ok"], DELIVER_CMD_S).strip() != "ok":
            raise Fail(2, "webpc044 is not reachable over SSH: switch it on and check the network")

    def remote(self, cmd, timeout=DELIVER_CMD_S):
        return sh(["ssh", *self.SSH_OPTS, "webpc044", cmd], timeout)

    def copy(self, src, dest):
        sh(["scp", "-q", *self.SSH_OPTS, src, f"webpc044:{dest}"], DELIVER_COPY_S, True)

    def fixture_path(self, name):
        """The absolute path of a delivered test file on webpc044 (~/qa-fixtures/<name>); Fail 2 when it is not there."""
        if not name or not fixtures.NAME.fullmatch(name) or name.endswith(fixtures.PARTIAL):
            raise Fail(4, f"--file must be a test file's plain name, not {name!r}")
        rel_path = f"{fixtures.WEB_DIR}/{name}"
        home = self.remote(f"test -f {shlex.quote(rel_path)} && echo $HOME").strip()
        if not home.startswith("/"):
            raise Fail(2, f"~/{rel_path} is not on webpc044: the case's test file is delivered first (act.py W044 "
                          "deliver <CASE>)")
        return f"{home}/{rel_path}"

    def require_app_focus(self):
        return None

    def tap_css(self, cx, cy):
        for t in ("mouseMoved", "mousePressed", "mouseReleased"):
            p = {"type": t, "x": cx, "y": cy}
            if t != "mouseMoved":
                p.update({"button": "left", "clickCount": 1})
            self.sess.call("Input.dispatchMouseEvent", p)
        return {"css": [cx, cy]}

    def key(self, name):
        keys = {"enter": ("Enter", 13), "tab": ("Tab", 9), "escape": ("Escape", 27), "back": None}
        if keys[name] is None:
            raise Fail(4, "back is a tablet key")
        k, code = keys[name]
        for t in ("keyDown", "keyUp"):
            self.sess.call("Input.dispatchKeyEvent", {"type": t, "key": k, "code": k, "windowsVirtualKeyCode": code})

    def shot(self, path):
        data = base64.b64decode(self.sess.call("Page.captureScreenshot", {"format": "png"})["data"])
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        open(path, "wb").write(data)
        return {"path": path, "sha256": sha256_bytes(data), "bytes": len(data)}

    def cond(self, c):
        if c["kind"] == "focus":
            return True
        return bool(self.sess.eval(COND_JS + f"({json.dumps(c)})"))


# ---------------------------------------------------------------- test files (engine piece 2, 2026-10-26)

DELIVER_ERRORS = (subprocess.TimeoutExpired, OSError)  # adb, ssh or scp hung or could not start


def remote_sha(t, path):
    """sha256 of the file at path on the target; None when nothing is there. A folder or a link there is the person's."""
    q = shlex.quote(path)
    out = t.remote(f"if [ -L {q} ]; then echo QA_LINK; elif [ -d {q} ]; then echo QA_DIR; "
                   f"elif [ -e {q} ]; then {t.SHA_CMD} {q}; else echo QA_MISSING; fi", timeout=DELIVER_SUM_S)
    first = (out.strip().splitlines() or [""])[0]
    if first == "QA_MISSING":
        return None
    if first in ("QA_DIR", "QA_LINK"):
        raise Fail(2, f"a {'folder' if first == 'QA_DIR' else 'link'} is at {t.shown(path)} on {t.where()}, where a test "
                      "file goes: move it away by hand")
    m = re.match(r"([0-9a-f]{64})\s", first + " ")
    if not m:
        raise Fail(3, f"could not read the sha256 of {t.shown(path)} on {t.where()}: {out.strip()[:120] or 'no output'}")
    return m.group(1)


def drop_partial(t, part):
    """Best effort: remove the engine's own partial copy (only ever a name ending in fixtures.PARTIAL)."""
    try:
        t.remote(f"rm -f {shlex.quote(part)}")
    except (Fail, *DELIVER_ERRORS):
        pass


def deliver_one(t, item, check_only, actions):
    """One test file on one target: there already, copied (to a partial name, checked, then moved into place with
    mv -n), or with --check only reported as missing. A different file, folder or link at the path is never
    overwritten. A *.qa-partial file is the engine's own (no fixture may carry that name): it is replaced."""
    dest = t.dest(item)
    row = {"name": item["name"], "dest": t.shown(dest), "sha256": item["sha256"], "bytes": item["size"]}
    have = remote_sha(t, dest)
    if have == item["sha256"]:
        return dict(row, state="already there")
    if have is not None:
        raise Fail(2, f"a different file is already at {t.shown(dest)} on {t.where()} (sha256 {have[:12]}…, the case "
                      f"pins {item['sha256'][:12]}…): the engine never overwrites it; move or rename it by hand")
    if check_only:
        return dict(row, state="missing: the engine copies it at Start")
    part = dest + fixtures.PARTIAL
    remote_sha(t, part)  # a folder or link in the way is the person's
    t.remote(f"mkdir -p {shlex.quote(posixpath.dirname(dest))}")
    try:
        t.copy(item["src"], part)
    except (Fail, *DELIVER_ERRORS):
        drop_partial(t, part)
        raise
    got = remote_sha(t, part)
    if got != item["sha256"]:
        drop_partial(t, part)
        raise Fail(3, f"the copy of {item['name']} on {t.where()} does not match (sha256 {(got or 'none')[:12]}…); "
                      "the partial copy was removed")
    t.remote(f"{t.MV_CMD} {shlex.quote(part)} {shlex.quote(dest)}")
    try:
        final = remote_sha(t, dest)
    except Fail:  # a folder or link appeared at the destination during the copy
        drop_partial(t, part)
        raise
    if final != item["sha256"]:
        drop_partial(t, part)
        if final is None:
            raise Fail(3, f"moving the checked copy of {item['name']} into place on {t.where()} failed; the partial "
                          "copy was removed")
        raise Fail(2, f"another file appeared at {t.shown(dest)} on {t.where()} during the copy; the engine did not "
                      "overwrite it and removed its partial copy")
    actions.append(f"copied {item['name']} to {t.shown(dest)} ({item['size']} bytes, sha256 checked)")
    return dict(row, state="copied")


def load_case_version(cid, case_file):
    """(case, path): the version run.py pins (case_file, a version file of cid), else the newest version."""
    from run import load_case  # the runner's own lookup: the newest version of the case
    if case_file is None:
        return load_case(cid)
    if not re.fullmatch(r"qa/(?:cases|runner/tests/cases)/" + re.escape(cid) + r"(?:\.c\d+)?\.json", case_file):
        raise Fail(4, f"--case-file must be a version file of {cid} under qa/cases/ or qa/runner/tests/cases/")
    path = os.path.join(WORKSPACE, *case_file.split("/"))
    try:
        return json.load(open(path, encoding="utf-8")), path
    except (OSError, ValueError):
        return None, path


def deliver(t, cid, check_only, case_file=None):
    """Engine piece 2 (2026-10-26): put the test files of case cid (its newest version, or the version file run.py
    pins with --case-file) on the target, if the case runs there. Only what the case pins is copied (fixtures.py:
    source folder, name, sha256, destination). The person's (Fail 2): a source that is missing or does not match the
    case, a different file, folder or link at the destination, an unreachable target. Every failure, a timeout too,
    carries what deliver did before it (deliver.actions)."""
    if not cid or not CASE_ID.fullmatch(cid):
        raise Fail(4, "deliver needs a case id such as TERR-004 (capital letters, a dash, three digits)")
    case, path = load_case_version(cid, case_file)
    if not isinstance(case, dict) or case.get("id") != cid:
        raise Fail(4, f"no case {cid}" + (f" in {case_file}" if case_file else ""))
    platform = "web" if isinstance(t, Web) else "tablet"
    if platform not in fixtures.PLATFORMS.get(case.get("targets"), ()):
        raise Fail(4, f"{cid} runs on {case.get('targets')}: {t.where()} gets no test files")
    out = {"case": cid, "version_file": rel(path), "check_only": bool(check_only), "fixtures": [], "actions": []}
    try:
        if case.get("status") == "retired":
            raise Fail(2, f"{cid} is retired: its test files are not delivered")
        try:
            items = fixtures.plan(case, WORKSPACE, platforms=(platform,))
        except fixtures.FixtureError as e:
            raise Fail(2, f"{cid}: {e}") from None
        if items:
            t.reachable()
        for it in items:
            out["fixtures"].append(deliver_one(t, it, check_only, out["actions"]))
    except Fail as e:
        raise Fail(e.code, e.msg, **dict(e.extra, deliver=out)) from None
    except DELIVER_ERRORS as e:
        raise Fail(3, f"{type(e).__name__}: {e}"[:300], deliver=out) from None
    return out


# ---------------------------------------------------------------- main

def wait_all(t, conds, timeout):
    end = time.time() + timeout
    last = None
    while True:
        last = {c["kind"] + ":" + c["value"][:60]: t.cond(c) for c in conds}
        if all(last.values()):
            return last
        if time.time() > end:
            raise Fail(3, "readback: expected change did not happen", expect=last)
        time.sleep(0.25)


def resolve_target(name, profiles):
    if name.upper() == "W044":
        return Web()
    for serial, p in profiles.items():
        if name in (serial, p.get("tag")):
            return Tablet(serial, p)
    raise Fail(4, f"unknown target {name!r} (registered: {', '.join(p['tag'] for p in profiles.values())}, W044)")


# Security review 2026-11-03: the filing Next under a Touch ID approval. The gate lets
# QA_TICKET=<ticket dir> ... tap text:Next --approved pass only for an approval of that draft's title.txt and
# description.txt; the reporter's form must then hold exactly that text, or the approval would carry text the person
# never approved (typed in by hand before the tap).
TICKET_DIR_RE = re.compile(r"^tests/\d{4}-\d{2}-\d{2}-[a-z0-9-]+/tickets/[0-9]+$", re.A)
TICKET_FORM_JS = ("(() => { const t = document.querySelectorAll('input[name=title]'), "
                  "d = document.querySelectorAll('textarea[name=description]'); "
                  "return t.length === 1 && d.length === 1 ? [t[0].value, d[0].value] : null; })()")


def _form_text(s):
    """As the form holds it: CRLF read as LF, trailing newlines dropped ($(cat file) drops them when it is typed)."""
    return s.replace("\r\n", "\n").rstrip("\n")


def ticket_form_check(sess, ticket_dir):
    """Before the filing Next tap of an approved ticket: the reporter's one title field and one description field hold
    the draft's title.txt and description.txt. Fail(2), and no tap, otherwise."""
    if not isinstance(ticket_dir, str) or not TICKET_DIR_RE.match(ticket_dir):
        raise Fail(2, "QA_TICKET must be tests/<yyyy-mm-dd-slug>/tickets/<n>; no tap")
    base = os.path.join(os.path.dirname(QA_DIR), ticket_dir)
    try:
        want = []
        for name in ("title.txt", "description.txt"):
            with open(os.path.join(base, name), "rb") as f:
                want.append(_form_text(f.read().decode("utf-8")))
    except (OSError, UnicodeDecodeError) as e:
        raise Fail(2, f"the approved draft {ticket_dir} cannot be read ({type(e).__name__}); no tap") from None
    got = sess.eval(TICKET_FORM_JS)
    if not isinstance(got, list) or len(got) != 2 or not all(isinstance(v, str) for v in got):
        raise Fail(2, "the reporter's title and description fields were not found (exactly one each); no tap")
    differs = [n for n, g, w in zip(("title", "description"), got, want) if _form_text(g) != w]
    if differs:
        raise Fail(2, f"the reporter's {' and '.join(differs)} differ from the approved draft {ticket_dir}; no tap. "
                      "Type the draft's text exactly, or press File ticket again for new text")
    return {"title": "matches the draft", "description": "matches the draft"}


def check_lock(name):
    """A target the runner has locked takes calls only from that run (QA_RUN_ID); a stale lock file is ignored."""
    path = os.path.join(STATE, "locks", f"{name}.lock")
    if not os.path.exists(path):
        return
    with open(path, "a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_SH | fcntl.LOCK_NB)
            fcntl.flock(f, fcntl.LOCK_UN)
            return
        except BlockingIOError:
            pass
    try:
        holder = json.load(open(os.path.join(STATE, "locks", f"{name}.json"))).get("holder")
    except (OSError, ValueError):
        holder = None
    if holder != os.environ.get("QA_RUN_ID"):
        raise Fail(2, f"{name} is BUSY: locked by {holder}; no device call made")


def run_wifi(t, a, conds):
    """act.py <tablet> wifi off|on: the switch and its readback (Tablet.wifi), then for --until-offline the app's offline
    indicator and then every --expect, all within one --timeout. A device action over USB, not a cloud write: no write
    list and no app page for the switch itself (a page is read only for --until-offline and --expect)."""
    if a.selector not in WIFI_SET:
        raise Fail(4, f"wifi takes off or on, not {a.selector!r}")
    if not isinstance(t, Tablet):
        raise Fail(4, "wifi is for tablets")
    if a.until_offline and a.selector != "off":
        raise Fail(4, "--until-offline goes with wifi off")
    deadline = time.time() + a.timeout
    res = {"wifi": t.wifi(a.selector, deadline)}
    try:
        if a.until_offline or conds:
            res["identity"] = t.connect()
        if a.until_offline:
            try:
                wait_all(t, [{"kind": "js", "value": OFFLINE_JS}], max(0.0, deadline - time.time()))
            except Fail:
                raise Fail(3, f"readback: Wi-Fi is off on {t.tag}, but the app does not show its offline indicator "
                              f"within {a.timeout:g} s") from None
            res["wifi"]["app_offline"] = True
        if conds:
            res["readback"] = wait_all(t, conds, max(0.0, deadline - time.time()))
    except Fail as f:
        f.extra.setdefault("wifi", res["wifi"])  # the switch was made: the record keeps it
        raise
    except (OSError, ValueError, subprocess.TimeoutExpired) as e:  # review 2026-11-03: the page read fell over
        raise Fail(3, f"{type(e).__name__}: {e}"[:300], wifi=res["wifi"]) from e
    return res


def map_fractions(a):
    """(fx, fy) of a map-tap call, checked before any device call: two numbers within MAP_MIN..MAP_MAX, else Fail 4."""
    out = []
    for name, v in (("fx", a.selector), ("fy", a.fy)):
        try:
            f = float(v)
        except (TypeError, ValueError):
            raise Fail(4, f"map-tap takes two fractions of the map, <fx> <fy> (got {name} {v!r})") from None
        if not MAP_MIN <= f <= MAP_MAX:  # NaN fails here too
            raise Fail(4, f"map-tap {name} {v} is outside {MAP_MIN:g}..{MAP_MAX:g}, where the map's own buttons sit; "
                          "no tap")
        out.append(f)
    return tuple(out)


def map_point(t, fx, fy):
    """MAP_JS now: the CSS point at fx, fy of the one visible map canvas. Fail 2 while a dialog is open (no tap through a
    dialog over the map), Fail 3 when the page has no single visible canvas or something else lies on the point (the
    sidebar, a toolbar, a marker, the loading screen)."""
    m = t.sess.eval(MAP_JS + f"({json.dumps(fx)}, {json.dumps(fy)})")
    if not isinstance(m, dict):
        raise Fail(3, "map: the page did not answer the map read")
    if m.get("dialogs"):
        title = m["dialogs"][-1] or "(no title)"
        raise Fail(2, f'a dialog is open over the map ("{title}"); no map tap', device_state=f'dialog open: "{title}"')
    if m.get("canvases") != 1:
        raise Fail(3, f"map: {m.get('canvases', 0)} visible canvas.mapboxgl-canvas on the page, expected exactly one "
                      "(the flight planner's map)")
    if m.get("hit") != "canvas":
        x, y = (round(v) for v in m["point"])
        raise Fail(3, f"covered: map point ({fx:g}, {fy:g}) at CSS ({x}, {y}) is under {m.get('hit')}, not the map")
    return m


def run_map_tap(t, a, conds):
    """act.py <target> map-tap <fx> <fy> [--double] (2026-11-03): a tap, or two MAP_DOUBLE_GAP_S apart, at fractions of
    the planner's map (map_point, measured now and again before the second tap), on a tablet through tap_css (its
    keyboard and re-measure handling), on W044 through the Web click; then every --expect. A map tap only draws: the plan
    reaches the cloud with Save, a write-list control, so no write list and no side_effect here."""
    fx, fy = map_fractions(a)
    m = map_point(t, fx, fy)
    rec = {"fractions": [fx, fy], "canvas": [round(v, 1) for v in m["rect"]],
           "css": [round(v, 1) for v in m["point"]], "taps": 0}
    res = {"map": rec, "action": {"taps": []}}
    try:
        res["action"]["taps"].append(t.tap_css(*m["point"]))
        rec["taps"] = 1
        if a.double:
            sent = time.time()
            m = map_point(t, fx, fy)  # the first tap may have opened something over the point
            time.sleep(max(0.0, MAP_DOUBLE_GAP_S - (time.time() - sent)))
            res["action"]["taps"].append(t.tap_css(*m["point"]))
            rec["taps"] = 2
            rec["gap_s"] = round(time.time() - sent, 2)  # first tap's end to the second's: includes a tablet's adb call
        if conds:
            res["readback"] = wait_all(t, conds, a.timeout)
    except Fail as f:
        f.extra.setdefault("map", rec)  # what was tapped before the failure
        raise
    except (OSError, ValueError, subprocess.TimeoutExpired) as e:  # adb or CDP fell over mid-way
        raise Fail(3, f"{type(e).__name__}: {e}"[:300], map=rec) from e
    return res


def run(a):
    if a.until_offline and a.verb != "wifi":  # review 2026-11-03: never a silent no-op on another verb
        raise Fail(4, "--until-offline goes with wifi off")
    if a.double and a.verb != "map-tap":
        raise Fail(4, "--double goes with map-tap")
    if a.fy is not None and a.verb != "map-tap":
        raise Fail(4, f"{a.verb} takes one argument; two numbers are map-tap's fractions")
    if a.verb == "map-tap":
        map_fractions(a)  # Fail 4 before any device call
    if a.timeout is None:
        a.timeout = WIFI_TIMEOUT_S if a.verb == "wifi" else READBACK_TIMEOUT_S
    runner = os.environ.get("QA_RUNNER") == "1"
    wl = write_list_request(a, runner)
    if wl and runner:
        raise Fail(2, f"{wl}: refused under the runner, no device call made; hand back to Claude to run with your OK",
                   command=hand_back(a))
    if wl and not a.approved:
        raise Fail(2, f"{wl}: re-run with --approved (asks you first); no device call made")
    profiles = load_profiles()
    t = resolve_target(a.target, profiles)
    check_lock("webpc044" if isinstance(t, Web) else t.serial)
    tname = a.target
    res = {"target": tname, "verb": a.verb}
    if a.verb == "ready":
        # before connect: connect is what fails on an asleep or covered tablet
        res.update(t.ready())
        return res
    if a.verb == "deliver":
        # no page needed: the copy goes over adb or ssh
        res["deliver"] = deliver(t, a.selector, a.check, a.case_file)
        return res
    conds = [parse_cond(x) for x in a.expect or []]
    if a.verb == "wifi":
        res.update(run_wifi(t, a, conds))
        return res
    if a.verb == "sys-tap" and isinstance(t, Tablet) and conds and all(c["kind"] == "focus" for c in conds):
        # engine piece 3: a full-screen system window (the file browser) pauses the app's page, so no connect;
        # the tap goes only to a system window, and the readback is the window in front. @storage is the internal
        # storage's name in the file browser: the tablet's device name (the Downloads place lists only files the media
        # index marks as downloads, which a delivered file is not)
        text = t.adb("shell", "settings get global device_name").strip() if a.selector == "@storage" else a.selector
        if not text or text == "null":
            raise Fail(3, "the tablet has no device name, so its storage cannot be found in the file browser")
        res["action"] = t.sys_tap(text, system_only=True)
        res["readback"] = wait_all(t, conds, a.timeout)
        return res
    res["identity"] = t.connect()
    conds = [parse_cond(x) for x in a.expect or []]
    if a.verb == "probe":
        if isinstance(t, Tablet):
            res["focus"] = t.focus()[0]
        return res
    if a.verb == "read":
        # read-only: one page expression, its value returned (never an Input call)
        res["value"] = t.sess.eval(a.selector)
        return res
    if a.verb == "wait":
        res["readback"] = wait_all(t, conds, a.timeout)
        return res
    if a.verb == "shot":
        res["shot"] = t.shot(a.selector)
        return res
    if a.verb == "find":
        res["element"] = find_one(t.sess, a.selector, a.nth)
        return res
    if a.verb in ("tap", "type", "key", "sys-tap", "choose-file") and not conds and a.verb != "type":
        raise Fail(4, f"{a.verb} needs at least one --expect (the readback)")
    if a.verb == "sys-tap":
        if not isinstance(t, Tablet):
            raise Fail(4, "sys-tap is for tablets")
        res["action"] = t.sys_tap(a.selector)
        res["readback"] = wait_all(t, conds, a.timeout)
        return res
    if a.verb == "key":
        t.require_app_focus()
        t.key(a.selector)
        res["readback"] = wait_all(t, conds, a.timeout)
        return res
    if a.verb == "map-tap":
        t.require_app_focus()
        res.update(run_map_tap(t, a, conds))
        return res
    t.require_app_focus()
    open_update = t.sess.eval(UPDATE_DIALOG_JS + f"({json.dumps(UPDATE_DIALOG_TITLES)})")
    el = find_waiting(t.sess, a.selector, a.nth, a.find_timeout)
    if open_update and not a.approved and norm_label(el.get("label") or el.get("text")) not in UPDATE_DIALOG_DECLINE:
        raise Fail(2, f"an update dialog is open ('{open_update}'); no tap until it is handled", device_state=open_update)
    res["element"] = {k: el[k] for k in ("tag", "text", "testid", "label", "inDialog", "cx", "cy")}
    if el.get("scrolled"):
        res["element"]["scrolled_into_view"] = el["scrolled"]
    wl = is_write_list(el, a.selector, el.get("dialogTitle", ""))
    if runner and standing_flight_save(a):
        # 2026-11-03: run.py's standing flight save taps only one of the planner's save controls, else as before; and
        # (review 2026-11-03) only in the planner of a flight in the site this run created, read right before the tap
        wl = flight_save_control(el, a.selector, el.get("dialogTitle", ""))
        if not wl:
            res["own_site"] = own_site_planner(t, a)
    if wl:
        # second line: the element found is a write-list control the selector did not name (reads only so far)
        if runner:
            raise Fail(2, f"{wl}: refused under the runner; hand back to Claude to run with your OK", command=hand_back(a))
        if not a.approved:
            raise Fail(2, f"{wl}: re-run with --approved (asks you first)")
    if a.verb == "choose-file":
        # engine piece 4 (2026-10-26): Chrome's file chooser is intercepted, so no macOS dialog opens; the input it
        # belongs to gets the delivered test file
        if not isinstance(t, Web):
            raise Fail(4, "choose-file is for W044; a tablet picks with sys-tap steps in the file browser")
        if cloud_write(el):
            raise Fail(2, f"choose-file only clicks a control that opens a file chooser, not {cloud_write(el)}")
        path = t.fixture_path(a.file)
        t.sess.call("Page.enable")
        t.sess.call("Page.setInterceptFileChooserDialog", {"enabled": True})
        try:
            res["action"] = t.tap_css(el["cx"], el["cy"])
            chooser = t.sess.wait_event("Page.fileChooserOpened", 10)
            t.sess.call("DOM.setFileInputFiles", {"files": [path], "backendNodeId": chooser["backendNodeId"]})
        finally:
            t.sess.call("Page.setInterceptFileChooserDialog", {"enabled": False})
        res["file"] = {"path": path, "mode": chooser.get("mode")}
        res["readback"] = wait_all(t, conds, a.timeout)
        return res
    if a.verb == "tap":
        if os.environ.get("QA_TICKET") is not None and a.selector == "text:Next":
            res["ticket_form"] = ticket_form_check(t.sess, os.environ["QA_TICKET"])  # security review 2026-11-03
        cloud = cloud_write(el, gated=os.environ.get("QA_CLOUD_WRITE") == "1")
        if cloud:
            # G3: a tap on a cloud-write control leaves a side_effect record (written in main, after the ledger)
            res["cloud_write"] = {"control": cloud, "route": t.sess.eval("location.pathname")}
        try:
            res["action"] = t.tap_css(el["cx"], el["cy"])
        except Fail:
            raise  # refused before any input was sent (no WebView origin, tap outside it)
        except Exception as e:  # noqa: BLE001 (adb or CDP fell over while sending: the tap may have landed)
            if not cloud:
                raise
            raise Fail(3, f"{type(e).__name__}: {e}"[:300],
                       cloud_write=dict(res["cloud_write"], outcome="unknown (the tap may have been sent)")) from e
        try:
            res["readback"] = wait_all(t, conds, a.timeout)
        except Fail as f:
            if cloud:
                f.extra["cloud_write"] = res["cloud_write"]  # the tap was made; the side effect is still recorded
            raise
        except Exception as e:  # noqa: BLE001 (the readback connection failed after the tap)
            if not cloud:
                raise
            raise Fail(3, f"{type(e).__name__}: {e}"[:300], cloud_write=res["cloud_write"]) from e
        return res
    if a.verb == "type":
        if a.text_from is not None:
            if a.text is not None or a.secret:
                raise Fail(4, "type needs exactly one of --text, --text-from or --secret")
            a.text = read_ticket_text(a.text_from)
        if bool(a.text is not None) == bool(a.secret):
            raise Fail(4, "type needs exactly one of --text, --text-from or --secret")
        res["action"] = t.tap_css(el["cx"], el["cy"])
        focused = wait_all(t, [{"kind": "js", "value": "document.activeElement && ['INPUT','TEXTAREA'].includes(document.activeElement.tagName)"}], 5)
        # select what the field already holds, so the inserted text replaces it (a selection is not a value change)
        t.sess.eval("document.activeElement.select && document.activeElement.select()")
        if a.secret:
            if t.origin not in SECRET_ORIGINS:
                raise Fail(2, f"secrets are typed only on {sorted(SECRET_ORIGINS)}, not {t.origin}")
            val = None
            for line in open(os.path.join(QA_DIR, "secrets.env"), encoding="utf-8"):
                if line.strip().startswith(a.secret + "="):
                    val = line.split("=", 1)[1].strip()
            if not val:
                raise Fail(2, f"{a.secret} is not in qa/secrets.env")
            t.sess.call("Input.insertText", {"text": val})
            b64 = base64.b64encode(val.encode()).decode()
            ok = t.sess.eval(f"document.activeElement.value === new TextDecoder().decode(Uint8Array.from(atob('{b64}'), c => c.charCodeAt(0)))")
            res["readback"] = {"value_matches": bool(ok), "secret": a.secret}
            if not ok:
                raise Fail(3, "readback: the field does not hold the secret", readback=res["readback"])
        else:
            t.sess.call("Input.insertText", {"text": a.text})
            ok = t.sess.eval(f"document.activeElement.value === {json.dumps(a.text)}")
            res["readback"] = {"value_matches": bool(ok)}
            if not ok:
                raise Fail(3, "readback: the field does not hold the typed text")
        if conds:
            res["readback"].update(wait_all(t, conds, a.timeout))
        res["focused"] = focused
        return res
    raise Fail(4, f"unknown verb {a.verb}")


def parser():
    # allow_abbrev=False: argparse would otherwise take --a or --appro as --approved, past the gate (G2, 2026-10-23)
    ap = argparse.ArgumentParser(prog="act.py", add_help=True, allow_abbrev=False)
    ap.add_argument("target")
    ap.add_argument("verb", choices=["probe", "ready", "deliver", "read", "find", "tap", "type", "key", "sys-tap",
                                     "choose-file", "shot", "wait", "wifi", "map-tap"])
    ap.add_argument("selector", nargs="?")
    ap.add_argument("fy", nargs="?", help="map-tap: the second fraction <fy> (the first, <fx>, takes the selector's place)")
    ap.add_argument("--double", action="store_true", help=f"map-tap: tap the point twice, {MAP_DOUBLE_GAP_S:g} s apart")
    ap.add_argument("--expect", action="append")
    ap.add_argument("--timeout", type=float, default=None,
                    help=f"seconds for the readback (default {READBACK_TIMEOUT_S:g}; wifi {WIFI_TIMEOUT_S:g}, for all its waits)")
    ap.add_argument("--until-offline", action="store_true",
                    help="wifi off: also wait until the app shows its offline indicator")
    ap.add_argument("--find-timeout", type=float, default=10.0, help="seconds to wait for the element before a tap or type")
    ap.add_argument("--text")
    ap.add_argument("--text-from", help="type: a ticket draft's title.txt or description.txt (tests/<campaign>/tickets/<n>/)")
    ap.add_argument("--secret")
    ap.add_argument("--nth", type=int)
    ap.add_argument("--approved", action="store_true")
    ap.add_argument("--check", action="store_true", help="deliver: only read whether the test files are there")
    ap.add_argument("--file", help="choose-file: the test file's name in ~/qa-fixtures on webpc044")
    ap.add_argument("--case-file", help="deliver: the case version file to use (run.py pins its own), not the newest")
    return ap


def main(argv):
    a = parser().parse_args(argv)
    rest = [x for x in argv[2:] if x != a.text]  # never ledger typed text
    try:
        res = run(a)
        res["ok"] = True
        rc = 0
    except Fail as f:
        res = {"target": a.target, "verb": a.verb, "ok": False, "code": f.code, "error": f.msg, **f.extra}
        rc = f.code
    except (OSError, ValueError, subprocess.TimeoutExpired) as e:
        res = {"target": a.target, "verb": a.verb, "ok": False, "code": 3, "error": f"{type(e).__name__}: {e}"[:300]}
        rc = 3
    ev = ledger(a.target, a.verb, rest, rc, res)
    if ev:
        res["ledger"] = {"day": ev["t"][:10], "seq": ev["seq"]}  # added after hashing: out_sha256 covers the rest
    cw = res.get("cloud_write")
    if cw and ev:
        campaign = os.environ.get("QA_CAMPAIGN") or f"{ev['t'][:10]}-side-effects"
        try:
            res["side_effect"] = side_effect(campaign, {
                "target": a.target, "what": side_effect_what(cw["control"]),
                "object": {"control": cw["control"], "route": redact(str(cw.get("route") or ""))},
                "account": engine_account(),
                "ledger": res["ledger"], "observed_by": "act.py",
                "outcome": cw.get("outcome") or ("ok" if rc == 0 else "tapped, readback failed"),
                "run": os.environ.get("QA_HANDBACK_RUN") or os.environ.get("QA_RUN_ID"), "reverted_by": None},
                session=os.environ.get("QA_RUN_ID", "-"))
        except (OSError, ValueError) as e:
            res["side_effect_error"] = f"{type(e).__name__}: {e}"[:200]
    print(json.dumps(res, indent=1))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
