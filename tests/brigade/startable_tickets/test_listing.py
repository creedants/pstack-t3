class ListingMixin:
    def test_startable_lists_a_ticket_whose_paths_are_free_as_startable_with_its_summary(self):
        self.queue()
        self.filed("Fix a", "--paths", "a.txt")
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 1, startable: 1",
            "T1 normal fix, 0m: startable; a.txt",
            "  Fix a",
        ]))

    def test_startable_names_the_lease_and_holder_that_block_a_ticket(self):
        self.queue()
        self.land("lease", "claim", "--holder", "engine/D1", "--paths", "src")
        self.filed("Fix the app", "--paths", "src/app.py")
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 1, blocked: 1",
            "T1 normal fix, 0m: blocked, waiting on L1 (engine/D1); src/app.py",
        ]))

    def test_startable_lists_a_ticket_that_records_no_paths_as_unknown(self):
        self.queue()
        self.filed("Fix something")
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 1, unknown: 1",
            "T1 normal fix, 0m: unknown, no paths recorded",
        ]))

    def test_startable_lists_a_decision_ticket_as_a_decision_with_or_without_paths(self):
        self.queue()
        self.filed("Pick a name", "--decision", "--paths", "a.txt", "--priority", "urgent")
        self.filed("Pick a color", "--decision")
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 2, decisions: 2",
            "T1 urgent fix, 0m: decision for the owner; a.txt",
            "T2 normal fix, 0m: decision for the owner",
        ]))

    def test_startable_lists_free_tickets_below_the_auto_start_level_as_below_it(self):
        self.queue()
        self.brigade("set", "--autofire", "urgent")
        self.filed("Sync", "--paths", "a.txt", "--priority", "urgent")
        self.filed("Fix b", "--paths", "b.txt")
        self.filed("Tidy c", "--paths", "c.txt", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 0 of 2 running, 2 idle, auto-start: urgent",
            "waiting tickets: 3, startable: 1, below auto-start: 2",
            "T1 urgent fix, 0m: startable; a.txt",
            "  Sync",
            "T2 normal fix, 0m: below auto-start; b.txt",
            "T3 low fix, 0m: below auto-start; c.txt",
        ]))

    def test_startable_lists_no_ticket_as_startable_at_level_off(self):
        self.queue()
        self.brigade("set", "--autofire", "off")
        self.filed("Sync", "--paths", "a.txt", "--priority", "urgent")
        self.filed("Tidy c", "--paths", "c.txt", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 0 of 2 running, 2 idle, auto-start: off",
            "waiting tickets: 2, below auto-start: 2",
            "T1 urgent fix, 0m: below auto-start; a.txt",
            "T2 low fix, 0m: below auto-start; c.txt",
        ]))

    def test_startable_lists_a_low_ticket_on_a_startable_ticket_file_or_under_its_directory_as_riding_with_it(self):
        self.queue()
        self.filed("Fix the guide and the scripts", "--paths", "docs/guide.md,scripts")
        self.filed("Tidy the guide", "--paths", "docs/guide.md", "--priority", "low")
        self.filed("Tidy the installer", "--paths", "scripts/install.py", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 3, startable: 1, riding: 2",
            "T1 normal fix, 0m: startable; docs/guide.md,scripts",
            "  Fix the guide and the scripts",
            "T2 low fix, 0m: rides with T1; docs/guide.md",
            "  Tidy the guide",
            "T3 low fix, 0m: rides with T1; scripts/install.py",
            "  Tidy the installer",
        ]))

    def test_startable_blocks_a_second_normal_ticket_that_shares_a_path_with_a_startable_one(self):
        self.queue()
        self.filed("Fix a", "--paths", "a.txt")
        self.filed("Fix a and b", "--paths", "a.txt,b.txt")
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 2, startable: 1, blocked: 1",
            "T1 normal fix, 0m: startable; a.txt",
            "  Fix a",
            "T2 normal fix, 0m: blocked, starts after T1; a.txt,b.txt",
        ]))

    def test_startable_blocks_a_low_ticket_that_shares_paths_with_two_startable_tickets_and_names_both(self):
        self.queue("--workers", "3")
        self.filed("Fix a", "--paths", "a.txt")
        self.filed("Fix b", "--paths", "b.txt")
        self.filed("Tidy both", "--paths", "a.txt,b.txt", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 0 of 3 running, 3 idle, auto-start: normal",
            "waiting tickets: 3, startable: 2, blocked: 1",
            "T1 normal fix, 0m: startable; a.txt",
            "  Fix a",
            "T2 normal fix, 0m: startable; b.txt",
            "  Fix b",
            "T3 low fix, 0m: blocked, starts after T1, T2; a.txt,b.txt",
        ]))

    def test_startable_blocks_a_low_ticket_with_one_path_on_a_startable_ticket_file_and_one_path_outside_its_paths(self):
        self.queue()
        self.filed("Fix the guide", "--paths", "docs/guide.md")
        self.filed("Tidy the docs", "--paths", "docs/guide.md,docs/cli.md", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 2, startable: 1, blocked: 1",
            "T1 normal fix, 0m: startable; docs/guide.md",
            "  Fix the guide",
            "T2 low fix, 0m: blocked, starts after T1; docs/cli.md,docs/guide.md",
        ]))

    def test_startable_blocks_a_low_ticket_on_a_directory_above_a_startable_ticket_file_at_a_cap_of_one(self):
        self.queue("--workers", "1")
        self.filed("Fix one", "--paths", "src/a/one.py", "--priority", "urgent")
        self.filed("Fix two", "--paths", "src/b/two.py")
        self.filed("Tidy the source", "--paths", "src", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 0 of 1 running, 1 idle, auto-start: normal",
            "waiting tickets: 3, startable: 1, blocked: 2",
            "T1 urgent fix, 0m: startable; src/a/one.py",
            "  Fix one",
            "T2 normal fix, 0m: blocked, waiting for an idle worker; src/b/two.py",
            "T3 low fix, 0m: blocked, starts after T1; src",
        ]))

    def test_startable_blocks_a_free_ticket_when_the_worker_cap_is_reached(self):
        self.queue("--workers", "1")
        self.filed("Running")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Running")
        self.filed("Fix b", "--paths", "b.txt")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 1 of 1 running, 0 idle, auto-start: normal",
            "waiting tickets: 1, blocked: 1",
            "T2 normal fix, 0m: blocked, waiting for an idle worker; b.txt",
        ]))
