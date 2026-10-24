if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import json
from .support import CheckoutCase, commit, setUpModule, snapshot
class ExcludedTest(CheckoutCase):
    def check(self, *flags):
        return self.sync("--check", *flags)
    def test_a_change_under_an_excluded_path_is_its_own_row(self):
        head = commit(self.upstream, {"pstack/skills/beta/SKILL.md": "edited\n", "harness/pstack/README.md": "edited\n"})
        result = self.check()
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        header, *rows = result.stdout.splitlines()
        self.assertEqual(header, f"upstream main: {self.pinned[:12]} -> {head[:12]} (1.0.0)")
        self.assertEqual(rows, ["changed  harness/pstack/README.md  excluded by upstream.json", "changed  pstack/skills/beta/SKILL.md  ships unchanged"])
        report = json.loads(self.check("--json").stdout)
        found = [e for e in report["changes"] if e["path"] == "harness/pstack/README.md"][0]
        self.assertEqual(found, {"path": "harness/pstack/README.md", "change": "changed", "handling": "excluded", "t3_path": None})
    def test_an_added_file_under_an_excluded_path_is_its_own_row(self):
        commit(self.upstream, {"harness/pstack/new.md": "added\n"})
        result = self.check()
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        _, *rows = result.stdout.splitlines()
        self.assertEqual(rows, ["added    harness/pstack/new.md  excluded by upstream.json"])
    def test_a_sync_leaves_the_excluded_tree_out_of_vendor_and_keeps_the_key(self):
        head = commit(self.upstream, {"harness/pstack/README.md": "edited\n"})
        result = self.sync()
        self.assertEqual(result.stderr, "")
        self.assertFalse((self.root / "vendor/pstack/harness").exists())
        self.assertFalse((self.root / "vendor/harness").exists())
        meta = json.loads((self.root / "upstream.json").read_text())
        self.assertEqual(meta["excluded"], ["harness/pstack"])
        self.assertEqual(meta["commit"], head)
    def test_an_excluded_path_that_overlaps_the_tracked_path_is_refused(self):
        for entry in ("pstack", "pstack/skills"):
            with self.subTest(entry=entry):
                self.assert_refused([entry], f"upstream.json excluded path {entry} overlaps the tracked path pstack\n")
    def test_an_excluded_path_that_is_not_a_relative_path_is_refused(self):
        for entry, message in (("", "upstream.json excluded path '' is not a non-empty string\n"), ("/harness", "upstream.json excluded path '/harness' is not a relative path\n")):
            with self.subTest(entry=entry):
                self.assert_refused([entry], message)
    def assert_refused(self, excluded, message):
        meta = json.loads((self.root / "upstream.json").read_text())
        (self.root / "upstream.json").write_text(json.dumps({**meta, "excluded": excluded}))
        before = snapshot(self.root)
        for flags in (("--check",), ()):
            result = self.sync(*flags)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (1, "", message))
            self.assertEqual(snapshot(self.root), before)
