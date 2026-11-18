"""New test's write guard (2026-11-02): a plan case may write only through import, create, process, Optimize, Next and
Continue, and since 2026-11-03 the "Enable offline use" toggle and saving a site the case names QA_; every other write
control is refused before Run, whatever the step's gates say."""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(HERE)
QA_DIR = os.path.dirname(APP_DIR)  # this copy's qa folder (qa, or a staging copy), never another one
sys.path[:0] = [APP_DIR, os.path.join(QA_DIR, "runner")]
import run  # noqa: E402
import worker  # noqa: E402

NAME = "act.py {target} type testid:input-site-name --text QA_OFFL-001_{run} --expect x"
SAVE_NEW = "act.py {target} tap 'text:Save new site' --expect x --timeout 30"


def step(helper, gates=(), n=5):
    return {"n": n, "text": "x", "helper": helper, "gates": list(gates)}


def site_case(name=NAME, listed=(4,), extra=()):
    """A site-creation case: New site, the name, the offline toggle, Save new site (step 4), then the given steps."""
    steps = [step("act.py {target} tap testid:btn-new-site --expect x", n=1),
             step(name, n=2),
             step("act.py {target} tap 'text:Enable offline use' --expect x", n=3),
             step(SAVE_NEW, ["cloud_write"], n=4)] + list(extra)
    return {"id": "OFFL-001", "steps": steps,
            "cloud_writes": [{"step": n, "what": "creates the site QA_OFFL-001_<run>", "object": "site"}
                             for n in listed]}


def problems(case):
    return [p for p in (worker.new_test_write_problem(s, run, case) for s in case["steps"]) if p]


