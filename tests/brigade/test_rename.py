from .support import (BrigadeTest, Path, ProcessReason, SCRIPT, _wait_for_path, call_in_process, datetime, json,
                      mock, os, real_popen, run_globals, setUpModule, subprocess, sys, time, timezone)


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
elif mode == "claim":
    real = Path.mkdir

    def paused_mkdir(self, *args, **kwargs):
        if self.name == "speed":
            (case / os.environ["D554_SIGNAL"]).touch()
            wait(os.environ["D554_SIGNAL"] + "-go")
        return real(self, *args, **kwargs)

    Path.mkdir = paused_mkdir
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


class RenameTest(BrigadeTest):
    """A rename moves the store and records the new name, and is refused while a landing hold, a dish, or the target path blocks it."""

    def rename(self, new="Speed", *extra, **kwargs):
        return self.brigade("open", "--project-root", str(self.project), "--name", "Perf",
                            "--rename", new, *extra, **kwargs)

    def tables_at(self, directory):
        return {path.name: path.read_bytes() for path in sorted(Path(directory).glob("*.tsv"))}

    def race_child(self, mode, args, signal=None):
        """A real brigade process that CHILD pauses at the step mode names: before os.rename, before mkdir of speed, or before the first write into speed."""
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", D554_CASE=str(self.case),
                   D554_SCRIPT=str(SCRIPT), D554_MODE=mode, D554_SIGNAL=signal or "")
        return real_popen(ProcessReason.RACE, [sys.executable, "-c", CHILD, *args], env=env, text=True)

    def rename_child(self, mode, signal):
        """A rename of Perf to Speed, paused in mode."""
        return self.race_child(mode, ["--store", str(self.store), "--at", str(self.at),
                                      "--owner", "coordinator@1", "open", "--project-root", str(self.project),
                                      "--name", "Perf", "--rename", "Speed"], signal)

    def open_child(self):
        """An open --name Speed, which needs no --at and no --owner."""
        return self.race_child("open", ["--store", str(self.store), "open",
                                        "--project-root", str(self.project), "--name", "Speed"])

    def test_rename_moves_the_store_and_new_name_reaches_meta_and_both_headers(self):
        self.open()
        tables = self.tables()
        menu = (self.at / "menu.md").read_bytes()
        rules = (self.at / "house-rules.md").read_bytes()
        new = self.at.parent / "speed"
        self.rename()
        self.assertFalse(self.at.exists())
        self.assertEqual(json.loads((new / "restaurant.json").read_text())["restaurant"], "Speed")
        for name, heading, before in (("menu.md", "# Menu: Speed", menu),
                                      ("house-rules.md", "# House rules: Speed", rules)):
            after = (new / name).read_bytes()
            self.assertEqual(after.split(b"\n", 1)[0], heading.encode())
            self.assertEqual(after.split(b"\n", 1)[1], before.split(b"\n", 1)[1])
        self.assertEqual(self.tables_at(new), tables)
        self.run_at(new, "set", "--reporting", "digest")
        self.assertEqual(json.loads((new / "restaurant.json").read_text())["reporting"], "digest")

    def test_rename_refuses_a_lease_held_under_the_old_prefix(self):
        self.started()
        store = self.store_files()
        error = self.rename(code=1)
        self.assertIn("L1", error)
        self.assertEqual(self.store_files(), store)
        self.assertTrue(self.at.exists())

    def test_rename_refuses_a_queue_entry_held_under_the_old_prefix(self):
        """The CLI exits 1 on a queue entry its status read prints and the store keeps its bytes."""
        self.open()
        store = self.store_files()
        readings = {("lease", "list"): "no leases held",
                    ("status", "--holder", "perf/"): "E3 queued (perf/D1, ab12cd34ef56)"}

        def land_result(project_root, *args):
            return subprocess.CompletedProcess([], 0, stdout=readings[args], stderr="")

        with mock.patch.dict(run_globals(self, SCRIPT), {"land_result": land_result}):
            outcome = call_in_process(self, SCRIPT, [
                "--store", str(self.store), "--at", str(self.at), "open",
                "--project-root", str(self.project), "--name", "Perf", "--rename", "Speed"])
        self.assertEqual(outcome.code, 1, outcome.stderr_text())
        self.assertIn("E3", outcome.stderr_text())
        self.assertEqual(self.store_files(), store)
        self.assertFalse((self.at.parent / "speed").exists())

    def test_rename_refuses_a_dish_that_is_not_merged_or_dropped(self):
        self.open()
        path = self.at / "dishes.tsv"
        header = path.read_text()
        for state in ("in-progress", "in-review", "queued", "passed", "sent-back", "blocked"):
            with self.subTest(state=state):
                row = "\t".join(["D1", datetime.now(timezone.utc).isoformat(), state] + [""] * 14)
                path.write_text(f"{header}{row}\n")
                store = self.store_files()
                error = self.rename(code=1)
                self.assertIn(f"D1 is {state}", error)
                self.assertEqual(self.store_files(), store)
                self.assertFalse((self.at.parent / "speed").exists())

    def test_rename_refuses_an_existing_target_and_keeps_its_file(self):
        self.open()
        target = self.at.parent / "speed"
        target.mkdir()
        (target / "keep.txt").write_text("keep\n")
        store = self.store_files()
        error = self.rename(code=1)
        self.assertIn(str(target), error)
        self.assertEqual((target / "keep.txt").read_text(), "keep\n")
        self.assertEqual(self.store_files(), store)

    def test_rename_refuses_a_stale_owner_although_open_needs_no_store(self):
        self.open()
        self.brigade("set", "--thread", "coordinator")
        store = self.store_files()
        error = self.run_at(self.at, "--owner", "other@1", "open", "--project-root", str(self.project),
                            "--name", "Perf", "--rename", "Speed", owner=False, code=1)
        self.assertIn("coordinator@1", error)
        self.assertEqual(self.store_files(), store)

    def test_rename_refuses_workers_which_it_would_ignore(self):
        self.open()
        store = self.store_files()
        error = self.rename("Speed", "--workers", "2", code=1)
        self.assertIn("--workers", error)
        self.assertEqual(self.store_files(), store)

    def test_a_concurrent_open_waits_for_a_recovering_rename_and_finds_the_moved_store(self):
        """D554 review 3 blocker 1: the project lock keeps a recovering rename's target clear of a concurrent open, which then finds the moved store."""
        self.open()
        self.brigade("set", "--thread", "coordinator")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
        meta = json.loads((self.at / "restaurant.json").read_text())
        rail = (self.at / "rail.tsv").read_bytes()
        self.case = Path(self.temporary.name) / "case"
        self.case.mkdir()
        killed = rerun = opener = None
        try:
            killed = self.rename_child("claim", "claimed")
            _wait_for_path(self.case / "claimed")
            killed.kill()
            killed.communicate(timeout=15)
            marker = self.at.parent / ".rename-speed.json"
            self.assertTrue(marker.is_file())
            self.assertFalse((self.at.parent / "speed").exists())
            rerun = self.rename_child("claim", "recovered")
            _wait_for_path(self.case / "recovered")
            opener = self.open_child()
            _wait_for_path(self.case / "started")
            # Review 3's ordering: the opener starts before the rerun's own mkdir. The project lock holds the
            # opener at its claim until the rerun creates the target and moves the store onto it.
            deadline = time.monotonic() + 3
            while not (self.at.parent / "speed").exists() and time.monotonic() < deadline:
                time.sleep(0.005)
            (self.case / "recovered-go").touch()
            rerun.communicate(timeout=15)
            if rerun.returncode != 0:
                self.rename()
            (self.case / "open-proceed").touch()
            open_out, open_err = opener.communicate(timeout=15)
        finally:
            for name in ("claimed-go", "recovered-go", "move-proceed", "open-proceed"):
                (self.case / name).touch()
            for process in (killed, rerun, opener):
                if process is not None and process.poll() is None:
                    process.kill()
                    process.communicate()
        speed = self.at.parent / "speed"
        self.assertEqual((speed / "rail.tsv").read_bytes(), rail)
        self.assertIn(b"T1\t", rail)
        moved = json.loads((speed / "restaurant.json").read_text())
        self.assertEqual(moved["restaurant"], "Speed")
        self.assertEqual(moved["thread"], meta["thread"])
        self.assertEqual(moved["generation"], meta["generation"])
        self.assertFalse((self.at.parent / ".rename-speed.json").exists())
        self.assertEqual(opener.returncode, 0, open_out + open_err)
        self.assertIn("exists", open_out)
        self.assertNotIn("opened", open_out + open_err)

    def test_a_rename_that_cannot_write_its_marker_keeps_an_existing_empty_target(self):
        """D554 review 3 blocker 1: a marker write that fails removes no directory the rename did not create."""
        self.open()
        target = self.at.parent / "speed"
        target.mkdir()
        store = self.store_files()
        real_write = Path.write_text

        def write_text(path, *args, **kwargs):
            if path.name.startswith(".rename-"):
                raise OSError("simulated marker write failure")
            return real_write(path, *args, **kwargs)

        with mock.patch.object(Path, "write_text", write_text):
            outcome = call_in_process(self, SCRIPT, [
                "--store", str(self.store), "--at", str(self.at), "open",
                "--project-root", str(self.project), "--name", "Perf", "--rename", "Speed"])
        self.assertEqual(outcome.code, 1, outcome.stderr_text())
        self.assertIn("simulated marker write failure", outcome.stderr_text())
        self.assertTrue(target.is_dir())
        self.assertEqual(list(target.iterdir()), [])
        self.assertEqual(self.store_files(), store)
        self.assertFalse((self.at.parent / ".rename-speed.json").exists())
