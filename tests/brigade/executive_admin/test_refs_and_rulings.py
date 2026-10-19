class RefsRulingsMixin:
    def test_a_ref_moved_through_the_admin_frees_at_every_step_once_done(self):
        ref = "https://github.com/o/r/issues/7"
        self.open("core", "--intake", "github")
        for name in ("docs", "engine"):
            self.open(name)
        self.open_admin()

        def refused(ident):
            self.assertEqual(self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "again", "--source", "github", "--ref", ref,
                                          ok=False), f"brigade: {ref} is already {ident} (moved); nothing added")

        self.assertEqual(self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "Fix", "--source", "github", "--ref", ref), "T1")
        self.brigade("core", "ticket", "move", "T1", "--to", ".admin")
        refused("T1")
        self.assertEqual(self.admin("inbox", "take"), "T1 from app/core/T1: Fix")
        refused("T1")
        self.assertEqual(self.admin("ticket", "move", "T1", "--to", "docs"), "T1 moved to docs; no thread recorded for docs")
        refused("T1")
        self.assertEqual(self.brigade("docs", "inbox", "take"), "T1 from app/.admin/T1: Fix")
        refused("T1")
        self.brigade("docs", "ticket", "set", "T1", "--state", "done")
        self.assertEqual(self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "Fix", "--source", "github", "--ref", ref), "T2")
        self.brigade("core", "ticket", "move", "T2", "--to", ".admin")
        self.assertEqual(self.admin("inbox", "take"), "T2 from app/core/T2: Fix")
        self.admin("ticket", "move", "T2", "--to", "docs")
        self.assertEqual(self.brigade("docs", "inbox", "take"), "T2 from app/.admin/T2: Fix")
        self.brigade("docs", "ticket", "move", "T2", "--to", ".admin")
        refused("T2")
        self.assertEqual(self.admin("inbox", "take"), "T3 from app/docs/T2: Fix")
        refused("T2")
        self.admin("ticket", "move", "T3", "--to", "engine")
        refused("T2")
        self.assertEqual(self.brigade("engine", "ticket", "take"), "T1 from app/.admin/T3: Fix")
        refused("T2")
        self.brigade("engine", "ticket", "set", "T1", "--state", "done")
        self.assertEqual(self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "Fix", "--source", "github", "--ref", ref), "T3")

    def test_a_tracker_ref_the_admin_files_is_refused_again_and_reaches_the_coordinator_unchanged(self):
        ref = "app-7f3"
        self.open("engine")
        self.open_admin("--intake", "beads")

        def refused(store, ident, where=""):
            self.assertEqual(self.brigade(store, "ticket", "add", "--kind", "fix", "--summary", "again", "--source", "user", "--ref", ref,
                                          ok=False), f"brigade: {ref} is already {ident}{where}; nothing added")

        self.assertEqual(self.admin("ticket", "add", "--kind", "fix", "--summary", "Fix login", "--source", "beads", "--ref", ref), "T1")
        self.assertEqual(self.brigade("engine", "ticket", "add", "--kind", "fix", "--summary", "Fix login", "--source", "beads", "--ref", ref,
                                      ok=False), "brigade: .admin owns intake from beads; ask it to file this and move it here")
        refused(".admin", "T1", " (waiting)")
        refused("engine", "T1", " in .admin (waiting)")
        self.assertEqual(self.admin("ticket", "move", "T1", "--to", "engine"), "T1 moved to engine; no thread recorded for engine")
        refused(".admin", "T1", " (moved)")
        self.assertEqual(self.brigade("engine", "inbox", "take"), "T1 from app/.admin/T1: Fix login")
        self.assertEqual(self.brigade("engine", "ticket", "list"), "T1 waiting normal fix [beads (from app/.admin/T1)] Fix login app-7f3")
        refused("engine", "T1", " (waiting)")
        refused(".admin", "T1", " (moved)")

    def test_rule_overrule_leaves_two_rows_and_close_lists_both(self):
        self.open("docs")
        self.open_admin()
        self.assertEqual(self.brigade("docs", "rule", "list", ok=False), "brigade: rule works only in the executive admin's store")
        self.assertEqual(self.admin("rule", "add", "--kind", "contested-paths", "--parties", "docs,engine",
                                    "--question", "Who claims README.md next?", "--rule", "age",
                                    "--decision", "docs claims README.md next"), "R1")
        self.assertEqual(self.admin("rule", "overrule", "R1", "--decision", "engine goes first"), "R2")
        rows = self.rows(".admin", "rulings.tsv")
        self.assertEqual([(row[0], row[2], row[3], row[5], row[6], row[7], row[8]) for row in rows], [
            ("R1", "contested-paths", "docs, engine", "age", "docs claims README.md next", "", "overruled"),
            ("R2", "contested-paths", "docs, engine", "user", "engine goes first", "R1", "in-force"),
        ])
        self.assertEqual(self.admin("rule", "list"), "\n".join([
            "R1 overruled contested-paths (docs, engine): Who claims README.md next? Decided by age: docs claims README.md next.",
            "R2 in-force contested-paths (docs, engine): Who claims README.md next? Decided by user: engine goes first. Supersedes R1.",
        ]))
        self.assertEqual(self.admin("rule", "list", "--state", "in-force").splitlines()[0][:12], "R2 in-force ")
        report = self.admin("close")
        self.assertIn("\n".join([
            "## Rulings", "",
            "- R1 overruled contested-paths (docs, engine): Who claims README.md next? Decided by age: docs claims README.md next.",
            "- R2 in-force contested-paths (docs, engine): Who claims README.md next? Decided by user: engine goes first. Supersedes R1.",
        ]), report)
        self.assertNotIn("## Rulings", self.admin("close"))
        self.assertEqual(self.admin("rule", "add", "--kind", "ownership", "--parties", "docs,engine", "--question", "Who owns T4?",
                                    "--rule", "priority", "--decision", "docs", "--supersedes", "R2"), "R3")
        self.assertEqual(self.admin("rule", "set", "R1", "--state", "done", ok=False),
                         "brigade: R1 is overruled; only an in-force ruling ends")
        self.assertEqual(self.admin("rule", "set", "R3", "--state", "expired"), "R3 expired")
        self.assertEqual(self.admin("rule", "set", "R3", "--state", "expired"), "R3 expired")
        self.assertEqual([row[8] for row in self.rows(".admin", "rulings.tsv")], ["overruled", "superseded", "expired"])
        self.assertEqual(self.admin("rule", "list", "--state", "in-force"), "no rulings")
        self.assert_refused_choice(self.admin("rule", "add", "--kind", "ownership", "--parties", "docs",
                                              "--question", "q", "--rule", "guess", "--decision", "d", ok=False),
                                   "--rule", "guess", ("purpose", "priority", "age", "related-work", "dependency", "floor", "user"))

    def test_a_ruling_killed_before_its_log_row_is_named_by_the_next_close(self):
        self.open_admin()
        died = self.child("die-before-log", ".admin", "rule", "add", "--kind", "ownership", "--parties", "docs",
                          "--question", "Who owns this?", "--rule", "priority", "--decision", "docs")
        self.assertEqual(died.returncode, 9, died.stderr)
        self.assertEqual(self.admin("rule", "list"),
                         "R1 in-force ownership (docs): Who owns this? Decided by priority: docs.")
        section = self._ruling_section(
            "- R1 in-force ownership (docs): Who owns this? Decided by priority: docs.")
        self.assertIn(section, self.admin("close", "--dry-run"))
        self.assertEqual(self.ruling_events(), [])
        self.assertIn(section, self.admin("close"))
        self.assertEqual(self.ruling_events(), [("R1", "in-force", "docs")])
        self.assertNotIn("## Rulings", self.admin("close"))

    def test_an_overrule_killed_before_its_log_rows_is_named_by_the_next_close(self):
        self.open_admin()
        self.admin("rule", "add", "--kind", "contested-paths", "--parties", "docs,engine",
                   "--question", "Who claims README.md next?", "--rule", "age",
                   "--decision", "docs claims README.md next")
        died = self.child("die-before-log", ".admin", "rule", "overrule", "R1", "--decision", "engine goes first")
        self.assertEqual(died.returncode, 9, died.stderr)
        self.assertEqual(self.admin("rule", "list"), "\n".join([
            "R1 overruled contested-paths (docs, engine): Who claims README.md next? Decided by age: docs claims README.md next.",
            "R2 in-force contested-paths (docs, engine): Who claims README.md next? Decided by user: engine goes first. Supersedes R1.",
        ]))
        section = self._ruling_section(
            "- R1 overruled contested-paths (docs, engine): Who claims README.md next? Decided by age: docs claims README.md next.",
            "- R2 in-force contested-paths (docs, engine): Who claims README.md next? Decided by user: engine goes first. Supersedes R1.",
        )
        self.assertIn(section, self.admin("close", "--dry-run"))
        self.assertEqual(self.ruling_events(), [("R1", "in-force", "docs claims README.md next")])
        self.assertIn(section, self.admin("close"))
        self.assertEqual(self.ruling_events(), [
            ("R1", "in-force", "docs claims README.md next"),
            ("R2", "in-force", "engine goes first"),
            ("R1", "overruled", "replaced by R2"),
        ])
        self.assertNotIn("## Rulings", self.admin("close"))

    def test_an_ended_ruling_killed_before_its_log_row_is_named_by_the_next_close(self):
        self.open_admin()
        self.admin("rule", "add", "--kind", "ownership", "--parties", "docs",
                   "--question", "Who owns this?", "--rule", "priority", "--decision", "docs")
        died = self.child("die-before-log", ".admin", "rule", "set", "R1", "--state", "done")
        self.assertEqual(died.returncode, 9, died.stderr)
        self.assertEqual(self.admin("rule", "list"),
                         "R1 done ownership (docs): Who owns this? Decided by priority: docs.")
        section = self._ruling_section(
            "- R1 done ownership (docs): Who owns this? Decided by priority: docs.")
        self.assertIn(section, self.admin("close", "--dry-run"))
        self.assertEqual(self.ruling_events(), [("R1", "in-force", "docs")])
        self.assertIn(section, self.admin("close"))
        self.assertEqual(self.ruling_events(), [
            ("R1", "in-force", "docs"),
            ("R1", "done", "docs (done)"),
        ])
        self.assertNotIn("## Rulings", self.admin("close"))

    def test_the_next_rule_add_logs_a_killed_ruling_once(self):
        self.open_admin()
        died = self.child("die-before-log", ".admin", "rule", "add", "--kind", "ownership", "--parties", "docs",
                          "--question", "Who owns this?", "--rule", "priority", "--decision", "docs")
        self.assertEqual(died.returncode, 9, died.stderr)
        self.assertEqual(self.admin("rule", "add", "--kind", "ownership", "--parties", "engine",
                                    "--question", "Who owns T4?", "--rule", "priority", "--decision", "engine"), "R2")
        self.assertEqual(self.ruling_events(), [
            ("R1", "in-force", "docs"),
            ("R2", "in-force", "engine"),
        ])
