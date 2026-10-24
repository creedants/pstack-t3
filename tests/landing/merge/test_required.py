if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from ..support import (
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
from .base import MergeBase

class LandingTest(MergeBase, LandingTestBase):
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
