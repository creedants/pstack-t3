if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from .support import BrigadeTest, json, setUpModule
import subprocess


class OnboardR3Test(BrigadeTest):
    def detect_at(self, project_root):
        return json.loads(self.run_at(self.at, "onboard", "--project-root", str(project_root),
                                     "--name", "Perf", "--detect"))

    def test_worktree_finds_recorded_checks(self):
        self.git_project()
        self.init_landing("--check", "make test")
        self.landing_env()
        self.open()
        worktree = self.root / "wt"
        subprocess.run(["git", "-C", str(self.project), "worktree", "add", "--detach", str(worktree)],
                       capture_output=True, text=True, check=True)
        self.addCleanup(subprocess.run, ["git", "-C", str(self.project), "worktree", "remove", "--force", str(worktree)],
                        capture_output=True)
        found = self.detect_at(worktree)
        self.assertEqual(found["facts"]["contract_checks"]["state"], "detected")
        self.assertEqual(found["facts"]["contract_checks"]["value"], ["make test"])

    def test_subdir_finds_recorded_checks(self):
        self.git_project()
        self.init_landing("--check", "make test")
        self.landing_env()
        self.open()
        sub = self.project / "sub"
        sub.mkdir(exist_ok=True)
        found = self.detect_at(sub)
        self.assertEqual(found["facts"]["contract_checks"]["state"], "detected")
        self.assertEqual(found["facts"]["contract_checks"]["value"], ["make test"])

    def test_generated_map_parses_from_single_status_call(self):
        self.git_project()
        self.init_landing("--generated", "skills", "--generated-map", "t3src=skills/dst",
                          "--rebuild", "python3 scripts/build.py")
        self.landing_env()
        self.open()
        import os as _os
        import subprocess as _sp
        import sys as _sys
        import time as _time
        from pathlib import Path as _Path
        from ._support.names import ROOT as _ROOT, _owner_words as _owner_words
        from ._support.race_child import _race_args as _race_args
        from .support import SUPPORT as _SUPPORT
        case = _Path(self.temporary.name) / f"fake-land-count-{_time.time_ns()}"
        case.mkdir()
        count_file = case / "land-calls.txt"
        real_land = str(_ROOT / "t3/added/landing/scripts/land.py")
        (case / "land.py").write_text(
            "#!/usr/bin/env python3\n"
            "import subprocess, sys\n"
            f"real = {real_land!r}\n"
            f"count = {str(count_file)!r}\n"
            "args = sys.argv[1:]\n"
            "if 'status' in args and '--json' in args:\n"
            "    open(count, 'a').write('status --json\\n')\n"
            "r = subprocess.run([sys.executable, real, *args], capture_output=True, text=True)\n"
            "sys.stdout.write(r.stdout)\n"
            "sys.stderr.write(r.stderr)\n"
            "sys.exit(r.returncode)\n"
        )
        brigade_args = ["--store", str(self.store), "--at", str(self.at), *_owner_words(self.at),
                        "onboard", "--project-root", str(self.project), "--name", "Perf", "--detect"]
        tail = _race_args("fake-land", case, brigade_args)
        proc = _sp.run([_sys.executable, str(_SUPPORT), *tail], capture_output=True, text=True,
                       env=dict(_os.environ, PYTHONDONTWRITEBYTECODE="1"))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        found = json.loads(proc.stdout.strip())
        self.assertEqual(found["facts"]["generated"]["state"], "detected")
        self.assertEqual(count_file.read_text().strip().splitlines(), ["status --json"])
