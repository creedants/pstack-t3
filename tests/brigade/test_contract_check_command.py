import json
import shlex
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from .support import BrigadeTest, setUpModule


class ContractCheckCommandTest(BrigadeTest):
    """The affected reader and edits verify take the landing contract's one check when no store records one."""

    CONTRACT_CHECK = "make lint"

    def contract(self, *checks):
        """A landing contract on the project recording these checks, read by in-process brigade calls."""
        self.git_project()
        self.init_landing(*[word for check in checks for word in ("--check", check)])
        self.landing_env()

    def failing_check(self):
        return f"{shlex.quote(sys.executable)} -c 'raise SystemExit(9)'"

    def affected(self):
        return self.brigade("affected", "--root", str(self.project), "--paths", "src/a.py")

    def database(self):
        return next((Path(self.temporary.name) / "state").glob("pstack-t3/landing/*/land.db"))

    def crash_left(self, path, write):
        """Leave path and its log as a killed process does, with the rows write commits in the log and no shared-memory file."""
        scratch = Path(self.temporary.name) / "crash"
        scratch.mkdir()
        writer = sqlite3.connect(str(path), isolation_level=None)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        write(writer)
        shutil.copyfile(path, scratch / "land.db")
        shutil.copyfile(f"{path}-wal", scratch / "land.db-wal")
        writer.close()
        shutil.copyfile(scratch / "land.db", path)
        shutil.copyfile(scratch / "land.db-wal", f"{path}-wal")

    def test_affected_names_the_contract_check_when_the_store_records_no_check_fails_when_the_store_alone_is_read(self):
        # Processes: git and land build the fixture project and its contract.
        self.contract(self.CONTRACT_CHECK)
        self.open()
        out = self.affected()
        self.assertIn(f"slot -- {self.CONTRACT_CHECK}`", out)
        self.assertNotIn("No verify command is set", out)

    def test_edits_verify_runs_its_contract_check_when_the_store_records_no_check_fails_when_the_store_alone_is_read(self):
        # Processes: git and land build the fixture project and its contract; edits verify runs its tools' scripts.
        self.contract(self.CHECK_COMMAND)
        reviewed = self.edits_store()
        self.brigade("set", "--check-command", "")
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        self.assertEqual(self.verify(reviewed, head),
                         (0, f"built out/greeting.txt\nedited src/greeting.txt\nok check\n"
                             f"{self.verified(reviewed, head)}", ""))
        self.assertEqual(len(self.edits_rows()), 1)

    def test_a_stored_check_command_wins_over_the_contract_check_fails_when_the_contract_is_read_first(self):
        # Processes: git and land build the fixture project and its contract.
        self.contract(self.CONTRACT_CHECK)
        self.open()
        self.brigade("set", "--check-command", "make test")
        out = self.affected()
        self.assertIn("slot -- make test`", out)
        self.assertNotIn(self.CONTRACT_CHECK, out)

    def test_a_stored_none_keeps_the_contract_check_unused_fails_when_an_unset_store_falls_back(self):
        # Processes: git and land build the fixture project and its contract.
        self.contract(self.CONTRACT_CHECK)
        self.open()
        self.brigade("set", "--check-command", "none")
        out = self.affected()
        self.assertIn("No verify command is set", out)
        self.assertNotIn(self.CONTRACT_CHECK, out)

    def test_edits_verify_keeps_the_store_check_over_the_contract_check_fails_when_the_contract_is_read_first(self):
        # Processes: git and land build the fixture project and its contract; edits verify runs its tools' scripts.
        self.contract(self.failing_check())
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        self.assertEqual(self.verify(reviewed, head),
                         (0, f"built out/greeting.txt\nedited src/greeting.txt\nok check\n"
                             f"{self.verified(reviewed, head)}", ""))

    def test_no_check_in_the_store_or_the_contract_keeps_the_needs_commands_refusal_fails_when_an_empty_contract_is_taken_as_a_command(self):
        # Processes: git and land build the fixture project and a contract holding no check.
        self.contract()
        reviewed = self.edits_store()
        self.brigade("set", "--check-command", "")
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        self.assertEqual(self.verify(reviewed, head), (1, "", self.NEEDS_COMMANDS))

    def test_two_contract_checks_give_affected_no_command_fails_when_one_of_several_is_taken(self):
        # Processes: git and land build the fixture project and a contract holding two checks.
        self.contract("make lint", "make test")
        self.open()
        out = self.affected()
        self.assertIn("No verify command is set", out)

    def test_a_contract_check_of_none_gives_affected_no_command_fails_when_the_word_none_is_supplied(self):
        # Processes: git and land build the fixture project and a contract whose one check is the word none.
        self.contract("none")
        self.open()
        out = self.affected()
        self.assertIn("No verify command is set", out)

    def test_a_crashed_queue_keeps_its_database_and_log_bytes_fails_when_the_open_is_read_write(self):
        # Processes: git and land build the fixture project and its contract.
        self.contract(self.CONTRACT_CHECK)
        self.open()
        database = self.database()
        self.crash_left(database, lambda db: db.execute(
            "INSERT OR REPLACE INTO contract (key, value) VALUES ('noted', '1')"))
        before = database.read_bytes()
        log = Path(f"{database}-wal")
        log_before = log.read_bytes()
        out = self.affected()
        self.assertEqual(database.read_bytes(), before)
        self.assertEqual(log.read_bytes(), log_before)
        self.assertFalse(Path(f"{database}-shm").exists())
        self.assertIn("No verify command is set", out)

    def test_a_lookup_leaves_a_live_queue_and_another_repositorys_queue_alone_fails_when_every_candidate_is_opened_read_write(self):
        # Processes: git and land build this project and its contract, and git builds the other repository.
        self.contract(self.CONTRACT_CHECK)
        self.open()
        live = self.database()
        holder = sqlite3.connect(str(live), isolation_level=None)
        self.addCleanup(holder.close)
        holder.execute("INSERT OR REPLACE INTO contract (key, value) VALUES ('noted', '1')")
        self.assertTrue(Path(f"{live}-wal").exists())
        live_before = live.read_bytes()
        other_repo = Path(self.temporary.name) / "Other Kit"
        other_repo.mkdir()
        subprocess.run(["git", "init", "-q", str(other_repo)], check=True)
        common = subprocess.run(
            ["git", "-C", str(other_repo), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True, text=True, check=True).stdout.strip()
        other = Path(self.temporary.name) / "state/pstack-t3/landing/other/land.db"
        other.parent.mkdir(parents=True)
        self.crash_left(other, lambda db: (
            db.execute("CREATE TABLE contract (key TEXT PRIMARY KEY, value TEXT)"),
            db.execute("INSERT INTO contract (key, value) VALUES (?, ?)", ("commonDir", json.dumps(common))),
            db.execute("INSERT INTO contract (key, value) VALUES (?, ?)", ("checks", json.dumps(["make test"])))))
        other_before = other.read_bytes()
        out = self.affected()
        self.assertIn(f"slot -- {self.CONTRACT_CHECK}`", out)
        self.assertEqual(live.read_bytes(), live_before)
        self.assertEqual(other.read_bytes(), other_before)
        self.assertTrue(Path(f"{other}-wal").exists())
