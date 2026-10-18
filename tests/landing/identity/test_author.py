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
    def test_author_policy_any_lands_a_foreign_commit_with_no_credit_line(self):
        self.queue_one_by("Ann Lee <ann@example.com>", "-m", "port", author_policy=None)
        self.assertEqual(self.land("land"), "landed E1 (r/D1)")
        self.assertEqual(self.on_origin("log", "-1", "--format=%an <%ae>", "main"), "Ann Lee <ann@example.com>")

    def test_author_policy_configured_bounces_a_foreign_commit_that_carries_a_credit_line_and_a_trailer(self):
        sha = self.queue_one_by("Ann Lee <ann@example.com>", "-m", "port", "-m", "Based on #12 by @ann-lee",
                                "-m", "Co-authored-by: Ann Lee <ann@example.com>", author_policy="configured")
        self.assertEqual(self.land("land"), self.foreign_bounce(sha, "Ann Lee <ann@example.com>"))
        self.assertEqual(self.origin_log(), ["init"])

    def test_a_new_contract_stores_author_policy_any(self):
        self.init(mode="push", author_policy=None)
        self.assertEqual(self.stored_author_policy(), '"any"')

    def test_a_rerun_without_the_flag_keeps_a_stored_author_policy(self):
        self.init(mode="push", author_policy="credited")
        self.init(mode="push", author_policy=None)
        self.assertEqual(self.stored_author_policy(), '"credited"')
        sha = self.queue_one_by("Ann Lee <ann@example.com>", "-m", "port", author_policy=None)
        self.assertEqual(self.land("land"), self.foreign_bounce(sha, "Ann Lee <ann@example.com>"))
        self.assertEqual(self.origin_log(), ["init"])

    def test_an_unknown_author_policy_is_refused_and_leaves_the_contract_unchanged(self):
        self.init(mode="push")
        before = self.queue_rows()
        self.init(mode="push", author_policy="bogus", ok=False)
        self.assertEqual(self.queue_rows(), before)

    def test_a_stored_contract_without_the_author_policy_key_reads_as_any(self):
        self.queue_one_by("Ann Lee <ann@example.com>", "-m", "port", author_policy="credited")
        with self.raw_db() as db:
            db.execute("DELETE FROM contract WHERE key = 'authorPolicy'")
            db.commit()
        self.assertEqual(self.land("land"), "landed E1 (r/D1)")
        self.assertEqual(self.on_origin("log", "-1", "--format=%an <%ae>", "main"), "Ann Lee <ann@example.com>")

    def test_repo_status_names_each_author_policy_and_defaults_a_missing_key_to_any(self):
        for policy in ("any", "configured", "credited"):
            with self.subTest(policy=policy):
                self.init(mode="push", author_policy=policy)
                self.assertIn(f"author policy: {policy}", self.land("status"))
        with self.raw_db() as db:
            db.execute("DELETE FROM contract WHERE key = 'authorPolicy'")
            db.commit()
        self.assertIn("author policy: any", self.land("status"))

    def test_human_mode_bounces_an_entry_with_a_foreign_commit_author_and_opens_no_pr(self):
        with self.fake_gh():
            self.init(mode="human")
            self.configure_owner()
            self.queue_one()
            sha = sh("git", "rev-parse", "w1", cwd=self.work)
            self.assertEqual(self.land("land"), self.foreign_bounce(sha))
        self.assertFalse(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))
        self.assertFalse((self.base / "gh-calls").exists())

    def test_human_mode_opens_a_pr_for_an_entry_by_the_configured_author(self):
        with self.fake_gh():
            self.init(mode="human")
            self.configure_owner()
            self.worker("w1", {"a.txt": "agent\n"})
            sh("git", "commit", "-q", "--amend", "--author", "Owner <owner@example.com>", "-m", "w1", cwd=self.base / "w1")
            sha = sh("git", "rev-parse", "HEAD", cwd=self.base / "w1")
            self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
            self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER)
            self.assertEqual(self.land("land"), "opened PRs for E1 (r/D1) https://github.com/o/r/pull/9")
        self.assertEqual(self.created_heads(), ["landing/e1"])

    def test_push_mode_bounces_an_entry_with_a_foreign_commit_author_and_leaves_trunk(self):
        sha, printed = self.land_one_by("t <t@t>", "-m", "w1")
        self.assertEqual(printed, self.foreign_bounce(sha))
        self.assertEqual(self.origin_log(), ["init"])

    def test_push_mode_lands_an_entry_by_the_configured_author(self):
        _, printed = self.land_one_by("Owner <owner@example.com>", "-m", "w1")
        self.assertEqual(printed, "landed E1 (r/D1)")
        self.assertEqual(self.on_origin("log", "-1", "--format=%an <%ae> %s", "main"), "Owner <owner@example.com> w1")

    def test_an_author_with_the_configured_email_and_another_name_bounces(self):
        sha, printed = self.land_one_by("Other <owner@example.com>", "-m", "w1")
        self.assertEqual(printed, self.foreign_bounce(sha, "Other <owner@example.com>"))

    def test_a_configured_name_with_surrounding_spaces_bounces_an_author_without_them(self):
        sha, printed = self.land_one_by("Owner <owner@example.com>", "-m", "w1", owner=(" Owner ", "owner@example.com"))
        self.assertEqual(printed, self.foreign_bounce(sha, "Owner <owner@example.com>", " Owner  <owner@example.com>"))
        self.assertEqual(self.origin_log(), ["init"])

    def test_a_configured_email_with_surrounding_spaces_bounces_an_author_without_them(self):
        sha, printed = self.land_one_by("Owner <owner@example.com>", "-m", "w1", owner=("Owner", " owner@example.com "))
        self.assertEqual(printed, self.foreign_bounce(sha, "Owner <owner@example.com>", "Owner < owner@example.com >"))
        self.assertEqual(self.origin_log(), ["init"])

    def test_an_author_name_with_a_carriage_return_bounces_under_a_configured_name_with_a_newline_there(self):
        sha, printed = self.land_one_by("A\rB <owner@example.com>", "-m", "w1", owner=("A\nB", "owner@example.com"))
        self.assertEqual(printed, self.foreign_bounce(sha, r"A\rB <owner@example.com>", r"A\nB <owner@example.com>"))
        self.assertEqual(self.origin_log(), ["init"])

    def test_without_a_configured_identity_an_entry_by_any_author_lands(self):
        self.init(mode="push")
        for key in ("user.name", "user.email"):
            self.assertEqual(self.git_raw("config", key, cwd=self.work).returncode, 1)
        self.queue_one()
        self.assertEqual(self.land("land"), "landed E1 (r/D1)")
        self.assertEqual(self.on_origin("log", "-1", "--format=%an <%ae>", "main"), "t <t@t>")

    def test_with_user_name_set_and_no_user_email_an_entry_by_any_author_lands(self):
        self.init(mode="push")
        sh("git", "config", "user.name", "Owner", cwd=self.work)
        self.queue_one()
        self.assertEqual(self.land("land"), "landed E1 (r/D1)")

    def test_a_foreign_author_with_a_co_authored_by_trailer_and_a_based_on_line_lands(self):
        _, printed = self.land_one_by("Ann Lee <ann@example.com>", "-m", "port", "-m", "Based on #12 by @ann-lee",
                                      "-m", "Co-authored-by: Ann Lee <ann@example.com>")
        self.assertEqual(printed, "landed E1 (r/D1)")
        self.assertEqual(self.on_origin("log", "-1", "--format=%an <%ae>", "main"), "Ann Lee <ann@example.com>")

    def test_a_based_on_line_with_a_final_period_counts(self):
        _, printed = self.land_one_by("Ann Lee <ann@example.com>", "-m", "port", "-m", "Based on #12 by @ann-lee.",
                                      "-m", "Co-authored-by: Ann Lee <ann@example.com>")
        self.assertEqual(printed, "landed E1 (r/D1)")

    def test_a_based_on_line_inside_a_longer_line_does_not_count(self):
        sha, printed = self.land_one_by("Ann Lee <ann@example.com>", "-m", "port", "-m", "Based on #12 by @ann-lee, with fixes",
                                        "-m", "Co-authored-by: Ann Lee <ann@example.com>")
        self.assertEqual(printed, self.foreign_bounce(sha, "Ann Lee <ann@example.com>"))

    def test_a_co_authored_by_trailer_without_a_based_on_line_bounces(self):
        sha, printed = self.land_one_by("Ann Lee <ann@example.com>", "-m", "port", "-m", "Co-authored-by: Ann Lee <ann@example.com>")
        self.assertEqual(printed, self.foreign_bounce(sha, "Ann Lee <ann@example.com>"))
        self.assertEqual(self.origin_log(), ["init"])

    def test_a_based_on_line_without_a_co_authored_by_trailer_bounces(self):
        sha, printed = self.land_one_by("Ann Lee <ann@example.com>", "-m", "port", "-m", "Based on #12 by @ann-lee")
        self.assertEqual(printed, self.foreign_bounce(sha, "Ann Lee <ann@example.com>"))

    def test_a_co_authored_by_trailer_folded_over_two_lines_bounces(self):
        sha, printed = self.land_one_by("Ann Lee <ann@example.com>", "-m", "port", "-m", "Based on #12 by @ann-lee",
                                        "-m", "Co-authored-by: Ann Lee\n <ann@example.com>")
        self.assertEqual(printed, self.foreign_bounce(sha, "Ann Lee <ann@example.com>"))
        self.assertEqual(self.origin_log(), ["init"])

    def test_a_co_authored_by_trailer_that_names_someone_else_bounces(self):
        sha, printed = self.land_one_by("Ann Lee <ann@example.com>", "-m", "port", "-m", "Based on #12 by @ann-lee",
                                        "-m", "Co-authored-by: Bo Chen <bo@example.com>")
        self.assertEqual(printed, self.foreign_bounce(sha, "Ann Lee <ann@example.com>"))

    def test_a_based_on_line_in_the_submitted_body_counts(self):
        _, printed = self.land_one_by("Ann Lee <ann@example.com>", "-m", "port", "-m", "Co-authored-by: Ann Lee <ann@example.com>",
                                      body="Ports the parser.\n\nBased on #12 by @ann-lee\n")
        self.assertEqual(printed, "landed E1 (r/D1)")

    def test_a_submitted_body_without_the_based_on_line_bounces_though_the_commit_has_it(self):
        sha, printed = self.land_one_by("Ann Lee <ann@example.com>", "-m", "port", "-m", "Based on #12 by @ann-lee",
                                        "-m", "Co-authored-by: Ann Lee <ann@example.com>", body="Ports the parser.\n")
        self.assertEqual(printed, self.foreign_bounce(sha, "Ann Lee <ann@example.com>"))

    def test_an_entry_whose_second_commit_has_a_foreign_author_bounces_and_names_that_commit(self):
        self.init(mode="push")
        self.configure_owner()
        self.worker("w1", {"a.txt": "one\n"})
        path = self.base / "w1"
        sh("git", "commit", "-q", "--amend", "--author", "Owner <owner@example.com>", "-m", "first", cwd=path)
        (path / "a.txt").write_text("two\n")
        second = self.commit("second", cwd=path)
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", second, "--lease", "L1", "--reviewer", REVIEWER)
        self.assertEqual(self.land("land"), self.foreign_bounce(second))
        self.assertEqual(self.origin_log(), ["init"])

    def test_an_entry_left_awaiting_merge_without_a_pr_bounces_on_the_next_run_when_its_author_is_foreign(self):
        with self.fake_gh():
            self.require_pr_head_on_origin()
            self.init(mode="human")
            self.queue_one()
            sha = sh("git", "rev-parse", "w1", cwd=self.work)
            (self.base / "pr-create-fails").write_text("")
            self.assertEqual(self.land("land"),
                             "queue paused: gh pr create failed: gh: not authenticated. Fix it, then run land.py resume")
            self.assertEqual(tuple(self.entry_fields(1, "state", "pr")), ("awaiting-merge", ""))
            (self.base / "pr-create-fails").unlink()
            self.configure_owner()
            self.assertEqual(self.land("resume"), "queue resumed")
            self.assertEqual(self.land("land"), self.foreign_bounce(sha))
        self.assertEqual(self.created_heads(), [])
        self.assertFalse((self.base / "prs.json").exists())

    def test_the_first_land_with_an_identity_configured_lands_the_entry_and_stores_the_pair(self):
        self.init(mode="push")
        self.configure_owner()
        self.queue_one_as("Owner", "owner@example.com")
        self.assertEqual(self.land("land"), "landed E1 (r/D1)")
        self.assertEqual(self.stored_identity(), '["Owner", "owner@example.com"]')
