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
from pathlib import Path
from .support import ARCHIVE, ARCHIVE_CASES, CREDENTIAL, SESSION, OLD, RECENT, P1, P2, PROJECTS, OpsTest, setUpModule

class ArchiveBatchTest(OpsTest):
    def test_the_selection_flag_prints_the_expected_json_and_never_constructs_the_client(self):
        world = self.world()
        done = world.sandbox.run(ARCHIVE, "--project", "fixture project", "--selection-only")
        self.assertEqual((done.returncode, done.stderr, world.stub.requests), (0, "", []))
        self.assertEqual(sorted(json.loads(done.stdout)), sorted(ARCHIVE_CASES[0][2]))

    def test_plain_selection_only_prints_the_sorted_json_array_and_nothing_else(self):
        world = self.world()
        done = world.sandbox.run(ARCHIVE, "--project", "fixture project", "--selection-only")
        self.assertEqual((done.returncode, done.stdout, done.stderr, world.stub.requests),
                         (0, json.dumps(sorted(ARCHIVE_CASES[0][2])) + "\n", "", []))

    def test_batch_files_hold_the_same_ids_in_the_same_order_as_plain_selection_only(self):
        world = self.world()
        plain = world.sandbox.run(ARCHIVE, "--project", "fixture project", "--selection-only")
        batch = world.sandbox.root / "batches"
        before = world.database.read_bytes()
        done = world.sandbox.run(ARCHIVE, "--project", "fixture project", "--selection-only", "--batch-dir", str(batch))
        ids = json.loads(plain.stdout)
        self.assertEqual(ids, sorted(ARCHIVE_CASES[0][2]))
        self.assertEqual((batch / "batch-1.txt").read_text().splitlines(), ids)
        self.assertEqual((done.returncode, done.stdout, done.stderr),
                         (0, f"{batch / 'batch-1.txt'}\t{len(ids)}\n", ""))
        self.assertEqual([path.name for path in batch.iterdir()], ["batch-1.txt"])
        self.assertEqual(world.database.read_bytes(), before)
        self.assertEqual(world.stub.requests, [])

    def test_a_batch_size_of_2_over_5_candidates_gives_files_of_2_2_and_1(self):
        threads = (("top", P1, None, (), OLD, ""),
                   *[(f"n-{n}", P1, "top", (), OLD, "S") for n in range(5)])
        world = self.world(threads=threads)
        batch = world.sandbox.root / "batches"
        done = world.sandbox.run(ARCHIVE, "--project", "fixture project", "--selection-only",
                                 "--batch-dir", str(batch), "--batch-size", "2")
        files = [batch / "batch-1.txt", batch / "batch-2.txt", batch / "batch-3.txt"]
        groups = [["n-0", "n-1"], ["n-2", "n-3"], ["n-4"]]
        self.assertEqual([path.read_text().splitlines() for path in files], groups)
        self.assertEqual(done.stdout, "".join(f"{path}\t{len(group)}\n" for path, group in zip(files, groups)))
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertEqual(sorted(path.name for path in batch.iterdir()), ["batch-1.txt", "batch-2.txt", "batch-3.txt"])

    def test_default_batch_size_splits_141_candidates_into_140_and_1(self):
        threads = (("top", P1, None, (), OLD, ""),
                   *[(f"n-{n:03d}", P1, "top", (), OLD, "S") for n in range(141)])
        world = self.world(threads=threads)
        batch = world.sandbox.root / "batches"
        done = world.sandbox.run(ARCHIVE, "--project", "fixture project", "--selection-only",
                                 "--batch-dir", str(batch))
        ids = [f"n-{n:03d}" for n in range(141)]
        self.assertEqual((done.returncode, done.stderr, world.stub.requests), (0, "", []))
        self.assertEqual(sorted(path.name for path in batch.iterdir()), ["batch-1.txt", "batch-2.txt"])
        self.assertEqual((batch / "batch-1.txt").read_text().splitlines(), ids[:140])
        self.assertEqual((batch / "batch-2.txt").read_text().splitlines(), ids[140:])
        self.assertEqual(done.stdout, f"{batch / 'batch-1.txt'}\t140\n{batch / 'batch-2.txt'}\t1\n")

    def test_batch_dir_with_missing_parent_writes_nothing_and_exits_1(self):
        world = self.world()
        batch = world.sandbox.root / "no-parent" / "batches"
        done = world.sandbox.run(ARCHIVE, "--project", "fixture project", "--selection-only",
                                 "--batch-dir", str(batch))
        self.assertEqual((done.returncode, done.stdout, world.stub.requests), (1, "", []))
        self.assertFalse(batch.exists())
        self.assertFalse(batch.parent.exists())

    def test_dry_run_with_selection_only_prints_sorted_json(self):
        world = self.world()
        done = world.sandbox.run(ARCHIVE, "--project", "fixture project", "--dry-run", "--selection-only")
        self.assertEqual((done.returncode, done.stdout, done.stderr, world.stub.requests),
                         (0, json.dumps(sorted(ARCHIVE_CASES[0][2])) + "\n", "", []))

    def test_direct_run_without_credential_exits_1_with_summary_on_stdout_and_one_stderr_line(self):
        world = self.world()
        done = world.sandbox.run(ARCHIVE, "--project", "fixture project")
        self.assertEqual(done.returncode, 1)
        self.assertEqual(done.stdout, ARCHIVE_CASES[0][1] + "\n")
        self.assertEqual(world.stub.requests, [])
        self.assertIn("T3_CODE_MCP_AUTHORIZATION", done.stderr)
        self.assertEqual(len(done.stderr.strip().splitlines()), 1)

    def test_unknown_flag_exits_2(self):
        world = self.world()
        done = world.sandbox.run(ARCHIVE, "--project", "fixture project", "--no-such-flag")
        self.assertEqual((done.returncode, done.stdout, world.stub.requests), (2, "", []))
        self.assertIn("--no-such-flag", done.stderr)

    def test_an_id_containing_percent_and_colon_is_written_unchanged(self):
        name = "a%b:c"
        world = self.world(threads=(("top", P1, None, (), OLD, ""), (name, P1, "top", (), OLD, "S")))
        batch = world.sandbox.root / "batches"
        done = world.sandbox.run(ARCHIVE, "--project", "fixture project", "--selection-only", "--batch-dir", str(batch))
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertEqual((batch / "batch-1.txt").read_bytes(), b"a%b:c\n")
        self.assertEqual(done.stdout, f"{batch / 'batch-1.txt'}\t1\n")

    def test_zero_candidates_creates_the_directory_and_no_batch_file(self):
        world = self.world()
        batch = world.sandbox.root / "batches"
        done = world.sandbox.run(ARCHIVE, "--project", "fixture project", "--older-than-hours", "72",
                                 "--selection-only", "--batch-dir", str(batch))
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "", ""))
        self.assertEqual(list(batch.iterdir()), [])
        self.assertTrue(batch.is_dir())
        self.assertEqual(world.stub.requests, [])

    def test_an_existing_directory_is_refused(self):
        world = self.world()
        batch = world.sandbox.root / "batches"
        batch.mkdir()
        (batch / "keep.txt").write_text("stay\n")
        done = world.sandbox.run(ARCHIVE, "--project", "fixture project", "--selection-only", "--batch-dir", str(batch))
        self.assertEqual((done.returncode, done.stdout, done.stderr),
                         (1, "", f"{ARCHIVE.name}: {batch} already exists\n"))
        self.assertEqual([path.name for path in batch.iterdir()], ["keep.txt"])
        self.assertEqual((batch / "keep.txt").read_text(), "stay\n")
        self.assertEqual(world.stub.requests, [])

    def test_an_existing_file_is_refused(self):
        world = self.world()
        batch = world.sandbox.root / "batches"
        batch.write_text("stay\n")
        done = world.sandbox.run(ARCHIVE, "--project", "fixture project", "--selection-only", "--batch-dir", str(batch))
        self.assertEqual((done.returncode, done.stdout, done.stderr),
                         (1, "", f"{ARCHIVE.name}: {batch} already exists\n"))
        self.assertEqual(batch.read_text(), "stay\n")
        self.assertEqual(world.stub.requests, [])

    def test_batch_dir_without_selection_only_is_refused(self):
        world = self.world()
        batch = world.sandbox.root / "batches"
        done = world.sandbox.run(ARCHIVE, "--project", "fixture project", "--batch-dir", str(batch))
        self.assertEqual((done.returncode, done.stdout, done.stderr),
                         (2, "", f"{ARCHIVE.name}: --batch-dir needs --selection-only\n"))
        self.assertFalse(batch.exists())
        self.assertEqual(world.stub.requests, [])

    def test_batch_dir_works_with_t3_code_mcp_authorization_unset(self):
        world = self.world()
        batch = world.sandbox.root / "batches"
        done = world.sandbox.run(ARCHIVE, "--project", "fixture project", "--selection-only", "--batch-dir", str(batch))
        self.assertEqual((done.returncode, done.stderr, world.stub.requests), (0, "", []))
        self.assertEqual((batch / "batch-1.txt").read_text().splitlines(), sorted(ARCHIVE_CASES[0][2]))
        self.assertEqual(done.stdout, f"{batch / 'batch-1.txt'}\t9\n")

    def test_a_batch_size_below_1_is_an_argparse_error(self):
        world = self.world()
        batch = world.sandbox.root / "batches"
        for value in ("0", "-1"):
            with self.subTest(value=value):
                done = world.sandbox.run(ARCHIVE, "--project", "fixture project", "--selection-only",
                                         "--batch-dir", str(batch), "--batch-size", value)
                self.assertEqual((done.returncode, done.stdout, world.stub.requests), (2, "", []))
                self.assertIn("--batch-size", done.stderr)
                self.assertFalse(batch.exists())

    def test_without_the_flag_and_without_the_variable_the_exit_is_non_zero_and_the_line_names_the_variable_and_the_flag(self):
        world = self.world()
        done = world.sandbox.run(ARCHIVE, "--project", "fixture project")
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(world.stub.requests, [])
        self.assertIn("T3_CODE_MCP_AUTHORIZATION", done.stderr)
        self.assertIn("--selection-only", done.stderr)
        self.assertEqual(len(done.stderr.strip().splitlines()), 1)

