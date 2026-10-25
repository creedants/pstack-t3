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
    BrigadeTest,
    Path,
    SCRIPT,
    _owner_words,
    call_in_process,
    json,
    run_globals,
    setUpModule,
)
from ..seat_launch.fixtures import CATALOG, PARENT


SHOW = {"roles": {"feature, refactoring": {"seats": [
    {"providerInstanceId": "grok", "model": "grok-4.7", "options": {}},
]}}}
NOT_A_USAGE_LIMIT = {"decision": "not-usage-limit"}


class BriefFilesMixin:
    def fire_feature(self):
        """A feature dish with no worker row, so the brief after it is attempt one."""
        self.open()
        (self.at / "menu.md").write_text("## Purpose\n\nFast.\n")
        self.brigade("set", "--thread", "c1")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
        self.brigade("fire", "--tickets", "T1", "--station", "feature", "--summary", "s", "--branch", "perf/d1")

    def fix_round_brief(self):
        """A fire, a first brief, a recorded worker, one send-back, and the brief a fix worker reads."""
        self.fire_feature()
        first_text = self.brigade(*self.BRIEF)
        self.brigade("dish", "D1", "--thread", "w1")
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc")
        self.send_back("abc")
        self.brigade("dish", "D1", "--state", "in-progress")
        return first_text, self.brigade(*self.FIX_BRIEF, "--paths", "src/a.py", "--lease", "L1")

    def install_recording_roles(self, record):
        """A roles.py that appends its argv to record, answers show with one role's seat, and answers backup with no usage limit."""
        script = Path(self.temporary.name) / "record-roles.py"
        script.write_text(
            "import sys\n"
            f"with open({str(record)!r}, 'a', encoding='utf-8') as log:\n"
            "    log.write(' '.join(sys.argv[1:]) + '\\n')\n"
            f"sys.stdout.write({json.dumps(SHOW)!r} if sys.argv[1] == 'show' else {json.dumps(NOT_A_USAGE_LIMIT)!r})\n"
        )
        glob = run_globals(self, SCRIPT)
        glob["BUILT_ROLES"] = script
        glob["SOURCE_ROLES"] = script
        return record

    def test_a_first_brief_writes_the_plain_file_name(self):
        self.fire_feature()
        text = self.brigade(*self.BRIEF)
        first = self.at / "briefs" / "D1.md"
        self.assertEqual(first.read_text(), self.brief_body(text) + "\n")
        self.assertFalse((self.at / "briefs" / "D1-2.md").exists())

    def test_a_fix_round_writes_the_second_attempt_file_and_leaves_the_first_byte_identical(self):
        first_text, second_text = self.fix_round_brief()
        first = self.at / "briefs" / "D1.md"
        self.assertEqual(first.read_bytes(), (self.brief_body(first_text) + "\n").encode())
        second = self.at / "briefs" / "D1-2.md"
        self.assertTrue(second.is_file())
        self.assertEqual(second.read_text(), self.brief_body(second_text) + "\n")

    def test_every_reader_reads_the_attempt_the_launch_gets(self):
        """`launch_text` and `record_usage_limit` read the attempt file the launch gets.

        A reader that builds one fixed briefs/<dish>.md path takes the stale first attempt's mode.
        """
        first_text, second_text = self.fix_round_brief()
        current = next(line for line in self.brief_body(second_text).splitlines() if line.startswith("Mode: "))
        stale = "Mode: light" if current == "Mode: full" else "Mode: full"
        older = self.at / "briefs" / "D1.md"
        older.write_text(self.brief_body(first_text).replace(current, stale, 1) + "\n")
        self.assertIn(stale + "\n", older.read_text())
        self.assertNotIn(current + "\n", older.read_text())
        record = self.install_recording_roles(Path(self.temporary.name) / "roles-argv.txt")
        launch = call_in_process(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                                "launch", "D1", "--parent", PARENT, "--catalog", str(CATALOG)])
        self.assertEqual(launch.code, 0, launch.stderr_text())
        error = Path(self.temporary.name) / "usage-limit.txt"
        error.write_text("usage limit reached\n")
        usage = call_in_process(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                               *_owner_words(self.at), "dish", "D1", "--usage-limit", "grok/grok-4.7",
                                               "--role", "feature, refactoring", "--catalog", str(CATALOG),
                                               "--parent", PARENT, "--error", str(error)])
        self.assertEqual(usage.code, 0, usage.stderr_text())
        lines = record.read_text().splitlines()
        self.assertEqual([line.split(" ", 1)[0] for line in lines], ["show", "backup"])
        for name, line in zip(("launch_text", "record_usage_limit"), lines):
            with self.subTest(reader=name):
                self.assertIn("--brief-mode " + current.removeprefix("Mode: "), line)
                self.assertNotIn("--brief-mode " + stale.removeprefix("Mode: "), line)

    def test_the_printed_output_ends_with_an_absolute_path_and_the_launch_message(self):
        self.fire_feature()
        text = self.brigade(*self.BRIEF)
        name = self.at / "briefs" / "D1.md"
        lines = text.splitlines()
        self.assertEqual(lines[-2], str(name))
        self.assertEqual(lines[-1],
                         f"Your brief is the file {name}. Read the whole file first and follow it exactly. "
                         f"It is your full task. Do not edit, move, or delete anything under {self.store.resolve()}.")
        body = self.brief_body(text)
        self.assertEqual(name.read_text(), body + "\n")
        for line in lines[-2:]:
            self.assertNotIn(line, body)


class BriefFilesTest(BrigadeTest, BriefFilesMixin):
    pass
