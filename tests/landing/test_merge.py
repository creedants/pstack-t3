from .support import (
    setUpModule,
    LandingTest as LandingTestBase,
    json,
    land,
    os,
    ProcessReason,
    real_popen,
    real_process,
    SCRIPT,
    sh,
    subprocess,
    sys,
    time,
)
import types
from unittest import mock


class LandingTest(LandingTestBase):
    def contract(self):
        with land.Store.for_repo(self.work) as store:
            return dict(store.contract)

    def test_merge_mode_reports_a_pr_opened_for_an_entry_left_without_one(self):
        with self.fake_gh():
            (self.base / "checks").write_text("pending")
            self.init(mode="merge")
            self.queue_one()
            self.leave_awaiting_merge_without_a_pr()
            reported = self.land("land")
            self.assertEqual(
                reported,
                "opened PRs that merge when their checks pass: E1 (r/D1) https://github.com/o/r/pull/9")
            self.assertIn("waiting for required checks", self.land("status", "E1"))
            self.assertEqual(self.land("land"), "nothing to land")

    def test_merge_mode_merges_its_own_pr_when_there_are_no_checks_to_wait_for(self):
        with self.fake_gh():
            self.init(mode="merge")
            self.queue_one()
            self.assertIn("opened PRs that merge when their checks pass: E1 (r/D1)", self.land("land"))
            self.age_absence(1)
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertEqual((self.base / "merge-calls").read_text().splitlines(),
                             [self.pinned(9, "--auto", "--merge"), self.pinned(9, "--merge")])
            self.assertFalse(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))
            self.assertFalse(self.ref_exists("refs/remotes/origin/landing/e1", self.work))
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_merge_mode_lets_github_merge_after_required_checks_and_asks_once(self):
        with self.fake_gh():
            (self.base / "required-checks").write_text("")
            self.init(mode="merge")
            self.queue_one()
            self.assertEqual(self.land("land"), "opened PRs that merge when their checks pass: E1 (r/D1) https://github.com/o/r/pull/9")
            self.age_absence(1)
            self.assertEqual(self.land("land"), "nothing to land")
            candidate = sh("git", "rev-parse", "landing/e1", cwd=self.base / "origin.git")
            self.on_origin("update-ref", "refs/heads/main", candidate)
            (self.base / "pr-state").write_text(f"MERGED {candidate} {candidate}")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertEqual(len((self.base / "merge-calls").read_text().splitlines()), 1)

    def test_merge_mode_waits_for_running_checks_then_merges(self):
        with self.fake_gh():
            (self.base / "checks").write_text("pending")
            self.init(mode="merge", merge_method="squash")
            self.queue_one()
            self.assertEqual(self.land("land"), "opened PRs that merge when their checks pass: E1 (r/D1) https://github.com/o/r/pull/9")
            self.assertIn("waiting for required checks", self.land("status", "E1"))
            self.assertTrue(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))
            calls = (self.base / "merge-calls").read_text() if (self.base / "merge-calls").exists() else ""
            self.assertNotIn("pr merge https://github.com/o/r/pull/9 --squash", calls)
            self.assertEqual(self.land("land"), "nothing to land")
            (self.base / "checks").write_text("passed")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertIn("pr merge https://github.com/o/r/pull/9 --squash", (self.base / "merge-calls").read_text())
            self.assertFalse(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))
            self.assertFalse(self.ref_exists("refs/remotes/origin/landing/e1", self.work))

    def test_merge_mode_bounces_a_pr_whose_required_checks_failed(self):
        with self.fake_gh():
            (self.base / "checks").write_text("failed")
            self.init(mode="merge")
            self.queue_one()
            self.assertEqual(self.land("land"), "bounced E1 (r/D1): required checks failed on https://github.com/o/r/pull/9: test (3.12)")
            self.assertTrue(self.land("lease", "list").startswith("L1 active r/D1"))

    def test_merge_mode_does_not_merge_when_auto_would_succeed_with_a_pending_check(self):
        with self.fake_gh():
            (self.base / "checks").write_text("pending")
            (self.base / "auto-merges-immediately").write_text("")
            self.init(mode="merge")
            self.queue_one()
            self.assertEqual(self.land("land"), "opened PRs that merge when their checks pass: E1 (r/D1) https://github.com/o/r/pull/9")
            self.assertIn("waiting for required checks", self.land("status", "E1"))
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))
            self.assertFalse((self.base / "merge-calls").exists())
            self.assertTrue(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))

    def test_merge_mode_bounces_when_a_check_fails_after_auto_merge_was_enabled(self):
        with self.fake_gh():
            (self.base / "required-checks").write_text("")
            self.init(mode="merge")
            self.queue_one()
            self.assertEqual(self.land("land"), "opened PRs that merge when their checks pass: E1 (r/D1) https://github.com/o/r/pull/9")
            self.age_absence(1)
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertIn("merge requested by the queue", self.land("status", "E1"))
            (self.base / "checks").write_text("failed")
            self.assertEqual(self.land("land"), "bounced E1 (r/D1): required checks failed on https://github.com/o/r/pull/9: test (3.12)")
            self.assertTrue(self.land("lease", "list").startswith("L1 active r/D1"))
            self.assertTrue(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))

    def test_merge_mode_does_not_merge_when_the_check_query_fails(self):
        with self.fake_gh():
            (self.base / "checks").write_text("pending")
            (self.base / "checks-query-fails").write_text("")
            self.init(mode="merge")
            self.queue_one()
            out = self.land("land")
            self.assertEqual(
                out,
                "opened PRs that merge when their checks pass: E1 (r/D1) https://github.com/o/r/pull/9\n"
                "queue paused: could not read checks for https://github.com/o/r/pull/9: API unavailable. "
                "Fix it, then run land.py resume")
            self.assertIn("could not read checks", out)
            self.assertIn("queue paused", out)
            self.assertNotIn("landed E1", out)
            self.assertIn("awaiting-merge", self.land("status", "E1"))
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))
            self.assertFalse((self.base / "merge-calls").exists())

    def test_merge_mode_does_not_merge_a_just_opened_pr_with_zero_posted_checks(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text('{"reviewDecision":"","statusCheckRollup":[]}')
            self.init(mode="merge")
            self.queue_one()
            opened = self.land("land")
            self.assertIn("opened PRs that merge when their checks pass: E1 (r/D1)", opened)
            self.assertNotIn("landed", opened)
            self.assertNotIn("queue paused", opened)
            self.assertIn("waiting for required checks", self.land("status", "E1"))
            self.assertNotIn("Paused", self.land("status"))
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))
            self.assertTrue(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))
            self.assertFalse((self.base / "merge-calls").exists())
            self.age_absence(1)
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertEqual((self.base / "merge-calls").read_text().splitlines(),
                             [self.pinned(9, "--auto", "--merge"),
                              self.pinned(9, "--merge")])
            self.assertFalse(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def age_absence(self, *idents):
        """Backdate these entries' absent markers past the grace."""
        with land.Store.for_repo(self.work) as store:
            with store.tx() as db:
                row = db.execute("SELECT value FROM contract WHERE key = 'absentDrain'").fetchone()
                raw = json.loads(row["value"]) if row else {}
                aged = land.stamp(land.now() - land.ABSENT_CHECKS_GRACE - land.timedelta(seconds=1))
                for ident in idents:
                    candidate = db.execute("SELECT candidate FROM entry WHERE id = ?", (ident,)).fetchone()["candidate"]
                    raw[str(ident)] = {"since": aged, "candidate": candidate}
                db.execute("INSERT OR REPLACE INTO contract VALUES ('absentDrain', ?)", (json.dumps(raw),))

    def test_merge_mode_does_not_merge_absent_checks_on_a_later_drain(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text('{"reviewDecision":"","statusCheckRollup":[]}')
            self.init(mode="merge")
            self.queue_one()
            opened = self.land("land")
            self.assertIn("opened PRs that merge when their checks pass: E1 (r/D1)", opened)
            self.assertNotIn("landed", opened)
            for _ in range(2):
                self.assertEqual(self.land("land"), "nothing to land")
                self.assertIn("waiting for required checks", self.land("status", "E1"))
            self.assertFalse((self.base / "merge-calls").exists())

    def test_merge_mode_merges_absent_checks_after_the_grace(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text('{"reviewDecision":"","statusCheckRollup":[]}')
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            self.age_absence(1)
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")

    def test_merge_mode_lands_a_merged_pr_when_a_later_check_fails(self):
        with self.fake_gh():
            (self.base / "required-checks").write_text("")
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            self.age_absence(1)
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertIn("merge requested by the queue", self.land("status", "E1"))
            candidate = sh("git", "rev-parse", "landing/e1", cwd=self.base / "origin.git")
            self.on_origin("update-ref", "refs/heads/main", candidate)
            (self.base / "pr-state").write_text(f"MERGED {candidate} {candidate}")
            (self.base / "checks").write_text("failed")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertFalse(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))
            self.assertEqual(self.land("lease", "list"), "no leases held")
            self.assertIn("E1 landed", self.land("status", "E1"))

    def test_merge_mode_pauses_on_a_merge_conflict_when_auto_merge_is_disabled(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text('{"reviewDecision":"","statusCheckRollup":[]}')
            (self.base / "auto-merge-disabled").write_text("")
            (self.base / "plain-merge-fails").write_text("merge conflict")
            self.init(mode="merge")
            self.queue_one()
            opened = self.land("land")
            self.assertIn("opened PRs that merge when their checks pass: E1 (r/D1)", opened)
            self.assertNotIn("queue paused", opened)
            self.assertFalse((self.base / "merge-calls").exists())
            self.age_absence(1)
            paused = self.land("land")
            self.assertIn("queue paused", paused)
            self.assertIn("merge conflict", paused)
            self.assertNotIn("landed", paused)
            self.assertIn("Paused", self.land("status"))
            self.assertIn("merge conflict", self.land("status"))
            calls = [
                self.pinned(9, "--auto", "--merge"),
                self.pinned(9, "--merge"),
            ]
            self.assertEqual((self.base / "merge-calls").read_text().splitlines(), calls)
            self.assertIn("queue paused", self.land("land"))
            self.assertEqual((self.base / "merge-calls").read_text().splitlines(), calls)

    def test_merge_mode_waits_when_auto_merge_is_disabled_and_policy_refuses(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text('{"reviewDecision":"","statusCheckRollup":[]}')
            (self.base / "auto-merge-disabled").write_text("")
            (self.base / "policy-until-passed").write_text("")
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            self.age_absence(1)
            still = self.land("land")
            self.assertNotIn("queue paused", still)
            self.assertNotIn("landed", still)
            self.assertIn("waiting for required checks", self.land("status", "E1"))
            self.assertNotIn("Paused", self.land("status"))
            calls = (self.base / "merge-calls").read_text()
            self.assertIn("pr merge https://github.com/o/r/pull/9 --auto --merge", calls)
            self.assertIn("pr merge https://github.com/o/r/pull/9 --merge", calls)
            (self.base / "pr-checks.json").unlink()
            (self.base / "auto-merge-disabled").unlink()
            (self.base / "checks").write_text("passed")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")

    def test_merge_mode_waits_when_base_branch_policy_refuses_and_no_check_is_posted(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text('{"reviewDecision":"","statusCheckRollup":[]}')
            (self.base / "policy-until-passed").write_text("")
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            self.assertFalse((self.base / "auto-merge-disabled").exists())
            still_empty = self.land("land")
            self.assertNotIn("queue paused", still_empty)
            self.assertNotIn("landed", still_empty)
            self.assertIn("waiting for required checks", self.land("status", "E1"))
            (self.base / "pr-checks.json").unlink()
            (self.base / "checks").write_text("passed")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")

    def test_merge_mode_waits_when_policy_refuses_and_a_required_check_is_unposted(self):
        with self.fake_gh():
            parts = [
                {"name": f"part {number}", "status": "COMPLETED", "conclusion": "SUCCESS"}
                for number in range(1, 9)
            ]
            (self.base / "pr-checks.json").write_text(
                json.dumps({"reviewDecision": "", "statusCheckRollup": parts}))
            (self.base / "policy-until-passed").write_text("")
            (self.base / "protection-required.json").write_text(json.dumps({
                "contexts": ["test (3.10)", "test (3.12)"],
                "checks": [{"context": "part 1", "app_id": 1}],
            }))
            self.init(mode="merge")
            self.queue_one()
            first = self.land("land")
            self.assertIn("opened PRs that merge when their checks pass: E1 (r/D1)", first)
            self.assertNotIn("queue paused", first)
            self.assertNotIn("landed", first)
            with land.Store.for_repo(self.work) as store:
                self.assertEqual(store.contract.get("paused"), "")
            self.assertIn("waiting for required checks", self.land("status", "E1"))
            self.assertNotIn("Paused", self.land("status"))
            self.assertIn("protection/required_status_checks", (self.base / "gh-calls").read_text())
            (self.base / "pr-checks.json").unlink()
            (self.base / "checks").write_text("passed")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            with land.Store.for_repo(self.work) as store:
                self.assertEqual(store.contract.get("paused"), "")

    def test_merge_mode_pauses_when_policy_refuses_and_every_required_check_is_posted(self):
        with self.fake_gh():
            posted = [
                {"name": "test (3.10)", "status": "COMPLETED", "conclusion": "SUCCESS"},
                {"context": "test (3.12)", "status": "COMPLETED", "conclusion": "SUCCESS"},
            ]
            (self.base / "pr-checks.json").write_text(
                json.dumps({"reviewDecision": "", "statusCheckRollup": posted}))
            (self.base / "policy-until-passed").write_text("")
            (self.base / "protection-required.json").write_text(json.dumps({
                "contexts": ["test (3.10)", "test (3.12)"],
                "checks": [{"context": "ghost", "app_id": 1}],
            }))
            self.init(mode="merge")
            self.queue_one()
            first = self.land("land")
            self.assertIn("queue paused", first)
            self.assertIn("the base branch policy prohibits the merge", first)
            self.assertNotIn("landed", first)
            with land.Store.for_repo(self.work) as store:
                self.assertIn(
                    "the base branch policy prohibits the merge",
                    store.contract["paused"])
            second = self.land("land")
            self.assertIn("queue paused", second)
            self.assertNotIn("landed", second)

    def test_merge_mode_pauses_when_policy_refuses_and_required_names_are_unreadable(self):
        with self.fake_gh():
            parts = [
                {"name": f"part {number}", "status": "COMPLETED", "conclusion": "SUCCESS"}
                for number in range(1, 9)
            ]
            (self.base / "pr-checks.json").write_text(
                json.dumps({"reviewDecision": "", "statusCheckRollup": parts}))
            (self.base / "policy-until-passed").write_text("")
            self.init(mode="merge")
            self.queue_one()
            first = self.land("land")
            self.assertIn("queue paused", first)
            self.assertIn("the base branch policy prohibits the merge", first)
            self.assertNotIn("landed", first)
            with land.Store.for_repo(self.work) as store:
                self.assertIn(
                    "the base branch policy prohibits the merge",
                    store.contract["paused"])
            second = self.land("land")
            self.assertIn("queue paused", second)

    def test_merge_mode_pauses_when_policy_refuses_and_required_contexts_are_empty(self):
        with self.fake_gh():
            parts = [
                {"name": f"part {number}", "status": "COMPLETED", "conclusion": "SUCCESS"}
                for number in range(1, 9)
            ]
            (self.base / "pr-checks.json").write_text(
                json.dumps({"reviewDecision": "", "statusCheckRollup": parts}))
            (self.base / "policy-until-passed").write_text("")
            (self.base / "protection-required.json").write_text(json.dumps({"contexts": []}))
            self.init(mode="merge")
            self.queue_one()
            first = self.land("land")
            self.assertIn("queue paused", first)
            self.assertIn("the base branch policy prohibits the merge", first)
            self.assertNotIn("landed", first)
            with land.Store.for_repo(self.work) as store:
                self.assertIn(
                    "the base branch policy prohibits the merge",
                    store.contract["paused"])
            second = self.land("land")
            self.assertIn("queue paused", second)
            self.assertNotIn("landed", second)

    def test_merge_mode_waits_when_only_protection_requires_an_unposted_check(self):
        with self.fake_gh():
            parts = [
                {"name": f"part {number}", "status": "COMPLETED", "conclusion": "SUCCESS"}
                for number in range(1, 9)
            ]
            (self.base / "pr-checks.json").write_text(
                json.dumps({"reviewDecision": "", "statusCheckRollup": parts}))
            (self.base / "policy-until-passed").write_text("")
            (self.base / "protection-required.json").write_text(json.dumps({
                "contexts": ["test (3.12)"],
            }))
            self.init(mode="merge")
            self.queue_one()
            first = self.land("land")
            self.assertNotIn("queue paused", first)
            self.assertNotIn("landed", first)
            self.assertIn("waiting for required checks", self.land("status", "E1"))
            calls = (self.base / "gh-calls").read_text()
            self.assertIn("protection/required_status_checks", calls)
            self.assertIn("rules/branches", calls)

    def test_merge_mode_waits_when_only_rulesets_require_an_unposted_check(self):
        with self.fake_gh():
            parts = [
                {"name": f"part {number}", "status": "COMPLETED", "conclusion": "SUCCESS"}
                for number in range(1, 9)
            ]
            (self.base / "pr-checks.json").write_text(
                json.dumps({"reviewDecision": "", "statusCheckRollup": parts}))
            (self.base / "policy-until-passed").write_text("")
            (self.base / "rules-required.json").write_text(json.dumps([
                {"type": "required_status_checks",
                 "parameters": {"required_status_checks": [{"context": "test (3.12)"}]}},
            ]))
            self.init(mode="merge")
            self.queue_one()
            first = self.land("land")
            self.assertNotIn("queue paused", first)
            self.assertNotIn("landed", first)
            self.assertIn("waiting for required checks", self.land("status", "E1"))
            calls = (self.base / "gh-calls").read_text()
            self.assertIn("rules/branches", calls)

    def test_merge_mode_waits_when_union_holds_unposted_ruleset_check(self):
        with self.fake_gh():
            posted = [
                {"name": "test (3.10)", "status": "COMPLETED", "conclusion": "SUCCESS"},
            ]
            (self.base / "pr-checks.json").write_text(
                json.dumps({"reviewDecision": "", "statusCheckRollup": posted}))
            (self.base / "policy-until-passed").write_text("")
            (self.base / "protection-required.json").write_text(json.dumps({
                "contexts": ["test (3.10)"],
            }))
            (self.base / "rules-required.json").write_text(json.dumps([
                {"type": "required_status_checks",
                 "parameters": {"required_status_checks": [{"context": "test (3.12)"}]}},
            ]))
            self.init(mode="merge")
            self.queue_one()
            first = self.land("land")
            self.assertNotIn("queue paused", first)
            self.assertNotIn("landed", first)
            self.assertIn("waiting for required checks", self.land("status", "E1"))
            calls = (self.base / "gh-calls").read_text()
            self.assertIn("protection/required_status_checks", calls)
            self.assertIn("rules/branches", calls)

    def test_merge_mode_pauses_when_neither_source_is_readable(self):
        with self.fake_gh():
            parts = [
                {"name": f"part {number}", "status": "COMPLETED", "conclusion": "SUCCESS"}
                for number in range(1, 9)
            ]
            (self.base / "pr-checks.json").write_text(
                json.dumps({"reviewDecision": "", "statusCheckRollup": parts}))
            (self.base / "policy-until-passed").write_text("")
            self.init(mode="merge")
            self.queue_one()
            first = self.land("land")
            self.assertIn("queue paused", first)
            self.assertIn("the base branch policy prohibits the merge", first)
            calls = (self.base / "gh-calls").read_text()
            self.assertIn("protection/required_status_checks", calls)
            self.assertIn("rules/branches", calls)

    def test_ruleset_contexts_finds_required_check_only_on_second_page(self):
        first = [{"type": "other"} for _ in range(100)]
        second = [{"type": "required_status_checks",
                   "parameters": {"required_status_checks": [{"context": "test (3.12)"}]}}]

        def fake_gh(*args, cwd=None):
            endpoint = args[1] if len(args) > 1 else ""
            if "page=2" in endpoint:
                return types.SimpleNamespace(returncode=0, stdout=json.dumps(second), stderr="")
            return types.SimpleNamespace(returncode=0, stdout=json.dumps(first), stderr="")

        store = types.SimpleNamespace(contract={"trunk": "main"}, repo=str(self.work))
        with mock.patch.object(land, "gh", side_effect=fake_gh):
            self.assertEqual(land._ruleset_contexts(store), ("test (3.12)",))

    def test_ruleset_contexts_returns_none_when_second_page_unreadable(self):
        first = [{"type": "other"} for _ in range(100)]

        def fake_gh(*args, cwd=None):
            endpoint = args[1] if len(args) > 1 else ""
            if "page=2" in endpoint:
                return types.SimpleNamespace(returncode=1, stdout="", stderr="unavailable")
            return types.SimpleNamespace(returncode=0, stdout=json.dumps(first), stderr="")

        store = types.SimpleNamespace(contract={"trunk": "main"}, repo=str(self.work))
        with mock.patch.object(land, "gh", side_effect=fake_gh):
            self.assertIsNone(land._ruleset_contexts(store))

    def test_ruleset_contexts_returns_none_when_every_page_is_full(self):
        full = [{"type": "other"} for _ in range(100)]
        calls = []

        def fake_gh(*args, cwd=None):
            calls.append(args[1] if len(args) > 1 else "")
            return types.SimpleNamespace(returncode=0, stdout=json.dumps(full), stderr="")

        store = types.SimpleNamespace(contract={"trunk": "main"}, repo=str(self.work))
        with mock.patch.object(land, "gh", side_effect=fake_gh):
            self.assertIsNone(land._ruleset_contexts(store))
        self.assertEqual(len(calls), land.RULESET_MAX_PAGES)

    def test_merge_mode_lands_a_green_rollup_whose_check_name_contains_a_quote(self):
        with self.fake_gh():
            posted = [
                {"name": "b\"q\\\t\n", "status": "COMPLETED", "conclusion": "SUCCESS"},
            ]
            (self.base / "pr-checks.json").write_text(
                json.dumps({"reviewDecision": "", "statusCheckRollup": posted}))
            self.init(mode="merge")
            self.queue_one()
            first = self.land("land")
            self.assertNotIn("queue paused", first)
            self.assertIn("landed E1 (r/D1)", first)
            with land.Store.for_repo(self.work) as store:
                self.assertEqual(store.contract.get("paused"), "")
            self.assertEqual(self.land("land"), "nothing to land")

    def test_merge_mode_does_not_merge_when_a_pending_check_appears_between_reads(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text('{"reviewDecision":"APPROVED","statusCheckRollup":[]}')
            (self.base / "checks-flip").write_text("pending")
            self.init(mode="merge")
            self.queue_one()
            opened = self.land("land")
            self.assertNotIn("landed", opened)
            self.assertNotIn("queue paused", opened)
            self.assertFalse((self.base / "merge-calls").exists())
            self.assertIn("waiting for required checks", self.land("status", "E1"))
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))

    def test_merge_mode_does_not_merge_when_a_failed_check_appears_between_reads(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text('{"reviewDecision":"APPROVED","statusCheckRollup":[]}')
            (self.base / "checks-flip").write_text("failed")
            self.arm_auto_merge()
            self.init(mode="merge")
            self.queue_one()
            opened = self.land("land")
            self.assertNotIn("landed", opened)
            self.assertEqual((self.base / "merge-calls").read_text().strip(),
                             "pr merge https://github.com/o/r/pull/9 --disable-auto")
            self.assertIn("bounced E1 (r/D1): required checks failed on https://github.com/o/r/pull/9: test (3.12)", opened)
            self.assertTrue(self.land("lease", "list").startswith("L1 active"))

    def assert_absent(self, *idents):
        """These entries hold absent markers for their stored candidates."""
        marker = self.absent_marker()
        self.assertEqual(set(marker), {str(ident) for ident in idents})
        for ident in idents:
            self.assertEqual(set(marker[str(ident)]), {"since", "candidate"})
            self.assertEqual(marker[str(ident)]["candidate"], self.entry_fields(ident, "candidate")[0])

    def open_pr_with_a_merge_requested(self):
        """One merge-mode entry noted merge requested by the queue, with its PR open.

        This one ages the absence before the second land."""
        (self.base / "required-checks").write_text("")
        self.init(mode="merge")
        self.queue_one()
        self.land("land")
        self.age_absence(1)
        self.assertEqual(self.land("land"), "nothing to land")
        self.assertIn("merge requested by the queue", self.land("status", "E1"))

    def test_merge_mode_drops_the_absent_marker_when_the_entry_lands(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text('{"reviewDecision":"","statusCheckRollup":[]}')
            self.init(mode="merge")
            self.queue_one()
            opened = self.land("land")
            self.assertIn("opened PRs that merge when their checks pass: E1 (r/D1)", opened)
            self.assert_absent(1)
            self.age_absence(1)
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertNotIn("1", self.absent_marker())

    def test_merge_mode_drops_the_absent_marker_when_the_entry_bounces(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text('{"reviewDecision":"","statusCheckRollup":[]}')
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            self.assert_absent(1)
            (self.base / "pr-checks.json").unlink()
            (self.base / "checks").write_text("failed")
            self.assertIn("bounced E1 (r/D1): required checks failed on https://github.com/o/r/pull/9: test (3.12)", self.land("land"))
            self.assertNotIn("1", self.absent_marker())
            self.assertTrue(self.land("lease", "list").startswith("L1 active"))

    def test_a_checked_out_queue_branch_is_named_when_land_cannot_delete_it(self):
        with self.fake_gh():
            self.init(mode="human")
            self.queue_one()
            self.land("land")
            candidate = sh("git", "rev-parse", "landing/e1", cwd=self.base / "origin.git")
            sh("git", "branch", "landing/e1", candidate, cwd=self.work)
            sh("git", "checkout", "-q", "landing/e1", cwd=self.work)
            (self.base / "pr-state").write_text(f"MERGED {candidate} abc123")
            out = self.land("land")
            self.assertIn("landed E1 (r/D1)", out)
            self.assertIn("left local landing/e1", out)
            self.assertTrue(self.ref_exists("refs/heads/landing/e1", self.work))
            self.assertFalse(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))
            self.assertIn("E1 landed", self.land("status", "E1"))

    def test_merge_mode_lands_when_the_remote_deletes_the_queue_branch_during_push(self):
        with self.fake_gh():
            self.install_racing_branch_delete()
            self.init(mode="merge")
            self.queue_one()
            opened = self.land("land")
            self.assertIn("opened PRs that merge when their checks pass: E1 (r/D1)", opened)
            self.age_absence(1)
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertIn("refs/heads/landing/e1", (self.base / "race-deleted").read_text())
            self.assertEqual(self.land("lease", "list"), "no leases held")
            self.assertFalse(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))
            self.assertFalse(self.ref_exists("refs/remotes/origin/landing/e1", self.work))
            self.assertIn("branch-status 404", (self.base / "gh-calls").read_text())

    def test_merge_mode_leaves_the_entry_when_the_queue_branch_delete_fails(self):
        with self.fake_gh():
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            sh("git", "config", "receive.denyDeletes", "true", cwd=self.base / "origin.git")
            self.age_absence(1)
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertIn("awaiting-merge", self.land("status", "E1"))
            self.assertIn("merge requested by the queue", self.land("status", "E1"))
            self.assertTrue(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))
            self.assertIn("branch-status 200", (self.base / "gh-calls").read_text())
            sh("git", "config", "--unset", "receive.denyDeletes", cwd=self.base / "origin.git")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_merge_mode_leaves_the_entry_when_a_hidden_ref_refuses_deletion(self):
        with self.fake_gh():
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            remote = self.base / "origin.git"
            sh("git", "config", "receive.denyDeletes", "true", cwd=remote)
            sh("git", "config", "uploadpack.hideRefs", "refs/heads/landing/e1", cwd=remote)
            listed = subprocess.run(["git", "ls-remote", "origin", "refs/heads/landing/e1"],
                                    cwd=self.work, capture_output=True, text=True)
            self.assertEqual((listed.returncode, listed.stdout.strip()), (0, ""))
            self.age_absence(1)
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertTrue(self.ref_exists("refs/heads/landing/e1", remote))
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))
            self.assertIn("awaiting-merge", self.land("status", "E1"))
            self.assertIn("branch-status 200", (self.base / "gh-calls").read_text())

    def test_merge_mode_leaves_the_entry_when_a_hidden_ref_is_locked(self):
        with self.fake_gh():
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            remote = self.base / "origin.git"
            sh("git", "config", "uploadpack.hideRefs", "refs/heads/landing/e1", cwd=remote)
            (remote / "refs/heads/landing/e1.lock").write_text("x")
            listed = subprocess.run(["git", "ls-remote", "origin", "refs/heads/landing/e1"],
                                    cwd=self.work, capture_output=True, text=True)
            self.assertEqual((listed.returncode, listed.stdout.strip()), (0, ""))
            self.age_absence(1)
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertTrue(self.ref_exists("refs/heads/landing/e1", remote))
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))
            self.assertIn("awaiting-merge", self.land("status", "E1"))
            self.assertIn("branch-status 200", (self.base / "gh-calls").read_text())

    def test_merge_mode_leaves_the_entry_when_receive_pack_hides_the_queue_branch(self):
        with self.fake_gh():
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            remote = self.base / "origin.git"
            sh("git", "config", "receive.hideRefs", "refs/heads/landing/e1", cwd=remote)
            self.age_absence(1)
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertTrue(self.ref_exists("refs/heads/landing/e1", remote))
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))
            self.assertIn("awaiting-merge", self.land("status", "E1"))
            self.assertIn("branch-status 200", (self.base / "gh-calls").read_text())

    def test_an_annotated_tag_stays_local_when_the_queue_pushes(self):
        with self.fake_gh():
            self.init(mode="merge")
            self.queue_one()
            sh("git", "tag", "-a", "private-local-tag", "-m", "local tag", cwd=self.work)
            sh("git", "config", "push.followTags", "true", cwd=self.work)
            log = self.record_pushes()
            try:
                self.assertIn("opened PRs that merge when their checks pass: E1 (r/D1)", self.land("land"))
                self.merged_queue_branch()
                self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            finally:
                self.stop_recording_pushes()
            remote = self.base / "origin.git"
            self.assert_pushes_stay_on_their_refs(log)
            self.assertFalse(self.ref_exists("refs/tags/private-local-tag", remote))
            self.assertNotIn("branch-status", (self.base / "gh-calls").read_text())
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_push_mode_keeps_an_annotated_tag_local(self):
        self.init(mode="push")
        self.queue_one()
        sh("git", "tag", "-a", "private-local-tag", "-m", "local tag", cwd=self.work)
        sh("git", "config", "push.followTags", "true", cwd=self.work)
        log = self.record_pushes()
        try:
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
        finally:
            self.stop_recording_pushes()
        self.assert_pushes_stay_on_their_refs(log)
        self.assertFalse(self.ref_exists("refs/tags/private-local-tag", self.base / "origin.git"))

    def test_merge_mode_settles_when_the_forge_reports_the_branch_gone(self):
        with self.fake_gh():
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            remote = self.base / "origin.git"
            self.merged_queue_branch()
            sh("git", "update-ref", "-d", "refs/heads/landing/e1", cwd=remote)
            sh("git", "tag", "-a", "private-local-tag", "-m", "local tag", cwd=self.work)
            sh("git", "config", "push.followTags", "true", cwd=self.work)
            log = self.record_pushes()
            try:
                self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            finally:
                self.stop_recording_pushes()
            self.assertEqual(
                log.read_text().splitlines(),
                ["push --no-follow-tags origin --delete landing/e1"])
            self.assertFalse(self.ref_exists("refs/tags/private-local-tag", remote))
            self.assertIn("branch-status 404", (self.base / "gh-calls").read_text())
            self.assertIn(
                "api --include repos/{owner}/{repo}/branches/landing/e1",
                (self.base / "gh-calls").read_text())
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_merge_mode_leaves_the_entry_when_the_branch_query_fails(self):
        with self.fake_gh():
            self.open_pr_with_a_merge_requested()
            remote = self.base / "origin.git"
            self.merged_queue_branch()
            sh("git", "update-ref", "-d", "refs/heads/landing/e1", cwd=remote)
            (self.base / "branch-query-fails").write_text("x")
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertIn("branch-status 500", (self.base / "gh-calls").read_text())
            self.assertIn("awaiting-merge", self.land("status", "E1"))
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))
            self.assertFalse(self.ref_exists("refs/heads/landing/e1", remote))

    def test_merge_mode_leaves_the_entry_when_another_push_url_dropped_the_branch(self):
        with self.fake_gh():
            self.open_pr_with_a_merge_requested()
            remote = self.base / "origin.git"
            backup = self.base / "backup.git"
            ref = "refs/heads/landing/e1"
            sha = self.merged_queue_branch()
            sh("git", "checkout", "--detach", "-q", sha, cwd=self.work)
            sh("git", "clone", "--bare", "-q", str(remote), str(backup), cwd=self.base)
            sh("git", "update-ref", "-d", ref, cwd=backup)
            (remote / "refs/heads/landing/e1.lock").write_text("x")
            sh("git", "config", "--add", "remote.origin.pushurl", str(remote), cwd=self.work)
            sh("git", "config", "--add", "remote.origin.pushurl", str(backup), cwd=self.work)
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertTrue(self.ref_exists(ref, remote))
            self.assertFalse(self.ref_exists(ref, backup))
            self.assertNotIn("branches/", (self.base / "gh-calls").read_text())
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))
            self.assertIn("awaiting-merge", self.land("status", "E1"))

    def test_merge_mode_leaves_the_entry_when_the_other_push_url_keeps_the_branch(self):
        with self.fake_gh():
            self.open_pr_with_a_merge_requested()
            origin = self.base / "origin.git"
            backup = self.base / "backup.git"
            ref = "refs/heads/landing/e1"
            sha = self.merged_queue_branch()
            sh("git", "checkout", "--detach", "-q", sha, cwd=self.work)
            sh("git", "clone", "--bare", "-q", str(origin), str(backup), cwd=self.base)
            sh("git", "update-ref", "-d", ref, cwd=origin)
            lock = backup / "refs/heads/landing/e1.lock"
            lock.parent.mkdir(parents=True, exist_ok=True)
            lock.write_text("x")
            sh("git", "config", "--add", "remote.origin.pushurl", str(origin), cwd=self.work)
            sh("git", "config", "--add", "remote.origin.pushurl", str(backup), cwd=self.work)
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertFalse(self.ref_exists(ref, origin))
            self.assertTrue(self.ref_exists(ref, backup))
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))
            self.assertIn("awaiting-merge", self.land("status", "E1"))

    def test_merge_mode_leaves_the_entry_when_the_push_url_names_another_repo(self):
        with self.fake_gh():
            self.open_pr_with_a_merge_requested()
            remote = self.base / "origin.git"
            self.merged_queue_branch()
            sh("git", "remote", "set-url", "origin", "https://github.com/else/where.git", cwd=self.work)
            sh("git", "config", "receive.denyDeletes", "true", cwd=remote)
            (self.base / "forge-says-missing").write_text("x")
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertTrue(self.ref_exists("refs/heads/landing/e1", remote))
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))
            self.assertIn("awaiting-merge", self.land("status", "E1"))
            self.assertNotIn("branches/", (self.base / "gh-calls").read_text())

    def test_merge_mode_leaves_the_entry_when_the_forge_repo_cannot_be_read(self):
        with self.fake_gh():
            self.open_pr_with_a_merge_requested()
            remote = self.base / "origin.git"
            self.merged_queue_branch()
            sh("git", "update-ref", "-d", "refs/heads/landing/e1", cwd=remote)
            (self.base / "repo-view-fails").write_text("x")
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertFalse(self.ref_exists("refs/heads/landing/e1", remote))
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))
            self.assertIn("awaiting-merge", self.land("status", "E1"))

    def test_push_mode_settles_a_missing_branch_only_from_gits_absent_line(self):
        with self.fake_gh():
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            with land.Store.for_repo(self.work) as store:
                with store.tx() as db:
                    db.execute("INSERT OR REPLACE INTO contract VALUES ('mode', ?)", (json.dumps("push"),))
            with land.Store.for_repo(self.work) as store:
                self.assertEqual(store.contract["mode"], "push")
                remote = self.base / "origin.git"
                self.merged_queue_branch()
                sh("git", "update-ref", "-d", "refs/heads/landing/e1", cwd=remote)
                sh("git", "tag", "-a", "private-local-tag", "-m", "local tag", cwd=self.work)
                sh("git", "config", "push.followTags", "true", cwd=self.work)
                (self.base / "branch-query-fails").write_text("x")
                entry = store.entries("awaiting-merge")[0]
                log = self.record_pushes()
                try:
                    landed, bounced = [], []
                    self.assertTrue(land.take_pr(store, entry, landed, bounced))
                finally:
                    self.stop_recording_pushes()
                self.assertEqual(landed, [entry["id"]])
                self.assertEqual(bounced, [])
                self.assertEqual(
                    log.read_text().splitlines(),
                    ["push --no-follow-tags origin --delete landing/e1"])
                self.assertNotIn("branches/", (self.base / "gh-calls").read_text())
                self.assertFalse(self.ref_exists("refs/tags/private-local-tag", remote))
            self.assertEqual(self.land_in_another_process("status", "E1").splitlines()[0].split(". ")[0], "E1 landed (r/D1, w1)")
            self.assertEqual(self.land_in_another_process("lease", "list"), "no leases held")

    def test_merge_mode_lands_in_the_run_whose_plain_merge_succeeds(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text('{"reviewDecision":"","statusCheckRollup":[]}')
            (self.base / "merge-state-lags").write_text("")
            self.init(mode="merge", merge_method="squash")
            self.queue_one()
            opened = self.land("land")
            self.assertIn("opened PRs that merge when their checks pass: E1 (r/D1)", opened)
            self.assertNotIn("landed", opened)
            self.age_absence(1)
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertEqual(self.land("lease", "list"), "no leases held")
            self.assertFalse(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))
            self.assertFalse(self.ref_exists("refs/remotes/origin/landing/e1", self.work))
            self.assertIn("pr merge https://github.com/o/r/pull/9 --squash", (self.base / "merge-calls").read_text())

    def test_merge_mode_lands_in_the_run_whose_auto_merge_completes(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text('{"reviewDecision":"","statusCheckRollup":[]}')
            self.init(mode="merge", merge_method="squash")
            self.queue_one()
            self.land("land")
            (self.base / "auto-merges-immediately").write_text("")
            (self.base / "merge-state-lags").write_text("")
            (self.base / "pr-checks.json").unlink()
            (self.base / "checks").write_text("passed")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertEqual(self.land("lease", "list"), "no leases held")
            self.assertFalse(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))
            calls = (self.base / "merge-calls").read_text().splitlines()
            self.assertEqual(calls[-1], self.pinned(9, "--auto", "--squash"))

    def test_merge_mode_reports_a_merge_that_lands_after_the_opening_state_read(self):
        with self.fake_gh():
            (self.base / "required-checks").write_text("")
            self.init(mode="merge")
            self.queue_one()
            self.assertIn("opened PRs that merge when their checks pass: E1 (r/D1)", self.land("land"))
            self.age_absence(1)
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertIn("merge requested by the queue", self.land("status", "E1"))
            candidate = sh("git", "rev-parse", "landing/e1", cwd=self.base / "origin.git")
            (self.base / "pr-state").write_text(f"OPEN {candidate} ")
            self.on_origin("update-ref", "refs/heads/main", candidate)
            (self.base / "pr-state-after").write_text(f"MERGED {candidate} {candidate}")
            (self.base / "checks").write_text("passed")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertEqual(self.land("lease", "list"), "no leases held")
            self.assertFalse(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))

    def test_merge_mode_pauses_for_review_without_calling_auto_merge(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text(
                '{"reviewDecision":"REVIEW_REQUIRED","statusCheckRollup":'
                '[{"name":"test (3.12)","status":"COMPLETED","conclusion":"SUCCESS"}]}')
            (self.base / "required-checks").write_text("")
            self.init(mode="merge")
            self.queue_one()
            out = self.land("land")
            self.assertIn("queue paused", out)
            self.assertIn("https://github.com/o/r/pull/9", out)
            self.assertIn("approving review", out)
            self.assertNotIn("landed", out)
            calls = (self.base / "merge-calls").read_text() if (self.base / "merge-calls").exists() else ""
            self.assertNotIn("--auto", calls.split())
            self.assertIn("awaiting-merge", self.land("status", "E1"))
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))

    def test_merge_mode_pauses_when_changes_are_requested_without_calling_auto_merge(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text(
                '{"reviewDecision":"CHANGES_REQUESTED","statusCheckRollup":[]}')
            (self.base / "required-checks").write_text("")
            self.init(mode="merge")
            self.queue_one()
            out = self.land("land")
            self.assertIn("queue paused", out)
            self.assertIn("https://github.com/o/r/pull/9", out)
            self.assertIn("changes requested", out)
            calls = (self.base / "merge-calls").read_text() if (self.base / "merge-calls").exists() else ""
            self.assertNotIn("--auto", calls.split())

    def test_merge_mode_disables_auto_merge_when_it_bounces_an_open_pr(self):
        with self.fake_gh():
            (self.base / "required-checks").write_text("")
            self.arm_auto_merge()
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            self.age_absence(1)
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertIn("merge requested by the queue", self.land("status", "E1"))
            (self.base / "checks").write_text("failed")
            out = self.land("land")
            self.assertIn("bounced E1 (r/D1): required checks failed on https://github.com/o/r/pull/9: test (3.12)", out)
            self.assertIn("pr merge https://github.com/o/r/pull/9 --disable-auto", (self.base / "merge-calls").read_text())
            self.assertNotIn("pr comment", (self.base / "gh-calls").read_text())
            self.assertNotIn("pr close", (self.base / "gh-calls").read_text())
            self.assertTrue(self.land("lease", "list").startswith("L1 active r/D1"))
            self.assertTrue(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))

    def test_merge_mode_pauses_when_it_cannot_disable_auto_merge_on_a_failed_pr(self):
        with self.fake_gh():
            (self.base / "required-checks").write_text("")
            (self.base / "disable-auto-fails").write_text("API unavailable")
            self.arm_auto_merge()
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            self.age_absence(1)
            self.land("land")
            (self.base / "checks").write_text("failed")
            self.assertEqual(self.land("land"), "queue paused: required checks failed on https://github.com/o/r/pull/9: test (3.12). "
                                                "Auto-merge is still enabled: API unavailable. Turn auto-merge off on https://github.com/o/r/pull/9, "
                                                "then run land.py resume and land.py land")
            self.assertTrue(self.land("status", "E1").startswith("E1 awaiting-merge"))
            self.assertNotIn("pr close", (self.base / "gh-calls").read_text())
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))
            (self.base / "auto-merge-request.json").unlink()
            self.land("resume")
            self.assertIn("bounced E1 (r/D1): required checks failed on https://github.com/o/r/pull/9: test (3.12)", self.land("land"))
            self.assertTrue(self.land("lease", "list").startswith("L1 active"))

    def test_merge_mode_skips_disable_auto_when_auto_merge_request_is_null(self):
        with self.fake_gh():
            (self.base / "checks").write_text("failed")
            (self.base / "auto-merge-request.json").write_text("null")
            self.init(mode="merge")
            self.queue_one()
            self.assertEqual(self.land("land"), "bounced E1 (r/D1): required checks failed on https://github.com/o/r/pull/9: test (3.12)")
            calls = (self.base / "merge-calls").read_text() if (self.base / "merge-calls").exists() else ""
            self.assertNotIn("--disable-auto", calls)
            self.assertNotIn("pr close", (self.base / "gh-calls").read_text())
            self.assertNotIn("auto-merge still enabled", self.land("status", "E1"))
            self.assertTrue(self.land("lease", "list").startswith("L1 active"))

    def test_merge_mode_pauses_on_a_disable_auto_failure_when_auto_merge_request_is_set(self):
        with self.fake_gh():
            (self.base / "required-checks").write_text("")
            self.arm_auto_merge()
            (self.base / "disable-auto-fails").write_text("GraphQL: Auto merge is not enabled for this pull request")
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            self.age_absence(1)
            self.land("land")
            (self.base / "checks").write_text("failed")
            out = self.land("land")
            self.assertIn("queue paused: required checks failed on https://github.com/o/r/pull/9: test (3.12). "
                          "Auto-merge is still enabled: GraphQL: Auto merge is not enabled for this pull request", out)
            self.assertTrue(self.land("status", "E1").startswith("E1 awaiting-merge"))
            self.assertIn("pr merge https://github.com/o/r/pull/9 --disable-auto", (self.base / "merge-calls").read_text())
            self.assertNotIn("pr close", (self.base / "gh-calls").read_text())

    def test_merge_mode_pauses_when_github_requires_a_human_approval(self):
        with self.fake_gh():
            (self.base / "merge-refused").write_text("")
            self.init(mode="merge")
            self.queue_one()
            out = self.land("land")
            self.assertIn("queue paused: https://github.com/o/r/pull/9 needs an approving review", out)
            calls = (self.base / "merge-calls").read_text() if (self.base / "merge-calls").exists() else ""
            self.assertNotIn("--auto", calls.split())

    def test_merge_mode_builds_each_queued_entry_on_the_candidate_of_the_entry_ahead(self):
        with self.fake_gh():
            self.init(mode="merge", merge_method="squash")
            self.queue_entries(3)
            self.assertEqual(
                self.land("land"),
                "opened PRs that merge when their checks pass: E1 (r/D1) https://github.com/o/r/pull/9, "
                "E2 (r/D2) https://github.com/o/r/pull/10, E3 (r/D3) https://github.com/o/r/pull/11")
            self.assertEqual(self.origin_files("landing/e1"), ["one", "b.txt", "lib/x.py"])
            self.assertEqual(self.origin_files("landing/e2"), ["one", "two", "lib/x.py"])
            self.assertEqual(self.origin_files("landing/e3"), ["one", "two", "three"])
            self.assertEqual(self.on_origin("rev-parse", "landing/e1^"), self.on_origin("rev-parse", "main"))
            self.assertEqual(self.on_origin("rev-parse", "landing/e2^"), self.on_origin("rev-parse", "landing/e1"))
            self.assertEqual(self.on_origin("rev-parse", "landing/e3^"), self.on_origin("rev-parse", "landing/e2"))
            self.assertEqual(self.origin_log(), ["init"])
            self.assertFalse((self.base / "merge-calls").exists())

    def test_merge_mode_lands_three_stacked_entries_in_order_in_the_run_after_their_checks_pass(self):
        with self.fake_gh():
            self.open_line(3)
            for number in (9, 10, 11):
                self.post_check(number, "passed")
            self.assertEqual(self.land("land"), "landed E1 (r/D1), E2 (r/D2), E3 (r/D3)")
            self.assertEqual(self.origin_log(), ["w3", "w2", "w1", "init"])
            self.assertEqual([self.on_origin("show", "--format=", "--name-only", commit) for commit in ("main~2", "main~1", "main")],
                             ["a.txt", "b.txt", "lib/x.py"])
            self.assertEqual(self.origin_files("main"), ["one", "two", "three"])
            self.assertEqual(self.merge_calls(), [
                self.pinned(9, "--auto", "--squash"),
                self.pinned(9, "--squash"),
                self.pinned(10, "--auto", "--squash"),
                self.pinned(10, "--squash"),
                self.pinned(11, "--auto", "--squash"),
                self.pinned(11, "--squash"),
            ])
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_merge_mode_merges_nothing_while_the_first_entry_is_pending_notes_the_entry_ahead_on_the_passed_ones_then_lands_all_three(self):
        with self.fake_gh():
            self.open_line(3)
            self.post_check(9, "pending")
            self.post_check(10, "passed")
            self.post_check(11, "passed")
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertEqual(self.merge_calls(), [])
            self.assertEqual(self.origin_log(), ["init"])
            self.assertEqual(self.land("status", "E1"),
                             "E1 awaiting-merge (r/D1, w1). https://github.com/o/r/pull/9. waiting for required checks before merging")
            self.assertEqual(self.land("status", "E2"),
                             "E2 awaiting-merge (r/D2, w2). https://github.com/o/r/pull/10. waiting for E1 to merge first")
            self.assertEqual(self.land("status", "E3"),
                             "E3 awaiting-merge (r/D3, w3). https://github.com/o/r/pull/11. waiting for E2 to merge first")
            self.post_check(9, "passed")
            self.assertEqual(self.land("land"), "landed E1 (r/D1), E2 (r/D2), E3 (r/D3)")
            self.assertEqual(self.origin_log(), ["w3", "w2", "w1", "init"])

    def test_merge_mode_keeps_a_failed_entry_behind_a_pending_first_and_bounces_it_in_the_run_that_lands_the_first(self):
        with self.fake_gh():
            self.open_line(2)
            self.post_check(9, "pending")
            self.post_check(10, "failed")
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertEqual(self.land("status", "E2"),
                             "E2 awaiting-merge (r/D2, w2). https://github.com/o/r/pull/10. waiting for E1 to merge first")
            self.assertEqual(self.listed(), ["L1 submitted r/D1: a.txt", "L2 submitted r/D2: b.txt"])
            self.post_check(9, "passed")
            self.assertEqual(self.land("land"),
                             "landed E1 (r/D1)\n"
                             "bounced E2 (r/D2): required checks failed on https://github.com/o/r/pull/10: test (3.12)")
            self.assertEqual(self.origin_log(), ["w1", "init"])
            self.assertEqual(self.merge_calls(), [
                self.pinned(9, "--auto", "--squash"),
                self.pinned(9, "--squash"),
            ])

    def test_merge_mode_with_no_posted_checks_opens_two_entries_and_lands_both_in_the_second_run(self):
        with self.fake_gh():
            self.init(mode="merge", merge_method="squash")
            self.queue_entries(2)
            self.assertEqual(
                self.land("land"),
                "opened PRs that merge when their checks pass: E1 (r/D1) https://github.com/o/r/pull/9, "
                "E2 (r/D2) https://github.com/o/r/pull/10")
            self.assertEqual(self.merge_calls(), [])
            self.assertEqual(self.origin_log(), ["init"])
            self.age_absence(1, 2)
            self.assertEqual(self.land("land"), "landed E1 (r/D1), E2 (r/D2)")
            self.assertEqual(self.origin_log(), ["w2", "w1", "init"])

    def test_merge_mode_lands_both_entries_in_the_run_after_one_killed_right_after_the_first_merge(self):
        with self.fake_gh():
            self.open_line(2)
            self.post_check(9, "passed")
            self.post_check(10, "passed")
            (self.base / "crash-after-merge").write_text("")
            self.killed_land()
            self.assertEqual(self.origin_log(), ["w1", "init"])
            self.assertEqual(self.land("status", "E1").split(". ")[0], "E1 awaiting-merge (r/D1, w1)")
            self.assertEqual(self.land("land"), "landed E1 (r/D1), E2 (r/D2)")
            self.assertEqual(self.origin_log(), ["w2", "w1", "init"])
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_merge_mode_bounces_a_failed_first_entry_alone_rebuilds_the_two_behind_it_without_its_change_then_lands_both(self):
        with self.fake_gh():
            self.open_line(3)
            self.post_check(9, "failed")
            self.post_check(10, "passed")
            self.post_check(11, "passed")
            self.assert_absent(1, 2, 3)
            self.assertEqual(
                self.land("land"),
                "bounced E1 (r/D1): required checks failed on https://github.com/o/r/pull/9: test (3.12)\n"
                "rebuilt E2 (r/D2) https://github.com/o/r/pull/10\n"
                "rebuilt E3 (r/D3) https://github.com/o/r/pull/11")
            self.assertEqual(self.merge_calls(), [])
            self.assertEqual(self.origin_files("landing/e2"), ["a.txt", "two", "lib/x.py"])
            self.assertEqual(self.origin_files("landing/e3"), ["a.txt", "two", "three"])
            self.assertEqual(self.on_origin("rev-parse", "landing/e2^"), self.on_origin("rev-parse", "main"))
            self.assertEqual(self.on_origin("rev-parse", "landing/e3^"), self.on_origin("rev-parse", "landing/e2"))
            self.assertEqual(self.listed(), ["L1 active r/D1: a.txt", "L2 submitted r/D2: b.txt", "L3 submitted r/D3: lib/x.py"])
            self.assert_absent(2, 3)
            self.assertEqual(self.land("status", "E2"),
                             "E2 awaiting-merge (r/D2, w2). https://github.com/o/r/pull/10. waiting for required checks before merging")
            self.post_check(10, "passed")
            self.post_check(11, "passed")
            self.assertEqual(self.land("land"), "landed E2 (r/D2), E3 (r/D3)")
            self.assertEqual(self.origin_log(), ["w3", "w2", "init"])
            self.assertEqual(self.origin_files("main"), ["a.txt", "two", "three"])

    def test_merge_mode_lands_the_first_entry_bounces_a_failed_second_and_rebuilds_the_third_without_the_second(self):
        with self.fake_gh():
            self.open_line(3)
            self.post_check(9, "passed")
            self.post_check(10, "failed")
            self.post_check(11, "passed")
            self.assertEqual(
                self.land("land"),
                "landed E1 (r/D1)\n"
                "bounced E2 (r/D2): required checks failed on https://github.com/o/r/pull/10: test (3.12)\n"
                "rebuilt E3 (r/D3) https://github.com/o/r/pull/11")
            self.assertEqual(self.origin_log(), ["w1", "init"])
            self.assertEqual(self.origin_files("landing/e3"), ["one", "b.txt", "three"])
            self.assertEqual(self.on_origin("rev-parse", "landing/e3^"), self.on_origin("rev-parse", "main"))
            self.assertEqual(self.merge_calls(), [
                self.pinned(9, "--auto", "--squash"),
                self.pinned(9, "--squash"),
            ])

    def test_merge_mode_bounces_an_entry_whose_rebuild_conflicts_with_trunk_and_rebuilds_the_entry_behind_it(self):
        with self.fake_gh():
            self.open_line(3)
            self.post_check(9, "passed")
            self.post_check(10, "pending")
            self.post_check(11, "pending")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.commit_on_origin("outside", path="b.txt")
            self.assertEqual(
                self.land("land"),
                "bounced E2 (r/D2): conflict with trunk\n"
                "rebuilt E3 (r/D3) https://github.com/o/r/pull/11")
            self.assertEqual(self.origin_files("landing/e3"), ["one", "outside", "three"])
            self.assertEqual(self.on_origin("rev-parse", "landing/e3^"), self.on_origin("rev-parse", "main"))
            self.assertEqual(self.listed(), ["L2 active r/D2: b.txt", "L3 submitted r/D3: lib/x.py"])
            self.assertNotIn("pr close", (self.base / "gh-calls").read_text())
            self.assertEqual(self.origin_log(), ["outside", "w1", "init"])

    def test_merge_mode_merges_nothing_and_rebuilds_both_passed_entries_when_trunk_gained_an_outside_commit_then_lands_both(self):
        with self.fake_gh():
            self.open_line(2)
            self.post_check(9, "passed")
            self.post_check(10, "passed")
            self.commit_on_origin("outside")
            self.assertEqual(
                self.land("land"),
                "rebuilt E1 (r/D1) https://github.com/o/r/pull/9\n"
                "rebuilt E2 (r/D2) https://github.com/o/r/pull/10")
            self.assertEqual(self.merge_calls(), [])
            self.assertEqual(self.origin_log(), ["outside", "init"])
            self.assertEqual(self.on_origin("rev-parse", "landing/e1^"), self.on_origin("rev-parse", "main"))
            self.assertEqual(self.on_origin("rev-parse", "landing/e2^"), self.on_origin("rev-parse", "landing/e1"))
            self.assertEqual(self.on_origin("show", "landing/e2:merged.txt"), "outside")
            self.post_check(9, "passed")
            self.post_check(10, "passed")
            self.assertEqual(self.land("land"), "landed E1 (r/D1), E2 (r/D2)")
            self.assertEqual(self.origin_log(), ["w2", "w1", "outside", "init"])

    def test_merge_mode_rebuilds_an_entry_again_after_a_run_killed_between_the_rebuild_push_and_its_row(self):
        with self.fake_gh():
            self.open_line(2)
            self.post_check(9, "failed")
            before = self.entry_fields(2, "candidate", "onto")
            with self.land_dies_after_its_push_to("landing/e2"):
                self.killed_land()
            self.assertEqual(self.land("status", "E1"),
                             "E1 bounced (r/D1, w1). https://github.com/o/r/pull/9. "
                             "required checks failed on https://github.com/o/r/pull/9: test (3.12)")
            self.assertEqual(self.origin_files("landing/e2"), ["a.txt", "two", "lib/x.py"])
            self.assertEqual(self.entry_fields(2, "candidate", "onto"), before)
            self.assertEqual(self.land("land"), "rebuilt E2 (r/D2) https://github.com/o/r/pull/10")
            self.assertEqual(self.entry_fields(2, "candidate", "onto"),
                             (self.on_origin("rev-parse", "landing/e2"), self.on_origin("rev-parse", "main")))
            self.assertEqual(self.origin_files("landing/e2"), ["a.txt", "two", "lib/x.py"])
            self.assertEqual(self.land("status", "E2"),
                             "E2 awaiting-merge (r/D2, w2). https://github.com/o/r/pull/10. waiting for required checks before merging")

    def test_a_store_from_before_the_onto_column_keeps_its_first_entry_rebuilds_the_second_behind_it_then_lands_both(self):
        with self.fake_gh():
            self.open_line(2)
            self.post_check(9, "pending")
            first = self.on_origin("rev-parse", "landing/e1")
            alone = sh("git", "rev-parse", "w2", cwd=self.work)
            sh("git", "push", "-q", "--force", "origin", f"{alone}:refs/heads/landing/e2", cwd=self.work)
            self.entry_table_without_onto({2: alone})
            self.assertNotIn("onto", self.entry_columns())
            self.assertEqual(self.origin_files("landing/e2"), ["a.txt", "two", "lib/x.py"])
            log = self.record_pushes()
            try:
                self.assertEqual(self.land("land"), "rebuilt E2 (r/D2) https://github.com/o/r/pull/10")
            finally:
                self.stop_recording_pushes()
            self.assertIn("onto", self.entry_columns())
            self.assertEqual([line.rsplit(":", 1)[1] for line in log.read_text().splitlines()], ["refs/heads/landing/e2"])
            self.assertEqual(self.on_origin("rev-parse", "landing/e1"), first)
            self.assertEqual(self.on_origin("rev-parse", "landing/e2^"), first)
            self.assertEqual(self.origin_files("landing/e2"), ["one", "two", "lib/x.py"])
            self.post_check(9, "passed")
            self.post_check(10, "passed")
            self.assertEqual(self.land("land"), "landed E1 (r/D1), E2 (r/D2)")
            self.assertEqual(self.origin_log(), ["w2", "w1", "init"])

    def test_merge_mode_lands_an_entry_and_pauses_when_its_merge_commit_holds_another_tree_than_its_candidate_and_resume_clears_it(self):
        with self.fake_gh():
            self.open_line(1)
            self.post_check(9, "passed")
            candidate = self.on_origin("rev-parse", "landing/e1")
            (self.base / "outside-commit-before-merge").write_text("")
            out = self.land("land")
            merged = self.on_origin("rev-parse", "main")
            pause = (f"E1 merged as {merged[:12]}, whose tree is not the checked candidate {candidate[:12]}. "
                     "Trunk holds a change no check ran against. Check trunk, then run land.py resume")
            self.assertEqual(out, f"landed E1 (r/D1)\nqueue paused: {pause}")
            self.assertEqual(self.origin_log(), ["w1", "outside", "init"])
            self.assertEqual(self.land("status", "E1"), f"E1 landed (r/D1, w1). landed as {merged[:12]}. https://github.com/o/r/pull/9")
            self.assertEqual(self.land("status"), f"merge mode onto refs/remotes/origin/main. landed: 1. Paused: {pause} author policy: any")
            self.assertEqual(self.land("land"), f"queue paused: {pause}")
            self.assertEqual(self.land("resume"), "queue resumed")
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertEqual(self.land("status"), "merge mode onto refs/remotes/origin/main. landed: 1. author policy: any")

    def test_merge_mode_pauses_and_leaves_the_entry_awaiting_merge_when_git_cannot_read_its_merge_commit_then_reports_the_other_tree_after_resume(self):
        with self.fake_gh():
            self.open_line(1)
            self.post_check(9, "passed")
            candidate = self.on_origin("rev-parse", "landing/e1")
            (self.base / "outside-commit-before-merge").write_text("")
            with self.git_fails_for_the_merge_commit_of_pr_9("arg.startswith(merged)"):
                out = self.land("land")
                merged = self.on_origin("rev-parse", "main")
                pause = self.unread_commit_pause(merged)
                self.assertEqual(out, f"queue paused: {pause}")
                self.assertEqual(self.origin_log(), ["w1", "outside", "init"])
                self.assertEqual(self.land("status", "E1"), "E1 awaiting-merge (r/D1, w1). https://github.com/o/r/pull/9. "
                                                            "merge requested by the queue")
                self.assertEqual(self.land("status"),
                                 f"merge mode onto refs/remotes/origin/main. awaiting-merge: 1, leases held: 1. Paused: {pause} author policy: any")
                self.assertEqual(self.on_origin("rev-parse", "landing/e1"), candidate)
                self.assertEqual(self.land("resume"), "queue resumed")
                self.assertEqual(self.land("land"), f"queue paused: {pause}")
                self.assertEqual(self.land("resume"), "queue resumed")
            other_tree = (f"E1 merged as {merged[:12]}, whose tree is not the checked candidate {candidate[:12]}. "
                          "Trunk holds a change no check ran against. Check trunk, then run land.py resume")
            self.assertEqual(self.land("land"), f"landed E1 (r/D1)\nqueue paused: {other_tree}")

    def test_merge_mode_does_not_report_another_tree_when_the_tree_of_an_equal_merge_commit_cannot_be_read_and_lands_it_after_resume(self):
        with self.fake_gh():
            self.open_line(1)
            self.post_check(9, "passed")
            checked_tree = self.on_origin("rev-parse", "landing/e1^{tree}")
            with self.git_fails_for_the_merge_commit_of_pr_9("arg == merged + '^{tree}'"):
                out = self.land("land")
                merged = self.on_origin("rev-parse", "main")
                self.assertEqual(self.on_origin("rev-parse", "main^{tree}"), checked_tree)
                self.assertEqual(out, f"queue paused: {self.unread_commit_pause(merged)}")
                self.assertNotIn("whose tree is not the checked candidate", out)
                self.assertEqual(self.land("status", "E1").split(". ")[0], "E1 awaiting-merge (r/D1, w1)")
                self.assertEqual(self.listed(), ["L1 submitted r/D1: a.txt"])
            self.assertEqual(self.land("resume"), "queue resumed")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertEqual(self.land("status"), "merge mode onto refs/remotes/origin/main. landed: 1. author policy: any")
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_merge_mode_pauses_when_the_remote_does_not_serve_the_reported_merge_commit(self):
        with self.fake_gh():
            self.open_line(1)
            candidate = self.on_origin("rev-parse", "landing/e1")
            unknown = "1" * 40
            (self.base / "pr-state").write_text(f"MERGED {candidate} {unknown}")
            self.assertEqual(self.land("land"), f"queue paused: {self.unread_commit_pause(unknown)}")
            self.assertEqual(self.land("status", "E1").split(". ")[0], "E1 awaiting-merge (r/D1, w1)")
            self.assertEqual(self.on_origin("rev-parse", "landing/e1"), candidate)

    def test_merge_mode_pauses_when_the_state_read_of_a_merged_pr_names_no_merge_commit(self):
        self.pauses_on_a_merged_pr_whose_state_read_reports_the_merge_commit_as("")

    def test_merge_mode_pauses_when_the_state_read_of_a_merged_pr_names_an_abbreviated_merge_commit(self):
        self.pauses_on_a_merged_pr_whose_state_read_reports_the_merge_commit_as("abc123")

    def test_merge_mode_does_not_say_trunk_holds_an_unchecked_change_when_a_merge_with_another_tree_is_not_on_trunk_then_says_it_once_trunk_holds_it(self):
        with self.fake_gh():
            candidate, merged = self.pauses_on_a_merge_of_the_first_of_two_entries_that_trunk_was_rewound_past(outside=True)
            other_tree = (f"E1 merged as {merged[:12]}, whose tree is not the checked candidate {candidate[:12]}. "
                          "Trunk holds a change no check ran against. Check trunk, then run land.py resume")
            self.assertEqual(self.land("land"), f"landed E1 (r/D1)\nqueue paused: {other_tree}")
            self.assertEqual(self.land("resume"), "queue resumed")
            self.assertEqual(self.land("land"), "rebuilt E2 (r/D2) https://github.com/o/r/pull/10")
            self.post_check(10, "passed")
            self.assertEqual(self.land("land"), "landed E2 (r/D2)")
            self.assertEqual(self.origin_log(), ["w2", "w1", "outside", "init"])
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_merge_mode_leaves_an_entry_awaiting_merge_when_its_merge_with_the_candidate_tree_is_not_on_trunk_then_lands_the_line_once_trunk_holds_it(self):
        with self.fake_gh():
            self.pauses_on_a_merge_of_the_first_of_two_entries_that_trunk_was_rewound_past(outside=False)
            self.assertEqual(self.land("land"), "landed E1 (r/D1), E2 (r/D2)")
            self.assertEqual(self.origin_log(), ["w2", "w1", "init"])
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_merge_mode_fetches_a_merge_commit_that_trunk_lost_during_the_run_and_pauses_with_the_entry_awaiting_merge(self):
        with self.fake_gh():
            self.open_line(1)
            self.post_check(9, "passed")
            initial, candidate = self.on_origin("rev-parse", "main"), self.on_origin("rev-parse", "landing/e1")
            (self.base / "trunk-rewound-after-merge").write_text("")
            out = self.land("land")
            self.assertEqual(self.origin_log(), ["init"])
            merged = json.loads((self.base / "prs.json").read_text())["9"]["merge"]
            self.assertEqual(out, f"queue paused: {self.off_trunk_pause(merged, initial)}")
            self.assertEqual(sh("git", "log", "-1", "--format=%s", merged, cwd=self.work), "w1")
            self.assertEqual(self.land("status", "E1"), "E1 awaiting-merge (r/D1, w1). https://github.com/o/r/pull/9. "
                                                        "merge requested by the queue")
            self.assertEqual(self.on_origin("rev-parse", "landing/e1"), candidate)

    def test_merge_mode_says_the_merge_could_not_be_checked_when_git_cannot_tell_whether_trunk_holds_the_merge_commit(self):
        with self.fake_gh():
            self.open_line(1)
            self.post_check(9, "passed")
            candidate = self.on_origin("rev-parse", "landing/e1")
            (self.base / "outside-commit-before-merge").write_text("")
            with self.git_fails_for_the_merge_commit_of_pr_9("arg == '--is-ancestor'", code=128):
                out = self.land("land")
                merged = self.on_origin("rev-parse", "main")
                self.assertEqual(
                    out,
                    "queue paused: The merge of E1 could not be checked. "
                    f"Git could not tell whether trunk at {merged} holds merge commit {merged} "
                    "(git merge-base --is-ancestor exited 128), "
                    "so the tree of the merge commit was not compared with the tree of the candidate. "
                    "E1 stays awaiting merge. Check that git can read both commits here, "
                    "then run land.py resume and land.py land")
                self.assertEqual(self.land("status", "E1"), "E1 awaiting-merge (r/D1, w1). https://github.com/o/r/pull/9. "
                                                            "merge requested by the queue")
                self.assertEqual(self.on_origin("rev-parse", "landing/e1"), candidate)
            self.assertEqual(self.land("resume"), "queue resumed")
            other_tree = (f"E1 merged as {merged[:12]}, whose tree is not the checked candidate {candidate[:12]}. "
                          "Trunk holds a change no check ran against. Check trunk, then run land.py resume")
            self.assertEqual(self.land("land"), f"landed E1 (r/D1)\nqueue paused: {other_tree}")

    def test_merge_mode_says_it_could_not_check_trunk_when_git_cannot_read_the_last_landed_commit(self):
        with self.fake_gh():
            self.open_line(1)
            candidate = self.on_origin("rev-parse", "landing/e1")
            other, unknown = "0123456789abcdef0123456789abcdef01234567", "2" * 40
            (self.base / "pr-state").write_text(f"MERGED {other} {unknown}")
            self.assertEqual(self.land("land"), f"bounced E1 (r/D1): merged at {unknown[:12]} with head {other[:12]}, "
                                                f"not the checked {candidate[:12]}; review what reached trunk")
            (self.base / "pr-state").unlink()
            self.assertEqual(
                self.land("land"),
                f"queue paused: could not check that trunk contains the last landed commit {unknown[:12]} "
                "(git merge-base --is-ancestor exited 128); check that git can read that commit here, then run land.py resume")
            self.assertEqual(self.land("resume"), "queue resumed")
            self.assertEqual(self.land("land"), "nothing to land")

    def test_merge_mode_pauses_when_the_pr_state_read_fails(self):
        with self.fake_gh():
            self.open_line(1)
            self.post_check(9, "passed")
            (self.base / "state-query-fails").write_text("")
            self.assertEqual(
                self.land("land"),
                "queue paused: could not read the state of https://github.com/o/r/pull/9. gh said API unavailable. "
                "Fix it, then run land.py resume")
            self.assertEqual(self.merge_calls(), [])
            self.assertEqual(self.origin_log(), ["init"])
            (self.base / "state-query-fails").unlink()
            self.assertEqual(self.land("resume"), "queue resumed")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")

    def test_merge_mode_does_not_bounce_a_merge_requested_entry_with_a_failed_check_when_the_second_state_read_fails(self):
        with self.fake_gh():
            (self.base / "required-checks").write_text("")
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            self.age_absence(1)
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertIn("merge requested by the queue", self.land("status", "E1"))
            (self.base / "checks").write_text("failed")
            self.fail_state_reads_after_the_first()
            self.assertEqual(
                self.land("land"),
                "queue paused: could not read the state of https://github.com/o/r/pull/9. gh said API unavailable. "
                "Fix it, then run land.py resume")
            self.assertIn("E1 awaiting-merge", self.land("status", "E1"))
            self.assertEqual(self.listed(), ["L1 submitted r/D1: a.txt"])

    def test_human_mode_leaves_the_entry_and_does_not_pause_when_the_pr_state_read_fails(self):
        with self.fake_gh():
            self.init(mode="human")
            self.queue_one()
            self.land("land")
            (self.base / "state-query-fails").write_text("")
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertEqual(self.land("status"), "human mode onto refs/remotes/origin/main. awaiting-merge: 1, leases held: 1. author policy: any")

    def test_merge_mode_pauses_without_merging_a_head_that_replaced_the_candidate_after_a_base_branch_was_modified_refusal_then_lands_the_restored_candidate(self):
        with self.fake_gh():
            self.open_line(1)
            self.post_check(9, "passed")
            candidate = self.on_origin("rev-parse", "landing/e1")
            rogue = self.unreviewed_head()
            (self.base / "pr-9-base-moved").write_text("1")
            (self.base / "pr-9-head-replaced-on-refusal").write_text(rogue)
            pause = self.head_changed_pause(rogue, candidate)
            self.assertEqual(self.land("land"), f"queue paused: {pause}")
            self.assertEqual(self.on_origin("rev-parse", "landing/e1"), rogue)
            self.assertEqual((self.base / "pr-9-checks").read_text(), f"passed {rogue}")
            self.assertEqual(self.origin_log(), ["init"])
            self.assertFalse(self.ref_exists("main:rogue.txt", self.base / "origin.git"))
            self.assertEqual(self.merge_calls(), [self.pinned(9, "--auto", "--squash")] + [self.pinned(9, "--squash")] * 2)
            self.assertEqual(self.land("status", "E1"), "E1 awaiting-merge (r/D1, w1). https://github.com/o/r/pull/9. "
                                                        f"PR head changed to {rogue[:12]} outside the queue")
            self.assertEqual(self.land("status"),
                             f"merge mode onto refs/remotes/origin/main. awaiting-merge: 1, leases held: 1. Paused: {pause} author policy: any")
            self.assertEqual(self.land("land"), f"queue paused: {pause}")
            sh("git", "push", "-q", "--force", "origin", f"{candidate}:refs/heads/landing/e1", cwd=self.work)
            self.post_check(9, "passed")
            self.assertEqual(self.land("resume"), "queue resumed")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertEqual(self.origin_log(), ["w1", "init"])
            self.assertFalse(self.ref_exists("main:rogue.txt", self.base / "origin.git"))

    def test_merge_mode_pauses_without_merging_a_head_that_replaced_the_candidate_between_the_check_read_and_the_merge_request(self):
        with self.fake_gh():
            self.open_line(1)
            self.post_check(9, "passed")
            candidate = self.on_origin("rev-parse", "landing/e1")
            rogue = self.unreviewed_head()
            (self.base / "pr-9-head-replaced-after-check-read").write_text(rogue)
            self.assertEqual(self.land("land"), f"queue paused: {self.head_changed_pause(rogue, candidate)}")
            self.assertEqual(self.on_origin("rev-parse", "landing/e1"), rogue)
            self.assertEqual(self.origin_log(), ["init"])
            self.assertEqual(self.merge_calls(), [self.pinned(9, "--auto", "--squash"), self.pinned(9, "--squash")])

    def test_merge_mode_pins_its_auto_merge_request_to_the_candidate_and_pauses_when_another_head_replaced_it_after_the_check_read(self):
        with self.fake_gh():
            (self.base / "required-checks").write_text("")
            self.open_line(1)
            self.post_check(9, "passed")
            candidate = self.on_origin("rev-parse", "landing/e1")
            rogue = self.unreviewed_head()
            (self.base / "pr-9-head-replaced-after-check-read").write_text(rogue)
            self.assertEqual(self.land("land"), f"queue paused: {self.head_changed_pause(rogue, candidate)}")
            self.assertEqual(self.merge_calls(), [self.pinned(9, "--auto", "--squash"), self.pinned(9, "--squash")])
            self.assertNotIn("merge requested by the queue", self.land("status", "E1"))
            self.assertEqual(self.origin_log(), ["init"])

    def test_merge_mode_after_a_refused_merge_names_the_refusal_and_not_a_changed_head_when_the_state_read_fails(self):
        with self.fake_gh():
            self.open_line(1)
            self.post_check(9, "passed")
            rogue = self.unreviewed_head()
            (self.base / "pr-9-head-replaced-after-check-read").write_text(rogue)
            self.fail_state_reads_after_the_first()
            self.assertEqual(
                self.land("land"),
                "queue paused: GitHub refused to merge https://github.com/o/r/pull/9: "
                "GraphQL: Head branch was modified. Review and try the merge again. (mergePullRequest). "
                "Fix it, then run land.py resume")
            self.assertEqual(self.origin_log(), ["init"])

    def test_merge_mode_asks_again_after_one_base_branch_was_modified_refusal_and_lands_both_entries_in_that_run(self):
        with self.fake_gh():
            self.open_line(2)
            self.post_check(9, "passed")
            self.post_check(10, "passed")
            (self.base / "pr-10-base-moved").write_text("1")
            self.assertEqual(self.land("land"), "landed E1 (r/D1), E2 (r/D2)")
            self.assertEqual(self.merge_calls(), [
                self.pinned(9, "--auto", "--squash"),
                self.pinned(9, "--squash"),
                self.pinned(10, "--auto", "--squash"),
                self.pinned(10, "--squash"),
                self.pinned(10, "--squash"),
            ])
            self.assertEqual(self.land("status"), "merge mode onto refs/remotes/origin/main. landed: 2. author policy: any")

    def test_merge_mode_waits_without_a_pause_after_four_base_branch_was_modified_refusals_in_one_run_and_lands_in_the_next(self):
        with self.fake_gh():
            self.open_line(1)
            self.post_check(9, "passed")
            (self.base / "pr-9-base-moved").write_text("4")
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertEqual(self.merge_calls(), [self.pinned(9, "--auto", "--squash")]
                             + [self.pinned(9, "--squash")] * 4)
            self.assertEqual(self.land("status"), "merge mode onto refs/remotes/origin/main. awaiting-merge: 1, leases held: 1. author policy: any")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")

    def test_merge_mode_under_rebase_opens_the_second_entry_in_the_run_that_lands_the_first(self):
        with self.fake_gh():
            self.init(mode="merge", merge_method="rebase")
            self.queue_entries(2)
            self.assertEqual(
                self.land("land"),
                "opened PRs that merge when their checks pass: E1 (r/D1) https://github.com/o/r/pull/9\n"
                "still queued: E2")
            self.assertEqual(self.created_heads(), ["landing/e1"])
            self.age_absence(1)
            self.assertEqual(
                self.land("land"),
                "landed E1 (r/D1)\n"
                "opened PRs that merge when their checks pass: E2 (r/D2) https://github.com/o/r/pull/10")
            self.assertEqual(self.created_heads(), ["landing/e1", "landing/e2"])
            self.assertEqual(self.origin_log(), ["w1", "init"])
            self.assertEqual(self.on_origin("rev-parse", "landing/e2^"), self.on_origin("rev-parse", "main"))
            self.assertEqual(self.origin_files("landing/e2"), ["one", "two", "lib/x.py"])

    def test_merge_mode_keeps_an_entry_queued_while_an_unmerged_entry_ahead_holds_its_change_then_settles_it_as_landed(self):
        with self.fake_gh():
            self.init(mode="merge", merge_method="squash")
            self.assertEqual(self.queue_one(text="same\n", name="w1"), "E1")
            self.assertEqual(self.queue_one(text="same\n", name="w2"), "E2")
            self.assertEqual(
                self.land("land"),
                "opened PRs that merge when their checks pass: E1 (r/D1) https://github.com/o/r/pull/9\n"
                "still queued: E2")
            self.assertEqual(self.land("status", "E2"), "E2 queued (r/D1, w2)")
            self.assertEqual(self.listed(), ["L1 submitted r/D1: a.txt", "L2 submitted r/D1: a.txt"])
            self.post_check(9, "passed")
            self.land("land")
            self.assertEqual(self.origin_log(), ["w1", "init"])
            merged = self.on_origin("rev-parse", "main")
            self.assertEqual(self.land("status", "E2"), f"E2 landed (r/D1, w2). landed as {merged[:12]}. already in trunk")
            self.assertEqual(self.land("lease", "list"), "no leases held")
            self.assertEqual(self.created_heads(), ["landing/e1"])

    def test_human_mode_builds_each_candidate_alone_on_trunk_and_lands_the_second_entry_while_the_first_awaits_its_merge(self):
        with self.fake_gh():
            self.init(mode="human")
            self.queue_entries(2)
            self.assertEqual(
                self.land("land"),
                "opened PRs for E1 (r/D1) https://github.com/o/r/pull/9, E2 (r/D2) https://github.com/o/r/pull/10")
            self.assertEqual(self.origin_files("landing/e1"), ["one", "b.txt", "lib/x.py"])
            self.assertEqual(self.origin_files("landing/e2"), ["a.txt", "two", "lib/x.py"])
            self.assertEqual(self.on_origin("rev-parse", "landing/e2^"), self.on_origin("rev-parse", "main"))
            self.gh("pr", "merge", "https://github.com/o/r/pull/10", "--squash")
            self.assertEqual(self.land("land"), "landed E2 (r/D2)")
            self.assertEqual(self.origin_log(), ["w2", "init"])
            self.assertEqual(self.land("status", "E1"), "E1 awaiting-merge (r/D1, w1). https://github.com/o/r/pull/9")

    def test_init_in_merge_mode_picks_an_allowed_method(self):
        with self.fake_gh():
            self.allow_methods(squash=True)
            self.init(mode="merge")
            self.assertEqual(self.stored_merge_method(), "squash")
            self.assertIn(
                "repo view --json mergeCommitAllowed,squashMergeAllowed,rebaseMergeAllowed",
                (self.base / "gh-calls").read_text(),
            )
            self.allow_methods(rebase=True)
            self.init(mode="merge")
            self.assertEqual(self.stored_merge_method(), "rebase")
            self.allow_methods(squash=True, rebase=True)
            self.init(mode="merge")
            self.assertEqual(self.stored_merge_method(), "rebase")
            self.allow_methods(merge=True, squash=True)
            self.init(mode="merge")
            self.assertEqual(self.stored_merge_method(), "merge")
            self.allow_methods(merge=True, squash=True, rebase=True)
            self.init(mode="merge")
            self.assertEqual(self.stored_merge_method(), "merge")
            self.allow_methods(merge=True, rebase=True)
            self.init(mode="merge", merge_method="rebase")
            self.assertEqual(self.stored_merge_method(), "rebase")

    def test_init_outside_merge_mode_does_not_ask_which_methods_are_allowed(self):
        with self.fake_gh():
            self.allow_methods(squash=True)
            self.init(mode="human")
            calls = (self.base / "gh-calls").read_text() if (self.base / "gh-calls").exists() else ""
            self.assertNotIn("mergeCommitAllowed", calls)
            self.assertEqual(self.stored_merge_method(), "merge")

    def test_init_refuses_a_merge_method_the_repository_disallows(self):
        with self.fake_gh():
            self.allow_methods(squash=True, rebase=True)
            self.assertEqual(
                self.land("init", "--trunk", "main", "--mode", "merge", "--check", "./check.sh",
                          "--merge-method", "merge", ok=False),
                "land: repository does not allow merge; allowed: squash, rebase",
            )
            self.assertIn("has no landing contract", self.land("status", ok=False))
            self.allow_methods()
            self.assertEqual(
                self.land("init", "--trunk", "main", "--mode", "merge", "--check", "./check.sh", ok=False),
                "land: repository allows no merge method; allowed: none",
            )

    def test_init_keeps_merge_when_gh_cannot_read_allowed_methods(self):
        with self.fake_gh():
            (self.base / "repo-view-fails").write_text("x")
            self.allow_methods(squash=True)
            self.init(mode="merge")
            self.assertEqual(self.stored_merge_method(), "merge")
            self.init(mode="merge", merge_method="rebase")
            self.assertEqual(self.stored_merge_method(), "rebase")
            self.land("init", "--trunk", "main", "--mode", "merge")
            self.assertEqual(self.stored_merge_method(), "rebase")
            self.land("mode", "human")
            before = self.contract()
            self.land("init", "--trunk", "main", "--mode", "human")
            self.assertEqual(self.contract(), before)

    def test_a_rerun_keeps_an_omitted_check_setup_and_timeout_and_writes_a_passed_batch(self):
        with self.fake_gh():
            self.land("init", "--trunk", "main", "--mode", "human", "--check", "./check.sh",
                      "--setup", "make deps", "--batch", "2", "--timeout", "90")
            self.land("init", "--trunk", "main", "--mode", "human", "--batch", "4")
            stored = self.contract()
            self.assertEqual(stored["checks"], ["./check.sh"])
            self.assertEqual(stored["setup"], "make deps")
            self.assertEqual(stored["batch"], 4)
            self.assertEqual(stored["timeout"], 90)
            self.land("init", "--trunk", "main", "--mode", "human", "--remote", "origin")
            self.assertEqual(self.contract(), stored)
            self.land("init", "--trunk", "main", "--mode", "human", "--check", "make verify",
                      "--check", "make lint", "--setup", "", "--timeout", "45")
            self.assertEqual(self.contract(), {**stored, "checks": ["make verify", "make lint"],
                                              "setup": "", "timeout": 45})

    def test_a_rerun_without_merge_method_keeps_rebase_while_it_stays_allowed(self):
        with self.fake_gh():
            self.allow_methods(squash=True, rebase=True)
            self.init(mode="merge", merge_method="rebase")
            self.assertEqual(self.stored_merge_method(), "rebase")
            self.init(mode="merge")
            self.assertEqual(self.stored_merge_method(), "rebase")
            self.allow_methods(squash=True)
            self.init(mode="merge")
            self.assertEqual(self.stored_merge_method(), "squash")

    def test_a_rerun_fills_only_missing_contract_keys(self):
        with self.fake_gh():
            self.land("init", "--trunk", "main", "--mode", "human", "--check", "make verify",
                      "--setup", "make deps", "--batch", "7", "--timeout", "95")
            before = self.contract()
            with land.Store.for_repo(self.work) as store:
                with store.tx() as db:
                    db.execute("DELETE FROM contract WHERE key IN ('setup', 'batch')")
            self.land("init", "--trunk", "main", "--mode", "human")
            self.assertEqual(self.contract(), {**before, "setup": "", "batch": 1})

    def test_a_rerun_with_no_allowed_merge_method_leaves_the_contract_unchanged(self):
        with self.fake_gh():
            self.allow_methods(rebase=True)
            self.land("init", "--trunk", "main", "--mode", "human", "--check", "make verify",
                      "--setup", "make deps", "--batch", "7", "--timeout", "95", "--merge-method", "rebase")
            before = self.contract()
            self.allow_methods()
            self.assertEqual(
                self.land("init", "--trunk", "main", "--mode", "human", "--batch", "4", ok=False),
                "land: repository allows no merge method; allowed: none")
            self.assertEqual(self.contract(), before)

    def test_a_refused_merge_method_names_the_command_that_changes_it(self):
        refusal = "GraphQL: Merge commits are not allowed on this repository. (mergePullRequest)"
        with self.fake_gh():
            self.allow_methods(merge=True, squash=True, rebase=True)
            self.init(mode="merge")
            self.queue_one()
            self.assertIn("opened PRs that merge when their checks pass: E1 (r/D1)", self.land("land"))
            (self.base / "plain-merge-fails").write_text(refusal)
            self.allow_methods(squash=True)
            self.age_absence(1)
            self.assertEqual(
                self.land("land"),
                "queue paused: GitHub refused to merge https://github.com/o/r/pull/9: "
                f"{refusal}. Run land.py mode merge --merge-method squash, then land.py resume",
            )
            self.assertEqual(
                self.land("mode", "human", ok=False),
                "land: 1 entry is queued, landing, or awaiting merge; change the mode when the queue is empty",
            )
            self.assertEqual(
                self.land("mode", "merge", "--merge-method", "merge", ok=False),
                "land: repository does not allow merge; allowed: squash",
            )
            self.assertEqual(
                self.land("mode", "merge", "--merge-method", "squash"),
                "landing mode is now merge, merging with --squash; leases held by r/D1",
            )
            (self.base / "plain-merge-fails").unlink()
            self.assertEqual(self.land("resume"), "queue resumed")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertIn("pr merge https://github.com/o/r/pull/9 --squash", (self.base / "merge-calls").read_text())
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_the_merge_method_changes_while_an_entry_is_queued(self):
        with self.fake_gh():
            self.allow_methods(merge=True, squash=True)
            self.init(mode="merge")
            self.queue_one()
            self.assertEqual(
                self.land("mode", "merge", "--merge-method", "squash"),
                "landing mode is now merge, merging with --squash; leases held by r/D1",
            )
            self.assertEqual(self.stored_merge_method(), "squash")
            self.assertEqual(
                self.land("mode", "push", ok=False),
                "land: 1 entry is queued, landing, or awaiting merge; change the mode when the queue is empty",
            )

    def test_a_method_change_paused_in_github_keeps_the_mode_a_second_process_set(self):
        with self.fake_gh():
            self.allow_methods(merge=True, squash=True)
            self.init(mode="merge")
            self.pause_repo_view()
            (self.base / "pause-repo-view").write_text("")
            child = real_popen(
                ProcessReason.RACE,
                [sys.executable, str(SCRIPT), "--repo", str(self.work),
                 "mode", "merge", "--merge-method", "squash"],
                env=os.environ.copy())
            try:
                deadline = time.monotonic() + 10
                while not (self.base / "repo-view-waiting").exists():
                    if child.poll() is not None or time.monotonic() > deadline:
                        out, err = child.communicate()
                        self.fail(f"repo view did not pause: code {child.returncode}\n{out}\n{err}")
                    time.sleep(0.01)
                self.assertEqual(self.land("mode", "human"), "landing mode is now human")
                self.assertEqual(self.queue_one(), "E1")
                (self.base / "repo-view-release").write_text("")
                out, err = child.communicate(timeout=10)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.communicate()
            self.assertEqual(child.returncode, 1, out + err)
            self.assertEqual(
                err.strip(),
                "land: 1 entry is queued, landing, or awaiting merge; change the mode when the queue is empty",
            )
            self.assertEqual(self.stored_merge_method(), "merge")
            status = self.land("status")
            self.assertTrue(status.startswith("human mode onto"), status)
            self.assertIn("queued: 1", status)

    def test_mode_help_separates_a_switch_from_a_method_change(self):
        top = real_process(ProcessReason.HELP, SCRIPT, ["--help"], env=None)
        mode = real_process(ProcessReason.HELP, SCRIPT, ["mode", "--help"], env=None)
        self.assertEqual(top.returncode, 0, top.stderr)
        self.assertEqual(mode.returncode, 0, mode.stderr)
        for text in (top.stdout, mode.stdout):
            shown = " ".join(text.split())
            self.assertIn("when the queue is empty", shown)
            self.assertIn("--merge-method may change while entries are in flight", shown)
            self.assertNotIn("while nothing is in flight", shown)

    def test_one_busy_entry_is_singular_and_two_are_plural(self):
        with self.fake_gh():
            self.init(mode="human")
            self.queue_one()
            self.land("land")
            self.assertEqual(
                self.land("mode", "merge", ok=False),
                "land: 1 entry is queued, landing, or awaiting merge; change the mode when the queue is empty",
            )
            self.queue_one(path="b.txt", name="w2", holder="r/D2")
            self.land("land")
            self.assertEqual(
                self.land("mode", "push", ok=False),
                "land: 2 entries are queued, landing, or awaiting merge; change the mode when the queue is empty",
            )

    def test_the_mode_changes_only_while_nothing_is_in_flight(self):
        with self.fake_gh():
            self.init(mode="human")
            self.queue_one()
            self.land("land")
            self.assertIn("awaiting merge; change the mode when the queue is empty", self.land("mode", "merge", ok=False))
            candidate = sh("git", "rev-parse", "landing/e1", cwd=self.base / "origin.git")
            (self.base / "pr-state").write_text(f"MERGED {candidate} abc123")
            self.land("land")
            self.assertEqual(self.land("mode", "merge", "--merge-method", "squash"), "landing mode is now merge, merging with --squash")
            self.assertTrue(self.land("status").startswith("merge mode onto"))
            self.assertIn("needs a new contract", self.land("mode", "local", ok=False))

    def test_a_contract_written_with_the_old_auto_mode_reads_as_push(self):
        self.init(mode="auto")
        self.assertTrue(self.land("status").startswith("push mode onto"))
        self.assertEqual(self.land("init", "--trunk", "main", "--mode", "push", "--check", "./check.sh").split()[0], "updated")

    def test_human_mode_adopts_the_pr_a_crashed_run_created(self):
        with self.fake_gh():
            self.init(mode="human")
            self.queue_one()
            (self.base / "crash-on-create").write_text("")
            result = real_process(
                ProcessReason.KILLED_CALLER, SCRIPT, ["--repo", str(self.work), "land"], env=os.environ.copy())
            self.assertEqual(result.returncode, -9)
            self.assertEqual(self.land("land"), "adopted E1 (r/D1) https://github.com/o/r/pull/9")
            self.assertIn("awaiting-merge (r/D1, w1). https://github.com/o/r/pull/9", self.land("status", "E1"))

    def test_human_mode_bounces_a_pr_merged_with_a_head_the_queue_did_not_check(self):
        with self.fake_gh():
            self.init(mode="human")
            self.queue_one()
            self.land("land")
            (self.base / "pr-state").write_text("MERGED 0123456789abcdef deadbeef")
            self.assertIn("bounced E1 (r/D1): merged at deadbeef with head 0123456789ab", self.land("land"))

    def test_human_mode_pauses_when_trunk_loses_a_landed_commit(self):
        with self.fake_gh():
            self.init(mode="human")
            self.queue_one()
            self.land("land")
            sh("git", "fetch", "-q", "origin", cwd=self.work)
            merged = self.commit_on_origin("human merge")
            (self.base / "pr-state").write_text(f"MERGED {sh('git', 'rev-parse', 'landing/e1', cwd=self.base / 'origin.git')} {merged}")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            sh("git", "push", "-q", "--force", "origin", "HEAD:main", cwd=self.work)
            self.assertIn("queue paused: trunk no longer contains the last landed commit", self.land("land"))
