from ..support import BULLETS, ProcessReason, SCRIPT, real_process

class FromReportMixin:
    def test_a_ticket_with_an_empty_priority_cell_reads_urgent_from_upstream_low_from_report_and_normal_from_other_sources(self):
        self.open()
        self.brigade("set", "--intake", "upstream,report,github")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "a", "--source", "upstream")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "b", "--source", "report")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "c", "--source", "github")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "d")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "e", "--source", "upstream", "--request", "A1")
        self.assertEqual(self.brigade("ticket", "list").splitlines(), [
            "T1 waiting urgent fix [upstream] a",
            "T2 waiting low fix [report] b",
            "T3 waiting normal fix [github] c",
            "T4 waiting normal fix [user] d",
            "T5 waiting urgent fix [upstream (request A1)] e",
        ])
        self.assertEqual([self.cells(f"T{number}") for number in range(1, 6)], [("", "", "")] * 5)

    def test_from_report_files_one_waiting_ticket_per_item_and_a_rerun_adds_nothing(self):
        self.fired_bug_fix()
        first = ("Correctness: A no-ref `--source user` add still has no CLI dedupe. The new Refusals rule tells the coordinator to read "
                 "`ticket list` before refiling. A `brigade.py` guard would make that structural.")
        second = ("Safety: `docs/guide.md` still describes intake as `gh issue list` and `gh pr list` when the standing orders name "
                  "them. That file is outside this lease.")
        third = "Privacy: PR #87 was not commented on, approved, or closed."
        self.assertEqual(self.follow_ups_in(BULLETS).splitlines(), [
            f"T2 added from perf/reports/D1.md#1: {first}",
            "not filed, D1.md takes one ticket per item: " + second,
            "not filed, D1.md takes one ticket per item: " + third,
        ])
        self.assertEqual(self.brigade("ticket", "list").splitlines()[-1],
                         f"T2 waiting low polish [report] {first} perf/reports/D1.md#1")
        self.assertEqual(self.table_row(self.at, "rail.tsv", "T2")["summary"], first)
        self.assertIn(f"\tticket\tT2\twaiting\t{first}\n", (self.at / "log.tsv").read_text())
        before_rail = (self.at / "rail.tsv").read_bytes()
        before_notes = (self.at / "notes.tsv").read_bytes()
        self.assertEqual(self.follow_ups_in(BULLETS).splitlines(), [
            f"skipped perf/reports/D1.md#1, same text as T2 (waiting): {first}",
            "not filed, D1.md takes one ticket per item: " + second,
            "not filed, D1.md takes one ticket per item: " + third,
        ])
        self.assertEqual((self.at / "rail.tsv").read_bytes(), before_rail)
        self.assertEqual((self.at / "notes.tsv").read_bytes(), before_notes)
        self.brigade("ticket", "set", "T2", "--state", "dropped")
        before_rail = (self.at / "rail.tsv").read_bytes()
        before_notes = (self.at / "notes.tsv").read_bytes()
        self.assertEqual(self.follow_ups_in(BULLETS).splitlines()[0],
                         f"skipped perf/reports/D1.md#1, same text as T2 (dropped): {first}")
        self.assertEqual((self.at / "rail.tsv").read_bytes(), before_rail)
        self.assertEqual((self.at / "notes.tsv").read_bytes(), before_notes)

    def test_from_report_prints_a_list_item_with_no_text_as_not_filed_and_gives_it_no_number(self):
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in("## Follow-ups\n\n- Correctness: Fix a.\n- \n*   \n1. \n- Safety: Fix b.\n").splitlines(), [
            "T2 added from perf/reports/D1.md#1: Correctness: Fix a.",
            "not filed, D1.md takes one ticket per item: Safety: Fix b.",
            "not filed, list item with no text",
            "not filed, list item with no text",
            "not filed, list item with no text",
        ])
        self.assertEqual(self.brigade("ticket", "list", "--state", "waiting").splitlines(), [
            "T2 waiting low polish [report] Correctness: Fix a. perf/reports/D1.md#1",
        ])

    def test_from_report_refuses_the_flags_of_a_hand_written_ticket(self):
        self.fired_bug_fix()
        self.review_file("D1.md", "## Follow-ups\n\n- One thing.\n")
        before = (self.at / "rail.tsv").read_bytes()
        add = ("ticket", "add", "--from-report", "reports/D1.md")
        for extra in (("--source", "github"), ("--ref", "x"), ("--request", "A1"), ("--again",)):
            self.assertEqual(self.brigade(*add, *extra, ok=False),
                             "brigade: --from-report takes no --source, --ref, --request, or --again")
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "x", "--dry-run", ok=False),
                         "brigade: --dry-run needs --from-report")
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "x", "--source", "report", ok=False),
                         "brigade: no coordinator owns intake from report; the one that reads it runs set --intake report")
        for flags, message in ((("--summary", "x", "--from-report", "reports/D1.md"),
                                "argument --from-report: not allowed with argument --summary"),
                               ((), "one of the arguments --summary --from-report is required")):
            # process exit status
            result = real_process(
                ProcessReason.EXIT_STATUS, SCRIPT,
                ["--store", str(self.store), "--at", str(self.at), "ticket", "add", *flags],
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn(message, result.stderr)
        self.assertEqual((self.at / "rail.tsv").read_bytes(), before)

    def test_from_report_skips_text_a_ticket_already_holds_whatever_its_spacing_and_case(self):
        self.fired_bug_fix()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Privacy: pr #87   was NOT commented on, approved, or closed.")
        self.assertEqual(self.follow_ups_in(BULLETS).splitlines()[2],
                         "skipped perf/reports/D1.md#3, same text as T2 (waiting): "
                         "Privacy: PR #87 was not commented on, approved, or closed.")
        self.assertEqual(self.brigade("ticket", "list").splitlines()[-1].split(" ", 1)[0], "T3")
