from .support import (
    setUpModule,
    BrigadeTest,
    Path,
    ProcessReason,
    SCRIPT,
    StoresTest,
    _HandoffRace,
    _owner_words,
    _shown_root,
    call,
    call_in_process,
    json,
    os,
    real_process,
    run_globals,
    runpy,
    tempfile,
)


class HandoffTest(StoresTest, _HandoffRace):
    def test_ticket_add_takes_a_user_request_with_the_summary_of_a_moved_ticket(self):
        self.open("core")
        self.open("engine")
        self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "Fix the cache")
        self.brigade("core", "ticket", "move", "T1", "--to", "engine")
        self.assertEqual(self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "Fix the cache"), "T2")

    def test_from_report_files_the_same_report_name_in_two_siblings(self):
        body = "## Follow-ups\n\n- Shared text.\n"
        for name in ("core", "engine"):
            self.open(name)
            self.brigade(name, "ticket", "add", "--kind", "fix", "--summary", "s")
            self.brigade(name, "fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix s")
            (self.dir(name) / "reports").mkdir(exist_ok=True)
            (self.dir(name) / "reports" / "D1.md").write_text(body)
            self.assertEqual(self.brigade(name, "ticket", "add", "--from-report", "reports/D1.md"),
                             f"T2 added from {name}/reports/D1.md#1: Shared text.")
        self.assertEqual(self.rows("engine", "rail.tsv")[1][2:], ["waiting", "report", "engine/reports/D1.md#1", "", "Shared text.", "", "", "", "polish", ""])

    def test_a_sibling_cannot_claim_a_source_another_owns(self):
        self.open("docs", "--intake", "github")
        self.assertEqual(json.loads((self.dir("docs") / "restaurant.json").read_text())["intake"], ["github"])
        self.open("engine")
        self.assertEqual(self.brigade("engine", "set", "--intake", "github", ok=False),
                         "brigade: docs already owns intake from github; move tickets to it instead")
        self.assertEqual(self.brigade("engine", "open", "--project-root", str(self.project), "--name", "release",
                                      "--intake", "github", ok=False),
                         "brigade: docs already owns intake from github; move tickets to it instead")
        self.assertFalse(self.dir("release").exists())
        self.assertEqual(self.brigade("engine", "open", "--project-root", str(self.project), "--name", "engine",
                                      "--intake", "github"),
                         "\n".join([f"exists {self.dir('engine')}",
                                    "intake stays empty; change it with set --intake",
                                    f"sibling docs ({self.dir('docs')}), thread not recorded",
                                    "  purpose: not written yet",
                                    "  off the menu: not written yet"]))
        self.assertEqual(json.loads((self.dir("engine") / "restaurant.json").read_text())["intake"], [])

    def test_two_owners_of_one_source_both_refuse_to_file(self):
        self.open("core")
        self.open("docs")
        for name in ("core", "docs"):
            path = self.dir(name) / "restaurant.json"
            meta = json.loads(path.read_text())
            meta["intake"] = ["github"]
            path.write_text(json.dumps(meta))
        self.assertEqual(self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "x", "--source", "github", ok=False),
                         "brigade: docs owns intake from github; ask it to file this and move it here")
        self.assertEqual(self.brigade("docs", "ticket", "add", "--kind", "fix", "--summary", "x", "--source", "github", ok=False),
                         "brigade: core owns intake from github; ask it to file this and move it here")
        self.assertEqual(self.rows("core", "rail.tsv") + self.rows("docs", "rail.tsv"), [])
        self.assertEqual(self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "from the user"), "T1")

    def test_one_ref_is_filed_once_while_live(self):
        ref = "https://github.com/o/r/issues/7"
        self.open("core", "--intake", "github")
        self.assertEqual(self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "a", "--source", "github", "--ref", ref), "T1")
        self.assertEqual(self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "b", "--source", "github", "--ref", f"  {ref} ", ok=False),
                         f"brigade: {ref} is already T1 (waiting); nothing added")
        self.brigade("core", "fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "fix")
        self.assertEqual(self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "b", "--ref", ref, ok=False),
                         f"brigade: {ref} is already T1 (assigned); nothing added")
        self.brigade("core", "ticket", "set", "T1", "--state", "done")
        self.assertEqual(self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "reopened", "--source", "github", "--ref", ref), "T2")

    def test_dropping_a_source_with_a_waiting_ticket_is_refused(self):
        self.open("core", "--intake", "github,feed")
        self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "a", "--source", "github")
        self.assertEqual(self.brigade("core", "set", "--intake", "feed", ok=False),
                         "brigade: T1 from github is waiting; finish, drop, or move it before dropping github from intake")
        self.brigade("core", "ticket", "set", "T1", "--state", "dropped")
        self.brigade("core", "set", "--intake", "feed")
        self.assertEqual(json.loads((self.dir("core") / "restaurant.json").read_text())["intake"], ["feed"])

    def test_move_hands_a_ticket_to_a_sibling_and_take_files_it(self):
        self.open("core", "--intake", "github")
        self.open("engine")
        self.brigade("engine", "set", "--thread", "thread-engine")
        self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "Fix the cache", "--source", "github", "--ref", "R7")
        self.assertEqual(self.brigade("core", "ticket", "move", "T1", "--to", "engine"),
                         "T1 moved to engine; tell thread thread-engine")
        self.assertEqual(self.brigade("core", "ticket", "list"), "T1 moved normal fix [github] Fix the cache R7")
        self.assertEqual(self.brigade("core", "fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "x", ok=False),
                         "brigade: T1 is moved, not waiting")
        self.assertEqual(self.brigade("core", "ticket", "set", "T1", "--state", "waiting", ok=False),
                         "brigade: T1 moved to engine; it is that coordinator's ticket now")
        self.assertEqual(json.loads((self.dir("engine") / "inbox" / "app~core~T1.json").read_text()),
                         {"handoff": "app/core/T1", "summary": "Fix the cache", "source": "github", "ref": "R7",
                          "priority": "", "paths": "", "decision": "", "kind": "fix"})
        self.assertEqual(self.brigade("core", "watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nT1: moved to engine, waiting for ticket take")
        self.assertEqual(self.brigade("engine", "watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nhanded to you: 1; run ticket take")
        self.assertEqual(self.brigade("engine", "status"),
                         "thread thread-engine\nreporting: milestones, no landing contract, handed to you: 1\nworkers: 0 of 2 running, 2 idle, auto-start: normal\nowner thread-engine@1")
        self.assertEqual(self.brigade("engine", "ticket", "take"), "T1 from app/core/T1: Fix the cache")
        self.assertEqual(self.brigade("engine", "ticket", "list"), "T1 waiting normal fix [github (from app/core/T1)] Fix the cache R7")
        self.assertEqual(self.inbox("engine"), [])
        self.assertEqual(self.brigade("core", "watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nno work in progress")
        self.assertEqual(self.brigade("engine", "watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nno work in progress")
        self.assertIn("## Handed to another coordinator\n\n- T1: Fix the cache (to engine)",
                      self.brigade("core", "close", "--dry-run"))
        self.assertEqual(self.brigade("engine", "ticket", "take"), "nothing handed to you")
        (self.dir("engine") / "inbox" / "app~core~T1.json").write_text("{}")
        self.assertEqual(self.brigade("core", "watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nno work in progress")

    def test_a_moved_ref_stays_live_through_each_move_until_the_end_of_the_chain_finishes(self):
        ref = "R7"
        self.open("core", "--intake", "github")
        self.open("docs")
        self.open("engine")
        self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "s", "--source", "github", "--ref", ref)
        self.brigade("core", "ticket", "move", "T1", "--to", "docs")
        refused = f"brigade: {ref} is already T1 (moved); nothing added"
        self.assertEqual(self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "s", "--source", "github", "--ref", ref, ok=False), refused)
        self.brigade("docs", "ticket", "take")
        self.brigade("docs", "ticket", "move", "T1", "--to", "engine")
        self.assertEqual(self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "s", "--source", "github", "--ref", ref, ok=False), refused)
        self.brigade("engine", "ticket", "take")
        self.assertEqual(self.brigade("engine", "ticket", "list"), f"T1 waiting normal fix [github (from app/docs/T1)] s {ref}")
        self.assertEqual(self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "s", "--source", "github", "--ref", ref, ok=False), refused)
        self.brigade("engine", "ticket", "set", "T1", "--state", "done")
        self.assertEqual(self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "s", "--source", "github", "--ref", ref), "T2")

    def test_a_new_owner_refuses_a_ref_the_old_owner_still_holds_live(self):
        self.handoff(source="core", target="docs", ref="R7")
        self.open("engine")
        self.brigade("core", "set", "--intake", "")
        self.brigade("engine", "set", "--intake", "github")
        self.assertEqual(self.brigade("engine", "ticket", "add", "--kind", "fix", "--summary", "s", "--source", "github", "--ref", "R7", ok=False),
                         "brigade: R7 is already T1 in core (moved); nothing added")
        self.assertEqual(self.brigade("engine", "ticket", "add", "--kind", "fix", "--summary", "s", "--source", "github", "--ref", "R8"), "T1")

    def test_a_replaced_owners_paused_move_does_not_republish_the_handoff(self):
        self.open("core", "--intake", "github")
        self.open("engine")
        self.brigade("core", "set", "--thread", "t1")
        self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "Fix the cache", "--source", "github", "--ref", "R7")
        self.brigade("core", "ticket", "move", "T1", "--to", "engine")
        # The handoff was never delivered, so a rerun of the move would publish it again.
        (self.dir("engine") / "inbox" / "app~core~T1.json").unlink()
        stale = self.paused("stale", "core", "--owner", "t1@1", "ticket", "move", "T1", "--to", "engine")
        missing = self.paused("missing", "core", "ticket", "move", "T1", "--to", "engine")
        self.brigade("core", "--owner", "t1@1", "set", "--thread", "t2", "--replace")
        before = {"core": self.files("core"), "engine": self.files("engine")}
        self.assertEqual(self.resume(*stale), (1, "brigade: owner t1@1 is stale; this store is owned by t2@2"))
        self.assertEqual(self.resume(*missing),
                         (1, "brigade: this store is owned by t2@2; pass --owner <thread>@<generation> from status"))
        self.assertEqual({"core": self.files("core"), "engine": self.files("engine")}, before)
        self.assertEqual(self.inbox("engine"), [])
        self.brigade("core", "--owner", "t2@2", "ticket", "move", "T1", "--to", "engine")
        self.assertEqual(self.inbox("engine"), ["app~core~T1.json"])

    def test_a_replaced_owners_paused_take_does_not_delete_the_inbox_file(self):
        inbox = self.handoff()
        self.brigade("engine", "set", "--thread", "e1")
        handed = inbox.read_bytes()
        self.assertEqual(self.brigade("engine", "--owner", "e1@1", "ticket", "take"), "T1 from app/core/T1: Fix the cache")
        # A take that filed the ticket and died before deleting the file leaves exactly this.
        inbox.write_bytes(handed)
        stale = self.paused("stale", "engine", "--owner", "e1@1", "ticket", "take")
        missing = self.paused("missing", "engine", "ticket", "take")
        self.brigade("engine", "--owner", "e1@1", "set", "--thread", "e2", "--replace")
        before = self.files("engine")
        self.assertEqual(self.resume(*stale), (1, "brigade: owner e1@1 is stale; this store is owned by e2@2"))
        self.assertEqual(self.resume(*missing),
                         (1, "brigade: this store is owned by e2@2; pass --owner <thread>@<generation> from status"))
        self.assertEqual(self.files("engine"), before)
        self.assertEqual(self.inbox("engine"), ["app~core~T1.json"])
        self.assertEqual(self.brigade("engine", "--owner", "e2@2", "ticket", "take"), "T1 from app/core/T1: Fix the cache")
        self.assertEqual(self.inbox("engine"), [])

    def test_a_move_interrupted_after_the_rewrite_publishes_once_on_rerun(self):
        self.open("core", "--intake", "github")
        self.open("engine")
        self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "Fix the cache", "--source", "github", "--ref", "R7")
        died = self.child("die-before-publish", "core", "ticket", "move", "T1", "--to", "engine")
        self.assertEqual(died.returncode, 9, died.stderr)
        self.assertEqual(self.brigade("core", "ticket", "list"), "T1 moved normal fix [github] Fix the cache R7")
        self.assertEqual(self.inbox("engine"), [])
        self.assertEqual(self.brigade("core", "watch"),
                         "workers: 0 of 2 running, 2 idle, auto-start: normal\nT1: moved to engine, not delivered; run ticket move T1 --to engine again")
        self.brigade("core", "ticket", "move", "T1", "--to", "engine")
        self.brigade("core", "ticket", "move", "T1", "--to", "engine")
        self.assertEqual(self.inbox("engine"), ["app~core~T1.json"])
        self.assertEqual([row[1:4] for row in self.rows("core", "log.tsv") if row[3] == "moved"], [["ticket", "T1", "moved"]])
        self.brigade("engine", "ticket", "take")
        self.brigade("core", "ticket", "move", "T1", "--to", "engine")
        self.assertEqual(self.inbox("engine"), [])

    def test_watch_says_undelivered_when_the_inbox_file_is_deleted_before_take(self):
        self.handoff().unlink()
        self.assertEqual(self.brigade("core", "watch"),
                         "workers: 0 of 2 running, 2 idle, auto-start: normal\nT1: moved to engine, not delivered; run ticket move T1 --to engine again")

    def test_take_twice_with_the_file_copied_back_files_one_ticket(self):
        inbox = self.handoff()
        saved = inbox.read_bytes()
        self.brigade("engine", "ticket", "take")
        inbox.write_bytes(saved)
        self.assertEqual(self.brigade("engine", "ticket", "take"), "T1 from app/core/T1: Fix the cache")
        self.assertEqual(len(self.rows("engine", "rail.tsv")), 1)
        self.assertEqual([row for row in self.rows("engine", "log.tsv") if row[4] == "from app/core/T1"],
                         [[self.rows("engine", "log.tsv")[0][0], "ticket", "T1", "waiting", "from app/core/T1"]])
        self.assertEqual(self.inbox("engine"), [])

    def test_a_moved_ticket_keeps_its_priority_paths_and_decision_after_ticket_take(self):
        self.open("core")
        self.open("engine")
        self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "Fix the cache", "--priority", "urgent",
                     "--paths", "src/cache.py,docs", "--decision")
        self.brigade("core", "ticket", "move", "T1", "--to", "engine")
        self.assertEqual(json.loads((self.dir("engine") / "inbox" / "app~core~T1.json").read_text()),
                         {"handoff": "app/core/T1", "summary": "Fix the cache", "source": "user", "ref": "",
                          "priority": "urgent", "paths": "docs,src/cache.py", "decision": "yes", "kind": "fix"})
        self.assertEqual(self.brigade("engine", "ticket", "take"), "T1 from app/core/T1: Fix the cache")
        self.assertEqual(self.rows("engine", "rail.tsv")[0][2:],
                         ["waiting", "user (from app/core/T1)", "", "", "Fix the cache", "urgent", "docs,src/cache.py", "yes", "fix", ""])
        self.assertEqual(self.brigade("engine", "ticket", "list"), "T1 waiting urgent fix [user (from app/core/T1)] Fix the cache")

    def test_a_moved_ticket_with_an_empty_priority_cell_reads_the_default_of_its_source_after_ticket_take(self):
        self.open("core", "--intake", "upstream")
        self.open("engine")
        self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "Sync", "--source", "upstream")
        self.brigade("core", "ticket", "move", "T1", "--to", "engine")
        self.brigade("engine", "ticket", "take")
        self.assertEqual(self.rows("engine", "rail.tsv")[0][7:], ["", "", "", "fix", ""])
        self.assertEqual(self.brigade("engine", "ticket", "list"), "T1 waiting urgent fix [upstream (from app/core/T1)] Sync")

    def test_ticket_take_files_a_handoff_written_without_priority_paths_and_decision(self):
        self.open("core")
        self.open("engine")
        inbox = self.dir("engine") / "inbox"
        inbox.mkdir()
        (inbox / "app~core~T1.json").write_text(json.dumps(
            {"handoff": "app/core/T1", "summary": "Tidy the guide", "source": "report", "ref": "core/reports/D1.md#1"}))
        self.assertEqual(self.brigade("engine", "ticket", "take"), "T1 from app/core/T1: Tidy the guide")
        self.assertEqual(self.rows("engine", "rail.tsv")[0][2:],
                         ["waiting", "report (from app/core/T1)", "core/reports/D1.md#1", "", "Tidy the guide", "", "", "", "", ""])
        self.assertEqual(self.brigade("engine", "ticket", "list"),
                         "T1 waiting low [report (from app/core/T1)] Tidy the guide core/reports/D1.md#1")

    def labelled_handoff(self):
        """core files T1 as a polish ticket under its goal G1 and moves it to engine. Returns the line ticket move printed."""
        self.open("core")
        self.open("engine")
        self.brigade("core", "goal", "add", "--name", "activity view", "--purpose", "p", "--done", "d")
        self.brigade("core", "ticket", "add", "--summary", "Tidy the guide", "--kind", "polish", "--goal", "G1")
        return self.brigade("core", "ticket", "move", "T1", "--to", "engine")

    def test_ticket_move_writes_the_kind_and_no_goal_into_the_handoff_and_says_which_goal_the_ticket_leaves(self):
        said = "T1 moved to engine; no thread recorded for engine; it leaves G1 here and arrives unassigned"
        self.assertEqual(self.labelled_handoff(), said)
        self.assertEqual(json.loads((self.dir("engine") / "inbox" / "app~core~T1.json").read_text()),
                         {"handoff": "app/core/T1", "summary": "Tidy the guide", "source": "user", "ref": "",
                          "priority": "", "paths": "", "decision": "", "kind": "polish"})
        self.assertEqual(self.rows("core", "rail.tsv")[0][2:],
                         ["moved", "user", "", "to:engine", "Tidy the guide", "", "", "", "polish", "G1"])
        self.assertEqual(self.brigade("core", "ticket", "move", "T1", "--to", "engine"), said)

    def test_ticket_take_files_a_moved_ticket_with_its_kind_and_no_goal(self):
        self.labelled_handoff()
        self.brigade("engine", "goal", "add", "--name", "docs", "--purpose", "p", "--done", "d")
        self.assertEqual(self.brigade("engine", "ticket", "take"), "T1 from app/core/T1: Tidy the guide")
        self.assertEqual(self.rows("engine", "rail.tsv")[0][2:],
                         ["waiting", "user (from app/core/T1)", "", "", "Tidy the guide", "", "", "", "polish", ""])

    def test_ticket_move_of_a_ticket_with_no_kind_and_no_goal_writes_the_seven_keys_the_handoff_had_before_kinds(self):
        self.open("core")
        self.open("engine")
        (self.dir("core") / "rail.tsv").write_text(
            "id\tat\tstate\tsource\tref\tdish\tsummary\tpriority\tpaths\tdecision\n"
            "T1\t2026-10-01T00:00:00+00:00\twaiting\tuser\t\t\tTidy the guide\tlow\tdocs\t\n")
        self.assertEqual(self.brigade("core", "ticket", "move", "T1", "--to", "engine"),
                         "T1 moved to engine; no thread recorded for engine")
        self.assertEqual((self.dir("engine") / "inbox" / "app~core~T1.json").read_text(), json.dumps(
            {"handoff": "app/core/T1", "summary": "Tidy the guide", "source": "user", "ref": "",
             "priority": "low", "paths": "docs", "decision": ""}, indent=2) + "\n")

    def test_ticket_move_of_a_ticket_whose_kind_cell_holds_no_kind_writes_no_kind_into_the_handoff(self):
        self.open("core")
        self.open("engine")
        self.brigade("core", "ticket", "add", "--summary", "Tidy the guide", "--kind", "polish")
        rail = self.dir("core") / "rail.tsv"
        rail.write_text(rail.read_text().replace("\tpolish\t", "\tbugfix\t"))
        self.brigade("core", "ticket", "move", "T1", "--to", "engine")
        self.assertNotIn("kind", json.loads((self.dir("engine") / "inbox" / "app~core~T1.json").read_text()))
        self.assertEqual(self.brigade("engine", "ticket", "take"), "T1 from app/core/T1: Tidy the guide")
        self.assertEqual(self.rows("engine", "rail.tsv")[0][10:], ["", ""])

    def handed(self, *extras):
        """engine's inbox holds one handoff file per dict, from core's T1 on. Each holds the seven keys from before kinds, and that dict."""
        self.open("core")
        self.open("engine")
        inbox = self.dir("engine") / "inbox"
        inbox.mkdir()
        for number, extra in enumerate(extras, 1):
            (inbox / f"app~core~T{number}.json").write_text(json.dumps(
                {"handoff": f"app/core/T{number}", "summary": "Tidy the guide", "source": "user", "ref": "",
                 "priority": "low", "paths": "docs", "decision": "", **extra}))

    def take_handoff(self, **extra):
        """engine takes a handoff file that holds the seven keys from before kinds, and `extra`."""
        self.handed(extra)
        return self.brigade("engine", "ticket", "take")

    def unknown_kind(self, ident, quoted):
        """The warning of a take for the ticket `ident` whose handoff kind, in quotes, is `quoted`."""
        return (f"brigade: warning: {ident} came from app/core/{ident} with kind {quoted}, which is not one this version knows, "
                f"so it has no kind; run ticket set {ident} --kind <feature|fix|polish|upkeep|study>\n")

    def test_ticket_take_files_a_handoff_with_no_kind_key_as_a_ticket_with_no_kind_and_no_goal(self):
        self.assertEqual(self.take_handoff(), "T1 from app/core/T1: Tidy the guide")
        self.assertEqual(self.rows("engine", "rail.tsv")[0][2:],
                         ["waiting", "user (from app/core/T1)", "", "", "Tidy the guide", "low", "docs", "", "", ""])

    def test_ticket_take_files_a_handoff_with_a_kind_as_a_ticket_of_that_kind(self):
        self.assertEqual(self.take_handoff(kind="upkeep"), "T1 from app/core/T1: Tidy the guide")
        self.assertEqual(self.rows("engine", "rail.tsv")[0][10:], ["upkeep", ""])

    def test_ticket_take_files_a_handoff_with_a_kind_that_is_none_of_the_five_as_a_ticket_with_no_kind_and_warns_on_standard_error(self):
        self.handed({"kind": "chore"})
        took = self.run_brigade("engine", "ticket", "take")
        self.assertEqual((took.returncode, took.stdout, took.stderr),
                         (0, "T1 from app/core/T1: Tidy the guide\n",
                          "brigade: warning: T1 came from app/core/T1 with kind 'chore', which is not one this version knows, "
                          "so it has no kind; run ticket set T1 --kind <feature|fix|polish|upkeep|study>\n"))
        self.assertEqual(self.rows("engine", "rail.tsv")[0][10:], ["", ""])
        again = self.run_brigade("engine", "ticket", "take")
        self.assertEqual((again.returncode, again.stdout, again.stderr), (0, "nothing handed to you\n", ""))

    def test_inbox_take_warns_on_standard_error_of_a_handoff_kind_that_is_a_padded_fix(self):
        self.handed({"kind": " fix "})
        took = self.run_brigade("engine", "inbox", "take")
        self.assertEqual((took.returncode, took.stdout, took.stderr),
                         (0, "T1 from app/core/T1: Tidy the guide\n", self.unknown_kind("T1", "' fix '")))
        self.assertEqual(self.rows("engine", "rail.tsv")[0][10:], ["", ""])

    def test_a_take_of_two_handoffs_with_unknown_kinds_prints_one_warning_for_each(self):
        self.handed({"kind": "chore"}, {"kind": "chore"})
        took = self.run_brigade("engine", "ticket", "take")
        self.assertEqual((took.returncode, took.stdout, took.stderr),
                         (0, "T1 from app/core/T1: Tidy the guide\nT2 from app/core/T2: Tidy the guide\n",
                          self.unknown_kind("T1", "'chore'") + self.unknown_kind("T2", "'chore'")))
        self.assertEqual([row[10:] for row in self.rows("engine", "rail.tsv")], [["", ""], ["", ""]])

    def test_ticket_move_of_a_ticket_with_a_bad_kind_cell_and_a_bad_goal_cell_warns_of_both_on_standard_error_and_again_on_a_second_move(self):
        self.open("core")
        self.open("engine")
        self.brigade("core", "ticket", "add", "--summary", "Tidy the guide", "--kind", "polish")
        rail = self.dir("core") / "rail.tsv"
        rail.write_text(rail.read_text().replace("\tpolish\t\n", "\tchore\tG9\n"))
        by_hand = "ticket set does not change this cell while T1 is moved; correct it in rail.tsv by hand\n"
        said = (0, "T1 moved to engine; no thread recorded for engine\n",
                "brigade: warning: T1 has kind 'chore', which is not feature, fix, polish, upkeep, or study; it reads as no kind; " + by_hand
                + "brigade: warning: T1 names goal 'G9', which has no row in goals.tsv; it reads as unassigned; " + by_hand)
        for _ in range(2):
            moved = self.run_brigade("core", "ticket", "move", "T1", "--to", "engine")
            self.assertEqual((moved.returncode, moved.stdout, moved.stderr), said)
        self.assertEqual(self.rows("core", "rail.tsv")[0][10:], ["chore", "G9"])
        self.assertEqual(json.loads((self.dir("engine") / "inbox" / "app~core~T1.json").read_text()),
                         {"handoff": "app/core/T1", "summary": "Tidy the guide", "source": "user", "ref": "",
                          "priority": "", "paths": "", "decision": ""})

    def test_ticket_take_files_a_handoff_whose_kind_is_empty_and_one_whose_kind_is_null_as_tickets_with_no_kind(self):
        self.open("core")
        self.open("engine")
        inbox = self.dir("engine") / "inbox"
        inbox.mkdir()
        for number, kind in ((1, ""), (2, None)):
            (inbox / f"app~core~T{number}.json").write_text(json.dumps(
                {"handoff": f"app/core/T{number}", "summary": f"Tidy guide {number}", "source": "user", "ref": "",
                 "priority": "", "paths": "", "decision": "", "kind": kind}))
        self.assertEqual(self.brigade("engine", "ticket", "take"),
                         "T1 from app/core/T1: Tidy guide 1\nT2 from app/core/T2: Tidy guide 2")
        self.assertEqual([row[10:] for row in self.rows("engine", "rail.tsv")], [["", ""], ["", ""]])

    def test_ticket_take_ignores_a_goal_key_in_the_handoff(self):
        self.assertEqual(self.take_handoff(kind="fix", goal="G1"), "T1 from app/core/T1: Tidy the guide")
        self.assertEqual(self.rows("engine", "rail.tsv")[0][10:], ["fix", ""])

    def test_ticket_set_with_no_flag_on_a_moved_ticket_prints_the_moved_refusal(self):
        self.handoff()
        self.assertEqual(self.brigade("core", "ticket", "set", "T1", ok=False),
                         "brigade: T1 moved to engine; it is that coordinator's ticket now")

    def test_ticket_set_kind_or_no_goal_on_a_moved_ticket_prints_the_moved_refusal(self):
        self.handoff()
        for flags in (("--kind", "polish"), ("--no-goal",)):
            self.assertEqual(self.brigade("core", "ticket", "set", "T1", *flags, ok=False),
                             "brigade: T1 moved to engine; it is that coordinator's ticket now")

    def assert_one_handed_ticket(self, name="engine"):
        self.assertEqual([row[2:] for row in self.rows(name, "rail.tsv")],
                         [["waiting", "github (from app/core/T1)", "https://github.com/o/r/issues/7", "", "Fix the cache", "", "", "", "fix", ""]])
        log = (self.dir(name) / "log.tsv").read_text()
        self.assertTrue(log.endswith("\n"))
        self.assertTrue(all(len(line.split("\t")) == 5 for line in log.splitlines()))
        self.assertEqual([row[1:] for row in self.rows(name, "log.tsv") if "from app/core/T1" in row[4]],
                         [["ticket", "T1", "waiting", "from app/core/T1"]])
        self.assertEqual(self.inbox(name), [])
        self.assertEqual(sorted(path.name for path in self.dir(name).glob("*.tmp")), [])

    def test_a_take_killed_before_its_log_row_finishes_on_the_next_take(self):
        self.handoff()
        died = self.child("die-before-log", "engine", "ticket", "take")
        self.assertEqual(died.returncode, 9, died.stderr)
        self.assertEqual(len(self.rows("engine", "rail.tsv")), 1)
        self.assertEqual(self.rows("engine", "log.tsv"), [])
        self.brigade("engine", "ticket", "take")
        self.assert_one_handed_ticket()

    def test_a_take_killed_inside_the_log_append_recovers_the_tail(self):
        self.handoff()
        died = self.child("die-inside-log-append", "engine", "ticket", "take")
        self.assertEqual(died.returncode, 9, died.stderr)
        self.assertRegex((self.dir("engine") / "log.tsv").read_text(), r"\n[^\n]+\tticket\tT1\twaiting\tfro$")
        self.brigade("engine", "ticket", "take")
        self.assert_one_handed_ticket()

    def test_a_failure_inside_the_temp_file_write_leaves_the_same_end_state(self):
        self.handoff()
        module = runpy.run_path(str(SCRIPT))
        glob = module["run"].__globals__

        class FailingTempfile:
            @staticmethod
            def NamedTemporaryFile(*args, **kwargs):
                handle = tempfile.NamedTemporaryFile(*args, **kwargs)
                write = handle.write

                def fail(text):
                    write(text[:10])
                    handle.flush()
                    raise OSError(28, "No space left on device")

                handle.write = fail
                return handle

        glob["tempfile"] = FailingTempfile
        with self.assertRaises(OSError):
            glob["run"](["--at", str(self.dir("engine")), "ticket", "take"])
        glob["tempfile"] = tempfile
        self.assertEqual(self.rows("engine", "rail.tsv"), [])
        self.assertEqual(self.inbox("engine"), ["app~core~T1.json"])
        self.brigade("engine", "ticket", "take")
        self.assert_one_handed_ticket()

    def test_a_joined_line_stops_the_read_with_the_malformed_message(self):
        self.open("core")
        self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "a")
        log = self.dir("core") / "log.tsv"
        stamp = "2026-10-06T00:00:00.000000+00:00"
        log.write_text(log.read_text() + f"{stamp}\tticket\tT6\twaiting\tfro{stamp}\tticket\tT7\twaiting\tnote\n")
        self.assertEqual(self.brigade("core", "ticket", "list", ok=False), "brigade: app/core/log.tsv line 3 is malformed; fix or remove it")
        log.write_text(log.read_text().replace(f"fro{stamp}\tticket\tT7\twaiting\tnote", "fro"))
        self.assertEqual(self.brigade("core", "ticket", "list"), "T1 waiting normal fix [user] a")
        log.write_text(log.read_text() + f"{stamp[:19]}\tticket")
        self.assertEqual(self.brigade("core", "ticket", "list"), "T1 waiting normal fix [user] a")
        log.write_text(log.read_text() + f"{stamp}\tticket\tT8\twaiting\tnote\n")
        self.assertEqual(self.brigade("core", "ticket", "list", ok=False), "brigade: app/core/log.tsv line 4 is malformed; fix or remove it")
        log.write_text(log.read_text().replace(f"{stamp[:19]}\tticket{stamp}", "2026-10-06"))
        self.assertEqual(self.brigade("core", "ticket", "list", ok=False), "brigade: app/core/log.tsv line 4 is malformed; fix or remove it")

    def test_a_table_that_is_not_utf8_names_the_line(self):
        self.open("core")
        self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "a")
        rail = self.dir("core") / "rail.tsv"
        rail.write_bytes(rail.read_bytes() + b"\xff\n")
        self.assertEqual(self.brigade("core", "ticket", "list", ok=False),
                         "brigade: app/core/rail.tsv line 3 is malformed; fix or remove it")

    def test_a_malformed_line_is_named_with_its_store_from_a_sibling_from_walk_and_from_the_store_itself(self):
        self.open("core")
        self.open("docs")
        self.brigade("docs", "ticket", "add", "--kind", "fix", "--summary", "a")
        rail = self.dir("docs") / "rail.tsv"
        rail.write_text(rail.read_text() + "not a row\n")
        line = "brigade: app/docs/rail.tsv line 3 is malformed; fix or remove it"
        self.assertEqual(self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "x", "--source", "user", "--ref", "gh#1", ok=False),
                         line)
        self.assertEqual(self.brigade("core", "watch", ok=False), line)
        self.assertEqual(self.brigade("docs", "ticket", "list", ok=False), line)
        for words, cwd in ((("walk",), None), (("--at", ".", "ticket", "list"), self.dir("docs"))):
            # inherited environment
            result = real_process(
                ProcessReason.INHERITED_ENV, SCRIPT, ["--store", str(self.store), *words], cwd=cwd,
            )
            self.assertEqual((result.returncode, result.stderr.strip()), (1, line), words)

    def test_walk_prints_every_other_store_and_exits_1_when_one_store_has_a_malformed_table(self):
        for name in ("core", "docs", "engine"):
            self.open(name)
        self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "a")
        self.brigade("docs", "ticket", "add", "--kind", "fix", "--summary", "b")
        rail = self.dir("docs") / "rail.tsv"
        rail.write_text(rail.read_text() + "not a row\n")
        # inherited environment
        walked = real_process(ProcessReason.INHERITED_ENV, SCRIPT, ["--store", str(self.store), "walk"])
        self.assertEqual((walked.returncode, walked.stdout, walked.stderr), (1, "\n".join([
            f"{_shown_root(self.project)}: no landing contract",
            "  core (reports milestones): waiting tickets: 1",
            "    thread not recorded",
            "  docs: app/docs/rail.tsv line 3 is malformed; fix or remove it",
            "  engine (reports milestones): nothing on record",
            "    thread not recorded",
        ]) + "\n", "brigade: app/docs/rail.tsv line 3 is malformed; fix or remove it\n"))

    def test_walk_names_each_malformed_table_on_stderr_when_two_stores_have_one(self):
        for name in ("core", "docs", "engine"):
            self.open(name)
        rail = self.dir("core") / "rail.tsv"
        rail.write_text(rail.read_text() + "not a row\n")
        verdicts = self.dir("engine") / "pass.tsv"
        verdicts.write_text(verdicts.read_text() + "not a row\n")
        # inherited environment
        walked = real_process(ProcessReason.INHERITED_ENV, SCRIPT, ["--store", str(self.store), "walk"])
        self.assertEqual((walked.returncode, walked.stdout, walked.stderr), (1, "\n".join([
            f"{_shown_root(self.project)}: no landing contract",
            "  core: app/core/rail.tsv line 2 is malformed; fix or remove it",
            "  docs (reports milestones): nothing on record",
            "    thread not recorded",
            "  engine: app/engine/pass.tsv line 2 is malformed; fix or remove it",
        ]) + "\n", "brigade: app/core/rail.tsv line 2 is malformed; fix or remove it\n"
                   "brigade: app/engine/pass.tsv line 2 is malformed; fix or remove it\n"))

    def test_a_tail_cut_inside_a_multi_byte_character_is_skipped_by_a_read_and_dropped_by_the_next_append(self):
        self.open("core")
        self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "a")
        rail = self.dir("core") / "rail.tsv"
        complete = rail.read_bytes()
        rail.write_bytes(complete + "T9\t2026-10-10T00:00:00+00:00\twaiting\tuser\t\tcafé".encode()[:-1])
        self.assertEqual(self.brigade("core", "ticket", "list"), "T1 waiting normal fix [user] a")
        self.assertEqual(self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "b"), "T2")
        after = rail.read_bytes()
        self.assertEqual(after[:len(complete)], complete)
        added = after[len(complete):].decode()
        self.assertEqual((added.count("\n"), added[-1]), (1, "\n"))
        fields = added[:-1].split("\t")
        self.assertEqual(fields[:1] + fields[2:], ["T2", "waiting", "user", "", "", "b", "", "", "", "fix", ""])

    def test_a_short_append_restores_the_last_complete_row(self):
        from unittest import mock
        self.open("core")
        self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "a")
        log = self.dir("core") / "log.tsv"
        complete = log.read_text()
        log.write_text(complete + "2026-10-06T00:00:00.000000+00:00\tticket\tT9\twai")
        glob = runpy.run_path(str(SCRIPT))["run"].__globals__
        restaurant = glob["Restaurant"](self.dir("core"))
        row = {"at": "2026-10-06T00:00:01.000000+00:00", "kind": "ticket", "id": "T2", "state": "waiting", "note": "b"}
        real_write = os.write
        with mock.patch.object(os, "write", lambda fd, data: real_write(fd, data[:7])):
            with self.assertRaises(glob["BrigadeError"]) as raised:
                restaurant.append("log.tsv", row)
        self.assertEqual(str(raised.exception), "wrote 7 of 53 bytes to log.tsv; nothing appended")
        self.assertEqual(log.read_text(), complete)
        restaurant.append("log.tsv", row)
        self.assertEqual(log.read_text(), complete + "2026-10-06T00:00:01.000000+00:00\tticket\tT2\twaiting\tb\n")


