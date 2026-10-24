if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import json
import sqlite3
import unittest
from .support import ARCHIVE, ARCHIVE_CASES, CREDENTIAL, SESSION, OLD, RECENT, P1, P2, PROJECTS, SETTLED, TOOL_ERROR, RPC_ERROR, NO_REPLY, OpsTest, build_database, setUpModule

class ArchiveTest(OpsTest):
    def test_archives_exactly_the_expected_threads_for_each_flag_set(self):
        world = self.world()
        for flags, line, expected in ARCHIVE_CASES:
            with self.subTest(flags=flags):
                done = world.run(ARCHIVE, *flags)
                tail = f"archived {len(expected)}, failed 0\n" if expected else ""
                self.assertEqual((done.returncode, done.stdout, done.stderr), (0, line + "\n" + tail, ""))
                self.assertEqual(sorted(done.archived), sorted(expected))

    def test_dry_run_prints_the_same_summary_and_sends_nothing(self):
        world = self.world()
        for flags, line, _ in ARCHIVE_CASES:
            with self.subTest(flags=flags):
                done = world.run(ARCHIVE, *flags, "--dry-run")
                self.assertEqual((done.returncode, done.stdout, done.stderr), (0, line + "\n", ""))
                self.assertEqual(world.stub.requests, [])

    def test_nothing_to_archive_sends_nothing(self):
        world = self.world()
        done = world.run(ARCHIVE, "--older-than-hours", "72", "--top-level-settled")
        self.assertEqual((done.returncode, done.stdout), (0, "to archive: 0; skipped: live 12, recent 10, under a running thread 5, kept 0\n"))
        self.assertEqual(world.stub.requests, [])

    def test_the_database_file_is_unchanged_after_a_run(self):
        world = self.world()
        before = world.database.read_bytes()
        self.assertEqual(len(world.run(ARCHIVE).archived), 9)
        self.assertEqual(world.database.read_bytes(), before)
        self.assertEqual(sorted(path.name for path in world.database.parent.iterdir()), ["server-runtime.json", "statev2.sqlite"])

    def test_every_request_carries_the_credential_and_every_request_after_the_first_carries_the_session_id(self):
        world = self.world()
        done = world.run(ARCHIVE)
        requests = world.stub.requests
        self.assertEqual([request["method"] for request in requests], ["initialize", "notifications/initialized", *["tools/call"] * 9])
        self.assertEqual({request["path"] for request in requests}, {"/mcp"})
        self.assertEqual({request["authorization"] for request in requests}, {CREDENTIAL})
        self.assertEqual([request["session"] for request in requests], [None, *[SESSION] * 10])
        self.assertEqual([request["params"] for request in requests[2:]], [
            {"name": "t3_thread_organize", "arguments": {"action": "archive", "threadId": name}} for name in done.archived])

    def test_an_event_stream_reply_is_read(self):
        world = self.world()
        world.stub.event_stream = True
        done = world.run(ARCHIVE)
        self.assertEqual((done.returncode, done.stdout.splitlines()[1]), (0, "archived 9, failed 0"))
        self.assertEqual(sorted(done.archived), sorted(ARCHIVE_CASES[0][2]))

    def test_a_failed_archive_is_reported_and_the_rest_continue(self):
        world = self.world()
        world.stub.failures = {**dict.fromkeys(("d-old", "d-pinned", "q-old", "loop-a"), TOOL_ERROR),
                               **dict.fromkeys(("d-parent", "g-old", "loop-b"), RPC_ERROR), "del-kid": NO_REPLY}
        done = world.run(ARCHIVE)
        failures = []
        for number, name in enumerate(done.archived, 3):
            if name in world.stub.failures:
                reply = world.stub.answer({"id": number, "method": "tools/call", "params": {"arguments": {"threadId": name}}})[2]
                failures.append("failed: " + json.dumps(reply)[:200])
        self.assertEqual((sum('"isError": true' in line for line in failures), sum('"error": {"code"' in line for line in failures),
                          failures.count("failed: null")), (4, 2, 1))
        self.assertEqual(done.stdout.splitlines(), [ARCHIVE_CASES[0][1], *failures[:5], "archived 2, failed 7"])
        self.assertEqual((done.returncode, len(done.archived)), (1, 9))

    def test_the_project_title_must_match_exactly_one_project(self):
        world = self.world()
        done = world.run(ARCHIVE, project="no such project")
        self.assertEqual((done.returncode, done.stdout, done.stderr), (1, "", "expected one project titled 'no such project', found 0\n"))
        self.assertEqual(world.stub.requests, [])
        twins = self.world(projects=((P1, "Fixture Project"), (P2, "fixture project")))
        done = twins.run(ARCHIVE)
        self.assertEqual((done.returncode, done.stdout, done.stderr), (1, "", "expected one project titled 'fixture project', found 2\n"))
        self.assertEqual(twins.stub.requests, [])

    def test_a_missing_file_or_update_time_ends_with_a_traceback_and_exit_1(self):
        world = self.world()
        (world.database.parent / "server-runtime.json").unlink()
        done = world.run(ARCHIVE)
        self.assertEqual((done.returncode, done.stdout, done.archived), (1, ARCHIVE_CASES[0][1] + "\n", ()))
        self.assertRegex(done.stderr, r"(?s)^Traceback .*\nFileNotFoundError: ")
        world.database.unlink()
        done = world.run(ARCHIVE)
        self.assertEqual((done.returncode, done.stdout), (1, ""))
        self.assertRegex(done.stderr, r"(?s)^Traceback .*\nsqlite3\.OperationalError: ")
        world = self.world(threads=(("top", P1, None, (), OLD, ""), ("kid", P1, "top", (), None, "S")))
        done = world.run(ARCHIVE)
        self.assertEqual((done.returncode, done.stdout, world.stub.requests), (1, "", []))
        self.assertRegex(done.stderr, r"(?s)^Traceback .*\nTypeError: ")

    def test_the_database_under_archive_t3_home_is_read_and_home_dot_t3_is_not(self):
        world = self.world()
        decoy = world.sandbox.home / ".t3/userdata"
        build_database(decoy / "statev2.sqlite", PROJECTS, (("decoy-top", P1, None, (), OLD, "S"), ("decoy-kid", P1, "decoy-top", (), OLD, "S")))
        (decoy / "server-runtime.json").write_text(json.dumps({"origin": world.stub.origin}))
        done = world.run(ARCHIVE)
        self.assertEqual((done.returncode, sorted(done.archived)), (0, sorted(ARCHIVE_CASES[0][2])))

    def test_a_runless_child_with_a_pending_or_running_node_is_not_selected(self):
        threads = (("top", P1, None, (), OLD, "S"), ("kid-pending", P1, "top", (), OLD, "S"),
                   ("kid-running", P1, "top", (), OLD, "S"), ("kid-old", P1, "top", (), OLD, "S"))
        nodes = (("kid-pending", None, "pending"), ("kid-running", None, "running"))
        world = self.world(threads=threads, nodes=nodes)
        done = world.run(ARCHIVE)
        self.assertEqual(done.returncode, 0)
        self.assertNotIn("kid-pending", done.archived)
        self.assertNotIn("kid-running", done.archived)
        self.assertIn("kid-old", done.archived)
        self.assertNotIn("top", done.archived)

    def test_a_runless_child_with_a_waiting_provider_native_subagent_row_is_not_selected(self):
        threads = (("top", P1, None, (), OLD, "S"), ("kid-waiting", P1, "top", (), OLD, "S"),
                   ("kid-old", P1, "top", (), OLD, "S"))
        subagents = (("top", None, "kid-waiting", "provider_native", "waiting"),)
        world = self.world(threads=threads, subagents=subagents)
        done = world.run(ARCHIVE)
        self.assertEqual(done.returncode, 0)
        self.assertNotIn("kid-waiting", done.archived)
        self.assertNotIn("top", done.archived)
        self.assertNotIn("kid-old", done.archived)

    def test_a_child_whose_rows_are_all_terminal_and_which_is_past_the_cutoff_is_selected(self):
        threads = (("top", P1, None, (), OLD, "S"), ("kid-done", P1, "top", ("completed",), OLD, "S"))
        nodes = (("kid-done", "kid-done-run-0", "completed"),)
        subagents = (("top", "top-run-0", "kid-done", "provider_native", "completed"),)
        world = self.world(threads=threads, nodes=nodes, subagents=subagents)
        done = world.run(ARCHIVE)
        self.assertEqual(done.returncode, 0)
        self.assertIn("kid-done", done.archived)

    def test_a_live_runless_child_protects_its_ancestors(self):
        threads = (("top", P1, None, (), OLD, "S"), ("mid", P1, "top", (), OLD, "S"),
                   ("kid-live", P1, "mid", (), OLD, "S"))
        nodes = (("kid-live", None, "pending"),)
        world = self.world(threads=threads, nodes=nodes)
        done = world.run(ARCHIVE)
        self.assertEqual(done.returncode, 0)
        self.assertNotIn("kid-live", done.archived)
        self.assertNotIn("mid", done.archived)
        self.assertNotIn("top", done.archived)
        self.assertEqual(done.archived, ())

    def test_a_child_under_the_named_coordinator_follows_the_same_rule(self):
        threads = (("coord", P1, None, ("running",), RECENT), ("kid-live", P1, "coord", (), OLD, "S"),
                   ("kid-done", P1, "coord", ("completed",), OLD, "S"))
        nodes = (("kid-live", None, "running"),)
        world = self.world(threads=threads, nodes=nodes)
        done = world.run(ARCHIVE, "--coordinator", "coord")
        self.assertEqual(done.returncode, 0)
        self.assertNotIn("kid-live", done.archived)
        self.assertIn("kid-done", done.archived)

