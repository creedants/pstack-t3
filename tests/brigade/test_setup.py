from .support import BrigadeTest, Path, json, setUpModule


class SetupBoundaryTest(BrigadeTest):
    """The open and set commands refuse a malformed stored setting before they write restaurant.json or the settings file."""

    def settings(self):
        return Path(self.project) / ".claude" / "settings.local.json"

    def meta(self):
        return json.loads((self.at / "restaurant.json").read_text())

    def write_meta(self, **changes):
        meta = self.meta()
        meta.update(changes)
        (self.at / "restaurant.json").write_text(json.dumps(meta))

    def reopen(self, *extra, **kwargs):
        return self.brigade("open", "--project-root", str(self.project), "--name", "Perf", *extra, **kwargs)

    def test_reopen_refuses_a_malformed_caps_and_writes_nothing(self):
        self.open()
        self.write_meta(caps="polish=1")
        self.settings().unlink()
        store = self.store_files()
        error = self.reopen(code=1)
        self.assertIn("brigade: warning: restaurant.json caps 'polish=1' is invalid: it is not an object", error)
        self.assertEqual(self.store_files(), store)
        self.assertFalse(self.settings().exists())

    def test_reopen_refuses_a_malformed_review_entry_and_writes_nothing(self):
        self.open()
        self.write_meta(review={"fix": "fullish"})
        self.settings().unlink()
        store = self.store_files()
        error = self.reopen(code=1)
        self.assertIn("brigade: warning: restaurant.json review 'fullish' is invalid: 'fix' needs full or light", error)
        self.assertEqual(self.store_files(), store)
        self.assertFalse(self.settings().exists())

    def test_reopen_refuses_a_malformed_context_cap_on_the_other_provider_path(self):
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf",
                     "--coordinator-provider", "other")
        self.write_meta(contextCap="400000")
        store = self.store_files()
        error = self.reopen(code=1)
        self.assertIn("brigade: warning: restaurant.json contextCap '400000' is invalid: it is not a whole number",
                      error)
        self.assertEqual(self.store_files(), store)
        self.assertFalse(self.settings().exists())

    def test_set_refuses_a_malformed_context_cap_while_changing_the_provider(self):
        self.open()
        self.write_meta(contextCap="400000")
        store, settings = self.store_files(), self.settings().read_bytes()
        error = self.brigade("set", "--coordinator-provider", "other", code=1)
        self.assertIn("brigade: warning: restaurant.json contextCap '400000' is invalid: it is not a whole number",
                      error)
        self.assertEqual(self.store_files(), store)
        self.assertEqual(self.settings().read_bytes(), settings)

    def test_set_refuses_a_malformed_coordinator_provider_while_changing_the_reporting(self):
        self.open()
        self.write_meta(coordinatorProvider="vm")
        store, settings = self.store_files(), self.settings().read_bytes()
        error = self.brigade("set", "--reporting", "digest", code=1)
        self.assertIn("brigade: warning: restaurant.json coordinatorProvider 'vm' is invalid: it is not claude or other",
                      error)
        self.assertEqual(self.store_files(), store)
        self.assertEqual(self.settings().read_bytes(), settings)

    def test_set_repairs_or_clears_the_malformed_key_it_names(self):
        self.open()
        self.write_meta(contextCap="400000")
        self.brigade("set", "--context-cap", "")
        self.assertNotIn("contextCap", self.meta())
        self.write_meta(coordinatorProvider="vm")
        self.brigade("set", "--coordinator-provider", "claude")
        self.assertEqual(self.meta()["coordinatorProvider"], "claude")
        self.write_meta(review={"fix": "fullish"})
        self.brigade("set", "--review", "fix=light")
        self.assertEqual(self.meta()["review"], {"fix": "light"})

    def test_set_still_refuses_a_malformed_entry_the_command_does_not_name(self):
        self.open()
        self.write_meta(caps={"fix": "1"})
        store = self.store_files()
        error = self.brigade("set", "--cap", "polish=1", code=1)
        self.assertIn("brigade: warning: restaurant.json caps '1' is invalid: 'fix' needs a whole number of 1 or more",
                      error)
        self.assertEqual(self.store_files(), store)