class WriteGuardTests(unittest.TestCase):
    def test_the_example_cases_pass(self):
        for name in ("PLAN-001.c3", "PLAN-002", "TERR-004.c8", "TERR-005.c5", "UPG-013.c2"):
            with open(os.path.join(QA_DIR, "cases", name + ".json")) as f:
                case = json.load(f)
            self.assertEqual(problems(case), [], name)

    def test_allowed_writes(self):
        for h, g in (("act.py {target} tap testid:import-flight-dialog-submit-button --expect x", ["cloud_write"]),
                     ("act.py {target} tap testid:btn-wrapper-magic-planning-entry-point", ["cloud_write"]),
                     ("act.py {target} tap text:Next --expect x", ["write_list", "cloud_write"]),
                     ("act.py {target} tap 'text:Process' --expect x", ["cloud_write"])):
            self.assertIsNone(worker.new_test_write_problem(step(h, g), run), h)

    def test_reads_and_plain_taps_pass(self):
        self.assertIsNone(worker.new_test_write_problem(step("act.py {target} tap testid:btn-sites --expect x"), run))
        self.assertIsNone(worker.new_test_write_problem({"n": 2, "text": "r", "read_js": "1", "gates": []}, run))

    def test_the_offline_use_toggle_is_allowed_with_or_without_a_gate(self):
        for h in ("act.py {target} tap 'text:Enable offline use' --expect x",
                  'act.py {target} tap "text:Enable  offline USE"'):    # matched as act.py does: spacing and case
            for g in ([], ["cloud_write"]):
                self.assertIsNone(worker.new_test_write_problem(step(h, g), run), (h, g))

    def test_the_download_from_the_site_menu_is_allowed(self):
        # the site card's ... menu, then its toggle: the download starts at once (no save, no dialog)
        case = {"id": "OFFL-003", "steps": [
            step("act.py {target} tap testid:btn-sites --expect x", n=1),
            step("act.py {target} tap testid:elipsis-menu-handler --expect 'sel:text:Enable offline use'", n=2),
            step("act.py {target} tap 'text:Enable offline use' --expect x", ["cloud_write"], n=3),
            step("act.py {target} wifi off", n=4), step("act.py {target} wifi on", n=5)]}
        self.assertEqual(problems(case), [])

    def test_a_qa_site_save_is_allowed(self):
        self.assertEqual(problems(site_case()), [])
        # the label as act.py matches it (spacing and case), and by aria
        for save in ("act.py {target} tap 'text:Save  new SITE' --expect x", "act.py {target} tap 'aria:Save new site'"):
            case = site_case()
            case["steps"][3] = step(save, ["cloud_write"], n=4)
            self.assertEqual(problems(case), [], save)
        # --text=QA_... and a name typed in a step listed out of order
        case = site_case(name="act.py {target} type testid:input-site-name --text=QA_x")
        case["steps"].reverse()
        self.assertEqual(problems(case), [])

    def test_the_save_test_id_is_refused(self):
        # review 2026-11-03: btn-save-site-<loading> is the same button on Site details, where a typed QA_ name would
        # rename the open site: the label tells a new site from a rewrite, so the guard takes only the label
        for tid in ("testid:btn-save-site-false", "testid:btn-save-site-true", "testid:btn-wrapper-btn-save-site-false"):
            case = site_case()
            case["steps"][3] = step(f"act.py {{target}} tap {tid} --expect x", ["cloud_write"], n=4)
            got = problems(case)
            self.assertEqual(len(got), 1, (tid, got))
            self.assertIn(f"step 4 saves a site through {tid}, the same test id for a new site and for Site details: "
                          "tap it by its label, text:Save new site or text:Save site", got[0])

    def test_the_download_from_site_details_of_the_qa_site_is_allowed(self):
        # the QA_ site the case created: Site details, the toggle, Next to the last step, Save site (listed)
        case = site_case(listed=(4, 9), extra=[
            step("act.py {target} tap testid:btn-site-settings --expect x", n=5),
            step("act.py {target} tap 'text:Enable offline use' --expect x", n=6),
            step("act.py {target} tap testid:btn-sitecreation-next --expect x", n=7),
            step("act.py {target} tap text:Next --expect x", ["write_list"], n=8),
            step("act.py {target} tap 'text:Save site' --expect x", ["cloud_write"], n=9)])
        self.assertEqual(problems(case), [])

    def test_a_site_save_without_a_qa_name_typed_is_refused(self):
        other = "act.py {target} type testid:input-site-name --text 'Offline site' --expect x"
        bad = {
            "no case": None,
            "no name typed": dict(site_case(), steps=[s for s in site_case()["steps"] if s["n"] != 2]),
            "another name": site_case(name=other),
            "QA_ not first": site_case(name="act.py {target} type testid:input-site-name --text x_QA_1"),
            "lower case qa_": site_case(name="act.py {target} type testid:input-site-name --text qa_1"),
            "another field": site_case(name="act.py {target} type testid:input-flight-name --text QA_1"),
            "a secret": site_case(name="act.py {target} type testid:input-site-name --secret QA_NAME"),
            "a draft file": site_case(name="act.py {target} type testid:input-site-name --text-from QA_/title.txt"),
            "not in cloud_writes": site_case(listed=()),
            "listed under another step": site_case(listed=(3,)),
            # review 2026-11-03: act.py types the last of several --text values, so one QA_ value among others is not
            # the name typed
            "two --text values": site_case(name="act.py {target} type testid:input-site-name --text QA_1 --text 'Real'"),
            "--text= twice": site_case(name="act.py {target} type testid:input-site-name --text=QA_1 --text=Real"),
            "--text and --text-from": site_case(name="act.py {target} type testid:input-site-name --text QA_1 "
                                                      "--text-from x/title.txt"),
        }
        late = site_case()  # the name typed after the save
        late["steps"][1]["n"] = 6
        bad["typed after the save"] = late
        retyped = site_case()  # QA_ first, then another name before the save
        retyped["steps"][2] = step(other, n=3)
        bad["renamed before the save"] = retyped
        # review 2026-11-03: a name step that may not run before the save (skipped, replaced by JS, other platform)
        for extra in ({"when_js": "false"}, {"read_js": "1"}, {"on": "web"}):
            c = site_case()
            c["steps"][1].update(extra)
            bad[f"the name step with {extra}"] = c
        for why, case in bad.items():
            s = step(SAVE_NEW, ["cloud_write"], n=4)
            got = worker.new_test_write_problem(s, run, case)
            self.assertIsNotNone(got, why)
            self.assertIn("step 4 saves a site through text:Save new site, which never runs from New test", got)
            self.assertIn("type a name starting with QA_ into testid:input-site-name (act.py type --text, once)", got)
            self.assertIn("list step 4 in cloud_writes", got)

    def test_save_site_runs_only_on_the_site_the_case_created(self):
        # review 2026-11-03: Save site rewrites the open site (useSiteSaving.ts:62-104). A QA_ name typed into Site details
        # of an existing site, then Save site, would rename that site (the QA engine site or any other): refused
        save_site = "act.py {target} tap 'text:Save site' --expect x"
        details = [step("act.py {target} tap testid:btn-site-settings --expect x", n=1),
                   step("act.py {target} tap 'text:Enable offline use' --expect x", n=2)]
        rename = {"steps": details + [step(NAME, n=3), step(save_site, ["cloud_write"], n=4)],
                  "cloud_writes": [{"step": 4}]}
        bad = {
            "a QA_ name typed on an existing site": rename,
            "no name, no site created (the QA engine site)": {"steps": details + [step(save_site, ["cloud_write"], n=3)],
                                                               "cloud_writes": [{"step": 3}]},
            "no case": None,
        }
        for g in (["cloud_write"], []):
            bad[f"rename, gates {g}"] = dict(rename, steps=rename["steps"][:3] + [step(save_site, g, n=4)])
        own = lambda extra, listed=(4, 7): site_case(listed=listed, extra=[  # noqa: E731
            step("act.py {target} tap testid:btn-site-settings --expect x", n=5), *extra,
            step(save_site, ["cloud_write"], n=7)])
        bad["typed after the creation"] = own([step(NAME.replace("QA_OFFL", "QA_RENAMED"), n=6)])
        bad["typed by its label after the creation"] = own([step(
            "act.py {target} type 'text:Site name' --text 'Customer site'", n=6)])
        bad["Save site not listed"] = own([step("act.py {target} tap 'text:Enable offline use'", n=6)], listed=(4,))
        bad["the creation not allowed (not QA_)"] = own([step("act.py {target} tap 'text:Enable offline use'", n=6)])
        bad["the creation not allowed (not QA_)"]["steps"][1] = step(
            "act.py {target} type testid:input-site-name --text 'Offline site'", n=2)
        bad["a test-id creation"] = own([step("act.py {target} tap 'text:Enable offline use'", n=6)])
        bad["a test-id creation"]["steps"][3] = step("act.py {target} tap testid:btn-save-site-false", ["cloud_write"], n=4)
        for why, case in bad.items():
            s = (case or rename)["steps"][-1]
            got = worker.new_test_write_problem(s, run, case)
            self.assertIsNotNone(got, why)
            self.assertIn(f"step {s['n']} saves a site through text:Save site, which rewrites the open site: from New "
                          "test only on the site the case created itself", got, why)
            self.assertIn("never on the QA engine site or another existing site", got, why)
        # the case's own site: taps only between the creation and Save site
        self.assertEqual(problems(own([step("act.py {target} tap 'text:Enable offline use'", n=6)])), [])

    def test_labels_by_aria_or_sys_tap_are_still_controls(self):
        # review 2026-11-03: aria: and sys-tap name a control by its label, as act.py's write list and run.step_selectors
        # read them; before, the guard looked only at text: and testid:, so these passed without a gate
        for h in ("act.py {target} tap aria:Archive", "act.py {target} tap 'aria:Add drone'",
                  "act.py {target} sys-tap Archive", "act.py {target} sys-tap 'Delete from flight'"):
            why = worker.new_test_write_problem(step(h), run)
            self.assertIsNotNone(why, h)
            self.assertIn("never runs from New test (only import", why)
        # the file picker's sys-tap steps stay plain taps
        self.assertIsNone(worker.new_test_write_problem(step("act.py {target} sys-tap @storage --expect focus:x"), run))
        self.assertIsNone(worker.new_test_write_problem(step("act.py {target} sys-tap dsm.tif --expect focus:x"), run))

    def test_destructive_or_outside_writes_are_refused_whatever_the_gates(self):
        for h, g in (("act.py {target} tap 'text:Delete from flight'", ["cloud_write"]),
                     ("act.py {target} tap 'text:Delete from flight'", []),       # no gate: still a G3 control
                     ("act.py {target} tap text:Delete", ["cloud_write"]),        # the site card's ... menu
                     ("act.py {target} tap text:Archive", []),                    # SiteCard.tsx:82-86
                     ("act.py {target} tap testid:inviteBtn", ["cloud_write"]),
                     ("act.py {target} tap 'text:Add drone'", ["cloud_write"]),
                     ("act.py {target} tap text:Send", ["cloud_write"]),        # text:Save: FlightSaveGuardTests
                     ("act.py {target} tap css:.danger", ["cloud_write"]),        # an unnamed control
                     ("act.py {target} key enter", ["cloud_write"])):
            # the same in a case that names a QA_ site: that allows only the site save
            for case in (None, site_case()):
                why = worker.new_test_write_problem(step(h, g), run, case)
                self.assertIsNotNone(why, h)
                self.assertIn("never runs from New test (only import, create, process, Optimize, Next, Continue, "
                              "offline use and QA_ site creation)", why)


