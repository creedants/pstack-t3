if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from ..support import ProcessReason, SCRIPT, _shown_root, os, real_process, runpy, tempfile


class TornWritesMixin:
    def test_a_take_killed_before_its_log_row_finishes_on_the_next_take(self):
        self.handoff()
        died = self.child("die-before-log", "engine", "ticket", "take")
        self.assertEqual(died.returncode, 9, died.stderr)
        self.assertEqual(len(self.rows("engine", "rail.tsv")), 1)
        self.assertEqual(self.rows("engine", "log.tsv"), [])
        self.brigade("engine", "ticket", "take")
        self.assert_one_handed_ticket()

    def test_a_take_killed_inside_the_log_append_recovers_the_tail(self):
        self.handoff()
        died = self.child("die-inside-log-append", "engine", "ticket", "take")
        self.assertEqual(died.returncode, 9, died.stderr)
        self.assertRegex((self.dir("engine") / "log.tsv").read_text(), r"\n[^\n]+\tticket\tT1\twaiting\tfro$")
        self.brigade("engine", "ticket", "take")
        self.assert_one_handed_ticket()

    def test_a_failure_inside_the_temp_file_write_leaves_the_same_end_state(self):
        self.handoff()
        module = runpy.run_path(str(SCRIPT))
        glob = module["run"].__globals__

        class FailingTempfile:
            @staticmethod
            def NamedTemporaryFile(*args, **kwargs):
                handle = tempfile.NamedTemporaryFile(*args, **kwargs)
                write = handle.write

                def fail(text):
                    write(text[:10])
                    handle.flush()
                    raise OSError(28, "No space left on device")

                handle.write = fail
                return handle

        glob["tempfile"] = FailingTempfile
        with self.assertRaises(OSError):
            glob["run"](["--at", str(self.dir("engine")), "ticket", "take"])
        glob["tempfile"] = tempfile
        self.assertEqual(self.rows("engine", "rail.tsv"), [])
        self.assertEqual(self.inbox("engine"), ["app~core~T1.json"])
        self.brigade("engine", "ticket", "take")
        self.assert_one_handed_ticket()

    def test_a_joined_line_stops_the_read_with_the_malformed_message(self):
        self.open("core")
        self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "a")
        log = self.dir("core") / "log.tsv"
        stamp = "2026-10-06T00:00:00.000000+00:00"
        log.write_text(log.read_text() + f"{stamp}\tticket\tT6\twaiting\tfro{stamp}\tticket\tT7\twaiting\tnote\n")
        self.assertEqual(self.brigade("core", "ticket", "list", ok=False), "brigade: app/core/log.tsv line 3 is malformed; fix or remove it")
        log.write_text(log.read_text().replace(f"fro{stamp}\tticket\tT7\twaiting\tnote", "fro"))
        self.assertEqual(self.brigade("core", "ticket", "list"), "T1 waiting normal fix [user] a")
        log.write_text(log.read_text() + f"{stamp[:19]}\tticket")
        self.assertEqual(self.brigade("core", "ticket", "list"), "T1 waiting normal fix [user] a")
        log.write_text(log.read_text() + f"{stamp}\tticket\tT8\twaiting\tnote\n")
        self.assertEqual(self.brigade("core", "ticket", "list", ok=False), "brigade: app/core/log.tsv line 4 is malformed; fix or remove it")
        log.write_text(log.read_text().replace(f"{stamp[:19]}\tticket{stamp}", "2026-10-06"))
        self.assertEqual(self.brigade("core", "ticket", "list", ok=False), "brigade: app/core/log.tsv line 4 is malformed; fix or remove it")

    def test_a_table_that_is_not_utf8_names_the_line(self):
        self.open("core")
        self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "a")
        rail = self.dir("core") / "rail.tsv"
        rail.write_bytes(rail.read_bytes() + b"\xff\n")
        self.assertEqual(self.brigade("core", "ticket", "list", ok=False),
                         "brigade: app/core/rail.tsv line 3 is malformed; fix or remove it")

    def test_a_malformed_line_is_named_with_its_store_from_a_sibling_from_walk_and_from_the_store_itself(self):
        self.open("core")
        self.open("docs")
        self.brigade("docs", "ticket", "add", "--kind", "fix", "--summary", "a")
        rail = self.dir("docs") / "rail.tsv"
        rail.write_text(rail.read_text() + "not a row\n")
        line = "brigade: app/docs/rail.tsv line 3 is malformed; fix or remove it"
        self.assertEqual(self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "x", "--source", "user", "--ref", "gh#1", ok=False),
                         line)
        self.assertEqual(self.brigade("core", "watch", ok=False), line)
        self.assertEqual(self.brigade("docs", "ticket", "list", ok=False), line)
        for words, cwd in ((("walk",), None), (("--at", ".", "ticket", "list"), self.dir("docs"))):
            # inherited environment
            result = real_process(
                ProcessReason.INHERITED_ENV, SCRIPT, ["--store", str(self.store), *words], cwd=cwd,
            )
            self.assertEqual((result.returncode, result.stderr.strip()), (1, line), words)

    def test_walk_prints_every_other_store_and_exits_1_when_one_store_has_a_malformed_table(self):
        for name in ("core", "docs", "engine"):
            self.open(name)
        self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "a")
        self.brigade("docs", "ticket", "add", "--kind", "fix", "--summary", "b")
        rail = self.dir("docs") / "rail.tsv"
        rail.write_text(rail.read_text() + "not a row\n")
        # inherited environment
        walked = real_process(ProcessReason.INHERITED_ENV, SCRIPT, ["--store", str(self.store), "walk"])
        self.assertEqual((walked.returncode, walked.stdout, walked.stderr), (1, "\n".join([
            f"{_shown_root(self.project)}: no landing contract",
            "  core (reports milestones): waiting tickets: 1",
            "    thread not recorded",
            "  docs: app/docs/rail.tsv line 3 is malformed; fix or remove it",
            "  engine (reports milestones): nothing on record",
            "    thread not recorded",
        ]) + "\n", "brigade: app/docs/rail.tsv line 3 is malformed; fix or remove it\n"))

    def test_walk_names_each_malformed_table_on_stderr_when_two_stores_have_one(self):
        for name in ("core", "docs", "engine"):
            self.open(name)
        rail = self.dir("core") / "rail.tsv"
        rail.write_text(rail.read_text() + "not a row\n")
        verdicts = self.dir("engine") / "pass.tsv"
        verdicts.write_text(verdicts.read_text() + "not a row\n")
        # inherited environment
        walked = real_process(ProcessReason.INHERITED_ENV, SCRIPT, ["--store", str(self.store), "walk"])
        self.assertEqual((walked.returncode, walked.stdout, walked.stderr), (1, "\n".join([
            f"{_shown_root(self.project)}: no landing contract",
            "  core: app/core/rail.tsv line 2 is malformed; fix or remove it",
            "  docs (reports milestones): nothing on record",
            "    thread not recorded",
            "  engine: app/engine/pass.tsv line 2 is malformed; fix or remove it",
        ]) + "\n", "brigade: app/core/rail.tsv line 2 is malformed; fix or remove it\n"
                   "brigade: app/engine/pass.tsv line 2 is malformed; fix or remove it\n"))

    def test_a_tail_cut_inside_a_multi_byte_character_is_skipped_by_a_read_and_dropped_by_the_next_append(self):
        self.open("core")
        self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "a")
        rail = self.dir("core") / "rail.tsv"
        complete = rail.read_bytes()
        rail.write_bytes(complete + "T9\t2026-10-10T00:00:00+00:00\twaiting\tuser\t\tcafé".encode()[:-1])
        self.assertEqual(self.brigade("core", "ticket", "list"), "T1 waiting normal fix [user] a")
        self.assertEqual(self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "b"), "T2")
        after = rail.read_bytes()
        self.assertEqual(after[:len(complete)], complete)
        added = after[len(complete):].decode()
        self.assertEqual((added.count("\n"), added[-1]), (1, "\n"))
        fields = added[:-1].split("\t")
        self.assertEqual(fields[:1] + fields[2:], ["T2", "waiting", "user", "", "", "b", "", "", "", "fix", ""])

    def test_a_short_append_restores_the_last_complete_row(self):
        from unittest import mock
        self.open("core")
        self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "a")
        log = self.dir("core") / "log.tsv"
        complete = log.read_text()
        log.write_text(complete + "2026-10-06T00:00:00.000000+00:00\tticket\tT9\twai")
        glob = runpy.run_path(str(SCRIPT))["run"].__globals__
        restaurant = glob["Restaurant"](self.dir("core"))
        row = {"at": "2026-10-06T00:00:01.000000+00:00", "kind": "ticket", "id": "T2", "state": "waiting", "note": "b"}
        real_write = os.write
        with mock.patch.object(os, "write", lambda fd, data: real_write(fd, data[:7])):
            with self.assertRaises(glob["BrigadeError"]) as raised:
                restaurant.append("log.tsv", row)
        self.assertEqual(str(raised.exception), "wrote 7 of 53 bytes to log.tsv; nothing appended")
        self.assertEqual(log.read_text(), complete)
        restaurant.append("log.tsv", row)
        self.assertEqual(log.read_text(), complete + "2026-10-06T00:00:01.000000+00:00\tticket\tT2\twaiting\tb\n")
