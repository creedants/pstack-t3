if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from ..support import json


class SourcesMixin:
    def test_ticket_add_takes_a_user_request_with_the_summary_of_a_moved_ticket(self):
        self.open("core")
        self.open("engine")
        self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "Fix the cache")
        self.brigade("core", "ticket", "move", "T1", "--to", "engine")
        self.assertEqual(self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "Fix the cache"), "T2")

    def test_from_report_files_the_same_report_name_in_two_siblings(self):
        body = "## Follow-ups\n\n- Correctness: Shared text.\n"
        for name in ("core", "engine"):
            self.open(name)
            self.brigade(name, "ticket", "add", "--kind", "fix", "--summary", "s")
            self.brigade(name, "fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix s")
            (self.dir(name) / "reports").mkdir(exist_ok=True)
            (self.dir(name) / "reports" / "D1.md").write_text(body)
            self.assertEqual(self.brigade(name, "ticket", "add", "--from-report", "reports/D1.md"),
                             f"T2 added from {name}/reports/D1.md#1: Correctness: Shared text.")
        self.assertEqual(self.rows("engine", "rail.tsv")[1][2:], ["waiting", "report", "engine/reports/D1.md#1", "", "Correctness: Shared text.", "", "", "", "polish", ""])

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