SAVE = "act.py {target} tap text:Save --expect x"                       # the planner's tool button (tool-button)
SAVE_EXIT = "act.py {target} tap 'text:Save & exit' --expect x"         # Flight overview (btn-save-and-exit)
SAVE_DRAFT = "act.py {target} tap 'text:Save as draft' --expect x"      # the exit dialog (Blocker.tsx:150)
CREATE = "act.py {target} tap text:Create --expect x"                   # the New Flight form (NewFlightForm.tsx:230-232)
SAVE_GATES = ["write_list", "cloud_write"]


def flight_case(between=(), save=SAVE_EXIT, gates=SAVE_GATES, create=True, listed=True):
    """site_case (the QA_ site saved in step 4), then New flight (5), the flight name (6), Create (7), the given steps,
    the planner's Save and the given save (the last step). cloud_writes lists the site save, Create and both saves
    (listed=False leaves out the last one)."""
    case = site_case(extra=[step("act.py {target} tap testid:btn-new-flight --expect x"),
                            step("act.py {target} type testid:new-flight-name-input --text QA_flight --expect x")]
                     + ([step(CREATE, ["cloud_write"])] if create else []) + [dict(s) for s in between]
                     + [step(SAVE, SAVE_GATES), step(save, gates)])
    for n, s in enumerate(case["steps"], 1):
        s["n"] = n
    last = len(case["steps"])
    writes = [4, last - 1] + ([7] if create else []) + ([last] if listed else [])
    case["cloud_writes"] = [{"step": n, "what": "x", "object": "y"} for n in writes]
    return case