class RoundBudgetSetupTest(BrigadeTest):
    def meta(self):
        return json.loads((self.at / "restaurant.json").read_text())

    def write_meta(self, **changes):
        meta = self.meta()
        meta.update(changes)
        (self.at / "restaurant.json").write_text(json.dumps(meta))

    def test_open_holds_no_round_budget_key_and_the_decision_opens_at_three(self):
        self.fired_bug_fix()
        self.assertNotIn("roundBudget", self.meta())
        error = self.brigade("86", "add", "--dish", "D1", "--round-budget", ok=False)
        self.assertIn("opens at 3", error)

    def test_set_stores_and_clears_the_round_budget(self):
        self.open()
        self.brigade("set", "--round-budget", "2")
        self.assertEqual(self.meta()["roundBudget"], 2)
        self.brigade("set", "--round-budget", "")
        self.assertNotIn("roundBudget", self.meta())

    def test_set_refuses_a_round_budget_below_one_or_not_a_number_and_writes_nothing(self):
        self.open()
        for bad in ("two", "0", "-1"):
            with self.subTest(bad=bad):
                before = self.store_files()
                self.assertEqual(self.brigade("set", "--round-budget", bad, code=1),
                                 f"brigade: --round-budget takes a whole number of 1 or more, got '{bad}'")
                self.assertEqual(self.store_files(), before)

    def test_set_refuses_a_stored_boolean_round_budget_and_writes_nothing(self):
        self.open()
        self.write_meta(roundBudget=True)
        before = self.store_files()
        error = self.brigade("set", "--reporting", "digest", code=1)
        self.assertIn("brigade: warning: restaurant.json roundBudget True is invalid: "
                      "it is not a whole number of 1 or more", error)
        self.assertEqual(self.store_files(), before)


