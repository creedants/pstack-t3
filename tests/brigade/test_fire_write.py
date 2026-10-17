from .support import (
    setUpModule,
    StoresTest,
)


class FireWriteTest(StoresTest):
    """A fire that is killed partway through its writes."""

    FIRE = ("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix bug")

    def before_kinds(self):
        """core holds T1, a waiting ticket as the tool wrote it before kinds and goals."""
        self.open("core")
        (self.dir("core") / "rail.tsv").write_text(
            "id\tat\tstate\tsource\tref\tdish\tsummary\tpriority\tpaths\tdecision\n"
            "T1\t2026-10-01T00:00:00+00:00\twaiting\tuser\t\t\tFix bug\t\t\t\n")

    def labelled(self, table):
        """The id, state, kind, and goal of each row of core's table. A row from before kinds has no kind and no goal."""
        header, *lines = (self.dir("core") / table).read_text().splitlines()
        rows = [dict(zip(header.split("\t"), line.split("\t"))) for line in lines]
        return [tuple(row.get(key, "") for key in ("id", "state", "kind", "goal")) for row in rows]

    def killed(self, mode, *fire):
        died = self.child(mode, "core", *fire)
        self.assertEqual((died.returncode, died.stdout, died.stderr), (9, "", ""))

    def test_a_fire_killed_before_its_log_row_leaves_the_item_and_its_lead_ticket_with_the_same_kind_and_status_warns_of_nothing(self):
        self.before_kinds()
        self.killed("die-before-log", *self.FIRE)
        self.assertEqual(self.labelled("dishes.tsv"), [("D1", "in-progress", "fix", "")])
        self.assertEqual(self.labelled("rail.tsv"), [("T1", "waiting", "fix", "")])
        self.assertEqual(self.rows("core", "log.tsv"), [])
        status = self.run_brigade("core", "status")
        self.assertEqual((status.returncode, status.stderr), (0, ""))
        self.assertEqual(status.stdout.splitlines()[3:], ["liveness: every 600000 ms (default for milestones)", "kinds: fix 1 waiting, 1 in flight"])

    def test_a_fire_killed_before_its_log_row_is_dropped_and_the_same_fire_again_starts_the_ticket_with_the_same_kind(self):
        self.before_kinds()
        self.killed("die-before-log", *self.FIRE)
        self.assertEqual(self.brigade("core", "dish", "D1", "--state", "dropped"), "D1 dropped")
        self.assertEqual(self.labelled("rail.tsv"), [("T1", "waiting", "fix", "")])
        self.assertEqual(self.brigade("core", *self.FIRE), "D2")
        self.assertEqual(self.labelled("dishes.tsv"), [("D1", "dropped", "fix", ""), ("D2", "in-progress", "fix", "")])
        self.assertEqual(self.labelled("rail.tsv"), [("T1", "assigned", "fix", "")])

    def test_a_fire_killed_before_its_item_row_leaves_the_lead_ticket_waiting_with_the_kind_and_no_item_and_the_same_fire_again_starts_it(self):
        self.before_kinds()
        self.killed("die-before-item", *self.FIRE)
        self.assertEqual(self.labelled("dishes.tsv"), [])
        self.assertEqual(self.labelled("rail.tsv"), [("T1", "waiting", "fix", "")])
        self.assertEqual(self.rows("core", "log.tsv"), [])
        self.assertEqual(self.brigade("core", *self.FIRE), "D1")
        self.assertEqual(self.labelled("dishes.tsv"), [("D1", "in-progress", "fix", "")])
        self.assertEqual(self.labelled("rail.tsv"), [("T1", "assigned", "fix", "")])

    def test_a_fire_with_kind_killed_before_its_item_row_leaves_a_lead_ticket_whose_kind_ticket_set_changes(self):
        self.before_kinds()
        self.killed("die-before-item", *self.FIRE, "--kind", "polish")
        self.assertEqual((self.labelled("dishes.tsv"), self.labelled("rail.tsv")), ([], [("T1", "waiting", "polish", "")]))
        changed = self.run_brigade("core", "ticket", "set", "T1", "--kind", "fix")
        self.assertEqual((changed.returncode, changed.stdout, changed.stderr), (0, "T1 kind fix\n", ""))
        self.assertEqual(self.labelled("rail.tsv"), [("T1", "waiting", "fix", "")])
