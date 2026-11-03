if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import json
import sys
from pathlib import Path

from .support import BrigadeTest, setUpModule
from ._support.names import CLAUDE, CODEX

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "t3/added/brigade/scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "t3/added/brigade/scripts"))

MARKER = "the quick brown table column was never checked 9zq"


class LookBackTest(BrigadeTest):
    def seed_rounds(self, count):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix s")
        self.brigade("dish", "D1", "--state", "in-progress", "--thread", "w1")
        for number in range(1, count + 1):
            if number > 1:
                self.brigade("dish", "D1", "--state", "in-progress")
            self.review_file(f"D1-review-{number}.md", f"findings {number}\n{MARKER}\n")
            self.brigade("pass", "record", "D1", "--sha", f"sha{number}", "--verdict", "send-back",
                         "--reason", "other", "--author", CLAUDE, "--verifier", CODEX,
                         "--report", f"D1-review-{number}.md", "--reviewer-thread", f"r{number}")

    def look_back_path(self, out):
        lines = out.splitlines()
        self.assertEqual(len(lines), 2)
        return Path(lines[1])

    def proposal_file(self, name, evidence):
        path = Path(self.temporary.name) / name
        path.write_text(
            "category: check-gap\n"
            "fix: check\n"
            f"evidence: {evidence}\n"
            "change: Run the store tests in the pre-commit hook, so an unrecorded table column fails before review.\n"
        )
        return str(path)

    def recorded_of(self, ident="L1"):
        return json.loads((self.at / "look-backs" / f"{ident}.json").read_text())

    def test_evening_sheet_runs_the_look_back_once_when_a_round_is_new_in_light_mode(self):
        self.seed_rounds(1)
        sheet = self.brigade("next", "evening")
        self.assertEqual([line for line in sheet.splitlines() if line == "run look-back"], ["run look-back"])

    def test_evening_sheet_names_no_look_back_in_full_mode_because_the_scheduled_reflect_covers_it(self):
        self.seed_rounds(1)
        self.brigade("set", "--mode", "full")
        sheet = self.brigade("next", "evening")
        self.assertNotIn("run look-back", sheet)

    def test_evening_sheet_names_no_look_back_when_no_round_was_recorded_since_the_last_run(self):
        self.seed_rounds(1)
        out = self.brigade("look-back")
        self.look_back_path(out)
        self.brigade("look-back", "--record", "--none", "--reason", "nothing usable this week")
        sheet = self.brigade("next", "evening")
        self.assertNotIn("run look-back", sheet)

    def test_evening_sheet_names_no_look_back_when_the_setting_is_zero_or_empty(self):
        self.seed_rounds(1)
        self.assertIn("run look-back", self.brigade("next", "evening"))
        self.brigade("set", "--look-back", "0")
        self.assertNotIn("run look-back", self.brigade("next", "evening"))
        self.brigade("set", "--look-back", "")
        self.assertNotIn("run look-back", self.brigade("next", "evening"))

    def test_brief_lists_each_round_with_its_verdict_reason_report_and_both_threads_and_copies_no_report_body(self):
        self.seed_rounds(2)
        out = self.brigade("look-back")
        text = self.look_back_path(out).read_text()
        self.assertIn("send-back", text)
        self.assertIn("reason other", text)
        self.assertIn("reports/D1-review-1.md", text)
        self.assertIn("reports/D1-review-2.md", text)
        self.assertIn("worker thread w1", text)
        self.assertIn("reviewer thread r2", text)
        self.assertNotIn(MARKER, text)

    def test_brief_names_the_round_cap_when_more_rounds_exist_than_it_lists(self):
        self.seed_rounds(1)
        path = self.at / "pass.tsv"
        lines = path.read_text().splitlines()
        base = len(lines)
        extra = []
        for number in range(60):
            stamp = f"2026-11-0{1 + (number % 2)}T10:{number % 60:02d}:00+00:00"
            extra.append(f"{stamp}\tD1\t\tcap{number}\tsend-back\t{CLAUDE}\t{CODEX}\tnote\t\t\tother")
        path.write_text("\n".join(lines + extra) + "\n")
        out = self.brigade("look-back")
        text = self.look_back_path(out).read_text()
        self.assertIn(
            "The newest 50 of 61 rounds are listed. The other 11 are older and are not repeated here.",
            text,
        )
        self.assertEqual(text.count("reason other"), 50)
        self.assertEqual(base, 2)

    def test_record_refuses_a_report_file_that_does_not_exist_and_stamps_nothing(self):
        self.seed_rounds(1)
        self.brigade("look-back")
        proposal = self.proposal_file("missing-evidence.txt", "reports/D1-review-9.md")
        error = self.brigade("look-back", "--record", proposal, ok=False)
        self.assertIn("reports/D1-review-9.md", error)
        self.assertIn("is not a file this store holds", error)
        self.assertIsNone(self.recorded_of()["recorded"])
        self.assertIn("run look-back", self.brigade("next", "evening"))

    def test_record_refuses_a_thread_the_brief_does_not_list_and_stamps_nothing(self):
        self.seed_rounds(1)
        self.brigade("look-back")
        proposal = self.proposal_file("foreign-thread.txt", "mcp:00000000-0000-4000-8000-000000000099")
        error = self.brigade("look-back", "--record", proposal, ok=False)
        self.assertIn("mcp:00000000-0000-4000-8000-000000000099", error)
        self.assertIn("is not a file this store holds", error)
        self.assertIsNone(self.recorded_of()["recorded"])

    def test_record_twice_for_one_brief_stamps_once_and_stores_no_duplicate(self):
        self.seed_rounds(1)
        self.brigade("look-back")
        proposal = self.proposal_file("one.txt", "reports/D1-review-1.md")
        first = self.brigade("look-back", "--record", proposal)
        self.assertIn("recorded L1 at ", first)
        self.assertIn("1 proposal in look-backs/L1.json", first)
        stamp = self.recorded_of()["recorded"]["at"]
        second = self.brigade("look-back", "--record", proposal)
        self.assertEqual(second, f"L1 was recorded at {stamp}; nothing stamped")
        data = self.recorded_of()
        self.assertEqual(len(data["recorded"]["proposals"]), 1)

    def test_record_refuses_a_report_path_that_escapes_reports_through_parent_segments_or_a_symlink(self):
        self.seed_rounds(1)
        self.brigade("look-back")
        reports = self.at / "reports"
        (reports / "escape.md").symlink_to(self.at / "restaurant.json")
        for number, value in enumerate(("reports/../restaurant.json", "reports/escape.md",
                                        str(reports / "D1-review-1.md"))):
            proposal = self.proposal_file(f"escape-{number}.txt", value)
            error = self.brigade("look-back", "--record", proposal, ok=False)
            self.assertIn("is not a file this store holds", error)
            self.assertIsNone(self.recorded_of()["recorded"])

    def test_record_none_with_a_reason_stamps_a_run_that_found_nothing_usable(self):
        self.seed_rounds(1)
        self.brigade("look-back")
        first = self.brigade("look-back", "--record", "--none", "--reason", "nothing usable this week")
        self.assertIn("no usable proposal, nothing usable this week", first)
        data = self.recorded_of()
        self.assertEqual(data["recorded"]["kind"], "none")
        stamp = data["recorded"]["at"]
        second = self.brigade("look-back", "--record", "--none", "--reason", "nothing usable this week")
        self.assertEqual(second, f"L1 was recorded at {stamp}; nothing stamped")

    def test_close_prints_the_proposals_once_with_the_file_that_holds_them(self):
        self.seed_rounds(1)
        self.brigade("look-back")
        proposal = self.proposal_file("close.txt", "reports/D1-review-1.md")
        self.brigade("look-back", "--record", proposal)
        dry = self.brigade("close", "--dry-run")
        self.assertIn("## Look-back", dry)
        self.assertIn("missing or unconnected check", dry)
        self.assertIn("(evidence reports/D1-review-1.md, look-backs/L1.json)", dry)
        self.brigade("close")
        again = self.brigade("close", "--dry-run")
        self.assertNotIn("## Look-back", again)

    def test_close_prints_no_look_back_section_when_the_setting_is_off(self):
        self.seed_rounds(1)
        self.brigade("look-back")
        proposal = self.proposal_file("off.txt", "reports/D1-review-1.md")
        self.brigade("look-back", "--record", proposal)
        self.brigade("set", "--look-back", "0")
        self.assertNotIn("## Look-back", self.brigade("close", "--dry-run"))

    def test_look_back_refuses_when_the_setting_is_off(self):
        self.open()
        self.brigade("set", "--look-back", "0")
        before = self.store_files()
        out = self.brigade("look-back")
        self.assertEqual(out, "look-back is off; run set --look-back <days> to turn it on")
        self.assertEqual(self.store_files(), before)