class AffectedTestsSetupTest(BrigadeTest):
    def meta(self):
        return json.loads((self.at / "restaurant.json").read_text())

    def test_set_stores_a_valid_affected_tests_layout_from_inline_json(self):
        self.open()
        layout = {"always": "tests.test_pstack_t3", "entryPoints": ["t3/added/brigade/scripts/brigade.py"]}
        self.brigade("set", "--affected-tests", json.dumps(layout))
        self.assertEqual(self.meta()["affectedTests"], layout)

    def test_set_reads_a_valid_affected_tests_layout_from_a_file(self):
        self.open()
        layout = {"always": "tests.test_pstack_t3", "entryPoints": ["t3/added/brigade/scripts/brigade.py"]}
        path = self.at / "affected.json"
        path.write_text(json.dumps(layout), encoding="utf-8")
        self.brigade("set", "--affected-tests", str(path))
        self.assertEqual(self.meta()["affectedTests"], layout)

    def test_set_reads_a_valid_affected_tests_layout_from_stdin(self):
        self.open()
        layout = {"always": "tests.test_pstack_t3", "entryPoints": ["t3/added/brigade/scripts/brigade.py"]}
        raw = json.dumps(layout)
        self.brigade("set", "--affected-tests", "-", stdin=raw)
        self.assertEqual(self.meta()["affectedTests"], layout)

    def test_set_refuses_an_affected_tests_layout_missing_always_and_writes_nothing(self):
        self.open()
        before = self.store_files()
        error = self.brigade("set", "--affected-tests", '{"entryPoints": ["a.py"]}', code=1)
        self.assertIn("affectedTests", error)
        self.assertEqual(self.store_files(), before)

    def test_set_refuses_unparseable_affected_tests_and_writes_nothing(self):
        self.open()
        before = self.store_files()
        error = self.brigade("set", "--affected-tests", "{bad", code=1)
        self.assertIn("--affected-tests", error)
        self.assertEqual(self.store_files(), before)

    def test_set_refuses_a_non_utf8_affected_tests_file_and_writes_nothing(self):
        self.open()
        path = self.at / "affected.json"
        path.write_bytes(b"\xff\xfe{\n")
        before = self.store_files()
        error = self.brigade("set", "--affected-tests", str(path), code=1)
        self.assertIn("--affected-tests", error)
        self.assertEqual(self.store_files(), before)

    def test_read_json_setting_refuses_a_nul_byte_in_the_affected_tests_path_and_writes_nothing(self):
        self.open()
        before = self.store_files()
        import importlib
        import sys
        from types import SimpleNamespace
        from .support import ROOT
        scripts = str(ROOT / "t3/added/brigade/scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
            self.addCleanup(sys.path.remove, scripts)
        setup_mod = importlib.import_module("brigade_lib.commands.setup")
        shared_mod = importlib.import_module("brigade_lib.shared")
        with self.assertRaises(shared_mod.BrigadeError) as ctx:
            setup_mod._read_json_setting(SimpleNamespace(affected_tests="a\0b"), "affected_tests", "--affected-tests")
        self.assertIn("--affected-tests", str(ctx.exception))
        self.assertEqual(self.store_files(), before)

    def test_set_clears_affected_tests_with_empty_string(self):
        self.open()
        layout = {"always": "tests.test_pstack_t3", "entryPoints": ["a.py"]}
        self.brigade("set", "--affected-tests", json.dumps(layout))
        self.brigade("set", "--affected-tests", "")
        self.assertNotIn("affectedTests", self.meta())


class SharedDocsSetupTest(BrigadeTest):
    def meta(self):
        return json.loads((self.at / "restaurant.json").read_text())

    def test_set_stores_a_valid_shared_docs_catalog_from_inline_json(self):
        self.open()
        catalog = [{"path": "README.md", "kind": "file"}]
        self.brigade("set", "--shared-docs", json.dumps(catalog))
        self.assertEqual(self.meta()["sharedDocs"], catalog)

    def test_set_reads_a_valid_shared_docs_catalog_from_a_file(self):
        self.open()
        catalog = [{"path": "README.md", "kind": "file"}]
        path = self.at / "shared.json"
        path.write_text(json.dumps(catalog), encoding="utf-8")
        self.brigade("set", "--shared-docs", str(path))
        self.assertEqual(self.meta()["sharedDocs"], catalog)

    def test_set_refuses_a_shared_docs_entry_with_a_bad_kind_and_writes_nothing(self):
        self.open()
        before = self.store_files()
        error = self.brigade("set", "--shared-docs", '[{"path": "README.md", "kind": "doc"}]', code=1)
        self.assertIn("sharedDocs", error)
        self.assertEqual(self.store_files(), before)

    def test_set_clears_shared_docs_with_empty_string(self):
        self.open()
        self.brigade("set", "--shared-docs", '[{"path": "README.md", "kind": "file"}]')
        self.brigade("set", "--shared-docs", "")
        self.assertNotIn("sharedDocs", self.meta())


class DocTimeboxSetupTest(BrigadeTest):
    def meta(self):
        return json.loads((self.at / "restaurant.json").read_text())

    def test_set_stores_a_valid_doc_timebox(self):
        self.open()
        self.brigade("set", "--doc-timebox", "60")
        self.assertEqual(self.meta()["docTimebox"], 60)

    def test_set_refuses_a_doc_timebox_below_one_or_not_a_number_and_writes_nothing(self):
        self.open()
        for bad in ("two", "0", "-1"):
            with self.subTest(bad=bad):
                before = self.store_files()
                self.assertEqual(self.brigade("set", "--doc-timebox", bad, code=1),
                                 f"brigade: --doc-timebox takes a whole number of 1 or more, got '{bad}'")
                self.assertEqual(self.store_files(), before)

    def test_set_clears_doc_timebox_with_empty_string(self):
        self.open()
        self.brigade("set", "--doc-timebox", "60")
        self.brigade("set", "--doc-timebox", "")
        self.assertNotIn("docTimebox", self.meta())
