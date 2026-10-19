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
        import subprocess as sp
        from unittest import mock as mk
        real = sp.run
        seen = []
        def fake(cmd, *a, **k):
            text = " ".join(str(c) for c in (cmd if isinstance(cmd, (list, tuple)) else [cmd]))
            if "land.py" in text and "status" in text and "--json" in text:
                seen.append(text)
            return real(cmd, *a, **k)
        with mk.patch("subprocess.run", side_effect=fake):
            found = json.loads(self.run_at(self.at, "onboard", "--project-root", str(self.project),
                                           "--name", "Perf", "--detect"))
        self.assertEqual(found["facts"]["generated"]["state"], "detected")
        self.assertEqual(len(seen), 1)
