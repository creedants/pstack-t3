if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import os
import sys
import time
import unittest
from unittest import mock

from .._support.inprocess import ProcessReason, call_in_process, real_popen, real_process, run_globals
from .._support.names import SCRIPT, _owner_words
from ..support import StoresBase, setUpModule


class ProjectLockTest(StoresBase, unittest.TestCase):
    def argv(self, name, *args):
        return ["--store", str(self.store), "--at", str(self.dir(name)), *_owner_words(self.dir(name)),
                *(str(arg) for arg in args)]

    def start(self, name, *args):
        """A brigade process for name that runs now. The test reads its output after it exits."""
        proc = real_popen(ProcessReason.OTHER_LOCK, [sys.executable, str(SCRIPT), *self.argv(name, *args)])
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        return proc

    def settles(self, proc, seconds):
        """The exit status once proc exits, or None when it is still running after seconds."""
        deadline = time.monotonic() + seconds
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        return proc.poll()

    def rail_row(self, name, ident):
        lines = (self.dir(name) / "rail.tsv").read_text().splitlines()
        keys = lines[0].split("\t")
        return next(dict(zip(keys, line.split("\t"))) for line in lines[1:] if line.split("\t")[0] == ident)

    def two_each(self):
        self.open("core")
        self.open("engine")
        for name in ("core", "engine"):
            for summary in ("one", "two"):
                self.brigade(name, "ticket", "add", "--kind", "fix", "--summary", f"{name} {summary}")

    def test_ticket_set_after_in_one_store_waits_for_the_project_lock_the_other_store_holds_so_a_cycle_through_both_is_refused(self):
        self.two_each()
        engine_before = self.files("engine")
        case, core = self.paused("core-set", "core", *_owner_words(self.dir("core")),
                                 "ticket", "set", "T1", "--after", "engine/T1")
        engine = self.start("engine", "ticket", "set", "T1", "--after", "core/T1")
        self.assertIsNone(self.settles(engine, 3), "engine finished while core held the project lock")
        self.assertEqual(self.resume(case, core)[0], 0)
        self.assertEqual(self.rail_row("core", "T1")["after"], "engine/T1")
        _, err = engine.communicate(timeout=30)
        self.assertEqual(engine.returncode, 1, err)
        self.assertEqual(err.strip(), "brigade: T1 would wait on itself through core/T1; nothing changed")
        self.assertEqual(self.files("engine"), engine_before)

    def test_fire_waits_for_the_project_lock_so_a_dependency_set_back_to_waiting_in_between_is_not_fired_past(self):
        self.open("core")
        self.open("engine")
        self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "core one")
        self.brigade("core", "ticket", "set", "T1", "--state", "done")
        self.brigade("engine", "ticket", "add", "--kind", "feature", "--summary", "engine one")
        self.brigade("engine", "ticket", "add", "--kind", "feature", "--summary", "engine two", "--after", "core/T1")
        restaurant = run_globals(self, SCRIPT)["Restaurant"]
        original_append = restaurant.append
        seen = {}
        test = self

        def append_while_core_sets_waiting(restaurant, table, row):
            if table == "dishes.tsv" and "core" not in seen:
                seen["core"] = core = test.start("core", "ticket", "set", "T1", "--state", "waiting")
                seen["running"] = test.settles(core, 1) is None
            return original_append(restaurant, table, row)

        patcher = mock.patch.object(restaurant, "append", append_while_core_sets_waiting)
        patcher.start()
        self.addCleanup(patcher.stop)
        outcome = call_in_process(self, SCRIPT, self.argv("engine", "fire", "--tickets", "T2", "--station", "feature",
                                                          "--summary", "x", "--timebox", "60"))
        self.assertEqual(outcome.code, 0, outcome.stderr_text())
        self.assertIn("core", seen, "fire never reached its dish write")
        self.assertTrue(seen["running"], "core's ticket set finished while fire held the project lock")
        self.assertEqual(self.rail_row("engine", "T2")["state"], "assigned")
        _, err = seen["core"].communicate(timeout=30)
        self.assertEqual(seen["core"].returncode, 0, err)
        self.assertEqual(self.rail_row("core", "T1")["state"], "waiting")

    def test_ticket_set_state_takes_the_project_lock(self):
        self.open("core")
        self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "core one")
        lock = run_globals(self, SCRIPT)["project_lock"]
        with lock(self.store / "app"):
            state = self.start("core", "ticket", "set", "T1", "--state", "done")
            self.assertIsNone(self.settles(state, 1), "ticket set --state finished while the project lock was held")
            added = real_process(ProcessReason.OTHER_LOCK, SCRIPT,
                                 self.argv("core", "ticket", "add", "--kind", "fix", "--summary", "core two"), timeout=30)
            watched = real_process(ProcessReason.OTHER_LOCK, SCRIPT, self.argv("core", "watch"), timeout=30)
        self.assertEqual(added.returncode, 0, added.stderr)
        self.assertEqual(watched.returncode, 0, watched.stderr)
        _, err = state.communicate(timeout=30)
        self.assertEqual(state.returncode, 0, err)
        self.assertEqual(self.rail_row("core", "T1")["state"], "done")

    def listing(self):
        """Every path under the store, restaurant.lock included, so a lock file a sibling creates shows up."""
        return {str(path.relative_to(self.store)) for path in self.store.rglob("*")}

    def test_a_locked_ticket_set_after_creates_no_file_in_any_sibling_store(self):
        self.two_each()
        before = self.listing()
        lock = run_globals(self, SCRIPT)["project_lock"]
        with lock(self.store / "app"):
            waiting = self.start("engine", "ticket", "set", "T1", "--after", "core/T1")
            self.assertIsNone(self.settles(waiting, 1), "ticket set --after finished while the project lock was held")
            self.assertEqual(self.listing(), before)
        _, err = waiting.communicate(timeout=30)
        self.assertEqual(waiting.returncode, 0, err)
        self.assertEqual(self.rail_row("engine", "T1")["after"], "core/T1")


if __name__ == "__main__":
    unittest.main()
