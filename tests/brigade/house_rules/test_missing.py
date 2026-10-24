if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from ..support import BrigadeTest, json, setUpModule


class HouseRulesMissingTest(BrigadeTest):
    def test_brief_refuses_a_missing_house_rules_file_and_writes_no_brief(self):
        self.coordinator()
        (self.at / "house-rules.md").unlink()
        before = self.store_files()
        code, out, err = self.run_raw(*self.BRIEF)
        self.assertEqual(code, 1)
        self.assertEqual(err.strip().splitlines(),
                         [f"brigade: {self.at}/house-rules.md is missing; no brief was written"])
        self.assertEqual(out, "")
        self.assertFalse((self.at / "briefs/D1.md").exists())
        self.assertFalse((self.at / "briefs/D1.contract.md").exists())
        self.assertEqual(self.store_files(), before)

    def test_brief_refuses_a_missing_house_rules_file_before_recording_an_escalation(self):
        self.git_project()
        (self.project / ".pstack").mkdir()
        (self.project / ".pstack/t3-roles.json").write_text(json.dumps({"escalate": ["src/lock.py"]}))
        self.coordinator()
        (self.at / "house-rules.md").unlink()
        before = self.store_files()
        brief = [*self.BRIEF]
        brief[brief.index("src/a.py")] = "src/lock.py"
        code, out, err = self.run_raw(*brief)
        self.assertEqual(code, 1)
        self.assertEqual(err.strip().splitlines(),
                         [f"brigade: {self.at}/house-rules.md is missing; no brief was written"])
        self.assertEqual(self.store_files(), before)
