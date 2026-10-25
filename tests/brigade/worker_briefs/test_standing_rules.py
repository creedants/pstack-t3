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
    Path,
    ROOT,
    json,
)


class StandingRulesMixin:
    def test_brief_assembles_every_field_and_adds_the_exclusive_rule_for_measuring_stations(self):
        self.open()
        self.fire_one()
        self.record_layout()
        self.brigade("set", "--thread", "thread-coord")
        text = self.brigade("brief", "D1", "--contract", "none", "--goal", "Cold start under 400 ms.", "--acceptance", "Median cold start below 400 ms",
                            "--verify", "npm run perf", "--paths", "src/boot.ts", "--lease", "L4", "--base", "origin/main")
        self.assertTrue(text.startswith("Use the poteto-mode skill and its `perf-issue` playbook."))
        report = f"{self.at}/reports/D1.md"
        for part in ("PURPOSE: Make startup fast.", "TICKETS: T1: Startup is slow", "branch `perf/d1`, started from `origin/main`",
                     "leased to you as L4: src/boot.ts", "- Median cold start below 400 ms", "slot --exclusive --",
                     "A later commit that changes only prose does not rerun it.",
                     "TIMEBOX: 60 minutes. The timebox orders the work and never waives a playbook step (How, Architect, investigation, or the implementation delegate). At the limit, write the report with what remains instead of skipping steps.",
                     f"Write it to {report}", "1. Write in plain engineering prose.",
                     f'call t3_thread_send to thread thread-coord with mode "auto" and the one-line message "D1 done: report at {report}".'):
            self.assertIn(part, text)
        orders = self.brief_body(text).split("STANDING ORDERS:\n", 1)[1]
        numbered = orders.split("\n\n")[-1].splitlines()
        self.assertEqual(len(numbered), 2)
        self.assertEqual(numbered[0], "1. Write in plain engineering prose. brigade's kitchen terms name its files and commands only. Never use them in replies, reports, commits, or PRs.")
        self.assertEqual(numbered[-1], "2. Work lands only through the repository's landing queue (the landing skill). Workers never merge, rebase shared branches, or push trunk.")
        verify = text.split("\nVERIFY:\n", 1)[1].split("\nTIMEBOX:", 1)[0]
        for part in ("While working, run only the affected test modules.",
                     "Run all builds and tests through",
                     "slot -- <command>",
                     "slot --slots 2 -- python3 scripts/run_tests.py -j 8",
                     "When that command is the outermost slot, a governor with fewer than two worker slots refuses it and the suite does not run.",
                     "An affected-module run stays one slot.",
                     "Run the full verification chain once, after the last commit.",
                     "runs at most twice per round",
                     "tests.test_pstack_t3"):
            self.assertIn(part, verify)
        self.assertNotIn("Run builds and tests through", verify)
        self.assertEqual((self.at / "briefs/D1.md").read_text().strip(), self.brief_body(text))

    def test_the_standing_rules_paste_the_failed_test_rule_between_verify_and_timebox(self):
        self.coordinator()
        text = self.brigade(*self.BRIEF)
        rules = text.split("\nVERIFY:\n", 1)[1].split("\nTIMEBOX:", 1)[0]
        self.assertEqual(rules.split("\n\n")[-1].rstrip("\n"), "\n".join([
            "STANDING RULES:",
            f"- Save the full output of any failed test run to a file under {self.at}/reports/ before you rerun it, "
            "and name that file in the report.",
        ]))
        self.assertNotIn("mktemp -d -p", rules)

    def test_the_standing_rules_paste_the_scratch_rule_only_when_the_store_records_a_parent(self):
        self.coordinator()
        # A system directory outside /tmp wherever the checkout is. This test never creates or writes it.
        parent = str(Path("/usr").resolve())
        self.brigade("set", "--scratch-dir", parent)
        text = self.brigade(*self.BRIEF)
        self.assertIn(
            f"- Make every scratch directory, archive copy, and saved output with `mktemp -d -p {parent}`, never "
            "directly under `/tmp`. Delete that directory when you are done. A test suite's own temporary files stay "
            "where the tests put them. Do not set TMPDIR.", text)
        self.brigade("set", "--scratch-dir", "")
        self.assertNotIn("mktemp -d -p", self.brigade(*self.BRIEF))

    def test_the_failed_test_rule_is_dropped_when_the_verify_already_states_it(self):
        self.coordinator()
        reports = f"{self.at}/reports"
        dropped = ("brief", "D1", "--contract", "none", "--goal", "g", "--acceptance", "a",
                   "--paths", "src/a.py", "--lease", "L1", "--base", "origin/main")
        one = self.brigade(*dropped, "--verify", f"Save the full output under {reports}/ after a failed test run, before you rerun it")
        self.assertNotIn("- Save the full output of any failed test run", one)
        self.assertNotIn("STANDING RULES:", one)
        self.assertIn("1. Write in plain engineering prose. brigade's kitchen terms name its files and commands only. Never use them in replies, reports, commits, or PRs.", one)
        self.assertIn("2. Work lands only through the repository's landing queue (the landing skill). Workers never merge, rebase shared branches, or push trunk.", one)

    def test_a_pointer_to_a_failed_run_output_is_not_the_rule_and_the_line_stays(self):
        self.coordinator()
        reports = f"{self.at}/reports"
        dropped = ("brief", "D1", "--contract", "none", "--goal", "g", "--acceptance", "a",
                   "--paths", "src/a.py", "--lease", "L1", "--base", "origin/main")
        text = self.brigade(*dropped, "--verify",
                            f"The first attempt's failed test run output is at {reports}/D1-fail.txt. "
                            "Read it before you rerun the suite.")
        self.assertIn("STANDING RULES:", text)
        self.assertIn("- Save the full output of any failed test run", text)

    def test_an_untagged_house_rules_file_is_pasted_into_the_brief_byte_for_byte(self):
        self.coordinator()
        orders = self.brief_body(self.brigade(*self.BRIEF)).split("STANDING ORDERS:\n", 1)[1]
        self.assertEqual(orders.rstrip("\n"), (self.at / "house-rules.md").read_text().strip())

    def test_a_store_without_a_layout_gets_no_whole_suite_sentence_or_default_suite_command(self):
        """The generic slot sentence and the claims sentences stay; the suite sentence and its governor need a layout."""
        self.coordinator()
        verify = self.brigade(*self.BRIEF).split("\nVERIFY:\n", 1)[1].split("\nTIMEBOX:", 1)[0]
        self.assertIn("Run all builds and tests through", verify)
        self.assertIn("Run the claims check before the final verification.", verify)
        self.assertIn("Run the full verification chain once, after the last commit.", verify)
        self.assertNotIn("--slots 2", verify)
        self.assertNotIn("python3 -m unittest", verify)
        self.assertNotIn("An affected-module run stays one slot.", verify)

    def test_a_new_store_house_rules_hold_the_two_rule_template(self):
        self.open()
        text = (self.at / "house-rules.md").read_text()
        self.assertIn("1. Write in plain engineering prose. brigade's kitchen terms name its files and commands only. Never use them in replies, reports, commits, or PRs.", text)
        self.assertIn("2. Work lands only through the repository's landing queue (the landing skill). Workers never merge, rebase shared branches, or push trunk.", text)
        self.assertNotIn("A test earns its place", text)
        self.assertNotIn("You may write the change yourself", text)

    def test_a_store_whose_house_rules_lack_the_moved_rules_gets_neither_in_its_brief(self):
        self.coordinator()
        (self.at / "house-rules.md").write_text(
            "# House rules: Perf\n\nPaste these into every brief verbatim.\n\n1. Plain rule.\n")
        orders = self.brigade(*self.BRIEF).split("STANDING ORDERS:\n", 1)[1]
        self.assertIn("1. Plain rule.", orders)
        self.assertNotIn("write the change yourself", orders)
        self.assertNotIn("A test earns its place", orders)

    def test_a_brief_keeps_untagged_and_worker_rules_and_leaves_out_reviewer_and_coordinator_rules(self):
        self.coordinator()
        (self.at / "house-rules.md").write_text(
            "# House rules: Perf\n\nPaste these into every brief verbatim.\n\n"
            "1. Plain rule.\n"
            "2. [worker] Worker rule.\n"
            "3. [reviewer] Reviewer rule.\n"
            "4. [coordinator] Coordinator rule.\n")
        orders = self.brigade(*self.BRIEF).split("STANDING ORDERS:\n", 1)[1]
        self.assertIn("1. Plain rule.", orders)
        self.assertIn("2. Worker rule.", orders)
        self.assertNotIn("[worker]", orders)
        self.assertNotIn("Reviewer rule", orders)
        self.assertNotIn("Coordinator rule", orders)

    def test_a_house_rule_tagged_only_coordinator_is_left_out_of_the_brief(self):
        self.coordinator()
        (self.at / "house-rules.md").write_text(
            "# House rules: Perf\n\nPaste these into every brief verbatim.\n\n"
            "1. Plain rule.\n"
            "2. [coordinator] Coordinator rule.\n")
        orders = self.brigade(*self.BRIEF).split("STANDING ORDERS:\n", 1)[1]
        self.assertIn("1. Plain rule.", orders)
        self.assertNotIn("Coordinator rule", orders)

    def test_a_coordinator_rule_takes_its_indented_lines_out_of_the_brief_with_it(self):
        self.coordinator()
        (self.at / "house-rules.md").write_text(
            "# House rules: Perf\n\nPaste these into every brief verbatim.\n\n"
            "1. Plain rule.\n"
            "2. [coordinator] Coordinator rule.\n"
            "    - Sub-bullet of the coordinator rule.\n"
            "3. Rule after it.\n")
        orders = self.brigade(*self.BRIEF).split("STANDING ORDERS:\n", 1)[1]
        self.assertIn("1. Plain rule.", orders)
        self.assertIn("3. Rule after it.", orders)
        self.assertNotIn("Sub-bullet of the coordinator rule", orders)

    def test_the_built_skill_ships_the_verdict_protocol(self):
        text = (ROOT / "skills/brigade/verdict-protocol.md").read_text()
        self.assertIn("Rare:", text)
        self.assertIn("is not one the coordinator may accept as limit", text)
        self.assertIn("mktemp -d -p", text)
        self.assertNotIn("/home/", text)

    def test_a_scratch_path_prefix_does_not_drop_the_scratch_rule(self):
        self.coordinator()
        # A system directory outside /tmp wherever the checkout is. This test never creates or writes it.
        parent = str(Path("/usr").resolve())
        self.brigade("set", "--scratch-dir", parent)
        text = self.brigade(*self.BRIEF, "--context", f"`mktemp -d -p {parent}-old`")
        self.assertIn(
            f"- Make every scratch directory, archive copy, and saved output with `mktemp -d -p {parent}`, never "
            "directly under `/tmp`. Delete that directory when you are done. A test suite's own temporary files stay "
            "where the tests put them. Do not set TMPDIR.", text)

    def test_an_invalid_stored_scratch_dir_names_the_key(self):
        self.briefable()
        meta = json.loads((self.at / "restaurant.json").read_text())
        meta["scratchDir"] = "/tmp/x"
        (self.at / "restaurant.json").write_text(json.dumps(meta))
        before = self.store_files()
        refusal = ("brigade: restaurant.json scratchDir '/tmp/x' is invalid: --scratch-dir takes an absolute "
                   "directory path outside /tmp, or empty string to clear it\n")
        self.assertEqual(self.run_raw("brief", "D1", *self.FLAGS, "--contract", "none"), (1, "", refusal))
        self.assertFalse((self.at / "briefs" / "D1.md").exists())
        self.assertFalse((self.at / "briefs" / "D1.contract.md").exists())
        self.assertEqual(self.store_files(), before)

