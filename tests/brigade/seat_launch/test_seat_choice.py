if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from ..support import Path
from .fixtures import BARE, FAST, LOW, MEDIUM, XHIGH


class SeatChoiceMixin:
    def test_bug_fix_with_no_brief_prints_the_medium_seat(self):
        self.fired_bug_fix()
        self.write_launch_seats("bug-fix")
        before = self.store_files()
        self.assert_launch(MEDIUM)
        self.assertEqual(self.store_files(), before)

    def test_a_brief_with_no_mode_line_prints_the_medium_seat(self):
        self.fired_bug_fix()
        self.write_launch_seats("bug-fix")
        self.write_brief("GOAL: keep the seat\n")
        before = self.store_files()
        self.assert_launch(MEDIUM)
        self.assertEqual(self.store_files(), before)

    def test_mode_full_prints_the_xhigh_seat(self):
        self.fired_bug_fix()
        self.write_launch_seats("bug-fix")
        self.write_brief("Mode: full\n")
        self.assert_launch(XHIGH)

    def test_mode_light_prints_the_medium_seat(self):
        self.fired_bug_fix()
        self.write_launch_seats("bug-fix")
        self.write_brief("GOAL: a light dish\nMode: light\n")
        self.assert_launch(MEDIUM)

    def test_a_light_restaurant_mode_does_not_change_the_seat(self):
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--mode", "light")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix s")
        self.write_launch_seats("bug-fix", mode="full")
        self.assert_launch(XHIGH)

    def test_perf_issue_and_hillclimb_print_the_medium_seat(self):
        self.open_wide()
        self.write_launch_seats("perf-issue", "hillclimb")
        self.fire_station("perf-issue")
        self.assert_launch(MEDIUM, "D1")
        self.fire_station("hillclimb")
        self.assert_launch(MEDIUM, "D2")

    def test_feature_and_refactoring_print_the_seat_stored_on_feature_refactoring(self):
        self.open_wide()
        self.write_user_roles({
            "feature, refactoring": [{
                "providerInstanceId": "codex",
                "model": "gpt-6.1-sol",
                "options": {"reasoningEffort": "low"},
            }],
        })
        self.fire_station("feature")
        self.assert_launch(LOW, "D1")
        self.fire_station("refactoring")
        self.assert_launch(LOW, "D2")

    def test_launch_reads_the_seat_from_the_restaurant_project(self):
        self.fired_bug_fix()
        self.write_project_roles({
            "bug-fix": [{
                "providerInstanceId": "codex",
                "model": "gpt-6.1-sol",
                "options": {"reasoningEffort": "low"},
            }],
        })
        self.assert_launch(LOW)

    def test_a_seat_with_no_options_key_prints_an_empty_options_object(self):
        self.fired_bug_fix()
        self.write_user_roles({
            "bug-fix": [{"providerInstanceId": "grok", "model": "grok-4.7"}],
        }, mode="full")
        self.assert_launch(BARE)

    def test_fast_mode_false_is_copied(self):
        self.fired_bug_fix()
        self.write_user_roles({
            "bug-fix": [{
                "providerInstanceId": "claudeAgent",
                "model": "claude-opus-5-5",
                "options": {"fastMode": False},
            }],
        })
        self.assert_launch(FAST)

    def test_an_empty_options_object_is_copied(self):
        self.fired_bug_fix()
        self.show_payload({"roles": {"bug-fix": {"seats": [{
            "providerInstanceId": "grok", "model": "grok-4.7", "options": {},
        }]}}})
        self.assert_launch('{"instanceId": "grok", "model": "grok-4.7", "options": {}}', in_process=True)

    def test_investigation_has_no_code_delegate_role_before_roles_py(self):
        self.open_wide()
        self.fire_station("investigation")
        self.write_brief("Mode: turbo\n")
        missing = Path(self.temporary.name) / "missing-catalog.json"
        before = self.store_files()
        self.assert_refused("brigade: investigation has no code-delegate role", catalog=missing)
        self.assertEqual(self.store_files(), before)

    def test_an_unlisted_station_has_no_code_delegate_role_before_roles_py(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--state", "dropped")
        self.fire_station("correct")
        missing = Path(self.temporary.name) / "missing-catalog.json"
        self.assert_refused("brigade: correct has no code-delegate role", "D2", missing)

    def test_a_missing_dish_names_the_table(self):
        self.open()
        self.assert_refused("brigade: no D9 in dishes.tsv", "D9")

    def test_two_mode_lines_refuse_before_roles_py(self):
        self.fired_bug_fix()
        self.write_brief("Mode: full\nMode: light\n")
        missing = Path(self.temporary.name) / "missing-catalog.json"
        self.assert_refused("brigade: briefs/D1.md needs one Mode line of full or light", catalog=missing)

    def test_a_mode_outside_full_or_light_refuses_before_roles_py(self):
        self.fired_bug_fix()
        self.write_brief("Mode: turbo\n")
        missing = Path(self.temporary.name) / "missing-catalog.json"
        self.assert_refused("brigade: briefs/D1.md needs one Mode line of full or light", catalog=missing)

    def test_a_brief_that_is_not_utf8_refuses(self):
        self.fired_bug_fix()
        self.write_brief(b"\xff")
        self.assert_refused("brigade: briefs/D1.md is not UTF-8 text")
