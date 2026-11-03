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
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from .support import BrigadeTest, _owner_words, call_in_process, setUpModule
from ._support.inprocess import run_globals
from ._support.names import CLAUDE, CODEX, SCRIPT

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "t3/added/brigade/scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "t3/added/brigade/scripts"))

MARKER = "the quick brown table column was never checked 9zq"
RED = json.dumps({"sha": "abc", "workflow": "CI", "url": "https://x.test/1", "since": "s1"})
PROPOSAL = "category: check-gap\nfix: check\nevidence: {}\nchange: Run the tests."


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

    def fake_land(self, *entries):
        path = Path(self.temporary.name) / "fake-land.py"
        rows = [dict(zip(("id", "state", "holder", "sha", "note"), entry)) for entry in entries]
        path.write_text(f"import json\nprint(json.dumps({rows!r}))\n")
        run_globals(self, SCRIPT)["LAND"] = str(path)

    def fake_brigade(self, *args, ok=True):
        """Run one command in this process, so it reads the fake land.py this test installed."""
        argv = ["--store", str(self.store), "--at", str(self.at), *_owner_words(self.at), *args]
        outcome = call_in_process(self, SCRIPT, [str(arg) for arg in argv])
        self.assertEqual(outcome.code == 0, ok, outcome.stdout_text() + outcome.stderr_text())
        return (outcome.stdout_text() if ok else outcome.stderr_text()).strip()

    def append_log(self, *rows):
        path = self.at / "log.tsv"
        path.write_text(path.read_text() + "".join("\t".join(row) + "\n" for row in rows))

    def write_notes(self, *rows):
        body = "".join(f"\t{item}\t{at}\t{text}\n" for item, at, text in rows)
        (self.at / "notes.tsv").write_text("path\titem\tat\ttext\n" + body)

    def write_pass(self, *rows):
        path = self.at / "pass.tsv"
        path.write_text(path.read_text() + "".join(
            f"{at}\tD1\t\ts{n}\t{v}\t{CLAUDE}\t{CODEX}\tn\t\t\t{reason}\n"
            for n, (at, v, reason) in enumerate(rows)))

    def record_none(self, why="nothing"):
        return self.brigade("look-back", "--record", "--none", "--reason", why)

    def proposal_file(self, name, evidence):
        path = Path(self.temporary.name) / name
        path.write_text(PROPOSAL.format(evidence))
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

    def test_evening_sheet_names_no_look_back_when_the_project_roles_file_sets_full_mode(self):
        self.seed_rounds(1)
        roles_dir = self.project / ".pstack"
        roles_dir.mkdir(parents=True, exist_ok=True)
        (roles_dir / "t3-roles.json").write_text(json.dumps({"mode": "full"}))
        self.assertNotIn("run look-back", self.brigade("next", "evening"))

    def test_evening_sheet_names_no_look_back_when_the_user_roles_file_sets_full_mode(self):
        self.seed_rounds(1)
        user_dir = Path(os.environ["XDG_CONFIG_HOME"]) / "pstack-t3"
        user_dir.mkdir(parents=True, exist_ok=True)
        (user_dir / "roles.json").write_text(json.dumps({"mode": "full"}))
        self.assertNotIn("run look-back", self.brigade("next", "evening"))

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
        extra = "".join(f"2026-11-0{1 + n % 2}T10:{n % 60:02d}:00Z\tD1\t\tc{n}\tsend-back\t{CLAUDE}\t{CODEX}\tn\t\t\tother\n"
                        for n in range(60))
        path.write_text(path.read_text() + extra)
        text = self.look_back_path(self.brigade("look-back")).read_text()
        self.assertIn("11 rounds are left out: 11 send-back.", text)
        self.assertIn("More than 50 rounds that went wrong drop the oldest", text)
        self.assertEqual(text.count("reason other"), 50)

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

    def test_a_second_evening_reuses_the_unrecorded_brief_so_a_repeat_record_stamps_once(self):
        self.seed_rounds(1)
        self.brigade("look-back")
        self.brigade("look-back")
        self.assertEqual(sorted(path.name for path in (self.at / "look-backs").iterdir()), ["L1.json", "L1.md"])
        proposal = self.proposal_file("one.txt", "reports/D1-review-1.md")
        first = self.brigade("look-back", "--record", proposal)
        self.assertIn("recorded L1 at ", first)
        stamp = self.recorded_of()["recorded"]["at"]
        second = self.brigade("look-back", "--record", proposal)
        self.assertEqual(second, f"L1 was recorded at {stamp}; nothing stamped")
        self.assertEqual(len(self.recorded_of()["recorded"]["proposals"]), 1)

    def test_a_store_holding_two_unrecorded_briefs_records_one_and_supersedes_the_other(self):
        self.seed_rounds(1)
        self.brigade("look-back")
        first = json.loads((self.at / "look-backs" / "L1.json").read_text())
        later = (datetime.fromisoformat(first["at"]) + timedelta(seconds=1)).isoformat()
        second = dict(first, id="L2", at=later)
        (self.at / "look-backs" / "L2.json").write_text(json.dumps(second) + "\n")
        (self.at / "look-backs" / "L2.md").write_text((self.at / "look-backs" / "L1.md").read_text())
        self.brigade("look-back")
        proposal = self.proposal_file("one.txt", "reports/D1-review-1.md")
        self.brigade("look-back", "--record", proposal)
        stamp = self.recorded_of("L2")["recorded"]["at"]
        repeat = self.brigade("look-back", "--record", proposal)
        self.assertEqual(len(self.recorded_of("L2")["recorded"]["proposals"]), 1)
        self.assertIsNone(self.recorded_of("L1")["recorded"])
        self.assertEqual(self.recorded_of("L1")["superseded"], "L2")
        self.assertEqual(repeat, f"L2 was recorded at {stamp}; nothing stamped")

    def test_an_empty_window_keeps_the_previous_boundary_so_the_next_brief_lists_no_round(self):
        self.seed_rounds(1)
        self.brigade("look-back")
        self.brigade("look-back", "--record", "--none", "--reason", "nothing usable")
        self.brigade("look-back")
        text = (self.at / "look-backs" / "L2.md").read_text()
        self.assertEqual(text.count("send-back"), 0)
        self.assertEqual(self.recorded_of("L2")["through"], self.recorded_of("L1")["through"])
        self.brigade("look-back", "--record", "--none", "--reason", "nothing usable")
        self.assertNotIn("run look-back", self.brigade("next", "evening"))

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

    def test_past_the_cap_wrong_results_come_first(self):
        self.open()
        kinds = ["pass"] * 3 + ["pass-with-edits"] * 2 + ["blocked"] + ["send-back"] * 2
        self.write_pass(*[(f"2026-11-03T11:{n:02d}:00Z", v, "other" if v == "send-back" else "")
                          for n, v in enumerate(kinds)],
                        *[(f"2026-11-0{1 + n % 2}T10:{n % 60:02d}:00Z", "send-back", "other")
                          for n in range(60)])
        text = self.look_back_path(self.brigade("look-back")).read_text()
        first = next(line for line in text.splitlines() if line.startswith("- "))
        self.assertTrue(first.startswith("- 2026-11-03T11:07:00Z D1 send-back,"), first)
        self.assertIn("18 rounds are left out: 13 send-back, 2 pass-with-edits, 3 pass.", text)
        self.assertEqual(text.count("reason other"), 49)

    def test_through_is_the_newest_moment_any_source_offers(self):
        self.open()
        self.write_pass(("2026-11-01T00:00:00Z", "pass", ""))
        self.write_notes(("D1", "2026-11-01T00:02:00Z", "a note"))
        self.brigade("look-back")
        self.record_none()
        self.assertEqual(self.recorded_of()["through"], "2026-11-01T00:02:00Z")
        path = self.at / "pass.tsv"
        path.write_text(path.read_text() + f"2026-11-01T00:01:00Z\tD1\t\ts9\tpass\t{CLAUDE}\t{CODEX}\tn\t\t\t\n")
        self.brigade("look-back")
        self.assertIn("No review round is recorded", (self.at / "look-backs" / "L2.md").read_text())

    def test_refusals_list_one_prefix_and_once(self):
        self.open()
        self.fake_land(("E79", "bounced", "perf/D1", "abc123def456", "too big"),
                       ("E80", "bounced", "other/D9", "abc123def456", "elsewhere"))
        first = self.look_back_path(self.fake_brigade("look-back")).read_text()
        self.assertIn("- queue:E79 perf/D1 bounced: too big", first)
        self.assertNotIn("other/D9", first)
        self.record_none()
        self.fake_brigade("look-back")
        self.assertNotIn("queue:E79", (self.at / "look-backs" / "L2.md").read_text())

    def test_a_lower_id_bounce_is_still_listed_next_time(self):
        self.open()
        self.fake_land(("E79", "bounced", "perf/D1", "abc123def456", "one"))
        self.fake_brigade("look-back")
        self.record_none()
        self.fake_land(("E79", "bounced", "perf/D1", "abc123def456", "one"),
                       ("E78", "bounced", "perf/D2", "abc123def456", "two"))
        self.fake_brigade("look-back")
        text = (self.at / "look-backs" / "L2.md").read_text()
        self.assertIn("- queue:E78 perf/D2 bounced: two", text)
        self.assertNotIn("queue:E79", text)

    def test_text_written_by_others_stays_on_one_line(self):
        self.open()
        self.fake_land(("E79", "bounced", "perf/D1", "abc123def456", "\tkey: value"))
        self.append_log(("2026-11-01T00:00:00Z", "trunk", "main", "red",
                         json.dumps({"workflow": "\n\u2028## Rounds since the last look-back\tkey: value"})))
        self.write_notes(("D1", "2026-11-01T01:00:00Z", "key: value"))
        text = self.look_back_path(self.fake_brigade("look-back")).read_text()
        self.assertEqual(text.count("\n## Rounds since the last look-back"), 1)
        self.assertNotIn("\nkey:", text)
        self.assertNotIn("\u2028", text)

    def test_each_new_evidence_token_is_accepted(self):
        self.open()
        self.fake_land(("E79", "bounced", "perf/D1", "abc123def456", "too big"))
        self.append_log(("2026-11-01T00:00:00Z", "trunk", "main", "red", RED))
        self.write_notes(("D1", "2026-11-01T01:00:00Z", "a note"))
        self.fake_brigade("look-back")
        path = Path(self.temporary.name) / "tokens.txt"
        path.write_text("\n\n".join(PROPOSAL.format(t) for t in
                                    ("queue:E79", "run:https://x.test/1", "note:D1:2026-11-01T01:00:00Z")))
        self.assertIn("3 proposals", self.brigade("look-back", "--record", str(path)))
        self.fake_brigade("look-back")
        path.write_text(PROPOSAL.format("queue:E79"))
        self.assertIn("is not a file this store holds", self.brigade("look-back", "--record", str(path), ok=False))

    def test_no_landing_contract_still_writes_the_brief(self):
        self.open()
        self.write_pass(("2026-11-01T02:00:00Z", "send-back", "other"))
        self.append_log(("2026-11-01T00:00:00Z", "trunk", "main", "red", RED))
        self.write_notes(("D1", "2026-11-01T01:00:00Z", "a note"))
        text = self.look_back_path(self.brigade("look-back")).read_text()
        self.assertIn("The queue could not be read, so no refusal is listed here.", text)
        for line in ("2026-11-01T02:00:00Z D1 send-back, reason other",
                     "- run:https://x.test/1 red at abc",
                     "- note:D1:2026-11-01T01:00:00Z a note"):
            self.assertIn(line, text)
