import json
import os

from .support import (
    setUpModule,
    BrigadeTest,
    CLAUDE,
    ROOT,
    SCRIPT,
    Path,
    call,
    runpy,
)


CATALOG = ROOT / "tests/fixtures/catalog.json"
PARENT = CLAUDE
XHIGH = '{"instanceId": "grok", "model": "grok-4.7", "options": {"reasoningEffort": "xhigh"}}'
MEDIUM = '{"instanceId": "grok", "model": "grok-4.7", "options": {"reasoningEffort": "medium"}}'
LOW = '{"instanceId": "codex", "model": "gpt-6.1-sol", "options": {"reasoningEffort": "low"}}'
FAST = '{"instanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"fastMode": false}}'
BARE = '{"instanceId": "grok", "model": "grok-4.7", "options": {}}'


class LaunchTest(BrigadeTest):
    def fire_station(self, station):
        ticket = self.brigade("ticket", "add", "--kind", "fix", "--summary", station)
        return self.brigade("fire", "--tickets", ticket, "--station", station, "--summary", station)

    def open_wide(self, **extra):
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--workers", "8", *extra)

    def launch_call(self, dish="D1", catalog=None, parent=PARENT):
        catalog = CATALOG if catalog is None else catalog
        return call(self, SCRIPT, [
            "--store", str(self.store), "--at", str(self.at),
            "launch", dish, "--parent", parent, "--catalog", str(catalog),
        ])

    def assert_launch(self, expected, dish="D1", catalog=None):
        outcome = self.launch_call(dish, catalog)
        self.assertEqual(outcome.code, 0, outcome.stdout_text() + outcome.stderr_text())
        self.assertEqual(outcome.stdout_text(), expected + "\n")
        self.assertEqual(outcome.stderr_text(), "")
        return outcome

    def assert_refused(self, expected, dish="D1", catalog=None):
        outcome = self.launch_call(dish, catalog)
        self.assertEqual(outcome.code, 1, outcome.stdout_text() + outcome.stderr_text())
        self.assertEqual(outcome.stdout_text(), "")
        self.assertEqual(outcome.stderr_text().strip(), expected)
        return outcome

    def write_brief(self, text, dish="D1"):
        folder = self.at / "briefs"
        folder.mkdir(exist_ok=True)
        path = folder / f"{dish}.md"
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text)
        return path

    def write_user_roles(self, roles, budget="default"):
        directory = Path(os.environ["XDG_CONFIG_HOME"]) / "pstack-t3"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "roles.json"
        path.write_text(json.dumps({"version": 1, "budget": budget, "roles": roles}))
        return path

    def write_project_roles(self, roles, budget="default"):
        directory = self.project / ".pstack"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "t3-roles.json"
        path.write_text(json.dumps({"version": 1, "budget": budget, "roles": roles}))
        return path

    def entry_globals(self):
        if "_brigade_entry" not in self.__dict__ or SCRIPT not in self._brigade_entry:
            call(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at), "status"])
        return self._brigade_entry[SCRIPT]["run"].__globals__

    def install_show(self, text="", code=0, stderr=""):
        script = Path(self.temporary.name) / "show-roles.py"
        script.write_text(
            "import sys\n"
            f"sys.stderr.write({stderr!r})\n"
            f"code = {code}\n"
            "if code:\n"
            "    raise SystemExit(code)\n"
            f"sys.stdout.write({text!r})\n"
        )
        glob = self.entry_globals()
        glob["BUILT_ROLES"] = script
        glob["SOURCE_ROLES"] = script
        return script

    def show_payload(self, payload):
        self.install_show(text=json.dumps(payload))

    def test_bug_fix_with_no_brief_prints_the_xhigh_seat(self):
        self.fired_bug_fix()
        before = self.store_files()
        self.assert_launch(XHIGH)
        self.assertEqual(self.store_files(), before)

    def test_a_brief_with_no_mode_line_prints_the_xhigh_seat(self):
        self.fired_bug_fix()
        self.write_brief("GOAL: keep the seat\n")
        before = self.store_files()
        self.assert_launch(XHIGH)
        self.assertEqual(self.store_files(), before)

    def test_mode_full_prints_the_xhigh_seat(self):
        self.fired_bug_fix()
        self.write_brief("Mode: full\n")
        self.assert_launch(XHIGH)

    def test_mode_light_prints_the_medium_seat(self):
        self.fired_bug_fix()
        self.write_brief("GOAL: a light dish\nMode: light\n")
        self.assert_launch(MEDIUM)

    def test_a_light_restaurant_mode_does_not_change_the_seat(self):
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--mode", "light")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix s")
        self.assert_launch(XHIGH)

    def test_perf_issue_and_hillclimb_print_the_xhigh_seat(self):
        self.open_wide()
        self.fire_station("perf-issue")
        self.assert_launch(XHIGH, "D1")
        self.fire_station("hillclimb")
        self.assert_launch(XHIGH, "D2")

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
        })
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
        self.assert_launch('{"instanceId": "grok", "model": "grok-4.7", "options": {}}')

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

    def test_a_catalog_of_json_that_is_not_an_object_refuses_with_the_path(self):
        self.fired_bug_fix()
        path = Path(self.temporary.name) / "catalog-number.json"
        path.write_bytes(b"42")
        before = self.store_files()
        self.assert_refused(f"brigade: {path} is JSON that is not an object", catalog=path)
        self.assertEqual(self.store_files(), before)

    def test_a_catalog_that_is_not_utf8_text_refuses_with_the_path(self):
        self.fired_bug_fix()
        path = Path(self.temporary.name) / "catalog-bytes.json"
        path.write_bytes(b"\xff")
        before = self.store_files()
        self.assert_refused(f"brigade: {path} is not UTF-8 text", catalog=path)
        self.assertEqual(self.store_files(), before)

    def test_a_catalog_that_is_a_directory_refuses_with_the_path(self):
        self.fired_bug_fix()
        path = Path(self.temporary.name) / "catalog-dir"
        path.mkdir()
        before = self.store_files()
        self.assert_refused(f"brigade: {path} is a directory", catalog=path)
        self.assertEqual(self.store_files(), before)

    def test_inherit_seats_are_not_one_seat(self):
        self.fired_bug_fix()
        self.write_user_roles({"bug-fix": ["inherit"]})
        payload = json.loads(CATALOG.read_text())
        for provider in payload["providers"]:
            if provider["providerInstanceId"] != "claudeAgent":
                continue
            for model in provider["models"]:
                if model["id"] == "claude-opus-5-5":
                    model["options"] = [option for option in model["options"] if option["id"] != "fastMode"]
        catalog = Path(self.temporary.name) / "catalog-no-fast.json"
        catalog.write_text(json.dumps(payload))
        self.assert_refused("brigade: bug-fix seats are inherit, not one seat", catalog=catalog)

    def test_catalog_required_seats_are_not_one_seat(self):
        self.fired_bug_fix()
        self.show_payload({"roles": {"bug-fix": {"seats": "catalog-required"}}})
        self.assert_refused("brigade: bug-fix seats are catalog-required, not one seat")

    def test_two_seat_objects_on_bug_fix_refuse_with_the_count(self):
        self.fired_bug_fix()
        self.show_payload({"roles": {"bug-fix": {"seats": [
            {"providerInstanceId": "grok", "model": "grok-4.7"},
            {"providerInstanceId": "codex", "model": "gpt-6.1-sol"},
        ]}}})
        self.assert_refused("brigade: bug-fix has 2 seats")

    def test_zero_seats_refuse_with_the_count(self):
        self.fired_bug_fix()
        self.show_payload({"roles": {"bug-fix": {"seats": []}}})
        self.assert_refused("brigade: bug-fix has 0 seats")

    def test_a_missing_role_is_refused(self):
        self.fired_bug_fix()
        self.show_payload({"roles": {}})
        self.assert_refused("brigade: bug-fix is missing from roles.py show")

    def test_a_seat_without_provider_or_model_is_refused(self):
        self.fired_bug_fix()
        self.show_payload({"roles": {"bug-fix": {"seats": [{"model": "grok-4.7"}]}}})
        self.assert_refused("brigade: bug-fix seat lacks providerInstanceId or model")

    def test_options_that_are_not_an_object_are_refused(self):
        self.fired_bug_fix()
        self.show_payload({"roles": {"bug-fix": {"seats": [{
            "providerInstanceId": "grok", "model": "grok-4.7", "options": False,
        }]}}})
        self.assert_refused("brigade: bug-fix seat options are not an object")

    def test_an_unknown_role_name_from_roles_py_is_the_wrapped_refusal(self):
        self.fired_bug_fix()
        path = self.write_user_roles({"not-a-role": ["inherit"]})
        outcome = self.launch_call()
        self.assertEqual(outcome.code, 1, outcome.stdout_text() + outcome.stderr_text())
        self.assertEqual(outcome.stdout_text(), "")
        detail = outcome.stderr_text().strip().removeprefix("brigade: roles.py show refused the seat: ")
        self.assertIn(f"{path}: unknown role 'not-a-role'", detail)
        self.assertFalse(detail.startswith("error:"), detail)

    def test_roles_py_text_that_is_not_json_is_refused(self):
        self.fired_bug_fix()
        self.install_show(text="not json")
        self.assert_refused("brigade: roles.py show printed no JSON object")

    def test_a_light_cap_info_line_stays_off_both_streams_of_a_success(self):
        self.fired_bug_fix()
        self.show_payload({"roles": {"bug-fix": {"seats": [{
            "providerInstanceId": "grok", "model": "grok-4.7", "options": {"reasoningEffort": "xhigh"},
        }]}}})
        glob = self.entry_globals()
        script = glob["BUILT_ROLES"]
        script.write_text(
            script.read_text().replace(
                "sys.stderr.write('')\n",
                "sys.stderr.write('info: light mode caps reasoning at medium\\n')\n",
                1,
            )
        )
        self.assert_launch(XHIGH)

    def test_a_held_store_lock_refuses_launch_before_roles_py(self):
        self.fired_bug_fix()
        loaded = runpy.run_path(str(SCRIPT))
        glob = loaded["run"].__globals__
        glob["HELD_LOCKS"].append(Path("/store/held"))
        missing = Path(self.temporary.name) / "missing-catalog.json"
        before = self.store_files()
        with self.assertRaises(loaded["BrigadeError"]) as caught:
            loaded["run"]([
                "--store", str(self.store), "--at", str(self.at),
                "launch", "D1", "--parent", PARENT, "--catalog", str(missing),
            ])
        self.assertEqual(
            str(caught.exception),
            "roles.py may run git; release /store/held's store lock before resolving the mode",
        )
        self.assertEqual(self.store_files(), before)

    def test_launch_without_parent_or_catalog_exits_2(self):
        self.fired_bug_fix()
        cases = (
            ["launch", "D1", "--catalog", str(CATALOG)],
            ["launch", "D1", "--parent", PARENT],
        )
        for argv in cases:
            with self.subTest(argv=argv):
                outcome = call(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at), *argv])
                self.assertEqual(outcome.code, 2, outcome.stderr_text())
                self.assertEqual(outcome.stdout_text(), "")
