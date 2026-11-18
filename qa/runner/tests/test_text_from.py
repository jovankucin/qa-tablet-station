"""act.py type --text-from (2026-11-03): a ticket draft's title or description typed from its own file, and nothing
else; the gate lets the command through without its shell-expansion question.

python3 -I qa/runner/tests/test_text_from.py
"""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import act  # noqa: E402
import gate  # noqa: E402

DRAFT = "tests/2026-11-03-offline/tickets/1"


class TextFrom(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.saved = act.WORKSPACE
        act.WORKSPACE = self.tmp
        os.makedirs(os.path.join(self.tmp, DRAFT))
        with open(os.path.join(self.tmp, DRAFT, "title.txt"), "w") as f:
            f.write("[CT] Maps: import fails offline (TA5)\n")
        with open(os.path.join(self.tmp, DRAFT, "description.txt"), "w") as f:
            f.write("## Issue Summary\r\nThe import failed.\n\n")

    def tearDown(self):
        act.WORKSPACE = self.saved
        shutil.rmtree(self.tmp)

    def test_reads_like_cat(self):
        self.assertEqual(act.read_ticket_text(DRAFT + "/title.txt"), "[CT] Maps: import fails offline (TA5)")
        self.assertEqual(act.read_ticket_text(DRAFT + "/description.txt"), "## Issue Summary\nThe import failed.")

    def test_only_draft_text_files(self):
        for bad in ("qa/secrets.env", DRAFT + "/draft.json", "tests/x/tickets/1/title.txt", "../" + DRAFT + "/title.txt",
                    DRAFT + "/../1/title.txt", "/etc/passwd"):
            with self.assertRaises(act.Fail, msg=bad) as e:
                act.read_ticket_text(bad)
            self.assertEqual(e.exception.code, 4)

    def test_no_links_and_size_cap(self):
        os.makedirs(os.path.join(self.tmp, "tests/2026-11-03-offline/tickets/2"))
        os.symlink(os.path.join(self.tmp, DRAFT, "title.txt"),
                   os.path.join(self.tmp, "tests/2026-11-03-offline/tickets/2/title.txt"))
        with self.assertRaises(act.Fail):
            act.read_ticket_text("tests/2026-11-03-offline/tickets/2/title.txt")
        with open(os.path.join(self.tmp, DRAFT, "title.txt"), "w") as f:
            f.write("x" * 9000)
        with self.assertRaises(act.Fail):
            act.read_ticket_text(DRAFT + "/title.txt")

    def test_parser_and_exclusive_options(self):
        a = act.parser().parse_args(["TA5", "type", "css:input[name=title]", "--text-from", DRAFT + "/title.txt"])
        self.assertEqual(a.text_from, DRAFT + "/title.txt")

    def test_gate_passes_text_from_but_still_asks_on_cat(self):
        plain = f"python3 -I qa/runner/act.py TA5 type css:input[name=title] --text-from {DRAFT}/title.txt"
        self.assertEqual(gate.decide("Bash", {"command": plain})[0], "pass")
        cat = f'python3 -I qa/runner/act.py TA5 type css:input[name=title] --text "$(cat {DRAFT}/title.txt)"'
        self.assertEqual(gate.decide("Bash", {"command": cat})[0], "ask")


if __name__ == "__main__":
    unittest.main()