DRAW = [step("act.py {target} tap testid:btn-add-area --expect x"),   # stand-ins for the drawing steps (job 1)
        step("act.py {target} type testid:input-altitude --text 120 --expect x")]


class FlightSaveGuardTests(unittest.TestCase):
    """2026-11-03 (the person's OK): a flight the case created in the QA_ site it created itself may be saved, by label;
    nothing else."""

    def save_problem(self, case):
        return worker.new_test_write_problem(case["steps"][-1], run, case)

    def test_own_site_flight_save_is_allowed(self):
        for save in (SAVE_EXIT, SAVE, SAVE_DRAFT, "act.py {target} tap 'text:Save  &  EXIT' --expect x"):
            self.assertEqual(problems(flight_case(between=DRAW, save=save)), [], save)
        self.assertEqual(problems(flight_case()), [])  # nothing between Create and Save
        # a Next with offline use on, a wait, a read between: still the case's own flight
        case = flight_case(between=[step("act.py {target} tap 'text:Enable offline use' --expect x"),
                                    step("act.py {target} wait --expect 'text:Site available offline'"),
                                    {"n": 0, "text": "r", "read_js": "1", "gates": []}])
        self.assertEqual(problems(case), [])

    def test_refused_without_the_own_site(self):
        no_site = flight_case()
        no_site["steps"] = [s for s in no_site["steps"] if s["n"] != 4]  # the flight in the open (QA engine) site
        not_qa = flight_case()
        not_qa["steps"][1] = step("act.py {target} type testid:input-site-name --text 'Offline site'", n=2)
        by_tid = flight_case()
        by_tid["steps"][3] = step("act.py {target} tap testid:btn-save-site-false", ["cloud_write"], n=4)
        site_unlisted = flight_case()
        site_unlisted["cloud_writes"] = [w for w in site_unlisted["cloud_writes"] if w["step"] != 4]
        for why, case in {"no site created": no_site, "a site not named QA_": not_qa, "site saved by test id": by_tid,
                          "the site save not listed": site_unlisted, "no case": None}.items():
            got = worker.new_test_write_problem((case or flight_case())["steps"][-1], run, case)
            self.assertIsNotNone(got, why)
            self.assertIn("saves a flight through text:Save & exit, which from New test runs only for a flight the "
                          "case created in the QA_ site it created itself", got, why)
            self.assertIn("Never another flight, the QA engine site or another existing site", got, why)

    def test_refused_without_create(self):
        before = flight_case(create=False)  # Create before the site save: the flight is in the site open then
        before["steps"].insert(0, step(CREATE, ["cloud_write"], n=0))
        before["cloud_writes"].append({"step": 0})
        dup = flight_case(create=False, between=[step("act.py {target} tap testid:btn-create-duplicate-flight",
                                                      ["cloud_write"])])
        for why, case in {"no Create": flight_case(create=False), "Create before the site save": before,
                          "a duplicate by test id": dup}.items():
            self.assertIsNotNone(self.save_problem(case), why)

    def test_refused_when_not_listed_or_not_gated_or_not_by_text(self):
        for why, case in {"not in cloud_writes": flight_case(listed=False),
                          "cloud_write only": flight_case(gates=["cloud_write"]),
                          "write_list only": flight_case(gates=["write_list"]),
                          "no gate": flight_case(gates=[]),
                          "aria:": flight_case(save="act.py {target} tap 'aria:Save & exit' --expect x")}.items():
            got = self.save_problem(case)
            self.assertIsNotNone(got, why)
            self.assertIn("tapped by text:, gates write_list and cloud_write", got, why)

    def test_refused_when_the_flight_may_go_to_another_site(self):
        # the New Flight form's site field, or the planner's Edit flight dialog (its Confirm sets the site the save moves
        # the flight to, useFlightPlanMenu.ts:184-195)
        for between in ([step("act.py {target} tap testid:new-flight-site-input --expect x"),
                         step("act.py {target} tap 'text:QA engine' --expect x")],
                        [step("act.py {target} tap text:Confirm --expect x")],
                        [step("act.py {target} tap 'aria:Confirm'")]):
            self.assertIsNotNone(self.save_problem(flight_case(between=between)), between[0]["helper"])
        site_field_first = flight_case()  # the site field before Create
        site_field_first["steps"][5] = step("act.py {target} tap testid:new-flight-site-input --expect x", n=6)
        self.assertIsNotNone(self.save_problem(site_field_first))

    def test_review_confirm_by_any_selector_or_verb(self):
        # review 2026-11-03: the Edit flight Confirm has no test id of its own, so the shared Button renders "none" and
        # "btn-wrapper-none" (Button.tsx:65, :246-248); sys-tap and type reach it by its text too
        for h in ("act.py {target} sys-tap Confirm --expect x", "act.py {target} tap testid:btn-wrapper-none --nth 1",
                  "act.py {target} tap testid:none --nth 0 --expect x", "act.py {target} type text:Confirm --text x"):
            self.assertIsNotNone(self.save_problem(flight_case(between=[step(h)])), h)

    def test_review_site_save_and_create_must_run_with_the_save(self):
        # review 2026-11-03: a step skipped by when_js, replaced by read_js or on the other platform never ran before
        # the save, so the flight on screen is not the case's own
        for n, why in ((4, "Save new site"), (7, "Create")):
            for extra in ({"when_js": "false"}, {"read_js": "1"}, {"on": "web"}):
                case = flight_case()
                case["steps"][n - 1].update(extra)
                self.assertIsNotNone(self.save_problem(case), (why, extra))
        on_tablet = flight_case()  # positives on both platforms, the saves on the tablet only: allowed
        for s in on_tablet["steps"][-2:]:
            s["on"] = "tablet"
        self.assertEqual(problems(on_tablet), [])
        both_tablet = flight_case()  # everything on the tablet: allowed
        for s in both_tablet["steps"]:
            s["on"] = "tablet"
        self.assertEqual(problems(both_tablet), [])

    def test_save_test_ids_and_other_writes_stay_refused(self):
        for tid in ("testid:btn-save-and-exit", "testid:btn-wrapper-btn-save-and-exit",
                    "testid:btn-save-with-missing-components"):
            got = self.save_problem(flight_case(save=f"act.py {{target}} tap {tid} --expect x"))
            self.assertIsNotNone(got, tid)
            self.assertIn("from New test tap it by its label (text:Save & exit, text:Save)", got, tid)
        for h in ("act.py {target} tap text:Delete --expect x", "act.py {target} tap text:Share --expect x",
                  "act.py {target} tap text:Rename --expect x", "act.py {target} tap testid:sidebar-btn-share-flight",
                  "act.py {target} tap 'text:Sync & save' --expect x", "act.py {target} key enter"):
            got = self.save_problem(flight_case(save=h))
            self.assertIsNotNone(got, h)
            self.assertIn("never runs from New test (only import", got, h)
        # Save site in the planner's case is still only the site's own rule (no type step since the site save)
        self.assertIsNotNone(self.save_problem(flight_case(save="act.py {target} tap 'text:Save site' --expect x",
                                                           gates=["cloud_write"])))


