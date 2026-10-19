from ..support import CLAUDE, Path, ROOT, mock, os, shlex, subprocess, sys

class RetireMixin:
    def test_a_branch_rename_after_submit_says_the_lease_cannot_change(self):
        self.init_landing("--cap", "3")
        from unittest import mock
        with mock.patch.dict(os.environ, self.land_env()):
            self.open()
            self.brigade("set", "--change-note-dir", "changes")
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
            self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "one",
                         "--branch", "perf/d1", "--paths", "a.txt")
            sha = self.worker_commit("perf/d1", {"a.txt": "changed\n"})
            self.passed(sha)
            self.submit(sha)
            self.brigade("dish", "D1", "--state", "queued")
            self.assertEqual(self.brigade("dish", "D1", "--branch", "perf/d1-r2"), "\n".join([
                "D1 queued",
                "D1: L1 is submitted, so it cannot take changes/perf%2Fd1-r2.md; the submitted commit lands as it is. "
                "Run dish D1 --branch perf/d1-r2 again if its entry bounces",
            ]))
            self.assertEqual(self.lease_lines(), ["L1 submitted perf/D1: a.txt, changes/perf%2Fd1.md"])

    def test_a_branch_rename_by_a_replaced_owner_claims_nothing(self):
        from unittest import mock
        self.init_landing("--cap", "3")
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            taken = []
            result = self.renamed(lambda: taken or taken.append(
                self.brigade("set", "--thread", "replacement", "--replace")))
            self.assertEqual(len(taken), 1)
            self.assertIn("$B dish D1 --add-paths", result)
            self.assertEqual(self.lease_lines(), ["L1 active perf/D1: changes/perf%2Fd1.md, src"])
            stale = self.run_at(self.at, "--owner", "coordinator@1", "dish", "D1", "--branch", "perf/d1-r3", ok=False)
            self.assertEqual(stale, "brigade: owner coordinator@1 is stale; this store is owned by replacement@2")
            self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["branch"], "perf/d1-r2")
            self.assertEqual(self.brigade("dish", "D1", "--state", "dropped"), "D1 dropped; T1 waiting again")
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_a_drop_waits_for_the_worker_and_then_releases_the_lease(self):
        self.started()
        self.brigade("dish", "D1", "--thread", "w1")
        self.assertEqual(self.brigade("dish", "D1", "--state", "dropped", ok=False),
                         "brigade: D1 holds L1 and its worker may still be running; "
                         "wait for its run with t3_thread_wait, then pass --stopped <run id>")
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["state"], "in-progress")
        self.assertEqual(self.land("lease", "list").split(" until ")[0], "L1 active perf/D1")
        self.assertIn("L1 held by perf/D1",
                      self.land("lease", "claim", "--holder", "engine/D1", "--paths", "README.md", ok=False))
        self.worker_commit("perf/d1", {"README.md": "late\n"})
        self.assertEqual(self.brigade("dish", "D1", "--state", "dropped", "--stopped", "r1"), "D1 dropped; T1 waiting again")
        dropped = [line for line in (self.at / "log.tsv").read_text().splitlines() if "\tD1\tdropped\t" in line]
        self.assertEqual(len(dropped), 1)
        self.assertIn("stopped r1", dropped[0])
        self.assertEqual(self.land("lease", "claim", "--holder", "engine/D1", "--paths", "README.md"), "L2")

    def test_a_late_worker_submit_after_the_drop_is_refused(self):
        self.started()
        self.brigade("dish", "D1", "--thread", "w1")
        self.worker_commit("perf/d1", {"README.md": "first\n"})
        case = Path(self.temporary.name)
        worktree = case / "perf-d1"
        script = (f"while [ ! -e {shlex.quote(str(case / 'go'))} ]; do sleep 0.01; done; "
                  f"echo late > README.md && git add -A && git -c user.name=t -c user.email=t@t commit -qm late && "
                  f"{shlex.quote(sys.executable)} {shlex.quote(str(ROOT / 't3/added/landing/scripts/land.py'))} "
                  f"--repo {shlex.quote(str(self.project))} submit --holder perf/D1 --branch perf/d1 "
                  f"--sha $(git rev-parse HEAD) --lease L1 --reviewer {CLAUDE}")
        worker = subprocess.Popen(["bash", "-c", script], cwd=worktree, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True)
        try:
            self.assertEqual(self.brigade("dish", "D1", "--state", "dropped", "--stopped", "r1"), "D1 dropped; T1 waiting again")
            engine = self.store / "bridge-kit" / "engine"
            self.run_at(engine, "open", "--project-root", str(self.project), "--name", "Engine")
            (engine / "menu.md").write_text("## Purpose\n\nEngine.\n")
            self.run_at(engine, "ticket", "add", "--kind", "fix", "--summary", "engine work")
            self.assertEqual(self.run_at(engine, "fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "e",
                                         "--paths", "README.md"), "D1 (lease L2 held by engine/D1)")
            (case / "go").touch()
            _, err = worker.communicate(timeout=30)
        finally:
            worker.kill()
        self.assertEqual(worker.returncode, 1)
        self.assertEqual(err.strip(), "land: L1 is not an active lease held by perf/D1; claim one before submitting")
        self.assertEqual(self.land("status", "--holder", "perf/D1"), "no entries held by perf/D1")
        self.assertEqual([line.split(" until ")[0] for line in self.land("lease", "list").splitlines()],
                         ["L2 active engine/D1"])
        self.assertEqual(self.table_row(engine, "dishes.tsv", "D1")["lease"], "L2")

    def test_a_new_branch_prints_the_commands_that_lease_its_fragment(self):
        from unittest import mock
        self.init_landing("--cap", "3")
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.assertEqual(self.renamed(), "\n".join([
                "D1 in-progress",
                "D1: L1 does not cover changes/perf%2Fd1-r2.md; cover it with this command:",
                "  $B dish D1 --add-paths changes/perf%2Fd1-r2.md",
            ]))
            row = self.table_row(self.at, "dishes.tsv", "D1")
            self.assertEqual((row["branch"], row["lease"], row["paths"]), ("perf/d1-r2", "L1", "src,changes/perf%2Fd1.md"))
            self.assertEqual(self.lease_lines(), ["L1 active perf/D1: changes/perf%2Fd1.md, src"])
            self.assertEqual(self.brigade("dish", "D1", "--add-paths", "changes/perf%2Fd1-r2.md"), "D1: L1 now covers changes/perf%2Fd1-r2.md")
            self.assertEqual(self.lease_lines(), ["L1 active perf/D1: changes/perf%2Fd1-r2.md, changes/perf%2Fd1.md, src"])
            self.assertEqual(self.brigade("dish", "D1", "--branch", "perf/d1-r2"), "D1 in-progress")

    def test_an_active_fragment_gap_prints_no_manual_claim_or_release(self):
        from unittest import mock
        self.init_landing("--cap", "3")
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            result = self.renamed()
            self.assertIn("$B dish D1 --add-paths changes/perf%2Fd1-r2.md", result)
            self.assertNotIn("$L lease claim", result)
            self.assertNotIn("$L lease release", result)
            self.assertNotIn("--lease <new lease>", result)
