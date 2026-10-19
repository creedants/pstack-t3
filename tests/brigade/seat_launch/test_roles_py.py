from ..support import Path, SCRIPT, call, json, os, runpy
from .fixtures import BARE, CATALOG, PARENT, XHIGH


class RolesPyMixin:
    def test_roles_py_show_failures_are_one_stderr_line_and_a_valid_catalog_prints_the_bare_seat(self):
        self.fired_bug_fix()
        self.write_user_roles({
            "bug-fix": [{"providerInstanceId": "grok", "model": "grok-4.7"}],
        }, mode="full")
        root = Path(self.temporary.name)
        directory = root / "catalog-dir"
        directory.mkdir()
        binary = root / "catalog-bytes.json"
        binary.write_bytes(b"\xff")
        number = root / "catalog-number.json"
        number.write_bytes(b"42")
        missing = root / "missing-catalog.json"
        truncated = root / "catalog-truncated.json"
        truncated.write_bytes(b"{")
        denied = root / "catalog-denied.json"
        denied.write_bytes(b"{}")
        denied.chmod(0)
        providers_int = root / "providers-int.json"
        providers_int.write_text('{"providers": 42}')
        providers_text = root / "providers-text.json"
        providers_text.write_text('{"providers": "stale"}')
        providers_null = root / "providers-null.json"
        providers_null.write_text('{"providers": [null]}')
        script_catalog = root / "script-catalog.json"
        script_catalog.write_text("{}")
        # Grow the fixture until this interpreter raises RecursionError.
        depth = 20000
        while depth <= 1048576:
            deep_text = "[" * depth + "]" * depth
            try:
                json.loads(deep_text)
            except RecursionError:
                break
            depth *= 2
        else:
            self.fail("nested JSON did not raise RecursionError")
        deep_catalog = root / "catalog-deep.json"
        deep_catalog.write_text(deep_text)
        huge_text = '{"a": ' + "9" * 10000 + "}"
        huge_catalog = root / "catalog-huge.json"
        huge_catalog.write_text(huge_text)
        long_catalog = root / ("a" * 300 + ".json")
        trace = "\n".join([
            "Traceback (most recent call last):",
            '  File "roles.py", line 8, in <module>',
            "    alpha()",
            '  File "roles.py", line 12, in alpha',
            "    beta()",
            '  File "roles.py", line 16, in beta',
            "    gamma()",
            '  File "roles.py", line 20, in gamma',
            "    raise ValueError('boom')",
            "ValueError: boom",
        ])
        rows = [
            {"name": "directory", "catalog": directory, "line": f"brigade: {directory} is a directory"},
            {"name": "not utf-8", "catalog": binary, "line": f"brigade: {binary} is not UTF-8 text"},
            {"name": "json number", "catalog": number, "line": f"brigade: {number} is JSON that is not an object"},
            {"name": "missing", "catalog": missing,
             "line": f"brigade: roles.py show refused the seat: {missing}: catalog not found"},
            {"name": "truncated", "catalog": truncated,
             "prefix": f"brigade: roles.py show refused the seat: {truncated}: invalid JSON:"},
            {"name": "not json", "show": ("not json", 0, ""),
             "line": "brigade: roles.py show printed no JSON object"},
            {"name": "valid", "catalog": CATALOG, "ok": BARE},
        ]
        if not os.access(denied, os.R_OK):
            rows.append({
                "name": "unreadable",
                "catalog": denied,
                "prefix": f"brigade: roles.py show failed on {denied}: PermissionError:",
            })
        rows.extend([
            {"name": "providers int", "catalog": providers_int,
             "prefix": f"brigade: roles.py show failed on {providers_int}: TypeError:"},
            {"name": "providers text", "catalog": providers_text,
             "prefix": f"brigade: roles.py show failed on {providers_text}: TypeError:"},
            {"name": "providers null", "catalog": providers_null,
             "prefix": f"brigade: roles.py show failed on {providers_null}: TypeError:"},
            {"name": "traceback", "catalog": script_catalog, "show": ("", 1, trace + "\n"),
             "line": f"brigade: roles.py show failed on {script_catalog}: ValueError: boom"},
            {"name": "deep json catalog", "catalog": deep_catalog,
             "prefix": f"brigade: roles.py show failed on {deep_catalog}: RecursionError:"},
            {"name": "huge int catalog", "catalog": huge_catalog,
             "prefix": f"brigade: roles.py show failed on {huge_catalog}: ValueError:"},
            {"name": "overlong catalog basename", "catalog": long_catalog,
             "prefix": f"brigade: roles.py show failed on {long_catalog}: OSError:"},
            {"name": "deep json child stdout", "catalog": script_catalog,
             "show": (deep_text, 0, ""),
             "prefix": f"brigade: roles.py show failed on {script_catalog}: RecursionError:"},
            {"name": "huge int child stdout", "catalog": script_catalog,
             "show": (huge_text, 0, ""),
             "prefix": f"brigade: roles.py show failed on {script_catalog}: ValueError:"},
            {"name": "error prefixed frame", "catalog": script_catalog,
             "show": ("", 1, 'error: File "roles.py", line 3, in <module>\n'),
             "line": f"brigade: roles.py show failed on {script_catalog}: exit 1 with no message"},
            {"name": "frame", "catalog": script_catalog,
             "show": ("", 1, '  File "roles.py", line 3, in <module>\n'),
             "line": f"brigade: roles.py show failed on {script_catalog}: exit 1 with no message"},
            {"name": "stderr byte", "catalog": script_catalog, "buffer": True,
             "prefix": f"brigade: roles.py show failed on {script_catalog}:"},
        ])
        glob = self.entry_globals()
        real_built, real_source = glob["BUILT_ROLES"], glob["SOURCE_ROLES"]
        for row in rows:
            with self.subTest(name=row["name"]):
                glob = self.entry_globals()
                glob["BUILT_ROLES"] = real_built
                glob["SOURCE_ROLES"] = real_source
                if "show" in row:
                    text, code, stderr = row["show"]
                    self.install_show(text=text, code=code, stderr=stderr)
                elif row.get("buffer"):
                    script = root / "show-roles.py"
                    script.write_text(
                        "import sys\n"
                        "sys.stderr.buffer.write(b'\\xff')\n"
                        "raise SystemExit(1)\n"
                    )
                    glob["BUILT_ROLES"] = script
                    glob["SOURCE_ROLES"] = script
                outcome = self.launch_call(catalog=row.get("catalog", CATALOG), in_process=True)
                if "ok" in row:
                    self.assertEqual(outcome.code, 0, outcome.stdout_text() + outcome.stderr_text())
                    self.assertEqual(outcome.stdout_text(), row["ok"] + "\n")
                    self.assertEqual(outcome.stderr_text(), "")
                    continue
                stderr = outcome.stderr_text()
                self.assertEqual(outcome.code, 1, stderr)
                self.assertEqual(outcome.stdout_text(), "")
                self.assertEqual(len(stderr.splitlines()), 1, stderr)
                self.assertNotIn("Traceback", stderr)
                self.assertNotIn('File "', stderr)
                if "line" in row:
                    self.assertEqual(stderr, row["line"] + "\n")
                if "prefix" in row:
                    self.assertTrue(stderr.startswith(row["prefix"]), stderr)

    def test_inherit_seats_are_not_one_seat(self):
        self.fired_bug_fix()
        self.write_user_roles({"bug-fix": ["inherit"]}, mode="full")
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
        self.assert_refused("brigade: bug-fix seats are catalog-required, not one seat", in_process=True)

    def test_two_seat_objects_on_bug_fix_refuse_with_the_count(self):
        self.fired_bug_fix()
        self.show_payload({"roles": {"bug-fix": {"seats": [
            {"providerInstanceId": "grok", "model": "grok-4.7"},
            {"providerInstanceId": "codex", "model": "gpt-6.1-sol"},
        ]}}})
        self.assert_refused("brigade: bug-fix has 2 seats", in_process=True)

    def test_zero_seats_refuse_with_the_count(self):
        self.fired_bug_fix()
        self.show_payload({"roles": {"bug-fix": {"seats": []}}})
        self.assert_refused("brigade: bug-fix has 0 seats", in_process=True)

    def test_a_fake_roles_script_reaches_the_command_when_the_run_uses_real_processes(self):
        name = "PSTACK_TEST_REAL_PROCESS"
        previous = os.environ.get(name)
        os.environ[name] = "1"

        def restore():
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous

        self.addCleanup(restore)
        self.fired_bug_fix()
        self.show_payload({"roles": {"bug-fix": {"seats": []}}})
        self.assert_refused("brigade: bug-fix has 0 seats", in_process=True)

    def test_a_missing_role_is_refused(self):
        self.fired_bug_fix()
        self.show_payload({"roles": {}})
        self.assert_refused("brigade: bug-fix is missing from roles.py show", in_process=True)

    def test_a_seat_without_provider_or_model_is_refused(self):
        self.fired_bug_fix()
        self.show_payload({"roles": {"bug-fix": {"seats": [{"model": "grok-4.7"}]}}})
        self.assert_refused("brigade: bug-fix seat lacks providerInstanceId or model", in_process=True)

    def test_options_that_are_not_an_object_are_refused(self):
        self.fired_bug_fix()
        self.show_payload({"roles": {"bug-fix": {"seats": [{
            "providerInstanceId": "grok", "model": "grok-4.7", "options": False,
        }]}}})
        self.assert_refused("brigade: bug-fix seat options are not an object", in_process=True)

    def test_an_unknown_role_name_from_roles_py_is_the_wrapped_refusal(self):
        self.fired_bug_fix()
        path = self.write_user_roles({"not-a-role": ["inherit"]})
        outcome = self.launch_call()
        self.assertEqual(outcome.code, 1, outcome.stdout_text() + outcome.stderr_text())
        self.assertEqual(outcome.stdout_text(), "")
        detail = outcome.stderr_text().strip().removeprefix("brigade: roles.py show refused the seat: ")
        self.assertIn(f"{path}: unknown role 'not-a-role'", detail)
        self.assertFalse(detail.startswith("error:"), detail)

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
        self.assert_launch(XHIGH, in_process=True)

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
