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
    CODEX,
    _shown_root,
    json,
    subprocess,
)


class VerifyCommandMixin:
    def test_an_empty_base_is_refused_and_writes_nothing(self):
        self.briefable()
        refusal = "brigade: brief --base takes a trunk ref, such as origin/main\n"
        flagged = ("brief", "D1", "--contract", "none", "--goal", "g", "--acceptance", "a", "--verify", "v",
                   "--paths", "src/boot.ts", "--lease", "L4")
        for base in ("", "   "):
            with self.subTest(form="flag", base=base):
                before = self.store_files()
                self.assertEqual(self.run_raw(*flagged, "--base", base), (1, "", refusal))
                self.assertFalse((self.at / "briefs" / "D1.md").exists())
                self.assertFalse((self.at / "briefs" / "D1.contract.md").exists())
                self.assertEqual(self.store_files(), before)
        for base in ("", "   "):
            with self.subTest(form="fields", base=base):
                fields = self.at / "brief-fields.json"
                fields.write_text(json.dumps({"goal": "g", "acceptance": ["a"], "verify": "v", "base": base,
                                              "paths": "src/boot.ts", "lease": "L4", "contract": "none"}),
                                  encoding="utf-8")
                before = self.store_files()
                self.assertEqual(self.run_raw("brief", "D1", "--fields", str(fields)), (1, "", refusal))
                self.assertFalse((self.at / "briefs" / "D1.md").exists())
                self.assertFalse((self.at / "briefs" / "D1.contract.md").exists())
                self.assertEqual(self.store_files(), before)

    def test_ticket_list_startable_watch_86_list_close_walk_pass_check_and_contract_print_for_a_store_from_before_kinds_what_the_tool_printed_before_kinds_and_change_no_table(self):
        self.store_from_before_kinds()
        before = self.tables()
        waiting = ["T8 waiting normal [user] Importer crashes",
                   "T9 waiting normal [user] Tune the pool",
                   "T10 waiting low [report] Tidy the guide perf/reports/D7.md#1",
                   "T11 waiting urgent [user] Pick a database",
                   "T12 waiting low [user] Trim the pool"]
        self.assertEqual(self.brigade("ticket", "list").splitlines(), [
            "T1 assigned normal [user] Cold start is slow",
            "T2 assigned urgent [github] Splash hangs https://github.com/o/r/issues/2",
            "T3 assigned normal [user] Cache misses",
            "T4 assigned normal [user] Login loops",
            "T5 assigned normal [user] Export stalls",
            "T6 assigned low [user] Footer overlaps",
            "T7 done normal [user] Dates are wrong",
            *waiting,
            "T13 dropped normal [user] Old idea"])
        self.assertEqual(self.brigade("ticket", "list", "--state", "waiting").splitlines(), waiting)
        self.assertEqual(self.brigade("startable").splitlines(), [
            self.OLD_WORKERS,
            "waiting tickets: 5, blocked: 1, unknown: 1, decisions: 1, below auto-start: 2",
            "T11 urgent, 0m: decision for the owner",
            "T8 normal, 0m: unknown, no paths recorded",
            "T9 normal, 0m: blocked, waiting for an idle worker; a.txt",
            "T10 low, 0m: below auto-start; docs/guide.md",
            "T12 low, 0m: below auto-start; a.txt"])
        self.assertEqual(self.brigade("watch").splitlines(), [
            self.OLD_WORKERS, "D1: running 0m of 60m (thread w1)", "D2: in review", "D3: passed, not submitted", "D4: sent back",
            "D5: parked", "D6: queued, but no entry at s6"])
        self.assertEqual(self.brigade("86", "list"), "Q1: Drop Windows 7 support? Options: yes, no. Default: no.")
        code, report, err = self.close_run("--dry-run")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(report.splitlines(), [
            "# Perf report", "",
            f"Since opening. {self.OLD_COUNTS}.", "",
            "## Merged", "", "- D7 (T7): Fix the dates https://github.com/o/r/pull/7", "",
            "## Passed review, not submitted", "", "- D3 (T3): Fix the cache", "",
            "## Waiting to land", "", "- D6 (T6): Fix the footer", "",
            "## Sent back after review", "", "- D4 (T4): Fix the login", "",
            "## Blocked", "", "- D5 (T5): Rework the export", "",
            "## In progress", "", "- D1 (T1): Fix the cold start", "- D2 (T2): Fix the splash https://github.com/o/r/pull/2", "",
            "## New tickets, not started", "",
            "- T8: Importer crashes", "- T9: Tune the pool", "- T10: Tidy the guide", "- T11: Pick a database", "- T12: Trim the pool", "",
            "## Dropped", "", "- T13: Old idea", "- D8 (T8): Split the importer", "",
            "## Decisions for you", "", "- Q1: Drop Windows 7 support? Options: yes, no. Default if no answer: no.",
            "", "## Send-back reasons (last 7 days)", "", "unrecorded 1",
            "", "## Reviewer hit rate (last 7 days)", "",
            "codex/gpt-6.1-sol  rounds 4  pass 3  pass-with-edits 0  send-backs 1  upheld 0  overruled 0  rate n/a",
            "", "## Spend", "", "goal not recorded  wall 0m (6 open)  rounds 4  send-backs 1  threads not recorded",
            "", "## Follow-up share", "",
            "- 1 of 13 tickets created in the last 14 days came from a report (8%)."])
        self.assertEqual(self.brigade("walk").splitlines(), [
            f"{_shown_root(self.project)}: local mode onto refs/landing/lane. empty. author policy: any",
            f"  Perf (reports milestones): {self.OLD_COUNTS}",
            "    thread not recorded",
            "    Q1: Drop Windows 7 support?"])
        self.assertEqual(self.brigade("pass", "check", "D3", "--sha", "s3"), f"D3 at s3 passed review by {CODEX}")
        self.assertEqual(self.brigade("pass", "check", "D2", "--sha", "s2", ok=False), "brigade: D2 has no review verdict for s2")
        self.assertEqual(self.brigade("contract", "D1", ok=False), "brigade: D1 has no contract on record; run brief with --contract")
        self.assertEqual(self.tables(), before)
        self.assertFalse((self.at / "goals.tsv").exists())

    def test_omitted_verify_runs_the_check_command_and_names_no_affected_modules_fails_when_the_picker_runs_without_a_layout(self):
        self.started()
        self.brigade("set", "--thread", "thread-coord")
        self.brigade("set", "--check-command", "python3 scripts/check.py")
        text = self.brigade("brief", "D1", "--contract", "none", "--goal", "g", "--acceptance", "a",
                            "--paths", "README.md", "--lease", "L1", "--base", "origin/main")
        verify = text.split("\nVERIFY:\n", 1)[1].split("\nTIMEBOX:", 1)[0]
        self.assertIn("slot -- python3 scripts/check.py", verify)
        self.assertIn("picker is off", verify)
        self.assertNotIn("tests.test_pstack_t3", verify)
        self.assertNotIn("run_tests.py", verify)
        self.assertNotIn("affected modules while working", verify)
        self.assertEqual((self.at / "briefs/D1.md").read_text().strip(), self.brief_body(text))

    def test_no_layout_and_no_check_command_says_no_verify_command_is_set(self):
        self.started()
        self.brigade("set", "--thread", "thread-coord")
        text = self.brigade("brief", "D1", "--contract", "none", "--goal", "g", "--acceptance", "a",
                            "--paths", "README.md", "--lease", "L1", "--base", "origin/main")
        verify = text.split("\nVERIFY:\n", 1)[1].split("\nTIMEBOX:", 1)[0]
        self.assertIn("No verify command is set", verify)
        self.assertIn("picker is off", verify)
        self.assertNotIn("tests.test_pstack_t3", verify)

    def test_a_malformed_layout_refuses_the_brief_fails_when_the_value_is_ignored(self):
        self.briefable()
        self.record_layout("tests.test_pstack_t3")
        error = self.brigade("brief", "D1", *self.FLAGS, "--contract", "none", ok=False)
        self.assertIn("restaurant.json", error)
        self.assertIn("affectedTests", error)
        self.assertIn("'tests.test_pstack_t3'", error)
        self.assertFalse((self.at / "briefs" / "D1.md").exists())
        self.assertFalse((self.at / "briefs" / "D1.contract.md").exists())

    def test_a_layout_whose_always_module_is_not_the_change_note_module_gets_no_such_claim_fails_when_it_is_generic(self):
        self.git_project()
        self.briefable()
        self.record_layout({"always": "checks.smoke", "entryPoints": [], "suite": "python3 -m pytest -q",
                            "python": "/usr/bin/python3.11"})
        text = self.brigade("brief", "D1", "--contract", "none", "--goal", "g", "--acceptance", "a",
                            "--paths", "src/boot.ts", "--lease", "L1", "--base", "origin/main")
        verify = text.split("\nVERIFY:\n", 1)[1].split("\nTIMEBOX:", 1)[0]
        self.assertIn("The final run of the chain also includes `python3 -m unittest checks.smoke`.", verify)
        self.assertNotIn("because that module checks the change note", verify)

    def test_supplied_verify_unchanged_fails_when_override_ignored(self):
        self.briefable()
        text = self.brigade("brief", "D1", *self.FLAGS, "--contract", "none")
        verify = text.split("\nVERIFY:\n", 1)[1].split("\nTIMEBOX:", 1)[0]
        self.assertTrue(verify.startswith("- v\n"))
        self.assertNotIn("Affected test modules are", verify)
        self.assertNotIn("No affected mapping", verify)

    def test_mapped_verify_lists_modules_and_chain_fails_when_mapped_chain_missing(self):
        self.started(paths="src/a.py")
        self.record_layout()
        self.brigade("set", "--build-command", "python3 scripts/build.py",
                     "--check-command", "python3 scripts/check.py")
        self.brigade("set", "--thread", "thread-coord")
        (self.project / "src").mkdir(parents=True, exist_ok=True)
        (self.project / "src/a.py").write_text("x = 1\n")
        (self.project / "tests").mkdir(parents=True, exist_ok=True)
        (self.project / "tests/test_a.py").write_text(
            "from pathlib import Path\nROOT = Path(__file__).resolve().parents[1]\n"
            "def test_a():\n    (ROOT / \"src/a.py\").read_text()\n")
        subprocess.run(["git", "add", "-A"], cwd=self.project, check=True, capture_output=True, text=True)
        text = self.brigade("brief", "D1", "--contract", "none", "--goal", "g", "--acceptance", "a",
                            "--paths", "src/a.py", "--lease", "L1", "--base", "origin/main")
        verify = text.split("\nVERIFY:\n", 1)[1].split("\nTIMEBOX:", 1)[0]
        self.assertIn("Affected test modules are", verify)
        self.assertIn("tests.test_a", verify)
        self.assertIn("in the foreground as", verify)
        self.assertIn("3.10", verify)
        self.assertIn("scripts/build.py", verify)
        self.assertIn("no regeneration diff", verify)
        self.assertIn("scripts/check.py", verify)
        self.assertIn("foreground slot", verify)
        self.assertIn("affected modules while working", verify)
        self.assertNotIn("No affected mapping", verify)

    def test_fallback_verify_lists_unmapped_and_chain_fails_when_fallback_chain_missing(self):
        self.started()
        self.record_layout()
        self.brigade("set", "--build-command", "python3 scripts/build.py",
                     "--check-command", "python3 scripts/check.py")
        self.brigade("set", "--thread", "thread-coord")
        text = self.brigade("brief", "D1", "--contract", "none", "--goal", "g", "--acceptance", "a",
                            "--paths", "README.md", "--lease", "L1", "--base", "origin/main")
        verify = text.split("\nVERIFY:\n", 1)[1].split("\nTIMEBOX:", 1)[0]
        self.assertIn("No affected mapping", verify)
        self.assertIn("README.md", verify)
        self.assertIn("full suite in the foreground", verify)
        self.assertIn("slot --slots 2", verify)
        self.assertIn("3.10", verify)
        self.assertIn("scripts/build.py", verify)
        self.assertIn("no regeneration diff", verify)
        self.assertIn("scripts/check.py", verify)
        self.assertIn("foreground slot", verify)
