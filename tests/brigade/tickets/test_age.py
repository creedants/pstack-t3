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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from ..support import BrigadeTest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "t3/added/brigade/scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "t3/added/brigade/scripts"))
from brigade_lib._tickets.labels import Label
from brigade_lib.tickets import age_select


class AgeMixin:
    def old_low(self, summary="old work", kind="polish", **ago):
        self.brigade("ticket", "add", "--kind", kind, "--priority", "low", "--summary", summary)
        ident = f"T{len(self.rows_of('rail.tsv'))}"
        self.refile(ident, **ago)
        return ident

    def test_age_leaves_a_fix_ticket_unlisted(self):
        self.open()
        self.old_low(kind="fix", days=15)
        self.assertEqual(self.brigade("ticket", "age", "--dry-run"), "nothing old enough to age out")

    def test_age_leaves_a_feature_ticket_unlisted(self):
        self.open()
        self.old_low(kind="feature", days=15)
        self.assertEqual(self.brigade("ticket", "age", "--dry-run"), "nothing old enough to age out")

    def test_age_leaves_a_study_ticket_unlisted(self):
        self.open()
        self.old_low(kind="study", days=15)
        self.assertEqual(self.brigade("ticket", "age", "--dry-run"), "nothing old enough to age out")

    def test_age_leaves_a_ticket_with_no_kind_unlisted(self):
        self.open()
        ident = self.old_low(days=15)
        self.hand_edit("rail.tsv", ident, kind="")
        self.assertEqual(self.brigade("ticket", "age", "--dry-run"), "nothing old enough to age out")

    def test_age_leaves_a_ticket_with_an_empty_priority_unlisted(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "polish", "--summary", "old work")
        self.refile("T1", days=15)
        self.assertEqual(self.brigade("ticket", "age", "--dry-run"), "nothing old enough to age out")

    def test_age_leaves_a_normal_ticket_unlisted(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "polish", "--priority", "normal", "--summary", "old work")
        self.refile("T1", days=15)
        self.assertEqual(self.brigade("ticket", "age", "--dry-run"), "nothing old enough to age out")

    def test_age_leaves_an_urgent_ticket_unlisted(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "upkeep", "--priority", "urgent", "--summary", "old work")
        self.refile("T1", days=15)
        self.assertEqual(self.brigade("ticket", "age", "--dry-run"), "nothing old enough to age out")

    def test_age_leaves_a_ticket_marked_as_a_decision_unlisted(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "polish", "--priority", "low", "--summary", "old work",
                     "--decision")
        self.refile("T1", days=15)
        self.assertEqual(self.brigade("ticket", "age", "--dry-run"), "nothing old enough to age out")

    def test_age_leaves_a_ticket_with_a_ref_unlisted(self):
        self.open()
        ident = self.old_low(days=15)
        self.hand_edit("rail.tsv", ident, ref="https://github.com/o/r/issues/1")
        self.assertEqual(self.brigade("ticket", "age", "--dry-run"), "nothing old enough to age out")

    def test_age_leaves_a_ticket_another_ticket_waits_on_unlisted(self):
        self.open()
        first = self.old_low(summary="first", days=15)
        self.brigade("ticket", "add", "--kind", "polish", "--priority", "low", "--summary", "second",
                     "--after", first)
        self.refile("T2", days=15)
        out = self.brigade("ticket", "age", "--dry-run")
        self.assertNotIn(f"{first} ", out)
        self.assertIn("T2 15d polish: second", out.splitlines()[0])
        self.assertIn("1 ticket would age out", out.splitlines()[-1])

    def test_age_leaves_a_ticket_a_label_freeze_holds_back_unlisted(self):
        self.open()
        self.old_low(days=15)
        self.brigade("fire", "--tickets", "T1", "--station", "refactoring", "--summary", "old work")
        self.hand_edit("rail.tsv", "T1", state="waiting")
        self.assertEqual(self.brigade("ticket", "age", "--dry-run"), "nothing old enough to age out")

    def test_age_leaves_a_ticket_in_another_state_unlisted(self):
        self.open()
        self.old_low(summary="one", days=15)
        self.old_low(summary="two", days=15)
        self.old_low(summary="three", days=15)
        self.old_low(summary="four", days=15)
        self.brigade("fire", "--tickets", "T1", "--station", "refactoring", "--summary", "one")
        self.brigade("ticket", "set", "T2", "--state", "done")
        self.brigade("ticket", "set", "T3", "--state", "dropped")
        self.hand_edit("rail.tsv", "T4", state="moved", dish="to:docs")
        self.assertEqual(self.brigade("ticket", "age", "--dry-run"), "nothing old enough to age out")

    def test_age_selects_only_past_the_strict_day_boundary(self):
        now = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
        rows = [
            {"id": "T10", "at": (now - timedelta(days=14, seconds=1)).isoformat(), "state": "waiting",
             "source": "user", "ref": "", "dish": "", "summary": "past", "priority": "low", "paths": "",
             "decision": "", "kind": "polish", "goal": "", "after": ""},
            {"id": "T2", "at": (now - timedelta(days=14, seconds=1)).isoformat(), "state": "waiting",
             "source": "user", "ref": "", "dish": "", "summary": "past two", "priority": "low", "paths": "",
             "decision": "", "kind": "upkeep", "goal": "", "after": ""},
            {"id": "T1", "at": (now - timedelta(days=14)).isoformat(), "state": "waiting",
             "source": "user", "ref": "", "dish": "", "summary": "edge", "priority": "low", "paths": "",
             "decision": "", "kind": "polish", "goal": "", "after": ""},
        ]
        labels = SimpleNamespace(tickets={row["id"]: Label("polish" if row["id"] != "T2" else "upkeep", "")
                                          for row in rows})
        selected = age_select(rows, labels, {}, set(), 14, now)
        self.assertEqual([ticket.id for ticket in selected], ["T2", "T10"])

    def test_age_moves_a_15_day_old_low_polish_ticket(self):
        self.open()
        self.old_low(days=15)
        out = self.brigade("ticket", "age")
        self.assertEqual(out.splitlines()[0], "T1 15d polish: old work")
        self.assertEqual(out.splitlines()[-1], "1 ticket aged out")
        self.assertEqual(self.table_row(self.at, "rail.tsv", "T1")["state"], "dropped")
        dropped = [row for row in self.log_rows()
                   if row["kind"] == "ticket" and row["id"] == "T1" and row["state"] == "dropped"]
        self.assertEqual(len(dropped), 1)
        self.assertTrue(dropped[0]["note"].startswith("aged out"))
        self.assertIn("15 days", dropped[0]["note"])
        notes = self.brigade("ticket", "notes", "--all")
        self.assertIn("T1", notes)
        self.assertIn("old work", notes)

    def test_age_keeps_recorded_paths_as_note_paths(self):
        self.tracked_project("a.py")
        self.open()
        self.brigade("ticket", "add", "--kind", "polish", "--priority", "low", "--summary", "fix it",
                     "--paths", "a.py")
        self.refile("T1", days=15)
        self.assertEqual(self.brigade("ticket", "age").splitlines()[-1], "1 ticket aged out")
        out = self.brigade("ticket", "notes", "--paths", "a.py")
        self.assertIn("a.py: T1 ", out)
        self.assertIn("fix it", out)

    def test_age_dry_run_writes_nothing(self):
        self.open()
        self.old_low(days=15)
        before = self.store_files()
        out = self.brigade("ticket", "age", "--dry-run")
        self.assertIn("T1 15d polish: old work", out.splitlines()[0])
        self.assertIn("1 ticket would age out", out.splitlines()[-1])
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.table_row(self.at, "rail.tsv", "T1")["state"], "waiting")

    def test_age_older_than_overrides_the_limit_for_one_run(self):
        self.open()
        self.old_low(kind="upkeep", days=10)
        self.assertEqual(self.brigade("ticket", "age", "--dry-run"), "nothing old enough to age out")
        out = self.brigade("ticket", "age", "--older-than", "7", "--dry-run")
        self.assertIn("T1 10d upkeep: old work", out.splitlines()[0])
        self.assertIn("1 ticket would age out", out.splitlines()[-1])
        self.assertEqual(self.table_row(self.at, "rail.tsv", "T1")["state"], "waiting")

    def test_age_refuses_an_older_than_below_one(self):
        self.open()
        self.assertEqual(self.brigade("ticket", "age", "--older-than", "0", ok=False),
                         "brigade: --older-than takes a whole number of 1 or more")

    def test_age_with_zero_limit_turns_aging_off(self):
        self.open()
        self.old_low(days=15)
        self.brigade("set", "--ticket-age", "0")
        before = self.store_files()
        self.assertEqual(self.brigade("ticket", "age"),
                         "ticket aging is off; set --ticket-age N turns it on")
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.table_row(self.at, "rail.tsv", "T1")["state"], "waiting")

    def test_age_retry_after_a_crash_writes_one_note_and_one_state_change(self):
        self.open()
        self.old_low(days=15)
        (self.at / "notes.tsv").write_text("path\titem\tat\ttext\n\tT1\t2026-01-02T00:00:00+00:00\told work\n")
        out = self.brigade("ticket", "age")
        self.assertIn("1 ticket aged out", out.splitlines()[-1])
        rows = (self.at / "notes.tsv").read_text().splitlines()
        self.assertEqual(len(rows), 2)
        dropped = [row for row in self.log_rows()
                   if row["kind"] == "ticket" and row["id"] == "T1" and row["state"] == "dropped"]
        self.assertEqual(len(dropped), 1)
        self.assertEqual(self.table_row(self.at, "rail.tsv", "T1")["state"], "dropped")

    def test_set_ticket_age_takes_zero_and_empty_and_refuses_text(self):
        self.open()
        self.brigade("set", "--ticket-age", "0")
        self.assertEqual(json.loads((self.at / "restaurant.json").read_text())["ticketAge"], 0)
        self.brigade("set", "--ticket-age", "")
        self.assertEqual(json.loads((self.at / "restaurant.json").read_text())["ticketAge"], 0)
        before = (self.at / "restaurant.json").read_bytes()
        self.assertEqual(self.brigade("set", "--ticket-age", "soon", ok=False),
                         "brigade: --ticket-age takes a whole number of 0 or more, "
                         "or empty string to turn aging off, got 'soon'")
        self.assertEqual((self.at / "restaurant.json").read_bytes(), before)

    def test_next_evening_refuses_a_hand_written_bad_ticket_age(self):
        self.open()
        path = self.at / "restaurant.json"
        meta = json.loads(path.read_text())
        meta["ticketAge"] = "soon"
        path.write_text(json.dumps(meta, indent=2) + "\n")
        code, out, err = self.run_raw("next", "evening")
        self.assertEqual(code, 1)
        self.assertIn("ticketAge", err)
        self.assertIn("it is not a whole number of 0 or more", err)

    def test_next_evening_runs_ticket_age_before_the_report_see_line(self):
        self.open()
        sheet = self.next_sheet("evening")
        lines = sheet.splitlines()
        self.assertIn("run ticket age", lines)
        self.assertLess(lines.index("run ticket age"),
                        lines.index("see Write the evening report for this reporting level. At digest, plain sentences "
                                    "and the closeout path. Otherwise at most three sentences and then the close output."))
        self.assertEqual(lines[-1], "reply: yes (evening: report)")

    def test_next_evening_prints_no_age_run_after_set_zero(self):
        self.open()
        self.brigade("set", "--ticket-age", "0")
        sheet = self.next_sheet("evening")
        self.assertNotIn("ticket age", sheet)
        self.assertEqual(sheet.splitlines()[-1], "reply: yes (evening: report)")

    def test_close_prints_the_waiting_section_after_an_age_out(self):
        self.open()
        self.old_low(summary="one", kind="polish", days=21)
        self.old_low(summary="two", kind="upkeep", days=15)
        self.brigade("ticket", "add", "--kind", "polish", "--priority", "low", "--summary", "fresh")
        code, before, err = self.close_run("--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("## Waiting list", before)
        self.assertIn("- 3 waiting tickets, oldest 21 days old; no ticket aged out since the last report.", before)
        self.assertEqual(self.brigade("ticket", "age").splitlines()[-1], "2 tickets aged out")
        code, out, err = self.close_run("--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("## Waiting list", out)
        self.assertIn("- 1 waiting ticket, oldest 0 days old; 2 aged out since the last report.", out)
        self.assertIn("- no auto-start level is recorded; waiting tickets start only when told; "
                      "set --autofire decides which priorities start on their own.", out)
        self.brigade("set", "--autofire", "normal")
        code, out, err = self.close_run("--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("## Waiting list", out)
        self.assertNotIn("no auto-start level is recorded", out)

    def test_close_prints_no_waiting_section_for_a_fresh_waiting_list(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "polish", "--priority", "low", "--summary", "fresh")
        code, out, err = self.close_run("--dry-run")
        self.assertEqual(code, 0)
        self.assertNotIn("Waiting list", out)


class AgeTest(BrigadeTest, AgeMixin):
    pass