class ConfirmGuardTests(unittest.TestCase):
    """2026-11-05 (tests/2026-11-05-wingmate-debug/FINDINGS.md #5): a Confirm tap is a cloud write (the existing-map
    Confirm adds or removes the flight's terrain, the Edit flight Confirm moves the flight to another site), refused
    from New test whatever the case's gates say. Before, an ungated Confirm passed and a declared one was refused."""

    def test_a_confirm_tap_is_refused_with_or_without_gates(self):
        for helper in ("act.py {target} tap text:Confirm --expect x", "act.py {target} tap 'aria:Confirm'",
                       "act.py {target} tap 'text: confirm ' --expect x", "act.py {target} sys-tap Confirm --expect x",
                       "act.py {target} tap testid:none --nth 0 --expect x",
                       "act.py {target} tap testid:btn-wrapper-none --nth 1"):
            for gates in ([], ["cloud_write"], ["write_list", "cloud_write"]):
                got = worker.new_test_write_problem(step(helper, gates, n=63), run, site_case())
                self.assertIsNotNone(got, (helper, gates))
                self.assertIn("a Confirm, which writes to the cloud", got, (helper, gates))
                self.assertIn("never runs from New test, whatever the gates say", got, (helper, gates))
        self.assertNotIn("confirm", run.G3_TEXT, "act.py (the frozen engine) still does not list it")

    def test_looking_at_a_confirm_is_no_write(self):
        for helper in ("act.py {target} wait --expect text:Confirm", "act.py {target} find text:Confirm",
                       "act.py {target} tap 'text:Confirm site name'"):
            self.assertIsNone(worker.new_test_write_problem(step(helper, n=9), run, site_case()), helper)


if __name__ == "__main__":

    unittest.main()
