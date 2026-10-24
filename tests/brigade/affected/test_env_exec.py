if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import shlex
import subprocess

from ..support import Path, ROOT, setUpModule
from tests.brigade.test_affected import AffectedBase, LAYOUT


class AffectedEnvExecTest(AffectedBase):
    def test_the_printed_suite_command_observes_the_recorded_env_in_the_child_fails_when_the_env_prefix_is_missing(self):
        self.git_project()
        self.write("tests/test_a.py", "def test_a():\n    pass\n")
        self.track()
        probe = "python3 -c \"import os; print(os.environ.get('PSTACK_TEST_REAL_PROCESS'))\""
        self.restaurant(self.project, layout=dict(LAYOUT, suite=probe, env={"PSTACK_TEST_REAL_PROCESS": "2"}))
        out = self.affected("--paths", "README.md")
        marker = "slot --slots 2 -- "
        start = out.index(marker) + len(marker)
        printed = out[start:out.index("`", start)]
        args = shlex.split(printed)
        proc = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "2")
