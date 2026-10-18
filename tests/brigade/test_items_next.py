from .support import (
    setUpModule,
    BrigadeTest,
    CLAUDE,
    CODEX,
    json,
)


class ItemsNextTest(BrigadeTest):
    def test_next_accepts_commands(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix s")
        for number in range(3):
            self.brigade("pass", "record", "D1", "--sha", f"s{number}", "--verdict", "send-back",
                         "--author", CLAUDE, "--verifier", CODEX, "--late")
        inbox = self.at / "inbox"
        inbox.mkdir()
        (inbox / "hand.json").write_text(json.dumps(
            {"handoff": "app/other/T1", "summary": "Fix a", "source": "user", "ref": ""}))
        sheet = self.next_sheet("report-back", "D1")
        self.assertEqual(sheet, "\n".join([
            "wake: report-back D1",
            "reporting: milestones",
            "run inbox take",
            "run dish D1 --reported",
            "run 86 add --dish D1 --round-budget",
            "run close",
            "reply: yes (decision: D1)",
        ]))
        self.execute_sheet(sheet)
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("dish", "D1", "--state", "queued")
        sheet = self.next_sheet("merge", "D1")
        self.assertEqual(sheet, "\n".join([
            "wake: merge D1",
            "reporting: milestones",
            "run inbox take",
            "run dish D1 --state merged",
            "see Finish with the old worker for thread none, branch none, and pull request none for D1. "
            "If its run is still open, interrupt it and hang the attempt, then archive it. "
            "Remove the worktree and delete the branch after that. Do not claim they are already gone.",
            "if the queue is paused and this thread cannot fix it and this is not a bounce handed to a fresh worker and --failure stuck was not passed: "
            "run next --failure stuck",
            "reply: yes (merged: D1)",
        ]))
        self.execute_sheet(sheet)

    def test_next_facts(self):
        self.fired_bug_fix()
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("dish", "D1", "--state", "queued")
        pause = ("if the queue is paused and this thread cannot fix it and this is not a bounce handed to a fresh worker and --failure stuck was not passed: "
                 "run next --failure stuck")
        follow = "if land or watch reports D1 landed: run next merge D1"
        unspecified = self.next_sheet()
        self.assertEqual(unspecified, "\n".join([
            "wake: unspecified", "reporting: milestones", "see Run land for D1.", follow, pause,
            "reply: no (routine: unspecified)"]))
        self.assertNotIn("run dish D1 --state merged", unspecified)
        checks = self.next_sheet("checks", "D1", "--result", "pass")
        self.assertEqual(checks, "\n".join([
            "wake: checks D1", "reporting: milestones", "see Run land for holder perf/D1.", follow, pause,
            "reply: no (routine: pull request)"]))
        self.assertNotIn("run dish D1 --state merged", checks)
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "other")
        self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "Other")
        review = self.next_sheet("review", "D2", "--verdict", "send-back")
        self.assertIn("see Record pass record for D2 with verdict send-back. No sha is recorded.", review)
        self.assertNotIn("sha abc", review)
        self.assertNotIn("D2 is send-back", review)
        self.assertNotIn("D2 sent-back", review)
        self.assertIn("if the old worker's wait for D2 is terminal and D2 may take a fix round: run dish D2 --state in-progress", review)
        self.assertNotIn("run pass", review)
        self.assertFalse(any(line.startswith("run dish D2") for line in review.splitlines()))
        (self.at / "inbox").mkdir()
        (self.at / "inbox" / "bad.json").write_text("{")
        bad = self.next_sheet()
        self.assertIn("see inbox/bad.json is not valid JSON", bad)
        self.assertNotIn("run inbox take", bad)
        self.assertTrue(any(line.startswith("if ") for line in bad.splitlines()))

    def test_next_replies_yes_when_a_later_occasion_qualifies(self):
        self.fresh()
        self.brigade("set", "--reporting", "digest")
        stuck = self.next_sheet("review", "D1", "--verdict", "send-back", "--failure", "stuck")
        self.assertEqual(stuck.splitlines()[-1], "reply: yes (stuck: stuck)")
        self.assert_one_close_file(stuck)
        unsent = self.next_sheet("review", "D1", "--verdict", "send-back", "--unsent", "report")
        self.assertEqual(unsent.splitlines()[-1], "reply: yes (report: unsent report)")
        self.assert_one_close_file(unsent)
        failed = self.next_sheet("checks", "D1", "--result", "fail", "--unsent", "failed")
        self.assertEqual(failed.splitlines()[-1], "reply: yes (failed: unsent failed)")
        self.assert_one_close_file(failed)
        self.fresh()
        self.brigade("set", "--reporting", "digest")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "second")
        self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "Second")
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("dish", "D1", "--state", "queued")
        merged = self.next_sheet("merge", "D1", "--failure", "stuck")
        self.assertEqual(merged.splitlines()[-1], "reply: yes (stuck: stuck)")
        self.assert_one_close_file(merged)
        self.fresh()
        self.brigade("set", "--reporting", "digest")
        self.brigade("set", "--reports-to", "admin-thread")
        quiet = self.next_sheet("review", "D1", "--verdict", "send-back", "--failure", "stuck")
        self.assertEqual(quiet.splitlines()[-1], "reply: no (reports to the executive admin: send-back)")
        routed = self.next_sheet("review", "D1", "--verdict", "send-back", "--unsent", "report")
        self.assertEqual(routed.splitlines()[-1], "reply: yes (report: unsent report)")
        self.assert_one_close_file(routed)
        routed_fail = self.next_sheet("checks", "D1", "--result", "fail", "--unsent", "failed")
        self.assertEqual(routed_fail.splitlines()[-1], "reply: yes (failed: unsent failed)")
        self.assert_one_close_file(routed_fail)
        self.fresh()
        self.brigade("set", "--reporting", "digest")
        self.brigade("set", "--reports-to", "admin-thread")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "second")
        self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "Second")
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("dish", "D1", "--state", "queued")
        admin_merge = self.next_sheet("merge", "D1", "--failure", "stuck")
        self.assertEqual(admin_merge.splitlines()[-1], "reply: no (reports to the executive admin: merge)")
