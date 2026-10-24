if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from .support import (BrigadeTest, Path, ProcessReason, SCRIPT, _wait_for_path, call_in_process, json,
                      mock, os, real_popen, run_globals, setUpModule, sys)


CHILD = """
import os, runpy, sys, time
from pathlib import Path

case = Path(os.environ["D554_CASE"])
mode = os.environ["D554_MODE"]


def wait(name, timeout=60):
    end = time.monotonic() + timeout
    while not (case / name).exists():
        if time.monotonic() > end:
            print(f"timed out waiting for {name}", file=sys.stderr)
            sys.exit(2)
        time.sleep(0.005)


(case / "started").touch()
module = runpy.run_path(os.environ["D554_SCRIPT"])
if mode == "rename":
    real = os.rename

    def paused(source, target):
        (case / "before-move").touch()
        wait("move-proceed")
        return real(source, target)

    os.rename = paused
else:
    loaded = module["run"].__globals__
    real = loaded["write_atomic"]

    def paused_write(path, text):
        if Path(path).parent.name == "speed":
            (case / "initializing").touch()
            wait("open-proceed")
        return real(path, text)

    loaded["write_atomic"] = paused_write
sys.exit(module["main"](sys.argv[1:]))
"""


class RenameReferenceTest(BrigadeTest):
    """A rename is refused while it reports to the executive admin, while an executive admin request or a handoff names the store, or while --project-root is not the root the store records. A failed rewrite keeps the old store, and a rerun moves a store that already records the new name."""

    def rename(self, new="Speed", project_root=None, **kwargs):
        return self.brigade("open", "--project-root", str(project_root or self.project), "--name", "Perf",
                            "--rename", new, **kwargs)

    def files(self, directory):
        return {str(path.relative_to(directory)): path.read_bytes() for path in sorted(Path(directory).rglob("*"))
                if path.is_file() and path.name != "restaurant.lock"}

    def open_sibling(self, name):
        return self.run_at(self.at.parent / name, "open", "--project-root", str(self.project), "--name", name)

    def open_admin(self):
        return self.run_at(self.at.parent / ".admin", "open", "--admin", "--project-root", str(self.project))

    def admin(self, *args):
        return self.run_at(self.at.parent / ".admin", *args)

    def test_rename_refuses_an_executive_admin_request_to_it(self):
        self.open()
        self.open_admin()
        self.admin("request", "--to", "perf", "do one thing")
        self.assertTrue((self.at / "inbox" / "A1.line").is_file())
        store = self.store_files()
        admin = self.files(self.at.parent / ".admin")
        error = self.rename(code=1)
        self.assertIn("the executive admin recorded A1 for perf", error)
        self.assertEqual(self.store_files(), store)
        self.assertEqual(self.files(self.at.parent / ".admin"), admin)
        self.assertFalse((self.at.parent / "speed").exists())

    def test_rename_refuses_an_admin_request_file_in_its_inbox(self):
        self.open()
        (self.at / "inbox").mkdir()
        (self.at / "inbox" / "A1.line").write_text("do one thing\n")
        store = self.store_files()
        error = self.rename(code=1)
        self.assertIn("inbox/A1.line is an executive admin request", error)
        self.assertEqual(self.store_files(), store)
        self.assertFalse((self.at.parent / "speed").exists())

    def test_rename_refuses_a_coordinator_that_reports_to_the_executive_admin(self):
        self.open()
        self.brigade("set", "--reports-to", "admin-thread-1")
        store = self.store_files()
        error = self.rename(code=1)
        self.assertIn("reports to admin-thread-1", error)
        self.assertEqual(self.store_files(), store)
        self.assertFalse((self.at.parent / "speed").exists())

    def test_rename_refuses_a_ticket_it_handed_to_a_sibling(self):
        self.open()
        self.open_sibling("docs")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
        self.brigade("ticket", "move", "T1", "--to", "docs")
        store = self.store_files()
        sibling = self.files(self.at.parent / "docs")
        error = self.rename(code=1)
        self.assertIn("T1 is moved to docs", error)
        self.assertEqual(self.store_files(), store)
        self.assertEqual(self.files(self.at.parent / "docs"), sibling)
        self.assertFalse((self.at.parent / "speed").exists())

    def test_rename_refuses_a_ticket_a_sibling_took_from_it(self):
        self.open()
        self.open_sibling("docs")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
        self.brigade("ticket", "move", "T1", "--to", "docs")
        self.run_at(self.at.parent / "docs", "ticket", "take")
        store = self.store_files()
        error = self.rename(code=1)
        self.assertIn("T1 in docs came from bridge-kit/perf", error)
        self.assertEqual(self.store_files(), store)
        self.assertFalse((self.at.parent / "speed").exists())

    def test_rename_refuses_a_ticket_a_sibling_moved_to_it(self):
        self.open()
        self.open_sibling("docs")
        self.run_at(self.at.parent / "docs", "ticket", "add", "--kind", "fix", "--summary", "two")
        self.run_at(self.at.parent / "docs", "ticket", "move", "T1", "--to", "perf")
        store = self.store_files()
        error = self.rename(code=1)
        self.assertIn("T1 in docs is moved to perf", error)
        self.assertEqual(self.store_files(), store)
        self.assertFalse((self.at.parent / "speed").exists())

    def test_rename_refuses_a_ticket_it_took_from_a_sibling(self):
        self.open()
        self.open_sibling("docs")
        self.run_at(self.at.parent / "docs", "ticket", "add", "--kind", "fix", "--summary", "two")
        self.run_at(self.at.parent / "docs", "ticket", "move", "T1", "--to", "perf")
        self.brigade("ticket", "take")
        store = self.store_files()
        sibling = self.files(self.at.parent / "docs")
        error = self.rename(code=1)
        self.assertIn("came from bridge-kit/docs/T1", error)
        self.assertEqual(self.store_files(), store)
        self.assertEqual(self.files(self.at.parent / "docs"), sibling)
        self.assertFalse((self.at.parent / "speed").exists())

    def test_rename_refuses_a_project_root_the_store_does_not_record(self):
        self.init_landing()
        self.landing_env()
        self.open()
        self.brigade("set", "--thread", "coordinator")
        self.land("lease", "claim", "--holder", "perf/D55", "--paths", "a.txt", "--owner", "perf/@1")
        other = self.root / "other" / "Bridge Kit"
        other.mkdir(parents=True)
        store = self.store_files()
        error = self.rename(project_root=other, code=1)
        self.assertIn(f"{self.at} records the project root {self.project}", error)
        self.assertEqual(self.store_files(), store)
        self.assertFalse((self.at.parent / "speed").exists())
        self.assertIn("L1", self.land("lease", "list"))

    def test_a_failed_name_rewrite_leaves_the_old_store_and_no_target(self):
        self.open()
        store = self.store_files()
        restaurant = run_globals(self, SCRIPT)["Restaurant"]
        original = restaurant.write

        def write(store, relative, text):
            if relative == "restaurant.json":
                raise OSError("simulated write failure")
            return original(store, relative, text)

        with mock.patch.object(restaurant, "write", write):
            outcome = call_in_process(self, SCRIPT, [
                "--store", str(self.store), "--at", str(self.at), "open",
                "--project-root", str(self.project), "--name", "Perf", "--rename", "Speed"])
        self.assertEqual(outcome.code, 1, outcome.stderr_text())
        self.assertIn("simulated write failure", outcome.stderr_text())
        self.assertEqual(self.store_files(), store)
        self.assertFalse((self.at.parent / "speed").exists())
        self.assertEqual(sorted(path.name for path in self.at.parent.iterdir()), ["perf"])

    def test_a_failed_heading_rewrite_leaves_the_old_store_and_no_target(self):
        self.open()
        store = self.store_files()
        restaurant = run_globals(self, SCRIPT)["Restaurant"]
        original = restaurant.write

        def write(store, relative, text):
            if relative == "menu.md":
                raise OSError("simulated heading write failure")
            return original(store, relative, text)

        with mock.patch.object(restaurant, "write", write):
            outcome = call_in_process(self, SCRIPT, [
                "--store", str(self.store), "--at", str(self.at), "open",
                "--project-root", str(self.project), "--name", "Perf", "--rename", "Speed"])
        self.assertEqual(outcome.code, 1, outcome.stderr_text())
        self.assertIn("simulated heading write failure", outcome.stderr_text())
        self.assertEqual(self.store_files(), store)
        self.assertFalse((self.at.parent / "speed").exists())

    def test_an_open_started_before_the_move_finds_the_moved_store(self):
        """D554 review 3 blocker 1: an open whose process starts before the move completes finds the moved store and initializes nothing."""
        self.open()
        self.brigade("set", "--thread", "coordinator")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
        meta = json.loads((self.at / "restaurant.json").read_text())
        rail = (self.at / "rail.tsv").read_bytes()
        case = Path(self.temporary.name) / "case"
        case.mkdir()
        moving = opening = None
        try:
            moving = real_popen(
                ProcessReason.RACE,
                [sys.executable, "-c", CHILD, "--store", str(self.store), "--at", str(self.at),
                 "--owner", "coordinator@1", "open", "--project-root", str(self.project),
                 "--name", "Perf", "--rename", "Speed"],
                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", D554_CASE=str(case),
                         D554_SCRIPT=str(SCRIPT), D554_MODE="rename"),
                text=True)
            _wait_for_path(case / "before-move")
            opening = real_popen(
                ProcessReason.RACE,
                [sys.executable, "-c", CHILD, "--store", str(self.store),
                 "open", "--project-root", str(self.project), "--name", "Speed"],
                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", D554_CASE=str(case),
                         D554_SCRIPT=str(SCRIPT), D554_MODE="open"),
                text=True)
            _wait_for_path(case / "started")
            (case / "move-proceed").touch()
            move_out, move_err = moving.communicate(timeout=15)
            self.assertEqual(moving.returncode, 0, move_out + move_err)
            (case / "open-proceed").touch()
            open_out, open_err = opening.communicate(timeout=15)
            self.assertEqual(opening.returncode, 0, open_out + open_err)
            self.assertIn("exists", open_out)
            self.assertNotIn("opened", open_out + open_err)
        finally:
            for name in ("before-move", "move-proceed", "open-proceed"):
                (case / name).touch()
            for process in (moving, opening):
                if process is not None and process.poll() is None:
                    process.kill()
                    process.communicate()
        speed = self.at.parent / "speed"
        self.assertFalse(self.at.exists())
        moved = json.loads((speed / "restaurant.json").read_text())
        self.assertEqual(moved["restaurant"], "Speed")
        self.assertEqual(moved["thread"], meta["thread"])
        self.assertEqual(moved["generation"], meta["generation"])
        self.assertEqual((speed / "rail.tsv").read_bytes(), rail)
        self.assertIn(b"T1\t", rail)
        self.assertFalse((self.at.parent / ".rename-speed.json").exists())

    def test_a_rename_refuses_the_directory_a_live_open_just_created(self):
        """D554 review 2 blocker 1: a rename never fills a target another open created before the reservation."""
        self.open()
        self.brigade("set", "--thread", "coordinator")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
        store = self.store_files()
        case = Path(self.temporary.name) / "case"
        case.mkdir()
        opening = real_popen(
            ProcessReason.RACE,
            [sys.executable, "-c", CHILD, "--store", str(self.store),
             "open", "--project-root", str(self.project), "--name", "Speed"],
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", D554_CASE=str(case),
                     D554_SCRIPT=str(SCRIPT), D554_MODE="open"),
            text=True)
        try:
            _wait_for_path(case / "initializing")
            error = self.rename(code=1)
            (case / "open-proceed").touch()
            open_out, open_err = opening.communicate(timeout=15)
        finally:
            (case / "open-proceed").touch()
            if opening.poll() is None:
                opening.kill()
                opening.communicate()
        self.assertIn(str(self.at.parent / "speed"), error)
        self.assertEqual(self.store_files(), store)
        self.assertEqual(opening.returncode, 0, open_err)
        self.assertIn("opened", open_out)
        self.assertFalse((self.at.parent / ".rename-speed.json").exists())
        opened = json.loads((self.at.parent / "speed" / "restaurant.json").read_text())
        self.assertIsNone(opened["thread"])

    def test_a_rename_refuses_a_target_marker_another_store_left(self):
        self.open()
        marker = self.at.parent / ".rename-speed.json"
        marker.write_text('{"from": "docs"}')
        store = self.store_files()
        error = self.rename(code=1)
        self.assertIn(str(marker), error)
        self.assertEqual(self.store_files(), store)
        self.assertFalse((self.at.parent / "speed").exists())
        self.assertTrue(marker.is_file())

    def test_a_rerun_adopts_the_empty_target_an_interrupted_rename_reserved(self):
        self.open()
        self.brigade("set", "--thread", "coordinator")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
        case = Path(self.temporary.name) / "case"
        case.mkdir()
        moving = real_popen(
            ProcessReason.RACE,
            [sys.executable, "-c", CHILD, "--store", str(self.store), "--at", str(self.at),
             "--owner", "coordinator@1", "open", "--project-root", str(self.project),
             "--name", "Perf", "--rename", "Speed"],
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", D554_CASE=str(case),
                     D554_SCRIPT=str(SCRIPT), D554_MODE="rename"),
            text=True)
        try:
            _wait_for_path(case / "before-move")
        finally:
            moving.kill()
            moving.communicate(timeout=15)
        self.assertTrue(self.at.exists())
        self.assertTrue((self.at.parent / "speed").is_dir())
        self.rename()
        self.assertFalse(self.at.exists())
        speed = self.at.parent / "speed"
        self.assertEqual(json.loads((speed / "restaurant.json").read_text())["restaurant"], "Speed")
        self.assertIn(b"T1\t", (speed / "rail.tsv").read_bytes())

    def test_rename_moves_a_store_that_already_records_the_new_name(self):
        """The state an interruption after the name rewrite leaves: the old path, with the new name stored."""
        self.open()
        menu = (self.at / "menu.md").read_bytes()
        meta = json.loads((self.at / "restaurant.json").read_text())
        meta["restaurant"] = "Speed"
        (self.at / "restaurant.json").write_text(json.dumps(meta, indent=2) + "\n")
        self.rename()
        new = self.at.parent / "speed"
        self.assertFalse(self.at.exists())
        self.assertEqual(json.loads((new / "restaurant.json").read_text())["restaurant"], "Speed")
        after = (new / "menu.md").read_bytes()
        self.assertEqual(after.split(b"\n", 1)[0], b"# Menu: Speed")
        self.assertEqual(after.split(b"\n", 1)[1], menu.split(b"\n", 1)[1])
        self.run_at(new, "set", "--reporting", "digest")
        self.assertEqual(json.loads((new / "restaurant.json").read_text())["reporting"], "digest")
