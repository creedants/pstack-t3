from ..support import (
    setUpModule,
    LandingTest as LandingTestBase,
    _run,
    ADMIN,
    os,
    Path,
    ProcessReason,
    repository_state,
    REVIEWER,
    sh,
    subprocess,
    sys,
    tempfile,
    unittest,
)
from .base import IdentityBase

class LandingTest(IdentityBase, LandingTestBase):
    def test_push_mode_pauses_on_a_changed_name_and_leaves_trunk_and_the_entry(self):
        self.assert_push_mode_pauses(*self.IDENTITY_CHANGES["name"])

    def test_human_mode_pauses_on_a_changed_name_and_calls_no_gh_and_pushes_no_queue_branch_and_leaves_the_entry(self):
        self.assert_human_mode_pauses(*self.IDENTITY_CHANGES["name"])

    def test_push_mode_pauses_on_a_changed_email_and_leaves_trunk_and_the_entry(self):
        self.assert_push_mode_pauses(*self.IDENTITY_CHANGES["email"])

    def test_human_mode_pauses_on_a_changed_email_and_calls_no_gh_and_pushes_no_queue_branch_and_leaves_the_entry(self):
        self.assert_human_mode_pauses(*self.IDENTITY_CHANGES["email"])

    def test_push_mode_pauses_on_a_name_that_differs_only_in_surrounding_spaces(self):
        self.assert_push_mode_pauses(*self.IDENTITY_CHANGES["name_spaces"])

    def test_human_mode_pauses_on_a_name_that_differs_only_in_surrounding_spaces(self):
        self.assert_human_mode_pauses(*self.IDENTITY_CHANGES["name_spaces"])

    def test_push_mode_pauses_on_an_email_that_differs_only_in_surrounding_spaces(self):
        self.assert_push_mode_pauses(*self.IDENTITY_CHANGES["email_spaces"])

    def test_human_mode_pauses_on_an_email_that_differs_only_in_surrounding_spaces(self):
        self.assert_human_mode_pauses(*self.IDENTITY_CHANGES["email_spaces"])

    def test_push_mode_pauses_on_a_name_that_differs_only_in_letter_case(self):
        self.assert_push_mode_pauses(*self.IDENTITY_CHANGES["name_case"])

    def test_human_mode_pauses_on_a_name_that_differs_only_in_letter_case(self):
        self.assert_human_mode_pauses(*self.IDENTITY_CHANGES["name_case"])

    def test_push_mode_pauses_on_an_email_that_differs_only_in_letter_case(self):
        self.assert_push_mode_pauses(*self.IDENTITY_CHANGES["email_case"])

    def test_human_mode_pauses_on_an_email_that_differs_only_in_letter_case(self):
        self.assert_human_mode_pauses(*self.IDENTITY_CHANGES["email_case"])

    def test_push_mode_pauses_on_another_name_and_email_that_print_as_the_same_text(self):
        self.assert_push_mode_pauses(*self.IDENTITY_CHANGES["same_joined_text"])

    def test_human_mode_pauses_on_another_name_and_email_that_print_as_the_same_text(self):
        self.assert_human_mode_pauses(*self.IDENTITY_CHANGES["same_joined_text"])

    def test_a_second_land_while_paused_for_the_identity_prints_the_first_reason_after_another_change_and_changes_no_row_and_no_origin_ref(self):
        reason = self.land_one_then_pause_as_audit()
        self.configure_owner("third", "third@example.com")
        rows, refs = self.queue_rows(), self.on_origin("for-each-ref")
        self.assertEqual(self.land("land"), f"queue paused: {reason}")
        self.assertEqual(self.queue_rows(), rows)
        self.assertEqual(self.on_origin("for-each-ref"), refs)

    def test_status_shows_the_identity_pause_after_the_counts(self):
        reason = self.land_one_then_pause_as_audit()
        self.assertEqual(self.land("status"),
                         f"push mode onto refs/remotes/origin/main. landed: 1, queued: 1, leases held: 1. Paused: {reason} author policy: credited")

    def test_resume_after_an_identity_pause_lets_land_run_and_store_the_new_pair_and_a_change_back_pauses_against_the_new_pair(self):
        self.land_one_then_pause_as_audit()
        self.assertEqual(self.land("resume"), "queue resumed")
        self.assertIsNone(self.stored_identity())
        self.assertEqual(self.land("land"), "landed E2 (r/D2)")
        self.assertEqual(self.stored_identity(), '["audit", "audit@example.com"]')
        self.assertEqual(self.on_origin("log", "-1", "--format=%an <%ae> %s", "main"), "audit <audit@example.com> w2")
        self.configure_owner()
        self.assertEqual(self.land("land"),
                         "queue paused: " + self.identity_pause("Owner <owner@example.com>", "audit <audit@example.com>"))

    def test_an_unset_user_name_pauses_and_changes_no_row_but_the_paused_row_and_one_new_log_row_and_no_origin_ref(self):
        self.assert_an_unset_identity_pauses(("--unset", "user.name"))
        self.assertEqual((self.configured_value("user.name"), self.configured_value("user.email")), (None, "owner@example.com"))

    def test_an_unset_user_email_pauses_and_after_resume_land_lands_and_stores_no_identity(self):
        self.assert_an_unset_identity_pauses(("--unset", "user.email"))
        self.assertEqual((self.configured_value("user.name"), self.configured_value("user.email")), ("Owner", None))
        self.assert_resume_then_land_lands_and_stores_no_identity()

    def test_an_unset_user_name_and_user_email_pause_and_after_resume_land_lands_and_stores_no_identity(self):
        self.assert_an_unset_identity_pauses(("--unset", "user.name"), ("--unset", "user.email"))
        self.assertEqual((self.configured_value("user.name"), self.configured_value("user.email")), (None, None))
        self.assert_resume_then_land_lands_and_stores_no_identity()

    def test_an_empty_user_email_pauses_and_after_resume_land_lands_and_stores_no_identity(self):
        self.assert_an_unset_identity_pauses(("user.email", ""))
        self.assertEqual((self.configured_value("user.name"), self.configured_value("user.email")), ("Owner", ""))
        self.assert_resume_then_land_lands_and_stores_no_identity()

    def test_resume_from_a_rewind_pause_keeps_the_stored_identity_and_the_next_land_pauses_on_the_changed_one(self):
        self.init(mode="push")
        self.configure_owner()
        start = sh("git", "rev-parse", "origin/main", cwd=self.work)
        self.queue_one_as("Owner", "owner@example.com")
        self.assertEqual(self.land("land"), "landed E1 (r/D1)")
        landed = self.on_origin("rev-parse", "main")
        sh("git", "push", "-q", "--force", "origin", f"{start}:main", cwd=self.work)
        self.queue_one_as("Owner", "owner@example.com", number=2)
        self.assertEqual(self.land("land"), f"still queued: E2\nqueue paused: trunk no longer contains the last landed commit {landed[:12]} "
                                            f"(now {start[:12]}); check what happened to trunk, then run land.py resume")
        self.configure_owner("audit", "audit@example.com")
        self.assertEqual(self.land("resume"), "queue resumed")
        self.assertEqual(self.stored_identity(), '["Owner", "owner@example.com"]')
        self.assertEqual(self.land("land"), "queue paused: " + self.identity_pause("audit <audit@example.com>"))
        self.assertEqual(self.stored_identity(), '["Owner", "owner@example.com"]')

    def test_resume_on_a_queue_that_is_not_paused_keeps_the_stored_identity_and_land_pauses_on_a_changed_one(self):
        self.init(mode="push")
        self.configure_owner()
        self.assertEqual(self.land("land"), "nothing to land")
        self.assertEqual(self.land("resume"), "queue resumed")
        self.assertEqual(self.stored_identity(), '["Owner", "owner@example.com"]')
        self.configure_owner("audit", "audit@example.com")
        self.assertEqual(self.land("land"), "queue paused: " + self.identity_pause("audit <audit@example.com>"))

    def test_the_identity_pause_prints_a_newline_and_a_backslash_in_either_name_as_python_escapes(self):
        self.init(mode="push")
        self.configure_owner("A\nB\\C", "owner@example.com")
        self.assertEqual(self.land("land"), "nothing to land")
        self.configure_owner("X\\Y\nZ", "owner@example.com")
        self.assertEqual(self.configured_value("user.name"), "X\\Y\nZ")
        self.assertEqual(self.land("land"),
                         "queue paused: " + self.identity_pause(r"X\\Y\nZ <owner@example.com>", r"A\nB\\C <owner@example.com>"))

    def test_a_store_with_no_identity_key_runs_each_listed_command_with_status_0_and_land_stores_the_pair_and_lands(self):
        self.init(mode="push")
        self.queue_one()
        self.assertEqual(self.land("land"), "landed E1 (r/D1)")
        with self.raw_db() as db:
            keys = [key for key, in db.execute("SELECT key FROM contract ORDER BY key")]
        self.assertEqual(keys, ["authorPolicy", "batch", "checks", "commonDir", "generated", "heartbeatMs", "map", "mergeHeartbeatMs", "mergeMethod", "mode", "paused", "prItemLine", "rebuild", "remote",
                                "repo", "setup", "timeout", "tip", "trunk"])
        self.configure_owner()
        self.worker("w2", {"b.txt": "agent\n"})
        sh("git", "commit", "-q", "--amend", "--author", "Owner <owner@example.com>", "-m", "w2", cwd=self.base / "w2")
        sha = sh("git", "rev-parse", "HEAD", cwd=self.base / "w2")
        commands = (
            ["init", "--trunk", "main", "--mode", "push", "--check", "./check.sh"],
            ["cap", "5"],
            ["mode", "push"],
            ["owner", "--prefix", "docs/", "--generation", "1"],
            ["lease", "claim", "--holder", "r/D2", "--paths", "b.txt"],
            ["lease", "renew", "L2"],
            ["lease", "claim", "--holder", "r/D3", "--paths", "lib"],
            ["lease", "release", "L3"],
            ["lease", "list"],
            ["lease", "check", "--holder", "r/D4", "--paths", "c.txt"],
            ["lease", "reserve", "--for", "docs/", "--paths", "docs", "--ruling", "R1", *ADMIN],
            ["lease", "unreserve", "S1", *ADMIN],
            ["submit", "--holder", "r/D2", "--branch", "w2", "--sha", sha, "--lease", "L2", "--reviewer", REVIEWER],
            ["share", "--for", "docs/", "1", *ADMIN],
            ["contest", "--holders", "x/D1,y/D1", *ADMIN],
            ["contest", "--cancel", "C1", *ADMIN],
            ["land"],
            ["resume"],
            ["status"],
            ["status", "E2"],
            ["status", "--holder", "r/"],
        )
        printed = {}
        for command in commands:
            with self.subTest(command=command[:2]):
                status, printed[command[0]], error = self.run_land(self.work, *command)
                self.assertEqual((status, error), (0, ""))
        self.assertEqual(printed["land"], "landed E2 (r/D2)\n")
        self.assertEqual(self.stored_identity(), '["Owner", "owner@example.com"]')
