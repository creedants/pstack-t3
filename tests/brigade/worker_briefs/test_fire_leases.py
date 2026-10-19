from ..support import (
    Path,
    ROOT,
    os,
    subprocess,
    sys,
)


class FireLeasesMixin:
    def test_fire_claims_the_lease_and_a_refused_claim_fires_nothing(self):
        import os
        from unittest import mock
        run = lambda *a: subprocess.run(a, cwd=self.project, capture_output=True, text=True, check=True)
        run("git", "init", "-q", "-b", "main")
        (self.project / "a.txt").write_text("a\n")
        run("git", "add", "-A")
        run("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
        land = ROOT / "t3/added/landing/scripts/land.py"
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(Path(self.temporary.name) / "state")}):
            subprocess.run([sys.executable, str(land), "--repo", str(self.project), "init", "--trunk", "lane", "--mode", "local", "--base", "main"],
                           check=True, capture_output=True)
            self.open()
            self.brigade("set", "--change-note-dir", "changes")
            (self.at / "menu.md").write_text("## Purpose\n\nFast.\n")
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "two")
            self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s", "--paths", "src"),
                             "D1 (lease L1 held by perf/D1)")
            self.assertIn("nothing fired: paths overlap L1 held by perf/D1",
                          self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "s", "--paths", "src/x.py", ok=False))
            self.assertEqual(self.brigade("ticket", "list", "--state", "waiting"), "T2 waiting normal fix [user] two blocked: lease")
            self.brigade("set", "--thread", "thread-coord")
            text = self.brigade("brief", "D1", "--contract", "none", "--goal", "g", "--acceptance", "a", "--verify", "v", "--base", "refs/landing/lane")
            self.assertIn("leased to you as L1: src,changes/perf%2Fd1.md.", text)
            self.assertIn("branch `perf/d1`", text)

    def test_fire_with_change_note_dir_leases_the_encoded_fragment_and_dish_records_a_new_lease(self):
        from unittest import mock
        self.init_landing()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.open()
            self.brigade("set", "--change-note-dir", "changes")
            (self.at / "menu.md").write_text("## Purpose\n\nFast.\n")
            self.brigade("set", "--thread", "thread-coord")
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "two")
            self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s",
                         "--branch", "docs/a", "--paths", "src/a.py")
            text = self.brigade("brief", "D1", "--contract", "none", "--goal", "g", "--acceptance", "a", "--verify", "v", "--base", "origin/main")
            self.assertIn("leased to you as L1: src/a.py,changes/docs%2Fa.md.", text)
            self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "s",
                         "--branch", "docs/a%b", "--paths", "src/b.py")
            text = self.brigade("brief", "D2", "--contract", "none", "--goal", "g", "--acceptance", "a", "--verify", "v", "--base", "origin/main")
            self.assertIn("leased to you as L2: src/b.py,changes/docs%2Fa%25b.md.", text)
            self.assertEqual(text.count("changes/docs%2Fa%25b.md"), 1)
            self.brigade("dish", "D1", "--lease", "L9", "--paths", "src/a.py,changes/docs%2Fa.md")
            text = self.brigade("brief", "D1", "--contract", "none", "--goal", "g", "--acceptance", "a", "--verify", "v", "--base", "origin/main")
            self.assertIn("leased to you as L9: src/a.py,changes/docs%2Fa.md.", text)

    def test_fire_leases_no_fragment_when_change_note_dir_is_unset(self):
        from unittest import mock
        self.init_landing()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.open()
            (self.at / "menu.md").write_text("## Purpose\n\nFast.\n")
            self.brigade("set", "--thread", "thread-coord")
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
            self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s",
                                          "--branch", "docs/a", "--paths", "src/a.py"),
                             "D1 (lease L1 held by perf/D1)")
            self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["paths"], "src/a.py")
            text = self.brigade("brief", "D1", "--contract", "none", "--goal", "g", "--acceptance", "a", "--verify", "v", "--base", "origin/main")
            self.assertIn("leased to you as L1: src/a.py.", text)
            self.assertNotIn("changes/", text)

