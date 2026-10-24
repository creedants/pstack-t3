if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from ..support import Path, ProcessReason, SCRIPT, real_popen, sys


class StoreOpenMixin:
    def test_status_in_the_executive_admins_store_prints_the_kinds_line_and_the_goals_line_after_its_counts_line(self):
        self.open_admin()
        self.admin("goal", "add", "--name", "one inbox", "--purpose", "p", "--done", "d")
        self.admin("ticket", "add", "--kind", "upkeep", "--summary", "Add a FAQ", "--goal", "G1")
        self.assertEqual(self.admin("status").splitlines(),
                         ["thread not recorded", "reporting: milestones, no landing contract, waiting tickets: 1",
                          "liveness: every 600000 ms (default for milestones)",
                          "kinds: upkeep 1 waiting", "goals: G1 1 waiting"])

    def test_goal_add_goal_set_and_goal_list_work_in_the_executive_admins_store(self):
        self.open_admin()
        self.assertEqual(self.admin("goal", "add", "--name", "routing", "--purpose", "Requests reach an owner", "--done", "Standing",
                                    "--share", "1"), "G1")
        self.assertEqual(self.admin("goal", "set", "G1", "--state", "paused"), "G1 paused")
        self.assertEqual(self.admin("goal", "list"), "\n".join([
            "G1 paused routing: 0 waiting, 0 in flight, 0 merged, 0 send-backs, share 1",
            "  purpose: Requests reach an owner",
            "  done when: Standing",
            "unassigned: 0 waiting, 0 in flight, 0 merged, 0 send-backs"]))
        self.assertEqual([row[1:] for row in self.rows(".admin", "log.tsv") if row[1] == "goal"],
                         [["goal", "G1", "open", "routing"], ["goal", "G1", "paused", "routing"]])

    def test_the_executive_admins_ticket_add_needs_a_kind_and_takes_a_goal_and_its_moved_ticket_carries_the_kind(self):
        self.open_admin()
        self.open("core")
        self.admin("goal", "add", "--name", "routing", "--purpose", "p", "--done", "d")
        self.assertEqual(self.admin("ticket", "add", "--summary", "Fix the cache", ok=False),
                         "brigade: ticket add needs --kind <feature|fix|polish|upkeep|study>")
        self.assertEqual(self.admin("ticket", "add", "--summary", "Fix the cache", "--kind", "fix", "--goal", "G1"), "T1")
        self.assertEqual(self.rows(".admin", "rail.tsv")[0][10:], ["fix", "G1"])
        self.assertEqual(self.admin("ticket", "set", "T1", "--no-goal"), "T1 unassigned")
        self.assertEqual(self.admin("ticket", "set", "T1", "--goal", "G1"), "T1 goal G1")
        self.assertEqual(self.admin("ticket", "move", "T1", "--to", "core"),
                         "T1 moved to core; no thread recorded for core; it leaves G1 here and arrives unassigned")
        self.brigade("core", "ticket", "take")
        self.assertEqual(self.rows("core", "rail.tsv")[0][10:], ["fix", ""])

    def test_two_admin_opens_at_once_leave_one_store(self):
        argv = [sys.executable, str(SCRIPT), "--store", str(self.store), "open", "--admin",
                "--project-root", str(self.project)]
        procs = [real_popen(ProcessReason.RACE, argv, text=True) for _ in range(2)]
        words = sorted(proc.communicate(timeout=30)[0].split()[0] for proc in procs)
        self.assertEqual(words, ["exists", "opened"])
        self.assertEqual(sorted(path.name for path in (self.store / "app").iterdir()), [".admin"])
        meta = self.meta(".admin")
        self.assertEqual((meta["role"], meta["projectRoot"]), ("admin", str(self.project)))
        self.assertTrue((self.dir(".admin") / "rulings.tsv").is_file())
        self.assertIn("## Priorities", (self.dir(".admin") / "menu.md").read_text())

    def test_open_admin_refuses_a_second_root_with_the_same_directory_name(self):
        other = Path(self.temporary.name) / "other" / "app"
        other.mkdir(parents=True)
        self.open_admin()
        before = (self.dir(".admin") / "restaurant.json").read_bytes()
        self.assertEqual(self.brigade(".admin", "open", "--admin", "--project-root", str(other), ok=False),
                         f"brigade: {self.dir('.admin')} already holds a coordinator for {self.project}; pick another --name")
        self.assertEqual((self.dir(".admin") / "restaurant.json").read_bytes(), before)
        self.assertEqual(self.open_admin("--name", "boss", ok=False), "brigade: open --admin takes no --name")
        self.assertEqual(self.brigade("docs", "open", "--project-root", str(self.project), ok=False),
                         "brigade: open needs --name, or --admin")

    def test_open_admin_prints_every_coordinator_and_walk_puts_it_first(self):
        self.open("docs")
        (self.dir("docs") / "menu.md").write_text("## Purpose\n\nKeep the docs right.\n\n## Off the menu\n\nEngine work\n")
        self.assertEqual(self.open_admin("--reporting", "digest"), "\n".join([
            f"opened {self.dir('.admin')}",
            f"sibling docs ({self.dir('docs')}), thread not recorded",
            "  purpose: Keep the docs right.",
            "  off the menu: Engine work",
        ]))
        self.open("engine")
        self.assertIn(f"sibling executive admin ({self.dir('.admin')}), thread not recorded",
                      self.brigade("engine", "open", "--project-root", str(self.project), "--name", "engine"))
        walked = self.brigade("docs", "walk", "--repo", str(self.project)).splitlines()
        self.assertEqual(walked[1:], [
            "  executive admin (reports digest): nothing on record",
            "    thread not recorded",
            "  docs (reports milestones): nothing on record",
            "    thread not recorded",
            "  engine (reports milestones): nothing on record",
            "    thread not recorded",
        ])

    def test_the_admin_store_refuses_work_commands(self):
        self.open_admin()
        refusal = "brigade: the executive admin routes work and never runs it"
        for args in (("fire", "--tickets", "T1", "--station", "feature", "--summary", "s"),
                     ("brief", "D1", "--contract", "none", "--goal", "g", "--verify", "v", "--base", "main", "--acceptance", "a"),
                     ("contract", "D1"),
                     ("edits", "verify", "D1", "--from", "abc", "--to", "def"),
                     ("dish", "D1", "--state", "merged"),
                     ("dish", "D1", "--state", "dropped", "--stopped", "r1"),
                     ("pass", "check", "D1", "--sha", "abc"),
                     ("watch",)):
            self.assertEqual(self.admin(*args, ok=False), refusal, args)
        self.assertEqual(self.admin("ticket", "add", "--kind", "fix", "--summary", "Add a FAQ"), "T1")
        self.assertEqual(self.admin("86", "add", "--question", "Which purpose?", "--options", "docs, engine",
                                    "--default", "docs"), "Q1")
        self.assertEqual(self.admin("status"),
                         "thread not recorded\nreporting: milestones, no landing contract, waiting tickets: 1, decisions for you: 1\n"
                         "liveness: every 600000 ms (default for milestones)\n"
                         "kinds: fix 1 waiting")

    def test_the_admin_store_refuses_hang_and_leaves_the_store_unchanged(self):
        self.open_admin()
        before = self.files(".admin")
        result = self.run_brigade(".admin", "hang", "D1", "--provider", "opencode-go/step-5-preview-free",
                                  "--minutes", "30")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr.strip(), "brigade: the executive admin routes work and never runs it")
        self.assertEqual(self.files(".admin"), before)

    def test_reports_to_is_recorded_cleared_and_shown_by_status(self):
        self.open("docs")
        self.brigade("docs", "set", "--thread", "th-docs")
        self.brigade("docs", "set", "--reports-to", "th-admin")
        self.assertEqual(self.meta("docs")["reportsTo"], "th-admin")
        self.assertEqual(self.brigade("docs", "status"),
                         "thread th-docs\nreporting: milestones, no landing contract\nworkers: 0 of 2 running, 2 idle, auto-start: normal\nliveness: every 600000 ms (default for milestones)\nreports to th-admin\nowner th-docs@1")
        self.brigade("docs", "set", "--reports-to", "")
        self.assertNotIn("reportsTo", self.meta("docs"))
        self.assertEqual(self.brigade("docs", "status"), "thread th-docs\nreporting: milestones, no landing contract\nworkers: 0 of 2 running, 2 idle, auto-start: normal\nliveness: every 600000 ms (default for milestones)\nowner th-docs@1")
