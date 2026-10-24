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
    ROOT,
    SCRIPT,
    runpy,
)


class WaitThreadsMixin:
    def test_the_brief_pastes_the_runtime_seat_rule_unchanged(self):
        runtime = (ROOT / "t3/runtime.md").read_text()
        self.assertIn(f"  ```text\n  {self.SEAT_RULE}\n  ```", runtime)

    def test_the_runtime_still_states_the_bounded_wait(self):
        runtime = (ROOT / "t3/runtime.md").read_text()
        waiting = (ROOT / "t3/added/pstack-runtime/waiting.md").read_text()
        self.assertIn("does not end its turn while a child is open", waiting)
        self.assertIn("Decide completion by `workState` alone", runtime)
        self.assertIn("Its finished turn does not wake the launcher.", runtime)
        self.assertIn("interrupt with `t3_thread_interrupt`", runtime)
        self.assertIn("[waiting.md](waiting.md)", runtime)

    def test_wait_rule_waits_out_an_ended_run(self):
        rule = runpy.run_path(str(SCRIPT))["WAIT_RULE"]
        for phrase in (
            "returns at once while workState is still working or waiting_for_children",
            "t3_thread_wait and timeoutMs 120000 on your own thread",
            "with runId set to its activeRunId from t3_thread_read",
            "never a shell sleep",
            "whose run is still open and whose last message holds the result is stalled after two minutes with no new activity, per step 5",
            "count ten minutes from its last activity item",
        ):
            self.assertIn(phrase, rule)
        self.assertEqual(rule, self.WAIT)

    def test_the_report_section_opens_with_the_bounded_wait(self):
        self.coordinator()
        text = self.brigade(*self.BRIEF)
        lines = text.splitlines()
        start = lines.index("REPORT:")
        write = next(i for i in range(start + 1, len(lines)) if lines[i].startswith("- Write it to "))
        self.assertEqual(lines[start:write + 1], ["REPORT:", self.WAIT, lines[write]])

    def test_the_report_section_names_each_file_by_repository_path(self):
        self.coordinator()
        text = self.brigade(*self.BRIEF)
        self.assertIn("Name each file a follow-up touches by its repository path in backticks.", text)
        write = next(line for line in text.splitlines() if line.startswith("- Write it to "))
        self.assertTrue(write.endswith("Name each file a follow-up touches by its repository path in backticks."))

    def test_the_threads_section_lists_only_t3_threads_and_names_a_harness_child_in_the_body(self):
        self.coordinator()
        text = self.brigade(*self.BRIEF)
        threads = next(line for line in text.splitlines() if line.startswith("- Hold a `## Threads`"))
        self.assertIn("List only T3 threads started with `delegate_task` or `t3_thread_launch`", threads)
        self.assertIn("Name a child run inside the worker's own harness in the report body instead, never in the section.", threads)
        self.assertIn("code delegate, claims check, and architect runner", threads)
        self.assertIn("A missing section adds no row and is not an error.", threads)

    def test_threads_sentence_bars_a_seat_suffix_backticks_and_a_how_explainer(self):
        self.coordinator()
        text = self.brigade(*self.BRIEF)
        threads = next(line for line in text.splitlines() if line.startswith("- Hold a `## Threads`"))
        self.assertIn("one bare provider/model token with nothing after it and no backticks around it", threads)
        self.assertIn("no reasoning level, no note, and no effort word after the seat", threads)
        self.assertIn("The role is one of code delegate, claims check, and architect runner, named literally", threads)
        self.assertIn("List only code delegate, claims check, and architect runner under ## Threads", threads)
        self.assertIn("Put every other child under ## Notes", threads)
        self.assertIn("Name a child run inside the worker's own harness in the report body instead, never in the section.", threads)
        self.assertNotIn("such as a how explainer", threads)
        self.assertNotIn("Name any other child", threads)
        self.assertIn("List only T3 threads started with `delegate_task` or `t3_thread_launch`", threads)

    def test_template_example_thread_item_parses_as_one_child_with_no_unreadable_line(self):
        import re
        self.coordinator()
        text = self.brigade(*self.BRIEF)
        match = re.search(r"- (code delegate|claims check|architect runner) `([^`]+)` (\S+) parses as one child "
                          r"with no unreadable line", text)
        self.assertIsNotNone(match, "no example thread item in the brief template")
        role, thread, seat = match.groups()
        self.assertNotIn("`", seat)
        self.assertIn("/", seat)
        line = "- {} `{}` {}".format(role, thread, seat)
        report_threads = runpy.run_path(str(SCRIPT))["report_threads"]
        children, unreadable = report_threads("findings\n\n## Threads\n\n" + line + "\n")
        self.assertEqual(unreadable, ())
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0]["role"], role)
        self.assertEqual(children[0]["seat"], seat)

    def test_an_orchestrate_brief_carries_the_same_bounded_wait(self):
        self.coordinator(station="orchestrate", mode="full")
        text = self.brigade(*self.BRIEF)
        self.assertTrue(text.startswith("Use the poteto-mode skill and its `orchestrate` playbook.\n"), text[:80])
        self.assertEqual(text.split("REPORT:\n", 1)[1].splitlines()[0], self.WAIT)

