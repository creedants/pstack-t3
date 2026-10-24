if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "t3/scripts"))
sys.path.append(str(ROOT / "scripts"))

import roles  # noqa: E402


CODE_DELEGATE_SITES = (
    ("poteto-mode/playbooks/feature.md", "4. Delegate code-writing", "Playbook: playbooks/feature.md"),
    ("poteto-mode/playbooks/refactoring.md", "5. Move in small", "Playbook: playbooks/refactoring.md"),
    ("poteto-mode/playbooks/bug-fix.md", "3. Plan the fix.", "Playbook: playbooks/bug-fix.md"),
    ("poteto-mode/playbooks/perf-issue.md", "3. Plan the fix from the trace.", "Playbook: playbooks/perf-issue.md"),
    ("poteto-mode/playbooks/hillclimb.md", "   - Hand the change", "Playbook: playbooks/hillclimb.md"),
    ("swarm/SKILL.md", "Every brief stands alone.", "Playbook: playbooks/<name>.md"),
    ("arena/SKILL.md", "Spawn all N candidates", "Playbook: playbooks/<name>.md"),
)

class CodeDelegateBriefTest(unittest.TestCase):
    def test_every_code_delegate_step_points_at_the_brief_check(self):
        for path, step, playbook_line in CODE_DELEGATE_SITES:
            lines = (ROOT / "skills" / path).read_text().splitlines()
            matches = [line for line in lines if line.startswith(step)]
            self.assertEqual(len(matches), 1, (path, step))
            for needed in ("pstack-runtime/SKILL.md#delegation)", "poteto-agent persona first", playbook_line, "`roles.py mode --playbook ", "`roles.py check-brief` before `delegate_task`"):
                self.assertIn(needed, matches[0], path)

    def test_routed_skill_exception_excludes_code_writing_children(self):
        text = (ROOT / "skills/poteto-mode/SKILL.md").read_text()
        self.assertIn("It never covers a code-writing child.", text)
