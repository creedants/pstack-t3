if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from ..support import BULLETS, CLAUDE, ProcessReason, ROOT, _owner_words, json, real_process

class LabelsMiscMixin:
    def test_a_ticket_and_an_item_that_name_a_goal_with_no_row_are_warned_of_and_count_as_unassigned(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "fix", "--goal", "G1")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix a")
        self.hand_edit("rail.tsv", "T1", goal="")
        self.hand_edit("rail.tsv", "T2", goal="G9")
        self.hand_edit("dishes.tsv", "D1", goal="G7")
        out = self.assert_each_printing_command_warns(
            f"T2 names goal 'G9', {self.NO_GOAL_ROW}; run ticket set T2 --goal G<n> or --no-goal",
            f"D1 names goal 'G7', {self.NO_GOAL_ROW}; no command changes this cell; correct it in dishes.tsv by hand")
        self.assertEqual(out["status"].splitlines()[3:],
                         ["liveness: every 600000 ms (default for milestones)",
                          "kinds: fix 1 waiting, 1 in flight", "goals: unassigned 1 waiting, 1 in flight"])
        self.assertEqual(out["goal list"].splitlines()[0], "G1 open activity view: 0 waiting, 0 in flight, 0 merged, 0 send-backs")
        self.assertEqual(out["goal list"].splitlines()[-1], "unassigned: 1 waiting, 1 in flight, 0 merged, 0 send-backs")
        self.assertEqual(out["ticket list"], "T1 assigned normal fix [user] a\nT2 waiting normal fix [user] b")
        self.assertEqual(out["startable"].splitlines()[2], "T2 normal fix, 0m: unknown, no paths recorded")
        self.assertEqual(out["close --dry-run"].splitlines()[4:],
                         ["## In progress", "", "- fix D1 (T1): Fix a", "", "## New tickets, not started", "", "- fix T2: b",
                          "", "## Send-back reasons (last 7 days)", "", "No send-backs in the last 7 days.",
                          "", "## Spend", "", "G7  wall open  rounds 0  send-backs 0  threads not recorded",
                          "", "## Follow-up share", "",
                          "- 0 of 2 tickets created in the last 14 days came from a report (0%)."])
        self.assertNotIn("G9", "".join(out.values()))
        before, _, after = out["close --dry-run"].partition("## Spend")
        self.assertNotIn("G7", "".join(value for key, value in out.items() if key != "close --dry-run") + before)
        self.assertIn("G7", after)

    def test_from_report_changes_only_the_ticket_table_the_log_and_last_activity(self):
        self.fired_bug_fix()
        self.review_file("D1.md", "## Follow-ups\n\n- Correctness: Tidy.\n")

        def changed_by(*command):
            before = self.store_files()
            self.brigade(*command)
            after = self.store_files()
            return before, sorted(name for name in after if after[name] != before.get(name))

        _, by_hand = changed_by("ticket", "add", "--kind", "fix", "--summary", "by hand")
        (self.at / "restaurant.lock").unlink()
        before, from_report = changed_by("ticket", "add", "--from-report", "reports/D1.md")
        self.assertEqual(from_report, ["log.tsv", "rail.tsv", "restaurant.json"])
        self.assertEqual(from_report, by_hand)
        old, new = json.loads(before["restaurant.json"]), json.loads((self.at / "restaurant.json").read_text())
        self.assertEqual([key for key in new if new[key] != old.get(key)], ["lastActivityAt"])

    def test_goal_set_names_the_waiting_tickets_the_items_in_flight_and_the_tickets_riding_along_in_one_refusal(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "polish", "--goal", "G2", "--priority", "low")
        self.brigade("ticket", "add", "--summary", "c", "--kind", "fix", "--goal", "G2")
        self.brigade("ticket", "add", "--summary", "d", "--kind", "fix", "--goal", "G2")
        self.brigade("fire", "--tickets", "T1,T2", "--ride-along", "T2", "--station", "bug-fix", "--summary", "Do a and b")
        self.brigade("fire", "--tickets", "T3", "--station", "bug-fix", "--summary", "Do c")
        self.assert_goal_keeps_open(
            "G2", "T4 waiting; D2 in flight; T2 riding along",
            "give each waiting ticket another goal with ticket set --goal, or none with --no-goal; a waiting ticket that an item in "
            "flight names keeps its goal until that item is merged or dropped; finish or drop each item in flight; "
            "finish or drop the item each of those tickets rides along in")

    def test_the_built_brigade_refuses_a_bedrock_claude_author_with_a_claude_verifier(self):
        self.fired_bug_fix()
        built = ROOT / "skills/brigade/scripts/brigade.py"
        # process exit status
        result = real_process(
            ProcessReason.EXIT_STATUS, built,
            ["--store", str(self.store), "--at", str(self.at), *_owner_words(self.at),
             "pass", "record", "D1", "--sha", "abc", "--verdict", "pass",
             "--author", self.BEDROCK, "--verifier", CLAUDE],
        )
        self.assertEqual((result.returncode, result.stdout), (1, ""))
        self.assertEqual(result.stderr.strip(), self.same_family_refusal(self.BEDROCK, CLAUDE))
        self.assertEqual(self.pass_rows(), [])

    def test_with_goals_tsv_deleted_each_goal_cell_is_warned_of_its_row_counts_as_unassigned_and_no_command_writes_the_table(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "fix", "--goal", "G1")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix a")
        (self.at / "goals.tsv").unlink()
        out = self.assert_each_printing_command_warns(
            f"T1 names goal 'G1', {self.NO_GOAL_ROW}; {self.NOT_WHILE} T1 is assigned; correct it in rail.tsv by hand",
            f"T2 names goal 'G1', {self.NO_GOAL_ROW}; run ticket set T2 --goal G<n> or --no-goal",
            f"D1 names goal 'G1', {self.NO_GOAL_ROW}; no command changes this cell; correct it in dishes.tsv by hand")
        self.assertEqual(out["status"].splitlines()[3:], ["liveness: every 600000 ms (default for milestones)", "kinds: fix 1 waiting, 1 in flight"])
        self.assertEqual(out["goal list"], "no open or paused goals\nunassigned: 1 waiting, 1 in flight, 0 merged, 0 send-backs")
        self.assertEqual(out["ticket list"], "T1 assigned normal fix [user] a\nT2 waiting normal fix [user] b")
        self.assertEqual(out["close --dry-run"].splitlines()[4:],
                         ["## In progress", "", "- fix D1 (T1): Fix a", "", "## New tickets, not started", "", "- fix T2: b",
                          "", "## Send-back reasons (last 7 days)", "", "No send-backs in the last 7 days.",
                          "", "## Spend", "", "G1  wall open  rounds 0  send-backs 0  threads not recorded",
                          "", "## Follow-up share", "",
                          "- 0 of 2 tickets created in the last 14 days came from a report (0%)."])
        self.assertFalse((self.at / "goals.tsv").exists())
