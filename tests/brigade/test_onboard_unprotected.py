if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from .support import BrigadeTest, Path, json, mock, os, setUpModule, subprocess, time


class OnboardUnprotectedTest(BrigadeTest):
    """A branch protection read GitHub answers with HTTP 404 is the unprotected state, and every other failure stays unknown."""

    def github_project(self, message):
        self.git_project()
        subprocess.run(["git", "remote", "add", "origin", "https://github.com/o/r"],
                       cwd=self.project, check=True, capture_output=True)
        bindir = Path(self.temporary.name) / f"fakebin-gh-{time.time_ns()}"
        bindir.mkdir()
        script = bindir / "gh"
        script.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            f"message = {message!r}\n"
            "args = sys.argv[1:]\n"
            "if args[:2] == ['auth', 'status']:\n"
            "    sys.exit(0)\n"
            "if args[:1] == ['api']:\n"
            "    print(message, file=sys.stderr)\n"
            "    sys.exit(1)\n"
            "print('unexpected gh call: ' + ' '.join(args), file=sys.stderr)\n"
            "sys.exit(1)\n")
        script.chmod(0o755)
        return {**os.environ, "PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}

    def test_protection_404_reads_unprotected_with_no_required_checks(self):
        env = self.github_project("gh: Not Found (HTTP 404)")
        with mock.patch.dict(os.environ, env):
            found = json.loads(self.brigade("onboard", "--project-root", str(self.project),
                                            "--name", "Perf", "--detect"))
            summary = self.brigade("onboard", "--project-root", str(self.project),
                                   "--name", "Perf", "--summary")
        self.assertEqual(found["facts"]["required_checks"], {"state": "unprotected", "value": [], "fix": None})
        self.assertNotIn("required_checks", found["missing"])
        self.assertIn("required_checks: none", summary)

    def test_protection_error_without_404_stays_unknown(self):
        env = self.github_project("gh: Server Error (HTTP 500)")
        with mock.patch.dict(os.environ, env):
            found = json.loads(self.brigade("onboard", "--project-root", str(self.project),
                                            "--name", "Perf", "--detect"))
        self.assertEqual(found["facts"]["required_checks"]["state"], "unknown")
        self.assertIn("HTTP 500", found["facts"]["required_checks"]["value"])
