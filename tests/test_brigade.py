import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "t3/added/brigade/scripts/brigade.py"
CODEX = "codex/gpt-6.1-sol"
CLAUDE = "claudeAgent/claude-opus-5-5"


class BrigadeTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = Path(self.temporary.name) / "store"
        self.project = Path(self.temporary.name) / "Bridge Kit"
        self.project.mkdir()
        self.at = self.store / "bridge-kit" / "perf"

    def tearDown(self):
        self.temporary.cleanup()

    def brigade(self, *args, ok=True):
        result = subprocess.run([sys.executable, str(SCRIPT), "--store", str(self.store), "--at", str(self.at), *args],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        return (result.stdout if ok else result.stderr).strip()

    def open(self):
        return self.brigade("open", "--project-root", str(self.project), "--name", "Perf")

    def test_open_creates_the_store_once_and_keeps_edits(self):
        self.assertEqual(self.open(), f"opened {self.at}")
        (self.at / "menu.md").write_text("# Menu: Perf\n\nKeep startup under 400 ms.\n")
        self.assertEqual(self.open(), f"exists {self.at}")
        self.assertEqual((self.at / "menu.md").read_text(), "# Menu: Perf\n\nKeep startup under 400 ms.\n")
        self.assertIn("only through the repository's landing queue", (self.at / "house-rules.md").read_text())
        self.assertIn("Never use them in replies", (self.at / "house-rules.md").read_text())

    def test_fire_groups_waiting_tickets_into_one_dish(self):
        self.open()
        self.assertEqual(self.brigade("ticket", "add", "--summary", "Startup is slow on cold boot", "--source", "github", "--ref", "#12"), "T1")
        self.assertEqual(self.brigade("ticket", "add", "--summary", "Splash screen hangs"), "T2")
        self.assertEqual(self.brigade("fire", "--tickets", "T1,T2", "--station", "perf-issue", "--summary", "Cut cold start time"), "D1")
        self.assertEqual(self.brigade("ticket", "list"),
                         "T1 assigned [github] Startup is slow on cold boot #12\nT2 assigned [user] Splash screen hangs")
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "again", ok=False),
                         "brigade: T1 is assigned, not waiting")

    def test_pass_refuses_a_verifier_from_the_author_family(self):
        self.open()
        self.brigade("ticket", "add", "--summary", "s")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix s")
        error = self.brigade("pass", "record", "D1", "--pr", "u/1", "--sha", "abc", "--verdict", "pass",
                             "--author", CLAUDE, "--verifier", "cursor/claude-sonnet-5-5", ok=False)
        self.assertIn("same model family", error)
        self.assertEqual(self.brigade("pass", "record", "D1", "--pr", "u/1", "--sha", "abc", "--verdict", "pass",
                                      "--author", CLAUDE, "--verifier", "cursor/claude-sonnet-5-5", "--same-family"), "D1 passed")
        self.assertIn("same model family", (self.at / "pass.tsv").read_text())

    def test_queueing_or_merging_needs_a_pass_at_the_current_sha(self):
        self.open()
        self.brigade("ticket", "add", "--summary", "s")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix s")
        self.brigade("pass", "record", "D1", "--pr", "u/1", "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX)
        self.assertEqual(self.brigade("dish", "D1", "--state", "queued", "--sha", "def", ok=False),
                         "brigade: only reviewed work lands: D1 has no review verdict for def")
        self.assertEqual(self.brigade("dish", "D1", "--state", "queued"), "D1 queued")
        self.assertEqual(self.brigade("status"), "waiting to land: 1")
        self.assertEqual(self.brigade("dish", "D1", "--state", "merged"), "D1 merged")
        self.assertEqual(self.brigade("ticket", "list", "--state", "done"), "T1 done [user] s")

    def test_send_back_blocks_landing(self):
        self.open()
        self.brigade("ticket", "add", "--summary", "s")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix s")
        self.brigade("pass", "record", "D1", "--pr", "u/1", "--sha", "abc", "--verdict", "send-back",
                     "--author", CLAUDE, "--verifier", CODEX, "--note", "test asserts the bug")
        self.assertEqual(self.brigade("pass", "check", "D1", "--sha", "abc", ok=False),
                         "brigade: D1 at abc: send-back (test asserts the bug)")
        self.assertEqual(self.brigade("dish", "D1", "--state", "queued", ok=False),
                         "brigade: only reviewed work lands: D1 at abc: send-back (test asserts the bug)")

    def test_report_lists_only_what_changed_since_the_last_report(self):
        self.open()
        self.brigade("ticket", "add", "--summary", "Startup is slow")
        self.brigade("ticket", "add", "--summary", "Splash hangs")
        self.brigade("fire", "--tickets", "T1", "--station", "perf-issue", "--summary", "Cut cold start time")
        self.brigade("86", "add", "--question", "Drop Windows 7 support?", "--options", "yes, no", "--default", "no")
        first = self.brigade("close")
        self.assertIn("## In progress\n\n- D1 (T1): Cut cold start time\n", first)
        self.assertIn("## New tickets, not started\n\n- T2: Splash hangs\n", first)
        self.assertIn("- Q1: Drop Windows 7 support? Options: yes, no. Default if no answer: no.", first)
        self.brigade("pass", "record", "D1", "--pr", "https://github.com/o/r/pull/7", "--sha", "abc", "--verdict", "pass",
                     "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("dish", "D1", "--state", "merged")
        self.brigade("86", "answer", "Q1", "--answer", "no")
        second = self.brigade("close")
        self.assertIn("## Merged\n\n- D1 (T1): Cut cold start time https://github.com/o/r/pull/7", second)
        self.assertNotIn("In progress", second)
        self.assertNotIn("Passed review", second)
        self.assertNotIn("Decisions for you", second)
        self.assertIn("Nothing new.", self.brigade("close"))
        self.assertEqual(len(list((self.at / "closeouts").glob("*.md"))), 3)

    def test_a_dish_that_moves_through_every_state_in_one_report_appears_once(self):
        self.open()
        self.brigade("ticket", "add", "--summary", "s")
        self.brigade("ticket", "add", "--summary", "UI", "--source", "user")
        self.brigade("ticket", "set", "T2", "--state", "dropped")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix s")
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc")
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", "grok/grok-4.7", "--verifier", CODEX)
        self.brigade("dish", "D1", "--state", "merged")
        self.assertEqual(self.brigade("close"), "\n".join([
            "# Perf report", "",
            f"Since opening. merged: 1.", "",
            "## Merged", "", "- D1 (T1): Fix s", "",
            "## Dropped", "", "- T2: UI",
        ]))

    def test_status_and_walk_speak_plain_engineering_prose(self):
        self.open()
        self.brigade("set", "--thread", "thread-1", "--schedule", "report=s-1")
        self.brigade("ticket", "add", "--summary", "s")
        self.brigade("86", "add", "--question", "Ship it?", "--options", "yes, no", "--default", "no")
        self.assertEqual(self.brigade("status"), "waiting tickets: 1, decisions for you: 1")
        walked = self.brigade("walk")
        self.assertIn("Perf (", walked)
        self.assertIn("thread thread-1", walked)
        self.assertIn("Q1: Ship it?", walked)
        self.assertEqual(json.loads((self.at / "restaurant.json").read_text())["schedules"], {"report": "s-1"})
        for word in ("86", "plate", "heard", "chef", "fire", "pass "):
            self.assertNotIn(word, self.brigade("status").lower())

    def test_tabs_and_newlines_in_input_cannot_break_a_table(self):
        self.open()
        self.brigade("ticket", "add", "--summary", "line one\nline\ttwo")
        self.assertEqual(self.brigade("ticket", "list"), "T1 waiting [user] line one line two")

    def test_commands_outside_a_restaurant_fail_with_the_fix(self):
        self.assertIn("run brigade.py open", self.brigade("status", ok=False))


if __name__ == "__main__":
    unittest.main()