WATCH = "workers: 0 of 2 running, 2 idle, auto-start: normal\nno work in progress"


def size_warning(size):
    return (
        "brigade: warning: "
        f"handoff.md is {size} bytes. Rewrite its current-state section in place and move the old text to an archive file.\n"
    )


class HandoffSizeTest(BrigadeTest):
    def watch_call(self, in_process=False):
        runner = call_in_process if in_process else call
        return runner(self, SCRIPT, [
            "--store", str(self.store), "--at", str(self.at), *_owner_words(self.at), "watch",
        ])

    def assert_watch(self, stderr="", in_process=False):
        outcome = self.watch_call(in_process=in_process)
        self.assertEqual(outcome.code, 0, outcome.stdout_text() + outcome.stderr_text())
        self.assertEqual(outcome.stdout_text(), WATCH + "\n")
        self.assertEqual(outcome.stderr_text(), stderr)
        return outcome

    def test_a_handoff_over_40000_bytes_warns_and_keeps_the_watch_text(self):
        self.open()
        self.assert_watch()
        (self.at / "handoff.md").write_bytes(b"x" * 40001)
        self.assert_watch(size_warning(40001))

    def test_a_handoff_of_40000_bytes_prints_no_size_line(self):
        self.open()
        (self.at / "handoff.md").write_bytes(b"x" * 40000)
        self.assert_watch()

    def test_a_missing_handoff_prints_no_size_line(self):
        self.open()
        self.assertFalse((self.at / "handoff.md").exists())
        self.assert_watch()

    def test_a_directory_named_handoff_md_prints_no_size_line(self):
        self.open()
        (self.at / "handoff.md").mkdir()
        self.assert_watch()

    def test_a_symlink_to_a_large_handoff_warns_with_the_target_size(self):
        self.open()
        target = Path(self.temporary.name) / "archive.md"
        target.write_bytes(b"x" * 40001)
        (self.at / "handoff.md").symlink_to(target)
        self.assert_watch(size_warning(40001))

    def test_a_stat_error_on_handoff_md_returns_no_size_line(self):
        self.open()
        line = run_globals(self, SCRIPT)["watch"].__globals__["handoff_size_line"]
        real_is_file = Path.is_file
        real_stat = Path.stat

        def is_file(self):
            if self.name == "handoff.md":
                return True
            return real_is_file(self)

        def stat(self, *, follow_symlinks=True):
            if self.name == "handoff.md":
                raise OSError("stat failed")
            return real_stat(self, follow_symlinks=follow_symlinks)

        Path.is_file = is_file
        Path.stat = stat
        try:
            self.assertIsNone(line(self.at))
            self.assert_watch(in_process=True)
        finally:
            Path.is_file = real_is_file
            Path.stat = real_stat

