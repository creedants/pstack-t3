from .support import (
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


class LandingTest(LandingTestBase):
    def init(self, mode="auto", batch=1, author_policy="credited", ok=True, **extra):
        """The helper defaults the policy to credited for tests that name none. None omits the flag."""
        args = ["init", "--trunk", extra.get("trunk", "main"), "--mode", mode, "--check", "./check.sh", "--batch", str(batch)]
        if "merge_method" in extra:
            args += ["--merge-method", extra["merge_method"]]
        if "base" in extra:
            args += ["--base", extra["base"]]
        if "generated" in extra:
            args += ["--generated", extra["generated"]]
        if "rebuild" in extra:
            args += ["--rebuild", extra["rebuild"]]
        if author_policy is not None:
            args += ["--author-policy", author_policy]
        return self.land(*args, ok=ok)

    def stored_author_policy(self):
        """The JSON text under the contract key authorPolicy, or None when the key is absent."""
        with self.raw_db() as db:
            row = db.execute("SELECT value FROM contract WHERE key = 'authorPolicy'").fetchone()
        return row and row[0]

    def queue_one_by(self, author, *message, author_policy="credited"):
        """Init push mode with this policy, then queue one commit by this author. Returns the reviewed commit id."""
        self.init(mode="push", author_policy=author_policy)
        self.configure_owner()
        self.worker("w1", {"a.txt": "agent\n"})
        sh("git", "commit", "-q", "--amend", "--author", author, *message, cwd=self.base / "w1")
        sha = sh("git", "rev-parse", "HEAD", cwd=self.base / "w1")
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER)
        return sha

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
        self.assertEqual(keys, ["authorPolicy", "batch", "checks", "commonDir", "generated", "heartbeatMs", "mergeHeartbeatMs", "mergeMethod", "mode", "paused", "prItemLine", "rebuild", "remote",
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

    def test_a_core_worktree_that_names_a_missing_directory_stops_status_land_resume_lease_init_and_submit(self):
        self.init()
        sha = self.worker("w1", {"a.txt": "agent\n"})
        gone = self.base / "gone"
        sh("git", "config", "core.worktree", str(gone), cwd=self.work)
        config = self.work / ".git" / "config"
        before = config.read_bytes()
        commands = (["status"], ["land"], ["resume"], ["lease", "list"],
                    ["init", "--trunk", "main", "--mode", "auto", "--check", "./check.sh"],
                    ["submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER])
        for repo in (self.work, self.base / "w1"):
            for command in commands:
                with self.subTest(repo=repo.name, command=command[0]):
                    self.assertEqual(self.run_land(repo, *command), (1, "", self.stray_worktree_line(gone)))
        self.assertEqual(config.read_bytes(), before)

    def test_a_core_worktree_that_names_a_directory_outside_the_repository_stops_status(self):
        self.init()
        sh("git", "config", "core.worktree", str(self.base), cwd=self.work)
        self.assertEqual(self.run_land(self.work, "status"), (1, "", self.stray_worktree_line(self.base)))

    def test_the_printed_command_quotes_a_config_path_that_has_a_space(self):
        self.work = self.base / "spaced work"
        sh("git", "clone", "-q", "origin.git", "spaced work", cwd=self.base)
        gone = self.base / "gone"
        sh("git", "config", "core.worktree", str(gone), cwd=self.work)
        quoted = "'" + str((self.work / ".git").resolve() / "config") + "'"
        self.assertEqual(self.run_land(self.work, "status"), (1, "", self.stray_worktree_line(gone, quoted)))

    def test_a_core_worktree_that_names_the_checkout_does_not_stop_status(self):
        self.init(author_policy=None)
        for value in (str(self.work), ".."):
            with self.subTest(value=value):
                sh("git", "config", "core.worktree", value, cwd=self.work)
                self.assertEqual(self.run_land(self.work, "status"), (0, "push mode onto refs/remotes/origin/main. empty. author policy: any\n", ""))

    def test_a_relative_core_worktree_that_names_a_missing_directory_stops_status(self):
        self.init()
        sh("git", "config", "core.worktree", "../gone", cwd=self.work)
        for repo in (self.work, self.base):
            with self.subTest(cwd=repo.name):
                self.assertEqual(self.run_land(self.work, "status", cwd=repo), (1, "", self.stray_worktree_line("../gone")))

    def test_the_printed_command_run_in_the_checkout_removes_a_relative_core_worktree(self):
        self.init(author_policy=None)
        sh("git", "config", "core.worktree", "../gone", cwd=self.work)
        command = self.run_land(self.work, "status")[2].rstrip("\n").split("remove it with ", 1)[1]
        subprocess.run(command, shell=True, cwd=self.work, check=True, env=os.environ.copy())
        self.assertEqual(self.run_land(self.work, "status"), (0, "push mode onto refs/remotes/origin/main. empty. author policy: any\n", ""))

    def test_the_last_core_worktree_value_decides(self):
        self.init(author_policy=None)
        gone = self.base / "gone"
        sh("git", "config", "--add", "core.worktree", str(gone), cwd=self.work)
        sh("git", "config", "--file", str(self.work / ".git" / "config"), "--add", "core.worktree", str(self.work), cwd=self.base)
        self.assertEqual(self.run_land(self.work, "status"), (0, "push mode onto refs/remotes/origin/main. empty. author policy: any\n", ""))
        sh("git", "config", "--file", str(self.work / ".git" / "config"), "--add", "core.worktree", str(gone), cwd=self.base)
        self.assertEqual(self.run_land(self.work, "status"), (1, "", self.stray_worktree_line(gone)))

    def test_a_core_worktree_that_is_the_checkout_path_and_a_newline_stops_status_and_lease_claim(self):
        self.init()
        sh("git", "config", "core.worktree", str(self.work) + "\n", cwd=self.work)
        before = self.files_under(self.work / ".git"), self.files_under(self.base / "state")
        for command in (["status"], ["lease", "claim", "--holder", "r/D1", "--paths", "a.txt"]):
            with self.subTest(command=command[0]):
                self.assertEqual(self.run_land(self.work, *command), (1, "", self.stray_worktree_line(str(self.work) + "\\n")))
        self.assertEqual((self.files_under(self.work / ".git"), self.files_under(self.base / "state")), before)
        with self.raw_db() as db:
            self.assertEqual(db.execute("SELECT id FROM lease").fetchall(), [])

    def test_the_printed_command_removes_a_core_worktree_that_is_the_checkout_path_and_a_newline(self):
        self.init(author_policy=None)
        sh("git", "config", "core.worktree", str(self.work) + "\n", cwd=self.work)
        command = self.run_land(self.work, "status")[2].rstrip("\n").split("remove it with ", 1)[1]
        subprocess.run(command, shell=True, cwd=self.work, check=True, env=os.environ.copy())
        self.assertEqual(self.run_land(self.work, "status"), (0, "push mode onto refs/remotes/origin/main. empty. author policy: any\n", ""))

    def test_a_core_worktree_with_a_carriage_return_is_not_judged_as_the_directory_with_a_newline_there(self):
        self.init()
        (self.work / "a\nb").mkdir()
        sh("git", "config", "core.worktree", str(self.work / "a\rb"), cwd=self.work)
        self.assertEqual(self.run_land(self.work, "status"), (1, "", self.stray_worktree_line(str(self.work / "a") + "\\rb")))

    def test_the_core_worktree_line_is_one_line_when_the_value_holds_line_breaks_and_a_backslash(self):
        self.init()
        gone = str(self.base / "gone")
        sh("git", "config", "core.worktree", gone + "\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029\\", cwd=self.work)
        status, printed, complaint = self.run_land(self.work, "status")
        self.assertEqual((status, printed), (1, ""))
        self.assertEqual(complaint, self.stray_worktree_line(gone + r"\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029\\"))
        self.assertEqual(len(complaint.splitlines()), 1)

    def test_a_core_worktree_of_300_letters_stops_status(self):
        self.init()
        sh("git", "config", "core.worktree", "x" * 300, cwd=self.work)
        self.assertEqual(self.run_land(self.work, "status"), (1, "", self.stray_worktree_line("x" * 300)))

    def test_the_printed_command_is_one_line_and_runs_when_the_config_path_holds_a_newline_and_a_backslash(self):
        self.work = self.base / "new\nline\\work"
        sh("git", "clone", "-q", "origin.git", str(self.work), cwd=self.base)
        gone = self.base / "gone"
        sh("git", "config", "core.worktree", str(gone), cwd=self.work)
        top = str(self.base.resolve())
        shown = top + r"/new\nline\\work/.git/config"
        quoted = "\"$(printf %b '" + top + r"/new\0012line\0134work/.git/config" + "')\""
        status, printed, complaint = self.run_land(self.work, "status")
        self.assertEqual((status, printed, complaint), (1, "", self.stray_worktree_line(gone, quoted, shown)))
        self.assertEqual(len(complaint.splitlines()), 1)
        subprocess.run(complaint.rstrip("\n").split("remove it with ", 1)[1], shell=True, cwd=self.work, check=True, env=os.environ.copy())
        self.assertEqual(self.git_raw("config", "--get-all", "core.worktree", cwd=self.work).returncode, 1)

    def test_a_git_directory_whose_name_ends_in_a_space_is_read_under_that_name(self):
        self.work = self.base / "apart"
        store = self.base / "store "
        sh("git", "clone", "-q", "--separate-git-dir", str(store), "origin.git", "apart", cwd=self.base)
        gone = self.base / "gone"
        sh("git", "config", "core.worktree", str(gone), cwd=self.work)
        config = str(store.resolve() / "config")
        self.assertEqual(self.run_land(self.work, "status"), (1, "", self.stray_worktree_line(gone, "'" + config + "'", config)))


class GitEnvironmentFenceTest(unittest.TestCase):
    def test_a_fixture_test_leaves_a_repository_named_by_the_callers_git_variables_unchanged(self):
        for names in (("GIT_DIR", "GIT_WORK_TREE"), ("GIT_DIR",)):
            with self.subTest(leaked=names), tempfile.TemporaryDirectory() as tmp:
                victim = Path(tmp) / "victim"
                subprocess.run(["git", "init", "-q", "-b", "main", str(victim)], check=True)
                subprocess.run(["git", "-c", "user.name=v", "-c", "user.email=v@v", "commit", "-q", "--allow-empty", "-m", "init"],
                               cwd=victim, check=True)
                before = repository_state(victim)
                leaked = {"GIT_DIR": str(victim / ".git"), "GIT_WORK_TREE": str(victim)}
                child = _run(
                    [sys.executable, "-m", "unittest",
                     "tests.landing.test_queue.LandingTest.test_a_conflict_rerere_resolved_to_trunk_bounces"],
                    env={**os.environ, **{name: leaked[name] for name in names}},
                    timeout=300, reason=ProcessReason.INHERITED_ENV)
                self.assertEqual(repository_state(victim), before)
                self.assertEqual(child.returncode, 0, child.stderr)
